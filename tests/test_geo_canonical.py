"""Bug condition exploration tests para la dualidad de normalización geográfica.

Property 1 (Bug Condition): los dos writers (`name_cleaner.detect_region` y
`geocoder._extract_region`) deben producir la misma forma canónica para el
mismo concepto geográfico. Sobre el código sin fix, este test FALLA con el
counterexample `"españa" != "espana"`. El fallo confirma que el bug existe.
"""

import unicodedata
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings, strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import db as app_db
from app import geocoder as app_geocoder
from app import importer as app_importer
from app.geocoder import _extract_region
from app.models import Base, Route, User
from app.name_cleaner import _REGION_KEYWORDS, detect_region


def _expected_canonical(s):
    """Reproduce localmente el contrato canónico esperado por el design.

    NFKD + filtro combining + lower + strip + colapso de espacios. Devuelve
    None para entradas vacías o solo-espacios.
    """
    if s is None:
        return None
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = " ".join(s.lower().strip().split())
    return s or None


_KEYWORDS = sorted(_REGION_KEYWORDS)


@given(keyword=st.sampled_from(_KEYWORDS))
@settings(max_examples=len(_REGION_KEYWORDS), deadline=None)
def test_bug_condition_writers_acuerdan_pais_canonico(keyword):
    """Validates: Requirements 2.1, 2.2, 2.3.

    Property 1 (Bug Condition): para cualquier keyword del mapa de regiones,
    `detect_region(name, "")` y `_extract_region({"country": "España"})`
    SHALL producir el mismo valor canónico de country, y los componentes
    devueltos por detect_region SHALL ser ya canónicos.

    Sobre el código sin fix se espera el counterexample
    `name_cleaner_country='españa'` vs `geocoder_country='espana'`.
    """
    name = f"{keyword} ruta de prueba"
    c_nc, r_nc, s_nc = detect_region(name, "")
    c_geo, _r_geo, _s_geo = _extract_region({"country": "España"})

    assert c_nc == c_geo, (
        f"writers divergen para keyword={keyword!r}: "
        f"name_cleaner={c_nc!r} vs geocoder={c_geo!r}"
    )
    for component in (c_nc, r_nc, s_nc):
        if component is None:
            continue
        assert _expected_canonical(component) == component, (
            f"componente no canónico devuelto por detect_region: {component!r}"
        )


def _make_minimal_gpx(name: str) -> bytes:
    """GPX sintético mínimo con dos trkpts cronometrados (formato gpxpy)."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gpx version="1.1" creator="test" '
        'xmlns="http://www.topografix.com/GPX/1/1">\n'
        f"  <trk><name>{name}</name>\n"
        "    <trkseg>\n"
        '      <trkpt lat="42.8200" lon="-1.6500">'
        "<ele>500</ele><time>2024-06-01T08:00:00Z</time></trkpt>\n"
        '      <trkpt lat="42.8210" lon="-1.6510">'
        "<ele>510</ele><time>2024-06-01T08:00:30Z</time></trkpt>\n"
        "    </trkseg>\n"
        "  </trk>\n"
        "</gpx>\n"
    ).encode("utf-8")


@pytest.fixture
def in_memory_db(monkeypatch, tmp_path):
    """SQLite in-memory + redirección de GPX_ROOT y SessionLocal del geocoder."""
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
    )
    TestSessionLocal = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, future=True,
    )
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(app_db, "engine", engine)
    monkeypatch.setattr(app_db, "SessionLocal", TestSessionLocal)
    monkeypatch.setattr(app_geocoder, "SessionLocal", TestSessionLocal)
    monkeypatch.setattr(app_importer, "GPX_ROOT", tmp_path)
    return TestSessionLocal


def test_bug_condition_e2e_dos_writers_misma_localizacion(in_memory_db):
    """Validates: Requirements 2.3.

    Importa dos GPX en la misma localización real (España):
      - GPX 1: nombre con keyword 'Navarra' → camino name_cleaner.
      - GPX 2: nombre neutro → camino fallback geocoder (httpx mockeado).
    Sobre el código sin fix, route1.country='españa' y route2.country='espana'
    así que la igualdad falla.
    """
    TestSessionLocal = in_memory_db
    db = TestSessionLocal()
    try:
        user = User(email="t@test.local", password_hash="x")
        db.add(user)
        db.flush()
        user_id = user.id

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock(return_value=None)
        mock_resp.json = MagicMock(
            return_value={"address": {"country": "España"}},
        )

        gpx_keyword = _make_minimal_gpx("Navarra ruta de prueba")
        gpx_neutro = _make_minimal_gpx("Ruta sin region")

        with patch("app.geocoder.httpx.get", return_value=mock_resp):
            r1 = app_importer.process_gpx(db, user_id, "n1.gpx", gpx_keyword)
            r2 = app_importer.process_gpx(db, user_id, "n2.gpx", gpx_neutro)
        db.commit()

        assert r1.status == "ok", r1
        assert r2.status == "ok", r2

        route1 = db.query(Route).filter(Route.id == r1.route_id).one()
        route2 = db.query(Route).filter(Route.id == r2.route_id).one()

        # EXPECTED OUTCOME (sin fix): la siguiente aserción FALLA porque
        # name_cleaner devuelve "españa" y geocoder devuelve "espana".
        assert route1.country == route2.country, (
            f"writers divergen: route1.country={route1.country!r} "
            f"route2.country={route2.country!r}"
        )
    finally:
        db.close()


# Bug condition counterexample observed against unfixed code:
#   detect_region("navarra ruta de prueba", "")[0] == "españa"
#   _extract_region({"country": "España"})[0]      == "espana"
#   "españa" != "espana" → bug confirmado.


# ---------------------------------------------------------------------------
# Property 2: Preservation - Idempotencia y respeto a inputs ya canónicos.
#
# Estos tests capturan la baseline ANTES del fix: ya pasan sobre el código sin
# fix porque sólo ejercitan caminos que hoy están en forma canónica
# (`_expected_canonical` reproducido localmente, los lectores de stats que
# devuelven ASCII lower, `_extract_region` que ya canonicaliza vía
# `text_utils.normalize`, y `clean_name` que toca otra ruta y no debe verse
# afectada por el fix).
# ---------------------------------------------------------------------------

# Alfabeto restringido para construir strings ya en forma canónica:
# minúsculas ASCII + dígitos + espacio simple, sin diacríticos ni combining.
# Cualquier string compuesto de tokens de [a-z0-9]+ unidos por un espacio
# satisface `_expected_canonical(s) == s`.
_canonical_token = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789",
    min_size=1,
    max_size=8,
)
_canonical_strategy = st.builds(
    lambda parts: " ".join(parts),
    st.lists(_canonical_token, min_size=1, max_size=4),
)


@given(s=st.text(max_size=80))
@settings(max_examples=300, deadline=None)
def test_preservation_idempotencia_canonical_helper(s):
    """Validates: Requirements 3.1, 3.2.

    Idempotencia: para cualquier string Unicode `s`, aplicar la regla
    canónica dos veces produce el mismo resultado que aplicarla una vez.
    Equivalentemente, la salida del helper es siempre un punto fijo.
    """
    once = _expected_canonical(s)
    twice = _expected_canonical(once)
    assert once == twice, (
        f"helper no idempotente: f({s!r})={once!r}, f(f({s!r}))={twice!r}"
    )


@given(s=_canonical_strategy)
@settings(max_examples=200, deadline=None)
def test_preservation_invarianza_inputs_ya_canonicos(s):
    """Validates: Requirements 3.1, 3.2, 3.5.

    Invarianza: si `s` ya está en forma canónica, el helper devuelve `s`
    exactamente igual (no muta valores ya canónicos).
    """
    # Sanidad: la estrategia produce strings canónicos por construcción.
    # Si esta precondición fallase, el problema está en la estrategia.
    assert s and s == s.lower().strip()
    assert "  " not in s
    assert _expected_canonical(s) == s, (
        f"helper alteró un input ya canónico: {s!r} → {_expected_canonical(s)!r}"
    )


def test_preservation_none_vacio_espacios_devuelven_none():
    """Validates: Requirements 3.5.

    Preservación de la convención de "ausencia de valor": None, cadena
    vacía y cadena de solo espacios deben mapear a None.
    """
    assert _expected_canonical(None) is None
    assert _expected_canonical("") is None
    assert _expected_canonical("   ") is None
    assert _expected_canonical("\t\n  ") is None


def test_preservation_stats_country_key_espana_estable():
    """Validates: Requirements 3.3, 3.7.

    El lector `stats._country_key` recibe valores ya canónicos (post-fix la
    columna `routes.country` será siempre canónica) y debe seguir
    devolviéndolos tal cual. Capturamos el comportamiento ACTUAL para
    detectar regresiones tras el fix.
    """
    from app.stats import _country_key

    assert _country_key("espana") == "espana"
    # Robustez: el filtro `country=otros` debe seguir funcionando para
    # rutas con país ausente (es la rama que el fix promete preservar).
    assert _country_key(None) == "otros"
    assert _country_key("") == "otros"


def test_preservation_stats_region_key_navarra_estable():
    """Validates: Requirements 3.3, 3.7.

    Igual que el caso anterior pero para `_region_key`. Sobre el valor
    canónico `"navarra"` el resultado actual es `"navarra"` y debe
    mantenerse tras el fix.
    """
    from app.stats import _region_key

    assert _region_key("navarra") == "navarra"
    assert _region_key(None) == "otros"
    assert _region_key("") == "otros"


def test_preservation_extract_region_country_espana_ya_canonico():
    """Validates: Requirements 3.5.

    `_extract_region` hoy ya canonicaliza el country vía
    `text_utils.normalize`, devolviendo `"espana"` ante
    `address.country = "España"`. Tras el fix (que reemplaza `_normalize`
    por `canonical_geo`) el valor debe seguir siendo exactamente
    `"espana"`.
    """
    country, region, sub_region = _extract_region({"country": "España"})
    assert country == "espana"
    # Sin más campos en `address`, region y sub_region quedan en None.
    assert region is None
    assert sub_region is None


def test_preservation_extract_region_address_vacio_sigue_none():
    """Validates: Requirements 3.6.

    Cuando Nominatim no devuelve `address` (caso de fallo / cobertura
    inexistente), los tres componentes quedan en None. Es un invariante
    que el fix no debe romper.
    """
    assert _extract_region({}) == (None, None, None)


def test_preservation_clean_name_wikiloc_navarra_no_afectado():
    """Validates: Requirements 3.4.

    `clean_name` opera sobre el path de limpieza de nombres de GPX, que es
    independiente de la canonicalización geográfica. Capturamos el output
    actual para garantizar que el fix no toca por accidente este camino.
    """
    from app.name_cleaner import clean_name

    assert clean_name("Wikiloc - Ruta por Navarra") == "Ruta por Navarra"


# ---------------------------------------------------------------------------
# Integration tests (`test_integration_*`) — cobertura complementaria que
# ejercita el fix end-to-end. Se pueden seleccionar con `-k integration`.
#
# Cubren:
#   - Importación con dos writers (Requirement 2.3).
#   - Migración `_normalize_geo_columns` idempotente sobre datos mixtos
#     (Requirement 2.9).
#   - Filtros con tildes en `query_rutas` (Requirement 2.5).
#   - Facets de país sin duplicados en `build_rutas` (Requirement 2.4).
#   - `_related_routes` reconcilia formas mixtas tras la migración
#     (Requirement 2.8).
# ---------------------------------------------------------------------------


def _insert_route(
    db,
    *,
    user_id,
    name,
    country=None,
    region=None,
    sub_region=None,
    difficulty_score=5.0,
    started_at=None,
):
    """Inserta un Route mínimo permitiendo valores no canónicos.

    El ORM no canonicaliza al asignar columnas, así que este helper sirve
    para sembrar BD con datos legacy/no-canónicos sin pasar por `process_gpx`.
    """
    from datetime import datetime as _dt
    route = Route(
        user_id=user_id,
        name=name,
        name_original=name,
        country=country,
        region=region,
        sub_region=sub_region,
        started_at=started_at or _dt(2024, 6, 1, 8, 0, 0),
        start_lat=42.82,
        start_lon=-1.65,
        distance_km=10.0,
        elevation_gain_m=500,
        elevation_loss_m=500,
        moving_time_s=3600,
        total_time_s=3700,
        max_altitude_m=1000,
        min_altitude_m=500,
        difficulty_score=difficulty_score,
        difficulty_level="moderate",
    )
    db.add(route)
    db.flush()
    return route


def test_integration_import_dos_writers_misma_localizacion(in_memory_db):
    """Validates: Requirements 2.3.

    Importa dos GPX en la misma localización real (España):
      - GPX 1: nombre con keyword 'Navarra' → camino name_cleaner.
      - GPX 2: nombre neutro → camino fallback geocoder (httpx mockeado).
    Tras el fix, ambos rows deben terminar con el mismo `country` canónico.
    """
    TestSessionLocal = in_memory_db
    db = TestSessionLocal()
    try:
        user = User(email="int-import@test.local", password_hash="x")
        db.add(user)
        db.flush()
        user_id = user.id

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock(return_value=None)
        mock_resp.json = MagicMock(
            return_value={"address": {"country": "España"}},
        )

        gpx_keyword = _make_minimal_gpx("Navarra ruta de prueba")
        gpx_neutro = _make_minimal_gpx("Ruta sin region")

        with patch("app.geocoder.httpx.get", return_value=mock_resp):
            r1 = app_importer.process_gpx(db, user_id, "int1.gpx", gpx_keyword)
            r2 = app_importer.process_gpx(db, user_id, "int2.gpx", gpx_neutro)
        db.commit()

        assert r1.status == "ok", r1
        assert r2.status == "ok", r2

        route1 = db.query(Route).filter(Route.id == r1.route_id).one()
        route2 = db.query(Route).filter(Route.id == r2.route_id).one()

        assert route1.country == route2.country == "espana", (
            f"writers divergen tras el fix: route1.country={route1.country!r} "
            f"route2.country={route2.country!r}"
        )
    finally:
        db.close()


def test_integration_normalize_geo_columns_idempotente(in_memory_db):
    """Validates: Requirements 2.9.

    Carga una BD en memoria con valores mixtos pre-canónicos, llama a
    `_normalize_geo_columns()` dos veces y verifica:
      - Tras la primera pasada todas las filas (`routes` + `geocode_cache`)
        son canónicas.
      - La segunda pasada no modifica ninguna fila (idempotente).
    """
    from app.models import GeocodeCache
    from app.text_utils import canonical_geo

    TestSessionLocal = in_memory_db

    # Sembrar BD con valores mixtos no canónicos.
    db = TestSessionLocal()
    try:
        user = User(email="int-migrate@test.local", password_hash="x")
        db.add(user)
        db.flush()
        user_id = user.id

        _insert_route(
            db, user_id=user_id, name="r-tilde",
            country="España", region="navarra", sub_region=None,
        )
        _insert_route(
            db, user_id=user_id, name="r-eñe-acento",
            country="espana", region="león", sub_region="picos de europa",
        )
        _insert_route(
            db, user_id=user_id, name="r-espacios",
            country="espana", region="huesca", sub_region="  Pirineo  Aragonés  ",
        )
        # Ya canónica: la migración no debería tocarla.
        _insert_route(
            db, user_id=user_id, name="r-canonica",
            country="espana", region="cantabria", sub_region=None,
        )

        # geocode_cache: dos celdas, una mixta y otra canónica.
        db.add(GeocodeCache(
            cell_key="42.82,-1.65",
            country="España",
            region="León",
            sub_region="Pirineo Aragonés",
        ))
        db.add(GeocodeCache(
            cell_key="43.00,-2.00",
            country="espana",
            region="navarra",
            sub_region=None,
        ))
        db.commit()
    finally:
        db.close()

    # Primera pasada: canonicaliza.
    app_db._normalize_geo_columns()

    db = TestSessionLocal()
    try:
        rows = {r.name: r for r in db.query(Route).all()}
        # Todas las filas tienen valores canónicos (cada componente coincide
        # con su forma canónica o es None).
        for name, r in rows.items():
            for col, value in (
                ("country", r.country),
                ("region", r.region),
                ("sub_region", r.sub_region),
            ):
                assert canonical_geo(value) == value, (
                    f"row {name!r} columna {col} no canónica tras 1ª pasada: {value!r}"
                )

        # Comprobaciones explícitas de las transformaciones esperadas.
        assert rows["r-tilde"].country == "espana"
        assert rows["r-eñe-acento"].region == "leon"
        assert rows["r-espacios"].sub_region == "pirineo aragones"
        # La fila ya canónica conserva sus valores intactos.
        assert (
            rows["r-canonica"].country,
            rows["r-canonica"].region,
            rows["r-canonica"].sub_region,
        ) == ("espana", "cantabria", None)

        gc_rows = db.query(GeocodeCache).all()
        for gc in gc_rows:
            assert canonical_geo(gc.country) == gc.country
            assert canonical_geo(gc.region) == gc.region
            assert canonical_geo(gc.sub_region) == gc.sub_region

        # Snapshot post-1ª pasada para comparar tras la 2ª.
        snapshot = {
            r.id: (r.country, r.region, r.sub_region) for r in rows.values()
        }
        gc_snapshot = {
            gc.id: (gc.country, gc.region, gc.sub_region) for gc in gc_rows
        }
    finally:
        db.close()

    # Segunda pasada: debe ser idempotente.
    app_db._normalize_geo_columns()

    db = TestSessionLocal()
    try:
        for r in db.query(Route).all():
            assert (r.country, r.region, r.sub_region) == snapshot[r.id], (
                f"route {r.id} cambió en la 2ª pasada (no idempotente)"
            )
        for gc in db.query(GeocodeCache).all():
            assert (gc.country, gc.region, gc.sub_region) == gc_snapshot[gc.id], (
                f"geocode_cache {gc.id} cambió en la 2ª pasada (no idempotente)"
            )
    finally:
        db.close()


def test_integration_query_rutas_filter_with_accent(in_memory_db):
    """Validates: Requirements 2.5.

    `query_rutas` con `q.country='España'` (input con tilde) sobre una BD
    cuyas rutas están guardadas con `country='espana'` SHALL devolver
    todas esas rutas: el input del usuario se canonicaliza antes de
    comparar.
    """
    from app.stats import RutaQuery, query_rutas

    TestSessionLocal = in_memory_db
    db = TestSessionLocal()
    try:
        user = User(email="int-filter@test.local", password_hash="x")
        db.add(user)
        db.flush()
        user_id = user.id

        _insert_route(db, user_id=user_id, name="r-es-1", country="espana")
        _insert_route(db, user_id=user_id, name="r-es-2", country="espana")
        _insert_route(db, user_id=user_id, name="r-fr", country="francia")
        db.commit()

        # Input con tilde — el filtro lo canonicaliza a "espana".
        result = query_rutas(db, user_id, RutaQuery(country="España"))
        assert result.matched == 2, (
            f"esperado 2 rutas de España, recibido {result.matched}"
        )
        names = {it.name for it in result.items}
        assert names == {"r-es-1", "r-es-2"}, names

        # Sanity: input con eñe + tilde ('España ') con espacio extra también funciona.
        result_padded = query_rutas(db, user_id, RutaQuery(country=" España "))
        assert result_padded.matched == 2
    finally:
        db.close()


def test_integration_build_rutas_country_facets_no_duplicados(in_memory_db):
    """Validates: Requirements 2.4.

    `build_rutas` SHALL aglutinar todas las formas no canónicas de un país
    en una única chip de facets: la lista no contiene `Espana` y `España`
    como entradas separadas.
    """
    from app.stats import build_rutas

    TestSessionLocal = in_memory_db
    db = TestSessionLocal()
    try:
        user = User(email="int-facets@test.local", password_hash="x")
        db.add(user)
        db.flush()
        user_id = user.id

        # Inserción mixta: legacy + canónica + variación con espacios.
        _insert_route(db, user_id=user_id, name="r-españa", country="España")
        _insert_route(db, user_id=user_id, name="r-espana", country="espana")
        _insert_route(db, user_id=user_id, name="r-padded", country="  España  ")
        # País distinto: debe seguir apareciendo como otra chip.
        _insert_route(db, user_id=user_id, name="r-fr", country="francia")
        db.commit()

        data = build_rutas(db, user_id)
        keys = [c.key for c in data.countries]

        assert "espana" in keys
        # No debe haber dos entradas distintas para el mismo concepto.
        assert keys.count("espana") == 1, keys
        # No debe colarse ninguna forma con tildes/ñ ni con mayúsculas.
        for variant in ("españa", "España", "Espana", "  españa  "):
            assert variant not in keys, (
                f"facets no canónicos: {variant!r} aparece en {keys}"
            )

        espana_facet = next(c for c in data.countries if c.key == "espana")
        assert espana_facet.count == 3, espana_facet
    finally:
        db.close()


def test_integration_related_routes_match_canonical(in_memory_db):
    """Validates: Requirements 2.8.

    Rutas guardadas con `region='leon'` y `region='león'` SHALL relacionarse
    entre sí: tras la migración ambas quedan con la misma forma canónica
    `'leon'`, así que `_related_routes` las empareja por igualdad
    canónica.
    """
    from app.detail import _related_routes

    TestSessionLocal = in_memory_db
    db = TestSessionLocal()
    try:
        user = User(email="int-related@test.local", password_hash="x")
        db.add(user)
        db.flush()
        user_id = user.id

        # Mismo concepto regional, formas legacy distintas; difficulty
        # cercana para que entren ambos en el rango ±2.
        route_a = _insert_route(
            db, user_id=user_id, name="ruta A leon",
            country="España", region="león", sub_region=None,
            difficulty_score=4.5,
        )
        route_b = _insert_route(
            db, user_id=user_id, name="ruta B leon",
            country="espana", region="leon", sub_region=None,
            difficulty_score=5.0,
        )
        # Ruido: una ruta de otra región que NO debe aparecer como relacionada.
        _insert_route(
            db, user_id=user_id, name="ruta otra region",
            country="espana", region="navarra", sub_region=None,
            difficulty_score=4.7,
        )
        db.commit()
        a_id = route_a.id
        b_id = route_b.id
    finally:
        db.close()

    # Migración: canonicaliza ambas filas a region='leon'.
    app_db._normalize_geo_columns()

    db = TestSessionLocal()
    try:
        a = db.query(Route).filter(Route.id == a_id).one()
        # Sanity: la migración dejó ambas en forma canónica.
        b = db.query(Route).filter(Route.id == b_id).one()
        assert a.region == "leon"
        assert b.region == "leon"

        related = _related_routes(db, user_id, a)
        related_ids = {r.id for r in related}
        assert b_id in related_ids, (
            f"_related_routes no encontró route_B (region='león' migrada a "
            f"'leon'); related_ids={related_ids}"
        )
    finally:
        db.close()
