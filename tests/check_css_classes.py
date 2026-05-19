"""Verifica presencia real de clases CSS en JS/HTML via busqueda de texto.

Resultados sesion 2025 (rama chore/dead-code-audit)
----------------------------------------------------
ATENCION: este script busca solo en static/js/*.js y templates/*.html.
Para una busqueda exhaustiva usar check_css_groups.py o check_role_classes.py
que incluyen tambien app/*.py y admin_usuarios.js/html.

Clases OK (falsos positivos del extractor de run_css_audit.py):
- route-card, route-row, route-card-*, route-info, route-num, route-stat,
  route-stats, route-tag-wrap, route-arrow  -> generadas via innerHTML en rutas.js
- dup-group, dup-group-*, dup-tag, dup-tag-*  -> generadas via innerHTML en importar.js
- import-log-line, scatter-dot, search-hl  -> generadas via innerHTML en JS
- ana-combo-item, ana-combo-empty          -> generadas via innerHTML en analisis.js
- skeleton, route-row-skel                 -> generadas via innerHTML en rutas.js
- mendi-ctrl-btn, mendi-summit-ctrl        -> generadas via innerHTML en detail.js
- summit                                   -> usada en detail.js y detail.html
- is-active                                -> usada en analisis.js
- heat-1..4                                -> asignadas desde analisis.py (backend)
- leaflet-container, leaflet-control-layers*, leaflet-popup-*
                                           -> Leaflet las inyecta en runtime,
                                              el CSS las necesita para sobrescribir
                                              estilos de la libreria. NO TOCAR.

Clases DEAD confirmadas (verificadas con check_css_groups.py y check_role_classes.py):
- card-skel          -> reemplazada por route-row-skel, nunca aplicada
- role-admin/user/viewer -> solo en CSS como .role-chip.role-*, ningun elemento
                           las recibe en ningun template ni JS (incluido admin)
- nav-section        -> declarada, no existe en ningun template
- flash-ok/err/info  -> sistema flash migrado a server-side, clases huerfanas
- row-actions        -> declarada, no usada en ningun template ni JS
- import-log-dup/err/ok -> el JS usa import-log-line + kind como texto, no como clase
- ana-donut-easy/hard/moderate/very-hard
- ana-top-level-easy/hard/moderate/very-hard
- ana-ratio-accent/cool/warm
- ana-rec-accent/cool/danger/warm
  Todas las ana-*: el JS resuelve colores via colorMap[p.css] con variables CSS
  directas (--easy, --moderate...), nunca aplica estas clases a ningun elemento.

Decision: no se elimina nada hasta nueva orden.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
files = (
    list((ROOT / "static/js").glob("*.js")) +
    list((ROOT / "templates").glob("*.html"))
)

suspects = [
    "route-card", "route-row", "dup-group", "import-log-line",
    "scatter-dot", "search-hl", "heat-1", "heat-2", "heat-3", "heat-4",
    "skeleton", "card-skel", "route-row-skel", "ana-combo-item",
    "ana-combo-empty", "role-admin", "role-user", "role-viewer",
    "nav-section", "leaflet-container", "mendi-ctrl-btn", "summit",
    "is-active", "flash-ok", "flash-err", "flash-info",
    "route-card-body", "route-card-elev", "route-card-meta",
    "route-card-name", "route-card-stat", "route-card-stats",
    "route-info", "route-num", "route-stat", "route-stats",
    "route-tag-wrap", "row-actions", "route-arrow",
    "ana-donut-easy", "ana-donut-hard", "ana-donut-moderate", "ana-donut-very-hard",
    "ana-ratio-accent", "ana-ratio-cool", "ana-ratio-warm",
    "ana-rec-accent", "ana-rec-cool", "ana-rec-danger", "ana-rec-warm",
    "ana-top-level-easy", "ana-top-level-hard", "ana-top-level-moderate", "ana-top-level-very-hard",
    "axis", "block", "col", "date", "end", "fname", "grid", "lg", "line",
    "region", "sm", "start", "x",
    "leaflet-control-layers", "leaflet-control-layers-expanded",
    "leaflet-control-layers-toggle", "leaflet-popup-close-button",
    "leaflet-popup-content", "leaflet-popup-content-wrapper", "leaflet-popup-tip",
    "mendi-summit-ctrl", "dup-group-hash", "dup-group-keep", "dup-group-remove",
    "dup-tag", "dup-tag-keep", "dup-tag-remove",
    "import-log-dup", "import-log-err", "import-log-ok",
]

dead = []
for cls in suspects:
    hits = [f.name for f in files if cls in f.read_text(encoding="utf-8", errors="ignore")]
    status = "OK  " if hits else "DEAD"
    if not hits:
        dead.append(cls)
    print(f"{status}  {cls:45s}  {hits}")

print(f"\nTotal DEAD: {len(dead)}")
print("DEAD:", dead)
