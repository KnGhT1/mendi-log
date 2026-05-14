"""Helpers de formateo para presentación.

Antes vivían dentro de `app/stats.py` mezclados con la lógica de agregación.
Los movemos aquí para que la capa de presentación (Jinja, plantillas) tenga
un único punto de entrada y `stats.py` se quede centrada en SQL/agregación.

Diseño: funciones puras (sin dependencias de DB ni config) que devuelven
strings listos para mostrar. Mantenemos los nombres `fmt_*` y la coma decimal
en español para no romper plantillas.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

# Etiquetas de mes en castellano (3 letras, minúscula). Idéntico al original
# en stats.py — repetido aquí como única fuente de verdad de presentación.
MONTH_LABELS_ES = ["ene", "feb", "mar", "abr", "may", "jun",
                   "jul", "ago", "sep", "oct", "nov", "dic"]


def fmt_km(km: float) -> str:
    """Formato con 2 decimales y coma decimal española. Ej: 7,63."""
    return f"{km:.2f}".replace(".", ",")


def fmt_km_short(km: float) -> str:
    """Formato con 1 decimal y coma decimal española. Ej: 7,6."""
    return f"{km:.1f}".replace(".", ",")


def fmt_int(n: int) -> str:
    """Entero con separador de miles con punto (estilo es-ES). Ej: 1.234."""
    # Separador de miles con punto (estilo es-ES).
    return f"{n:,}".replace(",", ".")


def fmt_duration(seconds: int) -> str:
    """Formatea segundos como 'Xh YYm'. Devuelve '0h 00m' para valores ≤ 0."""
    if seconds <= 0:
        return "0h 00m"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    return f"{h}h {m:02d}m"


def fmt_pace(distance_km: float, moving_time_s: int) -> str:
    """Formatea el ritmo como 'MM:SS'. Devuelve '—' si la distancia es 0."""
    if distance_km <= 0:
        return "—"
    secs_per_km = moving_time_s / distance_km
    m = int(secs_per_km // 60)
    s = int(secs_per_km % 60)
    return f"{m}:{s:02d}"


def fmt_date_es(d: datetime | date) -> str:
    """Formatea una fecha como 'DD mes YYYY' en español. Ej: 29 mar 2025."""
    return f"{d.day:02d} {MONTH_LABELS_ES[d.month - 1]} {d.year}"


def fmt_world_pct(pct: float) -> str:
    """Formatea el porcentaje de vuelta al mundo con 2 decimales y coma española."""
    return f"{pct:.2f}".replace(".", ",")


def fmt_hero_km(km: float) -> str:
    """Formato km para el hero del resumen: 1 decimal y coma española."""
    return f"{km:.1f}".replace(".", ",")


def difficulty_label_es(level: str) -> str:
    """Traduce el nivel de dificultad al español. Devuelve el valor original si no hay traducción."""
    return {
        "easy": "fácil",
        "moderate": "moderada",
        "hard": "difícil",
        "very-hard": "muy difícil",
    }.get(level, level)


def location_subtitle(region: Optional[str], sub_region: Optional[str]) -> str:
    """Texto de ubicación: 'region · sub_region', 'region' o '' si no hay datos."""
    bits = [b for b in (region, sub_region) if b]
    if not bits:
        return ""
    if region and sub_region:
        return f"{region} · {sub_region}"
    return bits[0]
