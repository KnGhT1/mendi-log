# AGENTS.md - mendi.log

App web local (FastAPI + SQLite + Jinja + HTMX + Leaflet). Sin Node ni build
frontend. Python 3.14, entry point `app.main:app`, `lifespan` llama a `init_db()`.

## Comandos

```bash
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m pytest tests/test_difficulty.py -q   # un fichero
.venv/Scripts/python.exe -m pytest -q -k "nombre_parcial"        # un test
.venv/Scripts/python.exe scripts/create_user.py --email x@y.es --role admin  # alta (pide pass interactiva, min 12 chars)
.venv/Scripts/uvicorn.exe app.main:app --host 127.0.0.1 --port 8000 --reload --workers 1
```

- Suite actual: ~47 tests unitarios puros (difficulty, parser, name_cleaner,
  geo_canonical, css_audit). No necesita BD ni servidor.
- Siempre `--workers 1`: la caché de Análisis (`app/analisis_cache.py:_CACHE`,
  con `Lock`, clave `(user_id, range, from, to)`) vive en memoria de proceso.
- `scripts/audit_css.py` es esqueleto: `audit()`/`main()` lanzan
  `NotImplementedError`. Para CSS valida con `pytest tests/test_css_audit.py`,
  no ejecutes el script como CLI.

## Dónde está cada cosa

- `app/main.py`: handlers HTTP. `app/db.py`: engine, `get_session`,
  `commit()`, `init_db()` + migraciones suaves. `app/models.py`: ORM.
- Importación: `app/importer.py:process_gpx()` (sin commit) -> `gpx_parser`,
  `name_cleaner`, `geocoder`, `difficulty`, `summits`, `clustering`.
- Agregaciones: `app/stats.py` (Resumen/Rutas), `app/analisis.py` + caché en
  `app/analisis_cache.py`, `app/detail.py`.
- Auth/RBAC: `app/auth.py` (`require_writer`=admin+user, `require_admin`,
  `require_csrf`, `get_current_user`). Queries acotadas: `app/queries.py`.
- `data/` (SQLite + `gpx/<user_id>/` + `geocode_cache.json`) está en
  `.gitignore`: nunca lo crees, borres ni modifiques para implementar algo.

## Reglas que un agente rompería sin ayuda

- `process_gpx()` hace `flush()` pero nunca `commit()`; el handler decide:
  commit por archivo en `/importar/stream`, una vez al final en `/importar`.
  Los helpers que reciben `db` no commitean.
- Invalida Análisis solo por usuario afectado:
  `app.db.commit(db, invalidate_analisis=True, user_id=...)`. En `main.py` el
  alias local es `_commit(db, invalidate=..., user_id=...)` (nombre de kwarg
  distinto). No invalides todo el caché sin `user_id` salvo fallback.
- Toda query de `Route` se filtra por dueño vía `user_routes()` /
  `user_route_get_or_404()`. Recurso de otro usuario -> **404, nunca 403**.
  Páginas por rol (`/importar`, `/admin/usuarios`) -> 403 sí es correcto.
- Mutaciones exigen `require_csrf` + rol (`require_writer` rutas,
  `require_admin` usuarios). El CSRF llega en `X-CSRF-Token` (meta + wrapper
  global de fetch/HTMX en `base.html`); no lo omitas en endpoints nuevos.
- Invariantes admin (409 si se violan): siempre >= 1 admin activo; nadie se
  auto-elimina; el último admin no se degrada ni desactiva.
- `route_cluster_id` está materializado por usuario
  (`app/clustering.py`: trailhead < 150 m + alt. máx < 50 m). Al importar se
  asigna con `assign_cluster_for_new()`; no recalcules con union-find en la
  vista.
- Geo nuevo pasa siempre por `canonical_geo()` (`app/text_utils.py`,
  idempotente, minúsculas sin diacríticos). Rige para `country/region/sub_region`
  en importer, geocoder, maintenance y stats.
- Esquema sin migraciones: `create_all()` + `ALTER TABLE` idempotentes en
  `init_db()` (solo SQLite; en otros dialectos `_ensure_*` no hace nada). No
  declares soporte Postgres/MariaDB aunque `MENDI_DATABASE_URL` exista.
- Externos (Nominatim, Overpass, Open-Meteo) son lentos y falibles: httpx con
  timeout + fallback (geocoder silencioso, summits -> punto máximo, clima ->
  `{"available": false}`). Nunca hagas fallar una importación por ellos.
- `CacheHeadersMiddleware` y `AuthGuardMiddleware` son ASGI puros a propósito:
  `BaseHTTPMiddleware` consume el cuerpo y **rompe el streaming NDJSON**
  (`/importar/stream`, `/api/reprocesar`). No los conviertas.
- Frontend HTMX: `base.html` usa `hx-boost` con `hx-target/select="#hx-root"`.
  La hidratación (`scripts_extra` con `MENDI_*` JSON) debe ir **dentro** de
  `#hx-root` o el swap la descarta. Cada página registra
  `window.MENDI_PAGES[<page>].init()` y todo mapa/listener/timer/observer se
  limpia vía `window.MENDI_TEARDOWN`. Solo assets locales de `static/vendor/`;
  nada de CDN ni build.
- `MENDI_SECRET_KEY` obligatorio fuera de local (fallback dev solo con aviso
  en logs); `MENDI_REQUIRE_HTTPS=1` tras TLS. No loguees GPX, cookies, tokens,
  hashes ni secretos. Nombres de fichero GPX se sanean a basename + se
  validan con `resolve().relative_to()` antes de escribir.
- Estilo: `from __future__ import annotations`, tipos explícitos, dataclasses
  para DTOs, mensajes/UI en español. Cambios pequeños; no refactores
  `app/main.py` ni CSS global de paso.
- Versiones: fuente única `app/__init__.py:__version__` (SemVer), visible en
  footer/sidebar vía `_ctx(..., app_version=...)`. Bump = editar versión +
  entrada en `CHANGELOG.md` + commit + tag anotado `vX.Y.Z` (fix→parche,
  feature→minor, ruptura→major). Nunca hardcodees la versión en templates.
- Assets versionados: todo `url_for('static', ...)` en templates lleva el
  sufijo `?v={{ app_version }}` (Starlette 1.x NO admite `v=` como kwarg:
  lanza `NoMatchFound`; no intentarlo). Regla: PR que toque `static/` o
  `templates/` debe bumpear parche aunque el cambio no sea funcional.
  Excepciones documentadas (URL estable + `immutable`, invalidación manual):
  `url()` en CSS (fuentes), `images/` de Leaflet vendor y `.map` (solo
  DevTools). `login.html` no usa `_ctx`: su versión llega vía `_login_ctx()`.
