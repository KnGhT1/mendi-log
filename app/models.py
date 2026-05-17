"""Modelos de datos."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.db import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String, nullable=False, unique=True, index=True)
    password_hash = Column(String, nullable=False)
    display_name = Column(String, nullable=True)
    # SQLite no tiene bool nativo: usamos 0/1.
    is_active = Column(Integer, nullable=False, default=1)
    # Rol RBAC: "admin" | "user" | "viewer". Default "user" para no romper
    # los inserts antiguos. El primer usuario creado en una BD vacía se
    # promueve a "admin" desde scripts/create_user.py.
    role = Column(String(16), nullable=False, default="user", index=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC))
    last_login_at = Column(DateTime, nullable=True)


class UserSession(Base):
    __tablename__ = "user_sessions"

    # Token opaco generado con secrets.token_urlsafe(32) — ~43 chars.
    id = Column(String(64), primary_key=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC))
    expires_at = Column(DateTime, nullable=False, index=True)
    ip = Column(String(64), nullable=True)
    user_agent = Column(String(256), nullable=True)
    revoked = Column(Integer, nullable=False, default=0)


class Route(Base):
    __tablename__ = "routes"
    __table_args__ = (
        UniqueConstraint("user_id", "gpx_sha256", name="uq_routes_user_sha256"),
        Index("ix_routes_user_started", "user_id", "started_at"),
    )

    id = Column(Integer, primary_key=True)

    # Propietario de la ruta. CASCADE para que borrar un usuario limpie todo.
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )

    name = Column(String, nullable=False, index=True)  # nombre limpio (editable)
    name_original = Column(String, nullable=False)   # nombre tal cual venia el GPX
    country = Column(String, nullable=True, index=True)          # ej. "españa", "francia"
    region = Column(String, nullable=True, index=True)           # ej. "navarra"
    sub_region = Column(String, nullable=True, index=True)       # ej. "pirineo aragones"

    started_at = Column(DateTime, nullable=False, index=True)    # primer trkpt con time
    start_lat = Column(Float, nullable=False)
    start_lon = Column(Float, nullable=False)

    distance_km = Column(Float, nullable=False, default=0.0, index=True)
    elevation_gain_m = Column(Integer, nullable=False, default=0, index=True)
    elevation_loss_m = Column(Integer, nullable=False, default=0)
    moving_time_s = Column(Integer, nullable=False, default=0)
    total_time_s = Column(Integer, nullable=False, default=0)
    max_altitude_m = Column(Integer, nullable=True)
    min_altitude_m = Column(Integer, nullable=True)

    difficulty_score = Column(Float, nullable=False, default=0.0, index=True)  # 0-10
    difficulty_level = Column(String, nullable=False, default="moderate", index=True)
    # easy | moderate | hard | very-hard

    # Path SVG ya escalado a viewBox 800x200, mismo formato que el mockup hero.
    elev_line_path = Column(Text, nullable=True)
    elev_area_path = Column(Text, nullable=True)

    # Notas y etiquetas editables desde la vista de detalle.
    notes = Column(Text, nullable=True)
    tags = Column(Text, nullable=True)  # JSON: lista de strings

    gpx_filename = Column(String, nullable=True)
    # Sin unique aislado: la unicidad pasa a ser por (user_id, gpx_sha256)
    # mediante __table_args__.
    gpx_sha256 = Column(String(64), nullable=True, index=True)

    # Identificador de "ruta única" (cluster de sesiones similares por trailhead
    # y altitud máxima). Lo materializamos al importar para que las vistas de
    # análisis puedan agrupar con un GROUP BY en lugar del union-find O(n²) en
    # memoria. La asignación vive en `app.clustering`.
    route_cluster_id = Column(Integer, nullable=True, index=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

    track_points = relationship(
        "TrackPoint",
        back_populates="route",
        cascade="all, delete-orphan",
        order_by="TrackPoint.seq",
    )


class TrackPoint(Base):
    """Track muestreado para mostrar polilineas en mapas (no full GPX)."""

    __tablename__ = "track_points"

    id = Column(Integer, primary_key=True)
    route_id = Column(Integer, ForeignKey("routes.id", ondelete="CASCADE"), nullable=False)
    seq = Column(Integer, nullable=False)
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    elevation_m = Column(Float, nullable=True)
    time = Column(DateTime, nullable=True)  # timestamp del trkpt

    route = relationship("Route", back_populates="track_points")


class Summit(Base):
    """Cimas detectadas para una ruta (vía Overpass API o fallback al máximo absoluto)."""

    __tablename__ = "summits"

    id = Column(Integer, primary_key=True)
    route_id = Column(Integer, ForeignKey("routes.id", ondelete="CASCADE"), nullable=False, index=True)
    seq = Column(Integer, nullable=False)          # orden de aparición en la ruta (por km)
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    elevation_m = Column(Integer, nullable=True)   # altitud en metros
    name = Column(String, nullable=True)           # nombre OSM, None si no disponible
    source = Column(String, nullable=False, default="overpass")  # "overpass" | "fallback"


Index("ix_summits_route", Summit.route_id, Summit.seq, unique=True)


class WeatherCache(Base):
    """Caché de respuestas Open-Meteo por (lat, lon, fecha)."""

    __tablename__ = "weather_cache"

    id = Column(Integer, primary_key=True)
    # Lat/lon redondeados a 2 decimales (~1 km) para reutilizar peticiones.
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    date_iso = Column(String, nullable=False)  # YYYY-MM-DD
    payload = Column(Text, nullable=False)  # JSON serializado
    fetched_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC))


Index("ix_weather_cache_lookup", WeatherCache.lat, WeatherCache.lon, WeatherCache.date_iso, unique=True)


class GeocodeCache(Base):
    """Caché de respuestas de Nominatim por celda (~1 km).

    Antes vivía en `data/geocode_cache.json` y se reescribía entero en cada hit
    (race con `_save_cache` fuera del lock + crecimiento sin límite). Aquí lo
    movemos a SQLite con índice por celda y `accessed_at` para evictar LRU.
    """

    __tablename__ = "geocode_cache"

    id = Column(Integer, primary_key=True)
    cell_key = Column(String, nullable=False, unique=True, index=True)  # "lat.xx,lon.xx"
    country = Column(String, nullable=True)
    region = Column(String, nullable=True)
    sub_region = Column(String, nullable=True)
    fetched_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC))
    accessed_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC))
