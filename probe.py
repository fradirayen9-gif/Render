"""Real-download probe for kit/assets_core.py (one asset of each kind). (c) Rayen Fradi Developments"""
import os, sys
sys.path.insert(0, "kit")
import assets_core as A

def size(m):
    p = (m or {}).get("dir") or (m or {}).get("file")
    if not p: return 0
    if os.path.isfile(p) and p.endswith(".gltf"): p = os.path.dirname(p)          # model = gltf + its .bin/textures
    if os.path.isfile(p): return os.path.getsize(p)
    return sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(p) for f in fs)

jobs = [("polyhaven", "textures", "texture"), ("ambientcg", "textures", "texture"),
        ("polyhaven", "hdris", "hdri"), ("polyhaven", "models", "model")]
bad = 0
for src, cat, kind in jobs:
    ids = sorted(A.SOURCES[src][0](cat))[:6]
    done = None
    for aid in ids:
        m = A.get(src, kind, aid, "1k")
        if m and size(m) > 0:
            done = (aid, m); break
    if done:
        print(f"OK   {src:10s} {kind:8s} {done[0]:40s} {size(done[1])/1e6:7.2f} MB  license={done[1].get('license')}")
    else:
        bad += 1; print(f"FAIL {src:10s} {kind:8s} tried={ids}")
print("probe:", "ALL REAL DOWNLOADS OK" if not bad else f"{bad} kind(s) failed")
sys.exit(1 if bad else 0)
