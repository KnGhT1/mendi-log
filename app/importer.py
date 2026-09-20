"""Pipeline de importacion de un archivo GPX a la base de datos."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from gpxpy.gpx import GPXException

from app.clustering import assign_cluster_for_new
from app.db import DATA_DIR
from app.difficulty import DifficultyInputs, difficulty_level, difficulty_score
from app.geocoder import reverse_geocode
from app.gpx_parser import parse_gpx
from app.models import Route, Summit, TrackPoint
from app.name_cleaner import clean_name, detect_region
from app.summits import fetch_summits
from app.text_utils import canonical_geo
from app.tz import resolve_timezone

# Raíz común: los GPX viven en data/gpx/{user_id}/ para aislar por usuario.
GPX_ROOT = DATA_DIR / "gpx"


def user_gpx_dir(user_id: int) -> Path:
    """Devuelve (creándolo si hace falta) el directorio GPX del usuario."""
    d = GPX_ROOT / str(int(user_id))
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class ImportResult:
    status: str          # "ok" | "dup" | "error" | "skipped"
    filename: str
    route_id: int | None = None
    name_clean: str | None = None
    existing_name: str | None = None  # solo si status == "dup"
    error_msg: str | None = None      # solo si status == "error"


def process_gpx(db: Session, user_id: int, filename: str, content: bytes) -> ImportResult:
    """Procesa un GPX y lo persiste en BD (sin commit) bajo el usuario indicado.

    Devuelve un ImportResult con el estado. El commit lo hace el llamador
    para poder elegir entre commit-por-archivo (stream) o commit-al-final (batch).
    """
    if not filename.lower().endswith((".gpx", ".xml")):
        return ImportResult(status="skipped", filename=filename,
                            error_msg="no parece un GPX")

    gpx_hash = hashlib.sha256(content).hexdigest()
    existing = (
        db.query(Route)
        .filter(Route.user_id == user_id, Route.gpx_sha256 == gpx_hash)
        .first()
    )
    if existing:
        return ImportResult(status="dup", filename=filename,
                            existing_name=existing.name)

    try:
        stats = parse_gpx(content)
    except (ValueError, AttributeError, KeyError, GPXException) as exc:
        # ValueError: GPX vacío o malformado (lo lanza explícitamente parse_gpx).
        # AttributeError/KeyError: nodos del XML inesperados desde gpxpy.
        # GPXException: errores de sintaxis XML de gpxpy (GPXXMLSyntaxException)
        #   y otros fallos propios del parser; sin esto, un XML malformado
        #   provocaba un 500 en vez de un resultado "error" por archivo.
        return ImportResult(status="error", filename=filename, error_msg=str(exc))

    gpx_dir = user_gpx_dir(user_id)
    # Saneo: nos quedamos solo con el basename para evitar que un nombre tipo
    # "../../etc/passwd" o con separadores absolutos escape del directorio del
    # usuario. Después validamos con resolve()+relative_to() como cinturón y tirantes.
    safe_name = Path(filename).name or "ruta.gpx"
    target_path = (gpx_dir / f"{int(datetime.now(UTC).timestamp() * 1000)}_{safe_name}").resolve()
    gpx_dir_resolved = gpx_dir.resolve()
    try:
        target_path.relative_to(gpx_dir_resolved)
    except ValueError:
        return ImportResult(status="error", filename=filename,
                            error_msg="nombre de archivo inválido")
    with target_path.open("wb") as f:
        f.write(content)

    clean = clean_name(stats.name_original)
    country, region, sub_region = detect_region(stats.name_original, stats.description)
    if not region or not country or not sub_region:
        geo_country, geo_region, geo_sub = reverse_geocode(stats.start_lat, stats.start_lon)
        country = country or geo_country
        region = region or geo_region
        sub_region = sub_region or geo_sub

    score = difficulty_score(DifficultyInputs(
        distance_km=stats.distance_km,
        elevation_gain_m=stats.elevation_gain_m,
        moving_time_s=stats.moving_time_s,
    ))

    # Cinturón defensivo: aunque tanto `detect_region` como `_extract_region`
    # ya devuelven valores canónicos (NFKD + sin diacríticos + lower + strip),
    # canonicalizamos de nuevo aquí para proteger ante futuras nuevas fuentes
    # que pudieran no respetar el contrato. Es idempotente sobre valores ya
    # canónicos.
    country = canonical_geo(country)
    region = canonical_geo(region)
    sub_region = canonical_geo(sub_region)

    # Fase 2: zona IANA del trailhead (None si no se resuelve -> UTC).
    timezone = resolve_timezone(stats.start_lat, stats.start_lon)

    route = Route(
        user_id=user_id,
        name=clean,
        name_original=stats.name_original,
        country=country,
        region=region,
        sub_region=sub_region,
        timezone=timezone,
        started_at=stats.started_at,
        start_lat=stats.start_lat,
        start_lon=stats.start_lon,
        distance_km=stats.distance_km,
        elevation_gain_m=stats.elevation_gain_m,
        elevation_loss_m=stats.elevation_loss_m,
        moving_time_s=stats.moving_time_s,
        total_time_s=stats.total_time_s,
        max_altitude_m=stats.max_altitude_m,
        min_altitude_m=stats.min_altitude_m,
        difficulty_score=score,
        difficulty_level=difficulty_level(score),
        elev_line_path=stats.elev_line_path,
        elev_area_path=stats.elev_area_path,
        gpx_filename=target_path.name,
        gpx_sha256=gpx_hash,
    )
    db.add(route)
    db.flush()

    # Asigna route_cluster_id buscando coincidencia con rutas existentes
    # del MISMO usuario.
    assign_cluster_for_new(db, route)

    for pt in stats.track:
        db.add(TrackPoint(
            route_id=route.id, seq=pt.seq, lat=pt.lat, lon=pt.lon,
            elevation_m=pt.elevation, time=pt.time,
        ))

    for s in fetch_summits(stats):
        db.add(Summit(
            route_id=route.id, seq=s.seq, lat=s.lat, lon=s.lon,
            elevation_m=s.elevation_m, name=s.name, source=s.source,
        ))

    return ImportResult(status="ok", filename=filename,
                        route_id=route.id, name_clean=clean)
