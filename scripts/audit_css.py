"""Audit del CSS_System de mendi.log.

Script standalone (sólo stdlib) que verifica las propiedades de corrección
del refactor de consolidación CSS descritas en `.kiro/specs/css-consolidation`:

- Round-trip de tokens declarados/usados (R7.1, R11.1).
- Cobertura de clases declaradas vs. activas en HTML/JS (R7.2, R11.2 / R11.3).
- Detección de selectores duplicados dentro del mismo `@media` (R6.2, R11.4).
- Mapping plantilla → Page_Stylesheets cargados (R4.3 / R8.x).
- Soporte de aliases temporales durante el refactor incremental (R9.3 / R9.4).

La interfaz pública (dataclasses + funciones puras + capa fina de I/O + CLI)
es la fijada en la sección C4 del design. Esta tarea (1.1) deja sólo el
esqueleto: las dataclasses están completas y los extractores son stubs que
levantan `NotImplementedError`. La implementación real llega en las tareas
2.x.

Restricciones (R7.1, R10.3): este módulo SÓLO usa la stdlib (`re`,
`pathlib`, `dataclasses`, `argparse`, `sys`, `typing`). Hypothesis se
permite pero únicamente en `tests/`, vía `requirements-dev.txt`.

Uso (CLI):

    python scripts/audit_css.py [--css-dir PATH] [--templates-dir PATH] \
        [--js-dir PATH] [--allow-aliases]

Códigos de salida:
    0  → audit limpio (`AuditReport.is_clean is True`).
    1  → se detectaron problemas (tokens/clases huérfanos, duplicados, ...).
    2  → error léxico irrecuperable (EH1 del design).
"""

import argparse
import re  # noqa: F401  -- usado por los extractores en tareas 2.x
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence


# ---------------------------------------------------------------------------
# Regex compartidos por los extractores (sub-tareas 2.x)
# ---------------------------------------------------------------------------

# Comentario CSS multilínea ``/* ... */``. No-greedy y con DOTALL para que
# atrape comentarios que contengan saltos de línea.
_CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)

# Nombre de clase dentro de un selector CSS. Captura el nombre sin el ``.``
# inicial. Aceptamos guiones, dígitos y la convención BEM ``__`` / ``--``.
_CLASS_NAME_RE = re.compile(r"\.([a-zA-Z_][\w-]*)")

# Etiqueta ``<link ... rel="stylesheet" ... href="..." ...>`` (o con `rel` y
# `href` en orden inverso). Captura sólo la cadena del atributo `href`.
# Tolera atributos extra entre ``rel`` y ``href``, comillas simples o dobles
# en los atributos, y comillas del *otro* tipo *dentro* del valor (caso
# típico: ``href="{{ url_for('static', path='css/foo.css') }}"`` con
# comillas simples en el path Jinja).
_LINK_RE = re.compile(
    r"""<link\b[^>]*?
        (?:
            rel\s*=\s*(?P<relq>['"])stylesheet(?P=relq)
            [^>]*?
            href\s*=\s*(?P<hq1>['"])(?P<href>.*?)(?P=hq1)
          |
            href\s*=\s*(?P<hq2>['"])(?P<href2>.*?)(?P=hq2)
            [^>]*?
            rel\s*=\s*(?P<relq2>['"])stylesheet(?P=relq2)
        )
        [^>]*>
    """,
    re.IGNORECASE | re.VERBOSE | re.DOTALL,
)


def _link_href(match: re.Match[str]) -> str:
    """Devuelve el grupo `href` que haya disparado, sea cual sea el orden."""
    return match.group("href") or match.group("href2")


# Dentro del valor de `href`, el path puede llegar como literal
# (``css/foo.css``, ``/static/css/foo.css``) o vía Jinja
# (``{{ url_for('static', path='css/foo.css') }}``). En todos los casos lo
# que nos interesa es el nombre de archivo bajo el subdirectorio ``css/``.
_CSS_HREF_RE = re.compile(
    r"""(?:^|[/'"])css/(?P<file>[A-Za-z0-9_-]+)\.css\b""",
    re.IGNORECASE,
)
# Nota: la lookbehind incluye `'` y `"` además de `^` y `/` porque los
# templates Jinja canónicos escriben el path entre comillas simples
# (`url_for('static', path='css/X.css')`), de modo que el carácter
# inmediatamente anterior a `css/` es la propia comilla del literal.


# ---------------------------------------------------------------------------
# Dataclasses (sección C4 del design)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DuplicateRecord:
    """Una repetición de un selector dentro del mismo media query.

    `media_query` es la cadena vacía (``""``) cuando la regla está en el
    scope top-level, no anidada bajo un `@media`. `lines` recoge los
    números de línea (1-indexados) donde aparece cada apertura de bloque
    `selector { ... }` que constituye la duplicación.
    """

    selector: str
    media_query: str
    file: Path
    lines: tuple[int, ...]


@dataclass(frozen=True)
class CssParseResult:
    """Resultado del parseo de un único archivo CSS.

    `duplicates_in_mq` reporta `K - 1` registros por cada selector que
    aparece `K` veces dentro del mismo media query (R6.2 / R11.4).
    """

    declared_tokens: set[str]
    used_tokens: set[str]
    declared_classes: set[str]
    duplicates_in_mq: list[DuplicateRecord]


@dataclass(frozen=True)
class ActiveClasses:
    """Conjunto de clases efectivamente referenciadas desde HTML y JS.

    `sources` mapea cada nombre de clase al conjunto de archivos
    (`templates/*.html`, `static/js/*.js`) donde aparece, para que los
    mensajes de error puedan apuntar al lugar exacto.
    """

    classes: set[str]
    sources: dict[str, list[Path]]


@dataclass(frozen=True)
class PageCssLink:
    """Plantilla y los Page_Stylesheets que carga (R4.3, R8.x)."""

    template: Path
    css_files: tuple[Path, ...]


@dataclass(frozen=True)
class AuditReport:
    """Informe agregado del audit (R7.3, R11.x).

    Las propiedades `tokens_declared_unused`, `tokens_used_undeclared`,
    `classes_declared_unused`, `classes_used_undeclared` e `is_clean`
    derivan los problemas detectados a partir de los conjuntos crudos.

    `aliases_temporales` contiene las clases declaradas con el comentario
    inmediato anterior ``/* alias temporal — eliminar tras migrar
    plantilla X */`` (C6 del design). El audit las trata según la flag
    ``--allow-aliases`` del CLI: con la flag, no fallan; sin la flag, son
    parte de las clases declaradas no usadas (R9.3 / R9.4).
    """

    declared_tokens: set[str]
    used_tokens: set[str]
    declared_classes: set[str]
    active_classes: set[str]
    duplicates_in_mq: list[DuplicateRecord]
    page_css_links: list[PageCssLink]
    aliases_temporales: set[str]

    @property
    def tokens_declared_unused(self) -> set[str]:
        """Tokens declarados que ningún `var(--*)` referencia."""
        ...

    @property
    def tokens_used_undeclared(self) -> set[str]:
        """Tokens referenciados con `var(--*)` que nunca se declararon."""
        ...

    @property
    def classes_declared_unused(self) -> set[str]:
        """Clases declaradas en CSS que no aparecen ni en HTML ni en JS.

        Excluye las clases en `aliases_temporales` cuando el audit se ha
        invocado con `allow_aliases=True` (la propia función `audit()` se
        encarga de poblar el campo de modo coherente con la flag).
        """
        ...

    @property
    def classes_used_undeclared(self) -> set[str]:
        """Clases usadas en HTML o JS para las que no existe regla CSS."""
        ...

    @property
    def is_clean(self) -> bool:
        """True si y sólo si no hay problemas de ningún tipo (R7.4)."""
        ...


# ---------------------------------------------------------------------------
# Extractores puros (sin I/O) — implementación en tareas 2.1, 2.3, 2.5, 2.7,
# 2.9. Se exponen como stubs para que el wiring del módulo sea importable
# desde `tests/test_css_audit.py` (sub-tarea 1.2).
# ---------------------------------------------------------------------------


_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)

# `--name:` en cualquier punto del texto. Tras eliminar comentarios y dado
# que `var(--name)` nunca va seguido de `:` (sólo de `,` o `)`), basta con
# pedir el nombre seguido (con espacios opcionales) por `:` para distinguir
# una declaración de una referencia. Funciona dentro de `:root`,
# `html[data-theme="*"]`, cualquier otro selector o anidado bajo `@media`.
_DECLARED_TOKEN_RE = re.compile(r"(--[A-Za-z_][A-Za-z0-9_-]*)\s*:")

# `var(--name)` o `var(--name, fallback)`. `\s*` tolera espaciado tras `(`.
# La parte del fallback se ignora deliberadamente: sólo nos interesa el
# nombre del token referenciado.
_USED_TOKEN_RE = re.compile(r"var\(\s*(--[A-Za-z_][A-Za-z0-9_-]*)")


def _strip_css_comments(css_text: str) -> str:
    """Elimina comentarios `/* ... */` (incluidos los multilinea)."""
    return _COMMENT_RE.sub("", css_text)


def extract_declared_tokens(css_text: str) -> set[str]:
    """Tokens declarados (`--name: value;`) en cualquier selector.

    Detecta declaraciones en `:root`, `html[data-theme="*"]` y cualquier
    otro selector, incluso anidadas bajo `@media`. Tolerante a comentarios
    `/* ... */` y espaciado variable. Función pura (R7.1, sin I/O).
    """
    return set(_DECLARED_TOKEN_RE.findall(_strip_css_comments(css_text)))


def extract_used_tokens(css_text: str) -> set[str]:
    """Tokens referenciados con `var(--name[, fallback])` en cualquier valor.

    Detecta `var(--name)` y `var(--name, <fallback>)` en cualquier valor de
    cualquier propiedad, también dentro de `@media`. Tolerante a comentarios
    y espaciado. Función pura (R7.1, sin I/O).
    """
    return set(_USED_TOKEN_RE.findall(_strip_css_comments(css_text)))


def extract_declared_classes(css_text: str) -> set[str]:
    """Clases declaradas en CSS (top-level o dentro de `@media`).

    Se implementa en la sub-tarea 2.3.
    """
    # 1. Eliminar comentarios CSS para no confundir su contenido con
    #    selectores ni con cuerpos de bloques.
    css = re.sub(r"/\*.*?\*/", "", css_text, flags=re.DOTALL)

    # At-rules cuyo cuerpo NO contiene reglas CSS y por tanto no debe
    # explorarse en busca de selectores. Para `@media` / `@supports`
    # descendemos al cuerpo (sus reglas internas SÍ son selectores).
    skip_at_rules = (
        "@font-face",
        "@keyframes",
        "@-webkit-keyframes",
        "@-moz-keyframes",
        "@-o-keyframes",
        "@counter-style",
        "@font-feature-values",
        "@property",
    )
    selector_class_re = re.compile(r"\.([a-zA-Z_][\w-]*)")
    at_rule_name_re = re.compile(r"^@[\w-]+")

    classes: set[str] = set()
    n = len(css)
    i = 0
    depth = 0
    skip_until_depth = -1  # mientras `>= 0`, ignoramos selectores hasta
    # que `depth` vuelva a este valor (descenso en at-rule opaca).
    last_break = 0  # índice tras el último `{`, `}` o `;` (selector start).
    in_string: str | None = None  # `"`, `'` o None — neutraliza llaves
    # y separadores que aparezcan dentro de strings CSS.

    while i < n:
        ch = css[i]

        if in_string is not None:
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == in_string:
                in_string = None
            i += 1
            continue
        if ch == '"' or ch == "'":
            in_string = ch
            i += 1
            continue

        if ch == "{":
            if skip_until_depth < 0:
                text = css[last_break:i].strip()
                at_match = at_rule_name_re.match(text)
                if at_match is not None:
                    if at_match.group() in skip_at_rules:
                        skip_until_depth = depth
                    # Para @media / @supports / @document / etc.
                    # descendemos sin extraer (la cabecera no es un
                    # selector y sus paréntesis no contienen clases).
                else:
                    # Selector list: separar por `,` y tokenizar las
                    # clases (`.a.b`, `.a .b`, `tag.a`, `.a:hover`,
                    # `.a::before`, `.a > .b` quedan todas cubiertas
                    # por `\.([a-zA-Z_][\w-]*)`).
                    for selector in text.split(","):
                        for m in selector_class_re.finditer(selector):
                            classes.add(m.group(1))
            depth += 1
            last_break = i + 1
            i += 1
            continue

        if ch == "}":
            depth -= 1
            if skip_until_depth >= 0 and depth == skip_until_depth:
                skip_until_depth = -1
            last_break = i + 1
            i += 1
            continue

        if ch == ";":
            # `;` cierra at-rules sin cuerpo (`@import`, `@charset`,
            # `@namespace`) y declaraciones — en ningún caso forma
            # parte de un selector list, así que descartamos lo
            # acumulado.
            last_break = i + 1
            i += 1
            continue

        i += 1

    return classes


def _strip_css_comments_preserving_lines(text: str) -> str:
    """Sustituye los comentarios `/* ... */` por espacios respetando saltos
    de línea, de modo que los números de línea se mantengan estables tras la
    eliminación.
    """
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if i + 1 < n and text[i] == "/" and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            end = n if j == -1 else j + 2
            for c in text[i:end]:
                out.append("\n" if c == "\n" else " ")
            i = end
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def extract_duplicates_in_mq(
    css_text: str, file: Path
) -> list[DuplicateRecord]:
    """Selectores que se repiten dentro del mismo media query (R6.2 / R11.4).

    Devuelve `K - 1` registros `DuplicateRecord` por cada `(selector, mq)`
    que aparece `K ≥ 2` veces dentro del mismo scope, con `lines` apuntando
    a las `K` líneas de las llaves de apertura. El scope top-level se
    representa como `media_query == ""`. `@supports` abre su propio scope
    separado (no se mezcla con el top-level); `@font-face`, `@keyframes` y
    cualquier otro at-rule de bloque se ignoran (su contenido no contiene
    selectores de regla en sentido habitual).
    """
    text = _strip_css_comments_preserving_lines(css_text)
    n = len(text)

    occurrences: dict[tuple[str, str], list[int]] = {}
    # Pila de scopes; el tope es el `media_query` activo. Top-level = "".
    scope_stack: list[str] = [""]

    def line_of(idx: int) -> int:
        return text.count("\n", 0, idx) + 1

    def skip_string(start: int) -> int:
        """Avanza tras una cadena `"..."` o `'...'`. Devuelve el índice
        siguiente al delimitador de cierre."""
        quote = text[start]
        i = start + 1
        while i < n:
            c = text[i]
            if c == "\\" and i + 1 < n:
                i += 2
                continue
            if c == quote:
                return i + 1
            i += 1
        return n

    def skip_balanced_block(start: int) -> int:
        """Asume `text[start] == '{'`. Devuelve el índice siguiente al `}`
        equilibrado, ignorando llaves dentro de cadenas."""
        depth = 1
        i = start + 1
        while i < n and depth > 0:
            c = text[i]
            if c == '"' or c == "'":
                i = skip_string(i)
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            i += 1
        return i

    pos = 0
    while pos < n:
        # Saltar whitespace.
        while pos < n and text[pos] in " \t\n\r\f":
            pos += 1
        if pos >= n:
            break

        c = text[pos]

        if c == "}":
            if len(scope_stack) > 1:
                scope_stack.pop()
            pos += 1
            continue

        if c == ";":
            pos += 1
            continue

        if c == "@":
            # At-rule: leer el nombre.
            name_end = pos + 1
            while name_end < n and (
                text[name_end].isalnum() or text[name_end] == "-"
            ):
                name_end += 1
            atname = text[pos + 1 : name_end].lower()

            # Avanzar hasta `{` o `;` a profundidad 0 (respetando paréntesis
            # y cadenas — los corchetes en preludios de @media son raros pero
            # los paréntesis sí aparecen en `(max-width: …)`).
            i = name_end
            paren_depth = 0
            while i < n:
                ch = text[i]
                if ch == '"' or ch == "'":
                    i = skip_string(i)
                    continue
                if ch == "(":
                    paren_depth += 1
                elif ch == ")":
                    paren_depth -= 1
                elif paren_depth == 0 and (ch == "{" or ch == ";"):
                    break
                i += 1

            if i >= n:
                break

            if text[i] == ";":
                # @import, @charset, @namespace, …
                pos = i + 1
                continue

            # text[i] == '{'  → at-rule con bloque.
            prelude = " ".join(text[name_end:i].split())

            if atname == "media":
                scope_stack.append(prelude)
                pos = i + 1
            elif atname == "supports":
                # `@supports` abre su propio scope independiente del
                # top-level, etiquetado para que no colisione con un media
                # query vacío ni con otros scopes.
                scope_stack.append(f"@supports {prelude}".rstrip())
                pos = i + 1
            else:
                # @font-face, @keyframes (y vendor-prefixed), @page, @property,
                # @counter-style, @container, etc. — ignorar el bloque entero.
                pos = skip_balanced_block(i)
            continue

        # Caso general: regla `selector { ... }`. Acumular hasta `{`,
        # respetando `(...)`, `[...]` y cadenas.
        sel_start = pos
        i = pos
        paren_depth = 0
        bracket_depth = 0
        while i < n:
            ch = text[i]
            if ch == '"' or ch == "'":
                i = skip_string(i)
                continue
            if ch == "(":
                paren_depth += 1
                i += 1
                continue
            if ch == ")":
                paren_depth -= 1
                i += 1
                continue
            if ch == "[":
                bracket_depth += 1
                i += 1
                continue
            if ch == "]":
                bracket_depth -= 1
                i += 1
                continue
            if paren_depth == 0 and bracket_depth == 0 and ch in "{};@":
                break
            i += 1

        if i >= n:
            break

        if text[i] != "{":
            # Hemos llegado a `}`, `;` o `@` sin haber abierto bloque: no es
            # una regla. Dejar que el bucle externo maneje el carácter.
            if text[i] == ";":
                pos = i + 1
            else:
                pos = i
            continue

        brace_pos = i
        # Normalizar el selector: trim + colapsar espacios internos.
        normalized = " ".join(text[sel_start:brace_pos].split())
        if normalized:
            key = (normalized, scope_stack[-1])
            occurrences.setdefault(key, []).append(line_of(brace_pos))

        pos = skip_balanced_block(brace_pos)

    records: list[DuplicateRecord] = []
    for (selector, media_query), lines in occurrences.items():
        if len(lines) >= 2:
            line_tuple = tuple(lines)
            for _ in range(len(lines) - 1):
                records.append(
                    DuplicateRecord(
                        selector=selector,
                        media_query=media_query,
                        file=file,
                        lines=line_tuple,
                    )
                )
    return records


# Atributo `class="..."` o `class='...'` (con espaciado tolerado alrededor del
# `=`). Captura el valor del atributo en uno de los dos grupos según el quote.
_HTML_CLASS_ATTR_RE = re.compile(
    r"""class\s*=\s*(?:"([^"]*)"|'([^']*)')""",
    re.IGNORECASE,
)

# Expresiones Jinja que pueden aparecer interpoladas dentro del valor de
# `class="..."`. Las tratamos como agujeros dinámicos: cualquier token que las
# toque sin espacio en medio (p.ej. `role-{{ x }}` → fragmento `role-`) NO es
# un nombre de clase literal en el código fuente y debe descartarse.
_HTML_JINJA_RE = re.compile(
    r"""\{\{.*?\}\}|\{%.*?%\}""",
    re.DOTALL,
)

# Marcador interno (un carácter que no puede aparecer en un nombre de clase
# CSS válido) que sustituye a las expresiones Jinja antes de tokenizar el
# atributo. Tras `split()`, descartamos cualquier token que lo contenga.
_HTML_JINJA_SENTINEL = "\x00"


def extract_html_classes(html_text: str) -> set[str]:
    """Clases referenciadas en plantillas vía `class="..."` / `class='...'`.

    Soporta plantillas Jinja con interpolaciones `{{ ... }}` y bloques
    `{% ... %}` mezclados dentro del atributo: la expresión Jinja se elimina
    antes de tokenizar, pero los tokens que la tocan sin espacio (fragmentos
    dinámicos como `role-{{ x }}`) NO se cuentan como clases literales —
    sólo se devuelven las clases escritas íntegramente en el código fuente.

    El atributo se tokeniza por whitespace tras la sustitución de Jinja,
    siguiendo el contrato HTML del propio atributo `class` (R7.2 / R11.2).
    """
    classes: set[str] = set()
    for match in _HTML_CLASS_ATTR_RE.finditer(html_text):
        # Sólo uno de los dos grupos captura, según el quote usado.
        value = match.group(1) if match.group(1) is not None else match.group(2)
        if not value:
            continue
        marked = _HTML_JINJA_RE.sub(_HTML_JINJA_SENTINEL, value)
        for token in marked.split():
            if token and _HTML_JINJA_SENTINEL not in token:
                classes.add(token)
    return classes


# `classList.add(...)` y `classList.remove(...)`: pueden recibir múltiples
# argumentos string (`classList.add("a", "b")`), todos cuentan como clases.
# El cuerpo se captura como texto plano hasta el primer `)` y se le aplica
# `_JS_STRING_LITERAL_RE` después para extraer cada literal.
_JS_CLASSLIST_ADD_REMOVE_RE = re.compile(
    r"""classList\s*\.\s*(?:add|remove)\s*\(([^)]*)\)""",
    re.DOTALL,
)

# `classList.toggle(...)`: el segundo argumento es un *force* booleano (puede
# ser una expresión que a su vez contenga literales de cadena, p.ej.
# `view === "list"`), así que SÓLO se extrae el primer argumento si éste es
# un literal de cadena. Si el primer argumento es dinámico (`toggle(name)`,
# `toggle(cond ? "a" : "b")`), la regex no matchea y no se cuenta nada.
_JS_CLASSLIST_TOGGLE_RE = re.compile(
    r"""classList\s*\.\s*toggle\s*\(\s*"""
    r"""(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)')\s*"""
    r"""(?:,[^)]*)?\)""",
    re.DOTALL,
)

# Literal de cadena JS, tanto con comillas dobles como simples, soportando
# secuencias de escape estándar dentro del propio literal.
_JS_STRING_LITERAL_RE = re.compile(
    r""""((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)'""",
    re.DOTALL,
)

# `setAttribute("class", "...")` o `setAttribute('class', '...')`. El primer
# argumento debe ser literalmente la cadena `class` (cualquier otra clave de
# atributo se ignora). Tolerante a whitespace y mezclando los quoting.
_JS_SET_ATTR_CLASS_RE = re.compile(
    r"""setAttribute\s*\(\s*(?:"class"|'class')\s*,\s*"""
    r"""(?:"([^"]*)"|'([^']*)')\s*\)""",
    re.DOTALL,
)


def extract_js_classes(js_text: str) -> set[str]:
    """Clases referenciadas dinámicamente en JS (D7 del design).

    Cubre los patrones detectados hoy en `static/js/{app,rutas,analisis,
    detail,importar,admin_usuarios}.js`:

    - `classList.add("name"[, "name2", ...])`
    - `classList.remove("name"[, "name2", ...])`
    - `classList.toggle("name")` y `classList.toggle("name", cond)`
    - `setAttribute("class", "name1 name2 ...")` (valor splitteado por
      whitespace)

    Sólo se cuentan literales de cadena: argumentos dinámicos (variables,
    expresiones) se descartan porque no son determinables estáticamente.
    """
    classes: set[str] = set()

    # classList.add / classList.remove (múltiples argumentos string aceptados)
    for call_match in _JS_CLASSLIST_ADD_REMOVE_RE.finditer(js_text):
        args = call_match.group(1)
        for str_match in _JS_STRING_LITERAL_RE.finditer(args):
            literal = (
                str_match.group(1)
                if str_match.group(1) is not None
                else str_match.group(2)
            )
            if literal:
                classes.add(literal)

    # classList.toggle (sólo el primer argumento si es literal de cadena)
    for tog_match in _JS_CLASSLIST_TOGGLE_RE.finditer(js_text):
        literal = (
            tog_match.group(1)
            if tog_match.group(1) is not None
            else tog_match.group(2)
        )
        if literal:
            classes.add(literal)

    # setAttribute("class", "...")
    for set_match in _JS_SET_ATTR_CLASS_RE.finditer(js_text):
        value = (
            set_match.group(1)
            if set_match.group(1) is not None
            else set_match.group(2)
        )
        if not value:
            continue
        for token in value.split():
            if token:
                classes.add(token)

    return classes


def extract_aliases_temporales(css_text: str) -> set[str]:
    """Clases marcadas con `/* alias temporal — ... */` (C6, R9.3 / R9.4).

    Detecta comentarios CSS que contengan la frase ``alias temporal`` (sin
    importar si el separador es ``—`` o ``--``) y devuelve los nombres de
    clase del *selector inmediatamente posterior* al comentario. "Inmediato
    anterior" significa que entre el comentario y la regla sólo se admiten
    espacios en blanco y otros comentarios — cualquier otra regla rompe la
    asociación.
    """
    aliases: set[str] = set()
    n = len(css_text)
    for comment_match in _CSS_COMMENT_RE.finditer(css_text):
        if "alias temporal" not in comment_match.group(0).lower():
            continue
        # Saltar whitespace y comentarios subsiguientes hasta llegar al
        # selector de la regla destinataria del alias.
        i = comment_match.end()
        while i < n:
            ch = css_text[i]
            if ch.isspace():
                i += 1
                continue
            if css_text[i : i + 2] == "/*":
                end_comment = css_text.find("*/", i + 2)
                if end_comment < 0:
                    i = n
                    break
                i = end_comment + 2
                continue
            break
        if i >= n:
            continue
        brace_idx = css_text.find("{", i)
        if brace_idx < 0:
            continue
        selector = css_text[i:brace_idx]
        for cls_match in _CLASS_NAME_RE.finditer(selector):
            aliases.add(cls_match.group(1))
    return aliases


def extract_page_css_links(html_text: str) -> tuple[Path, ...]:
    """Page_Stylesheets cargados por una plantilla (R4.3, R8.x).

    Detecta `<link rel="stylesheet" href="{{ url_for('static', path='css/X.css') }}">`
    dentro del bloque `{% block extra_css %}` o, en `login.html`, en el `<head>`
    directo. Devuelve los `Path` normalizados al subdirectorio
    ``static/css/``, en el orden de aparición y sin deduplicar (el llamador
    decide si quiere conservar duplicados).
    """
    links: list[Path] = []
    for match in _LINK_RE.finditer(html_text):
        href = _link_href(match)
        if not href:
            continue
        css_match = _CSS_HREF_RE.search(href)
        if css_match is None:
            continue
        filename = css_match.group("file")
        links.append(Path("static/css") / f"{filename}.css")
    return tuple(links)


# ---------------------------------------------------------------------------
# Capa fina de I/O y CLI — implementación en tareas 2.12 y 2.14.
# ---------------------------------------------------------------------------


def audit(
    css_dir: Path,
    templates_dir: Path,
    js_dir: Path,
    *,
    allow_aliases: bool = False,
) -> AuditReport:
    """Ejecuta el audit completo y devuelve un `AuditReport`.

    Compone los extractores puros leyendo los archivos de los tres
    directorios, excluyendo `static/vendor/` (D6). Se implementa en la
    sub-tarea 2.12.
    """
    raise NotImplementedError("audit — sub-tarea 2.12")


def main(argv: Sequence[str] | None = None) -> int:
    """Entrada CLI del audit.

    Argumentos: `--css-dir`, `--templates-dir`, `--js-dir`, `--allow-aliases`.
    Imprime el informe en el orden de R7.3 y devuelve `0` si `is_clean`,
    `1` si no, `2` ante error léxico irrecuperable (EH1). Se implementa en
    la sub-tarea 2.14.
    """
    raise NotImplementedError("main — sub-tarea 2.14")


# Mantiene `argparse` y `field` aludidos por los stubs / dataclasses futuras
# para que linters no marquen los imports como no usados durante el periodo
# de skeleton (las tareas 2.x los emplearán).
_ = (argparse, field)


if __name__ == "__main__":  # pragma: no cover — entrada CLI real en 2.14
    sys.exit(main(sys.argv[1:]))
