"""
Cliente de Open-Meteo con cache en SQLite.

Anclamos la consulta a un único punto (idealmente la cima) y a la fecha del
inicio de la ruta. El endpoint Archive (ERA5) tiene un retraso de ~5 días,
asi que para fechas muy recientes caemos al endpoint Forecast.

Documentacion:
    https://open-meteo.com/en/docs/historical-weather-api
    https://open-meteo.com/en/docs
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from app.db import commit as _db_commit
from app.models import WeatherCache

logger = logging.getLogger(__name__)

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT_S = 10
# El archive ERA5 tiene un retraso de ~5 dias. Si la ruta es mas reciente,
# usamos forecast (que conserva ~92 dias hacia atras como past_days).
ARCHIVE_LAG_DAYS = 6
FORECAST_PAST_LIMIT_DAYS = 90

HOURLY_VARS = [
    "temperature_2m",
    "relative_humidity_2m",
    "cloud_cover",
    "precipitation",
    "wind_speed_10m",
    "wind_gusts_10m",
    "wind_direction_10m",
]
DAILY_VARS = [
    "temperature_2m_max",
    "temperature_2m_min",
    "sunrise",
    "sunset",
    "precipitation_sum",
    "wind_speed_10m_max",
    "wind_gusts_10m_max",
]


def _round_coord(v: float) -> float:
    """Redondea a 2 decimales (~1 km) para reutilizar peticiones."""
    return round(v, 2)


def _fetch_json(url: str, params: dict) -> dict:
    """GET con httpx: timeout, headers explícitos y reintento ligero en 5xx.

    Antes usábamos `urllib.request.urlopen` con un `noqa: S310`; httpx nos da
    manejo de excepciones por familia y un objeto Timeout estructurado para
    limitar connect/read/write por separado.
    """
    headers = {"User-Agent": "mendi.log/0.1"}
    timeout = httpx.Timeout(TIMEOUT_S, connect=min(TIMEOUT_S, 5.0))
    last_exc: Optional[Exception] = None
    for attempt in range(2):
        try:
            resp = httpx.get(url, params=params, headers=headers, timeout=timeout)
            if resp.status_code >= 500 and attempt == 0:
                last_exc = httpx.HTTPStatusError(
                    f"5xx ({resp.status_code})", request=resp.request, response=resp,
                )
                continue
            resp.raise_for_status()
            return resp.json()
        except (httpx.TimeoutException, httpx.HTTPError) as exc:
            last_exc = exc
            if attempt == 0:
                continue
            raise
    raise last_exc if last_exc else RuntimeError("weather: fetch failed")


def _fetch_archive(lat: float, lon: float, d: date) -> dict:
    """Llama al endpoint ERA5 Archive de Open-Meteo para una fecha y coordenada."""
    iso = d.isoformat()
    return _fetch_json(ARCHIVE_URL, {
        "latitude": lat,
        "longitude": lon,
        "start_date": iso,
        "end_date": iso,
        "hourly": ",".join(HOURLY_VARS),
        "daily": ",".join(DAILY_VARS),
        "timezone": "auto",
        "wind_speed_unit": "kmh",
    })


def _fetch_forecast(lat: float, lon: float, d: date) -> dict:
    """Forecast endpoint con past_days para dias recientes."""
    today = date.today()
    past_days = max(0, (today - d).days)
    forecast_days = 1 if d >= today else 0
    if past_days > FORECAST_PAST_LIMIT_DAYS:
        past_days = FORECAST_PAST_LIMIT_DAYS
    return _fetch_json(FORECAST_URL, {
        "latitude": lat,
        "longitude": lon,
        "past_days": past_days,
        "forecast_days": forecast_days if forecast_days > 0 else 1,
        "hourly": ",".join(HOURLY_VARS),
        "daily": ",".join(DAILY_VARS),
        "timezone": "auto",
        "wind_speed_unit": "kmh",
    })


def _slice_day(payload: dict, d: date) -> dict:
    """Recorta payload (forecast con multiples dias) a un unico dia."""
    iso = d.isoformat()

    # ---- daily ----
    daily_in = payload.get("daily") or {}
    times = daily_in.get("time") or []
    daily_out: dict = {"time": []}
    if iso in times:
        idx = times.index(iso)
        for k, vals in daily_in.items():
            if k == "time":
                daily_out["time"] = [iso]
            elif isinstance(vals, list) and idx < len(vals):
                daily_out[k] = [vals[idx]]

    # ---- hourly ----
    hourly_in = payload.get("hourly") or {}
    htimes = hourly_in.get("time") or []
    hourly_out: dict = {k: [] for k in hourly_in.keys()}
    for i, t in enumerate(htimes):
        if t.startswith(iso):
            for k, vals in hourly_in.items():
                if isinstance(vals, list) and i < len(vals):
                    hourly_out[k].append(vals[i])

    return {
        "latitude": payload.get("latitude"),
        "longitude": payload.get("longitude"),
        "elevation": payload.get("elevation"),
        "timezone": payload.get("timezone"),
        "daily_units": payload.get("daily_units"),
        "hourly_units": payload.get("hourly_units"),
        "daily": daily_out,
        "hourly": hourly_out,
    }


def _fetch_with_fallback(lat: float, lon: float, d: date) -> Optional[dict]:
    """Intenta obtener el clima con el endpoint primario y hace fallback al otro.

    Primario: archive (ERA5) para fechas con suficiente retraso, forecast para
    fechas recientes. Si el primario falla, intenta el contrario.
    Devuelve None si ambos fallan.
    """
    today = date.today()
    use_forecast = (today - d).days < ARCHIVE_LAG_DAYS

    def _try_primary() -> Optional[dict]:
        if use_forecast:
            return _slice_day(_fetch_forecast(lat, lon, d), d)
        return _fetch_archive(lat, lon, d)

    def _try_fallback() -> Optional[dict]:
        if use_forecast:
            return _fetch_archive(lat, lon, d)
        return _slice_day(_fetch_forecast(lat, lon, d), d)

    try:
        return _try_primary()
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        logger.debug(
            "weather: endpoint primario falló para %s %s (%s); intentando fallback",
            lat, d, exc.__class__.__name__,
        )
    try:
        return _try_fallback()
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        logger.warning(
            "weather: ambos endpoints fallaron para %s %s (%s)",
            lat, d, exc.__class__.__name__,
        )
        return None


def _save_to_cache(db: Session, lat_r: float, lon_r: float, iso: str, payload: dict) -> None:
    """Persiste o actualiza el payload de clima en la tabla WeatherCache."""
    serialized = json.dumps(payload, ensure_ascii=False)
    existing = (
        db.query(WeatherCache)
        .filter(
            WeatherCache.lat == lat_r,
            WeatherCache.lon == lon_r,
            WeatherCache.date_iso == iso,
        )
        .first()
    )
    if existing:
        existing.payload = serialized
        existing.fetched_at = datetime.now(UTC)
    else:
        db.add(WeatherCache(
            lat=lat_r,
            lon=lon_r,
            date_iso=iso,
            payload=serialized,
            fetched_at=datetime.now(UTC),
        ))
    # Cache de clima: no afecta a agregaciones de la vista análisis, así que
    # commiteamos sin invalidar (el helper centraliza la regla en `app.db`).
    _db_commit(db, invalidate_analisis=False)


def get_weather(
    db: Session,
    lat: float,
    lon: float,
    d: date,
    *,
    force_refresh: bool = False,
) -> Optional[dict]:
    """Devuelve clima del dia para un punto, usando cache de SQLite.

    Devuelve `None` si no se ha podido obtener el dato (ej. fecha futura sin
    pronostico o error de red).
    """
    lat_r = _round_coord(lat)
    lon_r = _round_coord(lon)
    iso = d.isoformat()

    # ---- cache hit ----
    if not force_refresh:
        row = (
            db.query(WeatherCache)
            .filter(
                WeatherCache.lat == lat_r,
                WeatherCache.lon == lon_r,
                WeatherCache.date_iso == iso,
            )
            .first()
        )
        if row:
            try:
                return json.loads(row.payload)
            except json.JSONDecodeError:
                logger.warning("weather: cache corrupta para %s %s, regenerando", lat_r, iso)

    # ---- fetch remoto ----
    payload = _fetch_with_fallback(lat_r, lon_r, d)
    if not payload:
        return None

    _save_to_cache(db, lat_r, lon_r, iso, payload)
    return payload
