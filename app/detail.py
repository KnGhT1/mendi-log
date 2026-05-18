"""
Constructor de datos para la vista de detalle de una ruta.

Carga la ruta + sus track_points y calcula:

  - Stats hero (numero ruta, fecha, region, score, ritmo medio...).
  - Hitos del recorrido (salida, cima, fin) con km y hora.
  - Stats tecnicos: dist 2D/3D, ritmo subida/bajada, VAM, pendiente, fatiga.
  - Perfil de elevacion para chart interactivo (km, alt, time, gradient).
  - Track polyline para mapa (lat/lon, ya downsampleado en parser).
  - Notas y tags.
  - Rutas relacionadas (similar dificultad y region).

Todos los formateos numericos respetan locale es_ES (coma decimal, miles con
punto), igual que en `stats.py`.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional, Tuple

from sqlalchemy import asc, func
from sqlalchemy.orm import Session

from app.models import Route, Summit, TrackPoint
from app.text_utils import canonical_geo
from app.stats import (
    MONTH_LABELS_ES,
    _difficulty_label_es,
    _fmt_date_es,
    _fmt_duration,
    _fmt_int,
    _fmt_km,
    _fmt_km_short,
    _location_subtitle,
    _region_key,
    _region_label,
)

# Umbral de pendiente (m/m) por debajo del cual consideramos terreno llano y
# no contabilizamos en ritmo subida/bajada.
FLAT_GRADIENT = 0.02
# Distancia minima entre puntos para calcular pendiente fiable.
MIN_GRADIENT_DISTANCE_M = 5.0
# Numero de tramos en los que dividimos la ruta para detectar el "tramo mas duro".
HARDEST_SECTOR_SLICES = 8


@dataclass
class Milestone:
    kind: str          # "start" | "summit" | "end"
    label: str         # "salida", "cumbre", "llegada"
    name: str
    elev_m: int
    km: float
    km_str: str
    time_str: str      # "09:14"


@dataclass
class HeroData:
    number: int                # "ruta #01"
    date_str: str
    region: str                # ej. "navarra"
    season: str                # primavera | verano | otono | invierno
    title: str
    subregion: Optional[str]   # ej. "iltzarbe" o None
    score: float
    score_str: str             # "4,1"
    level: str                 # easy | moderate | hard | very-hard
    level_label: str           # "moderada"
    distance_str: str          # "7,63"
    gain_str: str              # "641"
    time_str: str              # "2h 08m"
    pace_str: str              # "16:46"
    max_alt_str: str           # "1.121"


@dataclass
class TechData:
    distance_2d_str: str
    distance_3d_str: str
    total_time_str: str
    moving_time_str: str
    stop_time_str: str             # "12 min de paradas"
    start_end_str: str             # "salida 09:14 · llegada 11:22" o "—"
    pace_avg: str                  # "16:46"
    pace_avg_kmh: str              # "3,57"
    pace_up: str                   # "21:14" o "—"
    pace_up_kmh: str               # "2,82"
    pace_down: str                 # "12:18" o "—"
    pace_down_kmh: str             # "4,87"
    vam_str: str                   # "453"
    gain_str: str                  # "641"
    loss_str: str                  # "641"
    alt_range_str: str             # "575 — 1.121"
    alt_range_diff_str: str        # "rango: 546 m"
    slope_avg_str: str             # "8,4"
    slope_max_str: str             # "22,7"
    hardest_sector_label: str      # "tramo mas duro: km 3"
    fatigue_str: str               # "4,1"
    fatigue_level: str             # easy | moderate | hard | very-hard
    gpx_points: int
    gpx_density_str: str           # "~6,1 m / punto"


@dataclass
class ElevStrip:
    gain_str: str
    loss_str: str
    alt_min_str: str
    alt_max_str: str
    slope_avg_str: str
    slope_max_str: str


@dataclass
class ElevAxisLabels:
    y: List[str]   # 5 labels de arriba a abajo (alt_max, ..., alt_min)
    x: List[str]   # 5 labels de izquierda a derecha (0 km, ..., total km)


@dataclass
class ElevSummitMark:
    summit_id: int
    x: float        # 0..800
    y: float        # 0..280
    alt_str: str
    name: str


@dataclass
class ElevProfile:
    line: str                   # SVG path "M ..." (viewBox 800x280)
    area: str
    summit_x: float             # cima principal (compat)
    summit_y: float
    summit_alt_str: str
    summits: List[ElevSummitMark]  # todas las cimas
    samples: List[dict]
    axis: ElevAxisLabels


@dataclass
class TrackPoly:
    points: List[List[float]]   # [[lat, lon], ...]
    bbox: List[List[float]]     # [[lat_min, lon_min], [lat_max, lon_max]]


@dataclass
class MapInfoRow:
    label: str
    value: str


@dataclass
class MapData:
    track: TrackPoly
    milestones: List[Milestone]
    info_rows: List[MapInfoRow]
    points_count: int


@dataclass
class RelatedItem:
    id: int
    name: str
    origin: str       # "desde X"
    score_str: str
    level: str
    line: str         # SVG path
    area: str
    km_str: str
    gain_str: str
    time_str: str


@dataclass
class NotesData:
    text: str
    tags: List[str]
    updated_str: str    # "memo · 06.05.2026"


@dataclass
class DetailData:
    id: int
    name: str
    name_original: str
    hero: HeroData
    map: MapData
    elev_strip: ElevStrip
    elev: ElevProfile
    tech: TechData
    notes: NotesData
    related: List[RelatedItem]
    prev_id: Optional[int]
    prev_name: Optional[str]
    next_id: Optional[int]
    next_name: Optional[str]
    weather_meta: str            # "open-meteo · era5 archive · 29 mar 2025"
    started_at_iso: str          # ISO date para fetch de clima
    summit_lat: float            # punto de mayor altitud (anchor de clima)
    summit_lon: float


# ============= helpers =============

def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia haversine en metros entre dos coordenadas.

    Duplicado local de `clustering._haversine_m` para evitar dependencia
    cruzada entre módulos de presentación y de negocio.
    """
    R = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _season_es(d: datetime | date) -> str:
    """Devuelve la estación del año en español para una fecha dada."""
    m = d.month
    if m in (3, 4, 5):
        return "primavera"
    if m in (6, 7, 8):
        return "verano"
    if m in (9, 10, 11):
        return "otoño"
    return "invierno"


def _fmt_pace_min(secs_per_km: float) -> str:
    """Formatea segundos/km como 'MM:SS'. Devuelve '—' para valores no finitos o ≤ 0."""
    if secs_per_km <= 0 or not math.isfinite(secs_per_km):
        return "—"
    m = int(secs_per_km // 60)
    s = int(round(secs_per_km - m * 60))
    if s == 60:
        m += 1
        s = 0
    return f"{m}:{s:02d}"


def _fmt_kmh(km: float, hours: float) -> str:
    """Formatea velocidad en km/h con 2 decimales y coma decimal española."""
    if hours <= 0 or km <= 0:
        return "—"
    return f"{km / hours:.2f}".replace(".", ",")


def _fmt_pct(v: float, decimals: int = 1) -> str:
    """Formatea un valor como porcentaje con `decimals` decimales y coma española."""
    return f"{v:.{decimals}f}".replace(".", ",")


def _fmt_hhmm(d: Optional[datetime]) -> str:
    """Formatea un datetime como 'HH:MM'. Devuelve '—' si es None."""
    if not d:
        return "—"
    return f"{d.hour:02d}:{d.minute:02d}"


def _fmt_date_short(d: datetime | date) -> str:
    """Formatea una fecha como 'DD.MM.YYYY' para el pie de notas."""
    return f"{d.day:02d}.{d.month:02d}.{d.year}"


# ============= calculo =============

def _pick_summit(points: List[TrackPoint]) -> Optional[TrackPoint]:
    """Devuelve el punto de mayor altitud (la cima)."""
    valid = [p for p in points if p.elevation_m is not None]
    if not valid:
        return points[len(points) // 2] if points else None
    return max(valid, key=lambda p: p.elevation_m or -9999)


def _km_at_seq(points: List[TrackPoint], cum_dist_m: List[float], seq: int) -> float:
    """Distancia acumulada en km hasta el primer TrackPoint con seq >= `seq`."""
    for i, p in enumerate(points):
        if p.seq >= seq:
            return cum_dist_m[i] / 1000.0
    return cum_dist_m[-1] / 1000.0


def _compute_distances(points: List[TrackPoint]) -> Tuple[List[float], List[float], float, float]:
    """Calcula distancias acumuladas 2D y 3D para la lista de TrackPoints.

    Devuelve (cum_dist_m_2d, cum_dist_m_3d, total_2d_m, total_3d_m).
    La distancia 3D incorpora el desnivel entre puntos consecutivos.
    """
    if not points:
        return [], [], 0.0, 0.0
    cum2d = [0.0]
    cum3d = [0.0]
    for i in range(1, len(points)):
        a, b = points[i - 1], points[i]
        d = _haversine_m(a.lat, a.lon, b.lat, b.lon)
        cum2d.append(cum2d[-1] + d)
        ea = a.elevation_m if a.elevation_m is not None else 0.0
        eb = b.elevation_m if b.elevation_m is not None else 0.0
        dz = eb - ea
        d3 = math.sqrt(d * d + dz * dz)
        cum3d.append(cum3d[-1] + d3)
    return cum2d, cum3d, cum2d[-1], cum3d[-1]


def _compute_pace_split(
    points: List[TrackPoint],
    cum2d: List[float],
) -> Tuple[float, float, float, float]:
    """Separa distancia y tiempo en tramos de subida y bajada.

    Devuelve (km_up, secs_up, km_down, secs_down). Los tramos con pendiente
    absoluta < FLAT_GRADIENT o sin timestamp se ignoran en ambos contadores.
    """
    km_up = secs_up = km_down = secs_down = 0.0
    for i in range(1, len(points)):
        a, b = points[i - 1], points[i]
        d = cum2d[i] - cum2d[i - 1]
        if d < MIN_GRADIENT_DISTANCE_M:
            continue
        if a.elevation_m is None or b.elevation_m is None:
            continue
        dz = b.elevation_m - a.elevation_m
        grad = dz / d
        if not (a.time and b.time):
            continue
        dt = (b.time - a.time).total_seconds()
        if dt <= 0:
            continue
        km = d / 1000.0
        if grad > FLAT_GRADIENT:
            km_up += km
            secs_up += dt
        elif grad < -FLAT_GRADIENT:
            km_down += km
            secs_down += dt
    return km_up, secs_up, km_down, secs_down


def _compute_slopes(points: List[TrackPoint], cum2d: List[float]) -> Tuple[float, float, int]:
    """Devuelve (avg_pct, max_pct, hardest_sector_km).

    avg = pendiente media absoluta de los tramos en subida (>= flat threshold).
    max = mayor pendiente positiva instantanea sostenida en una ventana de 50 m.
    hardest_sector_km = km del tramo con mayor desnivel positivo acumulado.
    """
    if len(points) < 2:
        return 0.0, 0.0, 1

    total_dist = cum2d[-1]
    if total_dist <= 0:
        return 0.0, 0.0, 1

    grads_up: List[float] = []
    max_grad = 0.0
    win_dist = 0.0
    win_dz = 0.0
    # `start` apunta al primer punto de la ventana actual. Al añadir un nuevo
    # tramo (start..i) avanzamos `start` mientras la ventana siga > 50 m,
    # evitando el `break` antiguo que solo descontaba un tramo por iteración.
    start = 0

    for i in range(1, len(points)):
        a, b = points[i - 1], points[i]
        d = cum2d[i] - cum2d[i - 1]
        if d < MIN_GRADIENT_DISTANCE_M:
            continue
        if a.elevation_m is None or b.elevation_m is None:
            continue
        dz = b.elevation_m - a.elevation_m
        grad = dz / d
        if grad > FLAT_GRADIENT:
            grads_up.append(grad)

        # ventana deslizante de ~50 m para max
        win_dist += d
        win_dz += dz
        # Encoge la ventana desde `start` hasta dejarla justo por encima de 50 m
        # (mantiene al menos un tramo).
        while win_dist > 50 and start < i - 1:
            d0 = cum2d[start + 1] - cum2d[start]
            if d0 <= 0:
                start += 1
                continue
            e0, e1 = points[start].elevation_m, points[start + 1].elevation_m
            if e0 is not None and e1 is not None:
                win_dz -= e1 - e0
            win_dist -= d0
            start += 1
        if win_dist > 0:
            g = win_dz / win_dist
            if g > max_grad:
                max_grad = g

    avg_grad = sum(grads_up) / len(grads_up) if grads_up else 0.0

    # hardest sector
    n = len(points)
    sector_size_m = total_dist / HARDEST_SECTOR_SLICES if HARDEST_SECTOR_SLICES > 0 else total_dist
    sector_gains = [0.0] * HARDEST_SECTOR_SLICES
    for i in range(1, len(points)):
        a, b = points[i - 1], points[i]
        if a.elevation_m is None or b.elevation_m is None:
            continue
        dz = b.elevation_m - a.elevation_m
        if dz <= 0:
            continue
        idx = min(HARDEST_SECTOR_SLICES - 1, int(cum2d[i - 1] / sector_size_m))
        sector_gains[idx] += dz
    hardest_idx = max(range(HARDEST_SECTOR_SLICES), key=lambda k: sector_gains[k])
    hardest_km = max(1, int(round((hardest_idx + 0.5) * sector_size_m / 1000.0)))

    return avg_grad * 100.0, max_grad * 100.0, hardest_km


def _build_elev_profile(
    points: List[TrackPoint],
    cum2d: List[float],
    width: int = 800,
    height: int = 280,
    margin_top: int = 28,
    margin_bottom: int = 14,
    n_points: int = 200,
) -> ElevProfile:
    """Genera el perfil de elevacion para el chart interactivo."""
    elevations = [p.elevation_m if p.elevation_m is not None else 0.0 for p in points]
    if not cum2d or len(cum2d) < 2:
        return ElevProfile(
            line="", area="", summit_x=0, summit_y=0, summit_alt_str="—",
            summits=[], samples=[], axis=ElevAxisLabels(y=[], x=[]),
        )

    total = cum2d[-1]
    e_min = min(elevations)
    e_max = max(elevations)
    if e_max == e_min:
        e_max = e_min + 1

    inner_h = height - margin_top - margin_bottom

    samples: List[dict] = []
    j = 0
    for k in range(n_points):
        target = (total * k) / (n_points - 1)
        while j + 1 < len(cum2d) and cum2d[j + 1] < target:
            j += 1
        if j + 1 >= len(cum2d):
            ele = elevations[-1]
            t = points[-1].time
            jj = len(points) - 1
        else:
            d0, d1 = cum2d[j], cum2d[j + 1]
            e0, e1 = elevations[j], elevations[j + 1]
            ratio = (target - d0) / (d1 - d0) if d1 > d0 else 0.0
            ele = e0 + (e1 - e0) * ratio
            t = points[j].time
            jj = j

        x = (width * k) / (n_points - 1)
        y = margin_top + inner_h * (1 - (ele - e_min) / (e_max - e_min))

        # gradient (m/m) sobre tramo de ~100 m alrededor del punto
        grad_pct = 0.0
        if jj > 0 and jj < len(points) - 1:
            d_back = cum2d[jj] - cum2d[max(0, jj - 1)]
            d_fwd = cum2d[min(len(cum2d) - 1, jj + 1)] - cum2d[jj]
            d_total = d_back + d_fwd
            if d_total > 0:
                e_back = elevations[max(0, jj - 1)]
                e_fwd = elevations[min(len(elevations) - 1, jj + 1)]
                grad_pct = ((e_fwd - e_back) / d_total) * 100.0

        samples.append({
            "x": round(x, 1),
            "y": round(y, 1),
            "km": round(target / 1000.0, 2),
            "alt": int(round(ele)),
            "t_iso": t.isoformat() if t else None,
            "t_str": _fmt_hhmm(t),
            "grad_pct": round(grad_pct, 1),
        })

    pts = [f"{s['x']:.0f},{s['y']:.0f}" for s in samples]
    line = "M " + " L ".join(pts)
    area = (
        f"M {samples[0]['x']:.0f},{samples[0]['y']:.0f} "
        + " ".join(f"L {s['x']:.0f},{s['y']:.0f}" for s in samples[1:])
        + f" L {samples[-1]['x']:.0f},{height} L {samples[0]['x']:.0f},{height} Z"
    )

    # axis labels
    y_steps = 4
    y_labels = []
    for s in range(y_steps + 1):
        ele = e_max - (e_max - e_min) * (s / y_steps)
        y_labels.append(f"{_fmt_int(int(round(ele)))} m")

    total_km = total / 1000.0
    x_labels = []
    for s in range(5):
        km = total_km * s / 4
        x_labels.append(f"{_fmt_km_short(km)} km" if km > 0 else "0 km")

    # cima principal (max elevation en samples) para compat
    summit_idx = max(range(len(samples)), key=lambda k: samples[k]["alt"])
    summit_sample = samples[summit_idx]

    return ElevProfile(
        line=line,
        area=area,
        summit_x=float(summit_sample["x"]),
        summit_y=float(summit_sample["y"]),
        summit_alt_str=_fmt_int(summit_sample["alt"]),
        summits=[],  # se rellena en build_detail con los Summit reales
        samples=samples,
        axis=ElevAxisLabels(y=y_labels, x=x_labels),
    )


def _orientation(track: List[TrackPoint]) -> str:
    """Dame una orientacion aproximada NE → SO comparando inicio y mitad/cima."""
    if len(track) < 2:
        return "—"
    a = track[0]
    b = track[len(track) // 2]
    dlat = b.lat - a.lat
    dlon = b.lon - a.lon
    if abs(dlat) < 1e-6 and abs(dlon) < 1e-6:
        return "—"
    angle = (math.degrees(math.atan2(dlon, dlat)) + 360) % 360
    sectors = ["N", "NE", "E", "SE", "S", "SO", "O", "NO"]
    idx = int((angle + 22.5) // 45) % 8
    opp = (idx + 4) % 8
    return f"{sectors[idx]} → {sectors[opp]}"


def _bbox_size_km(track: List[TrackPoint]) -> Tuple[float, float]:
    """Devuelve (ancho_km, alto_km) del bounding box del track."""
    if not track:
        return 0.0, 0.0
    lats = [p.lat for p in track]
    lons = [p.lon for p in track]
    lat_min, lat_max = min(lats), max(lats)
    lon_min, lon_max = min(lons), max(lons)
    h_km = _haversine_m(lat_min, lon_min, lat_max, lon_min) / 1000.0
    w_km = _haversine_m(lat_min, lon_min, lat_min, lon_max) / 1000.0
    return w_km, h_km


def _is_circular(track: List[TrackPoint]) -> bool:
    """Si inicio y fin estan a < 200 m, consideramos circular."""
    if len(track) < 2:
        return False
    d = _haversine_m(track[0].lat, track[0].lon, track[-1].lat, track[-1].lon)
    return d < 200.0


def _fatigue_score(distance_km: float, gain_m: int) -> Tuple[float, str]:
    """Indice de fatiga estilo "tobler-lite" sin tiempo: d * sqrt((d+ / d) * 10)."""
    if distance_km <= 0:
        return 0.0, "easy"
    intensity = gain_m / distance_km if distance_km > 0 else 0.0
    score = math.sqrt(distance_km * (1 + intensity / 100.0)) * 1.5
    score = max(0.0, min(10.0, score))
    if score < 4:
        level = "easy"
    elif score < 6:
        level = "moderate"
    elif score < 8:
        level = "hard"
    else:
        level = "very-hard"
    return round(score, 1), level


def _build_hero(db: Session, user_id: int, route: Route) -> HeroData:
    """Construye el dataclass HeroData con los datos del encabezado de detalle."""
    number = _ruta_number(db, user_id, route)
    return HeroData(
        number=number,
        date_str=_fmt_date_es(route.started_at),
        region=(route.region or "—"),
        season=_season_es(route.started_at),
        title=route.name,
        subregion=route.sub_region,
        score=round(route.difficulty_score, 1),
        score_str=f"{route.difficulty_score:.1f}".replace(".", ","),
        level=route.difficulty_level,
        level_label=_difficulty_label_es(route.difficulty_level),
        distance_str=_fmt_km(route.distance_km),
        gain_str=_fmt_int(route.elevation_gain_m or 0),
        time_str=_fmt_duration(route.moving_time_s or 0),
        pace_str=_fmt_pace_min((route.moving_time_s or 0) / route.distance_km if route.distance_km > 0 else 0),
        max_alt_str=_fmt_int(route.max_altitude_m or 0),
    )


def _build_milestones(
    route: Route,
    points: List[TrackPoint],
    summits: List[Summit],
    cum2d: List[float],
    total_km_2d: float,
    alt_min: int,
    alt_max: int,
) -> Tuple[List[Milestone], float, float]:
    """Construye los hitos del recorrido: salida, cimas y llegada.

    Devuelve (milestones, summit_lat, summit_lon) donde summit_lat/lon
    corresponden a la cima de mayor altitud (ancla para el clima).
    """
    # Cima principal (mayor altitud) para el clima
    primary = max(summits, key=lambda s: s.elevation_m or -9999) if summits else None
    summit_lat = primary.lat if primary else route.start_lat
    summit_lon = primary.lon if primary else route.start_lon

    start_origin = route.sub_region or route.region or "salida"
    milestones: List[Milestone] = []
    if points:
        milestones.append(Milestone(
            kind="start",
            label="salida",
            name=start_origin,
            elev_m=int(round(points[0].elevation_m)) if points[0].elevation_m is not None else (alt_min or 0),
            km=0.0,
            km_str=f"km {_fmt_km_short(0.0)}",
            time_str=_fmt_hhmm(points[0].time),
        ))

        for s in summits:
            # km acumulado hasta el punto del track más cercano a la cima
            summit_km = 0.0
            if cum2d:
                best_i = min(
                    range(len(points)),
                    key=lambda i, _s=s: (points[i].lat - _s.lat) ** 2 + (points[i].lon - _s.lon) ** 2,
                )
                summit_km = cum2d[best_i] / 1000.0
            summit_alt = s.elevation_m or alt_max
            milestones.append(Milestone(
                kind="summit",
                label="cumbre",
                name=s.name or f"Cima {route.name}" if len(summits) == 1 else s.name or f"Cima {s.seq + 1}",
                elev_m=summit_alt,
                km=round(summit_km, 1),
                km_str=f"km {_fmt_km_short(summit_km)}",
                time_str="—",
            ))

        milestones.append(Milestone(
            kind="end",
            label="llegada",
            name=start_origin,
            elev_m=int(round(points[-1].elevation_m)) if points[-1].elevation_m is not None else (alt_min or 0),
            km=round(total_km_2d, 1),
            km_str=f"km {_fmt_km_short(total_km_2d)}",
            time_str=_fmt_hhmm(points[-1].time),
        ))

    return milestones, summit_lat, summit_lon


def _build_map(
    route: Route,
    points: List[TrackPoint],
    milestones: List[Milestone],
) -> MapData:
    """Construye MapData con la polilínea, bounding box e info rows del mapa."""
    track_pts = [[p.lat, p.lon] for p in points]
    if track_pts:
        lats = [p[0] for p in track_pts]
        lons = [p[1] for p in track_pts]
        bbox = [[min(lats), min(lons)], [max(lats), max(lons)]]
    else:
        bbox = [[route.start_lat, route.start_lon], [route.start_lat, route.start_lon]]
    bbox_w_km, bbox_h_km = _bbox_size_km(points) if points else (0.0, 0.0)
    info_rows = [
        MapInfoRow("tipo", "circular" if _is_circular(points) else "lineal"),
        MapInfoRow("superficie", "sendero"),
        MapInfoRow("orientación", _orientation(points)),
        MapInfoRow("bbox", f"{_fmt_km_short(bbox_w_km)} × {_fmt_km_short(bbox_h_km)} km"),
    ]
    return MapData(
        track=TrackPoly(points=track_pts, bbox=bbox),
        milestones=milestones,
        info_rows=info_rows,
        points_count=len(points),
    )


def _build_tech(
    route: Route,
    points: List[TrackPoint],
    cum2d: List[float],
    total2d: float,
    total3d: float,
    total_km_2d: float,
    alt_min: int,
    alt_max: int,
    slope_avg_pct: float,
    slope_max_pct: float,
    hardest_km: int,
) -> TechData:
    """Construye TechData con todas las métricas técnicas de la ruta."""
    total_time_s = route.total_time_s or 0
    moving_s = route.moving_time_s or 0
    stop_s = max(0, total_time_s - moving_s)

    pace_avg_secs = moving_s / route.distance_km if route.distance_km > 0 else 0
    pace_avg = _fmt_pace_min(pace_avg_secs)
    pace_avg_kmh = _fmt_kmh(route.distance_km, moving_s / 3600.0) if moving_s > 0 else "—"

    km_up, secs_up, km_down, secs_down = _compute_pace_split(points, cum2d) if points else (0, 0, 0, 0)
    pace_up = _fmt_pace_min(secs_up / km_up) if km_up > 0 else "—"
    pace_up_kmh = _fmt_kmh(km_up, secs_up / 3600.0) if secs_up > 0 else "—"
    pace_down = _fmt_pace_min(secs_down / km_down) if km_down > 0 else "—"
    pace_down_kmh = _fmt_kmh(km_down, secs_down / 3600.0) if secs_down > 0 else "—"

    vam = int(round(((route.elevation_gain_m or 0) / (secs_up / 3600.0)))) if secs_up > 0 else 0

    if points and points[0].time and points[-1].time:
        start_end_str = f"salida {_fmt_hhmm(points[0].time)} · llegada {_fmt_hhmm(points[-1].time)}"
    else:
        start_end_str = "—"

    fatigue_score, fatigue_level = _fatigue_score(route.distance_km, route.elevation_gain_m or 0)
    density_m = (total2d / len(points)) if points else 0.0

    return TechData(
        distance_2d_str=_fmt_km(total_km_2d),
        distance_3d_str=_fmt_km(total3d / 1000.0) if total3d else _fmt_km(total_km_2d),
        total_time_str=_fmt_duration(total_time_s),
        moving_time_str=_fmt_duration(moving_s),
        stop_time_str=f"{int(stop_s // 60)} min de paradas" if stop_s > 0 else "sin paradas",
        start_end_str=start_end_str,
        pace_avg=pace_avg,
        pace_avg_kmh=pace_avg_kmh,
        pace_up=pace_up,
        pace_up_kmh=pace_up_kmh,
        pace_down=pace_down,
        pace_down_kmh=pace_down_kmh,
        vam_str=_fmt_int(vam) if vam > 0 else "—",
        gain_str=_fmt_int(route.elevation_gain_m or 0),
        loss_str=_fmt_int(route.elevation_loss_m or 0),
        alt_range_str=f"{_fmt_int(alt_min)} — {_fmt_int(alt_max)}",
        alt_range_diff_str=f"rango: {_fmt_int(max(0, alt_max - alt_min))} m",
        slope_avg_str=_fmt_pct(slope_avg_pct),
        slope_max_str=_fmt_pct(slope_max_pct),
        hardest_sector_label=f"tramo más duro: km {hardest_km}",
        fatigue_str=f"{fatigue_score}".replace(".", ","),
        fatigue_level=fatigue_level,
        gpx_points=len(points),
        gpx_density_str=f"~{_fmt_pct(density_m)} m / punto" if density_m > 0 else "—",
    )


def _build_notes(route: Route) -> NotesData:
    """Construye NotesData con notas y etiquetas. Tolera JSON corrupto en tags."""
    try:
        tags = json.loads(route.tags) if route.tags else []
        if not isinstance(tags, list):
            tags = []
    except (json.JSONDecodeError, TypeError):
        # Tags se persiste como JSON. Si la cadena se corrompió o no es texto,
        # caemos a lista vacía en lugar de tirar el render del detalle.
        tags = []
    return NotesData(
        text=route.notes or "",
        tags=[str(t) for t in tags][:10],
        updated_str=f"memo · {_fmt_date_short(route.updated_at or route.created_at or route.started_at)}",
    )


def _related_routes(db: Session, user_id: int, route: Route) -> List[RelatedItem]:
    """3 rutas mas cercanas en dificultad y region (excluyendo la actual). Acotado al usuario."""
    score_lo = max(0.0, route.difficulty_score - 2.0)
    score_hi = min(10.0, route.difficulty_score + 2.0)
    diff = func.abs(Route.difficulty_score - route.difficulty_score)

    # Primero: misma region, rango de dificultad
    candidates: List[Route] = (
        db.query(Route)
        .filter(
            Route.user_id == user_id,
            Route.id != route.id,
            Route.difficulty_score >= score_lo,
            Route.difficulty_score <= score_hi,
            func.lower(Route.region) == canonical_geo(route.region),
        )
        .order_by(diff.asc())
        .limit(3)
        .all()
    )

    # Fallback: cualquier region si no hay suficientes
    if len(candidates) < 3:
        existing_ids = {r.id for r in candidates} | {route.id}
        extra: List[Route] = (
            db.query(Route)
            .filter(
                Route.user_id == user_id,
                Route.id.notin_(existing_ids),
                Route.difficulty_score >= score_lo,
                Route.difficulty_score <= score_hi,
            )
            .order_by(diff.asc())
            .limit(3 - len(candidates))
            .all()
        )
        candidates.extend(extra)

    out: List[RelatedItem] = []
    for r in candidates:
        out.append(RelatedItem(
            id=r.id,
            name=r.name,
            origin=f"desde {r.sub_region}" if r.sub_region else (r.region or ""),
            score_str=f"{r.difficulty_score:.1f}".replace(".", ","),
            level=r.difficulty_level,
            line=r.elev_line_path or "",
            area=r.elev_area_path or "",
            km_str=_fmt_km(r.distance_km),
            gain_str=_fmt_int(r.elevation_gain_m or 0),
            time_str=_fmt_duration(r.moving_time_s or 0),
        ))
    return out


def _route_neighbors(db: Session, user_id: int, route: Route) -> Tuple[Optional[Route], Optional[Route]]:
    """Devuelve (anterior_mas_reciente, siguiente_mas_reciente) por started_at. Acotado al usuario."""
    prev = (
        db.query(Route)
        .filter(Route.user_id == user_id, Route.started_at < route.started_at)
        .order_by(Route.started_at.desc())
        .first()
    )
    nxt = (
        db.query(Route)
        .filter(Route.user_id == user_id, Route.started_at > route.started_at)
        .order_by(asc(Route.started_at))
        .first()
    )
    return prev, nxt


def _ruta_number(db: Session, user_id: int, route: Route) -> int:
    """#01 = la mas antigua. Devuelve la posicion 1-based acotada al usuario."""
    from sqlalchemy import func
    return int(
        db.query(func.count(Route.id))
        .filter(Route.user_id == user_id, Route.started_at <= route.started_at)
        .scalar()
        or 0
    )


# ============= entry point =============

def build_detail(db: Session, user_id: int, route_id: int) -> Optional[DetailData]:
    """Construye DetailData completo para la vista de detalle de una ruta.

    Devuelve None si la ruta no existe o no pertenece al usuario.
    """
    from app.queries import user_route_get
    route = user_route_get(db, user_id, route_id)
    if not route:
        return None

    points: List[TrackPoint] = (
        db.query(TrackPoint)
        .filter(TrackPoint.route_id == route.id)
        .order_by(TrackPoint.seq.asc())
        .all()
    )

    summits: List[Summit] = (
        db.query(Summit)
        .filter(Summit.route_id == route.id)
        .order_by(Summit.seq.asc())
        .all()
    )

    cum2d, cum3d, total2d, total3d = _compute_distances(points)
    total_km_2d = total2d / 1000.0 if total2d else route.distance_km
    alt_max = route.max_altitude_m or 0
    alt_min = route.min_altitude_m or 0

    slope_avg_pct, slope_max_pct, hardest_km = _compute_slopes(points, cum2d) if points else (0.0, 0.0, 1)

    # Si no hay summits en BD, generar fallback desde el punto más alto del track
    effective_summits: List[Summit] = list(summits)
    if not effective_summits and points:
        best = max(
            (p for p in points if p.elevation_m is not None),
            key=lambda p: p.elevation_m or -9999,
            default=None,
        )
        if best:
            fallback = Summit(
                id=-1, route_id=route.id, seq=0,
                lat=best.lat, lon=best.lon,
                elevation_m=int(round(best.elevation_m)),
                name=None, source="fallback",
            )
            effective_summits = [fallback]

    milestones, summit_lat, summit_lon = _build_milestones(
        route, points, effective_summits, cum2d, total_km_2d, alt_min, alt_max
    )

    elev_strip = ElevStrip(
        gain_str=f"+{_fmt_int(route.elevation_gain_m or 0)} m",
        loss_str=f"−{_fmt_int(route.elevation_loss_m or 0)} m",
        alt_min_str=f"{_fmt_int(alt_min)} m",
        alt_max_str=f"{_fmt_int(alt_max)} m",
        slope_avg_str=f"{_fmt_pct(slope_avg_pct)} %",
        slope_max_str=f"{_fmt_pct(slope_max_pct)} %",
    )

    elev = _build_elev_profile(points, cum2d) if points else ElevProfile(
        line="", area="", summit_x=0, summit_y=0, summit_alt_str="—",
        summits=[], samples=[], axis=ElevAxisLabels(y=[], x=[]),
    )

    # Calcular posición SVG de cada cima en el perfil
    if points and cum2d and elev.samples and effective_summits:
        total_m = cum2d[-1]
        e_vals = [s["alt"] for s in elev.samples]
        e_min_s = min(e_vals)
        e_max_s = max(e_vals)
        height_svg, margin_top, margin_bottom = 280, 28, 14
        inner_h = height_svg - margin_top - margin_bottom
        summit_marks: List[ElevSummitMark] = []
        for s in effective_summits:
            best_i = min(
                range(len(points)),
                key=lambda i, _s=s: (points[i].lat - _s.lat) ** 2 + (points[i].lon - _s.lon) ** 2,
            )
            km_s = cum2d[best_i] / 1000.0
            x_s = (km_s / (total_m / 1000.0)) * 800.0 if total_m > 0 else 0.0
            # Altitud: usar la del Summit si existe, si no interpolar del perfil
            if s.elevation_m is not None:
                alt_s = s.elevation_m
            else:
                # Buscar el sample más cercano en x
                x_norm = x_s / 800.0
                sample_idx = min(range(len(elev.samples)),
                                 key=lambda i: abs(elev.samples[i]["x"] / 800.0 - x_norm))
                alt_s = elev.samples[sample_idx]["alt"]
            y_s = margin_top + inner_h * (1 - (alt_s - e_min_s) / max(1, e_max_s - e_min_s))
            summit_marks.append(ElevSummitMark(
                summit_id=s.id,
                x=round(x_s, 1),
                y=round(y_s, 1),
                alt_str=_fmt_int(alt_s),
                name=s.name or f"Cima {s.seq + 1}",
            ))
        elev.summits = summit_marks

    prev_route, next_route = _route_neighbors(db, user_id, route)

    return DetailData(
        id=route.id,
        name=route.name,
        name_original=route.name_original,
        hero=_build_hero(db, user_id, route),
        map=_build_map(route, points, milestones),
        elev_strip=elev_strip,
        elev=elev,
        tech=_build_tech(
            route, points, cum2d, total2d, total3d, total_km_2d,
            alt_min, alt_max, slope_avg_pct, slope_max_pct, hardest_km,
        ),
        notes=_build_notes(route),
        related=_related_routes(db, user_id, route),
        prev_id=prev_route.id if prev_route else None,
        prev_name=prev_route.name if prev_route else None,
        next_id=next_route.id if next_route else None,
        next_name=next_route.name if next_route else None,
        weather_meta=f"open-meteo · era5 archive · {_fmt_date_es(route.started_at)}",
        started_at_iso=route.started_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        summit_lat=summit_lat,
        summit_lon=summit_lon,
    )
