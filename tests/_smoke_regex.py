from scripts.audit_css import _CSS_HREF_RE

cases = [
    "/static/css/x.css",
    "static/css/x.css",
    "{{ url_for('static', path='css/login.css') }}",
    '"css/foo.css"',
    "vendor/leaflet.css",
]
for c in cases:
    print(repr(c), "->", [m.group("file") for m in _CSS_HREF_RE.finditer(c)])
