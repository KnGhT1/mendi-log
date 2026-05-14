"""Simulacion del clustering union-find por proximidad antes de aplicarlo."""
import sys, math
sys.stdout.reconfigure(encoding="utf-8")

from collections import defaultdict
from app.db import SessionLocal, init_db
from app.models import Route

TRAILHEAD_M = 150   # distancia máxima entre trailheads para ser "la misma ruta"
ALT_M       = 60    # diferencia máxima de altitud máxima

init_db()
db = SessionLocal()
routes = db.query(Route).order_by(Route.started_at).all()

def dist_m(r1, r2):
    dlat = (r1.start_lat - r2.start_lat) * 111320
    dlon = (r1.start_lon - r2.start_lon) * 111320 * math.cos(math.radians(r1.start_lat))
    return math.sqrt(dlat**2 + dlon**2)

def same_route(r1, r2):
    if dist_m(r1, r2) > TRAILHEAD_M:
        return False
    a1 = r1.max_altitude_m or 0
    a2 = r2.max_altitude_m or 0
    # si ambas tienen altitud, comparar; si alguna no tiene, solo trailhead
    if r1.max_altitude_m is not None and r2.max_altitude_m is not None:
        return abs(a1 - a2) <= ALT_M
    return True

# Union-Find
parent = list(range(len(routes)))
def find(x):
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x
def union(x, y):
    parent[find(x)] = find(y)

for i in range(len(routes)):
    for j in range(i + 1, len(routes)):
        if same_route(routes[i], routes[j]):
            union(i, j)

clusters = defaultdict(list)
for i, r in enumerate(routes):
    clusters[find(i)].append(r)

print(f"Total sesiones : {len(routes)}")
print(f"Rutas unicas   : {len(clusters)}")
print(f"Grupos repetidos: {sum(1 for v in clusters.values() if len(v) > 1)}")
print()
repeated = sorted([(k, v) for k, v in clusters.items() if len(v) > 1], key=lambda x: -len(x[1]))
for _, lst in repeated:
    names = list(dict.fromkeys(r.name for r in sorted(lst, key=lambda r: r.started_at)))
    last = max(lst, key=lambda r: r.started_at)
    print(f"  {len(lst)}x | {last.start_lat:.4f},{last.start_lon:.4f} | {names}")

print()
singles = [v[0] for v in clusters.values() if len(v) == 1]
print(f"Singletons: {len(singles)}")
for r in sorted(singles, key=lambda r: r.name):
    print(f"  {r.name[:70]}")

db.close()
