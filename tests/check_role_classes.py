"""Busqueda exhaustiva de clases role-admin/user/viewer en todo el proyecto.

Resultados sesion 2025 (rama chore/dead-code-audit)
----------------------------------------------------
Busca en todos los .html, .js, .py, .css excluyendo .venv y vendor.

Resultados:
- role-admin  -> solo aparece en static/css/styles.css como .role-chip.role-admin
- role-user   -> idem como .role-chip.role-user
- role-viewer -> idem como .role-chip.role-viewer

Ningun template (incluido admin_usuarios.html) ni JS (incluido admin_usuarios.js)
anade estas clases a ningun elemento del DOM. Son DEAD.

Decision: no se elimina nada hasta nueva orden.
"""
    if f.is_file()
    and f.suffix in (".html", ".js", ".py", ".css")
    and ".venv" not in str(f)
    and "vendor" not in str(f)
]
for c in ["role-admin", "role-user", "role-viewer"]:
    hits = []
    for f in all_files:
        for ln, line in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if c in line:
                hits.append((f.relative_to(ROOT), ln, line.strip()))
    print(f"--- {c} ({len(hits)} hits) ---")
    for path, ln, line in hits:
        print(f"  {path}:{ln}  {line[:90]}")
