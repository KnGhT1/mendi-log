from app.db import SessionLocal
from app.analisis import build_analisis, to_json_payload
from app.models import User

db = SessionLocal()
try:
    user = db.query(User).first()
    if not user:
        print("NO USER")
    else:
        data = build_analisis(db, user.id)
        print("has_data:", data.has_data)
        print("km_by_weekday:", data.km_by_weekday)
        print("km_by_month_hist:", data.km_by_month_hist)
        payload = to_json_payload(data)
        print("payload kmByWeekday:", payload.get("kmByWeekday"))
        print("payload kmByMonthHist:", payload.get("kmByMonthHist"))
finally:
    db.close()
