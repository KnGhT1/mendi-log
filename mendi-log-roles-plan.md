# Plan de roles y permisos — mendi.log

> Documento de diseño. Recoge la arquitectura RBAC, la matriz de permisos por
> página/endpoint, los invariantes duros y el roadmap de implementación en
> fases. Sirve como contrato entre el código y las decisiones de producto.

---

## 1. Roles definidos

Tres roles, jerárquicos en capacidad pero discretos en código (no se usa
herencia, cada dependencia comprueba un conjunto explícito):

| Rol      | Resumen                                                                 |
| -------- | ----------------------------------------------------------------------- |
| `admin`  | Todo lo del `user` + gestión completa de usuarios (alta, baja, rol, password reset). |
| `user`   | Acceso completo a sus propias rutas: importar, renombrar, anotar, borrar, mantenimiento. **Rol por defecto.** |
| `viewer` | Solo lectura. Puede consultar Resumen, Rutas, Análisis y Detalle, pero no importar, modificar ni borrar nada. |

**Bootstrap**: el primer usuario creado en una BD vacía se promueve
automáticamente a `admin`. Resto, por defecto `user` salvo que se pase
`--role` al script.

**Invariante global**: siempre debe existir al menos un `admin` activo.
Ninguna operación puede dejar el sistema sin admins.

---

## 2. Matriz de acceso por página

Las páginas que un rol **no puede ver** devuelven `403` (no `404`: queremos que
sepa que existe pero no tiene acceso). El sidebar oculta las entradas que el
rol no puede usar para que la UI sea coherente con los permisos efectivos.

| Página            | URL                  | viewer | user | admin |
| ----------------- | -------------------- | :----: | :--: | :---: |
| Resumen           | `/`                  |   ✅   |  ✅  |   ✅  |
| Rutas             | `/rutas`             |   ✅   |  ✅  |   ✅  |
| Detalle de ruta   | `/rutas/{id}`        |   ✅   |  ✅  |   ✅  |
| Análisis          | `/analisis`          |   ✅   |  ✅  |   ✅  |
| Importar          | `/importar`          |   ❌   |  ✅  |   ✅  |
| Admin de usuarios | `/admin/usuarios`    |   ❌   |  ❌  |   ✅  |

---

## 3. Detalle por página y elemento

### 3.1 Sidebar (todas las páginas)

| Entrada      | viewer | user | admin |
| ------------ | :----: | :--: | :---: |
| Resumen      |   ✅   |  ✅  |   ✅  |
| Rutas        |   ✅   |  ✅  |   ✅  |
| Análisis     |   ✅   |  ✅  |   ✅  |
| Importar     |   ❌   |  ✅  |   ✅  |
| Usuarios     |   ❌   |  ❌  |   ✅  |

### 3.2 Topbar

- **Chip de rol**: se muestra a la derecha del breadcrumb, badge pequeño con el
  rol del usuario. Solo informativo. Permite verificar de un vistazo qué
  permisos tienes.
- **Tema, logout**: igual para los tres roles.

### 3.3 Resumen (`/`)

Todos los roles ven exactamente el mismo contenido: hero KPIs, mapa, gráfica
mensual, tabla de rutas recientes. La tabla enlaza al detalle (todos pueden
abrirlo). **Ningún elemento es editable aquí**, por tanto no hay diferencias
de permisos: los datos son del propio usuario en sesión.

### 3.4 Rutas (`/rutas`)

| Elemento                              | viewer | user | admin |
| ------------------------------------- | :----: | :--: | :---: |
| Listado, búsqueda, filtros, orden     |   ✅   |  ✅  |   ✅  |
| Click → Detalle                       |   ✅   |  ✅  |   ✅  |
| Botón "Importar GPX" (atajo a /importar) |  ❌   |  ✅  |   ✅  |

### 3.5 Detalle de ruta (`/rutas/{id}`)

| Elemento                              | viewer | user | admin |
| ------------------------------------- | :----: | :--: | :---: |
| Mapa + perfil + datos técnicos        |   ✅   |  ✅  |   ✅  |
| Clima histórico                       |   ✅   |  ✅  |   ✅  |
| Editar nombre (inline)                |   ❌   |  ✅  |   ✅  |
| Editar notas y tags                   |   ❌   |  ✅  |   ✅  |
| Botón "Eliminar ruta"                 |   ❌   |  ✅  |   ✅  |

Para `viewer`, los controles editables (input de nombre, textarea de notas,
chips de tags, botón eliminar) se renderizan en modo solo-lectura o no se
renderizan en absoluto.

### 3.6 Análisis (`/analisis`)

Todos los roles ven el mismo contenido completo. No hay acciones
de mutación en esta página, por lo que no hay diferencias.

### 3.7 Importar (`/importar`)

- `viewer`: no puede entrar. Acceso → `403`. La entrada del sidebar está
  oculta.
- `user` y `admin`: acceso completo. Drag&drop, streaming NDJSON, pestaña
  "Mantenimiento" (limpiar duplicados, reprocesar, backfill regiones).

### 3.8 Admin de usuarios (`/admin/usuarios`)

Nueva página, solo accesible a `admin`. Layout:

| Elemento                                        | Descripción |
| ----------------------------------------------- | ----------- |
| Tabla de usuarios                               | email · nombre · rol · estado · creado · último login |
| Acción "Nuevo usuario"                          | Modal con email, password, rol, display_name opcional |
| Acción inline "Cambiar rol"                     | Select por fila — sujeto a invariantes |
| Acción "Restablecer contraseña"                 | Modal pidiendo nueva password (2 veces) |
| Acción "Eliminar usuario"                       | Modal de confirmación; cascada a rutas + GPX en disco |
| Acción "Activar/Desactivar"                     | Toggle `is_active` |

---

## 4. Matriz de endpoints

### 4.1 Endpoints existentes a clasificar

| Endpoint                                        | Método  | Permiso requerido |
| ----------------------------------------------- | ------- | ----------------- |
| `/login`, `/logout`                             | varios  | público           |
| `/`                                             | GET     | autenticado (cualquier rol) |
| `/rutas`                                        | GET     | autenticado |
| `/api/rutas`                                    | GET     | autenticado |
| `/rutas/{id}`                                   | GET     | autenticado |
| `/api/rutas/{id}/track`                         | GET     | autenticado |
| `/api/rutas/{id}/clima`                         | GET     | autenticado |
| `/analisis`                                     | GET     | autenticado |
| `/api/analisis`                                 | GET     | autenticado |
| `/importar`                                     | GET     | **`require_writer`** (user, admin) |
| `/importar`                                     | POST    | **`require_writer`** |
| `/importar/stream`                              | POST    | **`require_writer`** |
| `/api/limpiar-duplicados`                       | POST    | **`require_writer`** |
| `/api/rutas/{id}/notas`                         | PATCH   | **`require_writer`** |
| `/api/rutas/{id}/nombre`                        | PATCH   | **`require_writer`** |
| `/rutas/{id}/renombrar`                         | POST    | **`require_writer`** |
| `/rutas/{id}/eliminar`                          | POST    | **`require_writer`** |
| `/api/rutas/{id}`                               | DELETE  | **`require_writer`** |
| `/api/reprocesar`                               | POST    | **`require_writer`** |
| `/api/backfill-regions`                         | POST    | **`require_writer`** |

### 4.2 Endpoints nuevos (todos `require_admin`)

| Endpoint                            | Método | Descripción |
| ----------------------------------- | ------ | ----------- |
| `/admin/usuarios`                   | GET    | HTML — tabla de usuarios |
| `/api/usuarios`                     | GET    | JSON — listar usuarios |
| `/api/usuarios`                     | POST   | Crear usuario (email, password, rol, display_name) |
| `/api/usuarios/{id}/rol`            | PATCH  | Cambiar rol (validar invariante "≥1 admin") |
| `/api/usuarios/{id}/password`       | PATCH  | Restablecer contraseña |
| `/api/usuarios/{id}/activo`         | PATCH  | Activar/desactivar |
| `/api/usuarios/{id}`                | DELETE | Borrar usuario (cascada rutas + GPX dir) |

---

## 5. Dependencies de auth (sketch)

En `app/auth.py` añadimos:

```python
def require_role(*allowed: str):
    """Factory: devuelve una dependency que exige rol en `allowed`."""
    def _dep(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed:
            raise HTTPException(
                status_code=403,
                detail=f"Acceso denegado: rol '{current_user.role}' no autorizado.",
            )
        return current_user
    return _dep

# Atajos semánticos
require_admin  = require_role("admin")
require_writer = require_role("admin", "user")   # cualquiera que pueda escribir
```

Uso en endpoints:

```python
@app.post("/api/limpiar-duplicados")
def api_limpiar_duplicados(
    dry_run: bool = Query(False),
    db: Session = Depends(get_session),
    current_user: User = Depends(require_writer),   # ← antes get_current_user
    _csrf: None = Depends(require_csrf),
):
    ...
```

---

## 6. Invariantes duros

Se comprueban en el código de los endpoints `PATCH /api/usuarios/{id}/rol` y
`DELETE /api/usuarios/{id}`:

1. **Siempre ≥ 1 admin activo**: si el cambio bajara el conteo de admins
   activos a 0, devolver `409 Conflict` con mensaje explicativo.
2. **Sin auto-borrado**: un admin no puede borrarse a sí mismo (`409`).
3. **Sin auto-degradación del último admin**: un admin no puede degradarse
   a `user`/`viewer` si es el único admin activo.
4. **Sin auto-desactivación del último admin**: idem para `is_active=0`.

El error 409 lo emitimos con `detail` en español para que la UI lo muestre
tal cual.

---

## 7. Migración suave

Como `User` no tiene la columna `role` todavía, hacemos migración con
`ALTER TABLE` perezoso en `app/db.py:init_db()`:

```python
def _ensure_user_columns() -> None:
    """Añade columnas nuevas a `users` sin perder datos existentes."""
    with engine.connect() as conn:
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(users)")}
        if "role" not in cols:
            conn.exec_driver_sql(
                "ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'"
            )
            conn.commit()
```

Tras crear la columna, si hay usuarios existentes pero ninguno es admin,
promovemos al usuario más antiguo:

```python
def _bootstrap_first_admin() -> None:
    with SessionLocal() as db:
        any_admin = db.query(User).filter(User.role == "admin", User.is_active == 1).first()
        if any_admin:
            return
        first_user = db.query(User).order_by(User.created_at.asc()).first()
        if first_user:
            first_user.role = "admin"
            db.commit()
```

Ambos se invocan al final de `init_db()`.

---

## 8. Bootstrap en `scripts/create_user.py`

- Añadir flag `--role admin|user|viewer` (default `user`).
- Si la BD está vacía al crear el primer usuario, forzar `role="admin"`
  independientemente del flag (con aviso por consola).
- Validar que `--role` está en el set permitido.

---

## 9. Cambios en templates

### 9.1 `templates/base.html`

- Insertar conditional en el sidebar:
  - `{% if current_user.role in ("user", "admin") %}` envolviendo el link a `/importar`.
  - `{% if current_user.role == "admin" %}` envolviendo nuevo link a `/admin/usuarios`.
- Añadir chip de rol en el topbar, antes del botón de tema:
  ```html
  <span class="role-chip role-{{ current_user.role }}" title="Rol">{{ current_user.role }}</span>
  ```

### 9.2 `templates/detail.html`

- Envolver controles editables (nombre, notas, tags, botón eliminar) en
  `{% if current_user.role in ("user", "admin") %}`.
- Para `viewer`, renderizar los valores como texto plano sin inputs.

### 9.3 `templates/rutas.html`

- Botón "Importar GPX" del topbar: visible solo si `current_user.role != "viewer"`.

### 9.4 `templates/admin_usuarios.html` (nuevo)

- Tabla server-side con todos los usuarios.
- JS con `fetch` + CSRF para crear/editar/borrar.
- Estética consistente con el resto del proyecto (variables CSS existentes,
  IBM Plex Sans/Mono, Fraunces para headlines).

---

## 10. Fases de implementación

### Fase 1 — Modelo + migración + bootstrap script

1. Añadir `role` a `User` en `app/models.py`.
2. Implementar `_ensure_user_columns()` y `_bootstrap_first_admin()` en `app/db.py`.
3. Actualizar `scripts/create_user.py` con `--role` y auto-promote del primer usuario.

### Fase 2 — Dependencies + aplicar a endpoints existentes

1. Añadir `require_role`, `require_admin`, `require_writer` en `app/auth.py`.
2. Reemplazar `get_current_user` por `require_writer` en los 10 endpoints
   de escritura listados en §4.1.
3. Insertar chip de rol y sidebar condicional en `base.html`.
4. Condicional de controles editables en `detail.html` y `rutas.html`.

### Fase 3 — Endpoints de gestión de usuarios

1. Crear los 7 endpoints de §4.2, todos con `require_admin` + `require_csrf`.
2. Implementar los 4 invariantes duros de §6.
3. Cascada en delete: SQLAlchemy ya borra `routes` y `track_points` por FK
   `ON DELETE CASCADE`; añadir borrado manual de `data/gpx/{user_id}/` recursivo.

### Fase 4 — UI `/admin/usuarios`

1. Crear `templates/admin_usuarios.html`.
2. JS específico (`static/js/admin_usuarios.js`) con dispatcher MENDI_PAGES.
3. CSS añadidos a `static/css/styles.css` (chip de rol, tabla admin, modales).
4. Manejar errores 403/409 con mensajes claros.

### Fase 5 — Cascada GPX + README

1. Verificar borrado del directorio `data/gpx/{user_id}/` en `DELETE /api/usuarios/{id}`.
2. Actualizar README con sección "Roles y permisos" + cómo gestionar usuarios.

### Fase 6 — Empaquetado

1. Build `output/mendi-log-users.zip` excluyendo `__pycache__`, `data/`, `.git/`.
2. Verificar con `Glob output/**/*` que el zip existe.

---

## 11. Riesgos y consideraciones

- **Compatibilidad ascendente**: la migración suave hace que instancias
  existentes obtengan `role='user'` para todos los usuarios actuales y luego
  el primero se promueve a admin. **No hay riesgo de pérdida de datos.**
- **Sesiones existentes**: no se invalidan al añadir el rol. El siguiente
  request leerá el rol del User actualizado.
- **CSRF**: ya está en su sitio. Las dependencies nuevas se apilan después.
- **Frontend solo es sugerencia**: las protecciones reales están en backend
  (los 403 son emitidos por las dependencies, no por el template).

---

## 12. Definition of Done

- [ ] Los tres roles existen en BD y se respetan en backend (403 en endpoints).
- [ ] Sidebar muestra entradas según rol; `viewer` no ve "Importar" ni "Usuarios".
- [ ] Detalle de ruta esconde controles editables para `viewer`.
- [ ] `/admin/usuarios` lista, crea, modifica rol/password, activa/desactiva y borra usuarios.
- [ ] Borrar un usuario elimina sus rutas, track points y GPX en disco.
- [ ] Los 4 invariantes duros del §6 funcionan (probados manualmente).
- [ ] `scripts/create_user.py --role admin` funciona; primer usuario se auto-promueve.
- [ ] README actualizado con sección de roles.
- [ ] `output/mendi-log-users.zip` empaquetado y verificado.
