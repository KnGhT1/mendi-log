import pathlib

checks = {
    "templates/resumen.html": [
        "donut", "calendar", "kmByDay", "kmByWeekday", "kmByMonthHist",
        "chart-monthly", "chart-weekday", "chart-seasonality", "resumen-calendar",
    ],
    "static/js/app.js": [
        "donut", "renderResumenCalendar", "kmByDay", "kmByWeekday", "kmByMonthHist",
        "renderWeekdayChart", "renderSeasonalityChart",
    ],
    "app/stats.py": [
        "km_by_day", "km_by_weekday", "km_by_month_hist",
        "CalendarYear", "DonutSlice", "donut",
    ],
}

ROOT = pathlib.Path(__file__).resolve().parent.parent
all_ok = True
for filepath, terms in checks.items():
    content = (ROOT / filepath).read_text(encoding="utf-8")
    hits = [t for t in terms if t in content]
    if hits:
        print(f"RESTOS  {filepath}: {hits}")
        all_ok = False
    else:
        print(f"OK      {filepath}")

if all_ok:
    print("\nTodo limpio.")
