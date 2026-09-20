"""Tests de hardening H10 (revocar sesiones) y H11 (backfill por usuario).

SQLite en memoria + `Base.metadata.create_all`, siguiendo el patrón de
`tests/test_geo_canonical.py`. No toca `data/` ni la BD real.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.main as main_module
from app.auth import resolve_session, revoke_user_sessions
from app.clustering import backfill_clusters_for_user
from app.db import Base
from app.main import (
    MAX_UPLOAD_FILE_BYTES,
    MAX_UPLOAD_FILES,
    MAX_UPLOAD_TOTAL_BYTES,
    _check_upload_count,
    _check_upload_size,
    _login_rate_check,
    _login_rate_clear,
    _login_rate_fail,
)
from app.models import Route, User, UserSession


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


def _user(db, email: str) -> User:
    u = User(email=email, password_hash="hash", is_active=1, role="user")
    db.add(u)
    db.commit()
    return u


def _session(db, user_id: int, token: str) -> UserSession:
    s = UserSession(
        id=token,
        user_id=user_id,
        expires_at=datetime.now(UTC) + timedelta(days=30),
        revoked=0,
    )
    db.add(s)
    db.commit()
    return s


def _route(db, user_id: int, lat: float, lon: float, alt: int = 1000) -> Route:
    r = Route(
        user_id=user_id,
        name="Ruta",
        name_original="Ruta",
        started_at=datetime.now(UTC),
        start_lat=lat,
        start_lon=lon,
        max_altitude_m=alt,
    )
    db.add(r)
    db.commit()
    return r


def test_revoke_user_sessions_revoca_solo_las_activas_del_usuario(memdb):
    db = memdb
    ana = _user(db, "ana@x.es")
    bob = _user(db, "bob@x.es")
    _session(db, ana.id, "tok-ana-1")
    _session(db, ana.id, "tok-ana-2")
    old = _session(db, ana.id, "tok-ana-vieja")
    old.revoked = 1
    db.commit()
    _session(db, bob.id, "tok-bob-1")

    assert revoke_user_sessions(db, ana.id) == 2
    assert resolve_session(db, "tok-ana-1") is None
    assert resolve_session(db, "tok-ana-2") is None
    # El otro usuario conserva su sesión.
    assert resolve_session(db, "tok-bob-1") is not None
    # Segunda llamada: nada que revocar.
    assert revoke_user_sessions(db, ana.id) == 0


def test_backfill_por_usuario_no_toca_clusters_ajenos(memdb):
    db = memdb
    ana = _user(db, "ana@x.es")
    bob = _user(db, "bob@x.es")
    # Dos rutas cercanas de Ana -> mismo cluster tras backfill.
    _route(db, ana.id, 43.0, -1.5, 1000)
    _route(db, ana.id, 43.0005, -1.5005, 1010)
    # Rutas de Bob lejos, con cluster preexistente.
    rb1 = _route(db, bob.id, 46.0, 2.0, 500)
    rb2 = _route(db, bob.id, 46.0005, 2.0005, 510)
    rb1.route_cluster_id = 7
    rb2.route_cluster_id = 7
    db.commit()

    n = backfill_clusters_for_user(db, ana.id)

    assert n == 1
    db.refresh(rb1)
    db.refresh(rb2)
    assert (rb1.route_cluster_id, rb2.route_cluster_id) == (7, 7)
    clusters_ana = {
        r.route_cluster_id
        for r in db.query(Route).filter(Route.user_id == ana.id).all()
    }
    assert clusters_ana == {1}


def test_upload_count_por_encima_del_limite_lanza_413():
    with pytest.raises(HTTPException) as exc:
        _check_upload_count([None] * (MAX_UPLOAD_FILES + 1))
    assert exc.value.status_code == 413
    _check_upload_count([None] * MAX_UPLOAD_FILES)  # no lanza


def test_upload_size_por_archivo_y_total():
    assert _check_upload_size(100, 0) is None
    msg = _check_upload_size(MAX_UPLOAD_FILE_BYTES + 1, 0)
    assert msg is not None and "25 MB" in msg
    msg = _check_upload_size(100, MAX_UPLOAD_TOTAL_BYTES)
    assert msg is not None and "200 MB" in msg


def _fake_request(ip: str):
    from types import SimpleNamespace

    return SimpleNamespace(client=SimpleNamespace(host=ip))


def test_limiter_solo_cuenta_fallos_y_el_exito_limpia():
    """H6: 5 fallos -> 429; un éxito intermedio resetea el contador."""
    main_module._LOGIN_ATTEMPTS.clear()
    try:
        req = _fake_request("10.9.9.9")
        for _ in range(5):
            _login_rate_check(req)  # mirar no consume
            _login_rate_fail(req)
        with pytest.raises(HTTPException) as exc:
            _login_rate_check(req)
        assert exc.value.status_code == 429
        _login_rate_clear(req)
        _login_rate_check(req)  # ya no lanza
    finally:
        main_module._LOGIN_ATTEMPTS.clear()
