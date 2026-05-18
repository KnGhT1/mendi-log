"""Detección de cimas para una ruta.

Cadena de fuentes en orden de prioridad:
  1. Waypoints del GPX (<wpt>) filtrados por proximidad al track.
  2. Overpass API (natural=peak) dentro del bounding box.
  3. Fallback: punto de mayor altitud absoluta del track.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

import httpx

from app.gpx_parser import GpxStats, TrackPointLite

logger = logging.getLogger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
SUMMIT_MAX_DIST_M = 150
OVERPASS_TIMEOUT_S = 5


@dataclass
class SummitCandidate:
    seq: int
    lat: float
    lon: float
    elevation_m: Optional[int]
    name: Optional[str]
    source: str  # "wpt" | "overpass" | "fallback"


# ===== utilidades =====

def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _min_dist_to_track(lat: float, lon: float, track: List[TrackPointLite]) -> float:
    return min(_haversine_m(lat, lon, p.lat, p.lon) for p in track)


def _km_along_track(lat: float, lon: float, track: List[TrackPointLite]) -> float:
    best_i = min(range(len(track)), key=lambda i: _haversine_m(lat, lon, track[i].lat, track[i].lon))
    cum = 0.0
    for i in range(1, best_i + 1):
        cum += _haversine_m(track[i - 1].lat, track[i - 1].lon, track[i].lat, track[i].lon)
    return cum / 1000.0


def _assign_seq(candidates: list, track: List[TrackPointLite]) -> None:
    candidates.sort(key=lambda c: _km_along_track(c.lat, c.lon, track))
    for i, c in enumerate(candidates):
        c.seq = i


# ===== fuente 1: waypoints del GPX =====

def _from_waypoints(stats: GpxStats) -> List[SummitCandidate]:
    if not stats.waypoints or not stats.track:
        return []
    result = []
    for w in stats.waypoints:
        if _min_dist_to_track(w.lat, w.lon, stats.track) <= SUMMIT_MAX_DIST_M:
            elev = int(round(w.elevation)) if w.elevation is not None else None
            result.append(SummitCandidate(
                seq=0, lat=w.lat, lon=w.lon,
                elevation_m=elev, name=w.name, source="wpt",
            ))
    if result:
        _assign_seq(result, stats.track)
        logger.info("[summits] %d cima(s) desde waypoints GPX", len(result))
    return result


# ===== fuente 2: Overpass API =====

def _query_overpass(bbox: Tuple[float, float, float, float]) -> List[dict]:
    lat_min, lon_min, lat_max, lon_max = bbox
    query = (
        f"[out:json][timeout:{OVERPASS_TIMEOUT_S}];"
        f"node[natural=peak]({lat_min},{lon_min},{lat_max},{lon_max});"
        f"out body;"
    )
    resp = httpx.post(OVERPASS_URL, data={"data": query}, timeout=OVERPASS_TIMEOUT_S)
    resp.raise_for_status()
    return resp.json().get("elements", [])


def _from_overpass(stats: GpxStats) -> List[SummitCandidate]:
    if not stats.track:
        return []
    try:
        nodes = _query_overpass(stats.bbox)
    except Exception:  # noqa: BLE001
        logger.warning("[summits] Overpass no disponible")
        return []

    result = []
    for node in nodes:
        lat, lon = node.get("lat"), node.get("lon")
        if lat is None or lon is None:
            continue
        if _min_dist_to_track(lat, lon, stats.track) > SUMMIT_MAX_DIST_M:
            continue
        tags = node.get("tags", {})
        raw_ele = tags.get("ele")
        try:
            elevation_m = int(round(float(raw_ele))) if raw_ele else None
        except (ValueError, TypeError):
            elevation_m = None
        result.append(SummitCandidate(
            seq=0, lat=lat, lon=lon,
            elevation_m=elevation_m,
            name=tags.get("name") or tags.get("name:es") or None,
            source="overpass",
        ))

    if result:
        _assign_seq(result, stats.track)
        logger.info("[summits] %d cima(s) desde Overpass", len(result))
    return result


# ===== fuente 3: fallback =====

def _fallback_summit(stats: GpxStats) -> SummitCandidate:
    best = max(
        (p for p in stats.track if p.elevation is not None),
        key=lambda p: p.elevation or -9999,
        default=stats.track[len(stats.track) // 2] if stats.track else None,
    )
    if best is None:
        return SummitCandidate(
            seq=0, lat=stats.start_lat, lon=stats.start_lon,
            elevation_m=stats.max_altitude_m, name=None, source="fallback",
        )
    return SummitCandidate(
        seq=0, lat=best.lat, lon=best.lon,
        elevation_m=int(round(best.elevation)) if best.elevation is not None else stats.max_altitude_m,
        name=None, source="fallback",
    )


# ===== API pública =====

def fetch_summits(stats: GpxStats) -> List[SummitCandidate]:
    """Devuelve la lista de cimas para la ruta descrita por `stats`.

    Prueba las fuentes en orden: wpts → Overpass → fallback.
    """
    if not stats.track:
        return [_fallback_summit(stats)]

    result = _from_waypoints(stats)
    if result:
        return result

    result = _from_overpass(stats)
    if result:
        return result

    logger.debug("[summits] usando fallback (maximo absoluto)")
    return [_fallback_summit(stats)]
