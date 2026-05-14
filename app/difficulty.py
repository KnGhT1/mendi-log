"""Calculo de la puntuacion de dificultad 0-10."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DifficultyInputs:
    distance_km: float
    elevation_gain_m: float
    moving_time_s: float


def _norm(value: float, lo: float, hi: float) -> float:
    """Normaliza `value` al rango [0, 1] entre `lo` y `hi` (clamp incluido)."""
    if hi <= lo:
        return 0.0
    if value <= lo:
        return 0.0
    if value >= hi:
        return 1.0
    return (value - lo) / (hi - lo)


def difficulty_score(inputs: DifficultyInputs) -> float:
    """Devuelve un score 0-10.

    Combina cuatro componentes:
      * distancia (0-25 km)
      * desnivel positivo (0-2200 m)
      * intensidad m/km (0-150)
      * tiempo en movimiento (0-10 h)

    Pesos: distancia 35 %, desnivel 55 %, intensidad 5 %, tiempo 5 %.
    """
    d = _norm(inputs.distance_km, 0, 25) * 10
    g = _norm(inputs.elevation_gain_m, 0, 2200) * 10
    intensity = (inputs.elevation_gain_m / inputs.distance_km) if inputs.distance_km > 0 else 0
    i = _norm(intensity, 0, 150) * 10
    t = _norm(inputs.moving_time_s / 3600.0, 0, 10) * 10

    # Pesos: desnivel manda; distancia secundaria; intensidad y tiempo modulan.
    score = 0.35 * d + 0.55 * g + 0.05 * i + 0.05 * t
    return round(max(0.0, min(10.0, score)), 1)


def difficulty_level(score: float) -> str:
    """Convierte un score 0-10 en etiqueta: easy / moderate / hard / very-hard."""
    if score < 4:
        return "easy"
    if score < 6:
        return "moderate"
    if score < 8:
        return "hard"
    return "very-hard"
