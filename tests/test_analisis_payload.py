"""Tests del payload JSON de Análisis para incrustar en <script> (H1).

El template `analisis.html` usa `{{ payload_json|safe }}` dentro de un
`<script type="application/json">`. `json.dumps` no escapa `<`, así que un
nombre de ruta tipo `</script>` rompería el bloque. `payload_json_for_script`
debe escapar `&<>` a secuencias `\\uXXXX` sin alterar el valor tras `json.loads`.
"""
from __future__ import annotations

import json

from app.analisis import (
    AnalisisData,
    ScatterDot,
    StreakInfo,
    payload_json_for_script,
    to_json_payload,
)


def test_payload_con_script_no_rompe_bloque_y_conserva_valor():
    evil = '</script><script>alert(1)</script> & <b>negrita</b>'
    payload = {"scatter": [{"name": evil, "km": 12.5}], "zones": []}

    out = payload_json_for_script(payload)

    assert "</script>" not in out
    assert "<" not in out
    assert ">" not in out
    assert "&" not in out.replace("\\u0026", "")
    # Round-trip intacto: el lector (textContent + JSON.parse) ve lo mismo.
    assert json.loads(out) == payload


def test_payload_sin_html_no_cambia():
    payload = {"totalSessions": 3, "rangeKey": "all", "kmByDay": []}
    assert json.loads(payload_json_for_script(payload)) == payload
    assert payload_json_for_script(payload) == json.dumps(payload, ensure_ascii=False)


def _analisis_minimo(nombre_scatter: str) -> AnalisisData:
    """AnalisisData mínimo válido para serializar sin BD."""
    return AnalisisData(
        has_data=True, range_key="all", from_date=None, to_date=None,
        range_label="Todo", range_chips=[], hero_stats=[], heat_points=[],
        map_center=(43.0, -1.5), zones=[], ratio=[],
        streak=StreakInfo(current=0, best=0, last4_avg_km=0.0, spark=[]),
        discovery=[], top_routes=[], monthly=[], monthly_max=0,
        monthly_prev_year={}, donut_difficulty=[], donut_difficulty_total=0,
        donut_distance=[], donut_distance_total=0,
        scatter=[ScatterDot(name=nombre_scatter, km=12.5, gain=800,
                            score=5.0, level="moderate")],
        scatter_max_km=12.5, scatter_max_gain=800, records=[], calendar=[],
        calendar_mini=[], km_by_day=[], km_by_weekday=[], km_by_month_hist=[],
        total_sessions=1, total_unique_routes=1,
        top_has_repeated=False,
    )


def test_to_json_payload_real_con_nombre_adversarial():
    """Regresión: to_json_payload debe devolver el dict (no None) y el
    escape posterior debe neutralizar `</script>` con round-trip intacto."""
    evil = "</script><script>alert(1)</script>"
    payload = to_json_payload(_analisis_minimo(evil))

    assert isinstance(payload, dict)
    assert payload["scatter"][0]["name"] == evil
    assert payload["totalSessions"] == 1

    out = payload_json_for_script(payload)
    assert "</script>" not in out
    assert json.loads(out) == payload
