"""Query helpers: punto único para filtrar Route por usuario.

REGLA DE ORO: ningún módulo debe hacer `db.query(Route)` sin pasar por
`user_routes()` o `user_route_get_or_404()`. La excepción son los agregados
internos que ya filtran por user_id explícitamente.
"""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Query, Session

from app.models import Route


def user_routes(db: Session, user_id: int) -> Query:
    """Query base de Route filtrado por user_id. Encadenar `.filter()`, `.order_by()`, etc."""
    return db.query(Route).filter(Route.user_id == user_id)


def user_route_get(db: Session, user_id: int, route_id: int) -> Optional[Route]:
    """Equivalente a db.get(Route, route_id) pero verificando ownership."""
    return (
        db.query(Route)
        .filter(Route.id == route_id, Route.user_id == user_id)
        .first()
    )


def user_route_get_or_404(db: Session, user_id: int, route_id: int) -> Route:
    """404 deliberado: no revelamos si la ruta existe en otra cuenta."""
    r = user_route_get(db, user_id, route_id)
    if not r:
        raise HTTPException(status_code=404, detail="Ruta no encontrada")
    return r
