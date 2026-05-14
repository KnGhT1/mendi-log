"""Tests del parser GPX.

Trabajamos sobre un GPX sintético construido en memoria para no depender de
ficheros del repositorio. Validamos:
- ValueError en GPX vacío (sin trkpt) — se usa en `importer.process_gpx`.
- Distancia / desnivel positivos para una rampa simple.
- start_lat / start_lon coinciden con el primer punto.
"""
from __future__ import annotations

import pytest

from app.gpx_parser import parse_gpx


def _gpx(points: list[tuple[float, float, float, str | None]]) -> bytes:
    """Genera un GPX mínimo con la lista de (lat, lon, elev, isoTime|None)."""
    pts = []
    for lat, lon, ele, t in points:
        time_xml = f"<time>{t}</time>" if t else ""
        pts.append(
            f'<trkpt lat="{lat}" lon="{lon}"><ele>{ele}</ele>{time_xml}</trkpt>'
        )
    body = "\n".join(pts)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">'
        '<trk><name>Ruta de prueba</name><trkseg>'
        f'{body}'
        '</trkseg></trk></gpx>'
    ).encode("utf-8")


def test_gpx_vacio_lanza_value_error():
    empty = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">'
        '<trk><trkseg></trkseg></trk></gpx>'
    ).encode("utf-8")
    with pytest.raises(ValueError):
        parse_gpx(empty)


def test_parse_rampa_simple_calcula_distancia_y_desnivel():
    # Tres puntos en línea recta separados ~100 m con +50 m de elevación cada uno.
    # 0.001° de latitud ≈ 111 m. Usamos 0.0009 ≈ 100 m.
    pts = [
        (43.0000, -1.5000, 1000.0, "2026-05-01T08:00:00Z"),
        (43.0009, -1.5000, 1050.0, "2026-05-01T08:01:30Z"),
        (43.0018, -1.5000, 1100.0, "2026-05-01T08:03:00Z"),
        (43.0027, -1.5000, 1150.0, "2026-05-01T08:04:30Z"),
    ]
    stats = parse_gpx(_gpx(pts))

    # Distancia total razonable (~300 m = 0.3 km)
    assert 0.25 <= stats.distance_km <= 0.45

    # Desnivel positivo aproximado: 150 m. El parser aplica suavizado y
    # umbral, así que aceptamos un rango amplio para no acoplar al algoritmo.
    assert stats.elevation_gain_m >= 100
    assert stats.elevation_loss_m == 0

    # El primer punto define el start.
    assert abs(stats.start_lat - 43.0000) < 1e-6
    assert abs(stats.start_lon - (-1.5000)) < 1e-6

    # Altitud mínima/máxima coherentes.
    assert stats.min_altitude_m is not None
    assert stats.max_altitude_m is not None
    assert stats.min_altitude_m <= stats.max_altitude_m


def test_parse_devuelve_paths_svg_no_vacios():
    pts = [
        (43.0, -1.5, 1000.0, "2026-05-01T08:00:00Z"),
        (43.0009, -1.5, 1100.0, "2026-05-01T08:02:00Z"),
        (43.0018, -1.5, 1050.0, "2026-05-01T08:04:00Z"),
    ]
    stats = parse_gpx(_gpx(pts))
    # Los paths SVG se usan en el hero; deben ser cadenas no vacías.
    assert stats.elev_line_path
    assert stats.elev_area_path
    assert stats.elev_line_path.startswith("M")
