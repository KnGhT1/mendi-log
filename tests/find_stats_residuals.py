import pathlib
ROOT = pathlib.Path(__file__).resolve().parent.parent
lines = (ROOT / "app/stats.py").read_text(encoding="utf-8").splitlines()
terms = ["DonutSlice", "CalendarYear", "donut", "calendar", "MAP markers", "Donut", "Calendario"]
for i, l in enumerate(lines, 1):
    if any(t in l for t in terms):
        print(f"{i:4}: {l}")
