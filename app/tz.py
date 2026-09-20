"""Zona horaria por ruta (Fase 2).

La BD guarda UTC naive. Este módulo resuelve la zona IANA del trailhead
(timezonefinder, offline) y convierte instantes a fecha/hora locales.
`None`/inválido significa UTC (degradación segura, nunca excepción).
"""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

_finder = None


def _get_finder():
    """TimezoneFinder perezoso: no penaliza el import si nunca se usa."""
    global _finder
    if _finder is None:
        from timezonefinder import TimezoneFinder
        _finder = TimezoneFinder()
    return _finder


def resolve_timezone(lat: Optional[float], lon: Optional[float]) -> Optional[str]:
    """Devuelve la zona IANA para (lat, lon) o None si no se puede resolver."""
    if lat is None or lon is None:
        return None
    try:
        return _get_finder().timezone_at(lat=float(lat), lng=float(lon))
    except (ValueError, TypeError, RuntimeError) as exc:
        logger.warning("tz: no se pudo resolver zona para (%s, %s): %s", lat, lon, exc)
        return None


def _zoneinfo(tzname: Optional[str]) -> ZoneInfo:
    """ZoneInfo validado; UTC ante None/inválido (incl. Windows sin tzdata)."""
    if tzname:
        try:
            return ZoneInfo(tzname)
        except (ZoneInfoNotFoundError, ValueError):
            logger.warning("tz: zona inválida %r, usando UTC", tzname)
    return ZoneInfo("UTC")


def local_datetime(dt: Optional[datetime], tzname: Optional[str]) -> Optional[datetime]:
    """Convierte un naive UTC a aware en la zona indicada (None si sin dato)."""
    if dt is None:
        return None
    aware = dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
    return aware.astimezone(_zoneinfo(tzname))


def local_date(dt: Optional[datetime], tzname: Optional[str]) -> Optional[date]:
    """Fecha local de un naive UTC en la zona indicada (None si sin dato)."""
    local = local_datetime(dt, tzname)
    return local.date() if local else None
