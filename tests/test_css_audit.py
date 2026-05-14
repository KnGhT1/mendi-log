"""Tests del audit script de CSS (`scripts/audit_css.py`).

Esta suite cubre los property tests (Hypothesis) y los tests example/smoke
descritos en la sección "Testing Strategy" del design de css-consolidation
(TS3 / TS4 / TS5).

En esta primera tarea (1.2) se aterriza el andamiaje compartido:

- Strategies de Hypothesis (`token_name`, `class_name`, `color_value`).
- Helper sintetizador `synthesize_css(...)` para construir CSS bien formado a
  partir de conjuntos controlados de tokens / clases / duplicados.
- Un smoke test (`test_module_imports_ok`) que valida que la superficie pública
  de `scripts/audit_css.py` (las dataclasses C4 + las funciones puras y de
  I/O) se importa correctamente.

Los property tests propiamente dichos (Property 1–8 del design) se añaden en
las sub-tareas 2.2, 2.4, 2.6, 2.8, 2.10, 2.11, 2.13 y 2.15.

Hypothesis es una dependencia *de desarrollo* (`requirements-dev.txt`); R10.3
prohíbe únicamente añadirla a `requirements.txt`.
"""

from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st
from hypothesis.strategies import SearchStrategy

from scripts.audit_css import (
    ActiveClasses,
    AuditReport,
    CssParseResult,
    DuplicateRecord,
    PageCssLink,
    audit,
    extract_aliases_temporales,
    extract_declared_classes,
    extract_declared_tokens,
    extract_duplicates_in_mq,
    extract_html_classes,
    extract_js_classes,
    extract_page_css_links,
    extract_used_tokens,
    main,
)

# ---------------------------------------------------------------------------
# Strategies (TS4)
# ---------------------------------------------------------------------------


def token_name() -> SearchStrategy[str]:
    """Nombre de token CSS: ``--[a-z][a-z0-9-]{0,20}``."""
    return st.from_regex(r"^--[a-z][a-z0-9-]{0,20}$", fullmatch=True)


def class_name() -> SearchStrategy[str]:
    """Nombre de clase CSS (sin el ``.`` inicial): ``[a-z][a-z0-9-]{0,30}``."""
    return st.from_regex(r"^[a-z][a-z0-9-]{0,30}$", fullmatch=True)


def color_value() -> SearchStrategy[str]:
    """Valor de color: ``#RRGGBB`` o ``rgba(R,G,B,0.dd)``."""
    return st.one_of(
        st.from_regex(r"^#[0-9a-fA-F]{6}$", fullmatch=True),
        st.from_regex(
            r"^rgba\(\d{1,3},\d{1,3},\d{1,3},0\.\d{1,2}\)$", fullmatch=True
        ),
    )


# ---------------------------------------------------------------------------
# Helper sintetizador (TS4)
# ---------------------------------------------------------------------------

# Propiedades estándar (no custom) usadas para inyectar `var(--*)` sin generar
# tokens declarados accidentalmente. Se ciclan para no repetir la misma
# propiedad cuando hay muchos `used_tokens`.
_USED_PROPS: tuple[str, ...] = (
    "color",
    "background-color",
    "border-color",
    "outline-color",
    "fill",
    "stroke",
    "caret-color",
    "accent-color",
)


def _emit_class_rule(cls: str, partner: str, idx: int) -> list[str]:
    """Devuelve las líneas CSS para una clase declarada, rotando entre 6
    plantillas distintas (top-level y dentro de ``@media``, combinadas con
    tag/id/pseudo-clase/pseudo-elemento y otras clases en el mismo selector
    compuesto). ``partner`` SHALL ser otra clase del mismo conjunto ``C`` —
    se garantiza desde `synthesize_css(...)`— para que las plantillas que
    usan dos clases no introduzcan clases fantasma fuera de ``C``.
    """
    template = idx % 6
    if template == 0:
        # Plantilla 0: regla top-level mínima.
        return [f".{cls} {{ display: block; }}"]
    if template == 1:
        # Plantilla 1: top-level con tag y pseudo-clase.
        return [f"div.{cls}:hover {{ color: red; }}"]
    if template == 2:
        # Plantilla 2: bajo `@media`, combinada con id (los `#id` no son
        # clases, así que el extractor SHALL devolver sólo `.{cls}`).
        return [
            "@media (max-width: 720px) {",
            f"  #ident.{cls} {{ color: red; }}",
            "}",
        ]
    if template == 3:
        # Plantilla 3: bajo `@media`, con pseudo-elemento y string vacía
        # en el body para ejercitar el manejo de cadenas del extractor.
        return [
            "@media (min-width: 600px) {",
            f"  .{cls}::before {{ content: ''; }}",
            "}",
        ]
    if template == 4:
        # Plantilla 4: top-level con dos clases en el mismo selector
        # compuesto (`.a.b`). `partner` ya pertenece al conjunto ``C``.
        return [f".{cls}.{partner} {{ color: red; }}"]
    # Plantilla 5: bajo `@media`, dos clases con combinador descendiente
    # directo (`.a > .b`). `partner` ya pertenece al conjunto ``C``.
    return [
        "@media (max-width: 900px) {",
        f"  .{cls} > .{partner} {{ color: red; }}",
        "}",
    ]


def synthesize_css(
    *,
    declared_tokens: frozenset[str] | set[str],
    used_tokens: frozenset[str] | set[str],
    declared_classes: frozenset[str] | set[str] = frozenset(),
    duplicates: tuple[tuple[str, str, int], ...] = (),
) -> str:
    """Construye un texto CSS bien formado con los conjuntos pedidos.

    Args:
        declared_tokens: tokens a declarar en ``:root`` como ``--name: #000000;``.
        used_tokens: tokens a referenciar via ``var(--name)`` desde un selector
            de etiqueta (no clase) para no contaminar `declared_classes`.
        declared_classes: clases a declarar. Cada clase se emite en una de seis
            plantillas rotativas que mezclan reglas top-level y bajo ``@media``,
            combinadas con tag, id, pseudo-clase, pseudo-elemento y otras
            clases del mismo conjunto en el selector compuesto. Se garantiza
            que el conjunto exacto de clases vistas por el extractor coincide
            con ``declared_classes`` (no se inyectan clases fantasma).
        duplicates: tupla de ``(selector, media_query, n_repeticiones)``.
            ``media_query`` puede ser una cadena vacía para indicar scope
            top-level (sin ``@media``); cualquier otro valor se usa tal cual
            tras ``@media`` (p. ej. ``"(max-width: 720px)"``).

    El CSS resultante es determinista para los mismos inputs (las claves de
    los conjuntos se ordenan antes de emitirlas), de modo que los tests basados
    en este helper no dependen del hash randomization de Python.
    """
    lines: list[str] = []

    if declared_tokens:
        lines.append(":root {")
        for tok in sorted(declared_tokens):
            lines.append(f"  {tok}: #000000;")
        lines.append("}")

    if used_tokens:
        lines.append("body {")
        for i, tok in enumerate(sorted(used_tokens)):
            prop = _USED_PROPS[i % len(_USED_PROPS)]
            lines.append(f"  {prop}: var({tok});")
        lines.append("}")

    if declared_classes:
        sorted_classes = sorted(declared_classes)
        n = len(sorted_classes)
        for idx, cls in enumerate(sorted_classes):
            # `partner` se elige de forma cíclica dentro del propio conjunto
            # `C`. Si `|C| == 1`, partner == cls y obtenemos `.cls.cls`, que
            # el extractor devuelve como ``{cls}`` igualmente.
            partner = sorted_classes[(idx + 1) % n]
            lines.extend(_emit_class_rule(cls, partner, idx))

    for selector, media_query, n_repetitions in duplicates:
        if media_query:
            lines.append(f"@media {media_query} {{")
            for _ in range(n_repetitions):
                lines.append(f"  {selector} {{ color: red; }}")
            lines.append("}")
        else:
            for _ in range(n_repetitions):
                lines.append(f"{selector} {{ color: red; }}")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Smoke tests
# ---------------------------------------------------------------------------


def test_module_imports_ok() -> None:
    """`scripts/audit_css.py` expone toda la superficie pública del design C4.

    Comprueba sólo el wiring (las dataclasses son tipos y las funciones son
    invocables); el comportamiento de cada extractor se verifica en los
    property tests posteriores.
    """
    # Dataclasses (C4 del design)
    assert isinstance(DuplicateRecord, type)
    assert isinstance(CssParseResult, type)
    assert isinstance(ActiveClasses, type)
    assert isinstance(PageCssLink, type)
    assert isinstance(AuditReport, type)

    # Funciones puras (sin I/O)
    for fn in (
        extract_declared_tokens,
        extract_used_tokens,
        extract_declared_classes,
        extract_duplicates_in_mq,
        extract_html_classes,
        extract_js_classes,
        extract_aliases_temporales,
        extract_page_css_links,
    ):
        assert callable(fn), f"{fn!r} debería ser invocable"

    # Capa de I/O y CLI
    assert callable(audit)
    assert callable(main)


def test_synthesize_css_produces_nonempty_output() -> None:
    """Smoke del helper: el sintetizador produce CSS con contenido cuando se
    le pide al menos un token declarado o usado.

    No comprueba el round-trip (eso lo cubren los property tests 1, 2 y 4 en
    sub-tareas posteriores), sólo que el helper no devuelve cadena vacía y
    que respeta la forma básica del CSS (apertura/cierre de bloques).
    """
    css = synthesize_css(
        declared_tokens={"--bg", "--accent"},
        used_tokens={"--bg"},
        declared_classes={"card", "stat"},
        duplicates=((".foo", "(max-width: 720px)", 2),),
    )
    assert css.strip(), "synthesize_css no debería devolver cadena vacía"
    assert css.count("{") == css.count("}")
    assert ":root" in css
    assert "var(--bg)" in css
    assert ".card" in css
    assert "@media (max-width: 720px)" in css


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------


# Generador de selectores simples (con `.` ya antepuesto) usados en los
# duplicados sintéticos. No se solapa intencionalmente con `class_name()`
# porque aquí queremos el selector completo tal y como lo emitirá el helper
# `synthesize_css(...)` y el extractor.
def _duplicate_selector() -> SearchStrategy[str]:
    return st.from_regex(r"^\.[a-z][a-z0-9-]{0,15}$", fullmatch=True)


# Generador de cabecera de media query (sin la palabra `@media` y sin
# llaves). Cubrimos un par de variantes con `min-width` / `max-width` y
# orientación, suficientes para verificar la asociación selector ↔ mq sin
# explotar el espacio de búsqueda.
def _media_query() -> SearchStrategy[str]:
    return st.one_of(
        st.builds(
            lambda px: f"(max-width: {px}px)",
            st.integers(min_value=320, max_value=1600),
        ),
        st.builds(
            lambda px: f"(min-width: {px}px)",
            st.integers(min_value=320, max_value=1600),
        ),
        st.sampled_from(
            (
                "(orientation: portrait)",
                "(orientation: landscape)",
                "screen and (max-width: 720px)",
                "print",
            )
        ),
    )


@given(
    # Lista de duplicados a inyectar: cada entrada es
    # ``(selector, media_query, K_i)`` con ``K_i ≥ 2`` (cada selector
    # aparece al menos dos veces dentro del mismo media query). El scope
    # top-level se representa con ``media_query == ""``.
    duplicates=st.lists(
        st.tuples(
            _duplicate_selector(),
            st.one_of(st.just(""), _media_query()),
            st.integers(min_value=2, max_value=5),
        ),
        max_size=8,
    ),
    # Selectores únicos `M` que aparecen exactamente una vez (no contribuyen
    # al recuento de duplicados, sólo aportan ruido para que el extractor
    # tenga que distinguir entre repeticiones reales y reglas adicionales).
    uniques=st.lists(
        st.tuples(
            _duplicate_selector(),
            st.one_of(st.just(""), _media_query()),
        ),
        max_size=8,
    ),
)
@settings(max_examples=200)
def test_extract_duplicates_in_mq_round_trip(
    duplicates: list[tuple[str, str, int]],
    uniques: list[tuple[str, str]],
) -> None:
    """**Property 4: Detección exacta de duplicados dentro del mismo media query**.

    **Validates: Requirements 6.2, 11.4, 2.10**

    Generamos un CSS sintético con `K_i` repeticiones de selectores dados en
    distintos media queries más `M` selectores únicos, y comprobamos:

    1. ``len(extract_duplicates_in_mq(css)) == sum(K_i - 1)`` — cada selector
       que aparece `K` veces dentro del mismo media query produce exactamente
       `K - 1` registros (R6.2 / R11.4).
    2. La asociación selector ↔ media_query queda correctamente preservada en
       cada `DuplicateRecord` (R2.10).
    3. Los selectores únicos en el ruido (`uniques`) NO generan ningún
       `DuplicateRecord` por sí solos (un selector que aparece una sola vez
       en un scope dado nunca es duplicado).
    """
    # Hypothesis puede generar el mismo `(selector, mq)` en `duplicates` y en
    # `uniques`, o repetido dentro de `duplicates` en sí. Si dos entradas
    # comparten clave `(selector, mq)`, sus repeticiones se suman: el
    # extractor sólo ve el CSS final, no nuestro reparto. Calculamos el
    # recuento total por clave y verificamos contra la realidad agregada.
    counts: dict[tuple[str, str], int] = {}
    for selector, mq, k in duplicates:
        counts[(selector, mq)] = counts.get((selector, mq), 0) + k
    for selector, mq in uniques:
        counts[(selector, mq)] = counts.get((selector, mq), 0) + 1

    css = synthesize_css(
        declared_tokens=frozenset(),
        used_tokens=frozenset(),
        duplicates=tuple(duplicates),
    )
    # Añadimos los selectores únicos manualmente: `synthesize_css` sólo
    # emite duplicados a través del parámetro `duplicates`, así que los
    # `uniques` se inyectan como reglas adicionales con la misma forma para
    # asegurar que el extractor ve el mismo dialecto sintáctico.
    extra_lines: list[str] = []
    for selector, mq in uniques:
        if mq:
            extra_lines.append(f"@media {mq} {{")
            extra_lines.append(f"  {selector} {{ color: red; }}")
            extra_lines.append("}")
        else:
            extra_lines.append(f"{selector} {{ color: red; }}")
    if extra_lines:
        css = css + "\n".join(extra_lines) + "\n"

    records = extract_duplicates_in_mq(css, Path("synthetic.css"))

    # 1. Cardinalidad: K - 1 registros por cada (selector, mq) con K ≥ 2.
    expected_total = sum(k - 1 for k in counts.values() if k >= 2)
    assert len(records) == expected_total, (
        f"Esperaba {expected_total} duplicados pero el extractor devolvió "
        f"{len(records)}. Counts: {counts}"
    )

    # 2 + 3. Asociación selector ↔ media_query: agrupar los registros
    # devueltos por el extractor y verificar que coinciden con el recuento
    # esperado (`K - 1` por clave duplicada, `0` por clave única).
    actual_counts: dict[tuple[str, str], int] = {}
    for record in records:
        key = (record.selector, record.media_query)
        actual_counts[key] = actual_counts.get(key, 0) + 1

    expected_per_key = {
        key: k - 1 for key, k in counts.items() if k >= 2
    }
    assert actual_counts == expected_per_key, (
        f"La asociación selector ↔ media_query no coincide. "
        f"Esperaba {expected_per_key}, recibí {actual_counts}."
    )


# ---------------------------------------------------------------------------
# Property tests
# ---------------------------------------------------------------------------


# Feature: css-consolidation, Property 1: Round-trip de extracción de tokens.
# Validates: Requirements 7.1, 11.1, 1.9
@given(
    declared=st.sets(token_name(), max_size=20),
    used=st.sets(token_name(), max_size=20),
)
@settings(max_examples=200)
def test_property_1_token_extraction_roundtrip(
    declared: set[str], used: set[str]
) -> None:
    """`extract_declared_tokens` y `extract_used_tokens` round-trip con
    `synthesize_css(...)`.

    Para cualquier par de conjuntos de tokens declarados ``D`` y usados
    ``U``, el CSS sintetizado por el helper SHALL devolver exactamente esos
    conjuntos al pasarlo por los extractores, independientemente del orden,
    espaciado, comentarios o anidamiento de media queries.

    **Validates: Requirements 7.1, 11.1, 1.9**
    """
    css = synthesize_css(declared_tokens=declared, used_tokens=used)
    assert extract_declared_tokens(css) == declared
    assert extract_used_tokens(css) == used


# Feature: css-consolidation, Property 2: Round-trip de extracción de clases CSS.
# Validates: Requirements 7.1, 11.2, 11.3
@given(declared_classes=st.sets(class_name(), max_size=20))
@settings(max_examples=200)
def test_property_2_class_extraction_roundtrip(
    declared_classes: set[str],
) -> None:
    """`extract_declared_classes` round-trip con `synthesize_css(...)`.

    Para cualquier conjunto de nombres de clase ``C``, el CSS sintetizado
    por el helper —mezclando reglas top-level y bajo ``@media``, combinadas
    con tag, id, pseudo-clase, pseudo-elemento y otras clases del mismo
    conjunto en el selector compuesto— SHALL devolver exactamente ``C`` al
    pasarlo por `extract_declared_classes`, sin clases fantasma ni omisiones.

    **Validates: Requirements 7.1, 11.2, 11.3**
    """
    css = synthesize_css(
        declared_tokens=frozenset(),
        used_tokens=frozenset(),
        declared_classes=declared_classes,
    )
    assert extract_declared_classes(css) == declared_classes


# ---------------------------------------------------------------------------
# Property 3 — Round-trip de extracción de clases activas en HTML/JS (D7)
# ---------------------------------------------------------------------------

# Cinco patrones soportados por los extractores (D7 del design):
#   0: HTML  class="..."           (double-quoted)
#   1: HTML  class='...'           (single-quoted)
#   2: JS    classList.add(...) / classList.remove(...)
#   3: JS    classList.toggle("...") / classList.toggle("...", cond)
#   4: JS    setAttribute("class", "...")
#
# Los patrones 0 y 1 generan HTML; 2, 3 y 4 generan JS. La partición se hace
# en el test asignando a cada clase un único patrón.

# Ruido HTML: atributos no relacionados (id, data-*, aria-*, href, type, name,
# placeholder, title, tabindex) y texto suelto. Cuidadosamente construido
# para no contener ningún sufijo `class=` (`data-class=`, `someclass=`, etc.)
# que dispararía la regex `class\s*=` del extractor (no usa word boundary).
_HTML_NOISE: str = (
    '<header id="root" data-page="resumen" aria-hidden="false" tabindex="0">'
    'contenido neutro sin atributo de clase</header>'
    '<a href="/rutas" title="enlace" data-id="x-1">enlace</a>'
    '<button type="button" aria-label="cerrar">x</button>'
    '<input type="text" name="search" placeholder="buscar">'
    '<div data-foo="bar" role="presentation"></div>'
)

# Ruido JS: llamadas que NO entran en el contrato D7 del extractor.
#   * `classList.contains("...")` y `classList.replace("a", "b")` no son
#     ni `add` ni `remove` ni `toggle`, así que no matchean.
#   * `setAttribute("aria-*", ...)` y `setAttribute("data-*", ...)` con
#     primer argumento ≠ "class" están fuera del patrón 4.
#   * `removeAttribute("class")` y `getAttribute("class")` no están en D7.
#   * Strings sueltos (`const css = "..."`) no aparecen en ningún patrón.
#   * `classList.toggle(varName, cond)` con primer argumento dinámico (no
#     literal de cadena) tampoco matchea: la regex de toggle exige literal.
_JS_NOISE: str = """
// classList.contains: lectura, no añade/quita clases.
if (el.classList.contains("contains-noise-not-extracted")) { return; }
// classList.replace: no está en D7.
el.classList.replace("not-extracted-a", "not-extracted-b");
// setAttribute con clave distinta de "class".
el.setAttribute("aria-hidden", "true");
el.setAttribute("data-id", "noise-id-not-extracted");
// removeAttribute / getAttribute: no está en D7.
el.removeAttribute("hidden");
const v = el.getAttribute("class");
// Strings sueltos en código: no aparecen en ningún patrón D7.
const css_text = ".foo { color: red; }";
const message = "irrelevante-no-extraida";
// classList.toggle con primer argumento dinámico (variable, no literal).
el.classList.toggle(name, cond);
"""


def _emit_html_pattern(cls: str, pattern: int, idx: int) -> str:
    """Devuelve un fragmento HTML que inyecta `cls` en el patrón pedido.

    Args:
        cls: nombre de clase a inyectar (sin el ``.`` inicial).
        pattern: ``0`` para ``class="..."`` (double-quoted), ``1`` para
            ``class='...'`` (single-quoted).
        idx: índice del fragmento — sólo se usa para añadir un atributo
            `data-i` que aporta variación de quoting al ruido y NO afecta
            al extractor.
    """
    if pattern == 0:
        # Patrón 1 D7: class="..." (double-quoted), mezclado con atributos
        # no relacionados (id, data-*, aria-*) entre `class` y `>`.
        return (
            f'<div id="el-{idx}" class="{cls}" data-i="{idx}" '
            f'aria-hidden="false">x</div>'
        )
    # Patrón 2 D7: class='...' (single-quoted). Variamos también el quoting
    # de los atributos vecinos para mezclar single/double en la misma etiqueta.
    return (
        f"<span id='sp-{idx}' class='{cls}' data-i='{idx}' "
        f"role='presentation'>x</span>"
    )


def _emit_js_pattern(cls: str, pattern: int, idx: int) -> str:
    """Devuelve una sentencia JS que inyecta `cls` en el patrón pedido.

    Args:
        cls: nombre de clase a inyectar.
        pattern: ``2`` → ``classList.add/remove``; ``3`` → ``classList.toggle``
            (alterna con/sin segundo argumento); ``4`` → ``setAttribute(
            "class", "...")``.
        idx: índice — sirve para alternar entre sub-variantes (add↔remove,
            toggle con/sin `cond`, double↔single quoting en setAttribute).
    """
    if pattern == 2:
        # Patrón 3 D7: classList.add(...) / classList.remove(...). Alternamos
        # entre `add` y `remove`, y entre quoting double/single para ejercitar
        # ambos brazos del literal de cadena del extractor.
        if idx % 4 == 0:
            return f'el.classList.add("{cls}");'
        if idx % 4 == 1:
            return f"el.classList.remove('{cls}');"
        if idx % 4 == 2:
            return f"el.classList.add('{cls}');"
        return f'el.classList.remove("{cls}");'
    if pattern == 3:
        # Patrón 4 D7: classList.toggle(...). Alternamos las dos sub-variantes
        # documentadas (con segundo argumento `cond` y sin él), y mezclamos
        # quoting double/single.
        if idx % 4 == 0:
            return f'el.classList.toggle("{cls}");'
        if idx % 4 == 1:
            return f"el.classList.toggle('{cls}', cond);"
        if idx % 4 == 2:
            return f"el.classList.toggle('{cls}');"
        return f'el.classList.toggle("{cls}", true);'
    # pattern == 4 — Patrón 5 D7: setAttribute("class", "..."). Alternamos
    # quoting double/single en los dos argumentos.
    if idx % 4 == 0:
        return f'el.setAttribute("class", "{cls}");'
    if idx % 4 == 1:
        return f"el.setAttribute('class', '{cls}');"
    if idx % 4 == 2:
        return f"el.setAttribute(\"class\", '{cls}');"
    return f"el.setAttribute('class', \"{cls}\");"


# Feature: css-consolidation, Property 3: Round-trip de extracción de clases
# activas en HTML/JS.
# Validates: Requirements 7.2, 11.2
@given(
    classes_to_pattern=st.dictionaries(
        class_name(),
        st.integers(min_value=0, max_value=4),
        max_size=15,
    ),
)
@settings(max_examples=200)
def test_property_3_active_classes_extraction_roundtrip(
    classes_to_pattern: dict[str, int],
) -> None:
    """`extract_html_classes` y `extract_js_classes` round-trip sobre los
    cinco patrones soportados (D7).

    Para cualquier asignación de clases a uno de los cinco patrones D7
    (HTML ``class="..."``, HTML ``class='...'``, JS ``classList.add/remove``,
    JS ``classList.toggle`` con/sin segundo argumento, JS ``setAttribute(
    "class", "...")``), mezclados con quoting variable (double/single en la
    misma etiqueta o llamada), atributos HTML no relacionados (``id``,
    ``data-*``, ``aria-*``, ``href``, ``type``, ``name``, ``placeholder``,
    ``title``, ``tabindex``, ``role``) y llamadas JS no relacionadas
    (``classList.contains``, ``classList.replace``, ``setAttribute`` con
    clave ≠ ``"class"``, ``removeAttribute``, ``getAttribute``, strings
    sueltos, ``classList.toggle`` con primer argumento dinámico), los
    extractores SHALL devolver exactamente:

    - ``extract_html_classes(html) == {clases asignadas a los patrones 0 o 1}``
    - ``extract_js_classes(js) == {clases asignadas a los patrones 2, 3 o 4}``

    **Validates: Requirements 7.2, 11.2**
    """
    # Particionar las clases por patrón. `sorted` para que la salida sea
    # determinista respecto del hash randomization de Python (los strings
    # generados por Hypothesis se ordenan antes de emitirse).
    items = sorted(classes_to_pattern.items())

    expected_html: set[str] = {cls for cls, p in items if p in (0, 1)}
    expected_js: set[str] = {cls for cls, p in items if p in (2, 3, 4)}

    # Construir el HTML: ruido inicial + un fragmento por cada clase HTML +
    # ruido final, todo dentro de un wrapper `<body>` para que el documento
    # tenga una estructura razonable (el extractor sólo mira los atributos
    # `class=`, así que la estructura no afecta a la corrección, pero sí a
    # la legibilidad si el test fallase).
    html_fragments: list[str] = ["<body>", _HTML_NOISE]
    for idx, (cls, p) in enumerate(items):
        if p in (0, 1):
            html_fragments.append(_emit_html_pattern(cls, p, idx))
    html_fragments.append(_HTML_NOISE)
    html_fragments.append("</body>")
    html_text = "\n".join(html_fragments)

    # Construir el JS: cabecera de ruido + una sentencia por cada clase JS +
    # ruido final. Envolver todo en un IIFE no es necesario para el extractor;
    # cada patrón se busca de forma independiente vía regex.
    js_fragments: list[str] = [_JS_NOISE]
    for idx, (cls, p) in enumerate(items):
        if p in (2, 3, 4):
            js_fragments.append(_emit_js_pattern(cls, p, idx))
    js_fragments.append(_JS_NOISE)
    js_text = "\n".join(js_fragments)

    actual_html = extract_html_classes(html_text)
    actual_js = extract_js_classes(js_text)

    assert actual_html == expected_html, (
        f"Round-trip HTML falló: esperaba {expected_html}, "
        f"recibí {actual_html}.\nHTML:\n{html_text}"
    )
    assert actual_js == expected_js, (
        f"Round-trip JS falló: esperaba {expected_js}, "
        f"recibí {actual_js}.\nJS:\n{js_text}"
    )


# ---------------------------------------------------------------------------
# Property 6 — Round-trip de extracción de Page_Stylesheets en plantillas
# ---------------------------------------------------------------------------


def _css_basename() -> SearchStrategy[str]:
    """Nombre de archivo CSS sin extensión: ``[a-z][a-z0-9_-]{0,15}``.

    Restringido a un subconjunto del alfabeto admitido por `_CSS_HREF_RE`
    (``[A-Za-z0-9_-]+``) para que el round-trip sea determinista y
    Hypothesis no malgaste shrinking en variantes equivalentes.
    """
    return st.from_regex(r"^[a-z][a-z0-9_-]{0,15}$", fullmatch=True)


def _synthesize_extra_css_template(
    *,
    css_files: list[str],
    include_vendor_link: bool,
    include_preload_noise: bool,
) -> str:
    """Construye una plantilla Jinja sintética que extiende ``base.html`` y
    rellena ``{% block extra_css %}`` con un ``<link rel="stylesheet">`` por
    cada nombre de archivo CSS.

    El parámetro `include_vendor_link` añade un link a ``vendor/leaflet.css``
    dentro del mismo bloque: `extract_page_css_links` debe ignorarlo porque
    su path no está bajo el subdirectorio ``css/`` (D6 — vendor queda fuera
    del audit). `include_preload_noise` añade un ``<link rel="preload">`` de
    fuente que tampoco debe contar (no es ``rel="stylesheet"``).
    """
    lines: list[str] = ['{% extends "base.html" %}']
    if include_preload_noise:
        # `rel="preload"` (no `stylesheet`) → ignorado por `_LINK_RE`.
        lines.append(
            '{% block head_extra %}'
            '<link rel="preload" as="font" type="font/woff2" '
            "href=\"{{ url_for('static', path='fonts/foo.woff2') }}\" "
            "crossorigin>"
            '{% endblock %}'
        )
    lines.append('{% block extra_css %}')
    if include_vendor_link:
        # `vendor/leaflet.css` no está bajo `css/` → ignorado por `_CSS_HREF_RE`.
        lines.append(
            '<link rel="stylesheet" '
            "href=\"{{ url_for('static', path='vendor/leaflet.css') }}\">"
        )
    for filename in css_files:
        lines.append(
            '<link rel="stylesheet" '
            "href=\"{{ url_for('static', path='css/" + filename + ".css') }}\">"
        )
    lines.append('{% endblock %}')
    return "\n".join(lines) + "\n"


# Feature: css-consolidation, Property 6: Round-trip de extracción de
# Page_Stylesheets en plantillas.
# Validates: Requirements 4.3, 4.5, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 8.9
@given(
    css_files=st.sets(_css_basename(), max_size=8),
    include_vendor_link=st.booleans(),
    include_preload_noise=st.booleans(),
)
@settings(max_examples=200)
def test_property_6_page_css_links_roundtrip(
    css_files: set[str],
    include_vendor_link: bool,
    include_preload_noise: bool,
) -> None:
    """`extract_page_css_links` round-trip sobre plantillas Jinja sintéticas.

    Para cualquier plantilla Jinja sintética que extiende ``base.html`` y
    rellena ``{% block extra_css %}`` con un conjunto controlado de
    ``<link rel="stylesheet" href="{{ url_for('static', path='css/X.css') }}">``,
    la función `extract_page_css_links(template_text)` SHALL devolver
    exactamente el conjunto de paths CSS inyectados, normalizados al
    subdirectorio ``static/css/``. El extractor SHALL ignorar links no
    relacionados (preload de fuentes — ``rel="preload"`` ≠ ``stylesheet``)
    y links que cargan CSS fuera del subdirectorio ``css/`` (``vendor/``).

    **Validates: Requirements 4.3, 4.5, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 8.9**
    """
    template = _synthesize_extra_css_template(
        css_files=sorted(css_files),
        include_vendor_link=include_vendor_link,
        include_preload_noise=include_preload_noise,
    )

    expected: set[Path] = {
        Path("static/css") / f"{name}.css" for name in css_files
    }
    actual: set[Path] = set(extract_page_css_links(template))

    assert actual == expected, (
        f"Round-trip falló: esperaba {expected}, recibí {actual}. "
        f"Plantilla:\n{template}"
    )


# Marcador para que pytest no se queje si en el futuro se añaden fixtures
# parametrizadas; por ahora sólo declara el módulo como parte de la suite.
pytest_plugins: tuple[str, ...] = ()