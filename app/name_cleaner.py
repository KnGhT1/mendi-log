"""Limpieza de nombres de rutas y deteccion de region.

Las palabras clave de región viven en `data/regions.json` (cargadas en arranque)
para no tener que tocar código si añadimos provincias o sub-regiones nuevas.
Hoy son todas españolas; si en el futuro hace falta soportar otros países el
JSON puede evolucionar a `[country, region, sub_region]` sin romper esta capa.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Tuple

from app.text_utils import canonical_geo, strip_accents as _strip_accents

logger = logging.getLogger(__name__)

# Cualquier emoji o pictograma. No usamos \p{Emoji} porque re estandar no lo soporta.
_EMOJI_RANGES = (
    (0x1F300, 0x1FAFF),  # emoticonos, simbolos, transporte, banderas, comida, suplem.
    (0x1F900, 0x1F9FF),
    (0x2600, 0x27BF),    # simbolos varios + dingbats
    (0xFE00, 0xFE0F),    # variation selectors
    (0x1F1E6, 0x1F1FF),  # banderas regionales
)

_EMOJI_PATTERN = re.compile(
    "[" + "".join(f"{chr(a)}-{chr(b)}" for a, b in _EMOJI_RANGES) + "]"
)

# Prefijos repetitivos de Wikiloc / OruxMaps / etc.
_PREFIX_NOISE = re.compile(
    r"^\s*(wikiloc|track|ruta|hiking|senderismo|gpx)\s*[-:·|]\s*",
    flags=re.IGNORECASE,
)
_SUFFIX_NOISE = re.compile(
    r"\s*[-:·|]\s*(wikiloc|track\s*\d*|gpx|trk|copy)\s*$",
    flags=re.IGNORECASE,
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_REGIONS_PATH = _PROJECT_ROOT / "data" / "regions.json"

# Fallback histórico (idéntico al JSON) si el fichero no se puede leer.
# Mantener el código funcional aunque alguien borre `data/regions.json`.
_REGION_KEYWORDS_FALLBACK: dict[str, tuple[str, str | None]] = {
    "navarra": ("navarra", None),
    "nafarroa": ("navarra", None),
    "pirineo aragones": ("huesca", "pirineo aragones"),
    "pirineo aragonés": ("huesca", "pirineo aragones"),
    "huesca": ("huesca", "pirineo aragones"),
    "aragon": ("huesca", "pirineo aragones"),
    "aragón": ("huesca", "pirineo aragones"),
    "pirineo": ("huesca", "pirineo aragones"),
    "vizcaya": ("vizcaya", None),
    "bizkaia": ("vizcaya", None),
    "guipuzcoa": ("guipuzcoa", None),
    "gipuzkoa": ("guipuzcoa", None),
    "alava": ("alava", None),
    "araba": ("alava", None),
    "la rioja": ("la rioja", None),
    "rioja": ("la rioja", None),
    "cantabria": ("cantabria", None),
    "asturias": ("asturias", None),
    "leon": ("leon", None),
    "león": ("leon", None),
    "picos de europa": ("cantabria", "picos de europa"),
}


def _load_region_keywords() -> dict[str, tuple[str, str | None]]:
    """Carga el mapa desde `data/regions.json` o cae al fallback embebido."""
    try:
        with _REGIONS_PATH.open("r", encoding="utf-8") as f:
            blob = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "name_cleaner: %s ilegible (%s); usando fallback embebido",
            _REGIONS_PATH.name, exc.__class__.__name__,
        )
        return dict(_REGION_KEYWORDS_FALLBACK)

    out: dict[str, tuple[str, str | None]] = {}
    for kw, val in blob.items():
        if kw.startswith("_"):  # claves de comentario `_comment`
            continue
        if isinstance(val, list) and len(val) == 2:
            region, sub = val
            out[kw] = (str(region), sub if sub is None else str(sub))
    return out or dict(_REGION_KEYWORDS_FALLBACK)


_REGION_KEYWORDS = _load_region_keywords()


def clean_name(raw: str) -> str:
    """Limpia un nombre de Wikiloc tipico.

    Ejemplos:
        "🗺️ Navarra - 🧭 Ardanaz - ⛰️ Tangorriti y ⛰️ Malkaitz"
            -> "Ardanaz · Tangorriti y Malkaitz"
        "Wikiloc - Pico Anayet desde Portalet"
            -> "Pico Anayet desde Portalet"
    """
    if not raw:
        return "Sin nombre"

    text = _EMOJI_PATTERN.sub("", raw)
    text = re.sub(r"\s+", " ", text).strip()
    text = _PREFIX_NOISE.sub("", text)
    text = _SUFFIX_NOISE.sub("", text)

    # Trocea por separadores y descarta los trozos que sean "region pura"
    parts = [p.strip(" -·|:") for p in re.split(r"\s+[-·|]\s+", text)]
    parts = [p for p in parts if p]

    cleaned_parts = []
    for p in parts:
        flat = _strip_accents(p).lower()
        if flat in _REGION_KEYWORDS:
            continue
        # descarta tambien algun ruido extra tipico
        if flat in {"hiking trails", "senderismo", "ruta", "track"}:
            continue
        cleaned_parts.append(p)

    if not cleaned_parts:
        # nombre completamente compuesto por regiones? devolvemos el original sin emojis
        return text or "Sin nombre"

    # Une con " · " que es el separador del mockup
    result = " · ".join(cleaned_parts)
    # Capitaliza si todo viene en minusculas/mayusculas
    if result.isupper() or result.islower():
        result = " · ".join(p.capitalize() for p in cleaned_parts)
    return result


def detect_region(
    raw_name: str, description: str = "",
) -> Tuple[str | None, str | None, str | None]:
    """Detecta (country, region, sub_region) desde el nombre + descripcion.

    Todas las claves de _REGION_KEYWORDS son provincias españolas, así que
    cuando hay match asumimos `country = "españa"`. Si no hay match, los
    tres valores son None y deja paso al fallback de geocodificación.
    """
    haystack = _strip_accents(f"{raw_name} {description}").lower()
    # Empezamos por la mas larga para que "pirineo aragones" gane a "pirineo"
    for kw in sorted(_REGION_KEYWORDS, key=len, reverse=True):
        if kw in haystack:
            region, sub_region = _REGION_KEYWORDS[kw]
            return (
                canonical_geo("españa"),
                canonical_geo(region),
                canonical_geo(sub_region),
            )
    return (None, None, None)
