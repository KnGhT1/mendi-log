import pathlib
ROOT = pathlib.Path(__file__).resolve().parent.parent
lines = (ROOT / 'static/js/analisis.js').read_text(encoding='utf-8').splitlines()
for i, l in enumerate(lines[1150:1172], 1151):
    print(f'{i:4}: {repr(l)}')
