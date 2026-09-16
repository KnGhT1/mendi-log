# Arquitectura de mendi.log

## Alcance

mendi.log es una aplicacion web local para registrar rutas de montana a partir
de archivos GPX. El backend se ejecuta con Python 3.14, FastAPI y SQLAlchemy;
las paginas se renderizan en el servidor con Jinja2. El cliente usa JavaScript
sin framework, HTMX y Leaflet. No existe una cadena de compilacion frontend.

La aplicacion esta orientada a una persona, hogar o club pequeno. Los usuarios
se crean en un pool cerrado y cada uno solo puede acceder a sus propias rutas.

## Componentes

| Area | Responsabilidad |
| --- | --- |
| [`app/main.py`](../app/main.py) | App FastAPI, middleware y handlers HTTP. |
| [`app/auth.py`](../app/auth.py) | Contrasenas Argon2id, sesiones, CSRF y RBAC. |
| [`app/db.py`](../app/db.py) y [`app/models.py`](../app/models.py) | Engine, sesiones SQLAlchemy, esquema y bootstrap. |
| [`app/importer.py`](../app/importer.py) y [`app/gpx_parser.py`](../app/gpx_parser.py) | Pipeline GPX, metricas y perfiles de elevacion. |
| [`app/geocoder.py`](../app/geocoder.py), [`app/weather.py`](../app/weather.py) y [`app/summits.py`](../app/summits.py) | Integraciones de localizacion, clima y cimas. |
| [`app/stats.py`](../app/stats.py), [`app/analisis.py`](../app/analisis.py) y [`app/detail.py`](../app/detail.py) | Agregaciones y DTOs de las vistas. |
| [`templates/`](../templates) | Plantillas Jinja2. |
| [`static/`](../static) | CSS, JavaScript, fuentes y librerias locales. |
| [`scripts/`](../scripts) | Herramientas de alta de usuarios, assets y auditoria CSS. |

## Arranque y ciclo de solicitud

Los lanzadores [`run.bat`](../run.bat) y [`run.sh`](../run.sh) crean el entorno
virtual si es necesario, instalan dependencias y arrancan `uvicorn app.main:app`.
El lifespan de FastAPI llama a `init_db()` antes de aceptar solicitudes.

```text
Navegador
  -> middleware ASGI de cabeceras
  -> handler de app/main.py
  -> dependencia de sesion y usuario actual
  -> dependencia de rol, cuando corresponde
  -> modulo de dominio y SQLAlchemy
  -> HTML Jinja, JSON o stream NDJSON
```

Las paginas requieren sesion salvo login y recursos estaticos. Las mutaciones
requieren CSRF. Las rutas de datos que pertenecen a otro usuario devuelven 404
para no revelar su existencia.

## Importacion GPX

```text
UploadFile
  -> lectura de bytes
  -> SHA-256 y deduplicacion por usuario
  -> parse_gpx() y GpxStats
  -> clean_name() y detect_region()
  -> reverse_geocode() como fallback
  -> difficulty_score()
  -> Route + TrackPoint + Summit
  -> commit del handler e invalidacion de Analisis
```

La importacion normal confirma el lote al final. El endpoint streaming conserva
el progreso por fichero y emite una linea NDJSON por evento. Los originales se
guardan en `data/gpx/<user_id>/`; los puntos persistidos se downsamplean para
la visualizacion del mapa.

## Persistencia

SQLite es la implementacion soportada por defecto. El motor activa claves
foraneas, WAL, `busy_timeout` y `synchronous=NORMAL` para mejorar la
concurrencia local.

| Entidad | Proposito |
| --- | --- |
| `users` y `user_sessions` | Identidad, roles y sesiones revocables. |
| `routes` | Metadatos de la ruta, metricas, perfiles SVG y referencia GPX. |
| `track_points` y `summits` | Geometria reducida y cimas asociadas. |
| `weather_cache` y `geocode_cache` | Respuestas remotas cacheadas en SQLite. |

`Route` se aisla por `user_id`. La unicidad de contenido GPX es
`(user_id, gpx_sha256)`, por lo que usuarios distintos pueden importar el mismo
archivo. `route_cluster_id` materializa la agrupacion de sesiones equivalentes
por usuario.

`init_db()` crea tablas y contiene migraciones suaves limitadas. `create_all()`
no modifica tablas existentes: cualquier evolucion de esquema debe planearse
como una migracion idempotente y reproducible.

## Autorizacion y seguridad

- Las contrasenas usan Argon2id; las sesiones son tokens opacos guardados en
  base de datos y enviados en cookie `HttpOnly` con `SameSite=Lax`.
- CSRF deriva un HMAC de la sesion. El layout lo inyecta en HTMX y en `fetch()`.
- `admin`, `user` y `viewer` se aplican en dependencias FastAPI. La interfaz no
  es una frontera de seguridad.
- Debe existir al menos un administrador activo. Un administrador no puede
  eliminarse y el ultimo administrador no puede degradarse ni desactivarse.
- `MENDI_SECRET_KEY` es obligatoria fuera de desarrollo local. Con HTTPS debe
  configurarse `MENDI_REQUIRE_HTTPS=1`.

## Frontend y navegacion

`base.html` usa `hx-boost` y reemplaza `#hx-root` durante la navegacion. Los
modulos de pagina se registran en `window.MENDI_PAGES`; antes de cada swap el
dispatcher ejecuta las funciones de `window.MENDI_TEARDOWN`.

Los datos hidratados de cada vista deben vivir dentro de `#hx-root`. Los mapas
Leaflet, timers, observers y listeners globales deben registrar una funcion de
limpieza para no acumular recursos tras los swaps HTMX.

## Cache e integraciones

- Estaticos: cache fuerte e inmutable; APIs: `no-store`; HTML: `no-cache`.
- Analisis conserva una cache en memoria por usuario y rango. Se invalida tras
  escrituras que alteran agregaciones.
- Nominatim geocodifica de forma inversa, limita peticiones globalmente y usa
  `geocode_cache`. Un fallo no debe abortar una importacion.
- Open-Meteo proporciona clima historico y reciente con `weather_cache`.
- Overpass aporta cimas cuando el GPX no tiene waypoints utilizables; existe un
  fallback al punto de maxima altitud.

## Restricciones actuales

- El proceso debe usar un unico worker mientras la cache de Analisis sea local.
- El manejo de importaciones y algunas integraciones remotas es sincrono; no
  debe asumirse que el servidor escala horizontalmente.
- `MENDI_DATABASE_URL` permite una URL SQLAlchemy alternativa, pero PostgreSQL
  y MariaDB no se consideran soportados hasta disponer de drivers, migraciones
  y validacion especifica de esos dialectos.
- El mapa de regiones puede leerse desde `data/regions.json`; si no existe o es
  invalido, `name_cleaner.py` usa el fallback embebido.

## Documentos relacionados

- [`operations.md`](operations.md): ejecucion, configuracion y datos locales.
- [`adr/0001-single-worker-analysis-cache.md`](adr/0001-single-worker-analysis-cache.md): motivo del worker unico.
- [`adr/0002-sqlite-and-schema-evolution.md`](adr/0002-sqlite-and-schema-evolution.md): soporte de base de datos y evolucion del esquema.
- [`../AGENTS.md`](../AGENTS.md): instrucciones operativas para agentes.