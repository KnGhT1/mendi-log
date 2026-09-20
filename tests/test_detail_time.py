"""Tests Fase 1: instantes UTC explícitos en el detalle (H-clima).

La BD guarda UTC naive. El backend debe etiquetar con `Z` para que el
navegador no los lea como hora local:
- hitos start/end: `time_utc` ISO+Z (además de `time_str` SSR).
- muestras del perfil: `t_iso` ISO+Z.
- tech: `start_utc`/`end_utc` ISO+Z.
- `api_clima` usa el mismo formato (cubierto aquí vía build_detail + lógica
  equivalente; el endpoint solo añade la `Z`).

SQLite en memoria, sin tocar `data/`.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.detail import build_detail
from app.models import Route, TrackPoint, User


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


def _ruta_con_track(db) -> tuple[int, int]:
    u = User(email="ana@x.es", password_hash="h", is_active=1, role="user")
    db.add(u)
    db.commit()
    # 09:14 UTC = 11:14 CEST: el SSR mostrará "09:14" pero t_utc lleva Z.
    r = Route(
        user_id=u.id, name="R", name_original="R",
        started_at=datetime(2025, 7, 5, 7, 14, tzinfo=None),
        start_lat=43.0, start_lon=-1.5,
    )
    db.add(r)
    db.commit()
    for seq, (lat, hour, minute, ele) in enumerate(
        [(43.0, 7, 14, 1000.0), (43.0009, 8, 15, 1100.0), (43.0018, 9, 15, 1050.0)]
    ):
        db.add(TrackPoint(
            route_id=r.id, seq=seq, lat=lat, lon=-1.5,
            elevation_m=ele,
            time=datetime(2025, 7, 5, hour, minute, tzinfo=None),
        ))
    db.commit()
    return u.id, r.id


def test_hitos_y_tech_llevan_instante_utc_con_z(memdb):
    db = memdb
    uid, rid = _ruta_con_track(db)
    data = build_detail(db, uid, rid)
    assert data is not None

    kinds = {m.kind: m for m in data.map.milestones}
    assert kinds["start"].time_utc == "2025-07-05T07:14:00Z"
    assert kinds["end"].time_utc == "2025-07-05T09:15:00Z"
    # SSR intacto (hora UTC cruda, el JS lo reescribe a local).
    assert kinds["start"].time_str == "07:14"
    assert data.tech.start_utc == "2025-07-05T07:14:00Z"
    assert data.tech.end_utc == "2025-07-05T09:15:00Z"
    assert "salida 07:14" in data.tech.start_end_str


def test_elev_samples_t_iso_con_z(memdb):
    db = memdb
    uid, rid = _ruta_con_track(db)
    data = build_detail(db, uid, rid)
    assert data is not None
    with_time = [s for s in data.elev.samples if s["t_iso"]]
    assert with_time, "se esperaban muestras con tiempo"
    for s in with_time:
        assert s["t_iso"].endswith("Z"), s["t_iso"]


def test_hero_y_clima_usan_fecha_local(memdb):
    """Fase 2: ruta de 00:30 local (22:30Z previas) muestra el día local."""
    from app.models import User

    db = memdb
    u = User(email="n@x.es", password_hash="h", is_active=1, role="user")
    db.add(u)
    db.commit()
    r = Route(
        user_id=u.id, name="N", name_original="N",
        started_at=datetime(2025, 7, 5, 22, 30),
        start_lat=43.0, start_lon=-1.5, timezone="Europe/Madrid",
    )
    db.add(r)
    db.commit()
    db.add(TrackPoint(
        route_id=r.id, seq=0, lat=43.0, lon=-1.5,
        elevation_m=1000.0, time=datetime(2025, 7, 5, 22, 30),
    ))
    db.commit()
    data = build_detail(db, u.id, r.id)
    assert data is not None
    assert data.hero.date_str == "06 jul 2025"
    assert "06 jul 2025" in data.weather_meta
