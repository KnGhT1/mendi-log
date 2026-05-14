# mendi.log

Aplicación web local para llevar un registro de tus rutas de montaña a partir
de archivos GPX (Wikiloc, Strava, etc.). 100 % local, 100 % tuyo.

La estética es fiel al mockup `resumen-mockup.html`: paleta nocturna con
acentos verde-musgo, tipografías Fraunces + IBM Plex, mapa Leaflet con teselas
de CartoDB y siluetas de elevación rotando en el hero.

## Características

- **Importación de GPX** drag & drop, multi-archivo. Calcula distancia, desnivel
  positivo y negativo, tiempo en movimiento, altitudes y un perfil SVG listo
  para mostrar como silueta del hero.
- **Streaming de importación** vía NDJSON (`/importar/stream`): feedback en
  tiempo real por archivo (leyendo → parseando → geocodificando → guardando).
  Commit por archivo para preservar progreso parcial.
- **Deduplicación SHA-256**: el mismo GPX importado dos veces se detecta y
  se ignora automáticamente.
- **Vista Resumen** (`/`): hero animado, 6 KPIs, mapa con marcadores
  proporcionales a la distancia, gráfica de kilómetros por mes (14 meses),
  donut de dificultad, heatmap calendario y tabla de rutas recientes.
- **Vista Rutas** (`/rutas`): listado paginado con filtros server-side
  (texto libre, dificultad, región, país, distancia, desnivel, fecha) y
  ordenación múltiple. Servido vía `/api/rutas`.
- **Vista Análisis** (`/analisis`): filtro por rango temporal (todo, temporada
  astronómica, año actual, últimos 12 meses, rango personalizado), hero stats,
  mapa de calor, zonas, ratio repetidas/nuevas, racha semanal, descubrimiento
  mensual, top-10 rutas, dormidas, evolución mensual, donuts de dificultad y
  distancia, scatter km vs desnivel, récords personales, calendario heatmap y
  comparador de perfiles de elevación.
- **Vista Detalle** (`/rutas/{id}`): mapa con track y hitos (salida, cima,
  llegada), perfil de elevación interactivo, datos técnicos (ritmo subida/
  bajada, VAM, pendiente media/máxima, tramo más duro, índice de fatiga),
  clima histórico en la cima (Open-Meteo ERA5), notas y etiquetas editables,
  rutas relacionadas y navegación anterior/siguiente.
- **Tema oscuro / claro** persistido en `localStorage`.
- **Limpieza automática de nombres** (quita emojis y prefijos tipo "Wikiloc")
  con edición manual en línea desde la tabla de rutas y la vista de detalle.
- **Detección de región** por palabras clave (navarra, pirineo aragonés,
  guipúzcoa, etc.) con fallback a geocodificación inversa Nominatim.
- **Geocodificador** con caché en disco (`data/geocode_cache.json`) por celda
  de ~1 km, respetando el rate-limit de 1 req/s de Nominatim.
- **Clima histórico** con caché en SQLite (`WeatherCache`). Usa ERA5 archive
  para fechas con más de 6 días de antigüedad y el endpoint Forecast para
  fechas recientes.
- **Cálculo de dificultad 0–10**: desnivel (55 %), distancia (35 %),
  intensidad m/km (5 %) y tiempo en movimiento (5 %).
- **Agrupación de rutas únicas** por similitud de trailhead (haversine < 150 m)
  y altitud máxima (< 50 m de diferencia), usando union-find.
- **Caché en memoria** para la vista Análisis: se invalida automáticamente
  tras cualquier escritura (importar, renombrar, borrar, reprocesar, backfill).
- **Autenticación multiusuario** con sesiones server-side (Argon2id +
  cookies HttpOnly), CSRF mediante HMAC-SHA256 y rate limiting en `/login`
  (5 intentos / 15 min por IP). Pool cerrado: alta de usuarios con
  `scripts/create_user.py`. Cada usuario solo ve y modifica sus rutas.
- **Mantenimiento** vía endpoints autenticados:
  - `/api/reprocesar` — reprocesa todas las rutas desde los GPX en disco.
  - `/api/backfill-regions` — completa zonas faltantes con geocodificación.
  - `/api/limpiar-duplicados` — detecta y elimina GPX con contenido idéntico.
- **SQLite** por defecto (un único archivo en `data/mendi.db`).
  Puedes cambiar la URL con la variable de entorno `MENDI_DATABASE_URL` si
  prefieres MariaDB o PostgreSQL.

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
> caché en memoria del proceso (`app/analisis.py:_CACHE`) que se invalida en
> cada escritura. Con varios workers cada uno tendrá su propio caché y verás
> agregaciones obsoletas tras importar/renombrar/borrar rutas.

## Primer uso

La base de datos arranca **vacía** y sin usuarios. Para crear el primer
usuario y poder entrar a la app:

```bash
python scripts/create_user.py --email tu@correo.es
```

El script pedirá la contraseña por consola (sin eco), la hashea con
Argon2id y guarda el registro en `users`. El **primer usuario** que se
crea (o el primero que existe al arrancar la app) se promociona
automáticamente a rol `admin`: a partir de ese momento puede gestionar
el resto desde `/admin/usuarios` sin tocar la línea de comandos.

Si quieres forzar el rol al crear desde consola:

```bash
python scripts/create_user.py --email otro@correo.es --role viewer
```

Roles válidos: `admin`, `user`, `viewer` (por defecto `user`).

Después arranca el servidor, abre la app y entra con tus credenciales
en `/login`. Ve a **Importar** en la barra lateral, arrastra tus archivos
GPX y pulsa "Importar". Las rutas quedarán asociadas a tu usuario; cada
usuario solo ve y gestiona las suyas.

## Autenticación

- Sesiones server-side en la tabla `user_sessions`. El token (256 bits,
  generado con `secrets.token_urlsafe`) se guarda en una cookie
  `mendi_session` con `HttpOnly` + `SameSite=Lax`. Si arrancas detrás de
  HTTPS define `MENDI_REQUIRE_HTTPS=1` para añadir `Secure`.
- Hash de contraseñas con **Argon2id** (`argon2-cffi`, parámetros OWASP
  2024: `time_cost=3`, `memory_cost=64 MiB`, `parallelism=4`).
- Verificación dummy contra un hash fijo cuando el correo no existe →
  timing constante; un atacante no puede enumerar usuarios.
- **CSRF**: token HMAC-SHA256 del token de sesión usando `MENDI_SECRET_KEY`.
  El backend lo expone en cada render vía `<meta name="csrf-token">` y
  un script global lo inyecta como cabecera `X-CSRF-Token` en todas las
  peticiones HTMX y `fetch()` no-GET. Verificación con
  `hmac.compare_digest`.
- **Rate limiting** en `/login`: 5 intentos por IP en una ventana de
  15 minutos (contador en memoria, suficiente para `--workers 1`).
- Las cookies expiran a los 30 días (90 si marcas "recordarme").
- Cierre de sesión vía `POST /logout`: revoca la sesión en BD y borra
  la cookie.

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
| Gestionar usuarios (`/admin/usuarios`)      |  ✓   |  —  |   —   |

**Invariantes duros** del módulo de usuarios (todos devuelven `409` con
mensaje en español si se violan):

1. Siempre debe quedar **≥ 1 admin activo**.
2. Un admin **no puede auto-eliminarse**.
3. El último admin activo **no puede degradarse** ni desactivarse.

La gestión vive en `/admin/usuarios` (solo visible para `admin`). Permite
crear nuevos usuarios, restablecer contraseñas, cambiar rol inline,
activar/desactivar y eliminar. El borrado limpia en cascada las rutas y
los `track_points` del usuario (`ON DELETE CASCADE`) y borra su carpeta
de GPX en disco (`data/gpx/<user_id>/`).

## Estructura del proyecto

```
mendi-log/
├── app/
│   ├── main.py            ← rutas FastAPI + middleware de auth y caché
│   ├── models.py          ← Route + TrackPoint + WeatherCache + User + UserSession
│   ├── db.py              ← engine + sesiones + init_db() + migraciones suaves
│   ├── auth.py            ← Argon2id, sesiones, CSRF HMAC, dependency current_user
│   ├── queries.py         ← helpers user_routes / user_route_get acotados al usuario
│   ├── clustering.py      ← agrupación de sesiones en rutas únicas por usuario
│   ├── gpx_parser.py      ← parser GPX, perfiles SVG, downsampling por distancia
│   ├── difficulty.py      ← fórmula de dificultad 0–10
│   ├── name_cleaner.py    ← limpieza de nombres + detección de región
│   ├── geocoder.py        ← geocodificación inversa Nominatim con caché en disco
│   ├── weather.py         ← clima histórico Open-Meteo con caché en SQLite
│   ├── importer.py        ← pipeline de importación de un GPX (sin commit)
│   ├── maintenance.py     ← reprocesar, backfill de regiones, limpiar duplicados
│   ├── stats.py           ← agregaciones para Resumen y Rutas
│   ├── analisis.py        ← agregaciones para Análisis
│   ├── analisis_cache.py  ← caché en memoria por usuario para Análisis
│   └── detail.py          ← datos para la vista de detalle de una ruta
├── templates/
│   ├── base.html          ← layout (sidebar + topbar + CSRF meta + script)
│   ├── login.html         ← página de inicio de sesión (standalone)
│   ├── resumen.html       ← vista principal
│   ├── rutas.html         ← listado de rutas
│   ├── analisis.html      ← vista de análisis
│   ├── detail.html        ← detalle de una ruta
│   ├── importar.html      ← drag & drop
│   └── placeholder.html   ← stub para vistas futuras
├── static/
│   ├── css/
│   │   ├── styles.css     ← estilos globales (resumen, importar, base)
│   │   ├── login.css      ← estilos de la página de login (reutiliza tokens)
│   │   ├── analisis.css   ← estilos de la vista análisis
│   │   └── detail.css     ← estilos de la vista detalle
│   ├── fonts/             ← Fraunces + IBM Plex (woff2, descargados offline)
│   ├── img/               ← favicon e icono de la app
│   ├── js/
│   │   ├── app.js         ← mapa resumen, gráfica mensual, rotación hero, edición
│   │   ├── rutas.js       ← filtros, paginación y ordenación de rutas
│   │   ├── analisis.js    ← mapa de calor, donuts, scatter, comparador, chips
│   │   └── detail.js      ← mapa detalle, perfil de elevación, gráfica de clima
│   └── vendor/            ← Leaflet, leaflet-heat, HTMX (sin build step)
├── scripts/
│   ├── download_assets.py ← descarga fuentes y libs para uso offline
│   ├── create_user.py     ← alta de usuarios (Argon2id, pool cerrado)
│   └── _debug_keys.py     ← utilidad de depuración de claves de clustering
├── data/
│   ├── mendi.db           ← SQLite (rutas, usuarios, sesiones, clima)
│   ├── gpx/{user_id}/     ← GPX originales, aislados por usuario
│   └── geocode_cache.json ← caché Nominatim compartida (no PII)
├── requirements.txt
├── run.bat / run.sh
└── README.md
```

## Endpoints

Todos los endpoints requieren sesión autenticada salvo los del bloque
**Públicos**. Todas las peticiones no-GET además exigen un token CSRF
válido en `X-CSRF-Token` (inyectado automáticamente por el script global
de `base.html`).

### Públicos

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/login` | Página de inicio de sesión |
| POST | `/login` | Crea sesión y cookie (rate-limited 5/15 min) |
| POST | `/logout` | Revoca la sesión y borra la cookie |
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
| GET | `/api/analisis` | Datos de análisis en JSON |
| GET | `/api/rutas/{id}/track` | Track + hitos + perfil de elevación |
| GET | `/api/rutas/{id}/clima` | Clima histórico en la cima |
| PATCH | `/api/rutas/{id}/nombre` | Renombrar una ruta |
| PATCH | `/api/rutas/{id}/notas` | Actualizar notas y etiquetas |
| DELETE | `/api/rutas/{id}` | Eliminar una ruta |
| POST | `/rutas/{id}/renombrar` | Renombrar (form POST) |
| POST | `/rutas/{id}/eliminar` | Eliminar (form POST) |
| POST | `/api/reprocesar` | Reprocesar todas las rutas del usuario |
| POST | `/api/backfill-regions` | Completar zonas faltantes |
| POST | `/api/limpiar-duplicados` | Limpiar duplicados |
| GET | `/api/usuarios` | Listar usuarios (`admin`) |
| POST | `/api/usuarios` | Crear usuario (`admin`) |
| PATCH | `/api/usuarios/{id}/rol` | Cambiar rol (`admin`) |
| PATCH | `/api/usuarios/{id}/password` | Restablecer contraseña (`admin`) |
| PATCH | `/api/usuarios/{id}/activo` | Activar / desactivar (`admin`) |
| DELETE | `/api/usuarios/{id}` | Eliminar usuario + cascada (`admin`) |

Todos los endpoints `/api/*` y todos los `POST/PATCH/DELETE` resuelven
implícitamente el usuario actual desde la cookie de sesión y operan solo
sobre sus rutas. Acceder a una ruta de otro usuario devuelve **404** (no
403) para no revelar la existencia del recurso. Los endpoints de
mantenimiento dejan de tener filtro por localhost: la frontera de
seguridad es la sesión + CSRF.

## Modelos de datos

### User

| Columna | Tipo | Descripción |
|---------|------|-------------|
| `id` | Integer PK | — |
| `email` | String UNIQUE | Identificador único de login |
| `password_hash` | String | Hash Argon2id (parámetros OWASP 2024) |
| `created_at` | DateTime | — |

### UserSession

| Columna | Tipo | Descripción |
|---------|------|-------------|
| `id` | Integer PK | — |
| `user_id` | Integer FK | → `users.id` |
| `session_token` | String(64) UNIQUE | 256 bits, `secrets.token_urlsafe(32)` |
| `created_at` | DateTime | — |
| `expires_at` | DateTime | +30 días (90 con "recordarme") |

### Route

| Columna | Tipo | Descripción |
|---------|------|-------------|
| `id` | Integer PK | — |
| `user_id` | Integer FK | → `users.id`. Toda query se filtra por aquí. |
| `name` | String | Nombre limpio (editable) |
| `name_original` | String | Nombre tal cual venía en el GPX |
| `country` | String | ej. "españa", "francia" |
| `region` | String | ej. "navarra", "huesca" |
| `sub_region` | String | ej. "pirineo aragones" |
| `started_at` | DateTime | Primer trkpt con timestamp |
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
| `created_at/updated_at` | DateTime | — |

### TrackPoint

Puntos downsampleados (máx. 400) para renderizar la polilínea en el mapa.
Columnas: `route_id`, `seq`, `lat`, `lon`, `elevation_m`, `time`.

### WeatherCache

Caché de respuestas Open-Meteo por `(lat, lon, date_iso)`. Lat/lon
redondeados a 2 decimales (~1 km) para reutilizar peticiones.

## Notas técnicas

### Pipeline de importación

```
UploadFile (GPX bytes)
  → SHA-256 dedup check
  → gpx_parser.parse_gpx()        → GpxStats
  → name_cleaner.clean_name()     → nombre limpio
  → name_cleaner.detect_region()  → country, region, sub_region
  → geocoder.reverse_geocode()    → fallback si no hay match por keyword
  → difficulty.difficulty_score() → float 0–10
  → Route + TrackPoint → db.flush()
  → commit (por archivo en stream, al final en batch)
  → analisis_module.invalidate_analisis_cache()
```

### Fórmula de dificultad

```
score = 0.35 × norm(distancia, 0–25 km)
      + 0.55 × norm(desnivel+, 0–2200 m)
      + 0.05 × norm(intensidad m/km, 0–150)
      + 0.05 × norm(tiempo_mov, 0–10 h)
```

Niveles: easy < 4, moderate 4–6, hard 6–8, very-hard ≥ 8.

### Agrupación de rutas únicas (clustering)

La vista Análisis decide cuándo dos sesiones GPX representan "la misma ruta"
para contar repeticiones, dormidas, top-10, etc. Implementado en
`app/analisis.py` con union-find.

**Implementación actual**

Dos sesiones son la misma ruta si:
- Distancia haversine entre trailheads < 150 m (`SAME_ROUTE_TRAILHEAD_M`)
- Diferencia de altitud máxima < 50 m (`SAME_ROUTE_ALT_M`)

El union-find agrupa por componentes conexas, de modo que cadenas de rutas
próximas se fusionan correctamente aunque ningún par individual supere el
umbral. Complejidad O(n²) — negligible para los volúmenes habituales.

Lo que **no** distingue: dos rutas que comparten párking y cumbre pero suben
por vertientes distintas. En la práctica esto es raro y suele ser lo que el
usuario considera "la misma ruta".

**Futuro — comparación geométrica completa**

Cuando la opción actual empiece a mezclar rutas falsamente, la mejora natural
es comparar la geometría de la traza:

1. Simplificar con Douglas-Peucker (tolerancia ~10 m) → ~200 vértices.
2. Pre-filtrar por bounding-box solapado y diferencia de longitud < 20 %.
3. Calcular distancia de Fréchet (o Hausdorff) entre polilíneas. Si < ~80 m → misma ruta.
4. Guardar `route_cluster_id` en `Route`, calculado una vez al importar.

Dependencias razonables: `shapely` y/o `similaritymeasures`.

### Caché de análisis

`analisis_cache.py` mantiene un dict en memoria protegido por
`threading.Lock`. La clave incluye `user_id` además de `(range_key,
from_date, to_date)`, de modo que cada usuario ve su propio caché. Se
invalida solo la entrada del usuario afectado tras cualquier escritura
que cambie sus agregaciones: importar, renombrar, borrar, reprocesar,
backfill.

### Aislamiento por usuario

- `routes.user_id` FK obligatoria. Todas las queries pasan por
  `app/queries.py` (`user_routes(db, user_id)`, `user_route_get(db,
  user_id, id)`). Si una ruta no pertenece al usuario, la API responde
  404 — nunca 403 — para no filtrar la existencia del recurso.
- `UNIQUE(user_id, gpx_sha256)`: dos usuarios pueden importar el mismo
  GPX, pero un mismo usuario no puede duplicarlo.
- Archivos GPX en `data/gpx/{user_id}/` (`user_gpx_dir(user_id)`),
  creados con `parents=True, exist_ok=True` en la primera importación.
- Clustering por usuario: `route_cluster_id` se calcula tomando como
  universo solo las rutas del propio usuario.

### Middleware de caché HTTP

`CacheHeadersMiddleware` es ASGI puro (no `BaseHTTPMiddleware`) para no
romper el streaming NDJSON. Política:

- `/static/*` → `public, max-age=31536000, immutable`
- `/api/*` → `no-store`
- resto → `no-cache`

### Geocodificador

`geocoder.py` usa Nominatim con caché en disco (`data/geocode_cache.json`)
por celda de ~1.1 km (lat/lon redondeados a 2 decimales). Respeta el
rate-limit oficial de 1 req/s con un margen de 0.1 s. Falla silencioso ante
errores de red — la importación nunca se rompe por falta de geocodificación.

### Clima histórico

`weather.py` usa Open-Meteo. Para fechas con más de 6 días de antigüedad
usa el endpoint ERA5 Archive; para fechas recientes usa Forecast con
`past_days`. Caché en la tabla `WeatherCache` de SQLite, con lat/lon
redondeados a 2 decimales.

### Evolución del esquema (sin herramienta de migraciones)

`init_db()` llama a `Base.metadata.create_all()` y después a
`_ensure_route_columns()`, que añade columnas nuevas con `ALTER TABLE` de
forma no destructiva. Siempre comprueba `if "column_name" not in existing`
antes de alterar.

### Downsampling del track

`gpx_parser._downsample_track()` muestrea por distancia acumulada
(equidistante en km), no por índice. Esto evita sobrerrepresentar zonas con
muchos puntos juntos (paradas, GPS lento) y produce una polilínea más fiel
a la geometría real. Máximo 400 puntos por ruta.

## Dependencias

```
fastapi==0.115.0
uvicorn[standard]==0.32.0
jinja2==3.1.4
sqlalchemy==2.0.36
gpxpy==1.6.2
python-multipart==0.0.12
argon2-cffi==25.1.0
```

CSRF y HMAC se resuelven con la stdlib (`hmac`, `secrets`). El rate
limiting de `/login` es un contador en memoria embebido en `app/main.py`,
sin Redis ni dependencias adicionales.

Frontend: Leaflet, leaflet-heat, HTMX — sin build step, servidos desde
`static/vendor/`. Fuentes Fraunces + IBM Plex en `static/fonts/` (descargadas
con `scripts/download_assets.py` para uso offline).

## Variables de entorno

| Variable | Por defecto | Descripción |
|----------|-------------|-------------|
| `MENDI_DATABASE_URL` | `sqlite:///data/mendi.db` | URL de conexión a la BD. |
| `MENDI_SECRET_KEY` | *(dev-only fallback)* | **Obligatoria en producción.** Clave usada para firmar tokens CSRF con HMAC-SHA256. Genera 32–48 bytes random (`python -c "import secrets; print(secrets.token_urlsafe(48))"`). Si no se define, se usa un valor de desarrollo y se imprime un aviso en logs. |
| `MENDI_REQUIRE_HTTPS` | `0` | Si vale `1`, la cookie de sesión se emite con el flag `Secure` (solo se envía por HTTPS). Actívalo cuando despliegues tras un proxy TLS. |

## Próximos pasos previstos

- Comparación geométrica completa de rutas (opción D, ver arriba).
- Exportación de estadísticas (CSV, JSON).
- Soporte para waypoints y rutas planificadas (sin track grabado).
