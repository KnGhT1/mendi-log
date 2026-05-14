// ============================================================
// mendi.log · admin · gestión de usuarios
// ------------------------------------------------------------
// Módulo de la página /admin/usuarios. Se registra en
// window.MENDI_PAGES["admin-usuarios"] (kebab-case porque el
// dispatcher accede con bracket-syntax) y monta:
//   - Modal "nuevo usuario"   → POST   /api/usuarios
//   - Modal "reset password"  → PATCH  /api/usuarios/{id}/password
//   - Modal "eliminar"        → DELETE /api/usuarios/{id}
//   - Inline role select      → PATCH  /api/usuarios/{id}/rol
//   - Inline state toggle     → PATCH  /api/usuarios/{id}/activo
//
// El token CSRF lo inyecta automáticamente el wrapper de fetch en
// base.html — aquí NO hace falta añadir cabecera manual.
// ============================================================
(function () {
  "use strict";

  window.MENDI_PAGES = window.MENDI_PAGES || {};
  window.MENDI_TEARDOWN = window.MENDI_TEARDOWN || [];

  /**
   * Escapa caracteres HTML para interpolación segura en innerHTML.
   * @param {*} s
   * @returns {string}
   */
  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
  }

  function init() {
    const cfg = window.MENDI_ADMIN_USERS || {};
    const SELF_ID = cfg.selfId;

    const flashEl = document.getElementById("admin-flash");
    let flashTimer = null;

    /**
     * Muestra un mensaje efímero en la franja superior. Tipos:
     *   - "ok"    → tono accent
     *   - "err"   → tono danger
     *   - "info"  → tono muted
     * @param {string} msg
     * @param {"ok"|"err"|"info"} kind
     */
    function flash(msg, kind = "ok") {
      if (!flashEl) return;
      flashEl.textContent = msg;
      flashEl.className = `flash flash-${kind}`;
      flashEl.style.display = "block";
      if (flashTimer) clearTimeout(flashTimer);
      flashTimer = setTimeout(() => {
        flashEl.style.display = "none";
      }, 3200);
    }
    window.MENDI_TEARDOWN.push(() => {
      if (flashTimer) clearTimeout(flashTimer);
    });

    /**
     * Lee el detalle de error de una respuesta HTTP no-ok. Tolera
     * payloads no-JSON.
     */
    async function readError(res) {
      try {
        const json = await res.json();
        if (json && typeof json.detail === "string") return json.detail;
        if (json && Array.isArray(json.detail) && json.detail[0]?.msg) {
          return json.detail[0].msg;
        }
      } catch (_) {}
      return `Error ${res.status}`;
    }

    // ============ MODAL: NUEVO USUARIO ============
    const newModal = document.getElementById("new-user-modal");
    const btnNew = document.getElementById("btn-new-user");
    const newCancel = document.getElementById("new-cancel");
    const newConfirm = document.getElementById("new-confirm");
    const newEmail = document.getElementById("new-email");
    const newName = document.getElementById("new-name");
    const newRole = document.getElementById("new-role");
    const newPwd = document.getElementById("new-password");
    const newPwd2 = document.getElementById("new-password2");
    const newError = document.getElementById("new-error");

    function openNew() {
      newEmail.value = "";
      newName.value = "";
      newRole.value = "user";
      newPwd.value = "";
      newPwd2.value = "";
      newError.style.display = "none";
      newError.textContent = "";
      newModal.style.display = "grid";
      setTimeout(() => newEmail.focus(), 30);
    }
    function closeNew() {
      newModal.style.display = "none";
    }

    if (btnNew) btnNew.addEventListener("click", openNew);
    if (newCancel) newCancel.addEventListener("click", closeNew);
    if (newModal) newModal.addEventListener("click", (e) => {
      if (e.target === newModal) closeNew();
    });

    async function submitNew() {
      const email = (newEmail.value || "").trim().toLowerCase();
      const name = (newName.value || "").trim();
      const role = newRole.value;
      const pwd = newPwd.value || "";
      const pwd2 = newPwd2.value || "";

      newError.style.display = "none";
      if (!email || !email.includes("@")) {
        newError.textContent = "Email inválido.";
        newError.style.display = "block";
        return;
      }
      if (pwd.length < 12) {
        newError.textContent = "La contraseña debe tener al menos 12 caracteres.";
        newError.style.display = "block";
        return;
      }
      if (pwd !== pwd2) {
        newError.textContent = "Las contraseñas no coinciden.";
        newError.style.display = "block";
        return;
      }

      newConfirm.disabled = true;
      try {
        const res = await fetch("/api/usuarios", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            email, password: pwd, role,
            display_name: name || null,
          }),
        });
        if (!res.ok) {
          const msg = await readError(res);
          newError.textContent = msg;
          newError.style.display = "block";
          return;
        }
        closeNew();
        flash(`Usuario ${email} creado.`, "ok");
        setTimeout(() => window.location.reload(), 600);
      } catch (err) {
        newError.textContent = "Error de red al crear usuario.";
        newError.style.display = "block";
      } finally {
        newConfirm.disabled = false;
      }
    }
    if (newConfirm) newConfirm.addEventListener("click", submitNew);

    // ============ MODAL: RESET PASSWORD ============
    const pwModal = document.getElementById("pw-modal");
    const pwCancel = document.getElementById("pw-cancel");
    const pwConfirm = document.getElementById("pw-confirm");
    const pwEmail = document.getElementById("pw-email");
    const pwPwd = document.getElementById("pw-password");
    const pwPwd2 = document.getElementById("pw-password2");
    const pwError = document.getElementById("pw-error");
    let pwTargetId = null;

    function openPw(userId, email) {
      pwTargetId = userId;
      pwEmail.textContent = email;
      pwPwd.value = "";
      pwPwd2.value = "";
      pwError.style.display = "none";
      pwModal.style.display = "grid";
      setTimeout(() => pwPwd.focus(), 30);
    }
    function closePw() {
      pwModal.style.display = "none";
      pwTargetId = null;
    }

    if (pwCancel) pwCancel.addEventListener("click", closePw);
    if (pwModal) pwModal.addEventListener("click", (e) => {
      if (e.target === pwModal) closePw();
    });

    async function submitPw() {
      const pwd = pwPwd.value || "";
      const pwd2 = pwPwd2.value || "";
      pwError.style.display = "none";

      if (pwd.length < 12) {
        pwError.textContent = "La contraseña debe tener al menos 12 caracteres.";
        pwError.style.display = "block";
        return;
      }
      if (pwd !== pwd2) {
        pwError.textContent = "Las contraseñas no coinciden.";
        pwError.style.display = "block";
        return;
      }
      if (!pwTargetId) return;

      pwConfirm.disabled = true;
      try {
        const res = await fetch(`/api/usuarios/${pwTargetId}/password`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ password: pwd }),
        });
        if (!res.ok) {
          pwError.textContent = await readError(res);
          pwError.style.display = "block";
          return;
        }
        closePw();
        flash("Contraseña actualizada.", "ok");
      } catch (err) {
        pwError.textContent = "Error de red.";
        pwError.style.display = "block";
      } finally {
        pwConfirm.disabled = false;
      }
    }
    if (pwConfirm) pwConfirm.addEventListener("click", submitPw);

    // ============ MODAL: ELIMINAR ============
    const delModal = document.getElementById("del-modal");
    const delCancel = document.getElementById("del-cancel");
    const delConfirm = document.getElementById("del-confirm");
    const delEmail = document.getElementById("del-email");
    let delTargetId = null;

    function openDel(userId, email) {
      delTargetId = userId;
      delEmail.textContent = email;
      delModal.style.display = "grid";
    }
    function closeDel() {
      delModal.style.display = "none";
      delTargetId = null;
    }

    if (delCancel) delCancel.addEventListener("click", closeDel);
    if (delModal) delModal.addEventListener("click", (e) => {
      if (e.target === delModal) closeDel();
    });

    async function submitDel() {
      if (!delTargetId) return;
      delConfirm.disabled = true;
      try {
        const res = await fetch(`/api/usuarios/${delTargetId}`, {
          method: "DELETE",
        });
        if (!res.ok) {
          flash(await readError(res), "err");
          closeDel();
          return;
        }
        // Quita la fila del DOM directamente para feedback inmediato.
        const row = document.querySelector(`.users-row[data-user-id="${delTargetId}"]`);
        if (row) row.remove();
        closeDel();
        flash("Usuario eliminado.", "ok");
        setTimeout(() => window.location.reload(), 600);
      } catch (err) {
        flash("Error de red al eliminar.", "err");
      } finally {
        delConfirm.disabled = false;
      }
    }
    if (delConfirm) delConfirm.addEventListener("click", submitDel);

    // ============ ESC GLOBAL PARA MODALES ============
    function onEsc(e) {
      if (e.key !== "Escape") return;
      if (newModal && newModal.style.display === "grid") closeNew();
      if (pwModal && pwModal.style.display === "grid") closePw();
      if (delModal && delModal.style.display === "grid") closeDel();
    }
    document.addEventListener("keydown", onEsc);
    window.MENDI_TEARDOWN.push(() => {
      document.removeEventListener("keydown", onEsc);
    });

    // ============ DELEGACIÓN DE LA TABLA ============
    const table = document.querySelector(".users-table");
    if (!table) return;

    /**
     * Manejador del cambio de rol inline. En 409 revierte el select al
     * valor anterior y muestra el motivo.
     */
    async function handleRoleChange(select) {
      const row = select.closest(".users-row");
      if (!row) return;
      const userId = row.getAttribute("data-user-id");
      const email = row.getAttribute("data-email");
      const newVal = select.value;
      const prevVal = select.dataset.prev || newVal;

      select.disabled = true;
      try {
        const res = await fetch(`/api/usuarios/${userId}/rol`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ role: newVal }),
        });
        if (!res.ok) {
          select.value = prevVal;
          flash(await readError(res), "err");
          return;
        }
        select.dataset.prev = newVal;
        flash(`Rol de ${email} → ${newVal}.`, "ok");
      } catch (err) {
        select.value = prevVal;
        flash("Error de red al cambiar rol.", "err");
      } finally {
        select.disabled = false;
      }
    }

    /**
     * Manejador del toggle activo/inactivo. En 409 (último admin
     * activo) revierte el estado visual y muestra el motivo.
     */
    async function handleActiveToggle(btn) {
      const row = btn.closest(".users-row");
      if (!row) return;
      const userId = row.getAttribute("data-user-id");
      const email = row.getAttribute("data-email");
      const wasActive = btn.dataset.active === "1";
      const want = !wasActive;

      btn.disabled = true;
      try {
        const res = await fetch(`/api/usuarios/${userId}/activo`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ is_active: want }),
        });
        if (!res.ok) {
          flash(await readError(res), "err");
          return;
        }
        btn.dataset.active = want ? "1" : "0";
        btn.classList.toggle("active", want);
        btn.classList.toggle("inactive", !want);
        btn.innerHTML = `<span class="dot"></span>${want ? "activo" : "inactivo"}`;
        flash(`${email} ${want ? "activado" : "desactivado"}.`, "ok");
      } catch (err) {
        flash("Error de red.", "err");
      } finally {
        btn.disabled = false;
      }
    }

    /**
     * Manejador de la edición inline del nombre. Guarda solo si el valor
     * cambió respecto al inicial. En error revierte y avisa.
     */
    async function handleNameSave(input) {
      const row = input.closest(".users-row");
      if (!row) return;
      const userId = row.getAttribute("data-user-id");
      const email = row.getAttribute("data-email");
      const prev = input.dataset.prev || "";
      const next = (input.value || "").trim();

      if (next === prev) {
        // Normaliza el valor del DOM al trim, sin viajar al servidor.
        input.value = prev;
        return;
      }

      input.classList.remove("is-error");
      input.classList.add("is-saving");
      input.disabled = true;
      try {
        const res = await fetch(`/api/usuarios/${userId}/nombre`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ display_name: next || null }),
        });
        if (!res.ok) {
          input.value = prev;
          input.classList.add("is-error");
          flash(await readError(res), "err");
          return;
        }
        input.dataset.prev = next;
        input.value = next;
        flash(`Nombre de ${email} actualizado.`, "ok");
      } catch (err) {
        input.value = prev;
        input.classList.add("is-error");
        flash("Error de red al guardar el nombre.", "err");
      } finally {
        input.classList.remove("is-saving");
        input.disabled = false;
      }
    }

    // Captura el valor inicial de cada select de rol para poder revertir.
    table.querySelectorAll("select[data-action=\"set-role\"]").forEach((sel) => {
      sel.dataset.prev = sel.value;
    });
    // Idem para los inputs de nombre — sin esto no podemos detectar cambios.
    table.querySelectorAll('input[data-action="set-name"]').forEach((inp) => {
      inp.dataset.prev = (inp.value || "").trim();
    });

    function onTableChange(e) {
      const target = e.target;
      if (target.matches('select[data-action="set-role"]')) {
        handleRoleChange(target);
      }
    }
    function onTableFocusOut(e) {
      const target = e.target;
      if (target.matches('input[data-action="set-name"]')) {
        handleNameSave(target);
      }
    }
    function onTableKeyDown(e) {
      const target = e.target;
      if (!target.matches('input[data-action="set-name"]')) return;
      if (e.key === "Enter") {
        e.preventDefault();
        target.blur(); // dispara focusout → handleNameSave
      } else if (e.key === "Escape") {
        e.preventDefault();
        target.value = target.dataset.prev || "";
        target.classList.remove("is-error");
        target.blur();
      }
    }
    function onTableClick(e) {
      const target = e.target.closest("[data-action]");
      if (!target) return;
      const action = target.getAttribute("data-action");
      const row = target.closest(".users-row");
      if (!row) return;
      const userId = row.getAttribute("data-user-id");
      const email = row.getAttribute("data-email");

      if (action === "toggle-active") {
        handleActiveToggle(target);
      } else if (action === "reset-password") {
        openPw(userId, email);
      } else if (action === "delete") {
        if (parseInt(userId, 10) === SELF_ID) {
          flash("No puedes eliminarte a ti mismo.", "err");
          return;
        }
        openDel(userId, email);
      }
    }

    table.addEventListener("change", onTableChange);
    table.addEventListener("click", onTableClick);
    table.addEventListener("focusout", onTableFocusOut);
    table.addEventListener("keydown", onTableKeyDown);
    window.MENDI_TEARDOWN.push(() => {
      table.removeEventListener("change", onTableChange);
      table.removeEventListener("click", onTableClick);
      table.removeEventListener("focusout", onTableFocusOut);
      table.removeEventListener("keydown", onTableKeyDown);
    });
  }

  window.MENDI_PAGES["admin-usuarios"] = { init };
})();
