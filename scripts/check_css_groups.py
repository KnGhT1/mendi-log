"""Verifica grupos de clases CSS en templates, JS y app Python.

Resultados sesion 2025 (rama chore/dead-code-audit)
----------------------------------------------------
Busca en templates/*.html + static/js/*.js + app/*.py.
NOTA: no incluye admin_usuarios.html ni admin_usuarios.js.
Para role-admin/user/viewer usar check_role_classes.py que hace rglob completo.

Resultados:
- heat-1..4   -> OK, asignadas desde analisis.py (backend, calendario heatmap)
- card-skel   -> DEAD
- role-admin/user/viewer -> DEAD segun este script, pero verificar con
                            check_role_classes.py (busqueda exhaustiva)
- nav-section -> DEAD
- flash-ok/err/info -> DEAD
- row-actions -> DEAD
- import-log-dup/err/ok -> DEAD
"""
    list((ROOT / "templates").glob("*.html")) +
    list((ROOT / "static/js").glob("*.js")) +
    list((ROOT / "app").glob("*.py"))
)
checks = [
    "heat-1", "heat-2", "heat-3", "heat-4",
    "card-skel", "role-admin", "role-user", "role-viewer",
    "nav-section", "flash-ok", "flash-err", "flash-info",
    "row-actions", "import-log-dup", "import-log-err", "import-log-ok",
]
for c in checks:
    hits = [f.name for f in all_files if c in f.read_text(encoding="utf-8", errors="ignore")]
    print(f"{'DEAD' if not hits else 'OK  '}  {c:30s}  {hits}")
