(function () {
  // ---- Tabs ----
  const tabs = document.querySelectorAll(".tabs .tab");
  const panels = document.querySelectorAll(".tab-panel");
  tabs.forEach(t => {
    t.addEventListener("click", () => {
      const target = t.dataset.tab;
      tabs.forEach(x => {
        const on = x === t;
        x.classList.toggle("active", on);
        x.setAttribute("aria-selected", on ? "true" : "false");
      });
      panels.forEach(p => {
        const on = p.id === `tab-${target}`;
        p.classList.toggle("active", on);
        if (on) p.removeAttribute("hidden"); else p.setAttribute("hidden", "");
      });
    });
  });

  // ---- Importación con feedback por archivo (NDJSON streaming) ----
  // Reemplaza el POST clásico por una llamada a /importar/stream que emite
  // eventos JSON por línea. La UI los va consumiendo y actualiza barra de
  // progreso, contador, archivo en curso y un log de últimos eventos.
  const form = document.getElementById("import-form");
  const overlay = document.getElementById("import-overlay");
  const submitBtn = document.getElementById("dz-submit");
  const ovTitle = document.getElementById("import-overlay-title");
  const ovSub = document.getElementById("import-overlay-sub");
  const ovFill = document.getElementById("import-progress-fill");
  const ovCounter = document.getElementById("import-progress-counter");
  const ovCurrent = document.getElementById("import-progress-current");
  const ovLog = document.getElementById("import-log");
  const ovActions = document.getElementById("import-overlay-actions");
  const ovSpinner = document.getElementById("import-spinner");
  const ovClose = document.getElementById("import-overlay-close");

  function ovShow() {
    overlay.classList.add("visible");
    overlay.setAttribute("aria-hidden", "false");
  }
  function ovHide() {
    overlay.classList.remove("visible");
    overlay.setAttribute("aria-hidden", "true");
  }
  function ovReset() {
    if (ovTitle) ovTitle.textContent = "Procesando GPX…";
    // Hasta que llegue el primer evento `start` el servidor aún está
    // recibiendo/parseando el multipart: mostrar fase de subida, no 0/0.
    if (ovSub) ovSub.textContent = "Subiendo archivos…";
    if (ovFill) ovFill.style.width = "0%";
    if (ovCounter) ovCounter.textContent = "";
    if (ovCurrent) ovCurrent.textContent = "";
    if (ovLog) ovLog.innerHTML = "";
    if (ovActions) ovActions.style.display = "none";
    if (ovSpinner) ovSpinner.style.display = "";
  }
  function logEvent(kind, text) {
    if (!ovLog) return;
    const li = document.createElement("li");
    li.className = `import-log-line import-log-${kind}`;
    li.textContent = text;
    ovLog.appendChild(li);
    // Mantener solo las últimas 8 líneas visibles
    while (ovLog.children.length > 8) ovLog.removeChild(ovLog.firstChild);
    ovLog.scrollTop = ovLog.scrollHeight;
  }
  function phaseLabel(phase) {
    return ({
      "leyendo": "leyendo",
      "parseando": "parseando GPX",
      "geocodificando": "buscando zona",
      "guardando": "guardando",
    })[phase] || phase;
  }

  if (form && overlay) {
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      // Defensa frente a HTMX: el <body> tiene hx-boost="true" y, aunque
      // hayamos puesto hx-boost="false" en el <form>, queremos asegurarnos
      // de que ningún otro listener (HTMX u otro) procese este submit en
      // paralelo y dispare un POST clásico a /importar que redirija a "/".
      e.stopImmediatePropagation();
      const fileInput = form.querySelector('input[type="file"]');
      // Se construye un FormData por lote en postChunk (no uno global):
      // el input file se lee abajo vía fileInput.files.
      // Si no hay archivos, no hacemos nada (el botón ya estaría deshabilitado)
      if (!fileInput || !fileInput.files || !fileInput.files.length) return;

      ovReset();
      ovShow();
      if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.textContent = "Procesando…";
      }

      let total = 0, imported = 0, duplicates = 0, errors = 0;

      // Bacheo: el servidor parsea el multipart entero antes del primer
      // evento, así que un lote gigante deja el modal en "Subiendo…" sin
      // feedback. Se envía en grupos secuenciales de 5 (punto dulce
      // observado) con progreso global agregado.
      const allFiles = Array.from(fileInput.files);
      const CHUNK = 5;
      total = allFiles.length;
      ovCounter.textContent = `0 / ${total}`;
      ovSub.textContent = total === 1
        ? "Procesando 1 archivo…"
        : `Procesando ${total} archivos…`;
      let doneCount = 0;

      const renderProgress = (i) => {
        ovCounter.textContent = `${doneCount + i} / ${total}`;
        ovFill.style.width = `${((doneCount + i) / total) * 100}%`;
      };

      async function postChunk(chunk, chunkIndex) {
        const fd = new FormData();
        chunk.forEach((f) => fd.append("files", f, f.name));
        const resp = await fetch("/importar/stream", { method: "POST", body: fd });
        if (!resp.ok || !resp.body) {
          let detail = resp.statusText || "fallo en el servidor";
          try {
            const data = await resp.clone().json();
            if (data && data.detail) detail = data.detail;
          } catch (_) {}
          throw new Error(`lote ${chunkIndex + 1} (${resp.status}): ${detail}`);
        }

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buf = "";

        // Loop de lectura: NDJSON, una línea por evento.
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          let nl;
          while ((nl = buf.indexOf("\n")) !== -1) {
            const line = buf.slice(0, nl).trim();
            buf = buf.slice(nl + 1);
            if (!line) continue;
            let ev;
            try { ev = JSON.parse(line); } catch (_) { continue; }
            if (ev.type === "start") {
              // El total del lote ya se mostró arriba; este es por chunk.
              ovSub.textContent = `Procesando ${total} archivos… (lote ${chunkIndex + 1})`;
            } else if (ev.type === "file") {
              ovCurrent.textContent = `${ev.name} · ${phaseLabel(ev.phase)}`;
            } else if (ev.type === "ok") {
              imported++;
              renderProgress(ev.i);
              logEvent("ok", `✓ ${ev.name_clean || ev.name}`);
            } else if (ev.type === "dup") {
              duplicates++;
              renderProgress(ev.i);
              logEvent("dup", `⊘ ${ev.name} · duplicado de «${ev.existing}»`);
            } else if (ev.type === "error") {
              errors++;
              renderProgress(ev.i || 0);
              logEvent("err", `✗ ${ev.name} · ${ev.message}`);
            } else if (ev.type === "done") {
              // Resumen parcial: el final se compone al terminar todos.
              doneCount += (ev.imported || 0) + (ev.duplicates || 0) + (ev.errors || 0);
            }
          }
        }
      }

      try {
        for (let c = 0; c < allFiles.length; c += CHUNK) {
          await postChunk(allFiles.slice(c, c + CHUNK), c / CHUNK);
        }
        ovTitle.textContent = "Importación completada";
        const parts = [];
        if (imported) parts.push(`${imported} importadas`);
        if (duplicates) parts.push(`${duplicates} duplicadas`);
        if (errors) parts.push(`${errors} con error`);
        ovSub.textContent = parts.join(" · ") || "Sin cambios";
        ovCurrent.textContent = "";
        ovFill.style.width = "100%";
        if (ovSpinner) ovSpinner.style.display = "none";
        if (ovActions) ovActions.style.display = "";
      } catch (err) {
        ovTitle.textContent = "Error en la importación";
        ovSub.textContent = (err && err.message) || "fallo de red";
        if (ovSpinner) ovSpinner.style.display = "none";
        if (ovActions) ovActions.style.display = "";
        logEvent("err", String(err && err.message || err));
      } finally {
        if (submitBtn) {
          submitBtn.disabled = false;
          submitBtn.textContent = "Importar";
        }
      }
    });
  }

  if (ovClose) {
    ovClose.addEventListener("click", () => {
      ovHide();
      // Recarga ligera para refrescar contadores en otras pestañas / home
      // si hubo importaciones efectivas (lo más común tras este flow).
      window.location.reload();
    });
  }
})();

(function () {
  // ---- helpers streaming compartidos ----
  async function consumeNDJSON(url, onEvent) {
    const resp = await fetch(url, { method: "POST" });
    if (!resp.ok || !resp.body) throw new Error(resp.statusText || "fallo en el servidor");
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) !== -1) {
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (!line) continue;
        try { onEvent(JSON.parse(line)); } catch (_) {}
      }
    }
  }

  // ---- Reprocesar ----
  const reprocessBtn = document.getElementById("reprocess-btn");
  const reprocessOut = document.getElementById("reprocess-status");
  if (reprocessBtn) {
    reprocessBtn.addEventListener("click", async () => {
      if (!confirm(
        "Esto reprocesará todas las rutas (releerá GPX, recalculará stats, " +
        "nombres, zonas y dificultad). Notas y etiquetas se conservan, pero " +
        "los renames manuales se perderán. ¿Continuar?"
      )) return;
      reprocessBtn.disabled = true;
      reprocessOut.textContent = "iniciando…";
      let scanned = 0, updated = 0, skipped = 0, failed = 0;
      try {
        await consumeNDJSON("/api/reprocesar", (ev) => {
          if (ev.type === "start") {
            scanned = ev.total;
            reprocessOut.textContent = `0 / ${scanned}`;
          } else if (ev.type === "progress") {
            const label = ev.status === "geocoding" ? `${ev.name} · buscando zona…` : ev.name;
            reprocessOut.textContent = `${ev.i} / ${scanned} · ${label}`;
          } else if (ev.type === "done") {
            updated = ev.updated; skipped = ev.skipped; failed = ev.failed;
            reprocessOut.textContent =
              `✓ ${updated} actualizadas · ${skipped} omitidas · ${failed} fallidas · de ${ev.scanned} totales`;
            if (updated > 0) setTimeout(() => { window.location.reload(); }, 1500);
          }
        });
      } catch (e) {
        reprocessOut.textContent = "error: " + (e.message || e);
      } finally {
        reprocessBtn.disabled = false;
      }
    });
  }

  // ---- Completar zonas ----
  const backfillBtn = document.getElementById("backfill-btn");
  const backfillOut = document.getElementById("backfill-status");
  if (backfillBtn) {
    backfillBtn.addEventListener("click", async () => {
      backfillBtn.disabled = true;
      backfillOut.textContent = "consultando OpenStreetMap…";
      let scanned = 0;
      try {
        await consumeNDJSON("/api/backfill-regions", (ev) => {
          if (ev.type === "start") {
            scanned = ev.total;
            if (scanned === 0) {
              backfillOut.textContent = "todas las rutas ya tienen zona ✓";
              return;
            }
            backfillOut.textContent = `0 / ${scanned}`;
          } else if (ev.type === "progress") {
            backfillOut.textContent = `${ev.i} / ${scanned} · ${ev.name}`;
          } else if (ev.type === "done") {
            backfillOut.textContent =
              `${ev.updated} actualizadas · ${ev.failed} sin resultado · de ${ev.scanned} pendientes`;
          }
        });
      } catch (e) {
        backfillOut.textContent = "error: " + (e.message || e);
      } finally {
        backfillBtn.disabled = false;
      }
    });
  }
})();

(function () {
  // Flujo: detectar (dry_run) → revisar grupos → confirmar borrado.
  const detectBtn = document.getElementById("dups-detect-btn");
  const cleanBtn = document.getElementById("dups-clean-btn");
  const status = document.getElementById("dups-status");
  const results = document.getElementById("dups-results");
  if (!detectBtn) return;

  let lastReport = null;

  function escapeHtml(s) {
    return String(s)
      .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
  }

  function safeRouteId(id) {
    const n = parseInt(id, 10);
    return Number.isFinite(n) && n > 0 ? n : null;
  }

  function renderReport(rep) {
    if (!rep || !rep.groups || !rep.groups.length) {
      results.innerHTML = `<div style="font-family:'IBM Plex Mono',monospace; font-size:13px; color:var(--text-dim); padding:14px 0;">
        No se han detectado duplicados. Se han revisado ${rep ? rep.scanned : 0} rutas${rep && rep.missing ? ` (${rep.missing} sin GPX en disco — ignoradas)` : ""}.
      </div>`;
      cleanBtn.disabled = true;
      return;
    }
    const total = rep.duplicate_count;
    const head = `
      <div style="font-family:'IBM Plex Mono',monospace; font-size:12px; color:var(--text-dim); margin:0 0 10px;">
        ${rep.groups.length} grupo(s) · ${total} ruta(s) duplicada(s) a eliminar
        ${rep.missing ? ` · ${rep.missing} ruta(s) sin GPX ignorada(s)` : ""}
      </div>`;
    const groups = rep.groups.map(g => {
      const keepId = safeRouteId(g.keep.id);
      if (!keepId) return "";
      const removeItems = g.remove.map(r => {
        const removeId = safeRouteId(r.id);
        if (!removeId) return "";
        return `
            <li>
              <span class="dup-tag dup-tag-remove">eliminar</span>
              <a href="/rutas/${removeId}">${escapeHtml(r.name)}</a>
              <span style="font-family:'IBM Plex Mono',monospace; font-size:11px; opacity:.6;">#${removeId}</span>
            </li>`;
      }).join("");
      return `
      <div class="dup-group">
        <div class="dup-group-hash">hash · ${escapeHtml(g.hash)}</div>
        <div class="dup-group-keep">
          <span class="dup-tag dup-tag-keep">se conserva</span>
          <a href="/rutas/${keepId}">${escapeHtml(g.keep.name)}</a>
          <span style="font-family:'IBM Plex Mono',monospace; font-size:11px; opacity:.6;">#${keepId}</span>
        </div>
        <ul class="dup-group-remove">${removeItems}</ul>
      </div>`;
    }).join("");
    results.innerHTML = head + groups;
    cleanBtn.disabled = false;
  }

  detectBtn.addEventListener("click", async () => {
    detectBtn.disabled = true;
    cleanBtn.disabled = true;
    status.textContent = "calculando hashes…";
    results.innerHTML = "";
    try {
      const resp = await fetch("/api/limpiar-duplicados?dry_run=true", { method: "POST" });
      if (!resp.ok) throw new Error(resp.statusText);
      lastReport = await resp.json();
      status.textContent = "";
      renderReport(lastReport);
    } catch (e) {
      status.textContent = "error: " + (e.message || e);
    } finally {
      detectBtn.disabled = false;
    }
  });

  cleanBtn.addEventListener("click", async () => {
    if (!lastReport || !lastReport.groups || !lastReport.groups.length) return;
    const n = lastReport.duplicate_count;
    if (!confirm(`Se eliminarán ${n} ruta(s) duplicada(s). Esta acción no se puede deshacer (notas y etiquetas se perderán). ¿Continuar?`)) return;
    cleanBtn.disabled = true;
    detectBtn.disabled = true;
    status.textContent = "eliminando…";
    try {
      const resp = await fetch("/api/limpiar-duplicados?dry_run=false", { method: "POST" });
      if (!resp.ok) throw new Error(resp.statusText);
      const data = await resp.json();
      status.textContent = `✓ ${data.deleted} ruta(s) eliminada(s)`;
      lastReport = null;
      results.innerHTML = "";
      // Refresca la página: la pestaña queda intacta gracias a la URL,
      // pero los contadores de home/análisis se invalidan en el backend.
      setTimeout(() => { window.location.reload(); }, 1200);
    } catch (e) {
      status.textContent = "error: " + (e.message || e);
      cleanBtn.disabled = false;
      detectBtn.disabled = false;
    }
  });
})();
