"""Agregaciones para la vista Análisis.

El agrupamiento de sesiones en "rutas únicas" se hace ahora con la columna
materializada `route_cluster_id` (ver `app.clustering`). Aquí solo agregamos
y formateamos. Los umbrales y la lógica de similitud viven en clustering.py.
"""
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy import and_, extract, func, or_
from sqlalchemy.orm import Session

from app.analisis_cache import (
    get_analisis_cached as _cached_impl,
    invalidate_analisis_cache,
)
from app.clustering import (
    SAME_ROUTE_ALT_M,
    SAME_ROUTE_TRAILHEAD_M,
    group_by_cluster,
)
from app.models import Route
from app.stats import (
    MONTH_LABELS_ES,
    _difficulty_label_es,
    _fmt_date_es,
    _fmt_duration,
    _fmt_int,
    _fmt_km,
    _fmt_km_short,
    _fmt_pace,
    _region_key,
    _region_label,
)

logger = logging.getLogger(__name__)

MIN_ROUTES_FOR_ANALYSIS = 3
TOP_REPEATED_LIMIT = 10


# ============= dataclasses =============

@dataclass
class HeroRangeChip:
    key: str
    label: str
    selected: bool = False


@dataclass
class HeroStat:
    label: str
    value: str
    unit: str
    detail: str


@dataclass
class HeatPoint:
    lat: float
    lon: float
    weight: int


@dataclass
class ZoneItem:
    name: str
    count: int
    km: float
    pct: int
    bar_pct: int   # 0-100 relativo a la zona con más km
    css_class: str  # heat-1 .. heat-4


@dataclass
class RatioPart:
    label: str
    value: int
    pct: int
    css_class: str  # accent | warm | cool


@dataclass
class StreakPoint:
    label: str   # "S-12"
    value: int   # km de esa semana


@dataclass
class StreakInfo:
    current: int
    best: int
    last4_avg_km: float
    spark: List[StreakPoint]


@dataclass
class DiscoveryPoint:
    label: str   # "ene 25"
    new_routes: int


@dataclass
class TopRouteRow:
    rank: int
    name: str
    origin: str
    reps: int
    total_km: float
    bar_pct: int   # 0-100 relativo al máximo
    last_date_str: str
    last_date_iso: str   # UTC ISO para conversión TZ en cliente
    days_since: int
    avg_gain: int          # desnivel + medio por sesión
    score: float           # dificultad media
    level: str             # easy | moderate | hard | very-hard
    level_label: str
    duration_avg: str      # tiempo medio en movimiento
    first_year: int        # año de la primera sesión registrada
    elev_line: str         # SVG path del perfil (viewBox 800x200)
    elev_area: str         # SVG path del área del perfil
    trend: str             # "up" | "down" | "flat" (vs año anterior)


@dataclass
class MonthBar:
    label: str
    sessions: int
    unique_routes: int
    sessions_h: int   # altura en px relativa
    unique_h: int
    km: float         # km totales del mes en el rango filtrado


@dataclass
class DonutPart:
    label: str
    value: int
    pct: int
    css_class: str


@dataclass
class ScatterDot:
    name: str
    km: float
    gain: int
    score: float
    level: str


@dataclass
class RecordCard:
    label: str
    value: str
    unit: str
    detail: str
    css_class: str  # warm | accent | cool | danger


@dataclass
class CalendarMonth:
    label: str
    weeks: List[List[Dict]]
    # Cada celda: {"d": "01", "v": 2.4, "level": "lvl3", "title": "..."}


@dataclass
class CalendarYear:
    year: int
    months: List[CalendarMonth]


@dataclass
class ComparatorRoute:
    key: str           # route.id (sesión individual)
    name: str
    origin: str
    date_str: str      # fecha de la sesión
    date_iso: str      # ISO para ordenar
    km: float
    ref_km: float
    gain: int          # desnivel +
    loss: int          # desnivel -
    gain_pct: float    # m+ / km
    loss_pct: float    # m- / km
    score: float
    level: str
    level_label: str
    duration: str
    pace: str
    ele_min: int
    ele_max: int
    line: str
    area: str
    cluster_name: str  # nombre del cluster (ruta única) para agrupar en el combo


@dataclass
class AnalisisData:
    has_data: bool
    range_key: str
    from_date: Optional[str]
    to_date: Optional[str]
    range_label: str
    range_chips: List[HeroRangeChip]
    hero_stats: List[HeroStat]
    heat_points: List[HeatPoint]
    map_center: Tuple[float, float]
    zones: List[ZoneItem]
    ratio: List[RatioPart]
    streak: StreakInfo
    discovery: List[DiscoveryPoint]
    top_routes: List[TopRouteRow]
    monthly: List[MonthBar]
    monthly_max: int
    monthly_prev_year: Dict[int, List[float]]  # {año: [km mes 1..14]} para todos los años históricos
    donut_difficulty: List[DonutPart]
    donut_difficulty_total: int
    donut_distance: List[DonutPart]
    donut_distance_total: int
    scatter: List[ScatterDot]
    scatter_max_km: float
    scatter_max_gain: int
    records: List[RecordCard]
    calendar: List[CalendarYear]
    calendar_mini: List[CalendarYear]  # histórico global por (año, mes), independiente del filtro
    km_by_day: List[Dict]          # [{"iso": "YYYY-MM-DD", "km": float}] UTC
    km_by_weekday: List[float]     # [lun, mar, mie, jue, vie, sab, dom]
    km_by_month_hist: List[float]  # [ene, feb, ..., dic] historico total
    comparator_routes: List[ComparatorRoute]
    total_sessions: int
    total_unique_routes: int


# ============= helpers =============

def _today() -> date:
    """Devuelve la fecha actual. Indirección que facilita el parcheo en tests."""
    return date.today()


# Las constantes y la lógica de clustering viven en app.clustering. Aquí solo
# las re-exponemos por compatibilidad con código que las importaba antes.
__all_thresholds__ = (SAME_ROUTE_TRAILHEAD_M, SAME_ROUTE_ALT_M)


def _cluster_routes(routes: List[Route]) -> Dict[int, List[Route]]:
    """Agrupa rutas por su `route_cluster_id` materializado al importar.

    Antes este método ejecutaba un union-find O(n²) por cada render de la
    vista de análisis. Ahora simplemente delegamos en `group_by_cluster`,
    que es O(n).
    """
    return group_by_cluster(routes)


def _origin_text(r: Route) -> str:
    """Texto de origen de una ruta: sub_region si existe, si no region, si no '—'."""
    return (r.sub_region or r.region or "—")


# ---- Estaciones astronómicas (hemisferio norte) ----
# La clave "temporada" del filtro se desdobla en cuatro keys reales que
# guardan inicio (mes, día) y fin (mes, día). El fin del invierno cae en
# el año siguiente al inicio.
SEASONS: Dict[str, Tuple[Tuple[int, int], Tuple[int, int], str]] = {
    "spring": ((3, 20), (6, 20), "primavera"),
    "summer": ((6, 21), (9, 22), "verano"),
    "autumn": ((9, 23), (12, 21), "otoño"),
    "winter": ((12, 22), (3, 19), "invierno"),  # cruza año natural
}
SEASON_KEYS = set(SEASONS.keys())


def _resolve_range(
    range_key: str,
    from_date: Optional[str],
    to_date: Optional[str],
) -> Tuple[Optional[date], Optional[date], str, str]:
    """Devuelve (start, end, key_normalizada, label_visible) para un rango."""
    today = _today()
    rk = (range_key or "all").lower()

    if rk == "custom" and from_date and to_date:
        try:
            d_from = date.fromisoformat(from_date)
            d_to = date.fromisoformat(to_date)
            if d_from > d_to:
                d_from, d_to = d_to, d_from
            label = f"{_fmt_date_es(d_from)} → {_fmt_date_es(d_to)}"
            return d_from, d_to, "custom", label
        except ValueError:
            pass

    if rk in SEASON_KEYS:
        _, _, label = SEASONS[rk]
        return None, None, rk, f"todas las {label}s"

    if rk == "year":
        return (date(today.year, 1, 1), today,
                "year", f"año {today.year}")

    if rk == "12m":
        start = today - timedelta(days=365)
        return (start, today, "12m", "últimos 12 meses")

    return (None, None, "all", "histórico completo")


def _build_chips(selected: str) -> List[HeroRangeChip]:
    """La chip 'temporada' es un placeholder visual: en el template se
    renderiza como <select> con primavera/verano/otoño/invierno."""
    chips = [
        HeroRangeChip("all", "todo"),
        HeroRangeChip("temporada", "temporada"),
        HeroRangeChip("year", "año actual"),
        HeroRangeChip("12m", "últimos 12 meses"),
        HeroRangeChip("custom", "rango personalizado"),
    ]
    for c in chips:
        if c.key == "temporada":
            c.selected = (selected in SEASON_KEYS)
        else:
            c.selected = (c.key == selected)
    return chips


def _filter_routes(
    db: Session,
    user_id: int,
    start: Optional[date],
    end: Optional[date],
    season_key: Optional[str] = None,
) -> List[Route]:
    """Devuelve rutas del usuario ordenadas por fecha dentro del rango [start, end].

    Si `season_key` está presente, ignora start/end y filtra por todos los
    años del histórico que caigan en esa estación astronómica.
    """
    qry = db.query(Route).filter(Route.user_id == user_id)

    if season_key and season_key in SEASONS:
        (sm, sd), (em, ed), _ = SEASONS[season_key]
        if season_key == "winter":
            # Invierno cruza el año: (mes >= 12 AND dia >= 22) OR (mes <= 3 AND dia <= 19)
            qry = qry.filter(or_(
                and_(
                    extract("month", Route.started_at) == 12,
                    extract("day", Route.started_at) >= sd,
                ),
                and_(
                    extract("month", Route.started_at) == 1,
                ),
                and_(
                    extract("month", Route.started_at) == 2,
                ),
                and_(
                    extract("month", Route.started_at) == 3,
                    extract("day", Route.started_at) <= ed,
                ),
            ))
        else:
            # Estaciones que no cruzan el año
            qry = qry.filter(or_(
                # mes de inicio: solo días >= sd
                and_(
                    extract("month", Route.started_at) == sm,
                    extract("day", Route.started_at) >= sd,
                ),
                # meses intermedios completos
                *[
                    extract("month", Route.started_at) == m
                    for m in range(sm + 1, em)
                ],
                # mes de fin: solo días <= ed
                and_(
                    extract("month", Route.started_at) == em,
                    extract("day", Route.started_at) <= ed,
                ),
            ))
    else:
        if start:
            qry = qry.filter(Route.started_at >= datetime.combine(start, datetime.min.time()))
        if end:
            qry = qry.filter(Route.started_at <= datetime.combine(end, datetime.max.time()))

    return qry.order_by(Route.started_at.asc()).all()


# ============= empty state =============

def _empty_analisis(range_key: str) -> AnalisisData:
    """AnalisisData vacío para cuando no hay datos suficientes en el rango."""
    return AnalisisData(
        has_data=False,
        range_key=range_key or "all",
        from_date=None, to_date=None,
        range_label="sin datos suficientes",
        range_chips=_build_chips(range_key or "all"),
        hero_stats=[],
        heat_points=[], map_center=(42.7, -1.6),
        zones=[],
        ratio=[],
        streak=StreakInfo(0, 0, 0.0, []),
        discovery=[],
        top_routes=[],
        monthly=[], monthly_max=1,
        monthly_prev_year={},
        donut_difficulty=[], donut_difficulty_total=0,
        donut_distance=[], donut_distance_total=0,
        scatter=[], scatter_max_km=1.0, scatter_max_gain=1,
        records=[],
        calendar=[],
        calendar_mini=[],
        km_by_day=[],
        km_by_weekday=[0.0] * 7,
        km_by_month_hist=[0.0] * 12,
        comparator_routes=[],
        total_sessions=0, total_unique_routes=0,
    )


# ============= principal =============

def build_analisis(
    db: Session,
    user_id: int,
    range_key: str = "all",
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> AnalisisData:
    """Construye todas las agregaciones de la vista Análisis para el rango dado.

    Agrupa sesiones en rutas únicas via `route_cluster_id` materializado,
    calcula hero stats, mapa de calor, zonas, streak, top-10,
    evolución mensual, donuts, scatter, récords, calendario y comparador.
    El resultado se cachea en `analisis_cache`; no llamar directamente
    desde los endpoints — usar `get_analisis_cached` en su lugar.
    Acotado al usuario indicado.
    """
    total_overall = (
        db.query(func.count(Route.id))
        .filter(Route.user_id == user_id)
        .scalar() or 0
    )
    if total_overall < MIN_ROUTES_FOR_ANALYSIS:
        return _empty_analisis(range_key)

    start, end, rk_norm, range_label = _resolve_range(range_key, from_date, to_date)
    chips = _build_chips(rk_norm)

    routes = _filter_routes(db, user_id, start, end,
                             season_key=rk_norm if rk_norm in SEASON_KEYS else None)
    if not routes:
        empty = _empty_analisis(rk_norm)
        empty.range_chips = chips
        empty.range_label = range_label
        empty.from_date = from_date
        empty.to_date = to_date
        return empty

    # ----- Agregación por ruta única -----
    by_key: Dict[int, List[Route]] = _cluster_routes(routes)
    unique_count = len(by_key)
    total_sessions = len(routes)

    # ----- HERO -----
    total_km = sum(r.distance_km for r in routes)
    total_gain = sum(r.elevation_gain_m for r in routes)
    total_moving = sum(r.moving_time_s for r in routes)

    # peak personal: dia con mayor distancia
    peak_route = max(routes, key=lambda r: r.distance_km)
    avg_km = total_km / total_sessions if total_sessions else 0.0

    # repetida: la ruta unica con mas sesiones
    most_repeated_key, most_repeated_sessions = max(
        ((k, v) for k, v in by_key.items()),
        key=lambda kv: len(kv[1]),
    )
    most_repeated_routes = most_repeated_sessions
    most_repeated_name = max(most_repeated_routes, key=lambda r: r.started_at).name

    hero_stats = [
        HeroStat("km totales",     _fmt_km_short(total_km),
                 "km", f"media {_fmt_km_short(avg_km)} km/sesión"),
        HeroStat("desnivel +",     _fmt_int(int(total_gain)),
                 "m", "acumulado en el rango"),
        HeroStat("tiempo activo",  _fmt_duration(int(total_moving)),
                 "", "en movimiento"),
        HeroStat("pico personal",  _fmt_km_short(peak_route.distance_km),
                 "km", peak_route.name),
        HeroStat("más repetida",   str(len(most_repeated_routes)),
                 "x", most_repeated_name),
    ]

    # ----- MAPA DE CALOR -----
    heat_buckets: Dict[Tuple[int, int], float] = defaultdict(float)
    for r in routes:
        # Celda de ~550m: precision ×200 (antes ×100 ≈ 1.1km, demasiado
        # difuso). El peso es km acumulados por celda — refleja volumen
        # real de actividad, no solo frecuencia de visita.
        cell = (round(r.start_lat * 200), round(r.start_lon * 200))
        heat_buckets[cell] += r.distance_km

    heat_points: List[HeatPoint] = []
    for (lat200, lon200), weight_km in heat_buckets.items():
        heat_points.append(HeatPoint(
            lat=lat200 / 200.0,
            lon=lon200 / 200.0,
            weight=round(weight_km, 1),
        ))

    # centro del mapa: media ponderada
    if heat_points:
        sum_w = sum(p.weight for p in heat_points)
        cx = sum(p.lat * p.weight for p in heat_points) / sum_w
        cy = sum(p.lon * p.weight for p in heat_points) / sum_w
        map_center = (cx, cy)
    else:
        map_center = (42.7, -1.6)

    # ----- ZONAS (heatmap leyenda lateral) -----
    region_counter: Counter = Counter()
    region_km: Dict[str, float] = defaultdict(float)
    region_labels: Dict[str, str] = {}
    for r in routes:
        key = _region_key(r.region)
        region_counter[key] += 1
        region_km[key] += r.distance_km
        lbl = _region_label(r.region, r.sub_region)
        if key not in region_labels or len(lbl) > len(region_labels[key]):
            region_labels[key] = lbl

    # Ordenar por km acumulados (consistente con el peso del heatmap)
    top_zones = sorted(region_km.items(), key=lambda x: -x[1])[:20]
    zone_max_km = top_zones[0][1] if top_zones else 1.0
    zones: List[ZoneItem] = []
    for key, km_val in top_zones:
        cnt = region_counter[key]
        ratio = km_val / zone_max_km if zone_max_km else 0.0
        if ratio > 0.75:
            css = "heat-4"
        elif ratio > 0.50:
            css = "heat-3"
        elif ratio > 0.25:
            css = "heat-2"
        else:
            css = "heat-1"
        pct = int(round((km_val / sum(region_km.values())) * 100)) if region_km else 0
        zones.append(ZoneItem(
            name=region_labels.get(key, key),
            count=cnt,
            km=round(km_val, 1),
            pct=pct,
            bar_pct=int(round(ratio * 100)),
            css_class=css,
        ))

    # ----- A · RATIO repetidas vs nuevas -----
    # repeated: sesiones que no son la primera de su ruta única
    # new: primera sesión de cada ruta única (== unique_count)
    # ratio_total: total_sessions (cada sesión cae en uno de los dos grupos)
    repeated_sessions = sum(len(v) - 1 for v in by_key.values() if len(v) > 1)
    new_sessions = unique_count
    ratio_total = total_sessions
    if ratio_total == 0:
        ratio = []
    else:
        rep_pct = int(round((repeated_sessions / ratio_total) * 100))
        new_pct = 100 - rep_pct
        ratio = [
            RatioPart("repetidas", repeated_sessions, rep_pct, "warm"),
            RatioPart("nuevas",    new_sessions,      new_pct, "accent"),
        ]

    # ----- B · STREAK semanas ISO -----
    today = _today()
    weeks_with_activity = {
        (r.started_at.isocalendar()[0], r.started_at.isocalendar()[1])
        for r in routes
    }
    # current: si la semana en curso aún no tiene actividad, empezamos a
    # contar desde la semana pasada para no romper el streak a mitad de semana.
    streak_current = 0
    cursor = today - timedelta(days=today.weekday())
    this_week = (cursor.isocalendar()[0], cursor.isocalendar()[1])
    if this_week not in weeks_with_activity:
        cursor -= timedelta(weeks=1)
    while True:
        iso = cursor.isocalendar()
        if (iso[0], iso[1]) in weeks_with_activity:
            streak_current += 1
            cursor -= timedelta(weeks=1)
        else:
            break
    # best
    sorted_weeks = sorted(weeks_with_activity)
    streak_best = 0
    run = 0
    prev: Optional[Tuple[int, int]] = None
    for w in sorted_weeks:
        if prev is None:
            run = 1
        else:
            # construir "semana siguiente" de prev
            prev_monday = date.fromisocalendar(prev[0], prev[1], 1)
            cur_monday = date.fromisocalendar(w[0], w[1], 1)
            if (cur_monday - prev_monday).days == 7:
                run += 1
            else:
                run = 1
        streak_best = max(streak_best, run)
        prev = w

    # spark: ultimas 12 semanas (km por semana)
    km_by_week: Dict[Tuple[int, int], float] = defaultdict(float)
    for r in routes:
        iso = r.started_at.isocalendar()
        km_by_week[(iso[0], iso[1])] += r.distance_km
    spark: List[StreakPoint] = []
    spark_cursor = today - timedelta(days=today.weekday())
    weeks_back: List[Tuple[int, int]] = []
    for _ in range(12):
        iso = spark_cursor.isocalendar()
        weeks_back.append((iso[0], iso[1]))
        spark_cursor -= timedelta(weeks=1)
    weeks_back.reverse()
    for i, w in enumerate(weeks_back):
        spark.append(StreakPoint(
            label=f"S-{11 - i}",
            value=int(round(km_by_week.get(w, 0.0))),
        ))
    last4 = [s.value for s in spark[-4:]]
    last4_avg = sum(last4) / len(last4) if last4 else 0.0

    streak = StreakInfo(
        current=streak_current,
        best=streak_best,
        last4_avg_km=round(last4_avg, 1),
        spark=spark,
    )

    # ----- G · DESCUBRIMIENTO: nuevas rutas únicas/mes (12m) -----
    # La primera fecha de cada ruta única se calcula sobre el histórico GLOBAL,
    # no sobre el rango filtrado: si una ruta se hizo por primera vez en 2022
    # y también en 2024, no debe aparecer como "nueva" al filtrar por año actual.
    # Ahora que `route_cluster_id` es estable a nivel BD podemos resolver la
    # primera fecha por cluster con un GROUP BY plano — sin volver a clusterizar.
    cluster_keys = [k for k in by_key.keys() if k >= 0]
    first_date_rows = (
        db.query(Route.route_cluster_id, func.min(Route.started_at))
        .filter(Route.user_id == user_id)
        .filter(Route.route_cluster_id.in_(cluster_keys))
        .group_by(Route.route_cluster_id)
        .all()
        if cluster_keys else []
    )
    first_date_by_key: Dict[int, date] = {
        cid: dt.date() for cid, dt in first_date_rows if dt
    }
    # Fallback para clusters sin id (BD aún no migrada): usamos el min local.
    for k, lst in by_key.items():
        if k not in first_date_by_key:
            first_date_by_key[k] = min(r.started_at.date() for r in lst)

    months_back: List[Tuple[int, int]] = []
    cur = date(today.year, today.month, 1)
    for _ in range(12):
        months_back.append((cur.year, cur.month))
        if cur.month == 1:
            cur = date(cur.year - 1, 12, 1)
        else:
            cur = date(cur.year, cur.month - 1, 1)
    months_back.reverse()

    new_per_month: Dict[Tuple[int, int], int] = defaultdict(int)
    for d in first_date_by_key.values():
        new_per_month[(d.year, d.month)] += 1
    discovery = [
        DiscoveryPoint(
            label=f"{MONTH_LABELS_ES[m-1]} {str(y)[2:]}",
            new_routes=new_per_month.get((y, m), 0),
        )
        for y, m in months_back
    ]

    # ----- C · TOP10 más repetidas -----
    top_candidates = sorted(
        ((k, v) for k, v in by_key.items() if len(v) > 1),
        key=lambda kv: (-len(kv[1]), -sum(r.distance_km for r in kv[1])),
    )[:TOP_REPEATED_LIMIT]
    if not top_candidates:
        # si no hay repetidas, mostrar las top por distancia acumulada
        top_candidates = sorted(
            ((k, v) for k, v in by_key.items()),
            key=lambda kv: -sum(r.distance_km for r in kv[1]),
        )[:TOP_REPEATED_LIMIT]
    top_max_reps = max((len(v) for _, v in top_candidates), default=1)
    top_routes: List[TopRouteRow] = []
    for i, (_, lst) in enumerate(top_candidates, start=1):
        # Representante: la sesión más reciente, así el nombre/origen
        # reflejan los datos más actualizados (renombrados, etc.)
        ref = max(lst, key=lambda r: r.started_at)
        n = len(lst)
        total_route_km = sum(r.distance_km for r in lst)
        avg_gain_val = int(round(sum((r.elevation_gain_m or 0) for r in lst) / n))
        avg_score = round(sum(float(r.difficulty_score or 0) for r in lst) / n, 1)
        avg_moving = int(round(sum((r.moving_time_s or 0) for r in lst) / n))
        last_d = max(r.started_at.date() for r in lst)
        first_d = min(r.started_at.date() for r in lst)
        # tendencia: sesiones en los últimos 365 días vs los 365 anteriores
        cutoff_1y = today - timedelta(days=365)
        cutoff_2y = today - timedelta(days=730)
        reps_last_year = sum(1 for r in lst if r.started_at.date() >= cutoff_1y)
        reps_prev_year = sum(1 for r in lst if cutoff_2y <= r.started_at.date() < cutoff_1y)
        if reps_last_year > reps_prev_year:
            trend = "up"
        elif reps_last_year < reps_prev_year:
            trend = "down"
        else:
            trend = "flat"
        top_routes.append(TopRouteRow(
            rank=i,
            name=ref.name,
            origin=_origin_text(ref),
            reps=n,
            total_km=round(total_route_km, 1),
            bar_pct=int(round((n / top_max_reps) * 100)) if top_max_reps else 0,
            last_date_str=_fmt_date_es(last_d),
            last_date_iso=last_d.isoformat(),
            days_since=(today - last_d).days,
            avg_gain=avg_gain_val,
            score=avg_score,
            level=ref.difficulty_level,
            level_label=_difficulty_label_es(ref.difficulty_level),
            duration_avg=_fmt_duration(avg_moving),
            first_year=first_d.year,
            elev_line=ref.elev_line_path or "",
            elev_area=ref.elev_area_path or "",
            trend=trend,
        ))

    # ----- 03 · EVOLUCIÓN MENSUAL (km por mes, enero-diciembre, año actual vs histórico) -----
    # Eje fijo: enero a diciembre del año actual
    months_12: List[Tuple[int, int]] = [(today.year, m) for m in range(1, 13)]
    sessions_per_month: Dict[Tuple[int, int], int] = defaultdict(int)
    unique_per_month: Dict[Tuple[int, int], set] = defaultdict(set)
    km_per_month: Dict[Tuple[int, int], float] = defaultdict(float)
    for k, lst in by_key.items():
        for r in lst:
            ym = (r.started_at.year, r.started_at.month)
            sessions_per_month[ym] += 1
            unique_per_month[ym].add(k)
            km_per_month[ym] += r.distance_km
    monthly_max = max(
        max((sessions_per_month.get(ym, 0) for ym in months_12), default=0),
        max((len(unique_per_month.get(ym, set())) for ym in months_12), default=0),
        1,
    )
    monthly: List[MonthBar] = []
    for y, m in months_12:
        s = sessions_per_month.get((y, m), 0)
        u = len(unique_per_month.get((y, m), set()))
        km = round(km_per_month.get((y, m), 0.0), 1)
        monthly.append(MonthBar(
            label=MONTH_LABELS_ES[m-1],
            sessions=s,
            unique_routes=u,
            sessions_h=int(round((s / monthly_max) * 100)),
            unique_h=int(round((u / monthly_max) * 100)),
            km=km,
        ))
    # año anterior: mismos 14 meses desplazados 12 meses atrás, usando histórico global
    # (se calcula después de km_by_ym_all, ver más abajo)
    monthly_prev_year: Dict[int, List[float]] = {}

    # ----- 04 · DOS DONUTS: dificultad y distancia -----
    diff_counter = Counter(r.difficulty_level for r in routes)
    diff_total = sum(diff_counter.values())
    diff_label = {
        "easy": "fácil", "moderate": "moderada",
        "hard": "difícil", "very-hard": "muy difícil",
    }
    donut_difficulty: List[DonutPart] = []
    for lvl in ("easy", "moderate", "hard", "very-hard"):
        c = diff_counter.get(lvl, 0)
        if c == 0:
            continue
        donut_difficulty.append(DonutPart(
            label=diff_label[lvl],
            value=c,
            pct=int(round((c / diff_total) * 100)),
            css_class=lvl,
        ))

    dist_buckets = [
        ("0-8 km",    "easy",      lambda km: km < 8),
        ("8-14 km",   "moderate",  lambda km: 8 <= km < 14),
        ("14-22 km",  "hard",      lambda km: 14 <= km < 22),
        ("22+ km",    "very-hard", lambda km: km >= 22),
    ]
    donut_distance: List[DonutPart] = []
    dist_total = total_sessions
    for label, css, pred in dist_buckets:
        c = sum(1 for r in routes if pred(r.distance_km))
        if c == 0:
            continue
        donut_distance.append(DonutPart(
            label=label, value=c,
            pct=int(round((c / dist_total) * 100)),
            css_class=css,
        ))

    # ----- 05 · SCATTER km vs desnivel -----
    scatter: List[ScatterDot] = []
    scatter_max_km = max((r.distance_km for r in routes), default=1.0) or 1.0
    scatter_max_gain = max((r.elevation_gain_m for r in routes), default=1) or 1
    for r in routes:
        scatter.append(ScatterDot(
            name=r.name,
            km=round(r.distance_km, 1),
            gain=int(r.elevation_gain_m or 0),
            score=round(float(r.difficulty_score or 0.0), 1),
            level=r.difficulty_level,
        ))

    # ----- 06 · RECORDS -----
    longest = max(routes, key=lambda r: r.distance_km)
    biggest = max(routes, key=lambda r: r.elevation_gain_m or 0)
    paced = [r for r in routes if r.distance_km > 0 and r.moving_time_s > 0]
    if paced:
        best_pace_route = min(paced, key=lambda r: r.moving_time_s / r.distance_km)
        best_pace_str = _fmt_pace(best_pace_route.distance_km, best_pace_route.moving_time_s)
        best_pace_detail = best_pace_route.name
    else:
        best_pace_str = "—"
        best_pace_detail = "sin tiempos"
    hardest = max(routes, key=lambda r: r.difficulty_score or 0.0)

    records = [
        RecordCard("la más larga", _fmt_km_short(longest.distance_km), "km",
                   longest.name, "warm"),
        RecordCard("más desnivel +", _fmt_int(biggest.elevation_gain_m or 0), "m",
                   biggest.name, "accent"),
        RecordCard("mejor ritmo", best_pace_str, "/km",
                   best_pace_detail, "cool"),
        RecordCard("la más dura",
                   f"{hardest.difficulty_score:.1f}".replace(".", ","),
                   "/10",
                   hardest.name, "danger"),
    ]

    # ----- 07 · CALENDARIO HEATMAP por meses -----
    if start and end:
        cal_start = start
        cal_end = end
    else:
        cal_start = min(r.started_at.date() for r in routes)
        cal_end = max(r.started_at.date() for r in routes)
        # limitar a últimos 24 meses si el histórico es muy grande
        max_window = today - timedelta(days=730)
        if cal_start < max_window:
            cal_start = max_window

    km_by_day: Dict[date, float] = defaultdict(float)
    for r in routes:
        km_by_day[r.started_at.date()] += r.distance_km
    # Serializar como lista [{"iso": "YYYY-MM-DD", "km": float}] para que el
    # cliente reagrupe por día local (UTC+1/+2 en España).
    km_by_day_payload = [
        {"iso": d.isoformat(), "km": round(v, 2)}
        for d, v in km_by_day.items()
    ]

    calendar: List[CalendarYear] = []
    cur_year = cal_start.year
    cur_month = cal_start.month
    end_year = cal_end.year
    end_month = cal_end.month

    months_for_year: Dict[int, List[CalendarMonth]] = defaultdict(list)
    while (cur_year, cur_month) <= (end_year, end_month):
        first = date(cur_year, cur_month, 1)
        # ultimo dia del mes
        if cur_month == 12:
            next_first = date(cur_year + 1, 1, 1)
        else:
            next_first = date(cur_year, cur_month + 1, 1)
        last = next_first - timedelta(days=1)

        # construir matriz [semana][dia] empezando en lunes
        weeks: List[List[Dict]] = []
        # rellenar huecos al principio
        first_weekday = first.weekday()  # lunes=0
        cur_week: List[Dict] = [{"d": "", "v": 0.0, "level": "", "title": ""}
                                for _ in range(first_weekday)]
        d = first
        while d <= last:
            v = km_by_day.get(d, 0.0)
            level = ""
            if v > 18:
                level = "lvl4"
            elif v > 12:
                level = "lvl3"
            elif v > 6:
                level = "lvl2"
            elif v > 0:
                level = "lvl1"
            title = (f"{_fmt_date_es(d)} · {_fmt_km(v)} km" if v > 0
                     else f"{_fmt_date_es(d)} · sin actividad")
            cur_week.append({
                "d": f"{d.day:02d}",
                "v": round(v, 2),
                "level": level,
                "title": title,
            })
            if len(cur_week) == 7:
                weeks.append(cur_week)
                cur_week = []
            d += timedelta(days=1)
        if cur_week:
            while len(cur_week) < 7:
                cur_week.append({"d": "", "v": 0.0, "level": "", "title": ""})
            weeks.append(cur_week)

        months_for_year[cur_year].append(CalendarMonth(
            label=MONTH_LABELS_ES[cur_month - 1],
            weeks=weeks,
        ))

        # avanzar
        if cur_month == 12:
            cur_year += 1
            cur_month = 1
        else:
            cur_month += 1

    for y in sorted(months_for_year.keys(), reverse=True):
        calendar.append(CalendarYear(year=y, months=months_for_year[y]))

    # ----- km por dia de semana, estacionalidad y calendario mini (historico global) -----
    all_routes_user = (
        db.query(Route.started_at, Route.distance_km)
        .filter(Route.user_id == user_id)
        .all()
    )
    km_by_weekday = [0.0] * 7
    km_by_month_hist = [0.0] * 12
    km_by_ym_all: Dict[Tuple[int, int], float] = defaultdict(float)
    for r in all_routes_user:
        if r.started_at:
            km_by_weekday[r.started_at.weekday()] += r.distance_km
            km_by_month_hist[r.started_at.month - 1] += r.distance_km
            km_by_ym_all[(r.started_at.year, r.started_at.month)] += r.distance_km
    km_by_weekday = [round(v, 1) for v in km_by_weekday]
    km_by_month_hist = [round(v, 1) for v in km_by_month_hist]

    # todos los años históricos: para cada año distinto en km_by_ym_all (excepto el actual),
    # los mismos 14 slots de months_14 desplazados al año correspondiente
    current_year = today.year
    hist_years = sorted({y for y, m in km_by_ym_all.keys() if y != current_year})
    monthly_prev_year: Dict[int, List[float]] = {
        yr: [round(km_by_ym_all.get((yr, m), 0.0), 1) for m in range(1, 13)]
        for yr in hist_years
    }

    # Calendario mini: histórico completo por (año, mes), siempre 12 meses por año
    if km_by_ym_all:
        start_year = max(min(km_by_ym_all.keys())[0], today.year - 2)
        cal_mini_months_for_year: Dict[int, List[CalendarMonth]] = defaultdict(list)
        for y in range(start_year, today.year + 1):
            for m in range(1, 13):
                if (y, m) > (today.year, today.month):
                    break
                v = round(km_by_ym_all.get((y, m), 0.0), 1)
                if v > 60:
                    level = "lvl4"
                elif v > 35:
                    level = "lvl3"
                elif v > 15:
                    level = "lvl2"
                elif v > 0:
                    level = "lvl1"
                else:
                    level = ""
                title = (f"{MONTH_LABELS_ES[m-1]} {y} · {v:.0f} km" if v > 0
                         else f"{MONTH_LABELS_ES[m-1]} {y} · sin actividad")
                cal_mini_months_for_year[y].append(CalendarMonth(
                    label=MONTH_LABELS_ES[m - 1],
                    weeks=[{"level": level, "title": title, "v": v}],  # type: ignore[list-item]
                ))
        calendar_mini: List[CalendarYear] = [
            CalendarYear(year=y, months=cal_mini_months_for_year[y])
            for y in sorted(cal_mini_months_for_year.keys(), reverse=True)
        ]
    else:
        calendar_mini = []

    # ----- 08 · COMPARADOR (una entrada por sesión individual, ordenadas por fecha desc) -----
    comparator_routes: List[ComparatorRoute] = []
    for k, lst in by_key.items():
        cluster_ref = max(lst, key=lambda r: r.started_at)  # nombre del cluster = sesión más reciente
        for r in sorted(lst, key=lambda r: r.started_at, reverse=True):
            km_val = round(float(r.distance_km or 0.0), 1)
            gain_val = int(r.elevation_gain_m or 0)
            loss_val = int(r.elevation_loss_m or 0)
            gain_pct = round(gain_val / km_val, 1) if km_val > 0 else 0.0
            loss_pct = round(loss_val / km_val, 1) if km_val > 0 else 0.0
            comparator_routes.append(ComparatorRoute(
                key=str(r.id),
                name=r.name,
                origin=_origin_text(r),
                date_str=_fmt_date_es(r.started_at),
                date_iso=r.started_at.strftime("%Y-%m-%d"),
                km=km_val,
                ref_km=round(float(r.distance_km or 0.0), 2),
                gain=gain_val,
                loss=loss_val,
                gain_pct=gain_pct,
                loss_pct=loss_pct,
                score=round(float(r.difficulty_score or 0.0), 1),
                level=r.difficulty_level,
                level_label=_difficulty_label_es(r.difficulty_level),
                duration=_fmt_duration(r.moving_time_s or 0),
                pace=_fmt_pace(r.distance_km, r.moving_time_s or 0),
                ele_min=int(r.min_altitude_m or 0),
                ele_max=int(r.max_altitude_m or 0),
                line=r.elev_line_path or "",
                area=r.elev_area_path or "",
                cluster_name=cluster_ref.name,
            ))
    comparator_routes.sort(key=lambda c: (c.cluster_name.lower(), c.date_iso), reverse=False)

    return AnalisisData(
        has_data=True,
        range_key=rk_norm,
        from_date=from_date if rk_norm == "custom" else None,
        to_date=to_date if rk_norm == "custom" else None,
        range_label=range_label,
        range_chips=chips,
        hero_stats=hero_stats,
        heat_points=heat_points,
        map_center=map_center,
        zones=zones,
        ratio=ratio,
        streak=streak,
        discovery=discovery,
        top_routes=top_routes,
        monthly=monthly,
        monthly_max=monthly_max,
        monthly_prev_year=monthly_prev_year,
        donut_difficulty=donut_difficulty,
        donut_difficulty_total=diff_total,
        donut_distance=donut_distance,
        donut_distance_total=dist_total,
        scatter=scatter,
        scatter_max_km=round(scatter_max_km, 1),
        scatter_max_gain=int(scatter_max_gain),
        records=records,
        calendar=calendar,
        calendar_mini=calendar_mini,
        km_by_day=km_by_day_payload,
        km_by_weekday=km_by_weekday,
        km_by_month_hist=km_by_month_hist,
        comparator_routes=comparator_routes,
        total_sessions=total_sessions,
        total_unique_routes=unique_count,
    )


# ============= cache en proceso (opción A) =============
#
# La implementación del cache vive en `app.analisis_cache` para mantener este
# módulo enfocado en agregaciones. Aquí solo exponemos un wrapper fino que
# inyecta `build_analisis` como callback (evita import circular en el otro
# sentido) y re-exportamos `invalidate_analisis_cache` para los call sites
# existentes en `app.main` que importan desde `app.analisis`.
#
# `invalidate_analisis_cache` se importa arriba con los demás imports.

def get_analisis_cached(
    db: Session,
    user_id: int,
    range_key: str = "all",
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> "AnalisisData":
    """Wrapper cacheado de `build_analisis` — delega en `analisis_cache`."""
    return _cached_impl(build_analisis, db, user_id, range_key, from_date, to_date)


# ============= serialización JSON para hidratar el cliente =============

def to_json_payload(data: AnalisisData) -> Dict:
    """Convierte el dataclass en un dict serializable para window.MENDI_ANALISIS."""
    return {
        "hasData": data.has_data,
        "rangeKey": data.range_key,
        "rangeLabel": data.range_label,
        "fromDate": data.from_date,
        "toDate": data.to_date,
        "totalSessions": data.total_sessions,
        "totalUniqueRoutes": data.total_unique_routes,
        "mapCenter": list(data.map_center),
        "heatPoints": [[p.lat, p.lon, p.weight] for p in data.heat_points],
        "ratio": [
            {"label": r.label, "value": r.value, "pct": r.pct, "css": r.css_class}
            for r in data.ratio
        ],
        "streak": {
            "current": data.streak.current,
            "best": data.streak.best,
            "last4Avg": data.streak.last4_avg_km,
            "spark": [{"label": s.label, "value": s.value} for s in data.streak.spark],
        },
        "discovery": [
            {"label": d.label, "value": d.new_routes} for d in data.discovery
        ],
        "monthly": [
            {"label": m.label, "sessions": m.sessions,
             "unique": m.unique_routes, "km": m.km,
             "sessionsH": m.sessions_h, "uniqueH": m.unique_h}
            for m in data.monthly
        ],
        "monthlyMax": data.monthly_max,
        "monthlyPrevYear": {
            str(yr): vals
            for yr, vals in data.monthly_prev_year.items()
        },
        "donutDifficulty": [
            {"label": p.label, "value": p.value, "pct": p.pct, "css": p.css_class}
            for p in data.donut_difficulty
        ],
        "donutDistance": [
            {"label": p.label, "value": p.value, "pct": p.pct, "css": p.css_class}
            for p in data.donut_distance
        ],
        "scatter": [
            {"name": s.name, "km": s.km, "gain": s.gain,
             "score": s.score, "level": s.level}
            for s in data.scatter
        ],
        "scatterMaxKm": data.scatter_max_km,
        "scatterMaxGain": data.scatter_max_gain,
        "kmByDay": data.km_by_day,
        "kmByWeekday": data.km_by_weekday,
        "kmByMonthHist": data.km_by_month_hist,
    }
