import pathlib
ROOT = pathlib.Path(__file__).resolve().parent.parent
lines = (ROOT / 'static/js/analisis.js').read_text(encoding='utf-8').splitlines()
for i, l in enumerate(lines, 1):
    if any(t in l for t in ['renderCalendar', 'setupCombo', 'renderWeekday', 'renderScatter', 'autoSelect']):
        print(f'{i:4}: {repr(l)}')
