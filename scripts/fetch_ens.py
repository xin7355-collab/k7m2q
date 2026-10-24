#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Weather Lab 的 AI 系集颱風路徑 → 臺灣周邊「8 級／10 級風」機率格網（data/ens.json）。

**為什麼只公開機率、不公開路徑**

Weather Lab 的即時資料（起報時間之後、關聯到未來的預報）受
「GDM Real-Time Weather Forecasting Experimental Data Terms of Use」管：
- 原始資料（含裁切範圍、換格式、只挑幾個時間或成員）都算「未修改的資料」，
  只能給明確指定的對象、不得再傳出去——放在公開 repo／公開網頁就是公開散布，不行。
- 「從資料推算、為特定用途做的、而且推不回原始資料」的產品（Non-Retrievable Value Added Service）
  可以公開，條件是標註取得管道（Weather Lab）與指定的著作權聲明。
所以這支程式在 Actions 裡下載、換算，**只把每個格點的機率與成員中位數時間寫出去**，原始 CSV 不落地、不進 git：
- 機率是 50～64 組成員疊起來的次數，推不回任何一組成員的路徑；
- 時間只在命中成員 ≥ ENS_MIN_HITS 組的格點才寫（少數成員的中位數幾乎就是那組成員本身的值）。

**換算方式**
每組成員（sample）每條颱風路徑，6 小時一點內插成逐時；每個時刻用該時刻的四象限 34 kt／50 kt 風半徑
（東北、東南、西南、西北各自一個四分之一圓）判斷格點有沒有被掃到。
34 kt ≈ 17.5 m/s（8 級風下限）；50 kt ≈ 25.7 m/s（10 級風 24.5 m/s 以上）。
- p34／p50：起報後 ENS_HOURS 小時內曾被 34／50 kt 風圈掃到的成員百分比；
- t34／t34x：命中成員「第一次掃到」與「最後一次掃到」的時數中位數（起報後幾小時；不足門檻時 -1）。

**什麼時候抓**
起報 00/06/12/18 UTC，實測約 6 小時後才放出檔案。每輪只試「比手上這份新、而且起報已滿 ENS_MIN_AGE 小時」
的時次，最多往回 ENS_BACK 小時；手上已是最新就一個請求都不送（cwa.yml 每 10 分鐘跑一次）。
模型用 Weather Lab 自己的「OPER」（它當下的作業版本，換代不用改程式），抓不到再試 ENS_MODELS 後面的。

**失敗處理**
- 還沒有更新的時次（404）：正常，不寫檔、不紅燈。
- 網路錯：保留上一份，只記 error 與 tried；一小時內不重試同一件事。
- 抓到了卻看不懂（欄位改了）：這是程式要跟著改，回 1 紅燈。

離線自我測試：python3 scripts/fetch_ens.py selftest
"""
import csv
import io
import json
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE = os.environ.get("ENS_BASE", "https://deepmind.google.com/science/weatherlab/download/cyclones")
MODELS = [m for m in os.environ.get("ENS_MODELS", "OPER,WNV3").split(",") if m.strip()]
HOURS = int(os.environ.get("ENS_HOURS", "120"))         # 跟氣象署官方預報一樣到 5 天；不接長天期
MIN_AGE = float(os.environ.get("ENS_MIN_AGE", "4"))     # 起報後幾小時才開始試（太早一定 404）
BACK = float(os.environ.get("ENS_BACK", "30"))          # 最多往回找幾小時
MIN_HITS = int(os.environ.get("ENS_MIN_HITS", "5"))     # 命中成員少於這個數就不寫時間
RETRY_MIN = 60                                          # 網路錯之後，多久內不再試
MAX_BYTES = 30_000_000
OUT = os.environ.get("ENS_OUT", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "ens.json"))

# 臺灣周邊（含金門、馬祖、澎湖、東沙、蘭嶼）。0.1° ≈ 11 km，比系集本身的散布細得多
LAT0, LON0, D, NY, NX = 20.0, 115.0, 0.1, 76, 96
KM_LAT = 111.195
REGION = {"lat0": LAT0, "lon0": LON0, "d": D, "ny": NY, "nx": NX}

TERMS = "https://storage.googleapis.com/weathernext-public/terms-of-use.pdf"
CITE = ("© 2024-6 Google LLC, whose machine learning models were used to create the experimental data "
        "made available under the following licence terms " + TERMS + ". This data is intended for experimental "
        "modelling only and is not intended, validated, or approved for real world use.")
NEED = ["init_time", "track_id", "sample", "lead_time_hours", "lat", "lon"] + [
    f"radius_{k}_knot_winds_{q}_km" for k in (34, 50) for q in ("ne", "se", "sw", "nw")]


def iso(t):
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s):
    t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def url_for(model, t):
    return f"{BASE}/{model}/ensemble/paired/csv/{model}_{t:%Y_%m_%dT%H}_00_paired.csv"


def candidates(now, prev_init):
    """由新到舊列出可以試的起報時次；比手上那份舊或一樣的不試。"""
    t = now - timedelta(hours=MIN_AGE)
    t = t.replace(minute=0, second=0, microsecond=0) - timedelta(hours=t.hour % 6)
    out = []
    while (now - t).total_seconds() / 3600 <= BACK:
        if prev_init and t <= prev_init:
            break
        out.append(t)
        t -= timedelta(hours=6)
    return out


class Bad(Exception):
    """抓到了但格式看不懂：程式要改，紅燈。"""


def parse(text):
    """回傳 (init, n, tracks)。tracks: {(track_id, sample): [(lead, lat, lon, r34[4], r50[4]), ...]}"""
    lines = [l for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")]
    if not lines:
        raise Bad("檔案是空的")
    rd = csv.DictReader(io.StringIO("\n".join(lines)))
    miss = [c for c in NEED if c not in (rd.fieldnames or [])]
    if miss:
        raise Bad("缺欄位 " + ", ".join(miss))
    tracks, samples, init = {}, set(), None
    for r in rd:
        try:
            s = int(float(r["sample"]))
            lead = float(r["lead_time_hours"])
            lat, lon = float(r["lat"]), float(r["lon"])
            r34 = [max(0.0, float(r[f"radius_34_knot_winds_{q}_km"] or 0)) for q in ("ne", "se", "sw", "nw")]
            r50 = [max(0.0, float(r[f"radius_50_knot_winds_{q}_km"] or 0)) for q in ("ne", "se", "sw", "nw")]
        except (TypeError, ValueError):
            continue                     # 單列壞掉略過；整份都壞會在下面變成 0 列
        if not (math.isfinite(lat) and math.isfinite(lon)):
            continue
        samples.add(s)
        if init is None and r.get("init_time"):
            init = parse_iso(r["init_time"].strip().replace(" ", "T"))
        if lead > HOURS:
            continue
        tracks.setdefault((r["track_id"], s), []).append((lead, lat, lon, r34, r50))
    for v in tracks.values():
        v.sort(key=lambda p: p[0])
    return init, len(samples), tracks


def hourly(pts):
    """6 小時一點 → 逐時（位置、風半徑都線性內插）。"""
    out = []
    for a, b in zip(pts, pts[1:]):
        span = b[0] - a[0]
        if span <= 0:
            continue
        h = math.ceil(a[0])
        while h < b[0]:
            f = (h - a[0]) / span
            out.append((h, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f,
                        [x + (y - x) * f for x, y in zip(a[3], b[3])],
                        [x + (y - x) * f for x, y in zip(a[4], b[4])]))
            h += 1
    p = pts[-1]
    if float(p[0]).is_integer():
        out.append((int(p[0]), p[1], p[2], p[3], p[4]))
    return out


def spans(la, lo, r):
    """四象限風圈（ne, se, sw, nw 的半徑 km）在每一列格點上蓋到的欄範圍：[(列, 起欄, 迄欄+1)]。"""
    rmax = max(r)
    if rmax <= 0:
        return []
    out = []
    j0 = max(0, math.ceil((la - rmax / KM_LAT - LAT0) / D))
    j1 = min(NY - 1, math.floor((la + rmax / KM_LAT - LAT0) / D))
    for j in range(j0, j1 + 1):
        y = LAT0 + j * D
        dy = (y - la) * KM_LAT
        rE, rW = (r[0], r[3]) if dy >= 0 else (r[1], r[2])
        kx = 111.32 * math.cos(math.radians(y))
        west = lo - math.sqrt(rW * rW - dy * dy) / kx if rW > abs(dy) else None
        east = lo + math.sqrt(rE * rE - dy * dy) / kx if rE > abs(dy) else None
        if west is None and east is None:
            continue
        a = lo if west is None else west
        b = lo if east is None else east
        i0 = max(0, math.ceil((a - LON0) / D - 1e-9))
        i1 = min(NX - 1, math.floor((b - LON0) / D + 1e-9))
        if i1 >= i0:
            out.append((j, i0, i1 + 1))
    return out


def near_region(pts, pad=8.0):
    return any(LAT0 - pad <= p[1] <= LAT0 + NY * D + pad and LON0 - pad <= p[2] <= LON0 + NX * D + pad for p in pts)


def median(v):
    v = sorted(v)
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def compute(n, tracks):
    """回傳 (grid 或 None, 每顆颱風的摘要)。"""
    N = NY * NX
    BIG = 10 ** 6
    first = {}          # sample → [每格最早命中時數]
    last = {}
    hit50 = {}
    storms = {}
    for (tid, s), pts in tracks.items():
        st = storms.setdefault(tid, {"id": tid, "lat": [], "lon": [], "m": set(), "reg": set()})
        st["m"].add(s)
        if pts[0][0] == 0:
            st["lat"].append(pts[0][1]); st["lon"].append(pts[0][2])
        if not near_region(pts):
            continue
        hs = hourly(pts)
        F = first.setdefault(s, [BIG] * N)
        Lr = last.setdefault(s, [-1] * N)
        H = hit50.setdefault(s, bytearray(N))
        # 同一組成員可能有兩顆颱風：先各自標，再取最早／最晚
        tf, tl = [BIG] * N, [-1] * N
        marks = []
        for h, la, lo, r34, r50 in hs:
            sp = spans(la, lo, r34)
            marks.append((h, sp))
            for j, i0, i1 in sp:
                k = j * NX
                tl[k + i0:k + i1] = [h] * (i1 - i0)          # 時間由小到大：最後寫的就是最晚
            for j, i0, i1 in spans(la, lo, r50):
                k = j * NX
                H[k + i0:k + i1] = b"\x01" * (i1 - i0)
        for h, sp in reversed(marks):                          # 反過來寫一次：最後留下的是最早
            for j, i0, i1 in sp:
                k = j * NX
                tf[k + i0:k + i1] = [h] * (i1 - i0)
        if any(x >= 0 for x in tl):
            st["reg"].add(s)
        for i in range(N):
            if tf[i] < F[i]:
                F[i] = tf[i]
            if tl[i] > Lr[i]:
                Lr[i] = tl[i]
    p34, p50, t34, t34x = [0] * N, [0] * N, [-1] * N, [-1] * N
    any_hit = False
    for i in range(N):
        fs = [F[i] for F in first.values() if F[i] < BIG]
        c50 = sum(1 for H in hit50.values() if H[i])
        if fs:
            any_hit = True
            p34[i] = round(100 * len(fs) / n)
            if len(fs) >= MIN_HITS:
                t34[i] = round(median(fs))
                t34x[i] = round(median([Lr[i] for Lr in last.values() if Lr[i] >= 0]))
        if c50:
            any_hit = True
            p50[i] = round(100 * c50 / n)
    grid = {"p34": p34, "p50": p50, "t34": t34, "t34x": t34x} if any_hit else None
    out = []
    for st in storms.values():
        if not st["lat"]:
            continue
        out.append({"id": st["id"], "lat": round(median(st["lat"]), 2), "lon": round(median(st["lon"]), 2),
                    "m": len(st["m"]), "pReg": round(100 * len(st["reg"]) / n) if n else 0})
    out.sort(key=lambda x: (-x["pReg"], x["id"]))
    return grid, out


def fetch(url, timeout=60):
    """回傳文字；404 回 None（還沒出這一報）；其他錯誤丟出去。網路暫時錯誤重試兩次（2、4 秒）。"""
    err = None
    for k in range(3):
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; data-fetch/1.0)"})
            with urlopen(req, timeout=timeout) as r:
                b = r.read(MAX_BYTES + 1)
            if len(b) > MAX_BYTES:
                raise Bad(f"檔案超過 {MAX_BYTES // 1_000_000} MB，不像颱風路徑檔")
            return b.decode("utf-8", "replace")
        except HTTPError as e:
            if e.code == 404:
                return None
            err = e
            if e.code < 500 and e.code != 429:
                break
        except (URLError, TimeoutError, OSError) as e:
            err = e
        time.sleep(2 ** (k + 1))
    raise err


def load_prev():
    try:
        with open(OUT, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def write(doc):
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, OUT)


def run(now, getter=fetch):
    """回傳 (exit code, 訊息)。getter(url) → 文字／None（404）／丟例外。"""
    prev = load_prev() or {}
    prev_init = None
    try:
        prev_init = parse_iso(prev["init"]) if prev.get("init") else None
    except Exception:
        prev_init = None
    if prev.get("error") and prev.get("tried"):
        try:
            if (now - parse_iso(prev["tried"])).total_seconds() < RETRY_MIN * 60:
                return 0, "上一輪網路錯，一小時內不重試"
        except Exception:
            pass
    cands = candidates(now, prev_init)
    if not cands:
        return 0, "手上已是最新一報，不用抓"
    errs = []
    for t in cands:
        for model in MODELS:
            url = url_for(model, t)
            try:
                text = getter(url)
            except Bad as e:
                return fail(prev, now, f"{model} {t:%m/%d %H}Z：{e}")
            except Exception as e:                       # 網路錯：換下一個再試，全失敗才記
                errs.append(f"{model} {t:%m/%d %H}Z {getattr(e, 'code', '') or type(e).__name__}")
                continue
            if text is None:
                continue
            try:
                init, n, tracks = parse(text)
            except Bad as e:
                return fail(prev, now, f"{model} {t:%m/%d %H}Z 看不懂（{e}），Weather Lab 可能改了格式")
            grid, storms = compute(n, tracks) if n else (None, [])
            write({"v": 1, "src": "Weather Lab", "model": model, "init": iso(init or t), "members": n,
                   "hours": HOURS, "fetched": iso(now), "tried": iso(now), "error": "",
                   # 範圍固定寫出來：grid 是 null 時，前端才分得出「站在範圍內、0%」和「站在範圍外、沒有資料」
                   "region": REGION, "grid": grid, "storms": storms, "minHits": MIN_HITS,
                   "note": "由 Weather Lab 系集颱風路徑推算的風圈機率與成員中位數時間；不含原始路徑。",
                   "cite": CITE, "terms": TERMS})
            return 0, f"{model} {t:%m/%d %H}Z：{n} 組成員、{len(storms)} 顆，{'臺灣周邊有機率' if grid else '臺灣周邊沒有成員掃到'}"
    if errs:
        prev = dict(prev) if prev else {"v": 1, "grid": None, "storms": [], "cite": CITE, "terms": TERMS}
        prev["error"] = "Weather Lab 連不上：" + "；".join(errs[:4])
        prev["tried"] = iso(now)
        write(prev)
        return 0, prev["error"]
    if prev_init is None or (now - prev_init).total_seconds() / 3600 > BACK + 6:
        # 近 30 小時一個檔都沒有：多半是網址或模型代號改了，要人來看（紅燈）。
        # 錯誤寫進檔裡：前端講得出「抓不到」而不是一片空白；也讓下一輪照一小時規則等，不會每 10 分鐘紅一次
        return fail(prev, now, f"Weather Lab 近 {BACK:.0f} 小時都沒有檔案（{', '.join(MODELS)}），網址或模型代號可能改了")
    if prev.get("error"):
        # 上次連不上、這次連得上（只是新的一報還沒出）：把舊的錯誤拿掉，不然畫面一直說「連不上」
        doc = dict(prev)
        doc["error"], doc["tried"] = "", iso(now)
        write(doc)
    return 0, "還沒有更新的一報"


def fail(prev, now, msg):
    """要人來看的錯（紅燈）。錯誤寫進檔裡：前端講得出「抓不到、為什麼」而不是一片空白；
    上一份的機率保留（它的起報時間照實標，前端看得出多舊）；也讓下一輪照一小時規則等，不會每 10 分鐘紅一次。"""
    doc = dict(prev) if prev else {"v": 1, "grid": None, "storms": [], "cite": CITE, "terms": TERMS}
    doc["error"], doc["tried"] = msg, iso(now)
    write(doc)
    return 1, msg


def main():
    code, msg = run(datetime.now(timezone.utc))
    print(("錯誤：" if code else "") + msg)
    return code


# ───────────────────────── 離線自我測試 ─────────────────────────
def _csv(rows):
    head = ("# If this file contains data that relates to a time less than 1 hour ago or in the future,\n"
            "# BEGIN DATA\n")
    cols = ["init_time", "track_id", "sample", "valid_time", "lead_time", "lead_time_hours", "lat", "lon",
            "minimum_sea_level_pressure_hpa", "maximum_sustained_wind_speed_knots", "radius_of_maximum_winds_km"] + [
        f"radius_{k}_knot_winds_{q}_km" for k in (34, 50, 64) for q in ("ne", "se", "sw", "nw")]
    out = [",".join(cols)]
    for tid, s, lead, lat, lon, r34, r50 in rows:
        out.append(",".join(map(str, ["2026-10-24 12:00:00", tid, s, "x", "x", lead, lat, lon, 980, 80, 30,
                                      *r34, *r50, 0, 0, 0, 0])))
    return head + "\n".join(out) + "\n"


def selftest():
    global OUT
    import tempfile
    ok = 0

    def check(cond, msg):
        nonlocal ok
        if not cond:
            print("✗", msg)
            sys.exit(1)
        ok += 1
        print("  ✓", msg)

    def at(grid, key, lat, lon):
        j, i = round((lat - LAT0) / D), round((lon - LON0) / D)
        return grid[key][j * NX + i]

    # ① 幾何：東北象限 300 km、其他 100 km。中心東北方 200 km 要掃到，西南方 200 km 不能
    sp = spans(23.0, 120.0, [300, 100, 100, 100])
    cov = {(j, i) for j, a, b in sp for i in range(a, b)}
    ne = (round((23.0 + 200 / math.sqrt(2) / KM_LAT - LAT0) / D), round((120.0 + 200 / math.sqrt(2) / (111.32 * math.cos(math.radians(24.27))) - LON0) / D))
    sw = (round((23.0 - 200 / math.sqrt(2) / KM_LAT - LAT0) / D), round((120.0 - 200 / math.sqrt(2) / (111.32 * math.cos(math.radians(21.73))) - LON0) / D))
    check(ne in cov and sw not in cov, "四象限風半徑：東北 300 km 掃到 200 km 外，西南 100 km 掃不到 200 km 外")
    check(spans(23.0, 120.0, [0, 0, 0, 0]) == [], "沒有 34 kt 風圈（熱帶性低氣壓）不算掃到")
    check(spans(50.0, 150.0, [300] * 4) == [], "遠在範圍外的颱風不碰格網")

    # ② 系集：10 組成員，6 組往臺灣（中心 22→25N 沿 121E），4 組往南海；另有成員 9 只有一顆別處的颱風
    rows = []
    for s in range(6):
        for k, lead in enumerate(range(0, 132, 6)):
            lat = 18.0 + 0.25 * k
            rows.append(("WP302026", s, lead, lat, 121.0 + 0.02 * s, [250] * 4, [80] * 4 if s < 3 else [0] * 4))
    for s in range(6, 9):
        for k, lead in enumerate(range(0, 132, 6)):
            rows.append(("WP302026", s, lead, 18.0, 121.0 - 0.6 * k, [150] * 4, [0] * 4))
    rows.append(("EP222026", 9, 0, 13.0, -103.0, [200] * 4, [50] * 4))
    rows.append(("WP302026", 3, 144, 30.0, 121.0, [250] * 4, [0] * 4))   # 超過 120 小時：丟掉
    init, n, tracks = parse(_csv(rows))
    check(n == 10 and init == datetime(2026, 10, 24, 12, tzinfo=timezone.utc), "成員數用整份檔的 sample 數（含沒有這顆颱風的）")
    check(all(p[0] <= HOURS for v in tracks.values() for p in v), "超過 ENS_HOURS 的預報點丟掉（不接長天期）")
    grid, storms = compute(n, tracks)
    p = at(grid, "p34", 23.0, 121.0)
    check(p == 60, f"臺南外海 23N 121E：6/10 組掃到 → 60%（得到 {p}）")
    check(at(grid, "p50", 23.0, 121.0) == 30, "10 級風（50 kt）只有 3 組有 → 30%")
    t = at(grid, "t34", 23.0, 121.0)
    check(t > 0 and t < at(grid, "t34x", 23.0, 121.0), f"開始時間早於結束時間（{t} < {at(grid, 't34x', 23.0, 121.0)}）")
    check(at(grid, "p34", 27.4, 115.1) == 0, "沒有成員到的角落是 0%")
    ws = next(s for s in storms if s["id"] == "WP302026")
    check(ws["m"] == 9 and ws["pReg"] == 60 and abs(ws["lat"] - 18.0) < 0.01, "颱風摘要：9 組有這顆、往南海的 3 組沒碰到 → 60%、起報位置")
    check(not any(k in json.dumps(storms) for k in ("r34", "radius")), "輸出不含原始路徑或風半徑")

    # ③ 命中太少不寫時間（推不回單一成員）
    rows2 = [("WP302026", s, lead, 18.0 + 0.25 * k, 121.0, [250] * 4, [0] * 4)
             for s in range(2) for k, lead in enumerate(range(0, 126, 6))]
    rows2 += [("WP302026", s, 0, 10.0, 130.0, [0] * 4, [0] * 4) for s in range(2, 10)]
    g2, _ = compute(*parse(_csv(rows2))[1:])
    check(at(g2, "p34", 23.0, 121.0) == 20 and at(g2, "t34", 23.0, 121.0) == -1, "只有 2 組命中：有機率、不寫時間")

    # ④ 時次挑選
    now = datetime(2026, 10, 24, 18, 34, tzinfo=timezone.utc)
    c = candidates(now, None)
    check(c[0] == datetime(2026, 10, 24, 12, tzinfo=timezone.utc), "起報未滿 4 小時的 18Z 不試，從 12Z 開始")
    check(candidates(now, datetime(2026, 10, 24, 12, tzinfo=timezone.utc)) == [], "手上已是 12Z → 一個請求都不送")
    check(len(c) == 4, "最多往回 30 小時（12Z、06Z、00Z、前一天 18Z）")

    # ⑤ 整條流程（假的下載）
    with tempfile.TemporaryDirectory() as d:
        OUT = os.path.join(d, "ens.json")
        calls = []

        def g404(u):
            calls.append(u)
            return None
        code, msg = run(now, g404)
        d404 = load_prev()
        check(code == 1 and d404["grid"] is None and "init" not in d404 and "網址" in d404["error"],
              "近 30 小時全 404、手上也沒有 → 紅燈；檔裡只有錯誤原因，沒有假的起報時間")
        calls.clear()
        check(run(now + timedelta(minutes=10), g404)[0] == 0 and calls == [], "紅燈後一小時內不再試（不會每 10 分鐘紅一次）")
        os.remove(OUT)

        good = _csv(rows)

        def gok(u):
            calls.append(u)
            return good if "OPER_2026_10_24T06" in u else None
        calls.clear()
        code, msg = run(now, gok)
        doc = load_prev()
        check(code == 0 and doc["init"] == "2026-10-24T12:00:00Z" and doc["model"] == "OPER", "12Z 還沒有就退到 06Z；init 以檔案內容為準")
        check(doc["cite"].startswith("© 2024-6 Google LLC") and doc["terms"] == TERMS and doc["src"] == "Weather Lab",
              "附上條款要求的出處與著作權聲明")
        check(any("WNV3" in u for u in calls) and calls.index(next(u for u in calls if "WNV3_2026_10_24T12" in u)) < calls.index(next(u for u in calls if "OPER_2026_10_24T06" in u)),
              "同一時次先試 OPER、再試後備模型，才往前一個時次")
        raw = open(OUT, encoding="utf-8").read()
        check("WP302026,0" not in raw and "radius" not in raw, "原始 CSV 不落地：輸出沒有任何原始列")

        calls.clear()
        code, msg = run(now + timedelta(minutes=10), gok)
        check(code == 0 and calls == [], "手上已是最新：不送請求、不改檔")

        def gerr(u):
            raise URLError("timeout")
        code, msg = run(now + timedelta(hours=7), gerr)
        doc2 = load_prev()
        check(code == 0 and doc2["error"].startswith("Weather Lab 連不上") and doc2["grid"] == doc["grid"],
              "網路錯：保留上一份機率，只記錯誤與時間")
        calls.clear()
        code, msg = run(now + timedelta(hours=7, minutes=20), gok)
        check(calls == [] and "不重試" in msg, "網路錯後一小時內不重試")
        code, msg = run(now + timedelta(hours=8, minutes=10), g404)
        check(load_prev()["error"] == "" and load_prev()["init"] == doc["init"], "連得上了（新的一報還沒出）：錯誤清掉，機率照舊")

        def gbad(u):
            return "init_time,foo\n1,2\n"
        os.remove(OUT)
        code, msg = run(now, gbad)
        check(code == 1 and "看不懂" in msg and load_prev()["grid"] is None and "init" not in load_prev(),
              "欄位改了 → 紅燈、檔裡只記原因，不寫半套資料")
        os.remove(OUT)

        def gempty(u):
            return _csv([("WP302026", s, 0, 10.0, 160.0, [0] * 4, [0] * 4) for s in range(4)])
        code, msg = run(now, gempty)
        check(code == 0 and load_prev()["grid"] is None and load_prev()["members"] == 4, "颱風都在遠方：grid 為 null（前端說「沒有成員掃到」，不是「沒資料」）")

    # ⑥ 速度：50 組 × 一顆大颱風穿過臺灣，在 Actions 上不能拖太久
    rows3 = [("WP302026", s, lead, 18.0 + 0.3 * k, 118.0 + 0.2 * k + 0.05 * s, [400, 350, 300, 350], [150] * 4)
             for s in range(50) for k, lead in enumerate(range(0, 126, 6))]
    t0 = time.time()
    compute(*parse(_csv(rows3))[1:])
    dt = time.time() - t0
    check(dt < 60, f"50 組大颱風換算 {dt:.1f} 秒（< 60 秒）")
    print(f"全部通過（{ok} 項）")
    return 0


if __name__ == "__main__":
    sys.exit(selftest() if sys.argv[1:] == ["selftest"] else main())
