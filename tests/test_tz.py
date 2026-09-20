"""Tests Fase 2: zona horaria por ruta y fecha local.

- `app/tz.py` puro: resolución, conversión con DST, degradación a UTC.
- Migración: `_ensure_route_columns` re-añade la columna tras DROP,
  `_backfill_route_timezones` la rellena y es idempotente, `_purge_weather_cache`
  solo purga con migración pendiente.
- Filtros: `query_rutas` con día local incluye rutas nocturnas que en UTC
  caen el día anterior.

Todo en SQLite `:memory:`, sin tocar `data/`.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.db as app_db
from app.db import Base
from app.models import Route, User, WeatherCache
from app.tz import local_date, local_datetime, resolve_timezone


def test_resolve_timezone_madrid_y_cdmx():
    assert resolve_timezone(40.41, -3.70) == "Europe/Madrid"
    assert resolve_timezone(19.43, -99.13) == "America/Mexico_City"


def test_resolve_timezone_sin_coords_devuelve_none():
    assert resolve_timezone(None, -3.7) is None
    assert resolve_timezone(40.4, None) is None


def test_local_date_respeta_dst():
    # Verano CEST (UTC+2): 07:14Z -> mismo día 09:14 local.
    assert local_date(datetime(2025, 7, 5, 7, 14), "Europe/Madrid").isoformat() == "2025-07-05"
    # Madrugada: 22:30Z del día 5 -> 00:30 local del día 6.
    assert local_date(datetime(2025, 7, 5, 22, 30), "Europe/Madrid").isoformat() == "2025-07-06"
    # Invierno CET (UTC+1).
    assert local_date(datetime(2025, 1, 15, 8, 30), "Europe/Madrid").isoformat() == "2025-01-15"


def test_local_datetime_zona_invalida_degrada_a_utc():
    dt = datetime(2025, 7, 5, 7, 14)
    assert local_datetime(dt, "No/Existe").utcoffset().total_seconds() == 0
    assert local_datetime(dt, None).utcoffset().total_seconds() == 0
    assert local_datetime(None, "Europe/Madrid") is None


@pytest.fixture
def mem_engine(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
    )
    TestSessionLocal = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, future=True,
    )
    monkeypatch.setattr(app_db, "engine", engine)
    monkeypatch.setattr(app_db, "SessionLocal", TestSessionLocal)
    Base.metadata.create_all(bind=engine)
    return engine, TestSessionLocal


def test_ensure_reanade_columna_y_backfill_rellena(mem_engine):
    from app.db import _backfill_route_timezones, _ensure_route_columns

    engine, TestSessionLocal = mem_engine
    with engine.connect() as conn:
        conn.exec_driver_sql("ALTER TABLE routes DROP COLUMN timezone")
        conn.commit()
    cols = {r[1] for r in engine.connect().exec_driver_sql("PRAGMA table_info(routes)")}
    assert "timezone" not in cols

    _ensure_route_columns()
    cols = {r[1] for r in engine.connect().exec_driver_sql("PRAGMA table_info(routes)")}
    assert "timezone" in cols

    db = TestSessionLocal()
    try:
        u = User(email="a@x.es", password_hash="h", is_active=1, role="user")
        db.add(u)
        db.commit()
        db.add(Route(
            user_id=u.id, name="R", name_original="R",
            started_at=datetime(2025, 7, 5, 7, 14),
            start_lat=43.0, start_lon=-1.5, timezone=None,
        ))
        db.commit()
        _backfill_route_timezones()
        db.expire_all()
        assert db.query(Route).first().timezone == "Europe/Madrid"
        # Idempotente: segunda pasada no cambia nada.
        _backfill_route_timezones()
        db.expire_all()
        assert db.query(Route).first().timezone == "Europe/Madrid"
    finally:
        db.close()


def test_purge_solo_con_migracion_pendiente(mem_engine):
    from app.db import _purge_weather_cache

    engine, TestSessionLocal = mem_engine
    db = TestSessionLocal()
    try:
        u = User(email="b@x.es", password_hash="h", is_active=1, role="user")
        db.add(u)
        db.commit()
        db.add(Route(
            user_id=u.id, name="R", name_original="R",
            started_at=datetime(2025, 7, 5, 7, 14),
            start_lat=43.0, start_lon=-1.5, timezone=None,
        ))
        db.add(WeatherCache(lat=43.0, lon=-1.5, date_iso="2025-07-05", payload="{}"))
        db.commit()
        _purge_weather_cache()
        assert db.query(WeatherCache).count() == 0
        # Sin rutas pendientes, conserva la caché.
        db.add(WeatherCache(lat=43.0, lon=-1.5, date_iso="2025-07-05", payload="{}"))
        db.query(Route).update({"timezone": "Europe/Madrid"})
        db.commit()
        _purge_weather_cache()
        assert db.query(WeatherCache).count() == 1
    finally:
        db.close()


def test_query_rutas_filtra_por_dia_local(mem_engine):
    """Ruta de 00:30 local (22:30Z previas): el día local la incluye,
    el día UTC anterior también la contendría pero el filtro pide el local."""
    from app.stats import RutaQuery, query_rutas

    _, TestSessionLocal = mem_engine
    db = TestSessionLocal()
    try:
        u = User(email="c@x.es", password_hash="h", is_active=1, role="user")
        db.add(u)
        db.commit()
        db.add(Route(
            user_id=u.id, name="Nocturna", name_original="Nocturna",
            started_at=datetime(2025, 7, 5, 22, 30),  # 00:30 CEST del día 6
            start_lat=43.0, start_lon=-1.5, timezone="Europe/Madrid",
        ))
        db.commit()
        res = query_rutas(
            db, u.id,
            RutaQuery(q="", difficulty=[], region="all", country="all",
                      distance="all", gain="all",
                      date_from="2025-07-06", date_to="2025-07-06",
                      sort="date-desc", offset=0, limit=10),
        )
        assert res.matched == 1
        assert res.items[0].date_sort == "2025-07-06"
        # Y el día UTC previo ya no la reclama.
        res2 = query_rutas(
            db, u.id,
            RutaQuery(q="", difficulty=[], region="all", country="all",
                      distance="all", gain="all",
                      date_from="2025-07-05", date_to="2025-07-05",
                      sort="date-desc", offset=0, limit=10),
        )
        assert res2.matched == 0
    finally:
        db.close()
