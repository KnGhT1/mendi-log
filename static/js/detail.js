// ============================================================
// mendi.log · detalle de ruta
// ------------------------------------------------------------
// Datos hidratados desde Jinja en window.MENDI_DETAIL.
//
// Compatible con HTMX hx-boost: todo el setup vive en init(),
// invocada por el dispatcher de app.js cuando data-page="detail".
// El mapa Leaflet, los listeners en document y el MutationObserver
// del tema registran su teardown en window.MENDI_TEARDOWN.
// ============================================================
(function () {
  "use strict";

  window.MENDI_PAGES = window.MENDI_PAGES || {};
  window.MENDI_TEARDOWN = window.MENDI_TEARDOWN || [];

  const LEVEL_COLORS = {
    easy: "#7DAFC9",
    moderate: "#B5D17A",
    hard: "#E8B86D",
    "very-hard": "#E47862",
  };
  const levelColor = (lvl) => LEVEL_COLORS[lvl] || LEVEL_COLORS.moderate;

  // ============ utilidades ============
  /**
   * Escapa los caracteres especiales HTML de una cadena para interpolación
   * segura en innerHTML. Trata `null` y `undefined` como string vacío.
   * @param {*} s
   * @returns {string}
   */
  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
  }
  /**
   * Lee el valor de una CSS custom property del elemento raíz.
   * @param {string} name  Nombre de la variable, p.ej. `"--accent"`.
   * @returns {string}
   */
  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }
  /**
   * Devuelve `true` si el tema activo es `"light"`.
   * @returns {boolean}
   */
  function isLight() {
    return document.documentElement.getAttribute("data-theme") === "light";
  }
  /**
   * Formatea kilómetros con 2 decimales si `km < 10`, o 1 decimal en
   * caso contrario. Usa coma como separador decimal. Devuelve `"—"` si
   * el valor es nulo o no numérico.
   * @param {number} km
   * @returns {string}
   */
  function fmtKm(km) {
    if (km == null || isNaN(km)) return "—";
    return km.toFixed(km < 10 ? 2 : 1).replace(".", ",");
  }
  /**
   * Formatea un número entero con separador de miles según el locale
   * `es-ES`. Devuelve `"—"` si el valor es nulo o no numérico.
   * @param {number} n
   * @returns {string}
   */
  function fmtInt(n) {
    if (n == null || isNaN(n)) return "—";
    return Math.round(n).toLocaleString("es-ES");
  }

  function init() {
    const D = window.MENDI_DETAIL || {};
    // Leer el ID desde la URL para garantizar que es correcto tras swaps HTMX
    // (los <script> del swap pueden no re-ejecutarse antes de init())
    const urlMatch = window.location.pathname.match(/\/rutas\/(\d+)/);
    const ROUTE_ID = urlMatch ? parseInt(urlMatch[1], 10) : D.id;
    // Si el bloque de hidratación no se ejecutó (p.ej. swap parcial sin
    // datos), abortamos en silencio. Sin esto, fetch(`/api/rutas/undefined/...`)
    // dispara un 422 ruidoso en consola.
    if (ROUTE_ID == null) return;

    // ============ HORA LOCAL (Fase 1) ============
    // La BD guarda UTC naive y el backend lo etiqueta con `Z`. El clima
    // (hourly de Open-Meteo con timezone:auto) viene en wall-time local de
    // la ruta. `routeTz` es la zona para mostrar/ comparar: la del payload
    // de clima cuando llega, si no la del navegador.
    let routeTz = null;
    try {
      routeTz = Intl.DateTimeFormat().resolvedOptions().timeZone || null;
    } catch (_) { routeTz = null; }

    // ============ MAPA ============
    let detailMap = null;
    let detailMapLayerCtrl = null;
    let trackPolyline = null;
    let trackOutline = null;
    let trackPoints = [];  // copia del track para el cursor del perfil
    let trackCumKm = [];   // distancias acumuladas en km para cada punto
    // Marcadores de cima arrastrables: Map<summit_id, L.Marker>
    const summitMarkers = new Map();
    let addSummitMode = false;

    /**
     * Oculta el overlay de carga del mapa de detalle (`#detail-map-loading`)
     * con una transición CSS de 400 ms antes de retirar el nodo del flujo.
     */
    function hideMapLoading() {
      const ov = document.getElementById("detail-map-loading");
      if (!ov) return;
      ov.classList.add("hidden");
      setTimeout(() => { ov.style.display = "none"; }, 400);
    }

    /**
     * Punto de entrada para el mapa de detalle. Espera a que el contenedor
     * `.detail-map-wrap` tenga dimensiones reales mediante `ResizeObserver`
     * antes de llamar a `_buildMap`, evitando que Leaflet cachee un ancho
     * incorrecto tras un swap de HTMX. Registra el teardown del observer
     * en `window.MENDI_TEARDOWN`.
     */
    function initMap() {
      const el = document.getElementById("detail-map");
      if (!el || typeof L === "undefined") {
        const ov = document.getElementById("detail-map-loading");
        if (ov) ov.style.display = "none";
        return;
      }

      const wrap = el.closest(".detail-map-wrap") || el;
      const ro = new ResizeObserver((entries, observer) => {
        const w = entries[0].contentRect.width;
        if (w < 10) return;
        observer.disconnect();
        _buildMap(el);
      });
      ro.observe(wrap);
      window.MENDI_TEARDOWN.push(() => { try { ro.disconnect(); } catch (_) {} });

      const fallback = setTimeout(() => { ro.disconnect(); _buildMap(el); }, 600);
      window.MENDI_TEARDOWN.push(() => clearTimeout(fallback));
    }

    /**
     * Construye el mapa Leaflet vacío con teselas y teardown.
     * Los datos del track se cargan después vía _fetchTrack().
     */
    function _buildMap(el) {
      if (detailMap) return;
      // Limpiar cualquier instancia Leaflet residual en el contenedor
      if (el._leaflet_id) {
        try { el._leaflet_id = undefined; } catch (_) {}
      }
      el.innerHTML = "";
      el.className = el.className.replace(/\bleaflet-[\w-]+/g, "").trim();

      detailMap = L.map(el, {
        zoomControl: true,
        attributionControl: true,
        scrollWheelZoom: true,
      });

      detailMapLayerCtrl = window.MENDI_MAP.addLayerControl(detailMap);
      requestAnimationFrame(() => { try { detailMap && detailMap.invalidateSize(); } catch (_) {} });

      window.MENDI_TEARDOWN.push(() => {
        try { if (detailMap) detailMap.remove(); } catch (_) {}
        detailMap = null;
        detailMapLayerCtrl = null;
        trackPolyline = trackOutline = null;
        trackPoints = []; trackCumKm = [];
        profileCursorMarker = null;
      });

      _fetchTrack();
    }

    /**
     * Obtiene track, bbox, milestones y elev_samples desde la API
     * y los pinta sobre el mapa ya inicializado.
     */
    /**
     * Popups de hitos con hora local (Fase 1). Viven en scope de `init`
     * para que `renderWeather` pueda regenerarlos al llegar la zona de la
     * ruta (antes estaban dentro de `_fetchTrack` y rompían el clima con
     * un ReferenceError). `timePopups` lo rellena `_fetchTrack`.
     */
    const timePopups = [];
    function popupHtml(m) {
      const when = m.t ? fmtInstantHM(m.t) : (m.time || "—");
      const lines = [
        `<div style="font-family:Fraunces,serif;font-size:14px;font-weight:500;margin-bottom:4px;">${escapeHtml(m.name)}</div>`,
        `<div style="font-family:'IBM Plex Mono',monospace;font-size:11px;opacity:0.75;line-height:1.6;">`,
        `${escapeHtml(m.label)}${when && when !== "—" ? ` · ${escapeHtml(when)}` : ""}<br>`,
        `${fmtInt(m.elev_m)} m · km ${fmtKm(m.km)}`,
        `</div>`,
      ];
      return lines.join("");
    }
    function refreshPopupTimes() {
      timePopups.forEach(({ marker, m }) => {
        try { marker.setPopupContent(popupHtml(m)); } catch (_) {}
      });
    }

    async function _fetchTrack() {
      try {
        const res = await fetch(`/api/rutas/${ROUTE_ID}/track`);
        if (!res.ok) throw new Error("track http " + res.status);
        const json = await res.json();

        const track = json.track || [];
        const bbox  = json.bbox  || null;
        const milestones  = json.milestones  || [];
        const elevSamples = json.elev_samples || [];

        if (!track.length || !detailMap) { hideMapLoading(); return; }

        // Guardar track y calcular distancias acumuladas para el cursor del perfil
        trackPoints = track;
        trackCumKm = [0];
        for (let i = 1; i < track.length; i++) {
          const a = track[i - 1], b = track[i];
          const toRad = d => d * Math.PI / 180;
          const dLat = toRad(b[0] - a[0]), dLon = toRad(b[1] - a[1]);
          const s = Math.sin(dLat/2)**2 + Math.cos(toRad(a[0]))*Math.cos(toRad(b[0]))*Math.sin(dLon/2)**2;
          trackCumKm.push(trackCumKm[i-1] + 2 * 6371.0088 * Math.asin(Math.sqrt(s)));
        }

        const color = levelColor(D.level);
        trackOutline = L.polyline(track, {
          color: "#000", opacity: 0.35, weight: 6, lineCap: "round", lineJoin: "round",
        }).addTo(detailMap);
        trackPolyline = L.polyline(track, {
          color, weight: 3.2, opacity: 0.95, lineCap: "round", lineJoin: "round",
        }).addTo(detailMap);

        // Popups de hitos con hora local (Fase 1): el contenido se
        // regenera si llega una zona mejor (ver refreshPopupTimes,
        // definido en scope de init junto a timePopups/popupHtml).
        milestones.forEach((m) => {
          if (m.kind === "summit") return; // gestionados por _addSummitMarker o fallback
          const pt = _milestonePoint(m, track);
          if (!pt) return;
          const marker = L.marker(pt, { icon: buildPinIcon(m) }).addTo(detailMap);
          marker.bindPopup(popupHtml(m));
          timePopups.push({ marker, m });
        });

        const target = (bbox && bbox.length === 2) ? bbox : trackPolyline.getBounds();
        detailMap.fitBounds(target, { padding: [30, 30] });
        hideMapLoading();

        // Marcadores de cima arrastrables
        const summits = json.summits || [];
        if (summits.length > 0) {
          summits.forEach((s) => _addSummitMarker(s));
        } else {
          // Fallback: pintar los milestones de tipo summit como marcadores fijos
          milestones.filter(m => m.kind === "summit").forEach((m) => {
            const pt = _milestonePoint(m, track);
            if (!pt) return;
            const marker = L.marker(pt, { icon: buildPinIcon(m) }).addTo(detailMap);
            marker.bindPopup(`<div style="font-family:Fraunces,serif;font-size:14px;font-weight:500;">${escapeHtml(m.name)}</div><div style="font-family:'IBM Plex Mono',monospace;font-size:11px;opacity:0.75;">${fmtInt(m.elev_m)} m · km ${fmtKm(m.km)}</div>`);
          });
        }
        _bindMapClickForSummit();

        if (elevSamples.length >= 2) initElevHover(elevSamples);

      } catch (err) {
        hideMapLoading();
        console.warn("[detail] track fetch failed:", err);
      }
    }

    function _milestonePoint(m, track) {
      if (m.kind === "start") return track[0];
      if (m.kind === "end")   return track[track.length - 1];
      if (track.length < 2)  return track[0];
      const targetKm = m.km;
      let cum = 0, best = track[0], bestDelta = targetKm;
      for (let i = 1; i < track.length; i++) {
        const a = track[i - 1], b = track[i];
        const R = 6371.0088, toRad = (d) => d * Math.PI / 180;
        const dLat = toRad(b[0] - a[0]), dLon = toRad(b[1] - a[1]);
        const s = Math.sin(dLat / 2) ** 2 +
          Math.cos(toRad(a[0])) * Math.cos(toRad(b[0])) * Math.sin(dLon / 2) ** 2;
        cum += 2 * R * Math.asin(Math.sqrt(s));
        const delta = Math.abs(cum - targetKm);
        if (delta < bestDelta) { bestDelta = delta; best = b; }
      }
      return best;
    }

    /**
     * Construye un `L.divIcon` para el hito `m`. El glifo y color varían
     * según `m.kind`: `start` → `A` verde, `summit` → `▲` cálido,
     * `end` → `B` atenuado. El fondo semitransparente se adapta al tema.
     * @param {{kind:string, name:string}} m
     * @returns {L.DivIcon}
     */
    function buildPinIcon(m) {
      const accent = cssVar("--accent") || "#B5D17A";
      const warm = cssVar("--accent-warm") || "#E8B86D";
      const muted = cssVar("--text-muted") || "#9aa1a8";
      const bg = cssVar("--bg") || "#0e1014";
      let color = muted, glyph = "B", fill = "transparent";
      if (m.kind === "start") {
        color = accent; glyph = "A"; fill = isLight() ? "rgba(110,136,71,0.15)" : "rgba(181,209,122,0.18)";
      } else if (m.kind === "summit") {
        color = warm; glyph = "▲"; fill = isLight() ? "rgba(184,133,63,0.18)" : "rgba(232,184,109,0.18)";
      } else {
        color = muted; glyph = "B"; fill = "transparent";
      }
      const html = `
        <div style="
          width:26px;height:26px;border-radius:50%;
          border:2px solid ${color};background:${fill};
          display:grid;place-items:center;
          font-family:'IBM Plex Mono',monospace;font-size:11px;font-weight:600;
          color:${color};
          box-shadow:0 0 0 3px ${bg};
        ">${glyph}</div>`;
      return L.divIcon({
        html, className: "mendi-marker", iconSize: [26, 26], iconAnchor: [13, 13], popupAnchor: [0, -12],
      });
    }

    /**
     * Intercambia las capas de teselas del mapa de detalle según el tema
     * activo (`dark` / `light`), incluyendo las capas de etiquetas.
     */
    function updateMapTiles() {
      if (!detailMap || !window.MENDI_MAP) return;
      // Sincronizar capa base con el tema activo reutilizando la lógica
      // compartida de MENDI_MAP (igual que resumen y análisis).
      const { layers, active } = window.MENDI_MAP.buildMapLayers();
      const saved = (() => { try { return localStorage.getItem(window.MENDI_MAP.STORAGE_MAP_LAYER) || active; } catch (_) { return active; } })();
      const layer = layers[saved] || layers[active];
      // Reemplazar todas las capas tile existentes por la nueva
      detailMap.eachLayer((l) => { if (l instanceof L.TileLayer) detailMap.removeLayer(l); });
      layer.addTo(detailMap);
    }

    function _csrfHeaders(extra) {
      const meta = document.querySelector('meta[name="csrf-token"]');
      const token = meta ? meta.getAttribute("content") : "";
      return Object.assign({ "Content-Type": "application/json", "X-CSRF-Token": token }, extra);
    }

    // ============ CURSOR PERFIL → MAPA ============
    let profileCursorMarker = null;

    function _showProfileCursor(km) {
      if (!detailMap || !trackPoints.length) return;
      // Búsqueda binaria del punto más cercano al km dado
      let lo = 0, hi = trackCumKm.length - 1;
      while (lo < hi) {
        const mid = (lo + hi) >> 1;
        if (trackCumKm[mid] < km) lo = mid + 1;
        else hi = mid;
      }
      const a = trackCumKm[Math.max(0, lo - 1)];
      const b = trackCumKm[lo];
      const idx = Math.abs(a - km) < Math.abs(b - km) ? Math.max(0, lo - 1) : lo;
      const pt = trackPoints[idx];

      const warm = cssVar("--accent-warm") || "#E8B86D";
      const bg = cssVar("--bg") || "#0e1014";
      const icon = L.divIcon({
        html: `<div style="width:14px;height:14px;border-radius:50%;background:${warm};border:2px solid ${bg};box-shadow:0 0 0 2px ${warm};"></div>`,
        className: "",
        iconSize: [14, 14],
        iconAnchor: [7, 7],
      });

      if (!profileCursorMarker) {
        profileCursorMarker = L.marker(pt, { icon, interactive: false, zIndexOffset: 1000 }).addTo(detailMap);
      } else {
        profileCursorMarker.setLatLng(pt);
        profileCursorMarker.setIcon(icon);
      }
    }

    function _hideProfileCursor() {
      if (profileCursorMarker) {
        profileCursorMarker.remove();
        profileCursorMarker = null;
      }
    }

    // ============ CIMAS ============

    function _summitIcon() {
      const warm = cssVar("--accent-warm") || "#E8B86D";
      const bg = cssVar("--bg") || "#0e1014";
      const fill = isLight() ? "rgba(184,133,63,0.18)" : "rgba(232,184,109,0.18)";
      const html = `<div style="width:26px;height:26px;border-radius:50%;border:2px solid ${warm};background:${fill};display:grid;place-items:center;font-family:'IBM Plex Mono',monospace;font-size:11px;font-weight:600;color:${warm};box-shadow:0 0 0 3px ${bg};">&#9650;</div>`;
      return L.divIcon({ html, className: "mendi-marker", iconSize: [26, 26], iconAnchor: [13, 13], popupAnchor: [0, -16] });
    }

    function _addSummitMarker(summit) {
      if (!detailMap) return;
      const marker = L.marker([summit.lat, summit.lon], {
        icon: _summitIcon(),
        draggable: D.canEdit,
      });
      const popupContent = () => {
        const name = summit.name || "Cima";
        let html = `<div style="font-family:'IBM Plex Sans',sans-serif;min-width:160px;">`;
        html += `<div style="font-family:Fraunces,serif;font-size:14px;font-weight:500;margin-bottom:6px;">${escapeHtml(name)}</div>`;
        if (summit.elevation_m) html += `<div style="font-family:'IBM Plex Mono',monospace;font-size:11px;opacity:0.75;">${fmtInt(summit.elevation_m)} m</div>`;
        if (D.canEdit) {
          html += `<div style="margin-top:8px;display:flex;gap:6px;">`;
          html += `<input id="summit-name-${summit.id}" type="text" value="${escapeHtml(name)}" maxlength="60"
            style="flex:1;background:var(--surface-2);border:1px solid var(--border-2);color:var(--text);
            font-family:var(--mono);font-size:11px;padding:3px 6px;border-radius:4px;outline:none;">`;
          html += `<button data-action="save-summit" style="background:var(--accent);color:var(--bg);border:none;padding:3px 8px;border-radius:4px;font-family:var(--mono);font-size:10px;cursor:pointer;">ok</button>`;
          html += `<button data-action="delete-summit" style="background:var(--danger);color:#fff;border:none;padding:3px 8px;border-radius:4px;font-family:var(--mono);font-size:10px;cursor:pointer;">x</button>`;
          html += `</div>`;
        }
        html += `</div>`;
        return html;
      };
      marker.bindPopup(popupContent(), { maxWidth: 260 });
      marker.on("popupopen", () => {
        marker.setPopupContent(popupContent());
        const popup = marker.getPopup().getElement();
        if (popup && D.canEdit) {
          const btnSave = popup.querySelector('[data-action="save-summit"]');
          const btnDel = popup.querySelector('[data-action="delete-summit"]');
          if (btnSave) btnSave.addEventListener("click", () => window._saveSummitName(summit.id));
          if (btnDel) btnDel.addEventListener("click", () => window._deleteSummit(summit.id));
        }
      });

      if (D.canEdit) {
        marker.on("dragend", async (e) => {
          const { lat, lng } = e.target.getLatLng();
          try {
            const res = await fetch(`/api/rutas/${ROUTE_ID}/summits/${summit.id}`, {
              method: "PATCH",
              headers: _csrfHeaders(),
              body: JSON.stringify({ lat, lon: lng }),
            });
            if (res.ok) await _refreshSummits();
          } catch (_) {}
        });
      }

      marker.addTo(detailMap);
      summitMarkers.set(summit.id, marker);
    }

    // Refresca marcadores de cima en mapa y perfil SVG sin recargar la página
    async function _refreshSummits() {
      try {
        const res = await fetch(`/api/rutas/${ROUTE_ID}/track`);
        if (!res.ok) return;
        const json = await res.json();

        // Limpiar marcadores existentes del mapa
        summitMarkers.forEach((m) => m.remove());
        summitMarkers.clear();

        // Repintar marcadores en el mapa
        (json.summits || []).forEach((s) => _addSummitMarker(s));

        // Repintar marcadores en el perfil SVG
        const marksGroup = document.getElementById("summit-marks");
        if (marksGroup) {
          marksGroup.innerHTML = (json.summits || []).map((s) => {
            const summitId = parseInt(s.id, 10);
            if (!Number.isFinite(summitId) || summitId <= 0) return "";
            const x = Math.round(s.x);
            const y = Math.round(s.y);
            const label = escapeHtml(s.name || "Cima");
            const alt = escapeHtml(s.alt_str);
            return `<g class="summit-mark" data-summit-id="${summitId}" transform="translate(${x}, ${y})">
              <line x1="0" y1="0" x2="0" y2="14" stroke="var(--accent-warm)" stroke-width="1" stroke-dasharray="2 2"/>
              <circle cx="0" cy="0" r="4" fill="var(--accent-warm)" stroke="var(--bg)" stroke-width="2"/>
              <text x="6" y="-4" font-family="IBM Plex Mono" font-size="10" fill="var(--accent-warm)" letter-spacing="0.05em" transform="rotate(45, 6, -4)">&#9650; ${label} · ${alt} m</text>
            </g>`;
          }).join("");
        }

        // Repintar hitos del sidebar
        const milestones = json.milestones || [];
        const sidebarItems = document.querySelectorAll(".marker-item");
        // Eliminar los hitos de tipo summit existentes y reconstruirlos
        const summitItems = [...sidebarItems].filter(el => el.querySelector(".marker-pin.summit"));
        summitItems.forEach(el => el.remove());

        // Insertar los nuevos hitos de summit antes del hito "end"
        const endItem = [...document.querySelectorAll(".marker-item")].find(el => el.querySelector(".marker-pin.end"));
        const summitMilestones = milestones.filter(m => m.kind === "summit");
        summitMilestones.forEach((m) => {
          const div = document.createElement("div");
          div.className = "marker-item";
          div.innerHTML =
            `<div class="marker-pin summit">▲</div>` +
            `<div class="marker-info">` +
              `<div class="name">${escapeHtml(m.name || "Cima")}</div>` +
              `<div class="sub">${escapeHtml(m.label || "")}</div>` +
            `</div>` +
            `<div class="marker-meta">` +
              `<span>${fmtInt(m.elev_m)} m</span>` +
              `<span class="km">${fmtKm(m.km)} km</span>` +
            `</div>`;
          if (endItem) endItem.before(div);
        });

      } catch (_) {}
    }

    window._saveSummitName = async (summitId) => {
      const input = document.getElementById(`summit-name-${summitId}`);
      if (!input) return;
      const name = input.value.trim();
      try {
        const res = await fetch(`/api/rutas/${ROUTE_ID}/summits/${summitId}`, {
          method: "PATCH",
          headers: _csrfHeaders(),
          body: JSON.stringify({ name }),
        });
        if (res.ok) await _refreshSummits();
      } catch (_) {}
    };

    window._deleteSummit = async (summitId) => {
      try {
        const res = await fetch(`/api/rutas/${ROUTE_ID}/summits/${summitId}`, {
          method: "DELETE",
          headers: _csrfHeaders(),
        });
        if (res.ok) await _refreshSummits();
      } catch (_) {}
    };

    function _bindMapClickForSummit() {
      if (!detailMap || !D.canEdit) return;

      // Control personalizado: mano (pan) y pin (añadir cima)
      const SummitControl = L.Control.extend({
        options: { position: "topright" },
        onAdd() {
          const container = L.DomUtil.create("div", "leaflet-bar leaflet-control mendi-summit-ctrl");
          L.DomEvent.disableClickPropagation(container);

          const btnPan = L.DomUtil.create("a", "mendi-ctrl-btn mendi-ctrl-pan active", container);
          btnPan.title = "Mover mapa";
          btnPan.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" width="16" height="16"><path d="M18 11V8a2 2 0 00-4 0v3M14 8V6a2 2 0 00-4 0v5M10 9V7a2 2 0 00-4 0v8l-1-1a2 2 0 00-3 3l4 4a6 6 0 006 0 6 6 0 006-6V11a2 2 0 00-4 0" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

          const btnPin = L.DomUtil.create("a", "mendi-ctrl-btn mendi-ctrl-pin", container);
          btnPin.title = "Añadir cima";
          btnPin.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" width="16" height="16"><path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7z" stroke-linecap="round" stroke-linejoin="round"/><circle cx="12" cy="9" r="2.5" fill="currentColor" stroke="none"/></svg>`;

          L.DomEvent.on(btnPan, "click", () => {
            addSummitMode = false;
            btnPan.classList.add("active");
            btnPin.classList.remove("active");
            detailMap.getContainer().style.cursor = "";
            detailMap.dragging.enable();
          });

          L.DomEvent.on(btnPin, "click", () => {
            addSummitMode = true;
            btnPin.classList.add("active");
            btnPan.classList.remove("active");
            detailMap.getContainer().style.cursor = "crosshair";
          });

          return container;
        },
      });

      new SummitControl().addTo(detailMap);

      detailMap.on("click", async (e) => {
        if (!addSummitMode) return;
        const { lat, lng } = e.latlng;
        try {
          const res = await fetch(`/api/rutas/${ROUTE_ID}/summits`, {
            method: "POST",
            headers: _csrfHeaders(),
            body: JSON.stringify({ lat, lon: lng, name: "" }),
          });
          if (!res.ok) return;
          await _refreshSummits();
          addSummitMode = false;
          detailMap.getContainer().style.cursor = "";
          // Resetear botones del control
          const pan = detailMap.getContainer().querySelector(".mendi-ctrl-pan");
          const pin = detailMap.getContainer().querySelector(".mendi-ctrl-pin");
          if (pan) pan.classList.add("active");
          if (pin) pin.classList.remove("active");
        } catch (_) {}
      });
    }

    // ============ PERFIL DE ELEVACION ============
    /**
     * Inicializa el tooltip interactivo sobre el perfil de elevación
     * (`#elev-svg`). En cada `mousemove` / `touchmove` localiza el sample
     * más cercano con búsqueda binaria y actualiza el cursor SVG y el
     * tooltip con altitud, distancia, tiempo y pendiente.
     */
    function initElevHover(samples) {
      const wrap = document.getElementById("elev-wrap");
      const svg = document.getElementById("elev-svg");
      const cursor = document.getElementById("elev-cursor");
      const dot = document.getElementById("elev-cursor-dot");
      const tooltip = document.getElementById("elev-tooltip");
      if (!wrap || !svg || !cursor || !dot || !tooltip) return;
      if (!samples || samples.length < 2) return;

      const ttAlt = document.getElementById("tt-alt");
      const ttDist = document.getElementById("tt-dist");
      const ttTime = document.getElementById("tt-time");
      const ttGrad = document.getElementById("tt-grad");

      /**
       * Convierte una coordenada X de cliente (píxeles de pantalla) a
       * coordenada X en el sistema de coordenadas SVG [0, 800].
       * @param {number} clientX
       * @returns {number}
       */
      function svgX(clientX) {
        const rect = svg.getBoundingClientRect();
        const ratio = (clientX - rect.left) / rect.width;
        return Math.max(0, Math.min(800, ratio * 800));
      }

      /**
       * Búsqueda binaria del sample de elevación más cercano a la
       * coordenada SVG `xSvg`. Compara los dos candidatos adyacentes y
       * devuelve el de menor delta.
       * @param {number} xSvg
       * @returns {object}  Sample con `x`, `y`, `alt`, `km`, `t_str`, `grad_pct`.
       */
      function findSample(xSvg) {
        let lo = 0, hi = samples.length - 1;
        while (lo < hi) {
          const mid = (lo + hi) >> 1;
          if (samples[mid].x < xSvg) lo = mid + 1;
          else hi = mid;
        }
        const a = samples[Math.max(0, lo - 1)];
        const b = samples[lo];
        return Math.abs(a.x - xSvg) < Math.abs(b.x - xSvg) ? a : b;
      }

      function show(e) {
        const xSvg = svgX(e.clientX);
        const s = findSample(xSvg);
        cursor.setAttribute("x1", s.x);
        cursor.setAttribute("x2", s.x);
        cursor.style.display = "block";
        dot.setAttribute("cx", s.x);
        dot.setAttribute("cy", s.y);
        dot.style.display = "block";

        ttAlt.textContent = `${fmtInt(s.alt)} m`;
        ttDist.textContent = `${fmtKm(s.km)} km`;
        // Fase 1: t_iso lleva Z (UTC); se muestra en hora local de la ruta.
        ttTime.textContent = s.t_iso ? fmtInstantHM(s.t_iso) : (s.t_str || "—");
        const grad = s.grad_pct;
        if (grad == null || isNaN(grad)) {
          ttGrad.textContent = "—";
        } else {
          const sign = grad > 0 ? "+" : "";
          ttGrad.textContent = `${sign}${grad.toFixed(1).replace(".", ",")} %`;
        }

        const rect = svg.getBoundingClientRect();
        const wrapRect = wrap.getBoundingClientRect();
        const px = (s.x / 800) * rect.width + (rect.left - wrapRect.left);
        const py = (s.y / 280) * rect.height + (rect.top - wrapRect.top);
        // Mostrar arriba si hay espacio, abajo si no
        const tooltipH = tooltip.getBoundingClientRect().height || 90;
        const above = py - tooltipH - 10 >= 0;
        tooltip.style.left = px + "px";
        tooltip.style.top = above
          ? (py - tooltipH - 10) + "px"
          : (py + 16) + "px";
        tooltip.style.transform = "translateX(-50%)";
        tooltip.classList.add("visible");
        _showProfileCursor(s.km);
      }

      function hide() {
        cursor.style.display = "none";
        dot.style.display = "none";
        tooltip.classList.remove("visible");
        _hideProfileCursor();
      }

      svg.addEventListener("mousemove", show);
      svg.addEventListener("mouseleave", hide);
      svg.addEventListener("touchmove", (e) => {
        if (e.touches.length) {
          show({ clientX: e.touches[0].clientX });
          e.preventDefault();
        }
      }, { passive: false });
      svg.addEventListener("touchend", hide);
      // Listeners atados a un nodo dentro de #hx-root → mueren con el swap.
    }

    // ============ CLIMA ============
    /**
     * Obtiene el clima histórico en la cima desde `/api/rutas/{id}/clima`
     * y llama a `renderWeather` si los datos están disponibles. Muestra
     * el estado `wx-empty` ante cualquier error o respuesta sin datos.
     */
    async function initWeather() {
      const card = document.getElementById("wx-card");
      const loading = document.getElementById("wx-loading");
      const content = document.getElementById("wx-content");
      const empty = document.getElementById("wx-empty");
      if (!card) return;

      try {
        const res = await fetch(`/api/rutas/${ROUTE_ID}/clima`);
        if (!res.ok) throw new Error("clima http " + res.status);
        const json = await res.json();
        if (!json.available || !json.data) {
          loading.style.display = "none";
          empty.style.display = "block";
          return;
        }
        renderWeather(json);
        loading.style.display = "none";
        content.style.display = "block";
      } catch (err) {
        loading.style.display = "none";
        empty.style.display = "block";
      }
    }

    /**
     * Procesa el JSON de clima devuelto por la API y rellena todas las
     * celdas de la tarjeta meteorológica: temperatura, viento, ráfagas,
     * humedad, nubosidad, precipitación y horas de luz. Calcula los
     * estadísticos sobre la ventana temporal de la ruta si está disponible,
     * o sobre el día completo como fallback.
     * @param {{data:object, hike_start:string, hike_end:string}} payload
     */
    function renderWeather(payload) {
      const wx = payload.data || {};
      const daily = wx.daily || {};
      const hourly = wx.hourly || {};
      const hours = hourly.time || [];

      // Fase 1: `hourly.time` es wall-time local de la ruta (Open-Meteo con
      // timezone:auto). Los hike_* llegan como instantes UTC con `Z`: los
      // convertimos a wall-time de la zona del payload para comparar en el
      // mismo marco. Sin `timezone` en el payload, se usa la del navegador.
      const tz = (wx.timezone && String(wx.timezone)) || routeTz || null;
      if (wx.timezone) {
        routeTz = wx.timezone;
        rewriteClockTimes();
        // Blindaje: si el refresco de popups fallase, la tarjeta debe
        // seguir pintándose (un ReferenceError aquí la vaciaba entera).
        if (typeof refreshPopupTimes === "function") refreshPopupTimes();
      }

      const hikeStart = payload.hike_start ? parseLocalIso(utcToWall(payload.hike_start, tz)) : null;
      const hikeEnd = payload.hike_end ? parseLocalIso(utcToWall(payload.hike_end, tz)) : null;

      const inWindow = (iso) => {
        if (!hikeStart || !hikeEnd) return false;
        const d = new Date(iso);
        return d >= hikeStart && d <= hikeEnd;
      };

      const tempArr = hourly.temperature_2m || [];
      const windArr = hourly.wind_speed_10m || [];
      const gustArr = hourly.wind_gusts_10m || [];
      const dirArr = hourly.wind_direction_10m || [];
      const humArr = hourly.relative_humidity_2m || [];
      const cloudArr = hourly.cloud_cover || [];
      const precipArr = hourly.precipitation || [];

      const idxWin = hours.map((t, i) => (inWindow(t) ? i : -1)).filter((i) => i >= 0);
      const idxAll = hours.map((_, i) => i);
      const useIdx = idxWin.length ? idxWin : idxAll;

      function avg(arr, idx) {
        const vals = idx.map((i) => arr[i]).filter((v) => v != null && !isNaN(v));
        if (!vals.length) return null;
        return vals.reduce((a, b) => a + b, 0) / vals.length;
      }
      function mx(arr, idx) {
        const vals = idx.map((i) => arr[i]).filter((v) => v != null && !isNaN(v));
        if (!vals.length) return null;
        return Math.max(...vals);
      }
      function mn(arr, idx) {
        const vals = idx.map((i) => arr[i]).filter((v) => v != null && !isNaN(v));
        if (!vals.length) return null;
        return Math.min(...vals);
      }

      const tMean = avg(tempArr, useIdx);
      const tMax = mx(tempArr, useIdx);
      const tMin = mn(tempArr, useIdx);
      const wMean = avg(windArr, useIdx);
      const wDir = avg(dirArr, useIdx);
      const gustMaxIdx = useIdx.reduce((best, i) => (gustArr[i] != null && (best === -1 || gustArr[i] > gustArr[best]) ? i : best), -1);
      const gustMax = gustMaxIdx >= 0 ? gustArr[gustMaxIdx] : null;
      const gustTime = gustMaxIdx >= 0 ? hours[gustMaxIdx] : null;
      const hum = avg(humArr, useIdx);
      const cloud = avg(cloudArr, useIdx);
      const precip = useIdx.reduce((acc, i) => acc + (precipArr[i] || 0), 0);

      const cond = describeCondition(cloud, precip);
      setText("wx-cond-name", cond.name);
      setText("wx-cond-label", `condición · ${idxWin.length ? "ventana ruta" : "día completo"}`);
      setIcon("wx-icon", cond.icon);

      setHTML("wx-temp-big", tMean != null ? `${tMean.toFixed(1).replace(".", ",")}<span class="u">°C</span>` : `—<span class="u">°C</span>`);
      setText("wx-temp-mean", tMean != null ? `${tMean.toFixed(1).replace(".", ",")} °C` : "—");
      setText("wx-temp-max", tMax != null ? `${tMax.toFixed(1).replace(".", ",")} °C` : "—");
      setText("wx-temp-min", tMin != null ? `${tMin.toFixed(1).replace(".", ",")} °C` : "—");

      setHTML("wx-wind-mean", wMean != null ? `${wMean.toFixed(0)}<span class="u">km/h</span>` : "—");
      setText("wx-wind-dir", wDir != null ? compassDir(wDir) : "—");
      setHTML("wx-gust-max", gustMax != null ? `${gustMax.toFixed(0)}<span class="u">km/h</span>` : "—");
      setText("wx-gust-time", gustTime ? formatHourLocal(gustTime) : "—");

      setHTML("wx-hum", hum != null ? `${hum.toFixed(0)}<span class="u">%</span>` : "—");
      setHTML("wx-cloud", cloud != null ? `${cloud.toFixed(0)}<span class="u">%</span>` : "—");
      setText("wx-cloud-sub", cloud != null ? cloudLabel(cloud) : "—");

      setHTML("wx-precip", `${precip.toFixed(1).replace(".", ",")}<span class="u">mm</span>`);
      setText("wx-precip-sub", precip > 0 ? "acumulado en ruta" : "sin precipitación");

      const sunrise = (daily.sunrise || [])[0];
      const sunset = (daily.sunset || [])[0];
      if (sunrise && sunset) {
        const sr = formatHourLocal(sunrise);
        const ss = formatHourLocal(sunset);
        setText("wx-sun", `${sr} · ${ss}`);
        const lightHours = (new Date(sunset) - new Date(sunrise)) / 36e5;
        setText("wx-light", `${lightHours.toFixed(1).replace(".", ",")} h de luz`);
      } else {
        setText("wx-sun", "—");
        setText("wx-light", "—");
      }

      renderWeatherChart(hours, tempArr, hikeStart, hikeEnd);
    }

    /**
     * Clasifica la condición meteorológica en una etiqueta y un icono
     * a partir de la nubosidad media y la precipitación acumulada.
     * @param {number|null} cloud   Nubosidad media en %.
     * @param {number|null} precip  Precipitación acumulada en mm.
     * @returns {{name:string, icon:"sun"|"partly"|"cloud"|"rain"}}
     */
    function describeCondition(cloud, precip) {
      if (precip != null && precip >= 4) return { name: "Lluvia", icon: "rain" };
      if (precip != null && precip >= 0.5) return { name: "Lluvia ligera", icon: "rain" };
      if (cloud == null) return { name: "—", icon: "sun" };
      if (cloud < 20) return { name: "Despejado", icon: "sun" };
      if (cloud < 50) return { name: "Poco nuboso", icon: "partly" };
      if (cloud < 85) return { name: "Nuboso", icon: "cloud" };
      return { name: "Cubierto", icon: "cloud" };
    }

    /**
     * Inyecta el SVG del icono meteorológico en el elemento `#id`.
     * Soporta los tipos `"sun"`, `"partly"`, `"cloud"` y `"rain"`.
     * @param {string} id
     * @param {"sun"|"partly"|"cloud"|"rain"} kind
     */
    function setIcon(id, kind) {
      const el = document.getElementById(id);
      if (!el) return;
      let svg = "";
      if (kind === "sun") {
        svg = `<svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round">
          <circle cx="32" cy="32" r="11"/>
          <path d="M32 8v6M32 50v6M8 32h6M50 32h6M14 14l4.5 4.5M45.5 45.5L50 50M14 50l4.5-4.5M45.5 18.5L50 14"/>
        </svg>`;
      } else if (kind === "partly") {
        svg = `<svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="22" cy="24" r="9" stroke-opacity="0.95"/>
          <path d="M22 9v3M22 36v3M7 24h3M34 24h3M11.5 13.5l2.1 2.1M30.4 32.4l2.1 2.1M11.5 34.5l2.1-2.1M30.4 15.6l2.1-2.1" stroke-opacity="0.85"/>
          <path d="M28 38c-5 0-9 3.4-9.6 7.8C15.6 46.6 13 49.5 13 53c0 3.9 3.1 7 7 7h24c4.4 0 8-3.6 8-8 0-4.1-3.1-7.5-7.1-7.95C44.2 39.6 38.7 35 32 35c-1.4 0-2.7.2-4 .55"
                stroke="var(--text-muted)" stroke-opacity="0.75" fill="var(--surface-2)" fill-opacity="0.4"/>
        </svg>`;
      } else if (kind === "cloud") {
        svg = `<svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
          <path d="M18 44c-5 0-9-4-9-9s4-9 9-9c1 0 2 .2 3 .5C23 19 28 15 34 15c8 0 14 6 14 14 0 .7-.1 1.3-.2 2 3.5.6 6.2 3.7 6.2 7.5 0 4.2-3.4 7.5-7.5 7.5H18z"
                stroke="currentColor" fill="var(--surface-2)" fill-opacity="0.3"/>
        </svg>`;
      } else if (kind === "rain") {
        svg = `<svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
          <path d="M18 36c-5 0-9-4-9-9s4-9 9-9c1 0 2 .2 3 .5C23 11 28 7 34 7c8 0 14 6 14 14 0 .7-.1 1.3-.2 2 3.5.6 6.2 3.7 6.2 7.5 0 4.2-3.4 7.5-7.5 7.5H18z"
                stroke="currentColor" fill="var(--surface-2)" fill-opacity="0.3"/>
          <path d="M22 46l-3 8M32 46l-3 8M42 46l-3 8" stroke="var(--accent-cool)" stroke-opacity="0.85"/>
        </svg>`;
      }
      el.innerHTML = svg;
    }

    /**
     * Convierte grados de dirección de viento (0-360) a punto cardinal
     * de 8 posiciones con el ángulo numérico.
     * @param {number} deg
     * @returns {string}  Ej.: `"NE · 45°"`
     */
    function compassDir(deg) {
      const dirs = ["N", "NE", "E", "SE", "S", "SO", "O", "NO"];
      const idx = Math.round(((deg % 360) / 45)) % 8;
      return `${dirs[idx]} · ${Math.round(deg)}°`;
    }

    /**
     * Devuelve una etiqueta textual de nubosidad a partir del porcentaje.
     * @param {number} pct  Nubosidad en %.
     * @returns {string}
     */
    function cloudLabel(pct) {
      if (pct < 20) return "cielo despejado";
      if (pct < 50) return "nubosidad parcial";
      if (pct < 85) return "muy nuboso";
      return "cielo cubierto";
    }

    /**
     * Formatea un instante ISO con `Z` (UTC) como "HH:MM" en la zona `tz`
     * (o la del navegador si no se indica). Devuelve "—" sin dato.
     */
    function fmtInstantHM(isoZ, tz) {
      if (!isoZ) return "—";
      try {
        const zone = tz || routeTz || undefined;
        const parts = new Intl.DateTimeFormat("es-ES", {
          timeZone: zone, hour: "2-digit", minute: "2-digit", hourCycle: "h23",
        }).formatToParts(new Date(isoZ));
        const get = (t) => (parts.find((p) => p.type === t) || {}).value || "00";
        return `${get("hour")}:${get("minute")}`;
      } catch (_) {
        const d = new Date(isoZ);
        return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
      }
    }

    /**
     * Convierte un instante UTC a wall-time "YYYY-MM-DDTHH:MM" en la zona
     * `tz` (comparable lexicográficamente con los hourly de Open-Meteo,
     * que ya son wall-time local). Fallback: zona del navegador.
     */
    function utcToWall(isoZ, tz) {
      const zone = tz || routeTz || undefined;
      const parts = new Intl.DateTimeFormat("en-CA", {
        timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit", hourCycle: "h23",
      }).formatToParts(new Date(isoZ));
      const get = (t) => (parts.find((p) => p.type === t) || {}).value;
      return `${get("year")}-${get("month")}-${get("day")}T${get("hour")}:${get("minute")}`;
    }

    /**
     * Reescribe las horas SSR (UTC) a hora local: hitos `[data-t-utc]` y
     * celda salida/llegada `[data-start-utc]`. Se llama al init (zona del
     * navegador) y de nuevo al llegar el clima (zona de la ruta).
     */
    function rewriteClockTimes() {
      document.querySelectorAll("[data-t-utc]").forEach((el) => {
        const iso = el.getAttribute("data-t-utc");
        const label = el.getAttribute("data-label") || "";
        if (iso) el.textContent = `${label} · ${fmtInstantHM(iso)}`;
      });
      const se = document.querySelector("[data-start-utc]");
      if (se) {
        const s = se.getAttribute("data-start-utc");
        const e = se.getAttribute("data-end-utc");
        if (s && e) se.textContent = `salida ${fmtInstantHM(s)} · llegada ${fmtInstantHM(e)}`;
      }
    }

    /**
     * Formatea un string ISO sin zona horaria (`"YYYY-MM-DDTHH:MM"`) como
     * "HH:MM" verbatim (ya es hora local). Para instantes con `Z`, usar
     * fmtInstantHM en su lugar.
     */
    function formatHourLocal(iso) {
      if (!iso) return "—";
      const m = String(iso).match(/T(\d{2}):(\d{2})/);
      if (m) return `${m[1]}:${m[2]}`;
      const d = new Date(iso);
      return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
    }

    /**
     * Renderiza el gráfico SVG de temperatura horaria (`#wx-line` /
     * `#wx-area`). Escala automáticamente el eje Y con un margen del 10 %
     * (mínimo 4 °C de rango). Si se conoce la ventana temporal de la ruta
     * pinta un rectángulo de selección y marcadores de hora de inicio/fin.
     * @param {string[]} hours      Array de strings ISO horarios.
     * @param {number[]} tempArr    Temperaturas en °C (puede contener `null`).
     * @param {Date|null} hikeStart Inicio de la ruta.
     * @param {Date|null} hikeEnd   Fin de la ruta.
     */
    function renderWeatherChart(hours, tempArr, hikeStart, hikeEnd) {
      const lineEl = document.getElementById("wx-line");
      const areaEl = document.getElementById("wx-area");
      const yLabels = document.getElementById("wx-y-labels");
      const winRect = document.getElementById("wx-hike-window");
      const winMarkers = document.getElementById("wx-window-markers");
      if (!lineEl || !areaEl || !hours.length) return;

      const W = 800, H = 180, padTop = 18, padBottom = 22;
      const innerH = H - padTop - padBottom;

      const valid = tempArr.filter((v) => v != null && !isNaN(v));
      if (!valid.length) return;
      let tMin = Math.min(...valid);
      let tMax = Math.max(...valid);
      const range = tMax - tMin;
      if (range < 4) {
        tMin -= 2; tMax += 2;
      } else {
        tMin -= range * 0.1;
        tMax += range * 0.1;
      }

      function xFor(i) {
        return (W * i) / Math.max(1, hours.length - 1);
      }
      function yFor(t) {
        return padTop + innerH * (1 - (t - tMin) / (tMax - tMin));
      }

      let line = "", area = "";
      hours.forEach((_, i) => {
        const t = tempArr[i];
        if (t == null || isNaN(t)) return;
        const x = xFor(i);
        const y = yFor(t);
        if (line === "") {
          line = `M ${x.toFixed(1)} ${y.toFixed(1)}`;
          area = `M ${x.toFixed(1)} ${(padTop + innerH).toFixed(1)} L ${x.toFixed(1)} ${y.toFixed(1)}`;
        } else {
          line += ` L ${x.toFixed(1)} ${y.toFixed(1)}`;
          area += ` L ${x.toFixed(1)} ${y.toFixed(1)}`;
        }
      });
      if (area) area += ` L ${xFor(hours.length - 1).toFixed(1)} ${(padTop + innerH).toFixed(1)} Z`;
      lineEl.setAttribute("d", line);
      areaEl.setAttribute("d", area);

      yLabels.innerHTML = "";
      for (let s = 0; s <= 3; s++) {
        const t = tMax - ((tMax - tMin) * s) / 3;
        const y = padTop + (innerH * s) / 3 + 3;
        yLabels.insertAdjacentHTML("beforeend",
          `<text x="8" y="${y.toFixed(0)}">${t.toFixed(0)}°</text>`);
      }

      winRect.setAttribute("width", "0");
      winMarkers.innerHTML = "";
      if (hikeStart && hikeEnd && hours.length) {
        const findIdx = (date) => {
          let bestI = 0, bestDelta = Infinity;
          for (let i = 0; i < hours.length; i++) {
            const ht = parseLocalIso(hours[i]);
            const delta = Math.abs(ht - date);
            if (delta < bestDelta) { bestDelta = delta; bestI = i; }
          }
          return bestI;
        };
        const i1 = findIdx(hikeStart);
        const i2 = findIdx(hikeEnd);
        const x1 = Math.min(xFor(i1), xFor(i2));
        const x2 = Math.max(xFor(i1), xFor(i2));
        winRect.setAttribute("x", x1.toFixed(1));
        winRect.setAttribute("width", Math.max(2, x2 - x1).toFixed(1));
        winMarkers.insertAdjacentHTML("beforeend",
          `<text x="${x1.toFixed(0)}" y="14" font-family="IBM Plex Mono" font-size="9.5" fill="var(--moderate)" letter-spacing="0.05em">▸ ${formatHourLocal(hours[i1])}</text>`);
        winMarkers.insertAdjacentHTML("beforeend",
          `<text x="${x2.toFixed(0)}" y="14" font-family="IBM Plex Mono" font-size="9.5" fill="var(--moderate)" letter-spacing="0.05em" text-anchor="end">${formatHourLocal(hours[i2])} ◂</text>`);
      }
    }

    /**
     * Parsea un string ISO sin zona horaria (`"YYYY-MM-DDTHH:MM"`) como
     * hora local del navegador, evitando que el constructor `Date` lo
     * interprete como UTC y desplace la hora.
     * @param {string} iso
     * @returns {Date}
     */
    function parseLocalIso(iso) {
      const m = String(iso).match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/);
      if (!m) return new Date(iso);
      return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]);
    }

    /**
     * Escribe `v` en `textContent` del elemento `#id` si existe.
     * @param {string} id
     * @param {string} v
     */
    function setText(id, v) {
      const el = document.getElementById(id);
      if (el) el.textContent = v;
    }
    /**
     * Escribe `v` en `innerHTML` del elemento `#id` si existe.
     * @param {string} id
     * @param {string} v
     */
    function setHTML(id, v) {
      const el = document.getElementById(id);
      if (el) el.innerHTML = v;
    }

    // ============ NOTAS ============
    /**
     * Inicializa el editor de notas y etiquetas de la ruta. Gestiona las
     * transiciones vista/edición, el añadido y eliminación de tags con
     * teclado, y el guardado vía PATCH a `/api/rutas/{id}/notas`.
     */
    function initNotes() {
      const card = document.getElementById("notes-card");
      if (!card) return;

      const view = document.getElementById("notes-view");
      const edit = document.getElementById("notes-edit");
      const actionsView = document.getElementById("notes-actions-view");
      const actionsEdit = document.getElementById("notes-actions-edit");
      // Si no hay acciones renderizadas (rol viewer) no inicializamos el editor.
      if (!actionsView || !actionsEdit) return;
      const btnEdit = document.getElementById("btn-edit");
      const btnSave = document.getElementById("btn-save");
      const btnCancel = document.getElementById("btn-cancel");
      const textarea = document.getElementById("notes-textarea");
      const tagsEdit = document.getElementById("notes-tags-edit");
      const tagInput = document.getElementById("tag-input");
      const flash = document.getElementById("notes-saved-flash");
      const meta = document.getElementById("notes-meta");
      const textView = document.getElementById("notes-text-view");
      const tagsView = document.getElementById("notes-tags-view");

      let originalText = D.notes ? D.notes.text || "" : "";
      let editingTags = D.notes && D.notes.tags ? [...D.notes.tags] : [];
      let originalTags = [...editingTags];

      function enterEdit() {
        card.classList.add("editing");
        view.style.display = "none";
        edit.style.display = "block";
        actionsView.style.display = "none";
        actionsEdit.style.display = "inline-flex";
        textarea.value = originalText;
        editingTags = [...originalTags];
        renderEditTags();
        textarea.focus();
      }

      function leaveEdit() {
        card.classList.remove("editing");
        view.style.display = "block";
        edit.style.display = "none";
        actionsView.style.display = "inline-flex";
        actionsEdit.style.display = "none";
      }

      function renderEditTags() {
        tagsEdit.querySelectorAll(".notes-tag").forEach((n) => n.remove());
        editingTags.forEach((t, idx) => {
          const span = document.createElement("span");
          span.className = "notes-tag";
          span.innerHTML = `${escapeHtml(t)}<span class="x" data-idx="${idx}" title="quitar">×</span>`;
          tagsEdit.insertBefore(span, tagInput);
        });
      }

      function renderViewTags() {
        tagsView.innerHTML = originalTags
          .map((t) => `<span class="notes-tag">${escapeHtml(t)}</span>`)
          .join("");
      }

      function renderViewText() {
        if (originalText) {
          textView.classList.remove("notes-empty");
          textView.textContent = originalText;
        } else {
          textView.innerHTML = `<em class="notes-empty">Aún no hay observaciones · pulsa <strong>editar</strong> para añadir notas.</em>`;
        }
      }

      btnEdit.addEventListener("click", enterEdit);
      btnCancel.addEventListener("click", leaveEdit);

      tagsEdit.addEventListener("click", (e) => {
        const x = e.target.closest(".x");
        if (!x) return;
        const idx = parseInt(x.dataset.idx, 10);
        if (!isNaN(idx)) {
          editingTags.splice(idx, 1);
          renderEditTags();
        }
      });

      tagInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === ",") {
          e.preventDefault();
          const v = tagInput.value.trim().replace(/^#+/, "").slice(0, 22);
          if (v && !editingTags.includes(v) && editingTags.length < 10) {
            editingTags.push(v);
            renderEditTags();
          }
          tagInput.value = "";
        } else if (e.key === "Backspace" && !tagInput.value && editingTags.length) {
          editingTags.pop();
          renderEditTags();
        }
      });

      btnSave.addEventListener("click", async () => {
        btnSave.disabled = true;
        const newText = textarea.value.trim();
        const newTags = [...editingTags];
        try {
          const res = await fetch(`/api/rutas/${ROUTE_ID}/notas`, {
            method: "PATCH",
            headers: _csrfHeaders(),
            body: JSON.stringify({ notes: newText, tags: newTags }),
          });
          if (!res.ok) throw new Error("save failed");
          const json = await res.json();
          originalText = json.notes || "";
          originalTags = json.tags || [];
          renderViewText();
          renderViewTags();
          if (json.updated && meta) {
            meta.textContent = formatUpdated(json.updated);
          }
          flash.classList.add("visible");
          setTimeout(() => flash.classList.remove("visible"), 1600);
          leaveEdit();
        } catch (err) {
          flash.textContent = "error";
          flash.style.color = "var(--danger)";
          flash.classList.add("visible");
          setTimeout(() => {
            flash.classList.remove("visible");
            flash.textContent = "guardado";
            flash.style.color = "";
          }, 2000);
        } finally {
          btnSave.disabled = false;
        }
      });
      // Listeners atados a nodos dentro de #hx-root → mueren con el swap.
    }

    /**
     * Formatea la fecha de última edición de las notas como
     * `"memo · DD.MM.YYYY"`. Devuelve `"memo"` si el ISO es inválido.
     * @param {string} iso
     * @returns {string}
     */
    function formatUpdated(iso) {
      const d = new Date(iso);
      if (isNaN(d.getTime())) return "memo";
      const dd = String(d.getDate()).padStart(2, "0");
      const mm = String(d.getMonth() + 1).padStart(2, "0");
      const yy = d.getFullYear();
      return `memo · ${dd}.${mm}.${yy}`;
    }

    // ============ MODAL RENOMBRAR ============
    /**
     * Inicializa el modal de renombrado (`#rename-modal`). Al confirmar
     * envía un PATCH a `/api/rutas/{id}/nombre` y actualiza el título
     * hero, la miga de pan y el título del documento sin recargar la
     * página.
     */
    function initRenameModal() {
      const btn = document.getElementById("btn-rename");
      const modal = document.getElementById("rename-modal");
      const input = document.getElementById("rename-input");
      const cancel = document.getElementById("rename-cancel");
      const confirm = document.getElementById("rename-confirm");
      if (!btn || !modal || !input || !cancel || !confirm) return;

      function open() {
        input.value = D.name || "";
        modal.style.display = "grid";
        setTimeout(() => { input.focus(); input.select(); }, 30);
      }
      function close() { modal.style.display = "none"; }

      btn.addEventListener("click", open);
      cancel.addEventListener("click", close);
      modal.addEventListener("click", (e) => { if (e.target === modal) close(); });
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") { e.preventDefault(); confirm.click(); }
        if (e.key === "Escape") close();
      });

      confirm.addEventListener("click", async () => {
        const newName = input.value.trim();
        if (!newName) return;
        confirm.disabled = true;
        try {
          const res = await fetch(`/api/rutas/${ROUTE_ID}/nombre`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name: newName }),
          });
          if (!res.ok) throw new Error("rename failed");
          const json = await res.json();
          D.name = json.name;
          const heroTitle = document.getElementById("hero-title");
          const bcName = document.getElementById("bc-name");
          const delName = document.getElementById("delete-modal-name");
          if (heroTitle) heroTitle.textContent = json.name;
          if (bcName) bcName.textContent = json.name;
          if (delName) delName.textContent = json.name;
          document.title = `${json.name} · mendi.log`;
          close();
        } catch (err) {
          // silencioso
        } finally {
          confirm.disabled = false;
        }
      });
    }

    // ============ MODAL ELIMINAR ============
    /**
     * Inicializa el modal de eliminación (`#delete-modal`). Al confirmar
     * envía un DELETE a `/api/rutas/{id}` y redirige a `/rutas`. Registra
     * el listener de Escape en `document` con teardown para no acumular
     * listeners entre swaps de HTMX.
     */
    function initDeleteModal() {
      const btn = document.getElementById("btn-delete");
      const modal = document.getElementById("delete-modal");
      const cancel = document.getElementById("delete-cancel");
      const confirm = document.getElementById("delete-confirm");
      if (!btn || !modal || !cancel || !confirm) return;

      function open() { modal.style.display = "grid"; }
      function close() { modal.style.display = "none"; }

      btn.addEventListener("click", open);
      cancel.addEventListener("click", close);
      modal.addEventListener("click", (e) => { if (e.target === modal) close(); });

      // Listener global (document) → debe limpiarse en teardown.
      function onDelKey(e) {
        if (e.key === "Escape" && modal.style.display === "grid") close();
      }
      document.addEventListener("keydown", onDelKey);
      window.MENDI_TEARDOWN.push(() => {
        document.removeEventListener("keydown", onDelKey);
      });

      confirm.addEventListener("click", async () => {
        confirm.disabled = true;
        try {
          const res = await fetch(`/api/rutas/${ROUTE_ID}`, {
            method: "DELETE",
            headers: _csrfHeaders(),
          });
          if (!res.ok) throw new Error("delete failed");
          window.location.href = "/rutas";
        } catch (err) {
          confirm.disabled = false;
        }
      });
    }

    // ============ THEME OBSERVER ============
    /**
     * Observa cambios en el atributo `data-theme` del elemento raíz y
     * llama a `updateMapTiles` para sincronizar las teselas del mapa con
     * el tema nuevo. Registra el teardown del observer en
     * `window.MENDI_TEARDOWN`.
     */
    function initThemeObserver() {
      const obs = new MutationObserver((mutations) => {
        for (const m of mutations) {
          if (m.attributeName === "data-theme") {
            updateMapTiles();
          }
        }
      });
      obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
      window.MENDI_TEARDOWN.push(() => {
        try { obs.disconnect(); } catch (_) {}
      });
    }

    // ============ ARRANQUE ============
    // initAddSummitMode se elimina: el control se crea directamente en _bindMapClickForSummit
    // Fase 1: corrige las horas SSR (UTC) a hora local del navegador; se
    // repite al llegar el clima con la zona de la ruta.
    rewriteClockTimes();
    initMap();
    initWeather();
    initNotes();
    initRenameModal();
    initDeleteModal();
    initThemeObserver();
  }

  window.MENDI_PAGES.detail = { init };
})();
