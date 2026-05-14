"""Agregaciones para las vistas Resumen y Rutas."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy import func, or_, extract
from sqlalchemy.orm import Session

from app.format import (
    MONTH_LABELS_ES,
    difficulty_label_es as _difficulty_label_es,
    fmt_date_es as _fmt_date_es,
    fmt_duration as _fmt_duration,
    fmt_int as _fmt_int,
    fmt_km as _fmt_km,
    fmt_km_short as _fmt_km_short,
    fmt_pace as _fmt_pace,
)
from app.models import Route
from app.text_utils import canonical_geo

# Circunferencia ecuatorial de la Tierra en km, para el % "vuelta al mundo".
WORLD_CIRCUMFERENCE_KM = 40075.0

# Cuántas rutas (las más recientes) se exponen al cliente para la rotación
# del featured. NO se usa para listar; el listado siempre va por API paginada.
ROTATION_POOL_SIZE = 30


@dataclass
class HeroSummary:
    total_km: float
    route_count: int
    elevation_gain_total: int
    moving_time_total_s: int
    max_altitude: int
    world_pct: float


@dataclass
class KpiCard:
    label: str
    value: str
    unit: str
    detail: str
    detail_kind: str = "neutral"  # neutral | pos | neg
    description: str = ""  # descripción breve para que el usuario entienda el KPI


@dataclass
class RouteRow:
    id: int
    name: str
    sub: str
    date_str: str
    started_at_iso: str
    distance_str: str
    gain_str: str
    moving_str: str
    score: float
    level: str
    score_str: str
    ele_min: int
    ele_max: int
    line: str
    area: str


@dataclass
class MapMarker:
    name: str
    lat: float
    lon: float
    km: float
    gain: int
    score: float
    level: str
    date_str: str
    started_at_iso: str


@dataclass
class HeroProfile:
    name: str
    line: str
    area: str


@dataclass
class DonutSlice:
    label: str
    count: int
    pct: int
    css_class: str  # moderate | hard | very-hard | easy


@dataclass
class CalendarYear:
    year: int
    cells: List[Dict]  # [{"month": "ene", "level": "lvl3", "title": "12,3 km"}]


@dataclass
class MonthlyPoint:
    label: str
    value: float


@dataclass
class RutaItem:
    """Una ruta tal y como la consume la vista Rutas (cliente filtra/ordena)."""
    id: int
    name: str
    origin: str           # subregion · ciudad de salida
    region: str           # texto visible (ej. "huesca · pirineo aragonés")
    region_filter: str    # clave canónica para filtros (ej. "huesca")
    country: str          # texto visible (ej. "españa") — vacío si desconocido
    country_filter: str   # clave canónica para filtros (ej. "españa")
    lat: float
    lon: float
    km: float
    gain: int
    time: str             # "2h 08m"
    score: float
    level: str            # easy | moderate | hard | very-hard
    level_label: str      # "moderada" / "difícil" ...
    date: str             # "29 mar 2025" (fallback SSR)
    date_sort: str        # "2025-03-29"
    started_at_iso: str   # "2025-03-29T21:30:00Z" para conversión TZ en cliente
    year: int
    ele_min: int
    ele_max: int
    line: str             # SVG path "M ..."
    area: str             # SVG path "M ..."
    pace: str             # "16:46/km"


@dataclass
class RegionFacet:
    key: str
    label: str
    count: int


@dataclass
class RutasData:
    has_data: bool
    total: int
    total_km: float
    total_gain: int
    region_count: int
    country_count: int
    year_min: Optional[int]
    year_max: Optional[int]
    regions: List[RegionFacet]
    countries: List[RegionFacet]    # mismas claves que regions, pero por país
    featured: Optional[RutaItem]
    rotation_pool: List[RutaItem]   # las N más recientes (para rotación cliente)


@dataclass
class RutaQuery:
    """Parámetros de búsqueda paginada (vienen del API)."""
    q: str = ""
    difficulty: List[str] = field(default_factory=list)  # vacío = todas
    region: str = "all"
    country: str = "all"
    distance: str = "all"   # "0-8", "8-12", ... | "all"
    gain: str = "all"       # "0-700", ... | "all"
    date: str = "all"       # "2025" | "last-90" | "last-365" | "summer" | "winter" | "all"
    sort: str = "date-desc"
    offset: int = 0
    limit: int = 10


@dataclass
class RutaQueryResult:
    items: List[RutaItem]
    total: int       # total de rutas en la BD (sin filtros)
    matched: int     # total que pasan los filtros actuales
    has_more: bool   # ¿quedan más allá de offset+limit?


@dataclass
class ResumenData:
    has_data: bool
    hero: HeroSummary
    kpis: List[KpiCard]
    map_markers: List[MapMarker]
    monthly: List[MonthlyPoint]
    donut: List[DonutSlice]
    donut_total: int
    calendar: List[CalendarYear]
    km_by_day: List[Dict]          # [{"iso": "YYYY-MM-DD", "km": float}] UTC
    km_by_weekday: List[float]     # [lun, mar, mie, jue, vie, sab, dom]
    km_by_month_hist: List[float]  # [ene, feb, ..., dic] histórico total
    recent_routes: List[RouteRow]
    hero_profiles: List[HeroProfile]
    longest_route_name: Optional[str] = None


# Los helpers de formateo ahora viven en `app/format.py` y están importados
# como alias `_fmt_*` arriba para no tener que reescribir los call sites.

def _location_subtitle(region: Optional[str], sub_region: Optional[str]) -> str:
    """Texto de ubicación: 'region · sub_region', 'region' o '' si no hay datos."""
    bits = [b for b in (region, sub_region) if b]
    if not bits:
        return ""
    if region and sub_region:
        return f"{region} · {sub_region}"
    return bits[0]


# ============= computation =============

def _empty_resumen() -> ResumenData:
    """ResumenData vacío para cuando la BD no tiene rutas."""
    return ResumenData(
        has_data=False,
        hero=HeroSummary(0.0, 0, 0, 0, 0, 0.0),
        kpis=[
            KpiCard("ruta más larga", "—", "", "Importa tu primer GPX",
                    description="La ruta con mayor distancia recorrida."),
            KpiCard("mayor desnivel", "—", "", "Importa tu primer GPX",
                    description="La ruta con más metros de subida acumulados."),
            KpiCard("ritmo medio", "—", "", "Importa tu primer GPX",
                    description="Minutos por kilómetro en movimiento, sobre el total."),
            KpiCard("intensidad media", "—", "", "Importa tu primer GPX",
                    description="Metros de desnivel positivo por cada km recorrido."),
            KpiCard("racha activa", "0", "sem", "Sin actividad registrada",
                    description="Semanas seguidas con al menos una ruta hasta hoy."),
            KpiCard("km este mes", "0", "km", "Sin actividad registrada",
                    description="Kilómetros sumados en el último mes con actividad."),
        ],
        map_markers=[],
        monthly=[],
        donut=[],
        donut_total=0,
        calendar=[],
        km_by_day=[],
        km_by_weekday=[0.0] * 7,
        km_by_month_hist=[0.0] * 12,
        recent_routes=[],
        hero_profiles=[],
    )


def build_resumen(db: Session, user_id: int) -> ResumenData:
    """Construye todas las agregaciones de la vista Resumen acotadas al usuario.

    Calcula hero stats, KPIs, marcadores del mapa, gráfica mensual, donut
    de dificultad, calendario heatmap y tabla de rutas recientes.
    Minimiza la transferencia desde SQLite usando proyecciones parciales
    y GROUP BY en lugar de hidratar el modelo Route completo.
    """
    # Agregados HERO en SQL — antes esto cargaba toda la tabla `routes` en
    # memoria solo para sumar. Con miles de rutas el coste era O(n) en Python
    # y O(n) en transferencia desde SQLite. Ahora SQLite hace los SUM/MAX y
    # devuelve seis escalares.
    total = (
        db.query(func.count(Route.id))
        .filter(Route.user_id == user_id)
        .scalar() or 0
    )
    if total == 0:
        return _empty_resumen()

    today = date.today()

    total_km_raw, total_gain_raw, total_moving_raw, max_alt_raw = (
        db.query(
            func.coalesce(func.sum(Route.distance_km), 0.0),
            func.coalesce(func.sum(Route.elevation_gain_m), 0),
            func.coalesce(func.sum(Route.moving_time_s), 0),
            func.coalesce(func.max(Route.max_altitude_m), 0),
        )
        .filter(Route.user_id == user_id)
        .first()
    )
    total_km = float(total_km_raw or 0.0)
    total_gain = int(total_gain_raw or 0)
    total_moving = int(total_moving_raw or 0)
    max_alt = int(max_alt_raw or 0)
    world_pct = round((total_km / WORLD_CIRCUMFERENCE_KM) * 100, 2)

    # ----- KPIs -----
    longest = (
        db.query(Route)
        .filter(Route.user_id == user_id)
        .order_by(Route.distance_km.desc(), Route.id.desc())
        .limit(1).one()
    )
    biggest_gain = (
        db.query(Route)
        .filter(Route.user_id == user_id)
        .order_by(Route.elevation_gain_m.desc(), Route.id.desc())
        .limit(1).one()
    )
    avg_pace = _fmt_pace(total_km, total_moving) if total_km > 0 else "—"
    avg_intensity = int(round(total_gain / total_km)) if total_km > 0 else 0

    intensity_qualifier = "exigente" if avg_intensity > 90 else (
        "moderado" if avg_intensity > 50 else "suave"
    )

    # Cargamos solo las columnas que necesitamos para los agregados que NO
    # se pueden expresar limpiamente en SQL (mapa, weekday histogram, etc.).
    # Antes hidratábamos el modelo Route completo (~30 columnas, incluido el
    # SVG path como Text). Esto recorta la transferencia y la huella de RAM.
    light_rows = (
        db.query(
            Route.started_at, Route.start_lat, Route.start_lon,
            Route.distance_km, Route.elevation_gain_m, Route.difficulty_score,
            Route.difficulty_level, Route.name,
        )
        .filter(Route.user_id == user_id)
        .order_by(Route.started_at.desc())
        .all()
    )

    # racha activa: SELECT DISTINCT (year, week) en SQL.
    weeks_with_activity = {
        (r.started_at.isocalendar()[0], r.started_at.isocalendar()[1])
        for r in light_rows
    }
    streak = 0
    cursor = today - timedelta(days=today.weekday())  # lunes de esta semana
    # Si la semana actual aún no tiene actividad no rompemos la racha:
    # puede que todavía no haya terminado. Empezamos a contar desde la
    # semana anterior en ese caso.
    iso_current = cursor.isocalendar()
    if (iso_current[0], iso_current[1]) not in weeks_with_activity:
        cursor -= timedelta(weeks=1)
    while True:
        iso = cursor.isocalendar()
        if (iso[0], iso[1]) in weeks_with_activity:
            streak += 1
            cursor -= timedelta(weeks=1)
        else:
            break

    # último mes con actividad: GROUP BY (year, month) sumando km.
    km_by_ym_rows = (
        db.query(
            extract("year", Route.started_at).label("y"),
            extract("month", Route.started_at).label("m"),
            func.coalesce(func.sum(Route.distance_km), 0.0),
        )
        .filter(Route.user_id == user_id)
        .group_by("y", "m")
        .all()
    )
    km_by_ym: dict[tuple[int, int], float] = {
        (int(y), int(m)): float(km or 0.0) for y, m, km in km_by_ym_rows
    }
    if km_by_ym:
        last_active_ym = max(km_by_ym.keys())
        last_active_km = km_by_ym[last_active_ym]
    else:
        last_active_ym = (today.year, today.month)
        last_active_km = 0.0
    last_active_label = f"{MONTH_LABELS_ES[last_active_ym[1]-1]} {last_active_ym[0]}"

    kpis = [
        KpiCard("ruta más larga", _fmt_km_short(longest.distance_km), "km",
                longest.name,
                description="La ruta con mayor distancia recorrida."),
        KpiCard("mayor desnivel", _fmt_int(biggest_gain.elevation_gain_m), "m",
                biggest_gain.name,
                description="La ruta con más metros de subida acumulados."),
        KpiCard("ritmo medio", avg_pace, "/km",
                "ponderado por distancia",
                description="Tiempo en movimiento por kilómetro, sobre el total."),
        KpiCard("intensidad media", str(avg_intensity), "m/km",
                f"terreno: {intensity_qualifier}", detail_kind="pos",
                description="Metros de desnivel positivo por cada km recorrido."),
        KpiCard("racha activa", str(streak), "sem",
                "semanas consecutivas hasta hoy",
                description="Semanas seguidas con al menos una ruta hasta hoy."),
        KpiCard("km este mes", _fmt_km_short(last_active_km), "km",
                f"{last_active_label} · último mes activo",
                description="Kilómetros sumados en el último mes con actividad."),
    ]

    # ----- MAP markers ----- (usamos light_rows: solo columnas necesarias)
    map_markers = [
        MapMarker(
            name=r.name, lat=r.start_lat, lon=r.start_lon,
            km=r.distance_km, gain=r.elevation_gain_m,
            score=r.difficulty_score, level=r.difficulty_level,
            date_str=_fmt_date_es(r.started_at),
            started_at_iso=r.started_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        for r in light_rows
    ]

    # ----- Monthly chart: ultimos 14 meses ----- (km_by_ym ya viene del SQL de KPIs)
    monthly: List[MonthlyPoint] = []
    cur = date(today.year, today.month, 1)
    months_back: List[tuple[int, int]] = []
    for _ in range(14):
        months_back.append((cur.year, cur.month))
        if cur.month == 1:
            cur = date(cur.year - 1, 12, 1)
        else:
            cur = date(cur.year, cur.month - 1, 1)
    months_back.reverse()
    for y, m in months_back:
        label = f"{MONTH_LABELS_ES[m-1]} {str(y)[2:]}"
        monthly.append(MonthlyPoint(label=label, value=round(km_by_ym.get((y, m), 0.0), 2)))

    # km por día UTC + por día de la semana + por mes del año en una sola pasada
    # sobre light_rows (el cliente reagrupa por día local en su zona horaria).
    km_by_day_raw: dict[date, float] = defaultdict(float)
    km_by_weekday = [0.0] * 7
    km_by_month_hist = [0.0] * 12
    for r in light_rows:
        d = r.started_at.date()
        km_by_day_raw[d] += r.distance_km
        km_by_weekday[r.started_at.weekday()] += r.distance_km
        km_by_month_hist[r.started_at.month - 1] += r.distance_km
    km_by_day = [
        {"iso": d.isoformat(), "km": round(v, 2)}
        for d, v in km_by_day_raw.items()
    ]
    km_by_weekday = [round(v, 1) for v in km_by_weekday]
    km_by_month_hist = [round(v, 1) for v in km_by_month_hist]

    # ----- Donut por dificultad ----- (GROUP BY en SQL — evita cargar la tabla)
    diff_rows = (
        db.query(Route.difficulty_level, func.count(Route.id))
        .filter(Route.user_id == user_id)
        .group_by(Route.difficulty_level)
        .all()
    )
    counter = Counter({lvl: int(cnt) for lvl, cnt in diff_rows})
    donut_total = sum(counter.values())
    label_map = {
        "easy": "Fácil", "moderate": "Moderada",
        "hard": "Difícil", "very-hard": "Muy difícil",
    }
    donut = []
    for level in ("easy", "moderate", "hard", "very-hard"):
        c = counter.get(level, 0)
        if c == 0:
            continue
        pct = int(round((c / donut_total) * 100))
        donut.append(DonutSlice(
            label=label_map[level], count=c, pct=pct, css_class=level,
        ))

    # ----- Calendario heatmap: muestra los anos con datos (max 3 mas recientes) -----
    years_present = sorted({y for (y, _m) in km_by_ym.keys()}, reverse=True)[:3]
    years_present.sort()
    calendar: List[CalendarYear] = []
    for year in years_present:
        cells = []
        for m in range(1, 13):
            v = km_by_ym.get((year, m), 0)
            level = ""
            if v > 14:
                level = "lvl4"
            elif v > 12:
                level = "lvl3"
            elif v > 8:
                level = "lvl2"
            elif v > 0:
                level = "lvl1"
            title = f"{_fmt_km(v)} km" if v > 0 else "sin actividad"
            cells.append({
                "month": MONTH_LABELS_ES[m - 1],
                "level": level,
                "title": title,
            })
        calendar.append(CalendarYear(year=year, cells=cells))

    # ----- Tabla ultimas rutas (max 8) ----- query acotada con LIMIT 8.
    recent_rows = (
        db.query(Route)
        .filter(Route.user_id == user_id)
        .order_by(Route.started_at.desc())
        .limit(8)
        .all()
    )
    recent_routes = [
        RouteRow(
            id=r.id,
            name=r.name,
            sub=_location_subtitle(r.region, r.sub_region),
            date_str=_fmt_date_es(r.started_at),
            started_at_iso=r.started_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            distance_str=f"{_fmt_km(r.distance_km)} km",
            gain_str=f"{_fmt_int(r.elevation_gain_m)} m",
            moving_str=_fmt_duration(r.moving_time_s),
            score=r.difficulty_score,
            level=r.difficulty_level,
            score_str=f"{r.difficulty_score:.1f}".replace(".", ","),
            ele_min=int(r.min_altitude_m or 0),
            ele_max=int(r.max_altitude_m or 0),
            line=r.elev_line_path or "",
            area=r.elev_area_path or "",
        )
        for r in recent_rows
    ]

    # ----- Perfiles para rotacion del hero ----- solo nombre + paths SVG.
    profile_rows = (
        db.query(Route.name, Route.elev_line_path, Route.elev_area_path)
        .filter(Route.user_id == user_id, Route.elev_line_path.isnot(None))
        .order_by(Route.started_at.desc())
        .all()
    )
    hero_profiles = [
        HeroProfile(name=name, line=line or "", area=area or "")
        for name, line, area in profile_rows
    ]

    return ResumenData(
        has_data=True,
        hero=HeroSummary(
            total_km=round(total_km, 1),
            route_count=int(total),
            elevation_gain_total=int(total_gain),
            moving_time_total_s=int(total_moving),
            max_altitude=int(max_alt),
            world_pct=world_pct,
        ),
        kpis=kpis,
        map_markers=map_markers,
        monthly=monthly,
        donut=donut,
        donut_total=donut_total,
        calendar=calendar,
        km_by_day=km_by_day,
        km_by_weekday=km_by_weekday,
        km_by_month_hist=km_by_month_hist,
        recent_routes=recent_routes,
        hero_profiles=hero_profiles,
        longest_route_name=longest.name,
    )


# Helpers expuestos para Jinja — wrappers finos sobre app.format.
def fmt_km(km: float) -> str: """→ app.format.fmt_km"""; return _fmt_km(km)  # noqa: E704
def fmt_km_short(km: float) -> str: """→ app.format.fmt_km_short"""; return _fmt_km_short(km)  # noqa: E704
def fmt_int(n: int) -> str: """→ app.format.fmt_int"""; return _fmt_int(n)  # noqa: E704
def fmt_duration(s: int) -> str: """→ app.format.fmt_duration"""; return _fmt_duration(s)  # noqa: E704
def fmt_date_es(d) -> str: """→ app.format.fmt_date_es"""; return _fmt_date_es(d)  # noqa: E704
def difficulty_label_es(level: str) -> str: """→ app.format.difficulty_label_es"""; return _difficulty_label_es(level)  # noqa: E704
def fmt_world_pct(pct: float) -> str:
    """→ app.format.fmt_world_pct"""
    return f"{pct:.2f}".replace(".", ",")
def fmt_hero_km(km: float) -> str:
    """→ app.format.fmt_hero_km"""
    return f"{km:.1f}".replace(".", ",")


# ============= helpers RUTAS =============

def _region_key(region: Optional[str]) -> str:
    """Devuelve una clave canónica para filtrar (NFKD + sin diacríticos + lower)."""
    canon = canonical_geo(region)
    if not canon:
        return "otros"
    # Si tiene varias palabras tomamos la primera token significativa
    return canon.split(" ")[0]


def _country_key(country: Optional[str]) -> str:
    """Clave canónica para filtrar por país. Devuelve 'otros' si vacío."""
    return canonical_geo(country) or "otros"


def _region_label(region: Optional[str], sub_region: Optional[str]) -> str:
    """Texto visible: 'huesca · pirineo aragonés', 'navarra', ..."""
    bits = [b for b in (region, sub_region) if b]
    if not bits:
        return "—"
    return " · ".join(bits)


def _calc_pace(km: float, moving_s: int) -> str:
    """Formatea el ritmo como 'MM:SS/km'. Devuelve '—' si km o tiempo son 0."""
    if km <= 0 or moving_s <= 0:
        return "—"
    spk = moving_s / km
    mm = int(spk // 60)
    ss = int(round(spk - mm * 60))
    if ss == 60:
        mm += 1
        ss = 0
    return f"{mm}:{ss:02d}/km"


def _to_ruta_item(r: Route) -> RutaItem:
    """Convierte un modelo Route ORM en el DTO RutaItem para la vista Rutas."""
    return RutaItem(
        id=r.id,
        name=r.name,
        origin=(r.sub_region or r.region or "—"),
        region=_region_label(r.region, r.sub_region),
        region_filter=_region_key(r.region),
        country=(r.country or "").strip().lower() or "",
        country_filter=_country_key(r.country),
        lat=r.start_lat,
        lon=r.start_lon,
        km=round(r.distance_km, 2),
        gain=int(r.elevation_gain_m or 0),
        time=_fmt_duration(r.moving_time_s or 0),
        score=round(float(r.difficulty_score or 0.0), 1),
        level=r.difficulty_level,
        level_label=_difficulty_label_es(r.difficulty_level),
        date=_fmt_date_es(r.started_at),
        date_sort=r.started_at.strftime("%Y-%m-%d"),
        started_at_iso=r.started_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        year=r.started_at.year,
        ele_min=int(r.min_altitude_m or 0),
        ele_max=int(r.max_altitude_m or 0),
        line=r.elev_line_path or "",
        area=r.elev_area_path or "",
        pace=_calc_pace(r.distance_km, r.moving_time_s or 0),
    )


def _empty_rutas() -> RutasData:
    """RutasData vacío para cuando la BD no tiene rutas."""
    return RutasData(
        has_data=False,
        total=0,
        total_km=0.0,
        total_gain=0,
        region_count=0,
        country_count=0,
        year_min=None,
        year_max=None,
        regions=[],
        countries=[],
        featured=None,
        rotation_pool=[],
    )


def build_rutas(db: Session, user_id: int) -> RutasData:
    """Construye SOLO los metadatos de la página de rutas (cabecera + facets +
    pool de rotación). El listado se sirve aparte vía /api/rutas paginado.

    Acotado al usuario indicado.
    """
    total = db.query(Route).filter(Route.user_id == user_id).count()
    if total == 0:
        return _empty_rutas()

    # Agregaciones generales en SQL
    total_km_raw, total_gain_raw = (
        db.query(
            func.coalesce(func.sum(Route.distance_km), 0.0),
            func.coalesce(func.sum(Route.elevation_gain_m), 0),
        )
        .filter(Route.user_id == user_id)
        .first()
    )
    total_km = round(float(total_km_raw or 0.0), 2)
    total_gain = int(total_gain_raw or 0)

    # Año mín/máx
    min_dt, max_dt = (
        db.query(func.min(Route.started_at), func.max(Route.started_at))
        .filter(Route.user_id == user_id)
        .first()
    )
    year_min = min_dt.year if min_dt else None
    year_max = max_dt.year if max_dt else None

    # Facets de región (group by Route.region en SQL)
    region_rows = (
        db.query(Route.region, Route.sub_region, func.count(Route.id))
        .filter(Route.user_id == user_id)
        .group_by(Route.region, Route.sub_region)
        .all()
    )
    counter_keys: Counter = Counter()
    label_by_key: Dict[str, str] = {}
    for region_text, sub_text, count in region_rows:
        key = _region_key(region_text)
        counter_keys[key] += count
        label = _region_label(region_text, sub_text)
        if key not in label_by_key or len(label) > len(label_by_key[key]):
            label_by_key[key] = label
    regions = [
        RegionFacet(key=k, label=label_by_key.get(k, k), count=c)
        for k, c in counter_keys.most_common()
    ]

    # Facets de país (group by Route.country)
    country_rows = (
        db.query(Route.country, func.count(Route.id))
        .filter(Route.user_id == user_id)
        .group_by(Route.country)
        .all()
    )
    country_counter: Counter = Counter()
    for country_text, count in country_rows:
        country_counter[_country_key(country_text)] += count
    countries = [
        RegionFacet(key=k, label=k.capitalize() if k != "otros" else "otros", count=c)
        for k, c in country_counter.most_common()
    ]

    # Pool de rotación: las N más recientes
    pool_rows = (
        db.query(Route)
        .filter(Route.user_id == user_id)
        .order_by(Route.started_at.desc())
        .limit(ROTATION_POOL_SIZE)
        .all()
    )
    rotation_pool = [_to_ruta_item(r) for r in pool_rows]
    featured = rotation_pool[0] if rotation_pool else None

    return RutasData(
        has_data=True,
        total=total,
        total_km=total_km,
        total_gain=total_gain,
        region_count=len(counter_keys),
        country_count=len(country_counter),
        year_min=year_min,
        year_max=year_max,
        regions=regions,
        countries=countries,
        featured=featured,
        rotation_pool=rotation_pool,
    )


# ============= endpoint API: filtrado/orden/paginación SQL =============

_SORT_MAP = {
    "date-desc": Route.started_at.desc(),
    "date-asc": Route.started_at.asc(),
    "km-desc": Route.distance_km.desc(),
    "km-asc": Route.distance_km.asc(),
    "gain-desc": Route.elevation_gain_m.desc(),
    "gain-asc": Route.elevation_gain_m.asc(),
    "score-desc": Route.difficulty_score.desc(),
}

_ALLOWED_LEVELS = {"easy", "moderate", "hard", "very-hard"}


def _parse_range(token: str) -> Optional[tuple[float, float]]:
    """Parsea un token 'a-b' en (float, float). Devuelve None para 'all' o formato inválido."""
    if not token or token == "all":
        return None
    try:
        a, b = token.split("-")
        return float(a), float(b)
    except (ValueError, AttributeError):
        return None


def _apply_filters(qry, q: RutaQuery):
    """Aplica todos los filtros de RutaQuery a una Query de SQLAlchemy."""

    # Dificultad: lista; vacía o todas-las-4 = no filtrar
    if q.difficulty:
        diffs = [d for d in q.difficulty if d in _ALLOWED_LEVELS]
        if diffs and set(diffs) != _ALLOWED_LEVELS:
            qry = qry.filter(Route.difficulty_level.in_(diffs))

    # País (clave canónica: NFKD + sin diacríticos + lower)
    if q.country and q.country != "all":
        ckey = canonical_geo(q.country)
        if ckey == "otros":
            qry = qry.filter(or_(Route.country.is_(None), func.trim(Route.country) == ""))
        elif ckey:
            qry = qry.filter(func.lower(Route.country) == ckey)

    # Región (clave canónica: NFKD + sin diacríticos + lower)
    if q.region and q.region != "all":
        key = canonical_geo(q.region)
        if key == "otros":
            qry = qry.filter(or_(Route.region.is_(None), func.trim(Route.region) == ""))
        elif key:
            qry = qry.filter(func.lower(Route.region) == key)

    # Distancia [a, b)
    dist_range = _parse_range(q.distance)
    if dist_range:
        a, b = dist_range
        qry = qry.filter(Route.distance_km >= a, Route.distance_km < b)

    # Desnivel [a, b)
    gain_range = _parse_range(q.gain)
    if gain_range:
        a, b = gain_range
        qry = qry.filter(Route.elevation_gain_m >= a, Route.elevation_gain_m < b)

    # Fecha
    if q.date and q.date != "all":
        if q.date.isdigit() and len(q.date) == 4:
            year = int(q.date)
            qry = qry.filter(extract("year", Route.started_at) == year)
        elif q.date == "last-90":
            qry = qry.filter(Route.started_at >= datetime.now() - timedelta(days=90))
        elif q.date == "last-365":
            qry = qry.filter(Route.started_at >= datetime.now() - timedelta(days=365))
        elif q.date == "summer":
            qry = qry.filter(extract("month", Route.started_at).in_([6, 7, 8, 9]))
        elif q.date == "winter":
            qry = qry.filter(extract("month", Route.started_at).in_([12, 1, 2]))

    # Búsqueda libre — canonicalizamos también el término del usuario para que
    # coincida con los valores canónicos persistidos en country/region/sub_region.
    if q.q:
        term = canonical_geo(q.q) or q.q.strip().lower()
        like = f"%{term}%"
        qry = qry.filter(or_(
            func.lower(Route.name).like(like),
            func.lower(func.coalesce(Route.country, "")).like(like),
            func.lower(func.coalesce(Route.region, "")).like(like),
            func.lower(func.coalesce(Route.sub_region, "")).like(like),
        ))

    return qry


def query_rutas(db: Session, user_id: int, q: RutaQuery) -> RutaQueryResult:
    """Listado paginado, filtrado y ordenado a nivel SQL — acotado al usuario."""
    total = (
        db.query(func.count(Route.id))
        .filter(Route.user_id == user_id)
        .scalar() or 0
    )

    base = _apply_filters(
        db.query(Route).filter(Route.user_id == user_id), q,
    )
    matched = base.with_entities(func.count(Route.id)).scalar() or 0

    sort_clause = _SORT_MAP.get(q.sort, _SORT_MAP["date-desc"])
    # Desempate estable por id para que la paginación sea determinista cuando
    # hay rutas con la misma fecha/km/desnivel/score.
    rows = (
        base.order_by(sort_clause, Route.id.desc())
        .offset(max(0, q.offset))
        .limit(max(1, min(q.limit, 50)))
        .all()
    )
    items = [_to_ruta_item(r) for r in rows]

    return RutaQueryResult(
        items=items,
        total=int(total),
        matched=int(matched),
        has_more=(q.offset + len(items)) < int(matched),
    )
