"""Geocodificación inversa con Nominatim.

Convierte (lat, lon) en (region, sub_region) usando OSM Nominatim. Los
resultados se cachean en SQLite (tabla `geocode_cache`) por celda redondeada
de ~1 km, de modo que trailheads cercanos comparten respuesta y evitamos
repetir consultas. Se respeta el rate-limit oficial de Nominatim (1 req/s)
con un pequeño margen.

El caché tiene política LRU + TTL: cada `accessed_at` se actualiza al leer y
las entradas viejas (TTL de 1 año por defecto) se evictan oportunísticamente
cuando superamos `MAX_CACHE_ROWS`.

Si no hay red o el servicio falla devuelve (None, None, None) — la
importación nunca debe romperse por falta de geocodificación.

Nota histórica: hasta v13 el caché vivía en `data/geocode_cache.json` y se
reescribía entero en cada hit, fuera del lock (race entre escritor y lector).
La migración a SQLite cierra esa race y permite eviction por edad.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Optional

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import DATA_DIR, SessionLocal, commit as _db_commit
from app.models import GeocodeCache
from app.text_utils import (
    canonical_geo,
    normalize as _normalize,  # noqa: F401  (alias preservado por compatibilidad)
    strip_accents as _strip_accents,  # noqa: F401
)

logger = logging.getLogger(__name__)

LEGACY_CACHE_PATH = DATA_DIR / "geocode_cache.json"
USER_AGENT = "mendi.log/1.0 (personal hiking tracker)"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
RATE_LIMIT_SECONDS = 1.1  # margen sobre el límite oficial de 1 req/s
TIMEOUT_SECONDS = 8.0
# LRU/TTL: evictamos al pasar de MAX_CACHE_ROWS o al leer entradas más viejas
# que CACHE_TTL_DAYS. Nominatim cambia rara vez la respuesta para una celda,
# pero un año es un techo razonable para reflejar nuevas administrativas.
MAX_CACHE_ROWS = 50_000
CACHE_TTL_DAYS = 365

_request_lock = threading.Lock()
_last_request_at = 0.0
_legacy_migrated = False
_legacy_lock = threading.Lock()


def _cache_key(lat: float, lon: float) -> str:
    """Genera la clave de celda ~1.1 km para el caché de geocodificación.

    Redondea lat/lon a 2 decimales para que trailheads vecinos compartan
    la misma entrada y se minimicen las consultas a Nominatim.
    """
    # Celdas de ~1.1 km en latitud: trailheads vecinos comparten respuesta
    # y reducen consultas drásticamente. Formato fijo para evitar aritmética
    # flotante que produciría claves distintas para la misma celda.
    return f"{lat:.2f},{lon:.2f}"


def _extract_region(
    address: dict,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Extrae (country, region, sub_region) del objeto `address` de Nominatim.

    Usa directamente el campo province/state que devuelve Nominatim
    (con accept-language=es siempre en castellano) tras canonicalizar.
    Busca sub_region en varios campos candidatos tomando el primero no
    vacío que difiera de la provincia.
    """
    country = canonical_geo(address.get("country"))

    province = canonical_geo(address.get("province") or address.get("state"))
    region: Optional[str] = province or None

    sub_candidates = [
        address.get("county"),
        address.get("region"),
        address.get("municipality"),
        address.get("city_district"),
        address.get("town"),
        address.get("village"),
        address.get("hamlet"),
    ]
    sub_region: Optional[str] = None
    for cand in sub_candidates:
        if not cand:
            continue
        flat = canonical_geo(cand)
        if flat and flat != province:
            sub_region = flat
            break

    return (country, region, sub_region)


def _maybe_migrate_legacy_json(db: Session) -> None:
    """Importa entradas del antiguo `geocode_cache.json` la primera vez.

    Se hace una sola vez por proceso (`_legacy_migrated`). Si la tabla ya
    tiene filas no hace nada — evita duplicar trabajo en arranques sucesivos.
    Tras importar, renombra el JSON a `.legacy` para no volver a leerlo.
    """
    global _legacy_migrated
    with _legacy_lock:
        if _legacy_migrated:
            return
        _legacy_migrated = True

        if not LEGACY_CACHE_PATH.exists():
            return
        existing_count = db.execute(select(func.count(GeocodeCache.id))).scalar() or 0
        if existing_count > 0:
            try:
                LEGACY_CACHE_PATH.rename(LEGACY_CACHE_PATH.with_suffix(".json.legacy"))
            except OSError:
                logger.warning("geocoder: no pude renombrar el JSON legacy")
            return

        try:
            with LEGACY_CACHE_PATH.open("r", encoding="utf-8") as f:
                blob = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("geocoder: JSON legacy ilegible (%s); ignorado", exc)
            return
        if not isinstance(blob, dict):
            return

        now = datetime.now(UTC)
        for key, val in blob.items():
            if not isinstance(val, dict):
                continue
            db.add(GeocodeCache(
                cell_key=key,
                country=val.get("country"),
                region=val.get("region"),
                sub_region=val.get("sub_region"),
                fetched_at=now,
                accessed_at=now,
            ))
        try:
            db.commit()
            logger.info("geocoder: migradas %d celdas del JSON legacy", len(blob))
        except IntegrityError:
            db.rollback()
        try:
            LEGACY_CACHE_PATH.rename(LEGACY_CACHE_PATH.with_suffix(".json.legacy"))
        except OSError:
            pass


def _evict_if_needed(db: Session) -> None:
    """Evicta entradas LRU si superamos MAX_CACHE_ROWS o si están caducadas.

    Ojo: el commit lo hace el caller. Llamamos a esto raramente (1/100 hits)
    para no penalizar la ruta crítica.
    """
    cutoff = datetime.now(UTC) - timedelta(days=CACHE_TTL_DAYS)
    db.execute(delete(GeocodeCache).where(GeocodeCache.fetched_at < cutoff))

    total = db.execute(select(func.count(GeocodeCache.id))).scalar() or 0
    if total <= MAX_CACHE_ROWS:
        return
    excess = total - MAX_CACHE_ROWS
    # Borramos los menos accedidos
    victims = (
        db.execute(
            select(GeocodeCache.id)
            .order_by(GeocodeCache.accessed_at.asc())
            .limit(excess)
        )
        .scalars()
        .all()
    )
    if victims:
        db.execute(delete(GeocodeCache).where(GeocodeCache.id.in_(victims)))


def _request_nominatim(lat: float, lon: float) -> Optional[dict]:
    """Llamada cruda a Nominatim respetando el rate-limit.

    Usamos httpx en lugar de urlopen: timeout, headers explícitos y manejo
    de excepciones por familia (TimeoutException, HTTPError, NetworkError).
    """
    global _last_request_at
    with _request_lock:
        now = time.monotonic()
        wait = (_last_request_at + RATE_LIMIT_SECONDS) - now
        if wait > 0:
            time.sleep(wait)

    params = {
        "lat": f"{lat:.6f}",
        "lon": f"{lon:.6f}",
        "format": "json",
        "zoom": "8",
        "addressdetails": "1",
        "accept-language": "es",
    }
    headers = {"User-Agent": USER_AGENT}
    try:
        resp = httpx.get(
            NOMINATIM_URL,
            params=params,
            headers=headers,
            timeout=TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else None
    except httpx.TimeoutException:
        logger.warning("geocoder: timeout para (%s, %s)", lat, lon)
        return None
    except httpx.HTTPStatusError as exc:
        logger.warning("geocoder: HTTP %s para (%s, %s)", exc.response.status_code, lat, lon)
        return None
    except httpx.HTTPError as exc:
        logger.warning("geocoder: error de red (%s) para (%s, %s)", exc.__class__.__name__, lat, lon)
        return None
    except (ValueError, json.JSONDecodeError):
        logger.warning("geocoder: respuesta no-JSON para (%s, %s)", lat, lon)
        return None
    finally:
        with _request_lock:
            _last_request_at = time.monotonic()


def _read_cache(db: Session, key: str) -> Optional[GeocodeCache]:
    """Devuelve la entrada de caché para `key` o None si no existe."""
    return db.execute(
        select(GeocodeCache).where(GeocodeCache.cell_key == key)
    ).scalar_one_or_none()


def reverse_geocode(
    lat: float, lon: float,
    *,
    db: Optional[Session] = None,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Devuelve (country, region, sub_region) para una coordenada.

    Acepta una sesión opcional para que el caller pueda reusar la suya. Si no
    se proporciona, abrimos una sesión local de corta duración.
    """
    if lat is None or lon is None:
        return (None, None, None)

    own_session = db is None
    db = db or SessionLocal()
    try:
        _maybe_migrate_legacy_json(db)
        key = _cache_key(lat, lon)
        row = _read_cache(db, key)
        now = datetime.now(UTC)

        if row is not None:
            row.accessed_at = now
            try:
                db.commit()
            except Exception:  # noqa: BLE001
                db.rollback()
            return (row.country, row.region, row.sub_region)

        payload = _request_nominatim(lat, lon)
        if not payload:
            return (None, None, None)

        address = payload.get("address") or {}
        country, region, sub_region = _extract_region(address)
        try:
            db.add(GeocodeCache(
                cell_key=key,
                country=country,
                region=region,
                sub_region=sub_region,
                fetched_at=now,
                accessed_at=now,
            ))
            # Race: dos imports concurrentes con el mismo trailhead. La unique
            # constraint nos protege; absorbemos el IntegrityError y leemos.
            db.commit()
        except IntegrityError:
            db.rollback()
            row = _read_cache(db, key)
            if row is not None:
                return (row.country, row.region, row.sub_region)

        # Eviction: probabilístico (1/100) para no penalizar la ruta crítica.
        if (id(payload) & 0x7F) == 0:
            try:
                _evict_if_needed(db)
                db.commit()
            except Exception:  # noqa: BLE001
                db.rollback()

        return (country, region, sub_region)
    finally:
        if own_session:
            db.close()
