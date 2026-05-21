from app.db import SessionLocal
from app.analisis import build_analisis
from app.models import User

db = SessionLocal()
try:
    user = db.query(User).first()
    data = build_analisis(db, user.id)
    print("has_data:", data.has_data)
    print("calendar years:", len(data.calendar))
    for cy in data.calendar:
        print(f"  year={cy.year}, months={len(cy.months)}")
        for m in cy.months[:2]:
            print(f"    month={m.label}, weeks={len(m.weeks)}")
            for w in m.weeks[:2]:
                print(f"      week cells={len(w)}, sample={w[:3]}")
    print()
    print("km_by_day sample:", data.km_by_day[:3])
finally:
    db.close()
