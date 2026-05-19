"""Audit rapido de clases CSS: declaradas vs usadas en HTML/JS.

Resultados sesion 2025 (rama chore/dead-code-audit)
----------------------------------------------------
Declaradas en CSS : 548
Usadas en HTML/JS : 472
DEAD aparentes    : 85  (ver analisis detallado en check_css_classes.py)

NOTA IMPORTANTE sobre falsos positivos del extractor:
- El extractor de JS solo detecta classList.add/remove/toggle y setAttribute.
  Las clases inyectadas via innerHTML con template literals NO se detectan.
  Ejemplo: rutas.js genera route-card, route-row, etc. via buildCardHTML().
- Siempre verificar con check_css_classes.py antes de borrar nada.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.audit_css import (
    extract_declared_classes,
    extract_html_classes,
    extract_js_classes,
    extract_declared_tokens,
    extract_used_tokens,
)

ROOT = Path(__file__).resolve().parent.parent
css_dir = ROOT / "static/css"
templates_dir = ROOT / "templates"
js_dir = ROOT / "static/js"

# --- Clases ---
declared = set()
for f in sorted(css_dir.glob("*.css")):
    found = extract_declared_classes(f.read_text(encoding="utf-8"))
    print(f"  {f.name}: {len(found)} clases declaradas")
    declared |= found

active_html = set()
for f in sorted(templates_dir.glob("*.html")):
    active_html |= extract_html_classes(f.read_text(encoding="utf-8"))

active_js = set()
for f in sorted(js_dir.glob("*.js")):
    active_js |= extract_js_classes(f.read_text(encoding="utf-8"))

active = active_html | active_js
unused = declared - active
undeclared = active - declared

print(f"\nDeclaradas en CSS : {len(declared)}")
print(f"Usadas en HTML/JS : {len(active)}")

print(f"\n=== DECLARADAS SIN USO ({len(unused)}) ===")
for c in sorted(unused):
    print(f"  {c}")

print(f"\n=== USADAS SIN DECLARAR ({len(undeclared)}) ===")
for c in sorted(undeclared):
    print(f"  {c}")

# --- Tokens CSS ---
all_css = "\n".join(f.read_text(encoding="utf-8") for f in css_dir.glob("*.css"))
tok_declared = extract_declared_tokens(all_css)
tok_used = extract_used_tokens(all_css)
tok_unused = tok_declared - tok_used
tok_undeclared = tok_used - tok_declared

print(f"\n=== TOKENS DECLARADOS SIN USO ({len(tok_unused)}) ===")
for t in sorted(tok_unused):
    print(f"  {t}")

print(f"\n=== TOKENS USADOS SIN DECLARAR ({len(tok_undeclared)}) ===")
for t in sorted(tok_undeclared):
    print(f"  {t}")
