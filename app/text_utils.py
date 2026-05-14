"""Utilidades de texto compartidas entre módulos.

Antes había varias copias locales de `_strip_accents` (una en
`name_cleaner.py` y otra en `geocoder.py`) con la misma implementación. Las
centralizamos aquí para que cualquier nueva normalización (lowercase
acentos-fuera, etc.) tenga un único punto de definición.
"""
from __future__ import annotations

import unicodedata
from typing import Optional


def strip_accents(text: str) -> str:
    """Quita los diacríticos de una cadena (NFKD + filtrado de combining)."""
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(ch)
    )


def normalize(text: Optional[str]) -> Optional[str]:
    """`strip_accents` + lower + strip; devuelve None para entradas vacías."""
    if not text:
        return None
    return strip_accents(text).strip().lower() or None


def canonical_geo(value: Optional[str]) -> Optional[str]:
    """Regla canónica para campos geográficos (`country`, `region`, `sub_region`).

    Aplica NFKD + filtrado de combining + `lower` + `strip` + colapso de
    espacios internos a uno solo. Es el único contrato compartido por los
    writers (`name_cleaner.detect_region`, `geocoder._extract_region`) y por
    los lectores que canonicalizan inputs del usuario antes de comparar.

    Es idempotente: para cualquier `s` con `canonical_geo(s) == s`, una nueva
    pasada devuelve exactamente el mismo valor. Para `None`, cadena vacía o
    cadenas que tras normalizar quedan vacías, devuelve `None` (mismo
    comportamiento que `normalize` ante entradas vacías).
    """
    if value is None:
        return None
    stripped = "".join(
        ch for ch in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(ch)
    ).strip().lower()
    if not stripped:
        return None
    return " ".join(stripped.split())
