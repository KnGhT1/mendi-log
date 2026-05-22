"""Cache en proceso para `build_analisis`.

Extraído de `app.analisis` para mantener ese módulo enfocado en agregaciones.
La invalidación se sigue exponiendo desde `app.analisis` por compatibilidad
(re-export en el módulo padre).

Clave del cache: `(user_id, range_key, from_date, to_date)`. Cada usuario tiene
su propio espacio de cache. La invalidación global limpia todas las entradas.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Dict, Optional, Tuple

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


_CACHE: Dict[Tuple[int, str, Optional[str], Optional[str]], object] = {}
_CACHE_LOCK = threading.Lock()


def get_analisis_cached(
    build_fn,
    db: Session,
    user_id: int,
    range_key: str = "all",
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
):
    """Wrapper cacheado de `build_analisis` con clave por usuario.

    HIT: devuelve el AnalisisData ya construido sin tocar la BD.
    MISS: lo construye, lo guarda y deja un log con el tiempo en ms.

    Recibimos `build_fn` como parámetro para no introducir un import circular
    con `app.analisis`.
    """
    key = (int(user_id), range_key or "all", from_date, to_date)
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
    if cached is not None:
        logger.debug("[analisis] HIT  %s", key)
        return cached

    t0 = time.perf_counter()
    data = build_fn(db, user_id, range_key, from_date, to_date)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    logger.info(
        "[analisis] MISS %s · %s sesiones · %s únicas · %.0f ms",
        key, getattr(data, "total_sessions", "?"),
        getattr(data, "total_unique_routes", "?"), elapsed_ms,
    )
    with _CACHE_LOCK:
        _CACHE[key] = data
    return data


def invalidate_analisis_cache(user_id: int) -> None:
    """Elimina las entradas del cache del usuario afectado.

    Solo invalida las claves que pertenecen a `user_id`, dejando intacto
    el cache del resto de usuarios.
    """
    with _CACHE_LOCK:
        keys = [k for k in _CACHE if k[0] == user_id]
        for k in keys:
            del _CACHE[k]
    if keys:
        logger.info("[analisis] cache cleared user=%d (%d entradas)", user_id, len(keys))
