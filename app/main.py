"""Aplicacion FastAPI."""
from __future__ import annotations

import json
import secrets
import time as _time
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional

from fastapi import (
    Cookie,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session
from starlette.datastructures import MutableHeaders

from app import analisis as analisis_module
from app import format as format_module
from app import maintenance as maintenance_module
from app import stats as stats_module
from app import weather as weather_module
from app.auth import (
    SESSION_COOKIE,
    clear_session_cookie,
    create_session,
    csrf_token_for,
    get_current_user,
    hash_password,
    require_admin,
    require_csrf,
    require_writer,
    resolve_session,
    revoke_session,
    set_session_cookie,
    verify_dummy,
    verify_password,
)
from app.db import commit as _db_commit, get_session, init_db
from app.detail import build_detail
from app.importer import ImportResult, process_gpx, user_gpx_dir
from app.models import Route, TrackPoint, User
from app.queries import user_route_get_or_404

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = PROJECT_ROOT / "templates"
STATIC_DIR = PROJECT_ROOT / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicializa la BD al arrancar la aplicación."""
    init_db()
    yield


app = FastAPI(title="mendi.log", lifespan=lifespan)


# ===== Middleware: cabeceras de caché =====

class CacheHeadersMiddleware:
    """Cabeceras de caché — middleware ASGI puro (no `BaseHTTPMiddleware`).

    Política:
    - /static/*  →  cache fuerte e *immutable*. Fuentes y libs (Leaflet,
      HTMX) no cambian durante el uso normal y queremos que el navegador
      las sirva desde caché sin revalidar.
    - /api/*     →  `no-store`. Los endpoints devuelven datos vivos.
    - resto      →  `no-cache`. HTML revalidable pero cacheable.

    Por qué ASGI puro y no `BaseHTTPMiddleware`:
    `BaseHTTPMiddleware` *consume* el cuerpo de la respuesta antes de
    pasarlo al siguiente nivel, lo que **rompe el streaming** (NDJSON de
    /importar/stream, SSE, etc.).
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                if path.startswith("/static/"):
                    headers["cache-control"] = "public, max-age=31536000, immutable"
                elif path.startswith("/api/"):
                    if "cache-control" not in headers:
                        headers["cache-control"] = "no-store"
                else:
                    if "cache-control" not in headers:
                        headers["cache-control"] = "no-cache"
            await send(message)

        await self.app(scope, receive, send_wrapper)


# ===== Middleware: guardián de autenticación =====

# Rutas exentas: login/logout, ficheros estáticos, sondeo de Chrome DevTools.
PUBLIC_PATHS = {
    "/login",
    "/logout",
    "/.well-known/appspecific/com.chrome.devtools.json",
}
PUBLIC_PREFIXES = ("/static/",)


class AuthGuardMiddleware:
    """Middleware ASGI puro: deja pasar PUBLIC_PATHS; el resto cae al dep `get_current_user`.

    No bloquea por sí solo (la verificación real ocurre en la dependencia
    `get_current_user` que es la que tiene acceso a la sesión SQLAlchemy).
    Este middleware existe para *no* aplicar `get_current_user` a las rutas
    públicas y como punto de extensión: aquí podríamos meter cabeceras de
    seguridad globales (HSTS, X-Frame-Options) sin tocar handlers.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("x-content-type-options", "nosniff")
                headers.setdefault("x-frame-options", "DENY")
                headers.setdefault("referrer-policy", "same-origin")
            await send(message)

        await self.app(scope, receive, send_wrapper)


app.add_middleware(AuthGuardMiddleware)
app.add_middleware(CacheHeadersMiddleware)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["fmt_km"] = format_module.fmt_km
templates.env.filters["fmt_km_short"] = format_module.fmt_km_short
templates.env.filters["fmt_int"] = format_module.fmt_int
templates.env.filters["fmt_duration"] = format_module.fmt_duration
templates.env.filters["fmt_date_es"] = format_module.fmt_date_es
templates.env.filters["difficulty_label_es"] = format_module.difficulty_label_es
templates.env.filters["fmt_world_pct"] = format_module.fmt_world_pct
templates.env.filters["fmt_hero_km"] = format_module.fmt_hero_km


# ===== Manejador 401: convierte a 303 cuando NO es HTMX =====

@app.exception_handler(HTTPException)
async def _http_exception_with_login_redirect(request: Request, exc: HTTPException):
    """Si get_current_user emite 401 con Location, redirige al login."""
    if exc.status_code == 401 and exc.headers and "Location" in exc.headers:
        if request.headers.get("hx-request", "").lower() != "true":
            return RedirectResponse(exc.headers["Location"], status_code=303)
    return await http_exception_handler(request, exc)


def _commit(db: Session, *, invalidate: bool = False) -> None:
    """Alias hacia `app.db.commit` — mantiene la firma histórica."""
    _db_commit(db, invalidate_analisis=invalidate)


# ===== Flash messages server-side (con propietario) =====
# Antes serializábamos `msg` y `error` en la querystring del redirect — XSS
# reflejado si el nombre del fichero traía HTML. Ahora guardamos en memoria
# con TTL corto y emitimos solo un id opaco; además ahora cada flash tiene
# `user_id` para que el id de otro no se pueda recoger desde mi sesión.

_FLASH_STORE: dict[str, tuple[float, int, str | None, str | None]] = {}
_FLASH_TTL_S = 60


def _flash_set(user_id: int, msg: str | None, error: str | None) -> str:
    now = _time.time()
    expired = [k for k, (ts, _, _, _) in _FLASH_STORE.items() if now - ts > _FLASH_TTL_S]
    for k in expired:
        _FLASH_STORE.pop(k, None)
    fid = secrets.token_urlsafe(12)
    _FLASH_STORE[fid] = (now, int(user_id), msg or None, error or None)
    return fid


def _flash_pop(fid: str | None, user_id: int) -> tuple[str | None, str | None]:
    if not fid:
        return None, None
    entry = _FLASH_STORE.pop(fid, None)
    if not entry:
        return None, None
    ts, owner, msg, err = entry
    if owner != user_id:
        # No es tuyo: ignora silenciosamente (no leak de existencia).
        return None, None
    if _time.time() - ts > _FLASH_TTL_S:
        return None, None
    return msg, err


# ===== Helper de contexto para templates =====

def _ctx(request: Request, current_user: Optional[User], **extra) -> dict:
    """Contexto base para render — incluye request, user y csrf_token."""
    token = request.cookies.get(SESSION_COOKIE) or ""
    csrf = csrf_token_for(token) if token else ""
    ctx = {
        "request": request,
        "current_user": current_user,
        "csrf_token": csrf,
    }
    ctx.update(extra)
    return ctx


# ===== Sondeo silencioso de Chrome DevTools =====

@app.get("/.well-known/appspecific/com.chrome.devtools.json")
def _chrome_devtools_probe() -> Response:
    return Response(status_code=204)


# ===== Login / Logout =====
# Rate limiter en memoria: 5 intentos / 15 min por IP en POST /login.
# Defensa en profundidad: Argon2id ya hace el ataque caro (~50 ms/intento),
# pero el limiter evita inundación del log y reduce ruido en CPU.

_LOGIN_ATTEMPTS: dict[str, list[float]] = {}
_LOGIN_MAX = 5
_LOGIN_WINDOW_S = 15 * 60


def _login_rate_check(request: Request) -> None:
    """Lanza 429 si la IP ha hecho más de _LOGIN_MAX intentos en _LOGIN_WINDOW_S."""
    ip = (request.client.host if request.client else "anon")
    now = _time.time()
    history = _LOGIN_ATTEMPTS.get(ip, [])
    # Limpieza: descarta intentos fuera de ventana.
    history = [t for t in history if now - t < _LOGIN_WINDOW_S]
    if len(history) >= _LOGIN_MAX:
        retry_in = int(_LOGIN_WINDOW_S - (now - history[0]))
        raise HTTPException(
            status_code=429,
            detail=f"Demasiados intentos. Espera {retry_in}s.",
            headers={"Retry-After": str(max(retry_in, 1))},
        )
    history.append(now)
    _LOGIN_ATTEMPTS[ip] = history


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, db: Session = Depends(get_session)):
    """Formulario de login. Si ya hay sesión válida, redirige al home."""
    token = request.cookies.get(SESSION_COOKIE)
    sess = resolve_session(db, token)
    if sess:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        request,
        "login.html",
        {"current_user": None, "csrf_token": "", "error": None},
    )


@app.post("/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    remember: Optional[str] = Form(None),
    db: Session = Depends(get_session),
):
    """Verifica credenciales y abre sesión. Rate-limited a 5/15min por IP."""
    _login_rate_check(request)

    email_norm = (email or "").strip().lower()
    pwd = password or ""

    user = db.query(User).filter(User.email == email_norm).first()
    if not user or not user.is_active:
        # Igualamos timing verificando contra un hash dummy.
        verify_dummy(pwd)
        return templates.TemplateResponse(
            request,
            "login.html",
            {"current_user": None, "csrf_token": "",
             "error": "Credenciales incorrectas."},
            status_code=401,
        )

    if not verify_password(user.password_hash, pwd):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"current_user": None, "csrf_token": "",
             "error": "Credenciales incorrectas."},
            status_code=401,
        )

    token = create_session(db, user.id, remember=bool(remember), request=request)
    user.last_login_at = datetime.now(UTC)
    db.commit()
    response = RedirectResponse("/", status_code=303)
    set_session_cookie(response, token, remember=bool(remember))
    return response


@app.post("/logout")
def logout(
    request: Request,
    db: Session = Depends(get_session),
    mendi_session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
):
    """Cierra la sesión actual y borra la cookie. No requiere CSRF (idempotente)."""
    if mendi_session:
        revoke_session(db, mendi_session)
    response = RedirectResponse("/login", status_code=303)
    clear_session_cookie(response)
    return response


# ===== Rutas de páginas =====

@app.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Vista Resumen: hero, KPIs, mapa, gráfica mensual y tabla de rutas recientes."""
    data = stats_module.build_resumen(db, current_user.id)
    return templates.TemplateResponse(
        request,
        "resumen.html",
        _ctx(request, current_user, data=data, active_nav="resumen"),
    )


@app.get("/rutas", response_class=HTMLResponse)
def rutas(
    request: Request,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Vista Rutas: metadatos, facets y pool de rotación."""
    data = stats_module.build_rutas(db, current_user.id)
    return templates.TemplateResponse(
        request,
        "rutas.html",
        _ctx(request, current_user, data=data, active_nav="rutas", now=datetime.now()),
    )


@app.get("/api/rutas")
def api_rutas(
    q: str = "",
    difficulty: str = "",
    region: str = "all",
    country: str = "all",
    distance: str = "all",
    gain: str = "all",
    date_from: str = "",
    date_to: str = "",
    sort: str = "date-desc",
    offset: int = 0,
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Listado paginado, filtrado y ordenado a nivel SQL — acotado al usuario."""
    diffs = [d.strip() for d in difficulty.split(",") if d.strip()] if difficulty else []
    rq = stats_module.RutaQuery(
        q=q,
        difficulty=diffs,
        region=region,
        country=country,
        distance=distance,
        gain=gain,
        date_from=date_from,
        date_to=date_to,
        sort=sort,
        offset=max(0, offset),
        limit=limit,
    )
    result = stats_module.query_rutas(db, current_user.id, rq)
    return JSONResponse({
        "items": [asdict(i) for i in result.items],
        "total": result.total,
        "matched": result.matched,
        "hasMore": result.has_more,
    })


@app.get("/analisis", response_class=HTMLResponse)
def analisis(
    request: Request,
    range: str = Query("all"),
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Vista Análisis: agrega sesiones por rango temporal e hidrata el template."""
    data = analisis_module.get_analisis_cached(
        db, current_user.id, range_key=range, from_date=from_, to_date=to,
    )
    payload = analisis_module.to_json_payload(data)
    return templates.TemplateResponse(
        request,
        "analisis.html",
        _ctx(
            request, current_user,
            active_nav="analisis",
            data=data,
            payload_json=json.dumps(payload, ensure_ascii=False),
        ),
    )


@app.get("/api/analisis")
def api_analisis(
    range: str = Query("all"),
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Endpoint JSON de análisis: mismo payload que la vista HTML más extras para JS."""
    data = analisis_module.get_analisis_cached(
        db, current_user.id, range_key=range, from_date=from_, to_date=to,
    )
    payload = analisis_module.to_json_payload(data)
    payload["heroStats"] = [
        {"label": s.label, "value": s.value, "unit": s.unit, "detail": s.detail}
        for s in data.hero_stats
    ]
    payload["zones"] = [
        {"name": z.name, "count": z.count, "pct": z.pct, "css": z.css_class}
        for z in data.zones
    ]
    payload["topRoutes"] = [
        {"rank": t.rank, "name": t.name, "origin": t.origin,
         "reps": t.reps, "totalKm": t.total_km, "barPct": t.bar_pct}
        for t in data.top_routes
    ]
    payload["dormidas"] = [
        {"name": d.name, "origin": d.origin, "lastDate": d.last_date_str,
         "daysSince": d.days_since, "km": d.distance_km}
        for d in data.dormidas
    ]
    payload["records"] = [
        {"label": r.label, "value": r.value, "unit": r.unit,
         "detail": r.detail, "css": r.css_class}
        for r in data.records
    ]
    payload["calendar"] = [
        {"year": cy.year,
         "months": [{"label": m.label, "weeks": m.weeks} for m in cy.months]}
        for cy in data.calendar
    ]
    payload["chips"] = [
        {"key": c.key, "label": c.label, "selected": c.selected}
        for c in data.range_chips
    ]
    return JSONResponse(payload)


@app.get("/importar", response_class=HTMLResponse)
def importar_form(
    request: Request,
    fid: Optional[str] = None,
    current_user: User = Depends(require_writer),
):
    """Formulario de importación drag & drop."""
    msg, error = _flash_pop(fid, current_user.id)
    return templates.TemplateResponse(
        request,
        "importar.html",
        _ctx(request, current_user, active_nav="importar", msg=msg, error=error),
    )


@app.post("/importar")
async def importar_post(
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_session),
    current_user: User = Depends(require_writer),
    _csrf: None = Depends(require_csrf),
):
    """Importación batch: procesa todos los GPX y redirige con flash al terminar."""
    imported = 0
    duplicates = 0
    errors: list[str] = []

    for upload in files:
        if not upload.filename:
            continue
        result = process_gpx(db, current_user.id, upload.filename, await upload.read())
        if result.status == "ok":
            imported += 1
        elif result.status == "dup":
            duplicates += 1
            errors.append(
                f"{result.filename}: ya existe (importado como «{result.existing_name}»)"
            )
        else:
            errors.append(f"{result.filename}: {result.error_msg}")

    _commit(db, invalidate=bool(imported))

    msg_parts: list[str] = []
    if imported:
        msg_parts.append(f"Importadas {imported} ruta(s)")
    if duplicates:
        msg_parts.append(f"{duplicates} duplicada(s) ignorada(s)")
    msg = " · ".join(msg_parts)

    if imported and not errors:
        fid = _flash_set(current_user.id, msg, None)
        return RedirectResponse(f"/importar?fid={fid}", status_code=303)
    elif imported and errors:
        fid = _flash_set(current_user.id, msg, "; ".join(errors))
        return RedirectResponse(f"/importar?fid={fid}", status_code=303)
    else:
        fid = _flash_set(current_user.id, None, "; ".join(errors) or "No se importó ninguna ruta")
        return RedirectResponse(f"/importar?fid={fid}", status_code=303)


@app.post("/importar/stream")
async def importar_stream(
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_session),
    current_user: User = Depends(require_writer),
    _csrf: None = Depends(require_csrf),
):
    """Importación con feedback por archivo vía NDJSON.

    Cada línea es un evento JSON que el cliente lee con `ReadableStream` para
    actualizar la UI en tiempo real. Commit por archivo: si uno falla a mitad,
    los anteriores quedan guardados.
    """
    payloads: list[tuple[str, bytes]] = []
    for upload in files:
        if not upload.filename:
            continue
        payloads.append((upload.filename, await upload.read()))

    user_id = current_user.id

    async def generate():
        import asyncio

        def emit(obj: dict) -> str:
            return json.dumps(obj, ensure_ascii=False) + "\n"

        total = len(payloads)
        yield emit({"type": "start", "total": total})
        await asyncio.sleep(0)

        imported = 0
        duplicates = 0
        errors = 0

        for i, (filename, content) in enumerate(payloads, start=1):
            yield emit({"type": "file", "i": i, "name": filename, "phase": "leyendo"})
            await asyncio.sleep(0)

            result: ImportResult = process_gpx(db, user_id, filename, content)

            if result.status == "skipped":
                errors += 1
                yield emit({"type": "error", "i": i, "name": filename,
                            "message": result.error_msg})
                continue

            if result.status == "dup":
                duplicates += 1
                yield emit({"type": "dup", "i": i, "name": filename,
                            "existing": result.existing_name})
                continue

            if result.status == "error":
                errors += 1
                yield emit({"type": "error", "i": i, "name": filename,
                            "message": result.error_msg})
                continue

            _commit(db, invalidate=True)
            imported += 1
            yield emit({"type": "ok", "i": i, "name": filename,
                        "route_id": result.route_id, "name_clean": result.name_clean})
            await asyncio.sleep(0)

        yield emit({
            "type": "done",
            "imported": imported,
            "duplicates": duplicates,
            "errors": errors,
        })

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-store"},
    )


@app.post("/api/limpiar-duplicados")
def api_limpiar_duplicados(
    dry_run: bool = Query(False),
    db: Session = Depends(get_session),
    current_user: User = Depends(require_writer),
    _csrf: None = Depends(require_csrf),
):
    """Limpia duplicados de GPX dentro del usuario actual."""
    result = maintenance_module.limpiar_duplicados(db, current_user.id, dry_run=dry_run)
    if result["deleted"]:
        _commit(db, invalidate=True)
    return result


# ===== Detalle de ruta =====

@app.get("/rutas/{route_id}", response_class=HTMLResponse)
def detalle(
    route_id: int,
    request: Request,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Vista Detalle: mapa, perfil de elevación, datos técnicos y clima histórico."""
    data = build_detail(db, current_user.id, route_id)
    if not data:
        raise HTTPException(status_code=404, detail="Ruta no encontrada")
    return templates.TemplateResponse(
        request,
        "detail.html",
        _ctx(request, current_user, data=data, active_nav="rutas"),
    )


@app.get("/api/rutas/{route_id}/track")
def api_track(
    route_id: int,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Devuelve track, bounding box, hitos y muestras del perfil de elevación."""
    data = build_detail(db, current_user.id, route_id)
    if not data:
        raise HTTPException(status_code=404, detail="Ruta no encontrada")
    return {
        "track": data.map.track.points,
        "bbox": data.map.track.bbox,
        "milestones": [
            {
                "kind": m.kind,
                "label": m.label,
                "name": m.name,
                "elev_m": m.elev_m,
                "km": m.km,
                "time": m.time_str,
            }
            for m in data.map.milestones
        ],
        "elev_samples": data.elev.samples,
    }


@app.get("/api/rutas/{route_id}/clima")
def api_clima(
    route_id: int,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Clima histórico en la cima y ventana temporal de la ruta — acotado al usuario."""
    r = user_route_get_or_404(db, current_user.id, route_id)
    data = build_detail(db, current_user.id, route_id)
    if not data:
        raise HTTPException(status_code=404, detail="Ruta no encontrada")
    payload = weather_module.get_weather(
        db,
        lat=data.summit_lat,
        lon=data.summit_lon,
        d=r.started_at.date(),
    )
    if not payload:
        return JSONResponse({"available": False}, status_code=200)
    points: list[TrackPoint] = (
        db.query(TrackPoint)
        .filter(TrackPoint.route_id == r.id)
        .order_by(TrackPoint.seq.asc())
        .all()
    )
    hike_start = next((p.time for p in points if p.time), None)
    hike_end = next((p.time for p in reversed(points) if p.time), None)
    summit_pt = max(points, key=lambda p: (p.elevation_m or -9999)) if points else None
    summit_time = summit_pt.time if summit_pt else None
    return {
        "available": True,
        "data": payload,
        "hike_start": hike_start.isoformat() if hike_start else None,
        "hike_end": hike_end.isoformat() if hike_end else None,
        "summit_time": summit_time.isoformat() if summit_time else None,
        "summit_lat": data.summit_lat,
        "summit_lon": data.summit_lon,
    }


# ===== Payloads PATCH validados con Pydantic =====

class UpdateNotesPayload(BaseModel):
    notes: Optional[str] = None
    tags: Optional[list[str]] = None

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        if v is None:
            return None
        clean: list[str] = []
        for t in v:
            t = str(t).strip()
            if t and len(clean) < 10:
                clean.append(t[:22])
        return clean


class UpdateNamePayload(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("El nombre no puede estar vacío")
        return v


@app.patch("/api/rutas/{route_id}/notas")
def api_actualizar_notas(
    route_id: int,
    payload: UpdateNotesPayload,
    db: Session = Depends(get_session),
    current_user: User = Depends(require_writer),
    _csrf: None = Depends(require_csrf),
):
    """Actualiza notas y etiquetas de una ruta — acotado al usuario."""
    r = user_route_get_or_404(db, current_user.id, route_id)
    raw = payload.model_dump(exclude_unset=True)
    if "notes" in raw:
        r.notes = (payload.notes or "").strip() or None
    if "tags" in raw:
        clean = payload.tags or []
        r.tags = json.dumps(clean, ensure_ascii=False) if clean else None
    r.updated_at = datetime.now(UTC)
    _commit(db)
    return {
        "ok": True,
        "notes": r.notes or "",
        "tags": json.loads(r.tags) if r.tags else [],
        "updated": r.updated_at.isoformat() if r.updated_at else None,
    }


@app.patch("/api/rutas/{route_id}/nombre")
def api_actualizar_nombre(
    route_id: int,
    payload: UpdateNamePayload,
    db: Session = Depends(get_session),
    current_user: User = Depends(require_writer),
    _csrf: None = Depends(require_csrf),
):
    """Renombra una ruta e invalida el caché de análisis — acotado al usuario."""
    r = user_route_get_or_404(db, current_user.id, route_id)
    r.name = payload.name
    r.updated_at = datetime.now(UTC)
    _commit(db, invalidate=True)
    return {"ok": True, "name": r.name}


@app.post("/rutas/{route_id}/renombrar")
def renombrar(
    route_id: int,
    new_name: str = Form(...),
    db: Session = Depends(get_session),
    current_user: User = Depends(require_writer),
    _csrf: None = Depends(require_csrf),
):
    """Renombra una ruta vía form POST — acotado al usuario."""
    r = user_route_get_or_404(db, current_user.id, route_id)
    r.name = new_name.strip() or r.name
    r.updated_at = datetime.now(UTC)
    _commit(db, invalidate=True)
    return {"ok": True, "name": r.name}


@app.post("/rutas/{route_id}/eliminar")
def eliminar(
    route_id: int,
    db: Session = Depends(get_session),
    current_user: User = Depends(require_writer),
    _csrf: None = Depends(require_csrf),
):
    """Elimina una ruta y su GPX en disco vía form POST — acotado al usuario."""
    r = user_route_get_or_404(db, current_user.id, route_id)
    if r.gpx_filename:
        path = user_gpx_dir(r.user_id) / r.gpx_filename
        if path.exists():
            try:
                path.unlink()
            except OSError:
                pass
    db.delete(r)
    _commit(db, invalidate=True)
    return RedirectResponse("/rutas", status_code=303)


@app.post("/api/reprocesar")
def api_reprocesar(
    db: Session = Depends(get_session),
    current_user: User = Depends(require_writer),
    _csrf: None = Depends(require_csrf),
):
    """Reprocesa todas las rutas del usuario desde los GPX en disco. Streaming NDJSON."""
    user_id = current_user.id

    def generate():
        updated = 0
        for line in maintenance_module.reprocesar_stream(db, user_id):
            try:
                ev = json.loads(line)
                if ev.get("type") == "done":
                    updated = ev.get("updated", 0)
            except (json.JSONDecodeError, AttributeError):
                pass
            yield line
        _commit(db, invalidate=bool(updated))

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-store"},
    )


@app.post("/api/backfill-regions")
def api_backfill_regions(
    db: Session = Depends(get_session),
    current_user: User = Depends(require_writer),
    _csrf: None = Depends(require_csrf),
):
    """Completa zonas faltantes con geocodificación — acotado al usuario. Streaming NDJSON."""
    user_id = current_user.id

    def generate():
        updated = 0
        for line in maintenance_module.backfill_stream(db, user_id):
            try:
                ev = json.loads(line)
                if ev.get("type") == "done":
                    updated = ev.get("updated", 0)
            except (json.JSONDecodeError, AttributeError):
                pass
            yield line
        _commit(db, invalidate=bool(updated))

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-store"},
    )


@app.delete("/api/rutas/{route_id}")
def api_eliminar(
    route_id: int,
    db: Session = Depends(get_session),
    current_user: User = Depends(require_writer),
    _csrf: None = Depends(require_csrf),
):
    """Elimina una ruta y su GPX en disco vía DELETE — acotado al usuario."""
    r = user_route_get_or_404(db, current_user.id, route_id)
    if r.gpx_filename:
        path = user_gpx_dir(r.user_id) / r.gpx_filename
        if path.exists():
            try:
                path.unlink()
            except OSError:
                pass
    db.delete(r)
    _commit(db, invalidate=True)
    return {"ok": True}


# ===== Gestión de usuarios (solo admin) =====
# Endpoints CRUD para que un admin gestione el resto de usuarios.
# Todos los handlers comparten los mismos invariantes duros:
#  1. Siempre debe quedar ≥ 1 admin activo.
#  2. Un admin no puede auto-eliminarse.
#  3. El último admin no puede degradarse ni desactivarse.

_ALLOWED_ROLES = ("admin", "user", "viewer")


def _count_active_admins(db: Session) -> int:
    return (
        db.query(User)
        .filter(User.role == "admin", User.is_active == 1)
        .count()
    )


def _serialize_user(u: User) -> dict:
    return {
        "id": u.id,
        "email": u.email,
        "display_name": u.display_name,
        "role": u.role,
        "is_active": bool(u.is_active),
        "created_at": u.created_at.isoformat() if u.created_at else None,
        "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
    }


class CreateUserPayload(BaseModel):
    email: str = Field(..., min_length=5, max_length=200)
    password: str = Field(..., min_length=12, max_length=200)
    role: str = Field(default="user")
    display_name: Optional[str] = Field(default=None, max_length=120)

    @field_validator("email")
    @classmethod
    def _norm_email(cls, v: str) -> str:
        v = v.strip().lower()
        if "@" not in v:
            raise ValueError("Email inválido")
        return v

    @field_validator("role")
    @classmethod
    def _check_role(cls, v: str) -> str:
        if v not in _ALLOWED_ROLES:
            raise ValueError(f"Rol inválido: {v}")
        return v


class UpdateRolePayload(BaseModel):
    role: str

    @field_validator("role")
    @classmethod
    def _check_role(cls, v: str) -> str:
        if v not in _ALLOWED_ROLES:
            raise ValueError(f"Rol inválido: {v}")
        return v


class UpdatePasswordPayload(BaseModel):
    password: str = Field(..., min_length=12, max_length=200)


class UpdateDisplayNamePayload(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=120)

    @field_validator("display_name")
    @classmethod
    def _clean_name(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        return v or None


class UpdateActivePayload(BaseModel):
    is_active: bool


@app.get("/admin/usuarios", response_class=HTMLResponse)
def admin_usuarios(
    request: Request,
    db: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
):
    """Página HTML de gestión de usuarios — solo admin."""
    users = db.query(User).order_by(User.created_at.asc()).all()
    return templates.TemplateResponse(
        request,
        "admin_usuarios.html",
        _ctx(
            request, current_user,
            active_nav="admin-usuarios",
            users=[_serialize_user(u) for u in users],
        ),
    )


@app.get("/api/usuarios")
def api_usuarios_list(
    db: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
):
    users = db.query(User).order_by(User.created_at.asc()).all()
    return {"items": [_serialize_user(u) for u in users]}


@app.post("/api/usuarios")
def api_usuarios_create(
    payload: CreateUserPayload,
    db: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
    _csrf: None = Depends(require_csrf),
):
    existing = db.query(User).filter(User.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=409, detail="Ya existe un usuario con ese email.")
    user = User(
        email=payload.email,
        password_hash=hash_password(payload.password),
        display_name=(payload.display_name or "").strip() or None,
        is_active=1,
        role=payload.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return _serialize_user(user)


@app.patch("/api/usuarios/{user_id}/rol")
def api_usuarios_set_role(
    user_id: int,
    payload: UpdateRolePayload,
    db: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
    _csrf: None = Depends(require_csrf),
):
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    # Invariante: si el target es el último admin activo y queremos degradarlo, 409.
    if (
        target.role == "admin"
        and target.is_active == 1
        and payload.role != "admin"
        and _count_active_admins(db) <= 1
    ):
        raise HTTPException(
            status_code=409,
            detail="No puedes degradar al último admin activo. Crea o promueve otro antes.",
        )
    target.role = payload.role
    db.commit()
    return _serialize_user(target)


@app.patch("/api/usuarios/{user_id}/password")
def api_usuarios_reset_password(
    user_id: int,
    payload: UpdatePasswordPayload,
    db: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
    _csrf: None = Depends(require_csrf),
):
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    target.password_hash = hash_password(payload.password)
    db.commit()
    return {"ok": True}


@app.patch("/api/usuarios/{user_id}/nombre")
def api_usuarios_set_display_name(
    user_id: int,
    payload: UpdateDisplayNamePayload,
    db: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
    _csrf: None = Depends(require_csrf),
):
    """Actualiza el display_name de un usuario — solo admin."""
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    target.display_name = payload.display_name
    db.commit()
    return _serialize_user(target)


@app.patch("/api/usuarios/{user_id}/activo")
def api_usuarios_set_active(
    user_id: int,
    payload: UpdateActivePayload,
    db: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
    _csrf: None = Depends(require_csrf),
):
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    # Invariante: no desactivar al último admin activo.
    if (
        target.role == "admin"
        and target.is_active == 1
        and not payload.is_active
        and _count_active_admins(db) <= 1
    ):
        raise HTTPException(
            status_code=409,
            detail="No puedes desactivar al último admin activo.",
        )
    target.is_active = 1 if payload.is_active else 0
    db.commit()
    return _serialize_user(target)


@app.delete("/api/usuarios/{user_id}")
def api_usuarios_delete(
    user_id: int,
    db: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
    _csrf: None = Depends(require_csrf),
):
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    # Invariante: nadie se auto-elimina.
    if target.id == current_user.id:
        raise HTTPException(
            status_code=409,
            detail="No puedes eliminarte a ti mismo.",
        )
    # Invariante: no borrar al último admin activo.
    if (
        target.role == "admin"
        and target.is_active == 1
        and _count_active_admins(db) <= 1
    ):
        raise HTTPException(
            status_code=409,
            detail="No puedes eliminar al último admin activo.",
        )

    # Borrado en cascada: SQLAlchemy ya elimina routes + track_points por
    # ON DELETE CASCADE. Nos queda limpiar los GPX en disco del usuario.
    import shutil
    gpx_dir = user_gpx_dir(target.id)
    target_id = target.id
    db.delete(target)
    db.commit()
    if gpx_dir.exists():
        try:
            shutil.rmtree(gpx_dir)
        except OSError:
            pass
    # Invalida cache de análisis del usuario eliminado (sus rutas dejaron de existir).
    from app.analisis import invalidate_analisis_cache
    invalidate_analisis_cache()
    return {"ok": True, "deleted_id": target_id}
