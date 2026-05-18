# TASKS

## En progreso

- [ ] FEAT: Selector de capa de mapa en todos los mapas de la app
  - Control nativo Leaflet (`L.control.layers`) con estilos de la app
  - Capas disponibles: CartoDB Dark, CartoDB Light, CartoDB Voyager, OpenStreetMap, OpenTopoMap, Stadia Terrain, Stadia Toner
  - Aplicar en: mapa Resumen (`app.js`), mapa Rutas (`rutas.js`), mapa Detalle (`detail.js`)
  - Persistir la capa seleccionada en `localStorage`

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
