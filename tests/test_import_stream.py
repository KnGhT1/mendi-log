"""Tests del streaming de importación (cuelgue con lotes grandes).

`_process_gpx_in_thread` commitea en su propia sesión (el hilo principal
solo emite eventos y nunca toca esa sesión), y un fallo inesperado por
archivo se convierte en evento `error` en vez de colgar el stream.

Sin red (summits mockeados) y sin tocar `data/`.
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.db as app_db
import app.main as app_main
from app.db import Base
from app.main import _process_gpx_in_thread
from app.models import Route, User


def _gpx(name: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gpx version="1.1" creator="test" '
        'xmlns="http://www.topografix.com/GPX/1/1">\n'
        f"  <trk><name>{name}</name>\n"
        "    <trkseg>\n"
        '      <trkpt lat="42.8200" lon="-1.6500">'
        "<ele>500</ele><time>2024-06-01T08:00:00Z</time></trkpt>\n"
        '      <trkpt lat="42.8210" lon="-1.6510">'
        "<ele>510</ele><time>2024-06-01T08:00:30Z</time></trkpt>\n"
        "    </trkseg>\n"
        "  </trk>\n"
        "</gpx>\n"
    ).encode("utf-8")


def _memdb(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
    )
    TestSessionLocal = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, future=True,
    )
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(app_db, "engine", engine)
    monkeypatch.setattr(app_db, "SessionLocal", TestSessionLocal)
    monkeypatch.setattr(app_main, "SessionLocal", TestSessionLocal)
    monkeypatch.setattr("app.importer.fetch_summits", lambda stats: [])
    return TestSessionLocal


def test_process_in_thread_error_sin_colgar(monkeypatch):
    TestSessionLocal = _memdb(monkeypatch)
    db = TestSessionLocal()
    try:
        u = User(email="s@x.es", password_hash="h", is_active=1, role="user")
        db.add(u)
        db.commit()
        res = _process_gpx_in_thread(u.id, "roto.gpx", b"esto no es xml <")
        assert res.status == "error"
        assert TestSessionLocal().query(Route).count() == 0
    finally:
        db.close()


def test_process_in_thread_ok_commitea_en_sesion_propia(monkeypatch):
    TestSessionLocal = _memdb(monkeypatch)
    db = TestSessionLocal()
    try:
        u = User(email="o@x.es", password_hash="h", is_active=1, role="user")
        db.add(u)
        db.commit()
        uid = u.id
    finally:
        db.close()
    res = _process_gpx_in_thread(uid, "r.gpx", _gpx("Ruta Navarra"))
    assert res.status == "ok"
    assert res.route_id is not None
    # Visible desde una sesión nueva: el worker commiteó.
    db2 = TestSessionLocal()
    try:
        assert db2.query(Route).filter(Route.id == res.route_id).count() == 1
    finally:
        db2.close()
