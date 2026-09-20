"""Tests de agregaciones de Análisis (P0/P1/P3).

Cubren lo que la suite no tocaba: `build_analisis` completo, filtro local,
descubrimiento global, racha independiente del filtro, normalización
from/to y normalización de la clave de caché.

SQLite `:memory:`, sin tocar `data/`.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.analisis import _filter_routes, build_analisis
from app.analisis_cache import (
    get_analisis_cached,
    invalidate_analisis_cache,
)
from app.db import Base
from app.models import Route, User


@pytest.fixture
def memdb():
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
    )
    TestSessionLocal = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, future=True,
    )
    Base.metadata.create_all(bind=engine)
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


def _user(db, email="a@x.es") -> int:
    u = User(email=email, password_hash="h", is_active=1, role="user")
    db.add(u)
    db.commit()
    return u.id


def _route(db, uid, started_at, tz="Europe/Madrid", cid=1, km=10.0,
           name="R", lat=43.0, lon=-1.5) -> Route:
    r = Route(
        user_id=uid, name=name, name_original=name, started_at=started_at,
        start_lat=lat, start_lon=lon, distance_km=km,
        route_cluster_id=cid, timezone=tz,
    )
    db.add(r)
    db.commit()
    return r


def test_zero_distance_sin_division_por_cero(memdb):
    db = memdb
    uid = _user(db)
    for i in range(3):
        _route(db, uid, datetime(2025, 5, i + 1, 7, 0), km=0.0, cid=i + 1)
    data = build_analisis(db, uid)
    assert data.has_data
    assert tuple(data.map_center) == (42.7, -1.6)
    assert all(z.pct == 0 for z in data.zones)


def test_monthly_agrupa_por_mes_local(memdb):
    db = memdb
    uid = _user(db)
    y = date.today().year
    # 22:30Z del 30 de junio -> 00:30 local del 1 de julio (Madrid).
    _route(db, uid, datetime(y, 6, 30, 22, 30), km=10.0, cid=1)
    _route(db, uid, datetime(y, 1, 10, 9, 0), km=5.0, cid=2)
    _route(db, uid, datetime(y, 1, 11, 9, 0), km=5.0, cid=3)
    data = build_analisis(db, uid)
    june = next(m for m in data.monthly if m.label.startswith("jun"))
    july = next(m for m in data.monthly if m.label.startswith("jul"))
    assert july.km == 10.0
    assert june.km == 0


def test_filter_temporada_usa_dia_local(memdb):
    db = memdb
    uid = _user(db)
    # 20 jun 22:30Z -> 21 jun local: primer día de verano (desde 21 jun).
    r_in = _route(db, uid, datetime(2025, 6, 20, 22, 30), cid=1)
    # 19 jun 22:30Z -> 20 jun local: aún primavera.
    r_out = _route(db, uid, datetime(2025, 6, 19, 22, 30), cid=2)
    rows = _filter_routes(db, uid, None, None, season_key="summer")
    ids = {r.id for r in rows}
    assert r_in.id in ids
    assert r_out.id not in ids


def test_discovery_cuenta_primera_vez_global(memdb):
    db = memdb
    uid = _user(db)
    recent = datetime.combine(date.today() - timedelta(days=30), datetime.min.time())
    _route(db, uid, datetime(2022, 5, 10, 7, 0), cid=1, km=8.0)
    _route(db, uid, recent, cid=1, km=8.0)
    _route(db, uid, recent, cid=2, km=8.0)
    data = build_analisis(db, uid, range_key="year")
    # El cluster 1 nació en 2022: este año no aporta "nuevas".
    assert sum(d.new_routes for d in data.discovery) == 1


def test_streak_current_ignora_el_filtro(memdb):
    db = memdb
    uid = _user(db)
    monday = date.today() - timedelta(days=date.today().weekday())
    wed = datetime.combine(monday + timedelta(days=2), datetime.min.time()).replace(hour=12)
    _route(db, uid, wed, cid=1)
    _route(db, uid, datetime(2022, 5, 10, 7, 0), cid=2)
    _route(db, uid, datetime(2022, 5, 17, 7, 0), cid=3)
    data = build_analisis(
        db, uid, range_key="custom",
        from_date="2022-05-01", to_date="2022-05-31",
    )
    assert data.streak.current >= 1


def test_custom_from_to_se_normaliza(memdb):
    db = memdb
    uid = _user(db)
    for i in range(3):
        _route(db, uid, datetime(2024, 3, i + 1, 7, 0), cid=i + 1)
    data = build_analisis(
        db, uid, range_key="custom",
        from_date="2026-12-31", to_date="2020-01-01",
    )
    assert data.from_date == "2020-01-01"
    assert data.to_date == "2026-12-31"


def test_cache_key_normalizada_por_rango(memdb):
    db = memdb
    uid = _user(db, "cache@x.es")
    calls = []

    def build_fn(db_, uid_, rk, f, t):
        calls.append((rk, f, t))
        return f"data-{rk}"

    try:
        assert get_analisis_cached(build_fn, db, uid, "ALL", None, None) == "data-ALL"
        assert get_analisis_cached(build_fn, db, uid, "all", None, None) == "data-ALL"
        # from/to se ignoran fuera de custom: mismo HIT.
        assert get_analisis_cached(build_fn, db, uid, "all", "x", "y") == "data-ALL"
        assert len(calls) == 1
        assert get_analisis_cached(
            build_fn, db, uid, "custom", "2026-01-01", "2026-12-31") is not None
        assert len(calls) == 2
    finally:
        invalidate_analisis_cache(uid)


def test_api_payload_lleva_campos_completos(memdb):
    """Contrato P1: la serialización real de /api/analisis con los campos SSR."""
    from app.main import _serialize_api_analisis

    db = memdb
    uid = _user(db, "payload@x.es")
    for i in range(3):
        _route(db, uid, datetime(2025, 5, i + 1, 7, 0), cid=i + 1)
    data = build_analisis(db, uid)
    extra = _serialize_api_analisis(data)
    assert extra["zones"] and all("km" in z and "barPct" in z for z in extra["zones"])
    assert extra["topRoutes"] and all(
        k in extra["topRoutes"][0]
        for k in ("lastDateIso", "daysSince", "avgGain", "trend", "elevLine")
    )
    assert extra["heroStats"] and extra["chips"]
