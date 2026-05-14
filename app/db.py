"""
Conexion a la base de datos.

Por defecto usa SQLite en data/mendi.db. Para cambiar a Postgres o MariaDB
basta con definir la variable de entorno MENDI_DATABASE_URL, por ejemplo:

    set MENDI_DATABASE_URL=postgresql+psycopg2://user:pass@host/mendi
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import declarative_base, sessionmaker

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
(DATA_DIR / "gpx").mkdir(exist_ok=True)

DEFAULT_SQLITE_URL = f"sqlite:///{(DATA_DIR / 'mendi.db').as_posix()}"
DATABASE_URL = os.environ.get("MENDI_DATABASE_URL", DEFAULT_SQLITE_URL)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, future=True)


# PRAGMAs por conexión SQLite:
# - foreign_keys=ON: SQLite no aplica ON DELETE CASCADE por defecto; lo
#   activamos para que la cascada user → routes → track_points funcione.
# - journal_mode=WAL: los lectores ya no se bloquean por el escritor. Sin
#   WAL, una sesión que escribe deja "colgadas" a las demás hasta terminar.
# - busy_timeout=5000: si dos escrituras coinciden, la segunda espera hasta
#   5 s en vez de fallar con "database is locked".
# - synchronous=NORMAL: combinado con WAL es seguro y notablemente más rápido.
if DATABASE_URL.startswith("sqlite"):
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record):  # noqa: ARG001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


def get_session():
    """Generador de sesión SQLAlchemy para inyección de dependencias en FastAPI.

    Garantiza que la sesión se cierra al terminar el request, tanto en caso
    de éxito como de excepción.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def commit(db, *, invalidate_analisis: bool = False) -> None:
    """Commit + invalidación opcional del cache de análisis.

    Vivía en `app/main.py:_commit` y solo lo usaban los handlers HTTP. Lo
    movemos aquí para que los módulos que escriben fuera de un request
    (weather, maintenance) puedan compartirlo y mantener la regla "siempre
    commit con invalidación cuando cambian agregaciones" en un único sitio.

    `invalidate_analisis=True` invalida el cache de la vista análisis (importar,
    renombrar, borrar, reprocesar, backfill). Para escrituras que no afectan a
    agregaciones (cache de clima, geocoder), pasar False.
    """
    db.commit()
    if invalidate_analisis:
        # Import perezoso: app.analisis no debe cargarse al inicializar la BD.
        from app.analisis import invalidate_analisis_cache
        invalidate_analisis_cache()


def _ensure_user_columns() -> None:
    """Añade columnas nuevas a `users` sin perder datos existentes.

    Hace ALTER TABLE perezoso para que instancias existentes (con BD ya
    poblada antes de introducir roles) ganen la columna `role` con un
    default seguro `'user'` sin necesidad de migraciones formales.
    """
    if not DATABASE_URL.startswith("sqlite"):
        # En Postgres/MariaDB que el DBA maneje el ALTER. SQLAlchemy
        # create_all no añade columnas a tablas existentes en ninguna BD.
        return
    with engine.connect() as conn:
        rows = list(conn.exec_driver_sql("PRAGMA table_info(users)"))
        cols = {r[1] for r in rows}
        if "role" not in cols:
            conn.exec_driver_sql(
                "ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'"
            )
            conn.commit()


def _bootstrap_first_admin() -> None:
    """Si no hay ningún admin activo, promueve al usuario más antiguo.

    Cubre dos escenarios:
    - Instalación nueva sin usuarios → no hace nada.
    - Instalación previa que ya tenía usuarios → todos quedan con `role='user'`
      tras el ALTER; aquí promovemos al primero para que la UI de admin sea
      accesible al menos para alguien.
    """
    from app.models import User
    with SessionLocal() as db:
        any_admin = (
            db.query(User)
            .filter(User.role == "admin", User.is_active == 1)
            .first()
        )
        if any_admin:
            return
        first_user = db.query(User).order_by(User.created_at.asc()).first()
        if first_user:
            first_user.role = "admin"
            db.commit()


def _normalize_geo_columns() -> None:
    """Migración one-shot idempotente para canonicalizar campos geográficos.

    Recorre `routes` y `geocode_cache` reescribiendo `country`, `region` y
    `sub_region` a su forma canónica (`canonical_geo`) cuando difieran. Solo
    emite `UPDATE` para filas que cambian al menos una columna; ejecutar la
    función dos veces seguidas no toca nada en la segunda pasada.

    Corre dentro de `init_db()` antes de que exista cache de análisis, así
    que hace `commit` explícito sin invalidar (no usar `app.db.commit`).
    """
    # Import perezoso: text_utils no debe forzar el bootstrap de la BD.
    from app.text_utils import canonical_geo

    tables = ("routes", "geocode_cache")
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    for table_name in tables:
        if table_name not in existing_tables:
            # `create_all` ya corrió antes; este branch solo protege contra
            # un cambio futuro en el orden de bootstrap.
            logger.debug("[geo-normalize] tabla %s no existe, skip", table_name)
            continue

        select_sql = text(
            f"SELECT id, country, region, sub_region FROM {table_name}"
        )
        update_sql = text(
            f"UPDATE {table_name} "
            "SET country = :country, region = :region, sub_region = :sub_region "
            "WHERE id = :id"
        )

        try:
            with engine.begin() as conn:
                rows = conn.execute(select_sql).all()
                touched = 0
                for row_id, country, region, sub_region in rows:
                    new_country = canonical_geo(country)
                    new_region = canonical_geo(region)
                    new_sub_region = canonical_geo(sub_region)
                    if (
                        new_country == country
                        and new_region == region
                        and new_sub_region == sub_region
                    ):
                        continue
                    conn.execute(
                        update_sql,
                        {
                            "id": row_id,
                            "country": new_country,
                            "region": new_region,
                            "sub_region": new_sub_region,
                        },
                    )
                    touched += 1
        except OperationalError:
            # TOCTOU: la tabla desapareció entre el inspect y el select.
            logger.debug(
                "[geo-normalize] tabla %s no accesible, skip", table_name
            )
            continue

        logger.info(
            "[geo-normalize] %s: %d fila(s) canonicalizadas (de %d)",
            table_name,
            touched,
            len(rows),
        )


def init_db() -> None:
    """Crea las tablas si no existen y aplica migraciones suaves."""
    from app import models  # noqa: F401  (registra los modelos)
    Base.metadata.create_all(bind=engine)
    _ensure_user_columns()
    _normalize_geo_columns()
    _bootstrap_first_admin()
