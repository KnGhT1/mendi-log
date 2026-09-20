// ============================================================
// mendi.log - cliente principal
// ------------------------------------------------------------
//   1. Dispatcher de páginas (compatible con HTMX hx-boost)
//      Cuando navegamos entre menús, htmx hace swap de #hx-root
//      sin recargar el <head> → fuentes y CSS persisten,
//      desaparece el FOUT.
//      Cada módulo de página se registra en window.MENDI_PAGES
//      y el dispatcher llama a init() / teardown() al cambiar
//      de página.
//
//   2. Toggle de tema con event delegation, para que sobreviva
//      a los swaps (el botón es un nodo nuevo cada vez).
//
//   3. Indicador de progreso superior conectado a los eventos
//      htmx:beforeRequest / htmx:afterRequest.
//
//   4. Página "resumen": mapa, charts, rotación de siluetas,
//      edición inline. Toda la data viene en window.MENDI.
// ============================================================
(function () {
  "use strict";

  // ============ Constantes ============
  const STORAGE_THEME = "mendi.theme";
  const STORAGE_INTERVAL = "mendi.rotInterval";

  // ============ Registro global ============
  window.MENDI_PAGES = window.MENDI_PAGES || {};
  // Pila de teardowns activos. Cada módulo de página apila aquí sus
  // funciones de limpieza (intervals, mapas Leaflet, observers...).
  // El dispatcher las ejecuta antes de inicializar la siguiente página.
  window.MENDI_TEARDOWN = window.MENDI_TEARDOWN || [];

  /**
   * Ejecuta y vacía la pila de funciones de limpieza registradas en
   * `window.MENDI_TEARDOWN`. Se llama antes de inicializar cada página
   * nueva para destruir mapas Leaflet, intervals y observers del módulo
   * anterior sin dejar fugas de memoria.
   */
  function runTeardowns() {
    while (window.MENDI_TEARDOWN.length) {
      const fn = window.MENDI_TEARDOWN.pop();
      try { fn(); } catch (e) { /* ignore */ }
    }
  }

  // ============ Tema (event delegation, persiste tras swaps) ============
  /**
   * Aplica el tema visual (`"dark"` | `"light"`) al documento:
   * - Escribe `data-theme` en `<html>`.
   * - Alterna los iconos luna/sol del botón.
   * - Notifica a los módulos de mapa y charts que deben re-pintarse.
   * - Emite el evento `mendi:themechange` para que otros módulos reaccionen.
   * Expuesta como `window.applyTheme` para uso desde otros módulos.
   * @param {"dark"|"light"} theme
   */
  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    const moon = document.getElementById("theme-icon-moon");
    const sun = document.getElementById("theme-icon-sun");
    if (moon && sun) {
      moon.style.display = theme === "dark" ? "none" : "inline";
      sun.style.display = theme === "dark" ? "inline" : "none";
    }
    // Refresca elementos dependientes del tema si están presentes
    if (typeof window.updateMapTiles === "function") window.updateMapTiles();
    if (typeof window.renderMonthlyChart === "function") window.renderMonthlyChart();
    document.dispatchEvent(new CustomEvent("mendi:themechange", { detail: { theme } }));
  }
  window.applyTheme = applyTheme;

  /**
   * Sincroniza los iconos luna/sol con el tema actualmente almacenado en
   * `data-theme`. Se llama en cada `dispatch()` para que los iconos sean
   * correctos tras un swap de HTMX (el botón es un nodo nuevo cada vez).
   */
  function refreshThemeIcons() {
    const t = document.documentElement.getAttribute("data-theme") || "dark";
    const moon = document.getElementById("theme-icon-moon");
    const sun = document.getElementById("theme-icon-sun");
    if (moon && sun) {
      moon.style.display = t === "dark" ? "none" : "inline";
      sun.style.display = t === "dark" ? "inline" : "none";
    }
  }

  // El listener vive en document, así que sobrevive a cualquier swap.
  document.addEventListener("click", (e) => {
    const btn = e.target.closest("#theme-toggle");
    if (!btn) return;
    const cur = document.documentElement.getAttribute("data-theme") || "dark";
    const next = cur === "dark" ? "light" : "dark";
    try { localStorage.setItem(STORAGE_THEME, next); } catch (_) {}
    applyTheme(next);
  });

  // ============ Indicador de progreso (htmx) ============
  document.body && bindProgress();
  /**
   * Conecta los eventos `htmx:beforeRequest` / `htmx:afterRequest` al
   * indicador de progreso superior (`#hx-page-progress`). Se llama una
   * sola vez al cargar el script; el listener vive en `document.body` y
   * sobrevive a los swaps de HTMX.
   */
  function bindProgress() {
    const target = document.body;
    target.addEventListener("htmx:beforeRequest", () => {
      const p = document.getElementById("hx-page-progress");
      if (p) { p.classList.remove("done"); p.classList.add("active"); }
    });
    target.addEventListener("htmx:afterRequest", () => {
      const p = document.getElementById("hx-page-progress");
      if (!p) return;
      p.classList.remove("active");
      p.classList.add("done");
      setTimeout(() => p.classList.remove("done"), 500);
    });
  }

  // ============ Dispatcher ============
  /**
   * Núcleo del sistema de páginas. Lee el atributo `data-page` de
   * `#hx-root`, ejecuta los teardowns del módulo anterior y llama a
   * `init()` del módulo registrado en `window.MENDI_PAGES[page]`.
   * Se invoca en `DOMContentLoaded` y en cada `htmx:afterSwap` del
   * contenedor principal.
   */
  function dispatch() {
    refreshThemeIcons();
    const root = document.getElementById("hx-root");
    if (!root) return;
    const page = root.getAttribute("data-page");
    runTeardowns();
    const mod = window.MENDI_PAGES[page];
    if (mod && typeof mod.init === "function") {
      try { mod.init(); } catch (err) { console.error("[mendi] init error", page, err); }
    }
  }

  document.addEventListener("DOMContentLoaded", dispatch);
  document.body && document.body.addEventListener("htmx:afterSwap", (evt) => {
    // Solo nos interesa el swap principal del shell.
    if (evt.detail && evt.detail.target && evt.detail.target.id === "hx-root") {
      dispatch();
    }
  });

  // ============ utils ============
  /**
   * Escapa los caracteres especiales HTML de una cadena para interpolación
   * segura en innerHTML. Expuesta en `window.MENDI_UTIL.escapeHtml`.
   * @param {*} s  Valor a escapar (se convierte a string).
   * @returns {string}
   */
  function escapeHtml(s) {
    return String(s)
      .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
  }
  window.MENDI_UTIL = { escapeHtml };

  // Convierte un ISO UTC ("2025-06-30T23:30:00Z") a fecha local del navegador
  // con formato "30 jun 2025". Fallback al string original si falla.
  const MONTH_ES = ["ene","feb","mar","abr","may","jun","jul","ago","sep","oct","nov","dic"];
  /**
   * Convierte un string ISO UTC (p.ej. `"2025-06-30T23:30:00Z"`) a fecha
   * local del navegador con formato `"30 jun 2025"`. Si la conversión
   * falla devuelve el string original. Expuesta en
   * `window.MENDI_UTIL.fmtDateLocal`.
   * @param {string} iso
   * @returns {string}
   */
  function fmtDateLocal(iso) {
    if (!iso) return "";
    try {
      const d = new Date(iso);
      return `${String(d.getDate()).padStart(2,"0")} ${MONTH_ES[d.getMonth()]} ${d.getFullYear()}`;
    } catch (_) { return iso; }
  }
  window.MENDI_UTIL.fmtDateLocal = fmtDateLocal;

  // Devuelve "YYYY-MM-DD" en hora local (para agrupar por día en calendarios).
  /**
   * Devuelve `"YYYY-MM-DD"` en hora local del navegador a partir de un
   * string ISO. Se usa para agrupar actividades por día local en los
   * calendarios heatmap, evitando que rutas de tarde aparezcan en el día
   * siguiente por el desfase UTC. Expuesta en
   * `window.MENDI_UTIL.localDateKey`.
   * @param {string} iso
   * @returns {string}
   */
  function localDateKey(iso) {
    if (!iso) return "";
    try {
      const d = new Date(iso);
      const y = d.getFullYear();
      const m = String(d.getMonth() + 1).padStart(2, "0");
      const day = String(d.getDate()).padStart(2, "0");
      return `${y}-${m}-${day}`;
    } catch (_) { return iso.slice(0, 10); }
  }
  window.MENDI_UTIL.localDateKey = localDateKey;

  /**
   * Lee el valor de una CSS custom property del elemento raíz.
   * @param {string} name  Nombre de la variable, p.ej. `"--accent"`.
   * @returns {string}
   */
  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  // ============ Capas de mapa compartidas ============
  const STORAGE_MAP_LAYER = "mendi.map.layer";

  /**
   * Devuelve el objeto con todas las capas base disponibles y la clave
   * de la capa activa persistida en localStorage.
   * Expuesto como `window.MENDI_MAP_LAYERS` para que los módulos de
   * mapa (resumen, rutas, detalle) lo consuman sin duplicar URLs.
   */
  function buildMapLayers() {
    const attr_carto = "© <a href='https://www.openstreetmap.org/copyright'>OpenStreetMap</a> © <a href='https://carto.com/'>CartoDB</a>";
    const attr_osm   = "© <a href='https://www.openstreetmap.org/copyright'>OpenStreetMap</a> contributors";
    const attr_topo  = "© <a href='https://opentopomap.org'>OpenTopoMap</a> (<a href='https://creativecommons.org/licenses/by-sa/3.0/'>CC-BY-SA</a>)";
    const attr_esri  = "Tiles © <a href='https://www.esri.com/'>Esri</a> &mdash; Source: Esri, USGS, NOAA";

    // Crear instancias nuevas cada vez — una capa Leaflet no puede
    // pertenecer a más de un mapa simultáneamente.
    const layers = {
      "CartoDB Claro":   L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",   { attribution: attr_carto, maxZoom: 19 }),
      "CartoDB Voyager": L.tileLayer("https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png", { attribution: attr_carto, maxZoom: 19 }),
      "OpenStreetMap":   L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png",                   { attribution: attr_osm,   maxZoom: 19 }),
      "OpenTopoMap":     L.tileLayer("https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",                 { attribution: attr_topo,  maxZoom: 19, maxNativeZoom: 17 }),
      "Satélite (ESRI)": L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", { attribution: attr_esri, maxZoom: 19 }),
    };

    let saved = "Satélite (ESRI)";
    try {
      const stored = localStorage.getItem(STORAGE_MAP_LAYER);
      if (stored) {
        saved = stored;
      } else {
        // Primera visita: satélite por defecto y se persiste para que las
        // siguientes cargas (y el resto de mapas) mantengan el criterio.
        // A partir de aquí manda la elección del usuario (baselayerchange).
        localStorage.setItem(STORAGE_MAP_LAYER, saved);
      }
    } catch (_) {}
    if (!layers[saved]) saved = "Satélite (ESRI)";

    return { layers, active: saved };
  }

  /**
   * Añade el control de capas nativo de Leaflet al mapa dado,
   * restaura la capa guardada y persiste los cambios en localStorage.
   * Devuelve la instancia del control para poder destruirla en teardown.
   * @param {L.Map} map
   * @returns {L.Control.Layers}
   */
  function addLayerControl(map) {
    const { layers, active } = buildMapLayers();
    layers[active].addTo(map);

    const ctrl = L.control.layers(layers, {}, {
      position: "topright",
      collapsed: true,
    }).addTo(map);

    map.on("baselayerchange", (e) => {
      try { localStorage.setItem(STORAGE_MAP_LAYER, e.name); } catch (_) {}
      // Notificar al resto de mapas abiertos (p.ej. mini-mapa featured)
      document.dispatchEvent(new CustomEvent("mendi:layerchange", { detail: { name: e.name } }));
    });

    return ctrl;
  }

  window.MENDI_MAP = { buildMapLayers, addLayerControl, STORAGE_MAP_LAYER };

  // ============================================================
  //  Página "resumen"
  // ============================================================
  let map = null;
  let mapLayerCtrl = null;
  let currentProfileIdx = -1;
  let rotationTimer = null;

  /**
   * Oculta el overlay de carga del mapa resumen (`#map-loading`) con una
   * transición CSS de 400 ms antes de retirar el nodo del flujo.
   */
  function hideMapLoading() {
    const ov = document.getElementById("map-loading");
    if (!ov) return;
    ov.classList.add("hidden");
    setTimeout(() => { ov.style.display = "none"; }, 400);
  }

  /**
   * Punto de entrada para el mapa de la vista Resumen. Espera a que el
   * contenedor `#map` tenga dimensiones reales mediante `ResizeObserver`
   * antes de llamar a `_buildMap`, evitando que Leaflet cachee altura 0
   * tras un swap de HTMX. Registra el teardown del observer en
   * `window.MENDI_TEARDOWN`.
   */
  function initMap() {
    const el = document.getElementById("map");
    if (!el || typeof L === "undefined") {
      hideMapLoading();
      return;
    }

    const ro = new ResizeObserver((entries, observer) => {
      const h = entries[0].contentRect.height;
      if (h < 10) return;
      observer.disconnect();
      _buildMap(el);
    });
    ro.observe(el);
    window.MENDI_TEARDOWN.push(() => { try { ro.disconnect(); } catch (_) {} });

    const fallback = setTimeout(() => { ro.disconnect(); _buildMap(el); }, 800);
    window.MENDI_TEARDOWN.push(() => clearTimeout(fallback));
  }

  /**
   * Construye el mapa Leaflet vacío con teselas y teardown,
   * luego fetcha los marcadores vía /api/rutas.
   */
  function _buildMap(el) {
    if (map) return;

    map = L.map(el, {
      zoomControl: true,
      attributionControl: true,
      scrollWheelZoom: true,
    }).setView([42.78, -0.85], 6);

    mapLayerCtrl = addLayerControl(map);
    requestAnimationFrame(() => { try { map && map.invalidateSize(); } catch (_) {} });

    // Crear el clusterGroup una sola vez; _fetchMapMarkers añade capas progresivamente
    const clusterGroup = L.markerClusterGroup({
      maxClusterRadius: 40,
      showCoverageOnHover: false,
      iconCreateFunction(cluster) {
        const count = cluster.getChildCount();
        const color = cssVar("--accent") || "#B5D17A";
        const bg = cssVar("--surface") || "#10151E";
        const size = count < 10 ? 32 : count < 100 ? 38 : 44;
        return L.divIcon({
          html: `<div style="width:${size}px;height:${size}px;border-radius:50%;background:${bg};border:2px solid ${color};display:flex;align-items:center;justify-content:center;font-family:'IBM Plex Mono',monospace;font-size:11px;font-weight:600;color:${color};">${count}</div>`,
          className: "",
          iconSize: [size, size],
          iconAnchor: [size / 2, size / 2],
        });
      },
    });
    map.addLayer(clusterGroup);

    window.MENDI_TEARDOWN.push(() => {
      try { if (map) map.remove(); } catch (_) {}
      map = null;
      mapLayerCtrl = null;
    });

    _fetchMapMarkers(clusterGroup);
  }

  /**
   * Obtiene los marcadores del mapa desde /api/rutas y los pinta.
   * Usa limit=500 sin filtros para obtener todas las rutas del usuario.
   */
  async function _fetchMapMarkers(clusterGroup) {
    try {
      let offset = 0;
      const LIMIT = 200;
      let hasMore = true;
      while (hasMore) {
        const res = await fetch(`/api/rutas/markers?offset=${offset}&limit=${LIMIT}`);
        if (!res.ok) throw new Error("markers http " + res.status);
        const json = await res.json();
        const routes = json.items || [];
        hasMore = !!json.hasMore;
        offset += routes.length;

        if (!map) return;

        const lvlColors = { easy: "#7DAFC9", moderate: "#B5D17A", hard: "#E8B86D", "very-hard": "#E47862" };
        const bounds = [];

        const CELL = 667;
        const cells = new Map();
        routes.forEach(r => {
          if (!r.lat || !r.lon) return;
          const key = `${Math.round(r.lat * CELL)},${Math.round(r.lon * CELL)}`;
          if (!cells.has(key)) cells.set(key, []);
          cells.get(key).push(r);
        });

        cells.forEach((group) => {
          const ref = group.reduce((a, b) => (b.km > a.km ? b : a));
          const color = lvlColors[ref.level] || lvlColors.moderate;
          const radius = 6 + (ref.km || 0) * 0.6;

          let popupHtml;
          if (group.length === 1) {
            const r = group[0];
            popupHtml =
              `<div style="font-family:'IBM Plex Sans',sans-serif;min-width:190px;">` +
              `<div style="margin-bottom:6px;">` +
              `<a href="/rutas/${r.id}" style="font-family:Fraunces,serif;font-size:14px;font-weight:500;color:inherit;text-decoration:none;border-bottom:1px solid currentColor;">${escapeHtml(r.name)}</a>` +
              `</div>` +
              `<div style="font-family:'IBM Plex Mono',monospace;font-size:11px;opacity:0.75;line-height:1.7;">` +
              `<div>${(r.km || 0).toFixed(2)} km &middot; ${r.gain || 0} m+</div>` +
              `<div>dificultad ${(r.score || 0).toFixed(1)} &middot; ${escapeHtml(fmtDateLocal(r.started_at_iso) || "")}</div>` +
              `</div></div>`;
          } else {
            const rows = group
              .slice()
              .sort((a, b) => (b.started_at_iso || "").localeCompare(a.started_at_iso || ""))
              .map(r => {
                const dot = `<span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:${lvlColors[r.level] || lvlColors.moderate};margin-right:5px;flex-shrink:0;"></span>`;
                return (
                  `<li style="display:flex;align-items:baseline;gap:4px;padding:5px 0;border-bottom:1px dashed rgba(128,128,128,0.25);">` +
                  `<span style="display:flex;align-items:center;flex:1;min-width:0;">` +
                  `${dot}` +
                  `<a href="/rutas/${r.id}" style="font-family:Fraunces,serif;font-size:13px;font-weight:500;color:inherit;text-decoration:none;border-bottom:1px solid currentColor;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${escapeHtml(r.name)}</a>` +
                  `</span>` +
                  `<span style="font-family:'IBM Plex Mono',monospace;font-size:10px;opacity:0.65;white-space:nowrap;flex-shrink:0;">${(r.km || 0).toFixed(1)} km</span>` +
                  `</li>`
                );
              }).join("");
            popupHtml =
              `<div style="font-family:'IBM Plex Sans',sans-serif;min-width:220px;max-width:280px;">` +
              `<div style="font-family:'IBM Plex Mono',monospace;font-size:10px;text-transform:uppercase;letter-spacing:0.12em;opacity:0.5;margin-bottom:6px;">${group.length} rutas en esta zona</div>` +
              `<ul style="list-style:none;margin:0;padding:0;max-height:220px;overflow-y:auto;">${rows}</ul>` +
              `</div>`;
          }

          const marker = L.circleMarker([ref.lat, ref.lon], {
            radius, color, fillColor: color, fillOpacity: 0.45, weight: 2,
          });
          marker.bindPopup(popupHtml, { maxWidth: 300 });
          clusterGroup.addLayer(marker);
          bounds.push([ref.lat, ref.lon]);
        });

        // Ajustar bounds solo en el primer lote
        if (offset === routes.length && bounds.length >= 2) {
          map.fitBounds(bounds, { padding: [50, 50], maxZoom: 9 });
        } else if (offset === routes.length && bounds.length === 1) {
          map.setView(bounds[0], 11);
        }
      }

      hideMapLoading();
    } catch (_) {
      hideMapLoading();
    }
  }

  // ============ Monthly chart ============
  /**
   * Renderiza el gráfico SVG de kilómetros por mes (`#chart-monthly`).
   * Dibuja área degradada, línea y puntos con etiquetas para los últimos
   * 14 meses. Los datos vienen de `window.MENDI.months`. Expuesta como
   * `window.renderMonthlyChart` para re-pintarse al cambiar el tema.
   */
  function renderMonthlyChart() {
    const svg = document.getElementById("chart-monthly");
    if (!svg) return;
    const MENDI = window.MENDI || {};
    const months = MENDI.months || [];
    if (!months.length) { svg.innerHTML = ""; return; }

    const W = 600, H = 180;
    const PAD_L = 36, PAD_R = 16, PAD_T = 18, PAD_B = 26;
    const innerW = W - PAD_L - PAD_R;
    const innerH = H - PAD_T - PAD_B;
    // fix: maxV dinamico sin minimo artificial de 16
    const maxV = Math.max(...months.map(m => m.v), 1);
    const xStep = innerW / Math.max(1, months.length - 1);

    const cAccent = cssVar("--accent");
    const cBorder = cssVar("--border");
    const cDim = cssVar("--text-dim");
    const cText = cssVar("--text");
    const cBg = cssVar("--surface");

    let html = "";

    // fix: guias del eje Y dinamicas basadas en maxV
    const gridStep = maxV <= 10 ? 2 : maxV <= 30 ? 5 : maxV <= 100 ? 20 : 50;
    for (let g = 0; g <= maxV; g += gridStep) {
      const y = PAD_T + innerH - (g / maxV) * innerH;
      html += `<line x1="${PAD_L}" x2="${W - PAD_R}" y1="${y.toFixed(1)}" y2="${y.toFixed(1)}" stroke="${cBorder}" stroke-width="0.8" stroke-dasharray="2 4"/>`;
      html += `<text x="${PAD_L - 8}" y="${(y + 3).toFixed(1)}" text-anchor="end" font-family="IBM Plex Mono" font-size="9" fill="${cDim}">${g}</text>`;
    }

    let path = "", area = "";
    months.forEach((p, i) => {
      const x = PAD_L + i * xStep;
      const y = PAD_T + innerH - (p.v / maxV) * innerH;
      path += (i === 0 ? `M ${x} ${y}` : ` L ${x} ${y}`);
      if (i === 0) area = `M ${x} ${PAD_T + innerH} L ${x} ${y}`;
      else area += ` L ${x} ${y}`;
    });
    area += ` L ${PAD_L + (months.length - 1) * xStep} ${PAD_T + innerH} Z`;

    // fix: ID de gradiente fijo, sin Math.random()
    const gradId = "m-grad-monthly";
    html += `<defs><linearGradient id="${gradId}" x1="0" x2="0" y1="0" y2="1">
        <stop offset="0%" stop-color="${cAccent}" stop-opacity="0.35"/>
        <stop offset="100%" stop-color="${cAccent}" stop-opacity="0"/>
      </linearGradient></defs>`;
    html += `<path d="${area}" fill="url(#${gradId})"/>`;
    html += `<path d="${path}" fill="none" stroke="${cAccent}" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/>`;

    months.forEach((p, i) => {
      const x = PAD_L + i * xStep;
      const y = PAD_T + innerH - (p.v / maxV) * innerH;
      if (p.v > 0) {
        html += `<circle cx="${x}" cy="${y}" r="3.5" fill="${cBg}" stroke="${cAccent}" stroke-width="1.6"/>`;
        html += `<text x="${x}" y="${y - 9}" text-anchor="middle" font-family="IBM Plex Mono" font-size="9" fill="${cText}">${p.v.toFixed(1)}</text>`;
      }
      // fix: etiquetas X en todos los meses, saltando las impares solo si hay
      // mas de 8 meses para evitar solapamiento
      if (months.length <= 8 || i % 2 === 0) {
        html += `<text x="${x}" y="${H - 8}" text-anchor="middle" font-family="IBM Plex Mono" font-size="9" fill="${cDim}" letter-spacing="0.05em">${p.m}</text>`;
      }
    });

    svg.innerHTML = html;
  }
  window.renderMonthlyChart = renderMonthlyChart;

  // ============ Rotación de silueta hero ============
  /**
   * Muestra la silueta de elevación del hero correspondiente al índice
   * `idx` dentro de `window.MENDI.heroProfiles`. Aplica una transición
   * de fundido (clase `fading`) antes de actualizar los paths SVG y la
   * etiqueta de nombre.
   * @param {number} idx  Índice en el array `heroProfiles`.
   */
  function setHeroProfile(idx) {
    const MENDI = window.MENDI || {};
    const profiles = MENDI.heroProfiles || [];
    if (!profiles.length) return;
    currentProfileIdx = idx;
    const p = profiles[idx];
    const lineEl = document.getElementById("hero-elev-line");
    const areaEl = document.getElementById("hero-elev-area");
    const tagEl = document.getElementById("hero-route-tag");
    if (!lineEl || !areaEl) return;

    lineEl.classList.add("fading");
    areaEl.classList.add("fading");
    if (tagEl) tagEl.style.opacity = "0";

    setTimeout(() => {
      lineEl.setAttribute("d", p.line);
      areaEl.setAttribute("d", p.area);
      if (tagEl) tagEl.textContent = `silueta · ${p.name}`;
      requestAnimationFrame(() => {
        lineEl.classList.remove("fading");
        areaEl.classList.remove("fading");
        if (tagEl) tagEl.style.opacity = "1";
      });
    }, 600);
  }

  /**
   * Selecciona aleatoriamente un perfil distinto al actual y llama a
   * `setHeroProfile`. Garantiza que nunca se repite el mismo índice
   * consecutivo.
   */
  function rotateProfile() {
    const profiles = (window.MENDI || {}).heroProfiles || [];
    if (profiles.length < 2) return;
    let next = currentProfileIdx;
    while (next === currentProfileIdx) {
      next = Math.floor(Math.random() * profiles.length);
    }
    setHeroProfile(next);
  }

  /**
   * Cancela el timer de rotación existente y, si `ms > 0`, crea uno
   * nuevo con el intervalo indicado.
   * @param {number} ms  Milisegundos entre rotaciones. `0` desactiva.
   */
  function setRotationInterval(ms) {
    if (rotationTimer) { clearInterval(rotationTimer); rotationTimer = null; }
    if (ms > 0) rotationTimer = setInterval(rotateProfile, ms);
  }

  /**
   * Inicializa la rotación automática de siluetas en el hero:
   * - Muestra un perfil aleatorio inicial.
   * - Lee el intervalo guardado en `localStorage` y lo aplica.
   * - Conecta el `<select>` de intervalo si existe.
   * - Registra el teardown del timer en `window.MENDI_TEARDOWN`.
   */
  function initRotation() {
    const profiles = (window.MENDI || {}).heroProfiles || [];
    const sel = document.getElementById("rotation-select");
    if (!profiles.length) {
      const tag = document.getElementById("hero-route-tag");
      if (tag) tag.style.display = "none";
      return;
    }
    currentProfileIdx = -1;
    const startIdx = Math.floor(Math.random() * profiles.length);
    setHeroProfile(startIdx);

    let savedInterval = "15000";
    try { savedInterval = localStorage.getItem(STORAGE_INTERVAL) || "15000"; } catch (_) {}
    if (sel) {
      sel.value = savedInterval;
      if (profiles.length < 2) {
        sel.disabled = true;
      } else {
        setRotationInterval(parseInt(savedInterval, 10));
      }
      sel.addEventListener("change", (e) => {
        const v = e.target.value;
        try { localStorage.setItem(STORAGE_INTERVAL, v); } catch (_) {}
        setRotationInterval(parseInt(v, 10));
      });
    } else {
      setRotationInterval(parseInt(savedInterval, 10));
    }

    window.MENDI_TEARDOWN.push(() => {
      if (rotationTimer) { clearInterval(rotationTimer); rotationTimer = null; }
      currentProfileIdx = -1;
    });
  }

  // ============ Dropzone (importar) ============
  /**
   * Inicializa la zona de drag & drop de la vista Importar (`#dropzone`):
   * - Abre el selector de archivos al hacer clic fuera de la lista.
   * - Actualiza la lista de archivos y el resumen de tamaño total.
   * - Gestiona los eventos `dragenter`, `dragover`, `dragleave` y `drop`.
   * - Habilita/deshabilita el botón de envío según haya archivos.
   */
  function initDropzone() {
    const dz = document.getElementById("dropzone");
    if (!dz) return;
    const input = dz.querySelector("input[type=file]");
    const list = dz.querySelector(".filelist");
    const submit = document.getElementById("dz-submit");

    const meta = document.getElementById("filelist-meta");

    function refreshList() {
      if (!input.files || !input.files.length) {
        list.innerHTML = "";
        if (meta) { meta.style.display = "none"; meta.textContent = ""; }
        if (submit) submit.disabled = true;
        return;
      }
      const files = Array.from(input.files);
      const totalKB = files.reduce((s, f) => s + f.size, 0) / 1024;
      const totalLabel = totalKB > 1024
        ? `${(totalKB / 1024).toFixed(1).replace(".", ",")} MB`
        : `${totalKB.toFixed(0)} KB`;
      if (meta) {
        meta.style.display = "block";
        meta.textContent = `${files.length} archivo${files.length === 1 ? "" : "s"} · ${totalLabel}`;
      }
      list.innerHTML = files
        .map(f => `<span class="fname">📁 ${escapeHtml(f.name)} · ${(f.size/1024).toFixed(0)} KB</span>`)
        .join("");
      if (submit) submit.disabled = false;
    }

    dz.addEventListener("click", (e) => {
      if (e.target.tagName === "BUTTON") return;
      // No abrir el selector si el clic está dentro de la lista de archivos
      // (el usuario está intentando hacer scroll por la lista).
      if (e.target.closest(".filelist-wrap")) return;
      input.click();
    });
    input.addEventListener("change", refreshList);
    ["dragenter", "dragover"].forEach(ev =>
      dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("drag"); }));
    ["dragleave", "drop"].forEach(ev =>
      dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("drag"); }));
    dz.addEventListener("drop", (e) => {
      e.preventDefault();
      input.files = e.dataTransfer.files;
      refreshList();
    });
  }

  // ============ Registro de páginas ============
  window.MENDI_PAGES.resumen = {
    init() {
      // Aplicamos el tema en cada init (refresca iconos sin perder estado).
      let saved = "dark";
      try { saved = localStorage.getItem(STORAGE_THEME) || "dark"; } catch (_) {}
      applyTheme(saved);
      initMap();
      renderMonthlyChart();
      initRotation();
      // Filas clickables: navegar al detalle al hacer click en cualquier celda
      document.querySelectorAll("tr.recent-route-row").forEach(tr => {
        tr.addEventListener("click", () => { window.location.href = tr.dataset.href; });
      });
      // Convertir fechas de la tabla a hora local del navegador
      document.querySelectorAll("td[data-iso]").forEach(td => {
        const iso = td.dataset.iso;
        if (iso) td.textContent = fmtDateLocal(iso);
      });
    }
  };

  window.MENDI_PAGES.importar = {
    init() {
      let saved = "dark";
      try { saved = localStorage.getItem(STORAGE_THEME) || "dark"; } catch (_) {}
      applyTheme(saved);
      initDropzone();
    }
  };

  // página "analisis" se registra en /static/js/analisis.js
})();
