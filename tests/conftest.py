"""Configuración de pytest: añade la raíz del proyecto al sys.path.

Permite ejecutar `pytest` desde la raíz sin instalar el paquete.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
