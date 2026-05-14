"""Tests del cálculo de dificultad.

Cubren:
- Casos extremos: ruta trivial (0 todo) y maxout (≥25 km, ≥2200 m, ≥10 h).
- Monotonía: a más desnivel, score igual o mayor (con el resto fijo).
- Niveles: el bucket en `difficulty_level` debe respetar los umbrales
  declarados en el código (4 / 6 / 8).
"""
from __future__ import annotations

import pytest

from app.difficulty import DifficultyInputs, difficulty_level, difficulty_score


def _inp(km: float, gain: float, hours: float) -> DifficultyInputs:
    return DifficultyInputs(
        distance_km=km,
        elevation_gain_m=gain,
        moving_time_s=hours * 3600,
    )


def test_score_minimo_es_cero():
    assert difficulty_score(_inp(0, 0, 0)) == 0.0


def test_score_maximo_no_supera_diez():
    # Por encima de los topes (25 km, 2200 m, 10 h) la normalización satura a 1.
    score = difficulty_score(_inp(80, 5000, 20))
    assert score <= 10.0
    assert score >= 9.5  # debe estar muy cerca del techo


def test_monotono_en_desnivel():
    # Mantener distancia y tiempo fijos, subir desnivel ⇒ score no decrece.
    base = difficulty_score(_inp(10, 200, 3))
    medio = difficulty_score(_inp(10, 800, 3))
    alto = difficulty_score(_inp(10, 1600, 3))
    assert base <= medio <= alto


def test_intensidad_no_explota_si_distancia_cero():
    # No debe dividir por cero. Con distancia 0 no hay intensidad ponderable.
    assert difficulty_score(_inp(0, 1000, 0)) >= 0.0


@pytest.mark.parametrize("score,expected", [
    (0.0, "easy"),
    (3.9, "easy"),
    (4.0, "moderate"),
    (5.5, "moderate"),
    (6.0, "hard"),
    (7.9, "hard"),
    (8.0, "very-hard"),
    (10.0, "very-hard"),
])
def test_niveles_segun_score(score: float, expected: str):
    assert difficulty_level(score) == expected
