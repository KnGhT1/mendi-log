"""
Parser de archivos GPX.

Calcula distancia, desnivel positivo/negativo, tiempo en movimiento, altitud
maxima/minima y genera el perfil de elevacion en formato SVG path (compatible
con el viewBox 800x200 del hero del mockup).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import gpxpy
import gpxpy.gpx

# ----- Parametros calibracion -----
# Distancia objetivo de suavizado en metros. La ventana de media movil se
# calcula dinamicamente como round(ELEVATION_SMOOTH_TARGET_M / mean_spacing_m).
# Esto hace que rutas densas (GPS cada 1 s) reciban mas suavizado que rutas
# dispersas (GPS cada 10 s), sin tocar constantes manualmente.
ELEVATION_SMOOTH_TARGET_M = 25.0
# Umbral base (m) para considerar un tramo como subida/bajada real.
# Se escala proporcionalmente a la densidad: rutas mas densas -> umbral mayor.
ELEVATION_GAIN_THRESHOLD_BASE = 8.0
# Limites de la ventana de suavizado para evitar extremos.
ELEVATION_SMOOTH_WINDOW_MIN = 5
ELEVATION_SMOOTH_WINDOW_MAX = 30
# Velocidad minima (m/s) para considerar que el usuario esta en movimiento.
MIN_MOVING_SPEED_MPS = 0.4
# Distancia minima (m) entre puntos para descartar ruido GPS.
MIN_POINT_DISTANCE_M = 0.5
# Numero de puntos para el perfil de elevacion del hero (mockup usa ~80).
HERO_PROFILE_POINTS = 80
# Numero de puntos para la track de visualizacion en mapa de detalle.
TRACK_DOWNSAMPLE_POINTS = 400


@dataclass
class TrackPointLite:
    seq: int
    lat: float
    lon: float
    elevation: Optional[float]
    time: Optional[datetime] = None


@dataclass
class GpxStats:
    name_original: str
    description: str
    started_at: datetime
    total_time_s: int
    moving_time_s: int
    distance_km: float
    elevation_gain_m: int
    elevation_loss_m: int
    max_altitude_m: Optional[int]
    min_altitude_m: Optional[int]
    start_lat: float
    start_lon: float
    elev_line_path: str
    elev_area_path: str
    track: List[TrackPointLite] = field(default_factory=list)


# ===== utilidades =====

def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia en metros entre dos puntos lat/lon."""
    R = 6371008.8  # radio medio de la Tierra en m
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _moving_average(xs: List[float], k: int) -> List[float]:
    """Media móvil centrada de ventana `k` sobre la lista `xs`.

    Usada para suavizar el perfil de elevación antes de acumular desnivel,
    reduciendo el ruido GPS que inflaría artificialmente gain/loss.
    En los extremos la ventana se recorta (no rellena con ceros).
    """
    if k <= 1 or len(xs) < k:
        return xs[:]
    half = k // 2
    out = []
    for i in range(len(xs)):
        a = max(0, i - half)
        b = min(len(xs), i + half + 1)
        out.append(sum(xs[a:b]) / (b - a))
    return out


def _build_elev_paths(
    cum_dist_m: List[float],
    elevations: List[float],
    width: int = 800,
    height: int = 200,
    margin_top: int = 25,
    margin_bottom: int = 5,
    n_points: int = HERO_PROFILE_POINTS,
) -> Tuple[str, str]:
    """Genera (line_path, area_path) en formato SVG estilo mockup."""
    if not cum_dist_m or len(cum_dist_m) < 2:
        return "", ""

    total = cum_dist_m[-1]
    if total <= 0:
        return "", ""

    e_min = min(elevations)
    e_max = max(elevations)
    if e_max == e_min:
        e_max = e_min + 1  # evita division por cero

    inner_h = height - margin_top - margin_bottom

    # Para cada x objetivo (0..n_points-1) interpolamos la elevacion
    samples = []
    j = 0
    for k in range(n_points):
        target = (total * k) / (n_points - 1)
        while j + 1 < len(cum_dist_m) and cum_dist_m[j + 1] < target:
            j += 1
        if j + 1 >= len(cum_dist_m):
            ele = elevations[-1]
        else:
            d0, d1 = cum_dist_m[j], cum_dist_m[j + 1]
            e0, e1 = elevations[j], elevations[j + 1]
            t = (target - d0) / (d1 - d0) if d1 > d0 else 0.0
            ele = e0 + (e1 - e0) * t
        x = (width * k) / (n_points - 1)
        # invertimos eje Y: mas alto -> menos y
        y = margin_top + inner_h * (1 - (ele - e_min) / (e_max - e_min))
        samples.append((x, y))

    pts = [f"{x:.0f},{y:.0f}" for x, y in samples]
    line = "M " + " L ".join(pts)
    area = (
        f"M {samples[0][0]:.0f},{samples[0][1]:.0f} "
        + " ".join(f"L {x:.0f},{y:.0f}" for x, y in samples[1:])
        + f" L {samples[-1][0]:.0f},{height} L {samples[0][0]:.0f},{height} Z"
    )
    return line, area


def _downsample_track(points: List[TrackPointLite], target: int) -> List[TrackPointLite]:
    """Downsample por distancia acumulada (equidistante en km, no en índice).

    El muestreo uniforme por índice sobrerrepresenta zonas con muchos puntos
    juntos (paradas, GPS lento). Muestrear por distancia da una polilínea
    más fiel a la geometría real de la ruta.
    """
    if len(points) <= target:
        return points

    # Calcular distancias acumuladas entre puntos consecutivos
    cum: List[float] = [0.0]
    for i in range(1, len(points)):
        a, b = points[i - 1], points[i]
        d = math.sqrt((b.lat - a.lat) ** 2 + (b.lon - a.lon) ** 2)
        cum.append(cum[-1] + d)
    total = cum[-1]
    if total <= 0:
        # todos los puntos en el mismo sitio: fallback a muestreo por índice
        step = len(points) / target
        out = [points[int(i * step)] for i in range(target)]
        if out[-1].seq != points[-1].seq:
            out.append(points[-1])
        for i, p in enumerate(out):
            p.seq = i
        return out

    step = total / (target - 1)
    out: List[TrackPointLite] = [points[0]]
    j = 0
    for k in range(1, target - 1):
        target_d = k * step
        while j + 1 < len(cum) and cum[j + 1] < target_d:
            j += 1
        out.append(points[j])
    out.append(points[-1])

    for i, p in enumerate(out):
        p.seq = i
    return out


# ===== API publica =====

def parse_gpx(content: bytes | str) -> GpxStats:
    """Parsea el contenido de un archivo GPX y devuelve sus estadisticas."""
    if isinstance(content, bytes):
        content = content.decode("utf-8", errors="replace")

    gpx = gpxpy.parse(content)

    name_original = ""
    description = ""
    if gpx.name:
        name_original = gpx.name
    if gpx.description:
        description = gpx.description

    if gpx.tracks:
        first_trk = gpx.tracks[0]
        if not name_original and first_trk.name:
            name_original = first_trk.name
        if not description and first_trk.description:
            description = first_trk.description

    # Aplanamos todos los puntos en una unica secuencia
    pts: List[gpxpy.gpx.GPXTrackPoint] = []
    for trk in gpx.tracks:
        for seg in trk.segments:
            pts.extend(seg.points)
    if not pts:
        raise ValueError("El archivo GPX no contiene puntos de track.")

    # ---- distancia + delta tiempos ----
    cum_dist_m: List[float] = [0.0]
    elevations_raw: List[float] = [pts[0].elevation if pts[0].elevation is not None else 0.0]
    deltas_dist_m: List[float] = []
    deltas_time_s: List[float] = []

    for i in range(1, len(pts)):
        a, b = pts[i - 1], pts[i]
        d = _haversine_m(a.latitude, a.longitude, b.latitude, b.longitude)
        if d < MIN_POINT_DISTANCE_M:
            d = 0.0
        deltas_dist_m.append(d)
        if a.time and b.time:
            deltas_time_s.append((b.time - a.time).total_seconds())
        else:
            deltas_time_s.append(0.0)
        cum_dist_m.append(cum_dist_m[-1] + d)
        elevations_raw.append(b.elevation if b.elevation is not None else elevations_raw[-1])

    distance_m = cum_dist_m[-1]

    # ---- desnivel: suavizado adaptativo por densidad de puntos ----
    # mean_spacing_m: distancia media entre puntos consecutivos con movimiento real
    moving_deltas = [d for d in deltas_dist_m if d >= MIN_POINT_DISTANCE_M]
    mean_spacing_m = (sum(moving_deltas) / len(moving_deltas)) if moving_deltas else ELEVATION_SMOOTH_TARGET_M
    smooth_window = int(round(ELEVATION_SMOOTH_TARGET_M / max(mean_spacing_m, 0.5)))
    smooth_window = max(ELEVATION_SMOOTH_WINDOW_MIN, min(ELEVATION_SMOOTH_WINDOW_MAX, smooth_window))
    # El umbral escala con la densidad: mas puntos por metro -> mas ruido acumulado
    density_factor = ELEVATION_SMOOTH_TARGET_M / max(mean_spacing_m, 0.5)
    gain_threshold = ELEVATION_GAIN_THRESHOLD_BASE * max(1.0, density_factor / smooth_window)
    elev_smooth = _moving_average(elevations_raw, smooth_window)
    gain = 0.0
    loss = 0.0
    pending = 0.0  # acumulador del tramo monotono actual
    for i in range(1, len(elev_smooth)):
        delta = elev_smooth[i] - elev_smooth[i - 1]
        if delta * pending < 0:
            # cambio de signo: vacia el acumulador si supera umbral
            if abs(pending) >= gain_threshold:
                if pending > 0:
                    gain += pending
                else:
                    loss += -pending
            pending = delta
        else:
            pending += delta
    if abs(pending) >= gain_threshold:
        if pending > 0:
            gain += pending
        else:
            loss += -pending

    # ---- tiempos ----
    first_time = next((p.time for p in pts if p.time), None)
    last_time = next((p.time for p in reversed(pts) if p.time), None)
    total_time_s = int((last_time - first_time).total_seconds()) if first_time and last_time else 0

    moving_time_s = 0.0
    # Umbral superior de velocidad: descarta artefactos GPS (saltos imposibles).
    # 25 km/h = 6.94 m/s es un techo generoso para senderismo/trail.
    MAX_MOVING_SPEED_MPS = 6.94
    for d, dt in zip(deltas_dist_m, deltas_time_s):
        if dt > 0:
            speed = d / dt
            if MIN_MOVING_SPEED_MPS <= speed <= MAX_MOVING_SPEED_MPS:
                moving_time_s += dt

    # ---- altitudes ----
    # elevations_raw nunca contiene None (el bucle usa fallback al valor anterior)
    max_alt = int(round(max(elevations_raw))) if elevations_raw else None
    min_alt = int(round(min(elevations_raw))) if elevations_raw else None

    # ---- perfil SVG ----
    line_path, area_path = _build_elev_paths(cum_dist_m, elev_smooth)

    # ---- track downsampleado para mapa ----
    track_lite = [
        TrackPointLite(
            seq=i,
            lat=p.latitude,
            lon=p.longitude,
            elevation=p.elevation,
            time=p.time.replace(tzinfo=None) if p.time else None,
        )
        for i, p in enumerate(pts)
    ]
    track_lite = _downsample_track(track_lite, TRACK_DOWNSAMPLE_POINTS)

    return GpxStats(
        name_original=name_original or "Sin nombre",
        description=description or "",
        started_at=first_time.replace(tzinfo=None) if first_time else datetime.now(timezone.utc).replace(tzinfo=None),
        total_time_s=total_time_s,
        moving_time_s=int(moving_time_s),
        distance_km=round(distance_m / 1000.0, 2),
        elevation_gain_m=int(round(gain)),
        elevation_loss_m=int(round(loss)),
        max_altitude_m=max_alt,
        min_altitude_m=min_alt,
        start_lat=pts[0].latitude,
        start_lon=pts[0].longitude,
        elev_line_path=line_path,
        elev_area_path=area_path,
        track=track_lite,
    )
