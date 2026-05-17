# TASKS

## En progreso

## Pendiente

- [ ] FEAT: Gestión manual de cimas desde la vista de detalle
  - Marcar cima: click en el track del mapa → crea Summit con source="manual"
  - Mover cima: arrastrar marcador existente → actualiza lat/lon en BD
  - El marcador ▲ en el perfil de elevación (`#summit-mark` en `detail.html`) se recalcula al mover/crear
  - Confirmar cima conocida: botón explícito "guardar como cima conocida" → persiste en tabla `KnownSummit`
  - Archivos afectados: `app/models.py` (KnownSummit), `app/main.py` (endpoints PATCH/POST),
    `app/detail.py` (leer summits), `templates/detail.html` (UI), `static/js/detail.js` (mapa + perfil),
    `app/summits.py` (consultar KnownSummit al importar)

## Bugs

## Hecho

- [x] FEAT: Detección y persistencia de múltiples cimas por ruta vía Overpass API (OSM) — `app/summits.py`, `app/models.py`, `app/gpx_parser.py`
