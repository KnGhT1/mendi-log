# TASKS

## En progreso

## Pendiente

## Bugs

## Hecho

- [x] FEAT: Detección y persistencia de múltiples cimas por ruta vía Overpass API (OSM) — `app/summits.py`, `app/models.py`, `app/gpx_parser.py`
- [x] FEAT: Gestión manual de cimas desde la vista de detalle
  - Marcador ▲ arrastrable en el mapa para mover cimas existentes
  - Click en el mapa (modo pin) para añadir nuevas cimas
  - Popup con edición de nombre y botón eliminar
  - Perfil de elevación con múltiples marcadores ▲ rotados a 45°
  - Cursor del perfil sincronizado con posición en el mapa
  - Perfil integrado visualmente bajo el mapa en la vista de detalle
  - Endpoints REST: `POST/PATCH/DELETE /api/rutas/{id}/summits/{sid}` con auth completa
- [x] FEAT: Selector de capa de mapa en todos los mapas de la app
  - Control nativo Leaflet (`L.control.layers`) con estilos de la app
  - Capas: CartoDB Claro, CartoDB Voyager, OpenStreetMap, OpenTopoMap, Satélite ESRI
  - Aplicado en: mapa Resumen, mapa Rutas, mapa Detalle
  - Capa persistida en `localStorage`
- [x] FEAT: Vista mapa en /rutas con marcadores circulares, clustering y popup con perfil de elevación
- [x] FEAT: Filtros país+región concatenados en /rutas con combobox Select2 (búsqueda integrada, teclado, filtrado por país)
- [x] REFACTOR: Eliminar `_PROVINCE_TO_REGION` hardcodeado en geocoder — usar Nominatim directamente
- [x] FIX: Geocoder `database is locked` al reprocesar — pasar sesión del caller a `reverse_geocode`
- [x] FIX: Timeout Overpass reducido de 30s a 5s para no bloquear reprocesado
