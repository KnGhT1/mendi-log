"""Smoke standalone para sub-tarea 2.9. Borrar tras verificar."""
from pathlib import Path
from scripts.audit_css import (
    extract_aliases_temporales,
    extract_page_css_links,
)

# --- aliases ---
css = (
    "/* alias temporal — eliminar tras migrar plantilla rutas.html */\n"
    ".featured-card { color: red; }\n"
    ".normal { color: blue; }"
)
aliases = extract_aliases_temporales(css)
assert aliases == {"featured-card"}, aliases

# Caso con guion doble.
css2 = (
    "/* alias temporal -- eliminar tras migrar plantilla detail.html */\n"
    ".elev-card { display: block; }\n"
)
assert extract_aliases_temporales(css2) == {"elev-card"}

# Caso con regla intermedia: NO debe asociar.
css3 = (
    "/* alias temporal — eliminar tras migrar plantilla rutas.html */\n"
    ".other { color: green; }\n"
    ".featured-card { color: red; }\n"
)
assert extract_aliases_temporales(css3) == {"other"}

# Whitespace y comentario intermedios sí permitidos.
css4 = (
    "/* alias temporal — eliminar tras migrar */\n"
    "/* otro comentario */\n\n"
    ".x { color: red; }\n"
)
assert extract_aliases_temporales(css4) == {"x"}

# Selector compuesto: todas las clases se extraen.
css5 = (
    "/* alias temporal — eliminar tras migrar */\n"
    ".foo.bar { color: red; }\n"
)
assert extract_aliases_temporales(css5) == {"foo", "bar"}

# Comentario sin la frase: nada.
css6 = "/* otro comentario */\n.zzz { color: red; }\n"
assert extract_aliases_temporales(css6) == set()

# --- page links ---
html = """
<head>
<link rel="stylesheet" href="{{ url_for('static', path='css/rutas.css') }}">
</head>
"""
links = extract_page_css_links(html)
assert links == (Path("static/css/rutas.css"),), links

# Caso login.html-like con dos <link> y un vendor (que NO está en css/).
html2 = """
<link rel="stylesheet" href="{{ url_for('static', path='vendor/leaflet.css') }}">
<link rel="stylesheet" href="{{ url_for('static', path='css/styles.css') }}">
<link rel="stylesheet" href="{{ url_for('static', path='css/login.css') }}">
"""
links2 = extract_page_css_links(html2)
assert links2 == (
    Path("static/css/styles.css"),
    Path("static/css/login.css"),
), links2

# Orden inverso de atributos.
html3 = '<link href="/static/css/foo.css" rel="stylesheet">'
assert extract_page_css_links(html3) == (Path("static/css/foo.css"),)

# Sin coincidencias.
assert extract_page_css_links("<p>nope</p>") == ()

# Preload no debe contar.
html4 = '<link rel="preload" as="style" href="/static/css/foo.css">'
assert extract_page_css_links(html4) == ()

print("OK")
