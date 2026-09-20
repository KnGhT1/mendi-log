# mendi.log

Aplicación web local para llevar un registro de tus rutas de montaña a partir
de archivos GPX (Wikiloc, Strava, etc.). 100 % local, 100 % tuyo.

Stack: FastAPI + SQLite + Jinja2 + HTMX + Leaflet, sin build de frontend.
Paleta nocturna con acentos verde-musgo, tipografías Fraunces + IBM Plex
autohospedadas, mapa Leaflet y siluetas de elevación en el hero.

## Características

- **Importación de GPX** drag & drop, multi-archivo (máx. 50 archivos,
  25 MB por archivo, 200 MB por lote). Calcula distancia, desnivel
  positivo y negativo, tiempo en movimiento, altitudes y un perfil SVG listo
  para mostrar como silueta del hero.
- **Streaming de importación** vía NDJSON (`/importar/stream`): feedback en
  tiempo real por archivo (leyendo → parseando → geocodificando → guardando).
  Commit por archivo para preservar progreso parcial (el batch `/importar`
  commitea una vez al final).
- **Deduplicación SHA-256 por usuario**: el mismo GPX importado dos veces se
  detecta y se ignora automáticamente.
- **Vista Resumen** (`/`): hero animado, 6 KPIs, mapa con marcadores,
  gráfica de kilómetros por mes (14 meses), donut de dificultad, heatmap
  calendario y tabla de rutas recientes.
- **Vista Rutas** (`/rutas`): listado paginado con filtros server-side
  (texto libre, dificultad, región, país, distancia, desnivel, fecha local)
  y ordenación múltiple. Servido vía `/api/rutas` (+ `/api/rutas/markers`
  para el mapa).
- **Vista Análisis** (`/analisis`): filtro por rango temporal (todo, temporada
  astronómica, año actual, últimos 12 meses, rango personalizado), hero stats,
  mapa de calor, zonas, ratio repetidas/nuevas, racha semanal, descubrimiento
  mensual, top-10 rutas, evolución mensual, donuts de dificultad y
  distancia, scatter km vs desnivel, récords personales, calendario heatmap y
  comparador de perfiles de elevación. Requiere ≥ 3 rutas.
- **Vista Detalle** (`/rutas/{id}`): mapa con track y hitos (salida, cima,
  llegada), perfil de elevación interactivo, datos técnicos (ritmo subida/
  bajada, VAM, pendiente media/máxima, tramo más duro, índice de fatiga),
  clima histórico en la cima (Open-Meteo), notas y etiquetas editables,
  rutas relacionadas y navegación anterior/siguiente.
- **Cimas editables**: detección automática (waypoints → Overpass → punto
  máximo) con creación, renombrado y borrado manual (nombre máx. 60 chars).
- **Zona horaria por ruta**: cada ruta guarda su zona IANA (`timezonefinder`,
  offline); horas y fechas se muestran en hora local, y las agrupaciones por
  día/mes usan el día local.
- **Tema oscuro / claro** persistido en `localStorage`.
- **Limpieza automática de nombres** (quita emojis y prefijos tipo "Wikiloc")
  con edición manual en línea desde la tabla de rutas y la vista de detalle.
- **Detección de región** por palabras clave (`data/regions.json`, solo
  España) con fallback a geocodificación inversa Nominatim.
- **Geocodificador** con caché en SQLite (`geocode_cache`) por celda
  de ~1 km, con LRU (50k entradas, TTL 365 días) y rate-limit de 1,1 s.
- **Clima histórico** con caché en SQLite (`WeatherCache`). Usa ERA5 Archive
  para fechas con más de 6 días de antigüedad y el endpoint Forecast con
  `past_days` para fechas recientes (timeout 10 s + 1 reintento).
- **Cálculo de dificultad 0–10**: desnivel (55 %), distancia (35 %),
  intensidad m/km (5 %) y tiempo en movimiento (5 %).
- **Agrupación de rutas únicas** por trailhead (< 150 m, haversine) y
  altitud máxima (< 50 m de diferencia), materializada en `route_cluster_id`
  al importar (`assign_cluster_for_new`); la vista agrupa con `GROUP BY`.
- **Caché en memoria** para la vista Análisis (`app/analisis_cache.py`,
  clave por usuario + rango): se invalida solo el usuario afectado tras
  cualquier escritura (importar, renombrar, borrar, reprocesar, backfill).
- **Autenticación multiusuario** con sesiones server-side (Argon2id +
  cookies HttpOnly), CSRF mediante HMAC-SHA256 y rate limiting en `/login`
  (ventana de 15 min por IP). Pool cerrado: alta de usuarios con
  `scripts/create_user.py`. Cada usuario solo ve y modifica sus rutas.
  Restablecer la contraseña revoca todas las sesiones del usuario.
- **Mantenimiento** vía endpoints autenticados (streaming NDJSON salvo el
  último):
  - `/api/reprocesar` — reprocesa todas las rutas desde los GPX en disco.
  - `/api/backfill-regions` — completa zonas faltantes con geocodificación.
  - `/api/limpiar-duplicados` — detecta y elimina GPX con contenido idéntico
    (JSON, admite `?dry_run=true`).
- **SQLite** (un único archivo en `data/mendi.db`). Única base de datos
  soportada; `MENDI_DATABASE_URL` existe pero otros dialectos no tienen
  migraciones ni validación específica.

## Cómo arrancarlo

### Windows

Doble clic en `run.bat`. La primera vez creará el entorno virtual e instalará
las dependencias; las siguientes veces arrancará en segundos.

### Linux / Mac

```bash
chmod +x run.sh
./run.sh
```

Cuando veas el mensaje, abre **http://127.0.0.1:8000** en tu navegador.

### Manualmente

```bash
python -m venv .venv
.venv\Scripts\activate         # Windows
source .venv/bin/activate      # Linux / Mac
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload --workers 1
```

> **Importante: usa siempre `--workers 1`.** La vista *Análisis* mantiene un
> caché en memoria del proceso (`app/analisis_cache.py:_CACHE`) que se invalida
> en cada escritura. Con varios workers cada uno tendrá su propio caché y verás
> agregaciones obsoletas tras importar/renombrar/borrar rutas.

## Primer uso

La base de datos arranca **vacía** y sin usuarios. Para crear el primer
usuario y poder entrar a la app:

```bash
python scripts/create_user.py --email tu@correo.es --role admin
```

El script pedirá la contraseña por consola (sin eco, mín. 12 caracteres), la
hashea con Argon2id y guarda el registro en `users`. El **primer usuario** que
se crea (o el primero que existe al arrancar la app) se promociona
automáticamente a rol `admin`: a partir de ese momento puede gestionar
el resto desde `/admin/usuarios` sin tocar la línea de comandos.

Si quieres forzar el rol al crear desde consola:

```bash
python scripts/create_user.py --email otro@correo.es --role viewer
python scripts/create_user.py --email x@y.es --name "Nombre" --update  # actualizar existente
```

Roles válidos: `admin`, `user`, `viewer` (por defecto `user`).

Después arranca el servidor, abre la app y entra con tus credenciales
en `/login`. Ve a **Importar** en la barra lateral, arrastra tus archivos
GPX y pulsa "Importar". Las rutas quedarán asociadas a tu usuario; cada
usuario solo ve y gestiona las suyas.

## Tests

Suite de tests unitarios puros (no necesita BD ni servidor):

```bash
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m pytest tests/test_difficulty.py -q   # un fichero
.venv/Scripts/python.exe -m pytest -q -k "nombre_parcial"        # un test
```

~64 tests en 10 ficheros (`difficulty`, `gpx_parser`, `name_cleaner`,
`geo_canonical`, `analisis`, `analisis_payload`, `detail_time`, `tz`,
`security_hardening`, `css_audit`). Nota: `scripts/audit_css.py` es un
esqueleto (`audit()`/`main()` lanzan `NotImplementedError`); el CSS se valida
con `pytest tests/test_css_audit.py`, no como CLI.

## Autenticación

- Sesiones server-side en la tabla `user_sessions`. El token (256 bits,
  generado con `secrets.token_urlsafe`, PK de la fila) se guarda en una cookie
  `mendi_session` con `HttpOnly` + `SameSite=Lax`. Si arrancas detrás de
  HTTPS define `MENDI_REQUIRE_HTTPS=1` para añadir `Secure`. La fila guarda
  además `ip`, `user_agent` y `revoked`.
- Hash de contraseñas con **Argon2id** (`argon2-cffi`, parámetros OWASP
  2024: `time_cost=3`, `memory_cost=64 MiB`, `parallelism=4`).
- Verificación dummy contra un hash fijo cuando el correo no existe →
  timing constante; un atacante no puede enumerar usuarios.
- **CSRF**: token HMAC-SHA256 del token de sesión usando `MENDI_SECRET_KEY`.
  El backend lo expone en cada render vía `<meta name="csrf-token">` y
  un script global lo inyecta como cabecera `X-CSRF-Token` en todas las
  peticiones HTMX y `fetch()` no-GET (los formularios usan campo oculto
  `csrf_token`). Verificación con `hmac.compare_digest`. Todas las
  mutaciones lo exigen, incluido `POST /logout`.
- **Rate limiting** en `/login`: ventana de 15 minutos por IP
  (contador en memoria, suficiente para `--workers 1`); los
  logins correctos limpian el contador.
- Las cookies expiran a los 30 días (90 si marcas "recordarme").
- Cierre de sesión vía `POST /logout` (con CSRF): revoca la sesión en BD y
  borra la cookie.

> ⚠️ **Define siempre `MENDI_SECRET_KEY` en producción.** El fallback de
> desarrollo se imprime con un aviso en logs y no debe usarse fuera de
> tu máquina. Genéralo así (32 bytes random base64):
> `python -c "import secrets; print(secrets.token_urlsafe(48))"`

## Roles y permisos

Cada usuario tiene uno de tres roles, almacenados en la columna
`users.role`. Los permisos se aplican en el backend con dependencias
FastAPI (`require_admin`, `require_writer`) y en el frontend ocultando
acciones que el rol no puede usar. **La fuente de verdad es siempre el
backend** — ocultar un botón sin proteger su endpoint no protegería nada.

Las dependencies se definen en `app/auth.py` con una factory
`require_role(*allowed)` que devuelve `403` si el rol del usuario no
está en el conjunto permitido. Atajos semánticos:

- `require_writer` → permite `admin` y `user` (cualquier rol con escritura).
- `require_admin` → solo `admin`.

Las **páginas** restringidas por rol (`/importar`, `/admin/usuarios`)
devuelven `403 Forbidden` — el usuario sabe que la página existe pero no
tiene acceso. En cambio, los endpoints de **datos de rutas** devuelven
`404` cuando la ruta pertenece a otro usuario, para no revelar su
existencia.

| Acción                                     | admin | user | viewer |
|--------------------------------------------|:-----:|:----:|:------:|
| Ver Resumen / Rutas / Análisis / Detalle   |  ✓   |  ✓  |   ✓   |
| Importar GPX                                |  ✓   |  ✓  |   —   |
| Renombrar, editar notas y tags              |  ✓   |  ✓  |   —   |
| Eliminar rutas / GPX                        |  ✓   |  ✓  |   —   |
| Limpiar duplicados / reprocesar             |  ✓   |  ✓  |   —   |
| Gestionar cimas                             |  ✓   |  ✓  |   —   |
| Gestionar usuarios (`/admin/usuarios`)      |  ✓   |  —  |   —   |

**Invariantes duros** del módulo de usuarios (todos devuelven `409` con
mensaje en español si se violan):

1. Siempre debe quedar **≥ 1 admin activo**.
2. Un admin **no puede auto-eliminarse**.
3. El último admin activo **no puede degradarse** ni desactivarse.

La gestión vive en `/admin/usuarios` (solo visible para `admin`). Permite
crear nuevos usuarios, restablecer contraseñas (revoca sus sesiones),
cambiar rol y nombre inline, activar/desactivar y eliminar. El borrado
limpia en cascada las rutas y los `track_points` del usuario
(`ON DELETE CASCADE`) y borra su carpeta de GPX en disco
(`data/gpx/<user_id>/`).

## Estructura del proyecto

```
mendi-log/
├── app/
│   ├── main.py            ← rutas FastAPI + middlewares + rate-limit + límites subida
│   ├── models.py          ← User + UserSession + Route + TrackPoint + Summit + WeatherCache + GeocodeCache
│   ├── db.py              ← engine + sesiones + commit() + init_db() + migraciones suaves
│   ├── auth.py            ← Argon2id, sesiones, CSRF HMAC, current_user, RBAC
│   ├── queries.py         ← helpers user_routes / user_route_get_or_404 acotados al usuario
│   ├── clustering.py      ← assign_cluster_for_new() + backfill por usuario
│   ├── gpx_parser.py      ← parser GPX, perfiles SVG, downsampling por distancia, waypoints
│   ├── difficulty.py      ← fórmula de dificultad 0–10
│   ├── name_cleaner.py    ← limpieza de nombres + detección de región (data/regions.json)
│   ├── text_utils.py      ← canonical_geo() idempotente (minúsculas sin diacríticos)
│   ├── geocoder.py        ← Nominatim con caché SQLite + rate-limit 1,1 s
│   ├── weather.py         ← clima Open-Meteo (Archive/Forecast) con caché SQLite
│   ├── summits.py         ← cimas: waypoints → Overpass → punto máximo
│   ├── tz.py              ← zona IANA por ruta (timezonefinder) + fecha/hora local
│   ├── importer.py        ← pipeline de importación de un GPX (sin commit)
│   ├── maintenance.py     ← reprocesar, backfill de regiones, limpiar duplicados (sin commit)
│   ├── format.py          ← formateo es-ES (km, duración, fechas, dificultad)
│   ├── stats.py           ← agregaciones para Resumen y Rutas (fecha local)
│   ├── analisis.py        ← agregaciones para Análisis (fecha local)
│   ├── analisis_cache.py  ← caché en memoria por usuario para Análisis
│   └── detail.py          ← datos para la vista de detalle de una ruta
├── templates/
│   ├── base.html          ← layout (sidebar + topbar + CSRF meta + script + hx-boost #hx-root)
│   ├── login.html         ← página de inicio de sesión (standalone)
│   ├── resumen.html       ← vista principal
│   ├── rutas.html         ← listado de rutas
│   ├── analisis.html      ← vista de análisis
│   ├── detail.html        ← detalle de una ruta
│   ├── importar.html      ← drag & drop
│   └── admin_usuarios.html← gestión de usuarios
├── static/
│   ├── css/
│   │   ├── styles.css     ← estilos globales (resumen, rutas, importar, base)
│   │   ├── login.css      ← estilos de la página de login (reutiliza tokens)
│   │   ├── analisis.css   ← estilos de la vista análisis
│   │   └── detail.css     ← estilos de la vista detalle
│   ├── fonts/             ← Fraunces + IBM Plex (woff2, descargados offline)
│   ├── img/               ← favicon e icono de la app
│   ├── js/
│   │   ├── app.js         ← dispatcher MENDI_PAGES/MENDI_TEARDOWN + mapa resumen
│   │   ├── rutas.js       ← filtros, paginación y ordenación de rutas
│   │   ├── analisis.js    ← mapa de calor, donuts, scatter, comparador, chips
│   │   ├── detail.js      ← mapa detalle, perfil de elevación, gráfica de clima
│   │   ├── importar.js    ← streaming NDJSON de importación y duplicados
│   │   └── admin_usuarios.js ← CRUD de usuarios vía API
│   └── vendor/            ← Leaflet, leaflet-heat, markercluster, HTMX (sin build step)
├── scripts/
│   ├── download_assets.py ← descarga fuentes y libs para uso offline
│   ├── create_user.py     ← alta de usuarios (Argon2id, pool cerrado)
│   ├── audit_css.py       ← esqueleto de auditoría CSS (usar vía pytest, no como CLI)
├── data/                  ← estado local ignorado por Git (nunca commitear)
│   ├── mendi.db           ← SQLite (rutas, usuarios, sesiones, clima, geocodificación)
│   ├── gpx/{user_id}/     ← GPX originales, aislados por usuario
│   └── regions.json       ← mapa de palabras clave de región (opcional, con fallback embebido)
├── requirements.txt
├── run.bat / run.sh
└── README.md
```

## Endpoints

Todos los endpoints requieren sesión autenticada salvo los del bloque
**Públicos**. Todas las peticiones no-GET además exigen un token CSRF
válido en `X-CSRF-Token` (inyectado automáticamente por el script global
de `base.html`) o campo `csrf_token` en formularios.

### Públicos

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/login` | Página de inicio de sesión |
| POST | `/login` | Crea sesión y cookie (rate-limited por IP) |
| POST | `/logout` | Revoca la sesión y borra la cookie (con CSRF) |
| GET | `/static/*` | Recursos estáticos (CSS, JS, fuentes, vendor) |

### Páginas

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/` | Vista Resumen |
| GET | `/rutas` | Vista Rutas (metadatos + facets) |
| GET | `/rutas/{id}` | Vista Detalle de una ruta |
| GET | `/analisis` | Vista Análisis (acepta `?range=`, `?from=`, `?to=`) |
| GET | `/importar` | Formulario de importación (`user`/`admin`) |
| POST | `/importar` | Importación batch (`user`/`admin`) |
| POST | `/importar/stream` | Importación streaming NDJSON (`user`/`admin`) |
| GET | `/admin/usuarios` | Gestión de usuarios (`admin`) |

### API

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/api/rutas` | Listado paginado, filtrado y ordenado |
| GET | `/api/rutas/markers` | Marcadores del mapa (paginado) |
| GET | `/api/analisis` | Datos de análisis en JSON |
| GET | `/api/comparator/search` | Búsqueda de sesiones para el comparador |
| GET | `/api/rutas/{id}/track` | Track + hitos + perfil de elevación |
| GET | `/api/rutas/{id}/clima` | Clima histórico en la cima |
| PATCH | `/api/rutas/{id}/nombre` | Renombrar una ruta |
| PATCH | `/api/rutas/{id}/notas` | Actualizar notas y etiquetas |
| DELETE | `/api/rutas/{id}` | Eliminar una ruta |
| POST | `/rutas/{id}/renombrar` | Renombrar (form POST) |
| POST | `/rutas/{id}/eliminar` | Eliminar (form POST) |
| POST | `/api/rutas/{id}/summits` | Crear cima manual |
| PATCH | `/api/rutas/{id}/summits/{summit_id}` | Mover o renombrar cima |
| DELETE | `/api/rutas/{id}/summits/{summit_id}` | Eliminar cima |
| POST | `/api/reprocesar` | Reprocesar todas las rutas del usuario (streaming NDJSON) |
| POST | `/api/backfill-regions` | Completar zonas faltantes (streaming NDJSON) |
| POST | `/api/limpiar-duplicados` | Limpiar duplicados (`?dry_run=true` para simular) |
| GET | `/api/usuarios` | Listar usuarios (`admin`) |
| POST | `/api/usuarios` | Crear usuario (`admin`) |
| PATCH | `/api/usuarios/{id}/rol` | Cambiar rol (`admin`) |
| PATCH | `/api/usuarios/{id}/password` | Restablecer contraseña + revocar sesiones (`admin`) |
| PATCH | `/api/usuarios/{id}/nombre` | Cambiar nombre visible (`admin`) |
| PATCH | `/api/usuarios/{id}/activo` | Activar / desactivar (`admin`) |
| DELETE | `/api/usuarios/{id}` | Eliminar usuario + cascada (`admin`) |

Todos los endpoints `/api/*` y todos los `POST/PATCH/DELETE` resuelven
implícitamente el usuario actual desde la cookie de sesión y operan solo
sobre sus rutas. Acceder a una ruta de otro usuario devuelve **404** (no
403) para no revelar la existencia del recurso.

## Modelos de datos

### User

| Columna | Tipo | Descripción |
|---------|------|-------------|
| `id` | Integer PK | — |
| `email` | String UNIQUE | Identificador único de login |
| `password_hash` | String | Hash Argon2id (parámetros OWASP 2024) |
| `display_name` | String NULL | Nombre visible (editable por admin) |
| `is_active` | Integer | 1 activo / 0 desactivado |
| `role` | String(16) | `admin`, `user` o `viewer` |
| `created_at` | DateTime | — |
| `last_login_at` | DateTime NULL | Último acceso |

### UserSession

| Columna | Tipo | Descripción |
|---------|------|-------------|
| `id` | String(64) PK | Token opaco (256 bits, `secrets.token_urlsafe(32)`) |
| `user_id` | Integer FK | → `users.id` |
| `created_at` | DateTime | — |
| `expires_at` | DateTime | +30 días (90 con "recordarme") |
| `ip` | String(64) NULL | IP de creación |
| `user_agent` | String(256) NULL | User-Agent de creación |
| `revoked` | Integer | 1 revocada |

### Route

| Columna | Tipo | Descripción |
|---------|------|-------------|
| `id` | Integer PK | — |
| `user_id` | Integer FK | → `users.id`. Toda query se filtra por aquí. |
| `name` | String | Nombre limpio (editable) |
| `name_original` | String | Nombre tal cual venía en el GPX |
| `country` | String | ej. "españa", "francia" (canónico) |
| `region` | String | ej. "navarra", "huesca" (canónico) |
| `sub_region` | String | ej. "pirineo aragones" (canónico) |
| `started_at` | DateTime | Primer trkpt con timestamp (UTC naive) |
| `start_lat/lon` | Float | Coordenadas del trailhead |
| `distance_km` | Float | Distancia 2D |
| `elevation_gain_m` | Integer | Desnivel positivo acumulado |
| `elevation_loss_m` | Integer | Desnivel negativo acumulado |
| `moving_time_s` | Integer | Tiempo en movimiento (s) |
| `total_time_s` | Integer | Tiempo total (s) |
| `max/min_altitude_m` | Integer | Altitudes extremas |
| `difficulty_score` | Float | 0–10 |
| `difficulty_level` | String | easy / moderate / hard / very-hard |
| `elev_line_path` | Text | SVG path viewBox 800×200 (línea) |
| `elev_area_path` | Text | SVG path viewBox 800×200 (área) |
| `notes` | Text | Notas libres (editable) |
| `tags` | Text | JSON: lista de strings (editable) |
| `gpx_filename` | String | Nombre del archivo en `data/gpx/{user_id}/` |
| `gpx_sha256` | String(64) | Hash; único por usuario (`UNIQUE(user_id, gpx_sha256)`) |
| `route_cluster_id` | Integer | Clave estable de cluster (calculado al importar) |
| `timezone` | String(64) NULL | Zona IANA del trailhead (`Europe/Madrid`…; NULL = UTC) |
| `created_at/updated_at` | DateTime | — |

### TrackPoint

Puntos downsampleados (máx. 400) para renderizar la polilínea en el mapa.
Columnas: `route_id`, `seq`, `lat`, `lon`, `elevation_m`, `time`.

### Summit

Cimas de la ruta: `route_id`, `seq`, `lat`, `lon`, `elevation_m`,
`name` (NULL si no disponible) y `source` (`wpt` | `overpass` | `fallback`
| `manual`).

### WeatherCache

Caché de respuestas Open-Meteo por `(lat, lon, date_iso)` (índice único).
Lat/lon redondeados a 2 decimales (~1 km) para reutilizar peticiones.
Columnas: `lat`, `lon`, `date_iso`, `payload`, `fetched_at`.

### GeocodeCache

Caché de Nominatim por celda (`cell_key` único `"lat,lon"` a 2 decimales),
con `country`, `region`, `sub_region`, `fetched_at` y `accessed_at` (LRU).

## Notas técnicas

### Pipeline de importación

```
UploadFile (GPX bytes, máx. 25 MB/archivo, 50 archivos y 200 MB por lote)
  → SHA-256 dedup check (UNIQUE por usuario)
  → gpx_parser.parse_gpx()        → GpxStats
  → name_cleaner.clean_name()     → nombre limpio
  → name_cleaner.detect_region()  → country, region, sub_region
  → geocoder.reverse_geocode()    → fallback si no hay match por keyword
  → canonical_geo()               → minúsculas sin diacríticos (idempotente)
  → difficulty.difficulty_score() → float 0–10
  → tz.resolve_timezone()         → zona IANA del trailhead
  → clustering.assign_cluster_for_new() → route_cluster_id (mismo usuario)
  → summits.fetch_summits()       → waypoints → Overpass → punto máximo
  → Route + TrackPoint + Summit → db.flush() (sin commit)
  → commit (por archivo en stream, al final en batch)
  → commit(db, invalidate_analisis=True, user_id=...)  (caché solo de ese usuario)
```

El nombre de fichero se sanea a basename y se valida con
`resolve().relative_to()` antes de escribir en `data/gpx/<user_id>/`.
Nominatim, Overpass y Open-Meteo usan httpx con timeout y fallback: una
importación nunca falla por ellos.

### Fórmula de dificultad

```
score = 0.35 × norm(distancia, 0–25 km)
      + 0.55 × norm(desnivel+, 0–2200 m)
      + 0.05 × norm(intensidad m/km, 0–150)
      + 0.05 × norm(tiempo_mov, 0–10 h)
```

Niveles: easy < 4, moderate 4–6, hard 6–8, very-hard ≥ 8.

### Agrupación de rutas únicas (clustering)

Dos sesiones son la misma ruta única si el trailhead dista < 150 m
(haversine) **y** la altitud máxima difiere < 50 m. El `route_cluster_id` se
materializa al importar con `assign_cluster_for_new()` (O(n) sobre las rutas
del mismo usuario); la vista Análisis agrupa con `GROUP BY` y el reprocesado
recalcula por usuario (`backfill_clusters_for_user`).

Lo que **no** distingue: dos rutas que comparten párking y cumbre pero suben
por vertientes distintas. En la práctica esto es raro y suele ser lo que el
usuario considera "la misma ruta".

### Zona horaria por ruta

La BD guarda UTC naive. Cada ruta persiste su zona IANA (`Route.timezone`,
resuelta offline con `timezonefinder` al importar, con backfill automático
en `init_db()` para rutas antiguas). Horas y fechas se muestran en hora
local, y las agrupaciones por día/mes (Resumen, Rutas, Análisis, clima)
usan el día local (`app/tz.py:local_date`). Sin zona, degradan a UTC.

### Caché de análisis

`analisis_cache.py` mantiene un dict en memoria protegido por
`threading.Lock`. La clave es `(user_id, range_key, from_date, to_date)`
(normalizada: rango en minúsculas, fechas solo en custom), de modo que cada
usuario ve su propio caché. Se invalida solo la entrada del usuario afectado
tras cualquier escritura que cambie sus agregaciones: importar, renombrar,
borrar, reprocesar, backfill, limpiar duplicados (`commit(db,
invalidate_analisis=True, user_id=...)`; alias local `_commit(db,
invalidate=..., user_id=...)` en `main.py`).

### Aislamiento por usuario

- `routes.user_id` FK obligatoria. Todas las queries pasan por
  `app/queries.py` (`user_routes(db, user_id)`, `user_route_get_or_404(db,
  user_id, id)`). Si una ruta no pertenece al usuario, la API responde
  404 — nunca 403 — para no filtrar la existencia del recurso.
- `UNIQUE(user_id, gpx_sha256)`: dos usuarios pueden importar el mismo
  GPX, pero un mismo usuario no puede duplicarlo.
- Archivos GPX en `data/gpx/{user_id}/` (`user_gpx_dir(user_id)`),
  creados con `parents=True, exist_ok=True` en la primera importación.
- Clustering y caché de Análisis por usuario: cada usuario tiene su propio
  universo.

### Middleware de caché HTTP

`CacheHeadersMiddleware` y `AuthGuardMiddleware` son ASGI puros a propósito
(`BaseHTTPMiddleware` consume el cuerpo y rompería el streaming NDJSON).
Política (`CacheHeadersMiddleware`) más cabeceras de seguridad
(`AuthGuardMiddleware`: `nosniff`, `DENY`, `same-origin`, CSP parcial):

- `/static/*` → `public, max-age=31536000, immutable`
- `/api/*` → `no-store`
- resto → `no-cache`

### Geocodificador

`geocoder.py` usa Nominatim con caché en SQLite (`geocode_cache`) por celda
de ~1 km (lat/lon redondeados a 2 decimales), LRU de 50k entradas con TTL de
365 días y rate-limit de 1,1 s. Migra una vez la antigua
`data/geocode_cache.json` si existe. Falla silencioso ante errores de
red — la importación nunca se rompe por falta de geocodificación.

### Clima histórico

`weather.py` usa Open-Meteo. Para fechas con más de 6 días de antigüedad
usa el endpoint ERA5 Archive; para fechas recientes usa Forecast con
`past_days`. Caché en la tabla `WeatherCache` de SQLite, con lat/lon
redondeados a 2 decimales y fecha **local** de la ruta. Si ambos endpoints
fallan, la API devuelve `{"available": false}`.

### Evolución del esquema (sin herramienta de migraciones)

`init_db()` llama a `Base.metadata.create_all()` y después aplica pasos
idempotentes (solo SQLite; en otros dialectos no hacen nada):

- `_ensure_user_columns()` — añade `users.role`.
- `_ensure_route_columns()` — añade `routes.timezone`.
- `_normalize_geo_columns()` — canonicaliza `country/region/sub_region`.
- `_purge_weather_cache()` + `_backfill_route_timezones()` — migración de
  fecha UTC a local y zonas por ruta (una vez).
- `_bootstrap_first_admin()` — promueve al usuario más antiguo si no hay
  admin activo.

Además `data/` crea `gpx/` al arrancar, y SQLite usa `WAL` + `foreign_keys=ON`
+ `busy_timeout=5000`.

### Downsampling del track

`gpx_parser._downsample_track()` muestrea por distancia acumulada
(equidistante en km), no por índice. Esto evita sobrerrepresentar zonas con
muchos puntos juntos (paradas, GPS lento) y produce una polilínea más fiel
a la geometría real. Máximo 400 puntos por ruta.

### Frontend (HTMX, sin build)

- `base.html` navega con `hx-boost` y `hx-target/select="#hx-root"`: solo se
  intercambia el contenedor principal.
- La hidratación de cada página (`scripts_extra` con JSON `MENDI_*`) va
  **dentro** de `#hx-root` o el swap la descarta.
- Cada página registra `window.MENDI_PAGES[<page>].init()` y limpia
  mapas/listeners/timers/observers vía `window.MENDI_TEARDOWN`.
- Solo assets locales de `static/vendor/` (Leaflet, heat, markercluster,
  HTMX); nada de CDN ni build. Tema en `localStorage`, sidebar drawer en
  móvil.

## Dependencias

```
fastapi==0.136.1
uvicorn[standard]==0.46.0
jinja2==3.1.4
sqlalchemy==2.0.36
gpxpy==1.6.2
python-multipart==0.0.28
httpx==0.27.2
pydantic==2.13.5
argon2-cffi==25.1.0
hypothesis==6.152.7
pytest==9.0.3
timezonefinder==9.0.0
tzdata==2026.4
```

CSRF y HMAC se resuelven con la stdlib (`hmac`, `secrets`). El rate
limiting de `/login` es un contador en memoria embebido en `app/main.py`,
sin Redis ni dependencias adicionales.

Frontend: Leaflet, leaflet-heat, markercluster, HTMX — sin build step,
servidos desde `static/vendor/`. Fuentes Fraunces + IBM Plex en
`static/fonts/` (descargadas con `scripts/download_assets.py` para uso
offline).

## Variables de entorno

El proceso **no** autocarga ningún `.env`; define las variables en el
entorno de ejecución (ver `.env.example` como plantilla).

| Variable | Por defecto | Descripción |
|----------|-------------|-------------|
| `MENDI_DATABASE_URL` | `sqlite:///data/mendi.db` | URL de conexión a la BD. Solo SQLite está soportado (migraciones y PRAGMAs específicos). |
| `MENDI_SECRET_KEY` | *(dev-only fallback)* | **Obligatoria en producción.** Clave usada para firmar tokens CSRF con HMAC-SHA256. Genera 32–48 bytes random (`python -c "import secrets; print(secrets.token_urlsafe(48))"`). Si no se define, se usa un valor de desarrollo y se imprime un aviso en logs. |
| `MENDI_REQUIRE_HTTPS` | `0` | Si vale `1`, la cookie de sesión se emite con el flag `Secure` (solo se envía por HTTPS). Actívalo cuando despliegues tras un proxy TLS. |

## Desarrollo (notas para agentes)

Ver `AGENTS.md`: entry point `app.main:app`, siempre `--workers 1`,
`process_gpx()` no commitea (decide el handler), queries acotadas vía
`app/queries.py` (404, nunca 403), mutaciones con `require_csrf` + rol,
`canonical_geo()` para todo geo nuevo, middlewares ASGI puros y reglas de
frontend HTMX.
