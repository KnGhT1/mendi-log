"""Descarga las fuentes y librerías de terceros para servirlas localmente.

Se ejecuta automáticamente desde run.sh / run.bat la primera vez.
Idempotente: solo descarga lo que falte. Se puede relanzar sin problema.

Tras esto, la web funciona 100% offline (excepto los tiles del mapa) y la
navegación entre páginas no recarga ninguna fuente externa.

NOTA sobre las URLs:
- Las fuentes vienen del paquete @fontsource publicado en npm y servido
  por jsDelivr. Las URLs son estables y versionadas, no rotan como los
  hashes de Google Fonts.
- HTMX y Leaflet vienen de unpkg, también versiones inmutables.
- Cada asset acepta una lista de URLs candidatas (fallback) — si la
  primera falla, prueba la siguiente. Así sobrevivimos a un mirror caído.
"""
from __future__ import annotations

import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FONTS_DIR = ROOT / "static" / "fonts"
VENDOR_DIR = ROOT / "static" / "vendor"

# ============================================================
# Tabla de assets — cada entrada es (lista_urls, destino)
# ============================================================

def _font_urls(family_pkg: str, family_file: str, weight: int) -> list[str]:
    """Genera URLs candidatas para una fuente de @fontsource.

    Prueba primero jsdelivr y luego unpkg como fallback.
    """
    base = f"@fontsource/{family_pkg}/files/{family_file}-latin-{weight}-normal.woff2"
    return [
        f"https://cdn.jsdelivr.net/npm/{base}",
        f"https://unpkg.com/{base}",
    ]


ASSETS: list[tuple[list[str], Path]] = [
    # ---- Fraunces (serif) ----
    (_font_urls("fraunces", "fraunces", 300), FONTS_DIR / "fraunces-300.woff2"),
    (_font_urls("fraunces", "fraunces", 400), FONTS_DIR / "fraunces-400.woff2"),
    (_font_urls("fraunces", "fraunces", 500), FONTS_DIR / "fraunces-500.woff2"),
    (_font_urls("fraunces", "fraunces", 600), FONTS_DIR / "fraunces-600.woff2"),
    # ---- IBM Plex Sans ----
    (_font_urls("ibm-plex-sans", "ibm-plex-sans", 300), FONTS_DIR / "ibm-plex-sans-300.woff2"),
    (_font_urls("ibm-plex-sans", "ibm-plex-sans", 400), FONTS_DIR / "ibm-plex-sans-400.woff2"),
    (_font_urls("ibm-plex-sans", "ibm-plex-sans", 500), FONTS_DIR / "ibm-plex-sans-500.woff2"),
    (_font_urls("ibm-plex-sans", "ibm-plex-sans", 600), FONTS_DIR / "ibm-plex-sans-600.woff2"),
    # ---- IBM Plex Mono ----
    (_font_urls("ibm-plex-mono", "ibm-plex-mono", 300), FONTS_DIR / "ibm-plex-mono-300.woff2"),
    (_font_urls("ibm-plex-mono", "ibm-plex-mono", 400), FONTS_DIR / "ibm-plex-mono-400.woff2"),
    (_font_urls("ibm-plex-mono", "ibm-plex-mono", 500), FONTS_DIR / "ibm-plex-mono-500.woff2"),
    # ---- HTMX ----
    (
        ["https://unpkg.com/htmx.org@1.9.12/dist/htmx.min.js"],
        VENDOR_DIR / "htmx.min.js",
    ),
    # ---- Leaflet ----
    (
        ["https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"],
        VENDOR_DIR / "leaflet.js",
    ),
    (
        # Sourcemap referenciado por leaflet.js — sin él el navegador
        # registra un 404 en consola al abrir DevTools.
        ["https://unpkg.com/leaflet@1.9.4/dist/leaflet.js.map"],
        VENDOR_DIR / "leaflet.js.map",
    ),
    (
        ["https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"],
        VENDOR_DIR / "leaflet.css",
    ),
    # ---- Leaflet.heat (mapa de calor de la página Análisis) ----
    (
        [
            "https://unpkg.com/leaflet.heat@0.2.0/dist/leaflet-heat.js",
            "https://cdn.jsdelivr.net/npm/leaflet.heat@0.2.0/dist/leaflet-heat.js",
        ],
        VENDOR_DIR / "leaflet-heat.js",
    ),
    # Iconos por defecto de Leaflet (los referencia el CSS con rutas relativas)
    (
        ["https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png"],
        VENDOR_DIR / "images" / "marker-icon.png",
    ),
    (
        ["https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png"],
        VENDOR_DIR / "images" / "marker-icon-2x.png",
    ),
    (
        ["https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png"],
        VENDOR_DIR / "images" / "marker-shadow.png",
    ),
    (
        ["https://unpkg.com/leaflet@1.9.4/dist/images/layers.png"],
        VENDOR_DIR / "images" / "layers.png",
    ),
    (
        ["https://unpkg.com/leaflet@1.9.4/dist/images/layers-2x.png"],
        VENDOR_DIR / "images" / "layers-2x.png",
    ),
]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _try_url(url: str, dest: Path) -> tuple[bool, str]:
    """Devuelve (ok, mensaje). Crea el destino solo si la descarga es válida."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
            data = r.read()
        if not data:
            return False, "respuesta vacía"
        dest.write_bytes(data)
        return True, ""
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except urllib.error.URLError as e:
        return False, f"URL error: {e.reason}"
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def fetch(urls: list[str], dest: Path) -> tuple[bool, str]:
    """Intenta cada URL hasta que una funcione. Devuelve (descargado, error)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        return False, ""  # ya existe, no se ha descargado nada
    last_err = ""
    for url in urls:
        ok, err = _try_url(url, dest)
        if ok:
            return True, ""
        last_err = err
    return False, last_err


def main() -> int:
    print("[assets] verificando fuentes y librerías locales...")
    downloaded = 0
    skipped = 0
    failed: list[str] = []

    for urls, dest in ASSETS:
        ok, err = fetch(urls, dest)
        if ok:
            downloaded += 1
            rel = dest.relative_to(ROOT)
            size_kb = dest.stat().st_size / 1024
            print(f"  + {rel}  ({size_kb:.1f} KB)")
        elif err:
            failed.append(f"{dest.name}: {err}")
        else:
            skipped += 1

    if failed:
        print("\n[assets] avisos:")
        for line in failed:
            print(f"  ! {line}")
        print(
            "\n  La app seguirá funcionando, pero algunas fuentes pueden caer\n"
            "  al sistema. Si tienes conexión, vuelve a lanzar el arranque\n"
            "  para reintentar las descargas que han fallado."
        )

    if downloaded == 0 and not failed:
        print(f"[assets] todo en orden ({skipped} archivos ya cacheados)")
    elif downloaded:
        print(f"[assets] descargados {downloaded} archivos · {skipped} ya cacheados")

    return 0


if __name__ == "__main__":
    sys.exit(main())
