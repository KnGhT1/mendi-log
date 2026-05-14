// ============ mendi.log · rutas ============
// Filtros, búsqueda, ordenación y paginación: TODO server-side vía /api/rutas.
// El cliente sólo construye filas a partir del JSON devuelto y gestiona el
// pool de rotación del featured (max 30 rutas precargadas en window.MENDI_RUTAS).
//
// Compatibilidad HTMX hx-boost: todo el setup está en init(), invocada por el
// dispatcher de app.js al detectar data-page="rutas". Cada timer, mapa y
// listener global registra su teardown en window.MENDI_TEARDOWN para limpiar
// estado al navegar a otra página.

(function () {
  "use strict";

  window.MENDI_PAGES = window.MENDI_PAGES || {};
  window.MENDI_TEARDOWN = window.MENDI_TEARDOWN || [];

  function $(id) { return document.getElementById(id); }

  /**
   * Devuelve el color hexadecimal asociado a un nivel de dificultad.
   * @param {"easy"|"moderate"|"hard"|"very-hard"} level
   * @returns {string}
   */
  function levelColor(level) {
    return ({
      easy: "#7DAFC9",
      moderate: "#B5D17A",
      hard: "#E8B86D",
      "very-hard": "#E47862",
    })[level] || "#B5D17A";
  }

  /**
   * Escapa los caracteres especiales de una cadena para usarla de forma
   * segura dentro de un constructor `RegExp`.
   * @param {string} s
   * @returns {string}
   */
  function escapeRegex(s) {
    return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }
  /**
   * Escapa los caracteres especiales HTML de una cadena para interpolación
   * segura en innerHTML.
   * @param {*} s
   * @returns {string}
   */
  function escapeHTML(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }
  /**
   * Envuelve las coincidencias de `q` en `text` con `<mark class="search-hl">`
   * para resaltar los términos de búsqueda. El texto se escapa antes de
   * aplicar el reemplazo para evitar XSS.
   * @param {string} text  Texto original.
   * @param {string} q     Término de búsqueda (puede estar vacío).
   * @returns {string}  HTML seguro con marcas de resaltado.
   */
  function highlight(text, q) {
    const safe = escapeHTML(text || "");
    if (!q) return safe;
    const re = new RegExp(`(${escapeRegex(q)})`, "ig");
    return safe.replace(re, '<mark class="search-hl">$1</mark>');
  }

  /**
   * Formatea una fecha ISO a `"DD mes YYYY"` en hora local del navegador.
   * Delega en `window.MENDI_UTIL.fmtDateLocal` si está disponible;
   * si no, devuelve los primeros 10 caracteres del ISO como fallback.
   * @param {string} iso
   * @returns {string}
   */
  function fmtDateLocal(iso) {
    return (window.MENDI_UTIL && window.MENDI_UTIL.fmtDateLocal)
      ? window.MENDI_UTIL.fmtDateLocal(iso)
      : (iso || "").slice(0, 10);
  }

  function init() {
    const MENDI = window.MENDI_RUTAS || { total: 0, featuredId: null, rotationPool: [], todayISO: null };
    const ROTATION_POOL = Array.isArray(MENDI.rotationPool) ? MENDI.rotationPool : [];
    const ROTATION_BY_ID = new Map(ROTATION_POOL.map(r => [r.id, r]));

    const STORAGE_FEAT_INTERVAL = "mendi.rutas.featured.interval";
    const STORAGE_VIEW = "mendi.rutas.view";
    const PAGE_SIZE = 10;

    // Vista activa: "list" | "grid". Se persiste en localStorage.
    let currentView = "list";
    try { currentView = localStorage.getItem(STORAGE_VIEW) || "list"; } catch (_) {}
    if (currentView !== "list" && currentView !== "grid") currentView = "list";

    /**
     * Aplica la vista `view` al contenedor de rutas: alterna las clases
     * CSS `routes-list` / `routes-grid`, marca el botón activo y persiste
     * la preferencia en `localStorage`.
     * @param {"list"|"grid"} view
     */
    function applyView(view) {
      currentView = view;
      try { localStorage.setItem(STORAGE_VIEW, view); } catch (_) {}
      const list = $("routes-list");
      if (list) {
        list.classList.toggle("routes-list", view === "list");
        list.classList.toggle("routes-grid", view === "grid");
      }
      document.querySelectorAll(".view-switch button").forEach(b => {
        b.classList.toggle("active", b.dataset.view === view);
      });
    }

    /**
     * Devuelve el tema activo (`"dark"` | `"light"`) leyendo el atributo
     * `data-theme` del elemento raíz.
     * @returns {string}
     */
    function currentTheme() {
      return document.documentElement.getAttribute("data-theme") || "dark";
    }

    // ============ FEATURED MAP ============
    let featuredMap = null;
    let featuredDark = null;
    let featuredLight = null;
    let featuredMarker = null;

    /**
     * Punto de entrada para el mini-mapa del featured. Espera a que el
     * contenedor `#featured-map` tenga dimensiones reales mediante
     * `ResizeObserver` antes de llamar a `_buildFeaturedMap`, evitando
     * que Leaflet cachee altura 0 tras un swap de HTMX. Registra el
     * teardown del observer en `window.MENDI_TEARDOWN`.
     */
    function initFeaturedMap() {
      const el = $("featured-map");
      if (!el || !ROTATION_POOL.length || typeof L === "undefined") return;

      const initial = ROTATION_BY_ID.get(MENDI.featuredId) || ROTATION_POOL[0];

      // Esperar a que el contenedor flex haya resuelto su altura antes de
      // inicializar Leaflet. Con height:100% sobre un padre flex:1 1 auto,
      // si Leaflet mide antes de que el layout esté listo cachea altura 0
      // y el mapa queda en blanco. ResizeObserver dispara en cuanto el
      // contenedor tiene dimensiones reales.
      const ro = new ResizeObserver((entries, observer) => {
        const h = entries[0].contentRect.height;
        if (h < 10) return; // aún sin altura
        observer.disconnect();
        _buildFeaturedMap(el, initial);
      });
      ro.observe(el);
      window.MENDI_TEARDOWN.push(() => { try { ro.disconnect(); } catch (_) {} });

      // Fallback: si ResizeObserver no dispara en 800ms, inicializamos igualmente
      const fallback = setTimeout(() => { ro.disconnect(); _buildFeaturedMap(el, initial); }, 800);
      window.MENDI_TEARDOWN.push(() => clearTimeout(fallback));
    }

    /**
     * Construye el mini-mapa Leaflet del featured: capas de teselas
     * oscuras/claras (sin controles ni interacción), un marcador circular
     * coloreado por nivel y un `invalidateSize` diferido. Registra el
     * teardown en `window.MENDI_TEARDOWN`.
     * @param {HTMLElement} el       Contenedor del mapa.
     * @param {{lat:number, lon:number, level:string}} initial  Ruta inicial.
     */
    function _buildFeaturedMap(el, initial) {
      if (featuredMap) return; // ya inicializado

      featuredMap = L.map(el, {
        zoomControl: false,
        attributionControl: false,
        scrollWheelZoom: false,
        dragging: false,
        doubleClickZoom: false,
        boxZoom: false,
        keyboard: false,
        touchZoom: false,
      }).setView([initial.lat, initial.lon], 11);

      featuredDark = L.tileLayer(
        "https://{s}.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}{r}.png",
        { maxZoom: 18 }
      );
      featuredLight = L.tileLayer(
        "https://{s}.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}{r}.png",
        { maxZoom: 18 }
      );

      featuredMarker = L.circleMarker([initial.lat, initial.lon], {
        radius: 8,
        color: levelColor(initial.level),
        fillColor: levelColor(initial.level),
        fillOpacity: 0.55,
        weight: 2,
      }).addTo(featuredMap);

      syncFeaturedTiles();

      // Un único invalidateSize tras el primer pintado — el ResizeObserver
      // ya garantizó que el contenedor tiene dimensiones, así que solo
      // necesitamos un tick para que Leaflet confirme el tamaño final.
      requestAnimationFrame(() => {
        try { featuredMap && featuredMap.invalidateSize(); } catch (_) {}
      });

      window.MENDI_TEARDOWN.push(() => {
        try { if (featuredMap) featuredMap.remove(); } catch (_) {}
        featuredMap = featuredDark = featuredLight = featuredMarker = null;
      });
    }

    /**
     * Intercambia las capas de teselas del mini-mapa según el tema activo
     * (`dark` / `light`).
     */
    function syncFeaturedTiles() {
      if (!featuredMap) return;
      if (currentTheme() === "dark") {
        if (featuredMap.hasLayer(featuredLight)) featuredMap.removeLayer(featuredLight);
        if (!featuredMap.hasLayer(featuredDark)) featuredDark.addTo(featuredMap);
      } else {
        if (featuredMap.hasLayer(featuredDark)) featuredMap.removeLayer(featuredDark);
        if (!featuredMap.hasLayer(featuredLight)) featuredLight.addTo(featuredMap);
      }
    }

    /**
     * Mueve el marcador del mini-mapa a las coordenadas de `route` y hace
     * pan animado hacia ellas.
     * @param {{lat:number, lon:number, level:string}} route
     */
    function updateFeaturedMap(route) {
      if (!featuredMap || !featuredMarker || !route) return;
      const c = levelColor(route.level);
      featuredMarker.setLatLng([route.lat, route.lon]);
      featuredMarker.setStyle({ color: c, fillColor: c });
      featuredMap.panTo([route.lat, route.lon], { animate: true, duration: 0.6 });
    }

    // ============ FEATURED ROTATION ============
    let currentFeaturedIdx = -1;
    let featuredRotationTimer = null;

    /**
     * Actualiza la tarjeta featured completa con los datos de la ruta en
     * la posición `idx` del pool de rotación: nombre, región, fecha,
     * métricas, perfil SVG, enlace de detalle y mini-mapa. Aplica una
     * transición de fundido (clase `fading`) antes de actualizar el DOM.
     * @param {number} idx  Índice en `ROTATION_POOL`.
     */
    function setFeaturedRoute(idx) {
      if (!ROTATION_POOL.length) return;
      if (idx < 0 || idx >= ROTATION_POOL.length) return;
      currentFeaturedIdx = idx;
      const r = ROTATION_POOL[idx];

      const card = $("featured-card");
      const lineEl = $("featured-elev-line");
      const areaEl = $("featured-elev-area");
      const groupEl = $("featured-elev-group");

      if (card) card.classList.add("fading");
      if (lineEl) lineEl.classList.add("fading");
      if (areaEl) areaEl.classList.add("fading");

      setTimeout(() => {
        const meta = $("featured-meta");
        if (meta) meta.textContent = r.date;
        const regionEl = $("featured-region");
        if (regionEl) regionEl.textContent = r.region;
        const nameEl = $("featured-name");
        if (nameEl) nameEl.textContent = r.name;
        const originWrap = $("featured-origin-wrap");
        const originEl = $("featured-origin");
        const hasOrigin = r.origin && r.origin !== "—";
        if (originEl) originEl.textContent = r.origin || "";
        if (originWrap) originWrap.style.display = hasOrigin ? "" : "none";

        const kmEl = $("featured-km");        if (kmEl) kmEl.textContent = r.kmStr;
        const gainEl = $("featured-gain");    if (gainEl) gainEl.textContent = r.gainStr;
        const timeEl = $("featured-time");    if (timeEl) timeEl.textContent = r.time;
        const scoreEl = $("featured-score");
        if (scoreEl) {
          scoreEl.textContent = r.scoreStr;
          scoreEl.style.color = `var(--${r.level})`;
        }

        if (lineEl) lineEl.setAttribute("d", r.line);
        if (areaEl) areaEl.setAttribute("d", r.area);
        if (groupEl) groupEl.style.color = `var(--${r.level})`;
        const rangeEl = $("featured-elev-range");
        if (rangeEl) rangeEl.textContent = `PERFIL · ${r.eleMinStr}–${r.eleMaxStr} m`;
        const summaryEl = $("featured-elev-summary");
        if (summaryEl) summaryEl.textContent = `↗ ${r.gainStr} m · ${r.kmStr} km`;

        const linkDetail = $("featured-link-detail");
        if (linkDetail) linkDetail.setAttribute("href", `/rutas/${r.id}`);
        const linkGpx = $("featured-link-gpx");
        if (linkGpx) linkGpx.setAttribute("href", `/rutas/${r.id}/gpx`);

        updateFeaturedMap(r);

        requestAnimationFrame(() => {
          if (card) card.classList.remove("fading");
          if (lineEl) lineEl.classList.remove("fading");
          if (areaEl) areaEl.classList.remove("fading");
        });
      }, 350);
    }

    /**
     * Selecciona aleatoriamente una ruta distinta a la actual del pool y
     * llama a `setFeaturedRoute`. Garantiza que nunca se repite el mismo
     * índice consecutivo.
     */
    function rotateFeatured() {
      if (ROTATION_POOL.length < 2) return;
      let next = currentFeaturedIdx;
      while (next === currentFeaturedIdx) {
        next = Math.floor(Math.random() * ROTATION_POOL.length);
      }
      setFeaturedRoute(next);
    }

    /**
     * Cancela el timer de rotación del featured existente y, si `ms > 0`
     * y hay al menos 2 rutas en el pool, crea uno nuevo.
     * @param {number} ms  Milisegundos entre rotaciones. `0` desactiva.
     */
    function setFeaturedRotationInterval(ms) {
      if (featuredRotationTimer) {
        clearInterval(featuredRotationTimer);
        featuredRotationTimer = null;
      }
      if (ms > 0 && ROTATION_POOL.length >= 2) {
        featuredRotationTimer = setInterval(rotateFeatured, ms);
      }
    }

    /**
     * Inicializa la rotación automática de la tarjeta featured:
     * - Posiciona el índice inicial en la ruta `featuredId` si existe.
     * - Lee el intervalo guardado en `localStorage` y lo aplica.
     * - Conecta el `<select>` de intervalo si existe.
     * - Registra el teardown del timer en `window.MENDI_TEARDOWN`.
     */
    function initFeaturedRotation() {
      if (!ROTATION_POOL.length) return;

      let startIdx = 0;
      if (MENDI.featuredId != null) {
        const i = ROTATION_POOL.findIndex(r => r.id === MENDI.featuredId);
        if (i >= 0) startIdx = i;
      }
      currentFeaturedIdx = startIdx;

      const sel = $("featured-rotation-select");
      let savedInterval = "15000";
      try { savedInterval = localStorage.getItem(STORAGE_FEAT_INTERVAL) || "15000"; } catch (_) {}

      if (sel) {
        const exists = Array.from(sel.options).some(o => o.value === savedInterval);
        sel.value = exists ? savedInterval : "15000";

        if (ROTATION_POOL.length < 2) {
          sel.disabled = true;
        } else {
          setFeaturedRotationInterval(parseInt(sel.value, 10));
        }

        sel.addEventListener("change", e => {
          const v = e.target.value;
          try { localStorage.setItem(STORAGE_FEAT_INTERVAL, v); } catch (_) {}
          setFeaturedRotationInterval(parseInt(v, 10));
        });
      } else if (ROTATION_POOL.length >= 2) {
        setFeaturedRotationInterval(parseInt(savedInterval, 10));
      }

      window.MENDI_TEARDOWN.push(() => {
        if (featuredRotationTimer) { clearInterval(featuredRotationTimer); featuredRotationTimer = null; }
        currentFeaturedIdx = -1;
      });
    }

    // ============ STATE FILTERS ============
    const DEFAULT_DIFFICULTY = ["easy", "moderate", "hard", "very-hard"];
    const state = {
      filters: {
        difficulty: new Set(DEFAULT_DIFFICULTY),
        region: "all",
        country: "all",
        distance: "all",
        gain: "all",
        date_from: "",
        date_to: "",
        search: "",
      },
      sort: "date-desc",
      offset: 0,
      matched: 0,
      hasMore: false,
      loading: false,
    };

    /**
     * Genera el HTML de una tarjeta de ruta para la vista cuadrícula.
     * Muestra perfil SVG, nombre, región + fecha, y las métricas
     * km / desnivel / dificultad.
     * @param {object} r  Objeto de ruta devuelto por `/api/rutas`.
     * @param {string} q  Término de búsqueda activo para resaltar.
     * @returns {string}  HTML de la tarjeta.
     */
    function buildCardHTML(r, q) {
      const km = (r.km != null ? r.km : 0).toFixed(2).replace(".", ",");
      const gain = String(r.gain ?? 0).replace(/\B(?=(\d{3})+(?!\d))/g, ".");
      const score = (r.score ?? 0).toFixed(1).replace(".", ",");
      const eleMin = String(r.ele_min ?? 0).replace(/\B(?=(\d{3})+(?!\d))/g, ".");
      const eleMax = String(r.ele_max ?? 0).replace(/\B(?=(\d{3})+(?!\d))/g, ".");
      return (
        `<a class="route-card" href="/rutas/${r.id}" data-id="${r.id}">` +
          `<div class="route-card-elev">` +
            `<svg viewBox="0 0 800 200" preserveAspectRatio="none">` +
              `<defs>` +
                `<linearGradient id="cg-${r.id}" x1="0" x2="0" y1="0" y2="1">` +
                  `<stop offset="0%" stop-color="var(--${escapeHTML(r.level)})" stop-opacity="0.4"/>` +
                  `<stop offset="100%" stop-color="var(--${escapeHTML(r.level)})" stop-opacity="0"/>` +
                `</linearGradient>` +
              `</defs>` +
              `<path d="${escapeHTML(r.area || "")}" fill="url(#cg-${r.id})"/>` +
              `<path d="${escapeHTML(r.line || "")}" fill="none" stroke="var(--${escapeHTML(r.level)})" stroke-width="1.4" stroke-opacity="0.85"/>` +
            `</svg>` +
            `<div class="route-card-elev-meta">${eleMin}–${eleMax} m</div>` +
          `</div>` +
          `<div class="route-card-body">` +
            `<div class="route-card-name">${highlight(r.name, q)}</div>` +
            `<div class="route-card-meta">${escapeHTML(fmtDateLocal(r.started_at_iso) || r.date)} · ${highlight(r.region || "", q)}</div>` +
            `<div class="route-card-stats">` +
              `<div class="route-card-stat"><div class="l">km</div><div class="v">${km}</div></div>` +
              `<div class="route-card-stat"><div class="l">desnivel</div><div class="v">${gain} m</div></div>` +
              `<div class="route-card-stat"><div class="l">dific.</div><div class="v" style="color:var(--${escapeHTML(r.level)})">${score}</div></div>` +
            `</div>` +
          `</div>` +
        `</a>`
      );
    }

    // ============ RENDER FILA ============
    /**
     * Genera el HTML completo de una fila de ruta para la lista. Incluye
     * número de orden, nombre con resaltado de búsqueda, región, fecha,
     * perfil SVG de elevación, estadísticas y etiqueta de dificultad.
     * @param {object} r          Objeto de ruta devuelto por `/api/rutas`.
     * @param {number} displayIdx Número de orden a mostrar (base 1).
     * @param {string} q          Término de búsqueda activo para resaltar.
     * @returns {string}  HTML de la fila.
     */
    function buildRowHTML(r, displayIdx, q) {
      const num = String(displayIdx).padStart(2, "0");
      const km = (r.km != null ? r.km : 0).toFixed(2).replace(".", ",");
      const gain = String(r.gain ?? 0).replace(/\B(?=(\d{3})+(?!\d))/g, ".");
      const eleMin = String(r.ele_min ?? 0).replace(/\B(?=(\d{3})+(?!\d))/g, ".");
      const eleMax = String(r.ele_max ?? 0).replace(/\B(?=(\d{3})+(?!\d))/g, ".");
      const score = (r.score ?? 0).toFixed(1).replace(".", ",");

      return (
        `<a class="route-row"` +
          ` href="/rutas/${r.id}"` +
          ` data-id="${r.id}"` +
          ` data-level="${escapeHTML(r.level)}"` +
          ` data-region="${escapeHTML(r.region_filter || "")}"` +
          ` data-name="${escapeHTML((r.name || "").toLowerCase())}"` +
          ` data-origin="${escapeHTML((r.origin || "").toLowerCase())}"` +
          ` data-region-text="${escapeHTML((r.region || "").toLowerCase())}"` +
          ` data-km="${r.km}"` +
          ` data-gain="${r.gain}"` +
          ` data-score="${r.score}"` +
          ` data-date="${escapeHTML(r.date_sort || "")}"` +
          ` data-year="${r.year}">` +
          `<div class="route-num tnum" data-num>${num}</div>` +
          `<div class="route-info">` +
            `<div class="name" data-text-name>` +
              `${highlight(r.name, q)} · <span style="color:var(--text-muted); font-weight:400;" data-text-origin>${highlight(r.origin, q)}</span>` +
            `</div>` +
            `<div class="region" data-text-region>${highlight(r.region, q)}</div>` +
            `<div class="date">${escapeHTML(fmtDateLocal(r.started_at_iso) || r.date)}</div>` +
          `</div>` +
          `<div class="route-elev-wrap">` +
            `<svg class="route-elev-svg" viewBox="0 0 800 200" preserveAspectRatio="none">` +
              `<defs>` +
                `<linearGradient id="grad-${r.id}" x1="0" x2="0" y1="0" y2="1">` +
                  `<stop offset="0%" stop-color="var(--${escapeHTML(r.level)})" stop-opacity="0.4"/>` +
                  `<stop offset="100%" stop-color="var(--${escapeHTML(r.level)})" stop-opacity="0"/>` +
                `</linearGradient>` +
              `</defs>` +
              `<path d="${escapeHTML(r.area || "")}" fill="url(#grad-${r.id})"/>` +
              `<path d="${escapeHTML(r.line || "")}" fill="none" stroke="var(--${escapeHTML(r.level)})" stroke-width="1.4" stroke-opacity="0.85"/>` +
            `</svg>` +
            `<div class="route-elev-meta">${eleMin}–${eleMax} m</div>` +
          `</div>` +
          `<div class="route-stats">` +
            `<div class="route-stat"><div class="l">distancia</div><div class="v tnum">${km} km</div></div>` +
            `<div class="route-stat"><div class="l">desnivel +</div><div class="v tnum">${gain} m</div></div>` +
            `<div class="route-stat"><div class="l">tiempo</div><div class="v tnum">${escapeHTML(r.time || "")}</div></div>` +
            `<div class="route-stat"><div class="l">ritmo</div><div class="v tnum">${escapeHTML(r.pace || "")}</div></div>` +
          `</div>` +
          `<div class="route-tag-wrap">` +
            `<span class="tag ${escapeHTML(r.level)}">● ${score} · ${escapeHTML(r.level_label || "")}</span>` +
            `<span class="route-arrow">` +
              `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M5 12h14M12 5l7 7-7 7" stroke-linecap="round" stroke-linejoin="round"/></svg>` +
            `</span>` +
          `</div>` +
        `</a>`
      );
    }

    // ============ FETCH /api/rutas ============
    /**
     * Construye los `URLSearchParams` para la petición a `/api/rutas` a
     * partir del estado actual de filtros, ordenación y paginación.
     * Si `reset` es `true` fuerza `offset=0`.
     * @param {boolean} reset
     * @returns {URLSearchParams}
     */
    function buildQueryParams(reset) {
      const f = state.filters;
      const params = new URLSearchParams();
      const diffs = Array.from(f.difficulty);
      if (diffs.length > 0 && diffs.length < DEFAULT_DIFFICULTY.length) {
        params.set("difficulty", diffs.join(","));
      }
      if (f.search.trim()) params.set("q", f.search.trim());
      if (f.country !== "all") params.set("country", f.country);
      if (f.region !== "all") params.set("region", f.region);
      if (f.distance !== "all") params.set("distance", f.distance);
      if (f.gain !== "all") params.set("gain", f.gain);
      if (f.date_from) params.set("date_from", f.date_from);
      if (f.date_to) params.set("date_to", f.date_to);
      if (state.sort !== "date-desc") params.set("sort", state.sort);
      params.set("offset", String(reset ? 0 : state.offset));
      params.set("limit", String(PAGE_SIZE));
      return params;
    }

    /**
     * Genera el HTML de `count` filas skeleton para mostrar durante la
     * carga inicial o el reset de la lista.
     * @param {number} count
     * @returns {string}
     */
    function buildSkeletonHTML(count) {
      let html = "";
      for (let i = 0; i < count; i++) {
        html += `
          <div class="route-row-skel">
            <div class="col"><span class="skeleton line sm" style="width:60%;"></span></div>
            <div class="col">
              <span class="skeleton line lg" style="width:75%;"></span>
              <span class="skeleton line sm" style="width:40%;"></span>
            </div>
            <div class="col"><span class="skeleton line" style="width:80%;"></span></div>
            <div class="col"><span class="skeleton line" style="width:90%;"></span></div>
            <div class="col"><span class="skeleton line" style="width:85%;"></span></div>
            <div class="col"><span class="skeleton line sm" style="width:60%;"></span></div>
          </div>`;
      }
      return html;
    }

    /**
     * Obtiene una página de rutas de `/api/rutas` y actualiza la lista.
     * Si `reset` es `true` limpia la lista y reinicia el offset; si no,
     * añade las nuevas filas al final (scroll infinito). Gestiona el
     * estado de carga, el sentinel de scroll infinito, el contador de
     * resultados y la visibilidad del botón de reset de filtros.
     * @param {boolean} reset  `true` para reiniciar desde el principio.
     */
    async function fetchRoutes(reset) {
      if (state.loading) return;
      state.loading = true;

      const sentinel = $("routes-sentinel");
      if (sentinel && !reset) {
        sentinel.style.display = "block";
        requestAnimationFrame(() => sentinel.classList.add("visible"));
      }

      const list = $("routes-list");
      const empty = $("empty-state");
      if (reset && list) {
        list.style.display = "";
        if (empty) empty.style.display = "none";
        list.innerHTML = buildSkeletonHTML(6);
      }

      const params = buildQueryParams(reset);
      let json;
      try {
        const res = await fetch(`/api/rutas?${params.toString()}`, {
          headers: { "Accept": "application/json" },
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        json = await res.json();
      } catch (err) {
        state.loading = false;
        console.error("[mendi] fetchRoutes error:", err);
        if (sentinel) {
          sentinel.classList.remove("visible");
          sentinel.style.display = "none";
        }
        if (reset && list) list.innerHTML = "";
        return;
      }

      if (!list) { state.loading = false; return; }

      if (reset) {
        list.innerHTML = "";
        state.offset = 0;
        state.matched = 0;
      }

      const q = state.filters.search.trim().toLowerCase();
      const items = Array.isArray(json.items) ? json.items : [];
      const startIdx = reset ? 0 : state.offset;

      const html = items.map((r, i) => {
        return currentView === "grid" ? buildCardHTML(r, q) : buildRowHTML(r, startIdx + i + 1, q);
      }).join("");
      if (reset) list.innerHTML = html;
      else list.insertAdjacentHTML("beforeend", html);

      state.offset = (reset ? 0 : state.offset) + items.length;
      state.matched = json.matched || 0;
      state.hasMore = !!json.hasMore;

      if (state.matched === 0) {
        list.style.display = "none";
        if (empty) {
          empty.style.display = "block";
          empty.innerHTML = q
            ? `ninguna ruta coincide con <span style="color:var(--text); font-weight:500;">"${escapeHTML(q)}"</span> y los filtros activos`
            : "ningún resultado coincide con los filtros activos";
        }
      } else {
        list.style.display = "";
        if (empty) empty.style.display = "none";
      }

      if (sentinel) {
        if (state.hasMore) {
          sentinel.style.display = "block";
          requestAnimationFrame(() => {
            sentinel.classList.add("visible");
            // Si el sentinel ya está en el viewport (p.ej. tras cambiar de
            // vista con pocos resultados), el IntersectionObserver no vuelve
            // a disparar porque no hubo cambio de intersección. Forzamos una
            // comprobación manual para que el scroll infinito arranque.
            if (infiniteObserver) {
              const rect = sentinel.getBoundingClientRect();
              const inView = rect.top < window.innerHeight + 200;
              if (inView && !state.loading && state.hasMore) fetchRoutes(false);
            }
          });
        } else {
          sentinel.classList.remove("visible");
          sentinel.style.display = "none";
        }
      }

      const countEl = $("routes-count");
      const sortLabels = {
        "date-desc": "ordenadas por fecha · más reciente",
        "date-asc": "ordenadas por fecha · más antigua",
        "km-desc": "ordenadas por distancia · mayor",
        "km-asc": "ordenadas por distancia · menor",
        "gain-desc": "ordenadas por desnivel · mayor",
        "gain-asc": "ordenadas por desnivel · menor",
        "score-desc": "ordenadas por dificultad · mayor",
      };
      if (countEl) {
        const showing = state.offset < state.matched
          ? `mostrando ${state.offset} de ${state.matched}`
          : `${state.matched} ${state.matched === 1 ? "ruta" : "rutas"}`;
        countEl.textContent = `${showing} · ${sortLabels[state.sort] || ""}`;
      }
      const stats = $("filter-stats");
      if (stats) {
        const total = json.total || MENDI.total || 0;
        stats.innerHTML = `<span class="v">${state.matched}</span> de ${total} ${total === 1 ? "ruta" : "rutas"}`;
      }

      const f = state.filters;
      const isDirty =
        f.region !== "all" || f.country !== "all" ||
        f.distance !== "all" || f.gain !== "all" ||
        f.date_from !== "" || f.date_to !== "" || q !== "" ||
        f.difficulty.size !== DEFAULT_DIFFICULTY.length ||
        !DEFAULT_DIFFICULTY.every(d => f.difficulty.has(d)) ||
        state.sort !== "date-desc";
      const resetBtnEl = $("reset-filters");
      if (resetBtnEl) resetBtnEl.classList.toggle("hidden", !isDirty);

      updateFiltersCount();
      state.loading = false;
    }

    /** Alias de `fetchRoutes(true)` para mayor legibilidad en los listeners. */
    function reload() { fetchRoutes(true); }

    // ============ INFINITE SCROLL ============
    let infiniteObserver = null;
    /**
     * Inicializa el `IntersectionObserver` sobre `#routes-sentinel` para
     * cargar la siguiente página automáticamente cuando el sentinel entra
     * en el viewport (margen de 200 px). Registra el teardown en
     * `window.MENDI_TEARDOWN`.
     */
    function initInfiniteScroll() {
      const sentinel = $("routes-sentinel");
      if (!sentinel || typeof IntersectionObserver === "undefined") return;
      infiniteObserver = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
          if (!entry.isIntersecting) return;
          if (sentinel.style.display === "none") return;
          if (state.loading || !state.hasMore) return;
          fetchRoutes(false);
        });
      }, { rootMargin: "200px 0px" });
      infiniteObserver.observe(sentinel);
      window.MENDI_TEARDOWN.push(() => {
        if (infiniteObserver) { infiniteObserver.disconnect(); infiniteObserver = null; }
      });
    }

    // ============ FILTERS POPOVER ============
    const filtersTrigger = $("filters-trigger");
    const filtersPopover = $("filters-popover");
    const filtersCount = $("filters-count");

    /**
     * Abre el popover de filtros avanzados y marca el trigger como activo.
     */
    function openFilters() {
      if (!filtersTrigger || !filtersPopover) return;
      filtersTrigger.classList.add("open");
      filtersPopover.classList.add("open");
      filtersTrigger.setAttribute("aria-expanded", "true");
    }
    /**
     * Cierra el popover de filtros avanzados y desmarca el trigger.
     */
    function closeFilters() {
      if (!filtersTrigger || !filtersPopover) return;
      filtersTrigger.classList.remove("open");
      filtersPopover.classList.remove("open");
      filtersTrigger.setAttribute("aria-expanded", "false");
    }

    if (filtersTrigger && filtersPopover) {
      filtersTrigger.addEventListener("click", (e) => {
        e.stopPropagation();
        filtersPopover.classList.contains("open") ? closeFilters() : openFilters();
      });
      filtersPopover.addEventListener("click", (e) => e.stopPropagation());
    }

    // Listeners globales — registramos su limpieza en teardown.
    /**
     * Listener global de clic en `document`: cierra el popover de filtros
     * si está abierto y el clic fue fuera de él. Se registra con teardown
     * para no acumular listeners entre swaps de HTMX.
     */
    function onDocClick() {
      if (filtersPopover && filtersPopover.classList.contains("open")) closeFilters();
    }
    /**
     * Listener global de teclado en `document`:
     * - `Escape`: cierra el popover de filtros o limpia el campo de búsqueda.
     * - `/`: enfoca el campo de búsqueda si no está ya activo.
     * Se registra con teardown para no acumular listeners entre swaps.
     */
    function onDocKey(e) {
      if (e.key === "Escape" && filtersPopover && filtersPopover.classList.contains("open")) closeFilters();
      if (!searchInput) return;
      if (e.key === "/" && document.activeElement !== searchInput && !e.metaKey && !e.ctrlKey) {
        e.preventDefault();
        searchInput.focus();
        searchInput.select();
      }
      if (e.key === "Escape" && document.activeElement === searchInput) {
        searchInput.blur();
        if (searchInput.value) {
          searchInput.value = "";
          state.filters.search = "";
          updateSearchUI();
          reload();
        }
      }
    }
    document.addEventListener("click", onDocClick);
    document.addEventListener("keydown", onDocKey);
    window.MENDI_TEARDOWN.push(() => {
      document.removeEventListener("click", onDocClick);
      document.removeEventListener("keydown", onDocKey);
    });

    /**
     * Actualiza el badge numérico de filtros activos (`#filters-count`)
     * contando cuántos filtros del popover difieren de su valor por defecto.
     */
    function updateFiltersCount() {
      if (!filtersCount) return;
      const f = state.filters;
      let n = 0;
      if (f.country !== "all") n++;
      if (f.region !== "all") n++;
      if (f.distance !== "all") n++;
      if (f.gain !== "all") n++;
      const diffChanged =
        f.difficulty.size !== DEFAULT_DIFFICULTY.length ||
        !DEFAULT_DIFFICULTY.every(d => f.difficulty.has(d));
      if (diffChanged) n++;
      filtersCount.textContent = String(n);
      filtersCount.classList.toggle("zero", n === 0);
    }

    // ============ FILTER WIRING ============
    document.querySelectorAll(".chip[data-filter]").forEach(chip => {
      chip.addEventListener("click", () => {
        const lvl = chip.dataset.filter;
        if (state.filters.difficulty.has(lvl)) {
          state.filters.difficulty.delete(lvl);
          chip.classList.remove("active");
        } else {
          state.filters.difficulty.add(lvl);
          chip.classList.add("active");
        }
        reload();
      });
    });

    /**
     * Conecta un elemento `<select>` identificado por `id` al filtro
     * `key` del estado, disparando un `reload()` al cambiar.
     * @param {string} id   ID del elemento `<select>`.
     * @param {string} key  Clave en `state.filters`.
     */
    function bindSelect(id, key) {
      const el = $(id);
      if (!el) return;
      el.addEventListener("change", e => {
        state.filters[key] = e.target.value;
        reload();
      });
    }
    bindSelect("filter-country", "country");
    bindSelect("filter-region", "region");
    bindSelect("filter-distance", "distance");
    bindSelect("filter-gain", "gain");

    // ============ DATE RANGE PICKER ============
    const dateFrom = $("filter-date-from");
    const dateTo = $("filter-date-to");
    const dateClear = $("filter-date-clear");

    function updateDateClear() {
      if (dateClear) dateClear.classList.toggle("hidden", !state.filters.date_from && !state.filters.date_to);
    }
    if (dateFrom) {
      dateFrom.addEventListener("change", e => {
        state.filters.date_from = e.target.value;
        updateDateClear();
        reload();
      });
    }
    if (dateTo) {
      dateTo.addEventListener("change", e => {
        state.filters.date_to = e.target.value;
        updateDateClear();
        reload();
      });
    }
    if (dateClear) {
      dateClear.addEventListener("click", () => {
        state.filters.date_from = "";
        state.filters.date_to = "";
        if (dateFrom) dateFrom.value = "";
        if (dateTo) dateTo.value = "";
        updateDateClear();
        reload();
      });
    }

    const sortSelect = $("sort-select");
    if (sortSelect) {
      sortSelect.addEventListener("change", e => {
        state.sort = e.target.value;
        reload();
      });
    }

    // ============ SEARCH ============
    const searchInput = $("search-input");
    const searchClear = $("search-clear");
    const searchShortcut = $("search-shortcut");
    let searchDebounce = null;

    /**
     * Sincroniza la visibilidad del botón de limpiar búsqueda y el
     * indicador de atajo de teclado con el contenido actual del input.
     */
    function updateSearchUI() {
      if (!searchInput) return;
      const has = searchInput.value.length > 0;
      if (searchClear) searchClear.classList.toggle("visible", has);
      if (searchShortcut) {
        searchShortcut.classList.toggle("hidden", has || document.activeElement === searchInput);
      }
    }

    /**
     * Programa un `reload()` con un debounce de 220 ms para no disparar
     * una petición por cada tecla pulsada en el campo de búsqueda.
     */
    function scheduleSearch() {
      if (searchDebounce) clearTimeout(searchDebounce);
      searchDebounce = setTimeout(reload, 220);
    }

    if (searchInput) {
      searchInput.addEventListener("input", e => {
        state.filters.search = e.target.value;
        updateSearchUI();
        scheduleSearch();
      });
      searchInput.addEventListener("focus", () => {
        if (searchShortcut) searchShortcut.classList.add("hidden");
      });
      searchInput.addEventListener("blur", updateSearchUI);
    }
    if (searchClear && searchInput) {
      searchClear.addEventListener("click", () => {
        searchInput.value = "";
        state.filters.search = "";
        updateSearchUI();
        searchInput.focus();
        reload();
      });
    }
    window.MENDI_TEARDOWN.push(() => {
      if (searchDebounce) { clearTimeout(searchDebounce); searchDebounce = null; }
    });

    // ============ RESET ============
    const resetBtn = $("reset-filters");
    if (resetBtn) {
      resetBtn.addEventListener("click", () => {
        state.filters.difficulty = new Set(DEFAULT_DIFFICULTY);
        state.filters.region = "all";
        state.filters.country = "all";
        state.filters.distance = "all";
        state.filters.gain = "all";
        state.filters.date_from = "";
        state.filters.date_to = "";
        state.filters.search = "";
        state.sort = "date-desc";

        document.querySelectorAll(".chip[data-filter]").forEach(chip => {
          const lvl = chip.dataset.filter;
          chip.classList.toggle("active", DEFAULT_DIFFICULTY.includes(lvl));
        });
        ["filter-country", "filter-region", "filter-distance", "filter-gain"].forEach(id => {
          const el = $(id);
          if (el) el.value = "all";
        });
        if (dateFrom) dateFrom.value = "";
        if (dateTo) dateTo.value = "";
        updateDateClear();
        if (sortSelect) sortSelect.value = "date-desc";
        if (searchInput) searchInput.value = "";
        updateSearchUI();
        reload();
      });
    }

    // ============ VIEW SWITCH ============
    document.querySelectorAll(".view-switch button").forEach(btn => {
      btn.addEventListener("click", () => {
        const view = btn.dataset.view;
        if (view === "list" || view === "grid") {
          applyView(view);
          reload();
        }
      });
    });

    // ============ THEME SYNC para mini-mapa ============
    const themeObserver = new MutationObserver((mutations) => {
      for (const m of mutations) {
        if (m.attributeName === "data-theme") syncFeaturedTiles();
      }
    });
    themeObserver.observe(document.documentElement, { attributes: true });
    window.MENDI_TEARDOWN.push(() => {
      try { themeObserver.disconnect(); } catch (_) {}
    });

    // ============ ARRANQUE ============
    document.querySelectorAll(".chip[data-filter]").forEach(chip => {
      const lvl = chip.dataset.filter;
      chip.classList.toggle("active", state.filters.difficulty.has(lvl));
    });
    initFeaturedMap();
    initFeaturedRotation();
    applyView(currentView);
    updateSearchUI();
    initInfiniteScroll();
    fetchRoutes(true);
  }

  window.MENDI_PAGES.rutas = { init };
})();
