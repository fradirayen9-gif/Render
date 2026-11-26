"""NewsKit asset library — core: sources, cache, license policy, downloads.  © 2026 Rayen Fradi

The library is OFFLINE-FIRST and LICENSE-SAFE:
  * every asset kind has a procedural fallback (kit/assets_proc.py, kit/sfx_lib.py, blender/assets_*.py), so a video
    always renders even when no download works;
  * optional downloads come only from CC0 sources (Poly Haven, ambientCG) — free for commercial use, no attribution
    needed (a credits file is still written);
  * downloads happen ONCE in the `prepare` job (GitHub's machine pulls them directly — nothing passes through chat or
    your phone, so size does not matter) and are shared with every render chunk as an artifact.

CLI:  python3 kit/assets_core.py selftest            # can this machine reach the sources?
      python3 kit/assets_core.py catalog textures    # list cached/fetched catalog size
      python3 kit/assets_core.py fetch texture <id> [1k]   | fetch hdri <id> [1k] | fetch model <id> [1k]
Add a source: write `<name>_catalog(kind)` and `<name>_get(kind, id, res)` below and list it in SOURCES.
"""
import hashlib
import io
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
import zipfile

KIT = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(KIT)
ASSETS = os.path.join(ROOT, "assets")
CACHE = os.environ.get("NEWSKIT_ASSET_CACHE") or os.path.join(ASSETS, "cache")
MANIFEST = os.path.join(ASSETS, "manifest.json")
UA = "NewsKit/4 asset-fetcher (CC0 only; github.com/RayenFradi)"
RES_ORDER = ["1k", "2k", "4k", "8k"]
CATALOG_MAX_AGE = 30 * 86400
TIMEOUT = int(os.environ.get("NEWSKIT_ASSET_TIMEOUT", "40"))


def log(*a):
    print("assets:", *a, flush=True)


def manifest():
    try:
        return json.load(open(MANIFEST, encoding="utf-8"))
    except Exception:
        return {"policy": {"licenses": ["CC0"]}, "tiers": {}}


def tier_cfg(tier):
    return (manifest().get("tiers") or {}).get(tier) or {}


# ------------------------------------------------------------------ http (soft-fail: never raises)
def http_get(url, timeout=None, retries=2):
    for k in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=timeout or TIMEOUT) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (400, 401, 403, 404, 410):
                log(f"HTTP {e.code} {url[:90]}")
                return None
        except Exception as e:
            if k == retries:
                log(f"{type(e).__name__} {url[:90]}")
        time.sleep(1.2 * (k + 1))
    return None


def http_json(url, **kw):
    b = http_get(url, **kw)
    if not b:
        return None
    try:
        return json.loads(b.decode("utf-8"))
    except Exception:
        return None


def save_atomic(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def download(url, dest, md5=None):
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return True
    b = http_get(url)
    if not b:
        return False
    if md5 and hashlib.md5(b).hexdigest() != md5:
        log(f"md5 mismatch {os.path.basename(dest)} (kept anyway? no — skipped)")
        return False
    save_atomic(dest, b)
    return True


def cached_json(name, fetch, max_age=CATALOG_MAX_AGE):
    p = os.path.join(CACHE, "catalog", name + ".json")
    try:
        if os.path.exists(p) and time.time() - os.path.getmtime(p) < max_age:
            return json.load(open(p, encoding="utf-8"))
    except Exception:
        pass
    data = fetch()
    if data:
        save_atomic(p, json.dumps(data).encode("utf-8"))
        return data
    try:                                   # stale cache beats nothing
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return None


def pick_res(options, want):
    """closest available resolution not above `want`, else the smallest above."""
    opts = [r for r in RES_ORDER if r in options]
    if not opts:
        return None
    w = RES_ORDER.index(want) if want in RES_ORDER else 0
    below = [r for r in opts if RES_ORDER.index(r) <= w]
    return below[-1] if below else opts[0]


# ------------------------------------------------------------------ Poly Haven (CC0): textures, HDRIs, models
PH = "https://api.polyhaven.com"
PH_TEX_MAPS = {"diff": ("Diffuse", "diff"), "nor": ("nor_gl",), "rough": ("Rough",), "metal": ("Metal",), "disp": ("Displacement",), "ao": ("AO",)}


def polyhaven_catalog(kind):
    """kind: textures | hdris | models  ->  {id: {name, categories, tags, ...}}"""
    return cached_json(f"ph_{kind}", lambda: http_json(f"{PH}/assets?t={kind}")) or {}


def _ph_files(aid):
    return http_json(f"{PH}/files/{aid}")


def _first_fmt(node, prefs=("jpg", "png", "exr", "hdr")):
    for f in prefs:
        if isinstance(node, dict) and isinstance(node.get(f), dict) and node[f].get("url"):
            return f, node[f]
    return None, None


def polyhaven_get(kind, aid, res="1k"):
    """download one asset; returns {"dir"|"file", "id", "src", "license"} or None."""
    files = _ph_files(aid)
    if not files:
        return None
    meta = {"id": aid, "src": "polyhaven", "license": "CC0", "url": f"https://polyhaven.com/a/{aid}"}
    if kind == "texture":
        out = os.path.join(CACHE, "pbr", f"ph_{aid}_{res}")
        got = {}
        for key, names in PH_TEX_MAPS.items():
            for nm in names:
                node = files.get(nm)
                if not isinstance(node, dict):
                    continue
                r = pick_res(node.keys(), res)
                fmt, ent = _first_fmt(node.get(r) if r else None)
                if ent and download(ent["url"], os.path.join(out, f"{key}.{fmt}"), ent.get("md5")):
                    got[key] = f"{key}.{fmt}"
                    break
        if "diff" not in got:
            shutil.rmtree(out, ignore_errors=True)
            return None
        meta.update(maps=got, res=res, dir=out)
        save_atomic(os.path.join(out, "meta.json"), json.dumps(meta).encode())
        return meta
    if kind == "hdri":
        node = files.get("hdri") or {}
        r = pick_res(node.keys(), res)
        fmt, ent = _first_fmt(node.get(r) if r else None, ("hdr", "exr"))
        if not ent:
            return None
        dest = os.path.join(CACHE, "hdri", f"ph_{aid}_{r}.{fmt}")
        if not download(ent["url"], dest, ent.get("md5")):
            return None
        meta.update(file=dest, res=r)
        return meta
    if kind == "model":
        node = files.get("gltf") or {}
        r = pick_res(node.keys(), res)
        ent = (node.get(r) or {}).get("gltf") if r else None
        if not ent:
            return None
        out = os.path.join(CACHE, "models", f"ph_{aid}_{r}")
        main = os.path.join(out, f"{aid}.gltf")
        ok = download(ent["url"], main, ent.get("md5"))
        for rel, sub in (ent.get("include") or {}).items():
            rel = os.path.normpath(rel)
            if rel.startswith("..") or os.path.isabs(rel):
                continue                                        # never write outside the model folder
            ok = download(sub["url"], os.path.join(out, rel), sub.get("md5")) and ok
        if not ok:
            shutil.rmtree(out, ignore_errors=True)
            return None
        meta.update(file=main, res=r)
        return meta
    return None


# ------------------------------------------------------------------ ambientCG (CC0): PBR materials
ACG = "https://ambientcg.com/api/v2/full_json"
ACG_MAPS = {"diff": "_Color", "nor": "_NormalGL", "rough": "_Roughness", "metal": "_Metalness", "disp": "_Displacement", "ao": "_AmbientOcclusion"}


def ambientcg_catalog(kind="textures"):
    def fetch():
        d = http_json(f"{ACG}?type=Material&sort=Popular&limit=150&include=downloadData,tagData")
        out = {}
        for a in (d or {}).get("foundAssets", []):
            dls = ((((a.get("downloadFolders") or {}).get("default") or {}).get("downloadFiletypeCategories") or {}).get("zip") or {}).get("downloads") or []
            links = {x.get("attribute", "").lower().replace("-jpg", ""): x.get("downloadLink") for x in dls if "JPG" in x.get("attribute", "")}
            if a.get("assetId") and links:
                out[a["assetId"]] = {"name": a.get("displayName") or a["assetId"], "tags": [t.lower() for t in a.get("tags", [])],
                                     "categories": [(a.get("displayCategory") or "").lower()], "links": links}
        return out
    return cached_json("acg_materials", fetch) or {}


def ambientcg_get(kind, aid, res="1k"):
    cat = ambientcg_catalog()
    ent = cat.get(aid)
    if not ent or kind != "texture":
        return None
    r = pick_res(ent["links"].keys(), res)
    if not r:
        return None
    out = os.path.join(CACHE, "pbr", f"acg_{aid}_{r}")
    meta_p = os.path.join(out, "meta.json")
    if os.path.exists(meta_p):
        return json.load(open(meta_p))
    b = http_get(ent["links"][r], timeout=90)
    if not b:
        return None
    got = {}
    try:
        with zipfile.ZipFile(io.BytesIO(b)) as z:
            for n in z.namelist():
                base = os.path.basename(n)
                for key, suf in ACG_MAPS.items():
                    if key not in got and (suf + ".") in base and base.lower().endswith((".jpg", ".png")):
                        ext = base.rsplit(".", 1)[-1].lower()
                        save_atomic(os.path.join(out, f"{key}.{ext}"), z.read(n))        # fixed names: zip paths are never trusted
                        got[key] = f"{key}.{ext}"
    except Exception as e:
        log(f"zip {aid}: {e}")
        return None
    if "diff" not in got:
        shutil.rmtree(out, ignore_errors=True)
        return None
    meta = {"id": aid, "src": "ambientcg", "license": "CC0", "url": f"https://ambientcg.com/view?id={aid}", "maps": got, "res": r, "dir": out}
    save_atomic(meta_p, json.dumps(meta).encode())
    return meta


SOURCES = {"polyhaven": (polyhaven_catalog, polyhaven_get), "ambientcg": (ambientcg_catalog, ambientcg_get)}


def get(src, kind, aid, res="1k"):
    """download (or reuse) one asset from a source; license-checked against assets/manifest.json policy."""
    if src not in SOURCES:
        return None
    ok = (manifest().get("policy") or {}).get("licenses", ["CC0"])
    m = SOURCES[src][1](kind, aid, res)
    if m and m.get("license") not in ok:
        log(f"license {m.get('license')} not allowed -> skipped {aid}")
        return None
    return m


# ------------------------------------------------------------------ credits ledger (what a video used, and where it came from)
def write_credits(path, used):
    lines = ["NewsKit assets used in this video (all CC0 / procedural — attribution optional, appreciated):", ""]
    for u in used:
        if u.get("src") in ("polyhaven", "ambientcg"):
            lines.append(f"- {u.get('role', '')}: {u['id']} — {u['src']} ({u.get('license', 'CC0')}) {u.get('url', '')}")
        else:
            lines.append(f"- {u.get('role', '')}: {u.get('id', 'procedural')} — procedural, original (© Rayen Fradi Developments)")
    save_atomic(path, ("\n".join(lines) + "\n").encode("utf-8"))


def selftest():
    ok = True
    for name, (cat, _) in SOURCES.items():
        kinds = ["textures", "hdris", "models"] if name == "polyhaven" else ["textures"]
        for k in kinds:
            t0 = time.time()
            c = cat(k)
            good = bool(c)
            ok &= good
            print(f"{'OK  ' if good else 'FAIL'} {name:10s} {k:9s} {len(c or {}):5d} assets  {time.time() - t0:4.1f}s")
    print("selftest:", "all sources reachable" if ok else "some sources unreachable -> procedural fallbacks will be used (videos still render)")
    return ok


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a or a[0] == "selftest":
        sys.exit(0 if selftest() else 1)
    if a[0] == "catalog":
        c = polyhaven_catalog(a[1]) if len(a) > 1 else {}
        print(len(c), "assets", list(c)[:12])
    elif a[0] == "fetch" and len(a) >= 3:
        src = "ambientcg" if a[2][:1].isupper() and a[1] == "texture" else "polyhaven"
        print(json.dumps(get(src, a[1], a[2], a[3] if len(a) > 3 else "1k"), indent=1))
