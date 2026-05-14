"""Tests del limpiador de nombres y detector de región."""
from __future__ import annotations

import pytest

from app.name_cleaner import clean_name, detect_region


def test_clean_name_quita_emojis_y_prefijos():
    raw = "🗺️ Navarra - 🧭 Ardanaz - ⛰️ Tangorriti y ⛰️ Malkaitz"
    out = clean_name(raw)
    # Los emojis deben desaparecer.
    assert "🗺" not in out
    assert "⛰" not in out
    # Las regiones puras (Navarra) se eliminan; el contenido relevante se conserva.
    assert "Ardanaz" in out
    assert "Tangorriti" in out
    assert "Malkaitz" in out


def test_clean_name_quita_prefijo_wikiloc():
    out = clean_name("Wikiloc - Pico Anayet desde Portalet")
    assert "Wikiloc" not in out
    assert "Pico Anayet" in out


def test_clean_name_vacio_devuelve_sin_nombre():
    assert clean_name("") == "Sin nombre"
    assert clean_name(None) == "Sin nombre"  # type: ignore[arg-type]


def test_clean_name_solo_region_devuelve_algo():
    # Si todo es región pura, el resultado no debe ser cadena vacía.
    out = clean_name("Navarra")
    assert out and out != ""


@pytest.mark.parametrize("text,country,region", [
    ("Pico Larrun en Navarra", "espana", "navarra"),
    ("Senderismo Bizkaia", "espana", "vizcaya"),
    ("Aralar Gipuzkoa", "espana", "guipuzcoa"),
    ("Picos de Europa", "espana", "cantabria"),
])
def test_detect_region_keywords(text: str, country: str, region: str):
    c, r, _ = detect_region(text)
    assert c == country
    assert r == region


def test_detect_region_pirineo_aragones_gana_a_pirineo():
    # "pirineo aragones" se ordena antes que "pirineo" por longitud descendente.
    _, _, sub = detect_region("Aneto pirineo aragones")
    assert sub == "pirineo aragones"


def test_detect_region_sin_match():
    c, r, s = detect_region("una ruta cualquiera por el campo")
    assert (c, r, s) == (None, None, None)
