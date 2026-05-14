"""Asignación de `route_cluster_id`: identifica la 'ruta única' a la que
pertenece cada sesión.

Dos sesiones se consideran la misma ruta única si su trailhead dista menos de
SAME_ROUTE_TRAILHEAD_M metros Y la diferencia de altitud máxima es menor de
SAME_ROUTE_ALT_M metros.

Antes esto se calculaba con union-find sobre todas las sesiones cada vez que
se construía la vista de análisis (O(n²) por render). Ahora lo materializamos:
- al importar una ruta nueva → buscamos un cluster existente cercano (O(n))
- al hacer backfill → un único pase O(n²) sobre toda la BD
- en la vista de análisis → simple `GROUP BY route_cluster_id`

Aislamiento por usuario: el clustering opera SIEMPRE dentro del mismo
`user_id`. Cada usuario tiene su propia secuencia de `route_cluster_id`
empezando en 1.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Dict, Iterable, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Route

# Mismos umbrales que el algoritmo anterior, conservados aquí como única
# fuente de verdad. `analisis.py` los importa desde este módulo.
SAME_ROUTE_TRAILHEAD_M = 150
SAME_ROUTE_ALT_M = 50


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia haversine en metros entre dos coordenadas."""
    rlat1 = math.radians(lat1 or 0.0)
    rlat2 = math.radians(lat2 or 0.0)
    dlat = math.radians((lat2 or 0.0) - (lat1 or 0.0))
    dlon = math.radians((lon2 or 0.0) - (lon1 or 0.0))
    h = (math.sin(dlat / 2) ** 2
         + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2)
    return 2 * 6_371_008.8 * math.asin(math.sqrt(h))


def _alt_match(a: Optional[int], b: Optional[int]) -> bool:
    """True si las altitudes máximas difieren menos de SAME_ROUTE_ALT_M.

    Si alguna es None se considera coincidencia solo si ambas lo son
    (rutas sin altitud no se mezclan con rutas que sí la tienen).
    """
    if a is None or b is None:
        return a is b
    return abs(a - b) <= SAME_ROUTE_ALT_M


def _is_same_route(
    lat1: float, lon1: float, alt1: Optional[int],
    lat2: float, lon2: float, alt2: Optional[int],
) -> bool:
    """True si dos sesiones comparten trailhead y cima: misma ruta única.

    Criterio doble: distancia entre trailheads < SAME_ROUTE_TRAILHEAD_M
    Y diferencia de altitud máxima < SAME_ROUTE_ALT_M. Ambas condiciones
    deben cumplirse para evitar falsos positivos en valles con varios picos.
    """
    if _haversine_m(lat1, lon1, lat2, lon2) > SAME_ROUTE_TRAILHEAD_M:
        return False
    return _alt_match(alt1, alt2)


def assign_cluster_for_new(db: Session, route: Route) -> int:
    """Asigna `route_cluster_id` a una ruta recién importada del usuario `route.user_id`.

    Estrategia: O(n) sobre las rutas del MISMO usuario para encontrar la
    primera coincidencia. Si la hay, hereda su cluster_id; si no, crea uno
    nuevo (max+1 dentro del usuario).
    """
    others = (
        db.query(Route.id, Route.start_lat, Route.start_lon,
                 Route.max_altitude_m, Route.route_cluster_id)
        .filter(Route.user_id == route.user_id, Route.id != route.id)
        .all()
    )
    for _id, lat, lon, alt, cid in others:
        if cid is None:
            continue
        if _is_same_route(route.start_lat, route.start_lon, route.max_altitude_m,
                          lat, lon, alt):
            route.route_cluster_id = cid
            return cid

    next_id = (
        db.query(func.coalesce(func.max(Route.route_cluster_id), 0))
        .filter(Route.user_id == route.user_id)
        .scalar() or 0
    ) + 1
    route.route_cluster_id = next_id
    return next_id


def _cluster_user_routes(routes: List[Route]) -> int:
    """Aplica union-find a una lista de rutas y materializa cluster_ids 1..k.

    Devuelve el número de clusters creados. Modifica `routes[i].route_cluster_id`
    in-place.
    """
    n = len(routes)
    if n == 0:
        return 0

    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        parent[find(x)] = find(y)

    for i in range(n):
        for j in range(i + 1, n):
            if find(i) == find(j):
                continue
            if _is_same_route(
                routes[i].start_lat, routes[i].start_lon, routes[i].max_altitude_m,
                routes[j].start_lat, routes[j].start_lon, routes[j].max_altitude_m,
            ):
                union(i, j)

    root_to_cid: Dict[int, int] = {}
    next_cid = 1
    for i in range(n):
        root = find(i)
        if root not in root_to_cid:
            root_to_cid[root] = next_cid
            next_cid += 1
        routes[i].route_cluster_id = root_to_cid[root]

    return len(root_to_cid)


def backfill_clusters(db: Session) -> int:
    """Recalcula `route_cluster_id` para todas las rutas de la BD, por usuario.

    Devuelve el total de clusters distintos (sumado en todos los usuarios).
    Cada usuario tiene su propia secuencia 1..k independiente.
    """
    user_ids = [uid for (uid,) in db.query(Route.user_id).distinct()]
    total = 0
    for uid in user_ids:
        routes = (
            db.query(Route)
            .filter(Route.user_id == uid)
            .order_by(Route.id.asc())
            .all()
        )
        total += _cluster_user_routes(routes)
    return total


def group_by_cluster(routes: Iterable[Route]) -> Dict[int, List[Route]]:
    """Agrupa una lista de rutas por su `route_cluster_id` materializado.

    Si alguna ruta no lo tiene asignado (BD sin migrar), cae a un grupo
    propio con clave negativa para no colisionar con cluster_ids reales.
    """
    out: Dict[int, List[Route]] = defaultdict(list)
    fallback = -1
    for r in routes:
        if r.route_cluster_id is not None:
            out[r.route_cluster_id].append(r)
        else:
            out[fallback].append(r)
            fallback -= 1
    return dict(out)
