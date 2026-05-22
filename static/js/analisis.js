// ============================================================
// mendi.log · página ANÁLISIS
// ------------------------------------------------------------
// Toda la lógica especifica de la vista /analisis. Se registra
// en window.MENDI_PAGES.analisis para que el dispatcher de
// app.js la invoque tras cada htmx:afterSwap.
// ============================================================
(function () {
  "use strict";

  const STORAGE_THEME = "mendi.theme";

  // ============ helpers ============
  function $(sel, root = document) { return root.querySelector(sel); }
  function $$(sel, root = document) { return Array.from(root.querySelectorAll(sel)); }
  /**
   * Escapa los caracteres especiales HTML de una cadena para interpolación
   * segura en innerHTML.
   * @param {*} s
   * @returns {string}
   */
  function escapeHtml(s) {
    return String(s)
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
   * Formatea un número de kilómetros con un decimal y coma decimal.
   * @param {number} n
   * @returns {string}  Ej.: `"12,3"`
   */
  function fmtKm(n) {
    return Number(n).toFixed(1).replace(".", ",");
  }
  /**
   * Formatea un número entero con separador de miles (punto).
   * @param {number} n
   * @returns {string}  Ej.: `"1.234"`
   */
  function fmtInt(n) {
    return String(Math.round(Number(n) || 0)).replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  }

  // Ejecuta `fn` con `HTMLCanvasElement.getContext('2d')` parcheado para que
  // siempre incluya `willReadFrequently: true`. Se usa al crear el heatLayer
  // de Leaflet, que internamente llama a `getImageData()` en cada redraw.
  // Se restaura el método original al salir (incluso si fn lanza), para no
  // afectar a otros canvas de la página (donuts SVG no, pero los tiles sí
  // pueden usar canvas internos según el motor del navegador).
  /**
   * Ejecuta `fn` con `HTMLCanvasElement.prototype.getContext` parcheado
   * para que toda llamada a `getContext('2d')` incluya
   * `willReadFrequently: true`. Necesario para leaflet-heat, que llama a
   * `getImageData()` en cada redraw; sin la flag Chrome no acelera esa
   * lectura por GPU y bloquea el hilo principal. El método original se
   * restaura siempre al salir, incluso si `fn` lanza.
   * @param {Function} fn  Función a ejecutar con el parche activo.
   */
  function withCanvasReadFlag(fn) {
    const proto = HTMLCanvasElement.prototype;
    const original = proto.getContext;
    proto.getContext = function (type, attrs) {
      if (type === "2d") {
        attrs = Object.assign({ willReadFrequently: true }, attrs || {});
      }
      return original.call(this, type, attrs);
    };
    try { fn(); }
    finally { proto.getContext = original; }
  }

  // requestIdleCallback con fallback a setTimeout(0) para navegadores
  // sin soporte (Safari < 17). Permite diferir trabajo pesado hasta
  // después del primer pintado para no bloquear el handler `load`.
  const ric = window.requestIdleCallback
    ? (cb) => window.requestIdleCallback(cb, { timeout: 500 })
    : (cb) => setTimeout(cb, 0);

  // ============ estado del módulo ============
  let payload = null;
  let heatMap = null;
  let heatLayer = null;
  let darkTiles = null;
  let lightTiles = null;

  /**
   * Lee y parsea el JSON embebido en `#ana-payload` (inyectado por
   * Jinja2 en el servidor). Devuelve `null` si el elemento no existe o
   * el JSON es inválido.
   * @returns {object|null}
   */
  function readPayload() {
    const tag = document.getElementById("ana-payload");
    if (!tag) return null;
    try { return JSON.parse(tag.textContent || "{}"); }
    catch (e) { console.error("[analisis] payload inválido", e); return null; }
  }

  // ============================================================
  //  CHIPS DE RANGO (recargan la página vía /analisis?range=...)
  // ============================================================
  /**
   * Conecta los chips de rango temporal (`.ana-chip`) y el desplegable
   * de temporada (`#ana-season-select`) para navegar a
   * `/analisis?range=...` al seleccionarlos. El chip `custom` no navega
   * hasta que se pulsa el botón "aplicar" con fechas válidas.
   */
  function initRangeChips() {
    const wrap = $(".ana-range");
    if (!wrap) return;

    wrap.addEventListener("click", (e) => {
      // El <select> de temporada NO debe disparar la navegación al hacer click
      const btn = e.target.closest("button.ana-chip");
      if (!btn) return;
      const key = btn.dataset.range;

      // Marcar visualmente al instante (feedback rápido antes de la navegación)
      $$(".ana-chip", wrap).forEach(c => c.classList.toggle("is-on", c === btn));
      const customWrap = $(".ana-range-custom", wrap);
      if (customWrap) customWrap.classList.toggle("is-on", key === "custom");

      if (key === "custom") return; // se aplica con el botón "aplicar"

      const url = `/analisis?range=${encodeURIComponent(key)}`;
      navigate(url);
    });

    // Desplegable de temporada: navega al elegir una estación, salvo el placeholder
    const seasonSel = $("#ana-season-select");
    if (seasonSel) {
      seasonSel.addEventListener("change", (e) => {
        const v = e.target.value;
        if (!v) return;
        navigate(`/analisis?range=${encodeURIComponent(v)}`);
      });
    }

    const apply = $("#ana-range-apply");
    if (apply) {
      apply.addEventListener("click", () => {
        const f = $("#ana-from").value;
        const t = $("#ana-to").value;
        if (!f || !t) return;
        const url = `/analisis?range=custom&from=${encodeURIComponent(f)}&to=${encodeURIComponent(t)}`;
        navigate(url);
      });
    }
  }

  /**
   * Navega a `url` usando htmx si está disponible (swap de `#hx-root`
   * + `pushState`), o con `location.href` como fallback.
   * @param {string} url
   */
  function navigate(url) {
    // Guardar posición de scroll antes de navegar para restaurarla tras el swap
    const scrollY = window.scrollY;
    if (window.htmx && typeof window.htmx.ajax === "function") {
      window.htmx.ajax("GET", url, {
        target: "#hx-root",
        select: "#hx-root",
        swap: "outerHTML",
      });
      window.history.pushState({}, "", url);
      // Restaurar scroll tras el swap
      const restore = () => {
        window.scrollTo({ top: scrollY, behavior: "instant" });
        document.body.removeEventListener("htmx:afterSwap", restore);
      };
      document.body.addEventListener("htmx:afterSwap", restore);
    } else {
      window.location.href = url;
    }
  }

  // ============================================================
  //  01 · MAPA DE CALOR (Leaflet + leaflet.heat)
  // ============================================================
  /**
   * Inicializa el mapa de calor Leaflet (`#ana-heatmap`) con los puntos
   * de `payload.heatPoints`. Aplica el parche `withCanvasReadFlag` al
   * crear el `heatLayer` para evitar bloqueos de GPU. Registra el
   * teardown en `window.MENDI_TEARDOWN`.
   */
  function initHeatMap() {
    const el = document.getElementById("ana-heatmap");
    if (!el || typeof L === "undefined") return;

    const center = (payload && payload.mapCenter) || [42.7, -1.6];

    // Esperar a que el contenedor tenga dimensiones reales antes de
    // inicializar Leaflet, igual que en resumen y detalle.
    const ro = new ResizeObserver((entries, observer) => {
      const h = entries[0].contentRect.height;
      if (h < 10) return;
      observer.disconnect();
      _buildHeatMap(el, center);
    });
    ro.observe(el);
    window.MENDI_TEARDOWN.push(() => { try { ro.disconnect(); } catch (_) {} });

    const fallback = setTimeout(() => { ro.disconnect(); _buildHeatMap(el, center); }, 800);
    window.MENDI_TEARDOWN.push(() => clearTimeout(fallback));
  }

  /**
   * Construye el mapa Leaflet vacío con teselas y teardown,
   * luego pinta los puntos de calor desde el payload ya disponible.
   */
  function _buildHeatMap(el, center) {
    if (heatMap) return;

    heatMap = L.map(el, {
      zoomControl: true,
      attributionControl: true,
      scrollWheelZoom: true,
    }).setView(center, 6);

    heatMap.createPane("labelsPane").style.zIndex = "450";

    darkTiles = L.tileLayer(
      "https://{s}.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}{r}.png",
      { attribution: "© OpenStreetMap, © CartoDB", maxZoom: 18 }
    );
    lightTiles = L.tileLayer(
      "https://{s}.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}{r}.png",
      { attribution: "© OpenStreetMap, © CartoDB", maxZoom: 18 }
    );

    updateHeatTiles();
    requestAnimationFrame(() => { try { heatMap && heatMap.invalidateSize(); } catch (_) {} });

    window.MENDI_TEARDOWN.push(() => {
      try { if (heatMap) heatMap.remove(); } catch (_) {}
      heatMap = null; heatLayer = null; darkTiles = null; lightTiles = null;
    });

    _paintHeatPoints();
  }

  /**
   * Pinta los puntos de calor sobre el mapa ya inicializado.
   * Los datos vienen del payload embebido en la página.
   */
  function _paintHeatPoints() {
    if (!heatMap || !payload) return;
    const points = payload.heatPoints || [];
    if (!points.length) return;

    if (typeof L.heatLayer === "function") {
      const maxW = Math.max(...points.map(p => p[2])) || 1;
      withCanvasReadFlag(() => {
        heatLayer = L.heatLayer(points, {
          radius: 22,
          blur: 14,
          max: Math.max(6, maxW),
          minOpacity: 0.45,
          gradient: {
            0.15: "#7DAFC9",
            0.40: "#B5D17A",
            0.70: "#E8B86D",
            1.00: "#E47862",
          },
        }).addTo(heatMap);
      });
    }

    if (points.length >= 2) {
      const bounds = L.latLngBounds(points.map(p => [p[0], p[1]]));
      heatMap.fitBounds(bounds, { padding: [40, 40], maxZoom: 9 });
    } else if (points.length === 1) {
      heatMap.setView([points[0][0], points[0][1]], 11);
    }
  }

  /**
   * Intercambia las capas de teselas del mapa de calor según el tema
   * activo (`dark` / `light`).
   */
  function updateHeatTiles() {
    if (!heatMap) return;
    const theme = document.documentElement.getAttribute("data-theme");
    if (theme === "dark") {
      if (heatMap.hasLayer(lightTiles)) heatMap.removeLayer(lightTiles);
      if (!heatMap.hasLayer(darkTiles)) darkTiles.addTo(heatMap);
    } else {
      if (heatMap.hasLayer(darkTiles)) heatMap.removeLayer(darkTiles);
      if (!heatMap.hasLayer(lightTiles)) lightTiles.addTo(heatMap);
    }
  }

  // ============================================================
  //  02-A · DONUT RATIO (repetidas vs nuevas)
  // ============================================================
  /**
   * Renderiza el donut SVG de ratio repetidas/nuevas (`#ana-ratio-donut`)
   * a partir de `payload.ratio`. Cada segmento usa el color CSS mapeado
   * por `p.css` (`warm`, `accent`, `cool`).
   */
  function renderRatioDonut() {
    const svg = document.getElementById("ana-ratio-donut");
    if (!svg || !payload || !payload.ratio || !payload.ratio.length) return;
    svg.innerHTML = "";

    const cx = 60, cy = 60, rOuter = 50, rInner = 32;
    const total = payload.ratio.reduce((a, p) => a + p.value, 0) || 1;
    let acc = -Math.PI / 2;
    const colorMap = {
      warm: cssVar("--accent-warm") || "#E8B86D",
      accent: cssVar("--accent") || "#B5D17A",
      cool: cssVar("--accent-cool") || "#7DAFC9",
    };
    let html = "";
    payload.ratio.forEach(p => {
      const angle = (p.value / total) * Math.PI * 2;
      const a0 = acc, a1 = acc + angle;
      acc = a1;
      const big = angle > Math.PI ? 1 : 0;
      const x0 = cx + rOuter * Math.cos(a0);
      const y0 = cy + rOuter * Math.sin(a0);
      const x1 = cx + rOuter * Math.cos(a1);
      const y1 = cy + rOuter * Math.sin(a1);
      const xi1 = cx + rInner * Math.cos(a1);
      const yi1 = cy + rInner * Math.sin(a1);
      const xi0 = cx + rInner * Math.cos(a0);
      const yi0 = cy + rInner * Math.sin(a0);
      const d = `M ${x0} ${y0} A ${rOuter} ${rOuter} 0 ${big} 1 ${x1} ${y1} L ${xi1} ${yi1} A ${rInner} ${rInner} 0 ${big} 0 ${xi0} ${yi0} Z`;
      html += `<path d="${d}" fill="${colorMap[p.css] || colorMap.accent}"/>`;
    });
    html += `<text x="${cx}" y="${cy + 4}" text-anchor="middle" font-family="Fraunces, serif" font-size="20" fill="${cssVar('--text')}">${total}</text>`;
    svg.innerHTML = html;
  }

  // ============================================================
  //  02-B · STREAK SPARK (12 semanas)
  // ============================================================
  /**
   * Renderiza el sparkline SVG de racha semanal (`#ana-streak-spark`)
   * con las últimas 12 semanas de `payload.streak.spark`. Marca el
   * último punto en `--accent-warm`.
   */
  function renderStreakSpark() {
    const svg = document.getElementById("ana-streak-spark");
    if (!svg || !payload || !payload.streak) return;
    svg.innerHTML = "";

    const W = 240, H = 60, PAD = 6;
    const data = payload.streak.spark || [];
    if (!data.length) return;
    const max = Math.max(1, ...data.map(p => p.value));
    const xStep = (W - PAD * 2) / (data.length - 1 || 1);
    const cAccent = cssVar("--accent");
    const cWarm = cssVar("--accent-warm");

    let pathLine = "", pathArea = "";
    data.forEach((p, i) => {
      const x = PAD + i * xStep;
      const y = H - PAD - (p.value / max) * (H - PAD * 2);
      pathLine += (i === 0 ? "M" : "L") + ` ${x.toFixed(1)} ${y.toFixed(1)} `;
      if (i === 0) pathArea = `M ${x} ${H - PAD} L ${x} ${y} `;
      else pathArea += `L ${x} ${y} `;
    });
    pathArea += `L ${PAD + (data.length - 1) * xStep} ${H - PAD} Z`;

    const gradId = "spark-grad-" + Math.random().toString(36).slice(2, 7);
    const last = data[data.length - 1];
    const lx = PAD + (data.length - 1) * xStep;
    const ly = H - PAD - (last.value / max) * (H - PAD * 2);
    svg.innerHTML =
      `<defs><linearGradient id="${gradId}" x1="0" x2="0" y1="0" y2="1">
        <stop offset="0%" stop-color="${cAccent}" stop-opacity="0.45"/>
        <stop offset="100%" stop-color="${cAccent}" stop-opacity="0"/>
      </linearGradient></defs>` +
      `<path d="${pathArea}" fill="url(#${gradId})"/>` +
      `<path d="${pathLine}" fill="none" stroke="${cAccent}" stroke-width="1.6" stroke-linejoin="round"/>` +
      `<circle cx="${lx}" cy="${ly}" r="3" fill="${cWarm}"/>`;
  }

  // ============================================================
  //  02-G · DESCUBRIMIENTO (línea fina · 12 meses)
  // ============================================================
  /**
   * Renderiza la línea SVG de descubrimiento mensual (`#ana-disc-line`)
   * con los últimos 12 meses de `payload.discovery`. Muestra etiquetas
   * cada 3 meses para no saturar el eje X.
   */
  function renderDiscLine() {
    const svg = document.getElementById("ana-disc-line");
    if (!svg || !payload || !payload.discovery) return;
    svg.innerHTML = "";

    const W = 240, H = 90, PAD_T = 8, PAD_B = 22, PAD_X = 8;
    const data = payload.discovery;
    if (!data.length) return;
    const max = Math.max(1, ...data.map(p => p.value));
    const xStep = (W - PAD_X * 2) / (data.length - 1 || 1);
    const cCool = cssVar("--accent-cool") || "#7DAFC9";
    const cDim = cssVar("--text-dim");

    let html = `<line x1="${PAD_X}" x2="${W - PAD_X}" y1="${H - PAD_B}" y2="${H - PAD_B}" stroke="${cssVar('--border')}" stroke-width="0.8" stroke-dasharray="2 4"/>`;

    let path = "";
    data.forEach((p, i) => {
      const x = PAD_X + i * xStep;
      const y = (H - PAD_B) - (p.value / max) * (H - PAD_T - PAD_B);
      path += (i === 0 ? "M" : "L") + ` ${x.toFixed(1)} ${y.toFixed(1)} `;
    });
    html += `<path d="${path}" fill="none" stroke="${cCool}" stroke-width="1.6" stroke-linejoin="round"/>`;

    data.forEach((p, i) => {
      const x = PAD_X + i * xStep;
      const y = (H - PAD_B) - (p.value / max) * (H - PAD_T - PAD_B);
      if (p.value > 0) html += `<circle cx="${x}" cy="${y}" r="2.2" fill="${cCool}"/>`;
      if (i % 3 === 0) html += `<text x="${x}" y="${H - 6}" text-anchor="middle" font-family="IBM Plex Mono" font-size="8" fill="${cDim}">${p.label}</text>`;
    });
    svg.innerHTML = html;
  }

  // ============================================================
  //  07 · CALENDARIO HEATMAP (reagrupado por día local)
  // ============================================================
  /**
   * Renderiza el heatmap de calendario (`#ana-calendar-wrap`). Reagrupa
   * los km de `payload.kmByDay` (días UTC del servidor) a días locales
   * del navegador antes de pintar. Muestra hasta 24 meses en orden
   * descendente por año, con niveles de color por km diario.
   */
  function renderCalendar() {
    const wrap = document.getElementById("ana-calendar");
    if (!wrap || !payload || !payload.kmByDay) return;

    const { fmtDateLocal, localDateKey } = window.MENDI_UTIL || {};
    if (!localDateKey) return;

    // Reagrupar km por día local (el servidor manda días UTC)
    const kmByLocalDay = {};
    (payload.kmByDay || []).forEach(({ iso, km }) => {
      // iso es "YYYY-MM-DD" UTC; convertimos a día local añadiendo T00:00:00Z
      const key = localDateKey(iso + "T12:00:00Z"); // mediodía UTC → mismo día en UTC±2
      kmByLocalDay[key] = (kmByLocalDay[key] || 0) + km;
    });

    if (!Object.keys(kmByLocalDay).length) return;

    const MONTH_ES = ["ene","feb","mar","abr","may","jun","jul","ago","sep","oct","nov","dic"];

    // Rango de meses a mostrar (igual que el backend: cal_start → cal_end)
    const allDays = Object.keys(kmByLocalDay).sort();
    const firstDay = new Date(allDays[0] + "T12:00:00");
    const lastDay  = new Date(allDays[allDays.length - 1] + "T12:00:00");

    // Limitar a 24 meses si el rango es muy grande
    const maxStart = new Date(lastDay);
    maxStart.setMonth(maxStart.getMonth() - 23);
    const calStart = firstDay < maxStart ? maxStart : firstDay;
    const calEnd   = lastDay;

    // Construir meses
    const monthsByYear = {};
    let cur = new Date(calStart.getFullYear(), calStart.getMonth(), 1);
    const endYM = [calEnd.getFullYear(), calEnd.getMonth()];

    while (cur.getFullYear() < endYM[0] ||
           (cur.getFullYear() === endYM[0] && cur.getMonth() <= endYM[1])) {
      const y = cur.getFullYear();
      const m = cur.getMonth();
      const lastOfMonth = new Date(y, m + 1, 0);
      const firstWeekday = cur.getDay() === 0 ? 6 : cur.getDay() - 1; // lunes=0

      const weeks = [];
      let week = Array(firstWeekday).fill(null);
      let d = new Date(cur);
      while (d <= lastOfMonth) {
        const key = `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`;
        const v = kmByLocalDay[key] || 0;
        let level = "";
        if (v > 18) level = "lvl4";
        else if (v > 12) level = "lvl3";
        else if (v > 6)  level = "lvl2";
        else if (v > 0)  level = "lvl1";
        const dateLabel = fmtDateLocal ? fmtDateLocal(key + "T12:00:00Z") : key;
        const title = v > 0
          ? `${dateLabel} · ${v.toFixed(2).replace(".",",")} km`
          : `${dateLabel} · sin actividad`;
        week.push({ d: String(d.getDate()).padStart(2,"0"), v, level, title });
        if (week.length === 7) { weeks.push(week); week = []; }
        d.setDate(d.getDate() + 1);
      }
      if (week.length) {
        while (week.length < 7) week.push(null);
        weeks.push(week);
      }

      if (!monthsByYear[y]) monthsByYear[y] = [];
      monthsByYear[y].push({ label: MONTH_ES[m], weeks });

      cur.setMonth(cur.getMonth() + 1);
    }

    // Renderizar HTML
    const years = Object.keys(monthsByYear).map(Number).sort((a,b) => b - a);
    let html = "";
    years.forEach(y => {
      html += `<div class="ana-cal-year"><div class="ana-cal-year-label">${y}</div><div class="ana-cal-months">`;
      monthsByYear[y].forEach(({ label, weeks }) => {
        html += `<div class="ana-cal-month"><div class="ana-cal-month-label">${label}</div><div class="ana-cal-weeks">`;
        weeks.forEach(week => {
          html += `<div class="ana-cal-week">`;
          week.forEach(cell => {
            if (!cell) {
              html += `<div class="cal-cell"></div>`;
            } else {
              html += `<div class="cal-cell ${cell.level}" title="${escapeHtml(cell.title)}"></div>`;
            }
          });
          html += `</div>`;
        });
        html += `</div></div>`;
      });
      html += `</div></div>`;
    });
    wrap.innerHTML = html;
  }

  // ============================================================
  //  04 · DONUTS (dificultad y distancia)
  // ============================================================
  /**
   * Renderiza un donut SVG genérico en el elemento `svgId` a partir de
   * un array de segmentos `parts`. Cada segmento necesita `value`,
   * `css` (clave de color: `easy`/`moderate`/`hard`/`very-hard`),
   * `label` y `pct`. Muestra el total en el centro.
   * @param {string} svgId   ID del elemento `<svg>` destino.
   * @param {Array<{value:number, css:string, label:string, pct:number}>} parts
   */
  function renderDonut(svgId, parts) {
    const svg = document.getElementById(svgId);
    if (!svg || !parts || !parts.length) return;
    svg.innerHTML = "";

    const cx = 70, cy = 70, rOuter = 58, rInner = 38;
    const total = parts.reduce((a, p) => a + p.value, 0) || 1;
    const colorMap = {
      easy: cssVar("--easy") || "#7DAFC9",
      moderate: cssVar("--moderate") || "#B5D17A",
      hard: cssVar("--hard") || "#E8B86D",
      "very-hard": cssVar("--very-hard") || "#E47862",
    };
    let acc = -Math.PI / 2;
    let html = "";
    parts.forEach(p => {
      const angle = (p.value / total) * Math.PI * 2;
      const a0 = acc, a1 = acc + angle;
      acc = a1;
      const big = angle > Math.PI ? 1 : 0;
      const x0 = cx + rOuter * Math.cos(a0);
      const y0 = cy + rOuter * Math.sin(a0);
      const x1 = cx + rOuter * Math.cos(a1);
      const y1 = cy + rOuter * Math.sin(a1);
      const xi1 = cx + rInner * Math.cos(a1);
      const yi1 = cy + rInner * Math.sin(a1);
      const xi0 = cx + rInner * Math.cos(a0);
      const yi0 = cy + rInner * Math.sin(a0);
      const d = `M ${x0} ${y0} A ${rOuter} ${rOuter} 0 ${big} 1 ${x1} ${y1} L ${xi1} ${yi1} A ${rInner} ${rInner} 0 ${big} 0 ${xi0} ${yi0} Z`;
      const fill = colorMap[p.css] || colorMap.moderate;
      html += `<path d="${d}" fill="${fill}"><title>${escapeHtml(p.label)} · ${p.value} (${p.pct}%)</title></path>`;
    });
    html += `<text x="${cx}" y="${cy + 5}" text-anchor="middle" font-family="Fraunces, serif" font-size="22" fill="${cssVar('--text')}">${total}</text>`;
    svg.innerHTML = html;
  }

  // ============================================================
  //  05 · SCATTER · km vs desnivel
  // ============================================================
  /**
   * Renderiza el scatter SVG de km vs desnivel (`#ana-scatter`) a partir
   * de `payload.scatter`. Dibuja grid de 5×5 ticks, etiquetas de ejes y
   * un círculo por ruta coloreado por nivel de dificultad.
   */
  function renderScatter() {
    const svg = document.getElementById("ana-scatter");
    if (!svg || !payload || !payload.scatter) return;
    svg.innerHTML = "";

    const W = 800, H = 360;
    const PAD_L = 50, PAD_R = 24, PAD_T = 18, PAD_B = 38;
    const innerW = W - PAD_L - PAD_R;
    const innerH = H - PAD_T - PAD_B;

    const maxKm = payload.scatterMaxKm || 1;
    const maxGain = payload.scatterMaxGain || 1;

    const cBorder = cssVar("--border");
    const cDim = cssVar("--text-dim");
    const colorMap = {
      easy: cssVar("--easy") || "#7DAFC9",
      moderate: cssVar("--moderate") || "#B5D17A",
      hard: cssVar("--hard") || "#E8B86D",
      "very-hard": cssVar("--very-hard") || "#E47862",
    };

    // grid horizontal y vertical (5 ticks)
    let grid = `<g class="grid">`;
    for (let i = 0; i <= 5; i++) {
      const y = PAD_T + (innerH / 5) * i;
      grid += `<line x1="${PAD_L}" x2="${W - PAD_R}" y1="${y}" y2="${y}" stroke="${cBorder}"/>`;
      const v = Math.round(maxGain * (1 - i / 5));
      grid += `<text x="${PAD_L - 8}" y="${y + 3}" text-anchor="end" font-family="IBM Plex Mono" font-size="10" fill="${cDim}">${fmtInt(v)}</text>`;
    }
    for (let i = 0; i <= 5; i++) {
      const x = PAD_L + (innerW / 5) * i;
      grid += `<line x1="${x}" x2="${x}" y1="${PAD_T}" y2="${H - PAD_B}" stroke="${cBorder}"/>`;
      const v = (maxKm * (i / 5)).toFixed(1).replace(".", ",");
      grid += `<text x="${x}" y="${H - PAD_B + 16}" text-anchor="middle" font-family="IBM Plex Mono" font-size="10" fill="${cDim}">${v}</text>`;
    }
    grid += `</g>`;
    svg.insertAdjacentHTML("beforeend", grid);

    // axis labels
    svg.insertAdjacentHTML("beforeend",
      `<text x="${PAD_L + innerW / 2}" y="${H - 8}" text-anchor="middle" font-family="IBM Plex Mono" font-size="10" fill="${cDim}" letter-spacing="0.1em">KM</text>`);
    svg.insertAdjacentHTML("beforeend",
      `<text x="14" y="${PAD_T + innerH / 2}" text-anchor="middle" transform="rotate(-90 14 ${PAD_T + innerH / 2})" font-family="IBM Plex Mono" font-size="10" fill="${cDim}" letter-spacing="0.1em">DESNIVEL +</text>`);

    // dots — acumulados en string para una sola escritura al DOM
    let dots = "";
    payload.scatter.forEach(s => {
      const x = PAD_L + (s.km / maxKm) * innerW;
      const y = PAD_T + innerH - (s.gain / maxGain) * innerH;
      const fill = colorMap[s.level] || colorMap.moderate;
      dots += `<circle class="scatter-dot" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="5" fill="${fill}" fill-opacity="0.65" stroke="${fill}" stroke-width="1.2"><title>${escapeHtml(s.name)} · ${fmtKm(s.km)} km · ${fmtInt(s.gain)} m+ · ${s.score} /10</title></circle>`;
    });
    svg.innerHTML = svg.innerHTML + dots;
  }

  // ============================================================
  //  08 · COMPARADOR
  // ============================================================
  const cmpState = { A: null, B: null, normalized: false };

  function setupCombo(side) {
    const wrap = document.querySelector(`.ana-combo[data-side="${side}"]`);
    if (!wrap) return;
    const input = $(".ana-combo-input", wrap);
    const list = $(".ana-combo-list", wrap);
    let activeIdx = -1, debounceTimer = null;

    function close() { wrap.classList.remove("is-open"); activeIdx = -1; }
    function open() { wrap.classList.add("is-open"); }

    function highlight(text, q) {
      if (!q) return escapeHtml(text);
      const idx = text.toLowerCase().indexOf(q.toLowerCase());
      if (idx === -1) return escapeHtml(text);
      return escapeHtml(text.slice(0, idx)) +
        `<mark>${escapeHtml(text.slice(idx, idx + q.length))}</mark>` +
        escapeHtml(text.slice(idx + q.length));
    }

    async function fetchItems(q) {
      list.innerHTML = `<div class="ana-combo-empty">buscando…</div>`;
      try {
        const res = await fetch(`/api/comparator/search?q=${encodeURIComponent(q)}&limit=20`, { credentials: "same-origin" });
        if (!res.ok) throw new Error(res.status);
        const data = await res.json();
        const lc = { easy: "#7DAFC9", moderate: "#B5D17A", hard: "#E8B86D", "very-hard": "#E47862" };
        if (!data.items || !data.items.length) { list.innerHTML = `<div class="ana-combo-empty">sin resultados</div>`; return; }
        list.innerHTML = data.items.map((it, i) =>
          `<button type="button" class="ana-combo-item" data-key="${escapeHtml(it.key)}">`+
          `<div>${highlight(it.name, q)}</div>`+
          `<div class="ana-combo-item-meta"><span>${escapeHtml(it.date)}</span>`+
          `<span>${it.km} km</span><span style="color:${lc[it.level]||'#888'}">${escapeHtml(it.levelLabel)}</span></div></button>`
        ).join("");
      } catch (_) { list.innerHTML = `<div class="ana-combo-empty">error al buscar</div>`; }
    }
    function schedule(q) { clearTimeout(debounceTimer); debounceTimer = setTimeout(() => fetchItems(q), 250); }
    input.addEventListener("focus", () => { open(); fetchItems(input.value); });
    input.addEventListener("input", () => { open(); schedule(input.value); });
    function setActive(idx) {
      const visible = $$(".ana-combo-item", list);
      visible.forEach((el, i) => el.classList.toggle("is-active", i === idx));
      if (visible[idx]) visible[idx].scrollIntoView({ block: "nearest" });
    }
    input.addEventListener("keydown", (e) => {
      const visible = $$(".ana-combo-item", list);
      if (e.key === "ArrowDown") { e.preventDefault(); activeIdx = Math.min(visible.length - 1, activeIdx + 1); setActive(activeIdx); }
      else if (e.key === "ArrowUp") { e.preventDefault(); activeIdx = Math.max(0, activeIdx - 1); setActive(activeIdx); }
      else if (e.key === "Enter") {
        e.preventDefault();
        const target = visible[activeIdx] || visible[0];
        if (target) target.click();
      } else if (e.key === "Escape") { close(); }
    });
    list.addEventListener("click", async (e) => {
      const btn = e.target.closest(".ana-combo-item"); if (!btn) return;
      const key = btn.dataset.key;
      const nameEl = btn.querySelector("div:first-child");
      const q = nameEl ? nameEl.textContent.trim() : "";
      try {
        const res = await fetch(`/api/comparator/search?q=${encodeURIComponent(q)}&limit=50`, { credentials: "same-origin" });
        const data = await res.json();
        const item = (data.items || []).find(it => it.key === key);
        if (item) { cmpState[side] = item; input.value = item.name + " · " + item.date; close(); renderComparator(); }
      } catch (_) {}
    });
    function outsideClick(e) { if (!wrap.contains(e.target)) close(); }
    document.addEventListener("click", outsideClick);
    window.MENDI_TEARDOWN.push(() => { document.removeEventListener("click", outsideClick); clearTimeout(debounceTimer); });

  }

  // Toggle normalizado/real
  function initCmpToggle() {
    const btn = document.getElementById("ana-cmp-normalize");
    if (!btn) return;
    btn.addEventListener("click", () => {
      cmpState.normalized = !cmpState.normalized;
      $$(".ana-cmp-toggle-opt", btn).forEach(opt => {
        opt.classList.toggle("is-active",
          (opt.dataset.mode === "norm") === cmpState.normalized);
      });
      renderComparator();
    });
  }

  // ---- Helpers para extraer puntos de los SVG paths del backend ----
  // Los paths ya vienen pre-escalados a viewBox 800x200 con la formula:
  //   x = (800·k)/(n-1)   ∈ [0, 800]   ⇒  km = (x/800) · refKm
  //   y = 25 + 170·(1-(ele-emin)/(emax-emin))  ⇒  ele = emax - ((y-25)/170)·(emax-emin)
  // Se parsea el "line" (M x y L x y L x y ...).
  /**
   * Parsea los tokens numéricos de un SVG path `"M x y L x y ..."` y
   * devuelve un array de puntos `{x, y}` en coordenadas del viewBox
   * 800×200.
   * @param {string} linePath
   * @returns {Array<{x:number, y:number}>}
   */
  function parseLinePoints(linePath) {
    if (!linePath) return [];
    const tokens = linePath.replace(/[ML,]/g, " ").trim().split(/\s+/);
    const pts = [];
    for (let i = 0; i + 1 < tokens.length; i += 2) {
      const x = parseFloat(tokens[i]);
      const y = parseFloat(tokens[i + 1]);
      if (!isNaN(x) && !isNaN(y)) pts.push({ x, y });
    }
    return pts;
  }

  // Devuelve [{km, ele}] reproyectado al sistema mundo.
  /**
   * Reproyecta los puntos SVG de un ítem del comparador al sistema mundo
   * (km, altitud en metros). Invierte la fórmula de escalado del backend:
   * `x ∈ [0,800] → km`, `y ∈ [25,195] → altitud`.
   * @param {{line:string, refKm:number, eleMin:number, eleMax:number}} item
   * @returns {Array<{km:number, ele:number}>}
   */
  function unprojectLine(item) {
    const pts = parseLinePoints(item.line);
    const refKm = item.refKm || item.km || 1;
    const eMin = item.eleMin || 0;
    const eMax = item.eleMax || (eMin + 1);
    const dEle = (eMax - eMin) || 1;
    return pts.map(p => ({
      km: (p.x / 800) * refKm,
      ele: eMax - ((p.y - 25) / 170) * dEle,
    }));
  }

  function renderComparator() {
    const svg = document.getElementById("ana-cmp-overlay");
    if (!svg) return;
    svg.innerHTML = "";

    const A = cmpState.A;
    const B = cmpState.B;
    const normalized = cmpState.normalized;

    const colorA = cssVar("--accent-warm") || "#E8B86D";
    const colorB = cssVar("--accent-cool") || "#7DAFC9";
    const cBorder = cssVar("--border");
    const cDim = cssVar("--text-dim");

    const legA = document.querySelector('#ana-cmp-legend [data-side="A"]');
    const legB = document.querySelector('#ana-cmp-legend [data-side="B"]');
    if (legA) legA.textContent = A ? `${A.name} · ${A.date}` : "sesión A";
    if (legB) legB.textContent = B ? `${B.name} · ${B.date}` : "sesión B";

    const W = 800, H = 260;
    const PAD_L = 50, PAD_R = 24, PAD_T = 16, PAD_B = 36;
    const innerW = W - PAD_L - PAD_R;
    const innerH = H - PAD_T - PAD_B;

    const ptsA = A ? unprojectLine(A) : [];
    const ptsB = B ? unprojectLine(B) : [];

    if (!ptsA.length && !ptsB.length) {
      svg.insertAdjacentHTML("beforeend",
        `<text x="${W/2}" y="${H/2}" text-anchor="middle" font-family="IBM Plex Mono" font-size="12" fill="${cDim}">elige dos sesiones para comparar</text>`);
      updateCmpAxes(0, 0);
      updateCmpTable();
      return;
    }

    // En modo normalizado cada ruta ocupa el 100% del eje X
    const maxKmA = A ? A.refKm : 0;
    const maxKmB = B ? B.refKm : 0;
    const maxKm = normalized ? 1.0 : Math.max(maxKmA, maxKmB, 0.1);

    function normX(pt, refKm) {
      return normalized ? pt.km / (refKm || 1) : pt.km / maxKm;
    }

    const allPts = [
      ...ptsA.map(p => p.ele),
      ...ptsB.map(p => p.ele),
    ];
    const minEle = allPts.length ? Math.min(...allPts) : 0;
    const maxEle = allPts.length ? Math.max(...allPts) : 1;
    const dEle = (maxEle - minEle) || 1;

    function project(pt, refKm) {
      const x = PAD_L + normX(pt, refKm) * innerW;
      const y = PAD_T + (1 - (pt.ele - minEle) / dEle) * innerH;
      return [x, y];
    }

    // Grid
    let grid = "";
    for (let i = 0; i <= 4; i++) {
      const y = PAD_T + (innerH / 4) * i;
      grid += `<line x1="${PAD_L}" x2="${W - PAD_R}" y1="${y}" y2="${y}" stroke="${cBorder}" stroke-width="0.6" stroke-dasharray="2 4"/>`;
      const ele = Math.round(maxEle - ((maxEle - minEle) / 4) * i);
      grid += `<text x="${PAD_L - 8}" y="${y + 3}" text-anchor="end" font-family="IBM Plex Mono" font-size="9" fill="${cDim}">${fmtInt(ele)}</text>`;
    }
    svg.insertAdjacentHTML("beforeend", grid);
    svg.insertAdjacentHTML("beforeend",
      `<text x="14" y="${PAD_T + innerH/2}" text-anchor="middle" transform="rotate(-90 14 ${PAD_T + innerH/2})" font-family="IBM Plex Mono" font-size="9" fill="${cDim}" letter-spacing="0.1em">ALTITUD m</text>`);

    function paintRoute(pts, color, refKm) {
      if (!pts.length) return;
      const projected = pts.map(p => project(p, refKm));
      let line = "";
      projected.forEach(([x, y], i) => { line += (i === 0 ? "M" : "L") + ` ${x.toFixed(1)} ${y.toFixed(1)} `; });
      let area = `M ${projected[0][0].toFixed(1)} ${PAD_T + innerH} `;
      projected.forEach(([x, y]) => { area += `L ${x.toFixed(1)} ${y.toFixed(1)} `; });
      area += `L ${projected[projected.length - 1][0].toFixed(1)} ${PAD_T + innerH} Z`;
      const gid = `cmp-grad-${Math.random().toString(36).slice(2, 6)}`;
      svg.insertAdjacentHTML("beforeend",
        `<defs><linearGradient id="${gid}" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stop-color="${color}" stop-opacity="0.30"/>
          <stop offset="100%" stop-color="${color}" stop-opacity="0"/>
        </linearGradient></defs>`);
      svg.insertAdjacentHTML("beforeend", `<path d="${area}" fill="url(#${gid})"/>`);
      svg.insertAdjacentHTML("beforeend",
        `<path d="${line}" fill="none" stroke="${color}" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round"/>`);
    }

    paintRoute(ptsA, colorA, maxKmA);
    paintRoute(ptsB, colorB, maxKmB);

    // Eje X label
    const midLabel = normalized ? "50%" : `${fmtKm(maxKm / 2)} km`;
    const maxLabel = normalized ? "100%" : `${fmtKm(maxKm)} km`;
    updateCmpAxes(midLabel, maxLabel);
    updateCmpTable();

    // Cursor sincronizado
    initCmpCursor(ptsA, ptsB, maxKmA, maxKmB, minEle, maxEle, dEle, normalized, maxKm,
                  PAD_L, PAD_R, PAD_T, PAD_B, innerW, innerH, W, H, colorA, colorB);
  }

  function initCmpCursor(ptsA, ptsB, maxKmA, maxKmB, minEle, maxEle, dEle, normalized, maxKm,
                         PAD_L, PAD_R, PAD_T, PAD_B, innerW, innerH, W, H, colorA, colorB) {
    const wrap = document.getElementById("ana-cmp-chart-wrap");
    const svg  = document.getElementById("ana-cmp-overlay");
    const cursor = document.getElementById("ana-cmp-cursor");
    const tip    = document.getElementById("ana-cmp-cursor-tip");
    if (!wrap || !svg || !cursor || !tip) return;

    function interpEle(pts, refKm, xPct) {
      if (!pts.length) return null;
      const km = xPct * (normalized ? refKm : maxKm);
      // buscar el segmento que contiene km
      for (let i = 1; i < pts.length; i++) {
        if (pts[i].km >= km) {
          const t = (km - pts[i-1].km) / (pts[i].km - pts[i-1].km || 1);
          return pts[i-1].ele + t * (pts[i].ele - pts[i-1].ele);
        }
      }
      return pts[pts.length - 1].ele;
    }

    function onMove(e) {
      const rect = svg.getBoundingClientRect();
      const scaleX = W / rect.width;
      const mx = (e.clientX - rect.left) * scaleX;
      if (mx < PAD_L || mx > W - PAD_R) { cursor.hidden = true; tip.hidden = true; return; }
      const xPct = (mx - PAD_L) / innerW;
      // posición del cursor en px del wrap
      const cursorX = (mx / W) * rect.width;
      cursor.style.left = `${cursorX}px`;
      cursor.hidden = false;

      const eleA = interpEle(ptsA, maxKmA, xPct);
      const eleB = interpEle(ptsB, maxKmB, xPct);
      const kmA  = xPct * (normalized ? maxKmA : maxKm);
      const kmB  = xPct * (normalized ? maxKmB : maxKm);

      let html = "";
      if (eleA !== null) html += `<span style="color:${colorA}">● A</span> ${fmtInt(Math.round(eleA))} m · ${fmtKm(kmA)} km<br>`;
      if (eleB !== null) html += `<span style="color:${colorB}">● B</span> ${fmtInt(Math.round(eleB))} m · ${fmtKm(kmB)} km`;
      tip.innerHTML = html;
      tip.hidden = false;
      // posicionar tip
      const tipLeft = cursorX + 10;
      tip.style.left = `${Math.min(tipLeft, rect.width - 140)}px`;
      tip.style.top = "16px";
    }

    function onLeave() { cursor.hidden = true; tip.hidden = true; }

    svg.addEventListener("mousemove", onMove);
    svg.addEventListener("mouseleave", onLeave);
    window.MENDI_TEARDOWN.push(() => {
      svg.removeEventListener("mousemove", onMove);
      svg.removeEventListener("mouseleave", onLeave);
    });
  }

  function updateCmpAxes(midLabel, maxLabel) {
    const mid = document.getElementById("ana-cmp-mid-km");
    const mx  = document.getElementById("ana-cmp-max-km");
    if (mid) mid.textContent = midLabel || "—";
    if (mx)  mx.textContent  = maxLabel  || "—";
  }

  function updateCmpTable() {
    const A = cmpState.A;
    const B = cmpState.B;
    const fmt = (item, key) => {
      if (!item) return "—";
      switch (key) {
        case "distance": return `${fmtKm(item.km)} <em>km</em>`;
        case "gain":     return `${fmtInt(item.gain)} <em>m</em>`;
        case "loss":     return `${fmtInt(item.loss)} <em>m</em>`;
        case "gainpct":  return `${String(item.gainPct).replace(".",",")} <em>m/km</em>`;
        case "losspct":  return `${String(item.lossPct).replace(".",",")} <em>m/km</em>`;
        case "score":    return `${String(item.score).replace(".", ",")} <em>/10</em>`;
        case "duration": return item.duration || "—";
        case "pace":     return item.pace || "—";
        case "alt":      return `${fmtInt(item.eleMin)}–${fmtInt(item.eleMax)} <em>m</em>`;
        default: return "—";
      }
    };
    const winners = computeWinners(A, B);
    document.querySelectorAll("#ana-cmp-table .ana-cmp-row[data-metric]").forEach(row => {
      const metric = row.dataset.metric;
      const cellA = row.querySelector(".ana-cmp-A");
      const cellB = row.querySelector(".ana-cmp-B");
      if (cellA) cellA.innerHTML = fmt(A, metric);
      if (cellB) cellB.innerHTML = fmt(B, metric);
      cellA && cellA.classList.toggle("is-best", winners[metric] === "A");
      cellB && cellB.classList.toggle("is-best", winners[metric] === "B");
    });
  }

  function computeWinners(A, B) {
    const out = { distance: null, gain: null, loss: null, gainpct: null, losspct: null,
                  score: null, duration: null, pace: null, alt: null };
    if (!A || !B) return out;
    const cmp = (a, b, dir) => {
      if (a == null || b == null || a === b) return null;
      return (dir === "max" ? a > b : a < b) ? "A" : "B";
    };
    out.distance = cmp(A.km, B.km, "max");
    out.gain     = cmp(A.gain, B.gain, "max");
    out.loss     = cmp(A.loss, B.loss, "max");
    out.gainpct  = cmp(A.gainPct, B.gainPct, "max");
    out.losspct  = cmp(A.lossPct, B.lossPct, "max");
    out.score    = cmp(A.score, B.score, "max");
    const paceSec = s => { if (!s) return Infinity; const m = s.match(/(\d+):(\d+)/); return m ? +m[1]*60 + +m[2] : Infinity; };
    const durSec  = s => { if (!s) return Infinity; let t=0; const h=s.match(/(\d+)\s*h/),m=s.match(/(\d+)\s*m/); if(h)t+=+h[1]*3600; if(m)t+=+m[1]*60; return t||Infinity; };
    out.pace     = cmp(paceSec(A.pace), paceSec(B.pace), "min");
    out.duration = cmp(durSec(A.duration), durSec(B.duration), "min");
    return out;
  }

  // Selección automática inicial: las dos primeras rutas distintas
  /**
   * Selecciona automáticamente las dos primeras rutas distintas de
   * `payload.comparator` como estado inicial del comparador y llama a
   * `renderComparator`. Se ejecuta una vez al inicializar la página.
   */
  function autoSelectComparator() {
    fetch("/api/comparator/search?q=&limit=2", { credentials: "same-origin" })
      .then(r => r.json())
      .then(data => {
        const list = data.items || [];
        if (list[0]) {
          cmpState.A = list[0];
          const inA = document.querySelector('[data-side="A"] .ana-combo-input');
          if (inA) inA.value = list[0].name + " · " + list[0].date;
        }
        if (list[1]) {
          cmpState.B = list[1];
          const inB = document.querySelector('[data-side="B"] .ana-combo-input');
          if (inB) inB.value = list[1].name + " · " + list[1].date;
        }
        renderComparator();
      }).catch(() => {});
  }

  // ============================================================
  //  04 · KPI SPARKLINE km/sesión por mes
  // ============================================================
  function renderKpiSpark() {
    const svg = document.getElementById("ana-kpi-spark");
    const elAvg = document.getElementById("ana-kpi-spark-avg");
    const elTrend = document.getElementById("ana-kpi-spark-trend");
    if (!svg || !payload || !payload.monthly) return;
    const months = payload.monthly.filter(m => m.sessions > 0);
    if (!months.length) return;
    const vals = months.map(m => m.km / m.sessions);
    const avg = vals.reduce((a, b) => a + b, 0) / vals.length;
    const maxV = Math.max(...vals) * 1.1 || 1;
    const W = svg.clientWidth || 300;
    const H = 70;
    const padX = 6, padT = 8, padB = 18;
    const innerW = W - padX * 2;
    const innerH = H - padT - padB;
    const accent  = cssVar("--accent") || "#B5D17A";
    const textDim = cssVar("--text-dim") || "#888";
    const border  = cssVar("--border") || "#333";
    const xOf = i => padX + (i / Math.max(vals.length - 1, 1)) * innerW;
    const yOf = v => padT + innerH - (v / maxV) * innerH;
    let html = "";
    // línea de media
    html += `<line x1="${padX}" x2="${W - padX}" y1="${yOf(avg)}" y2="${yOf(avg)}"
      stroke="${border}" stroke-width="1" stroke-dasharray="3 3" opacity="0.7"/>`;
    // área
    const areaPts = `${xOf(0)},${padT + innerH} ` +
      vals.map((v, i) => `${xOf(i)},${yOf(v)}`).join(" ") +
      ` ${xOf(vals.length - 1)},${padT + innerH}`;
    html += `<defs><linearGradient id="kpi-grad" x1="0" x2="0" y1="0" y2="1">
      <stop offset="0%" stop-color="${accent}" stop-opacity="0.4"/>
      <stop offset="100%" stop-color="${accent}" stop-opacity="0"/>
    </linearGradient></defs>`;
    html += `<polygon points="${areaPts}" fill="url(#kpi-grad)"/>`;
    // línea
    html += `<polyline points="${vals.map((v, i) => `${xOf(i)},${yOf(v)}`).join(" ")}" fill="none" stroke="${accent}" stroke-width="1.8" stroke-linejoin="round"/>`;
    // puntos y etiquetas
    vals.forEach((v, i) => {
      html += `<circle cx="${xOf(i)}" cy="${yOf(v)}" r="2.5" fill="${accent}"/>`;
      html += `<text x="${xOf(i)}" y="${H - 4}" text-anchor="middle"
        font-family="var(--font-mono)" font-size="8" fill="${textDim}">${months[i].label}</text>`;
    });
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.innerHTML = html;
    if (elAvg) elAvg.textContent = `media ${fmtKm(avg)} km/sal`;
    if (elTrend && vals.length >= 6) {
      const recent = vals.slice(-3).reduce((a, b) => a + b, 0) / 3;
      const older  = vals.slice(-6, -3).reduce((a, b) => a + b, 0) / 3;
      const diff = recent - older;
      if (Math.abs(diff) < 0.3) {
        elTrend.textContent = "→ estable"; elTrend.className = "";
      } else if (diff > 0) {
        elTrend.textContent = `↑ +${fmtKm(diff)} km`; elTrend.className = "up";
      } else {
        elTrend.textContent = `↓ ${fmtKm(diff)} km`; elTrend.className = "down";
      }
    }
  }

  //  03 · EVOLUCIÓN MENSUAL (km actual vs año anterior)
  // ============================================================
  function renderEvoChart() {
    const svg = document.getElementById("ana-evo-svg");
    const tooltip = document.getElementById("ana-evo-tooltip");
    const hoverInfo = document.getElementById("ana-evo-hover-info");
    if (!svg || !payload || !payload.monthly) return;

    const data = payload.monthly;          // [{label, km, sessions, unique}, ...]
    const prevYears = payload.monthlyPrevYear || {};  // {"2023": [km,...], "2024": [km,...], ...}
    const W = svg.clientWidth || 700;
    const H = 180;
    const padL = 36, padR = 12, padT = 16, padB = 24;
    const innerW = W - padL - padR;
    const innerH = H - padT - padB;

    const prevEntries = Object.entries(prevYears).sort((a, b) => +a[0] - +b[0]); // orden cronológico
    const allKm = [
      ...data.map(d => d.km),
      ...prevEntries.flatMap(([, vals]) => vals),
    ].filter(v => v > 0);
    const maxKm = allKm.length ? Math.max(...allKm) * 1.1 : 1;

    const accent   = cssVar("--accent-warm") || "#E8B86D";  // año actual destacado
    const accentHist = cssVar("--accent-cool") || "#7DAFC9"; // años históricos
    const textDim  = cssVar("--text-dim") || "#888";
    const border   = cssVar("--border")   || "#333";
    const surface2 = cssVar("--surface-2") || "rgba(255,255,255,0.04)";

    const xOf = i => padL + (i / (data.length - 1)) * innerW;
    const yOf = v => padT + innerH - (v / maxKm) * innerH;

    // grid lines
    const ticks = 4;
    let html = "";
    for (let i = 0; i <= ticks; i++) {
      const v = (maxKm / ticks) * i;
      const y = yOf(v);
      html += `<line x1="${padL}" x2="${W - padR}" y1="${y}" y2="${y}"
        stroke="${border}" stroke-width="1" stroke-dasharray="3 4" opacity="0.5"/>`;
      html += `<text x="${padL - 4}" y="${y + 4}" text-anchor="end"
        font-family="var(--font-mono)" font-size="9" fill="${textDim}">${Math.round(v)}</text>`;
    }

    // líneas de años históricos: opacidad decreciente hacia el pasado
    if (prevEntries.length) {
      const n = prevEntries.length;
      prevEntries.forEach(([yr, vals], idx) => {
        // el más reciente (idx = n-1) tiene opacidad 0.75, el más antiguo 0.35
        const opacity = 0.35 + (idx / Math.max(n - 1, 1)) * 0.40;
        const pts = data.map((_, i) => `${xOf(i)},${yOf(vals[i] || 0)}`).join(" ");
        const areaPts = `${padL},${yOf(0)} ` + data.map((_, i) => `${xOf(i)},${yOf(vals[i] || 0)}`).join(" ") + ` ${xOf(data.length - 1)},${yOf(0)}`;
        html += `<polygon points="${areaPts}" fill="${accentHist}" opacity="${(opacity * 0.25).toFixed(2)}"/>`;
        html += `<polyline points="${pts}" fill="none" stroke="${accentHist}" stroke-width="1.5"
          opacity="${opacity.toFixed(2)}" stroke-dasharray="4 3"/>`;
        // etiqueta del año al final de la línea
        const lastVal = vals[data.length - 1] || 0;
        if (lastVal > 0) {
          html += `<text x="${xOf(data.length - 1) + 4}" y="${yOf(lastVal) + 3}"
            font-family="var(--font-mono)" font-size="8" fill="${accentHist}" opacity="${opacity.toFixed(2)}">${yr}</text>`;
        }
      });
    }

    // área año actual (accent-warm, destacado)
    const areaAct = `${padL},${yOf(0)} ` + data.map((d, i) => `${xOf(i)},${yOf(d.km)}`).join(" ") + ` ${xOf(data.length - 1)},${yOf(0)}`;
    html += `<defs><linearGradient id="evo-grad" x1="0" x2="0" y1="0" y2="1">
      <stop offset="0%" stop-color="${accent}" stop-opacity="0.45"/>
      <stop offset="100%" stop-color="${accent}" stop-opacity="0.03"/>
    </linearGradient></defs>`;
    html += `<polygon points="${areaAct}" fill="url(#evo-grad)"/>`;
    const lineAct = data.map((d, i) => `${xOf(i)},${yOf(d.km)}`).join(" ");
    html += `<polyline points="${lineAct}" fill="none" stroke="${accent}" stroke-width="2.5" stroke-linejoin="round"/>`;

    // puntos interactivos + etiquetas eje X (todos los meses, eje fijo)
    data.forEach((d, i) => {
      const x = xOf(i), y = yOf(d.km);
      html += `<text x="${x}" y="${H - 4}" text-anchor="middle"
        font-family="var(--font-mono)" font-size="9" fill="${textDim}">${d.label}</text>`;
      html += `<circle cx="${x}" cy="${y}" r="14" fill="transparent" class="evo-hit" data-i="${i}"/>`;
      html += `<circle cx="${x}" cy="${y}" r="${d.km > 0 ? 3.5 : 0}" fill="${accent}" class="evo-dot" data-i="${i}"/>`;
    });

    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.innerHTML = html;

    // hover
    svg.querySelectorAll(".evo-hit").forEach(el => {
      el.addEventListener("mouseenter", e => {
        const i = +el.dataset.i;
        const d = data[i];
        const dot = svg.querySelector(`.evo-dot[data-i="${i}"]`);
        if (dot) dot.setAttribute("r", "5");
        const rect = svg.getBoundingClientRect();
        const wrap = svg.parentElement;
        const wRect = wrap.getBoundingClientRect();
        const cx = xOf(i), cy = yOf(d.km);
        const scaleX = rect.width / W;
        const tx = cx * scaleX + (rect.left - wRect.left);
        const ty = cy * (rect.height / H) + (rect.top - wRect.top);
        // filas de años históricos (del más reciente al más antiguo)
        const histRows = prevEntries.slice().reverse().map(([yr, vals]) => {
          const v = vals[i] || 0;
          return `<div class="ana-evo-tooltip-row">
            <span>${yr}</span>
            <span class="ana-evo-tooltip-prev">${fmtKm(v)} km</span>
          </div>`;
        }).join("");
        const prevRecent = prevEntries.length ? (prevEntries[prevEntries.length - 1][1][i] || 0) : null;
        tooltip.innerHTML = `
          <div class="ana-evo-tooltip-label">${d.label}</div>
          <div class="ana-evo-tooltip-row">
            <span>este período</span>
            <span class="ana-evo-tooltip-val">${fmtKm(d.km)} km · ${d.sessions} sal${d.sessions === 1 ? '' : 's'}</span>
          </div>
          ${histRows}
          ${d.km > 0 && prevRecent > 0 ? `<div class="ana-evo-tooltip-row">
            <span>vs año ant.</span>
            <span class="ana-evo-tooltip-val" style="color:${d.km >= prevRecent ? accent : '#E47862'}">${d.km >= prevRecent ? '+' : ''}${fmtKm(d.km - prevRecent)} km</span>
          </div>` : ''}`;
        tooltip.style.left = `${Math.min(tx + 10, wRect.width - 180)}px`;
        tooltip.style.top  = `${Math.max(ty - 60, 0)}px`;
        tooltip.hidden = false;
        if (hoverInfo) hoverInfo.textContent = `${d.label} · ${fmtKm(d.km)} km`;
      });
      el.addEventListener("mouseleave", e => {
        const i = +el.dataset.i;
        const dot = svg.querySelector(`.evo-dot[data-i="${i}"]`);
        if (dot) dot.setAttribute("r", data[i].km > 0 ? "3" : "0");
        tooltip.hidden = true;
        if (hoverInfo) hoverInfo.textContent = "";
      });
    });
  }

  function renderWeekdayChart() {
    const svg = document.getElementById("ana-chart-weekday");
    if (!svg || !payload || !payload.kmByWeekday) return;
    const data = payload.kmByWeekday;
    if (!data.length) { svg.innerHTML = ""; return; }

    const W = Math.max(svg.getBoundingClientRect().width || 0, svg.parentElement ? svg.parentElement.getBoundingClientRect().width || 0 : 0) || 300;
    const H = 90;
    const PAD_T = 14, PAD_B = 18, PAD_X = 4;
    const innerW = W - PAD_X * 2;
    const innerH = H - PAD_T - PAD_B;
    const labels = ["L", "M", "X", "J", "V", "S", "D"];
    const maxV = Math.max(...data, 1);
    const slotW = innerW / data.length;
    const barW = Math.max(4, slotW * 0.6);
    const cAccent = cssVar("--accent");
    const cWarm = cssVar("--accent-warm");
    const cDim = cssVar("--text-dim");
    const cText = cssVar("--text");

    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    let html = "";
    data.forEach((v, i) => {
      const cx = PAD_X + slotW * i + slotW / 2;
      const barH = Math.max(2, (v / maxV) * innerH);
      const y = PAD_T + innerH - barH;
      const isMax = v === maxV;
      const color = isMax ? cWarm : cAccent;
      html += `<rect x="${(cx - barW / 2).toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${barH.toFixed(1)}" rx="2" fill="${color}" fill-opacity="${isMax ? 0.9 : 0.55}"/>`;
      html += `<text x="${cx.toFixed(1)}" y="${H - 4}" text-anchor="middle" font-family="IBM Plex Mono" font-size="8" fill="${cDim}">${labels[i]}</text>`;
      if (v > 0) html += `<text x="${cx.toFixed(1)}" y="${(y - 3).toFixed(1)}" text-anchor="middle" font-family="IBM Plex Mono" font-size="7.5" fill="${isMax ? cWarm : cText}">${v}</text>`;
    });
    svg.innerHTML = html;
  }

  // ============================================================
  //  02-H · ESTACIONALIDAD
  // ============================================================
  function renderSeasonalityChart() {
    const svg = document.getElementById("ana-chart-seasonality");
    if (!svg || !payload || !payload.kmByMonthHist) return;
    const data = payload.kmByMonthHist;
    if (!data.length) { svg.innerHTML = ""; return; }

    const W = Math.max(svg.getBoundingClientRect().width || 0, svg.parentElement ? svg.parentElement.getBoundingClientRect().width || 0 : 0) || 300;
    const H = 90;
    const PAD_T = 14, PAD_B = 18, PAD_X = 4;
    const innerW = W - PAD_X * 2;
    const innerH = H - PAD_T - PAD_B;
    const labels = ["E","F","M","A","M","J","J","A","S","O","N","D"];
    const maxV = Math.max(...data, 1);
    const slotW = innerW / data.length;
    const barW = Math.max(4, slotW * 0.6);
    const cCool = cssVar("--accent-cool");
    const cWarm = cssVar("--accent-warm");
    const cDim = cssVar("--text-dim");
    const cText = cssVar("--text");

    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    let html = "";
    data.forEach((v, i) => {
      const cx = PAD_X + slotW * i + slotW / 2;
      const barH = Math.max(2, (v / maxV) * innerH);
      const y = PAD_T + innerH - barH;
      const isMax = v === maxV;
      const color = isMax ? cWarm : cCool;
      html += `<rect x="${(cx - barW / 2).toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${barH.toFixed(1)}" rx="2" fill="${color}" fill-opacity="${isMax ? 0.9 : 0.55}"/>`;
      html += `<text x="${cx.toFixed(1)}" y="${H - 4}" text-anchor="middle" font-family="IBM Plex Mono" font-size="8" fill="${cDim}">${labels[i]}</text>`;
      if (isMax) html += `<text x="${cx.toFixed(1)}" y="${(y - 3).toFixed(1)}" text-anchor="middle" font-family="IBM Plex Mono" font-size="7.5" fill="${cWarm}">${v}</text>`;
    });
    svg.innerHTML = html;
  }

  // ============================================================
  //  Nav de secciones: marca el link activo al hacer scroll
  // ============================================================
  function initSectionNav() {
    const nav = document.getElementById("ana-section-nav");
    if (!nav) return;
    const links = Array.from(nav.querySelectorAll(".ana-snav-link"));
    const sections = links.map(l => document.querySelector(l.getAttribute("href"))).filter(Boolean);
    if (!sections.length) return;

    const navH = nav.offsetHeight;
    function update() {
      const scrollY = window.scrollY + navH + 8;
      let active = sections[0];
      for (const s of sections) {
        if (s.offsetTop <= scrollY) active = s;
      }
      links.forEach(l => {
        l.classList.toggle("is-active", l.getAttribute("href") === "#" + active.id);
      });
    }
    window.addEventListener("scroll", update, { passive: true });
    update();
    window.MENDI_TEARDOWN.push(() => window.removeEventListener("scroll", update));
  }

  // ============================================================
  //  Reaccion al cambio de tema (re-tinta charts, mapa)
  // ============================================================
  /**
   * Re-renderiza todos los charts y el mapa de calor al recibir el
   * evento `mendi:themechange`. Garantiza que los colores SVG y las
   * teselas del mapa reflejen el tema nuevo.
   */
  function onThemeChange() {
    updateHeatTiles();
    renderRatioDonut();
    renderStreakSpark();
    renderDiscLine();
    renderEvoChart();
    if (payload) {
      renderDonut("ana-donut-diff", payload.donutDifficulty);
      renderDonut("ana-donut-dist", payload.donutDistance);
      renderKpiSpark();
      renderScatter();
    }
    renderComparator();
    requestAnimationFrame(() => {
      renderWeekdayChart();
      renderSeasonalityChart();
    });
  }

  // ============================================================
  //  Registro
  // ============================================================
  window.MENDI_PAGES = window.MENDI_PAGES || {};
  window.MENDI_TEARDOWN = window.MENDI_TEARDOWN || [];

  window.MENDI_PAGES.analisis = {
    init() {
      // Tema sincronizado
      let saved = "dark";
      try { saved = localStorage.getItem(STORAGE_THEME) || "dark"; } catch (_) {}
      if (typeof window.applyTheme === "function") window.applyTheme(saved);

      payload = readPayload();
      initRangeChips();
      if (!payload) return;  // empty state — sin más

      // El heatmap es la pieza más cara del primer render (Leaflet + tiles +
      // capa de calor con redraw vía canvas). Lo diferimos para que el resto
      // de la página pinte primero y no aparezca como violación del handler
      // `load`. La función registra su propio teardown.
      ric(() => { try { initHeatMap(); } catch (e) { console.error(e); } });
      renderRatioDonut();
      renderStreakSpark();
      renderDiscLine();
      renderEvoChart();
      renderDonut("ana-donut-diff", payload.donutDifficulty);
      renderDonut("ana-donut-dist", payload.donutDistance);
      renderKpiSpark();
      renderScatter();
      requestAnimationFrame(() => {
        renderWeekdayChart();
        renderSeasonalityChart();
      });
      setupCombo("A");
      setupCombo("B");
      initCmpToggle();
      autoSelectComparator();
      initSectionNav();

      const onTheme = () => onThemeChange();
      document.addEventListener("mendi:themechange", onTheme);
      window.MENDI_TEARDOWN.push(() => {
        document.removeEventListener("mendi:themechange", onTheme);
        payload = null;
        cmpState.A = null; cmpState.B = null;
      });
    }
  };
})();
