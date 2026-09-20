# Changelog — mendi.log

Formato [Keep a Changelog](https://keepachangelog.com/es-ES/1.0.0/).
Versiones [SemVer](https://semver.org/lang/es/): `vMAYOR.MENOR.PARCHE`.

## [Unreleased]

## [1.0.0] — 2026-09-20

Primera publicación.

### Añadido
- Registro multiusuario de rutas de montaña desde GPX (Wikiloc, Strava…),
  con importación drag & drop batch y streaming NDJSON.
- Vistas Resumen, Rutas (filtros server-side), Análisis (rangos, mapa de
  calor, racha, top-10, comparador…) y Detalle (track, perfil, clima
  histórico Open-Meteo, notas y etiquetas).
- Cimas detectadas y editables; dificultad 0–10; agrupación de rutas únicas
  por trailhead y altitud; zona horaria IANA por ruta con fecha/hora local.
- Autenticación con sesiones server-side (Argon2id), CSRF HMAC-SHA256,
  rate-limit en login y roles `admin`/`user`/`viewer`.
- Caché en memoria para Análisis con invalidación por usuario.
- Mantenimiento: reprocesar, backfill de regiones y limpieza de duplicados.

### Seguridad
- Aislamiento por usuario (recurso ajeno → 404); invariantes de último
  admin; CSRF en todas las mutaciones; nombres GPX saneados; externos
  (Nominatim, Overpass, Open-Meteo) con timeout y fallback.
