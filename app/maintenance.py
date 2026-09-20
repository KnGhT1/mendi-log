"""Operaciones de mantenimiento: reprocesar rutas y limpiar duplicados.

Todas las operaciones están acotadas al usuario indicado.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Generator

from sqlalchemy.orm import Session

from app.clustering import backfill_clusters_for_user
from app.difficulty import DifficultyInputs, difficulty_level, difficulty_score
from app.geocoder import reverse_geocode
from app.gpx_parser import parse_gpx
from app.importer import user_gpx_dir
from app.models import Route, Summit, TrackPoint
from app.name_cleaner import clean_name, detect_region
from app.summits import fetch_summits
from app.text_utils import canonical_geo
from app.tz import resolve_timezone


def _emit(obj: dict) -> str:
    """Serializa un dict como línea NDJSON (JSON + salto de línea)."""
    return json.dumps(obj, ensure_ascii=False) + "\n"


def _route_gpx_path(route: Route) -> Path | None:
    """Devuelve la ruta absoluta al GPX de `route` o None si no aplica."""
    if not route.gpx_filename:
        return None
    return user_gpx_dir(route.user_id) / route.gpx_filename


def reprocesar_stream(db: Session, user_id: int) -> Generator[str, None, None]:
    """Genera eventos NDJSON mientras reprocesa todas las rutas del usuario con GPX en disco.

    Eventos emitidos:
        {"type": "start", "total": N}
        {"type": "progress", "i": k, "name": "...", "status": "ok"|"skip"|"error", "detail": "..."}
        {"type": "done", "updated": N, "skipped": N, "failed": N, "scanned": N}

    No hace commit — lo delega al llamador.
    """
    routes: list[Route] = (
        db.query(Route)
        .filter(Route.user_id == user_id)
        .order_by(Route.id.asc())
        .all()
    )
    yield _emit({"type": "start", "total": len(routes)})

    updated = skipped = failed = dup_content = 0
    errors: list[str] = []

    for i, r in enumerate(routes, start=1):
        gpx_path = _route_gpx_path(r)
        if not gpx_path or not gpx_path.exists():
            skipped += 1
            yield _emit({"type": "progress", "i": i, "name": r.name,
                         "status": "skip", "detail": "sin GPX en disco"})
            continue

        try:
            with gpx_path.open("rb") as f:
                content = f.read()
            stats = parse_gpx(content)
        except (OSError, ValueError, AttributeError, KeyError) as exc:
            # OSError: lectura del fichero falla. ValueError: GPX inválido.
            # AttributeError/KeyError: estructura XML inesperada en gpxpy.
            failed += 1
            errors.append(f"{r.id}: {exc}")
            yield _emit({"type": "progress", "i": i, "name": r.name,
                         "status": "error", "detail": str(exc)})
            continue

        r.name_original = stats.name_original
        r.started_at = stats.started_at
        r.start_lat = stats.start_lat
        r.start_lon = stats.start_lon
        r.distance_km = stats.distance_km
        r.elevation_gain_m = stats.elevation_gain_m
        r.elevation_loss_m = stats.elevation_loss_m
        r.moving_time_s = stats.moving_time_s
        r.total_time_s = stats.total_time_s
        r.max_altitude_m = stats.max_altitude_m
        r.min_altitude_m = stats.min_altitude_m
        r.elev_line_path = stats.elev_line_path
        r.elev_area_path = stats.elev_area_path

        new_hash = hashlib.sha256(content).hexdigest()
        if r.gpx_sha256 != new_hash:
            # Clash solo dentro del mismo usuario (la UNIQUE constraint es
            # (user_id, gpx_sha256)).
            clash = (
                db.query(Route.id)
                .filter(
                    Route.user_id == user_id,
                    Route.gpx_sha256 == new_hash,
                    Route.id != r.id,
                )
                .first()
            )
            if clash:
                dup_content += 1
                errors.append(f"{r.id}: hash duplicado con ruta {clash[0]}")
            else:
                r.gpx_sha256 = new_hash

        r.name = clean_name(stats.name_original)
        country, region, sub_region = detect_region(stats.name_original, stats.description)
        if not region or not country or not sub_region:
            yield _emit({"type": "progress", "i": i, "name": r.name,
                         "status": "geocoding", "detail": "consultando zona"})
            geo_country, geo_region, geo_sub = reverse_geocode(r.start_lat, r.start_lon, db=db)
            country = country or geo_country
            region = region or geo_region
            sub_region = sub_region or geo_sub
        r.country = canonical_geo(country)
        r.region = canonical_geo(region)
        r.sub_region = canonical_geo(sub_region)
        # Fase 2: el trailhead puede haber cambiado al re-parsear.
        r.timezone = resolve_timezone(r.start_lat, r.start_lon)

        score = difficulty_score(DifficultyInputs(
            distance_km=stats.distance_km,
            elevation_gain_m=stats.elevation_gain_m,
            moving_time_s=stats.moving_time_s,
        ))
        r.difficulty_score = score
        r.difficulty_level = difficulty_level(score)

        db.query(TrackPoint).filter(TrackPoint.route_id == r.id).delete(
            synchronize_session=False
        )
        for pt in stats.track:
            db.add(TrackPoint(
                route_id=r.id, seq=pt.seq, lat=pt.lat, lon=pt.lon,
                elevation_m=pt.elevation, time=pt.time,
            ))

        db.query(Summit).filter(Summit.route_id == r.id).delete(
            synchronize_session=False
        )
        for s in fetch_summits(stats):
            db.add(Summit(
                route_id=r.id, seq=s.seq, lat=s.lat, lon=s.lon,
                elevation_m=s.elevation_m, name=s.name, source=s.source,
            ))

        r.updated_at = datetime.now(UTC)
        updated += 1
        yield _emit({"type": "progress", "i": i, "name": r.name,
                     "status": "ok", "detail": ""})

    # Tras reescribir trailheads y altitudes los clusters pueden haber cambiado.
    # H11: solo los del usuario que reprocesa, nunca los de otros usuarios.
    if updated:
        backfill_clusters_for_user(db, user_id)

    yield _emit({"type": "done", "scanned": len(routes),
                 "updated": updated, "skipped": skipped,
                 "failed": failed, "dup_content": dup_content,
                 "errors": errors[:10]})


def backfill_stream(db: Session, user_id: int) -> Generator[str, None, None]:
    """Genera eventos NDJSON mientras completa zonas faltantes con geocodificación.

    Eventos emitidos:
        {"type": "start", "total": N}
        {"type": "progress", "i": k, "name": "...", "status": "ok"|"skip"|"error"}
        {"type": "done", "updated": N, "failed": N, "scanned": N}

    No hace commit — lo delega al llamador.
    """
    from sqlalchemy import or_
    pending = (
        db.query(Route)
        .filter(Route.user_id == user_id)
        .filter(or_(
            Route.region.is_(None), Route.region == "",
            Route.country.is_(None), Route.country == "",
        ))
        .filter(Route.start_lat.isnot(None), Route.start_lon.isnot(None))
        .all()
    )
    yield _emit({"type": "start", "total": len(pending)})

    updated = failed = 0
    for i, r in enumerate(pending, start=1):
        yield _emit({"type": "progress", "i": i, "name": r.name,
                     "status": "geocoding", "detail": "consultando zona"})
        country, region, sub_region = reverse_geocode(r.start_lat, r.start_lon, db=db)
        if not region and not sub_region and not country:
            failed += 1
            yield _emit({"type": "progress", "i": i, "name": r.name,
                         "status": "error", "detail": "sin resultado"})
            continue
        if country and not r.country:
            r.country = canonical_geo(country)
        if region and not r.region:
            r.region = canonical_geo(region)
        if sub_region and not r.sub_region:
            r.sub_region = canonical_geo(sub_region)
        r.updated_at = datetime.now(UTC)
        updated += 1
        yield _emit({"type": "progress", "i": i, "name": r.name,
                     "status": "ok", "detail": region or country or ""})

    yield _emit({"type": "done", "scanned": len(pending),
                 "updated": updated, "failed": failed})


def limpiar_duplicados(db: Session, user_id: int, *, dry_run: bool = False) -> dict:
    """Detecta y elimina rutas del usuario con contenido GPX idéntico.

    Conserva la ruta con id más bajo de cada grupo. No hace commit — lo
    delega al llamador. Las rutas de OTROS usuarios nunca se tocan, aunque
    compartan SHA-256.
    """
    routes: list[Route] = (
        db.query(Route)
        .filter(Route.user_id == user_id)
        .order_by(Route.id.asc())
        .all()
    )

    by_hash: dict[str, list[Route]] = {}
    missing = 0

    for r in routes:
        path = _route_gpx_path(r)
        if not path or not path.exists():
            missing += 1
            continue
        try:
            with path.open("rb") as f:
                h = hashlib.sha256(f.read()).hexdigest()
        except OSError:
            missing += 1
            continue
        by_hash.setdefault(h, []).append(r)

    groups = []
    to_delete: list[Route] = []
    for h, members in by_hash.items():
        if len(members) < 2:
            continue
        keeper = members[0]
        rest = members[1:]
        groups.append({
            "hash": h[:12],
            "keep": {"id": keeper.id, "name": keeper.name},
            "remove": [{"id": x.id, "name": x.name} for x in rest],
        })
        to_delete.extend(rest)

    deleted = 0
    if not dry_run and to_delete:
        for r in to_delete:
            path = _route_gpx_path(r)
            if path and path.exists():
                try:
                    path.unlink()
                except OSError:
                    pass
            db.delete(r)
            deleted += 1

    return {
        "ok": True,
        "scanned": len(routes),
        "missing": missing,
        "groups": groups,
        "duplicate_count": len(to_delete),
        "deleted": deleted,
        "dry_run": dry_run,
    }
