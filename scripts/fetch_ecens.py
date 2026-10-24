#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ECMWF 系集颱風路徑（51 組成員、到 10 天）→ data/ecens.json，給路徑圖畫「模式系集」細線。

**為什麼是 ECMWF、為什麼可以畫線**
使用者看到別人的颱風圖有一大把延伸到 10 天的系集線，問為什麼自己的只有官方 5 天（2026-10-25 選擇加上）。
ECMWF 的即時開放資料是 CC BY 4.0：可以轉存、公開畫出來，條件是標出處、授權與「有修改」。
（Weather Lab 的 AI 系集條款不准公開原始路徑，所以那邊只放機率，這邊才畫線。）

**它不是預報**
這是數值模式每一組成員的颱風中心位置；官方預報只到 5 天。前端預設關閉、標「非官方」、第 6 天後變淡，
不進待機建議——第 7～10 天成員之間常差上千公里，拿來決定站臺動作沒有意義，只用來提早看有沒有線指向臺灣。

**抓什麼**
ECMWF 開放資料的 tropical cyclone track（type=tf，BUFR 第 4 版）：IFS 系集（stream=enfo）每天 00、12 UTC 兩報到 15 天；
06、18 UTC 只到 6 天，所以只用 00、12。檔案約起報後 7.5 小時出現。
「沒有任何熱帶氣旋時不出檔」：404 是正常的，不能當成壞掉。
官方站 data.ecmwf.int 先試；它說沒有就是還沒出（鏡像只會更晚），只有連線錯誤才換 Google／AWS 鏡像。

**輸出（只留需要的、標明修改）**
- 只留已編號的颱風（編號 70 以上是模式自己生成、還不存在的擾動，雜訊多，不畫）；只留西太平洋看得到的。
- 截到 ECENS_HOURS（預設 240 小時＝10 天）；6 小時一點；座標取到 0.1 度、風速取整數 m/s。
- 每條成員路徑存成 [時數, 緯度×10, 經度×10, 最大風速 m/s, …] 一維陣列（50 組 × 41 點，一顆約 30 KB）。

**失敗處理**（跟 fetch_ens.py 同一套）
- 沒有更新的時次：不寫檔、不紅燈；網路錯：保留上一份、記原因、一小時內不重試。
- 解不開 BUFR：紅燈（程式要改）。氣象署有颱風、ECMWF 卻 48 小時都沒檔：紅燈（網址可能改了）。

需要 eccodes（pip install eccodes，附 ECMWF 的解碼函式庫）；自我測試不需要：
  python3 scripts/fetch_ecens.py selftest            # 離線
  python3 scripts/fetch_ecens.py selftest 檔.bufr     # 另外拿一個真的檔解一次（要有 eccodes）
"""
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

MIRRORS = [m for m in os.environ.get("ECENS_MIRRORS", ",".join([
    "https://data.ecmwf.int/forecasts",
    "https://storage.googleapis.com/ecmwf-open-data",
    "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com",
])).split(",") if m.strip()]
STEPS = [int(s) for s in os.environ.get("ECENS_STEPS", "360,240").split(",") if s.strip()]   # 檔名裡的最長時效，ECMWF 改過
HOURS = int(os.environ.get("ECENS_HOURS", "240"))
MIN_AGE = float(os.environ.get("ECENS_MIN_AGE", "7.5"))
BACK = float(os.environ.get("ECENS_BACK", "48"))
RETRY_MIN = 60
MAX_BYTES = 20_000_000
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.environ.get("ECENS_OUT", os.path.join(ROOT, "data", "ecens.json"))
TYF = os.path.join(ROOT, "data", "typhoon.json")
CITE = ("© {y} European Centre for Medium-Range Weather Forecasts (ECMWF). Source: www.ecmwf.int. "
        "Licence: CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/). "
        "ECMWF does not accept any liability whatsoever for any error or omission in the data, their availability, "
        "or for any loss or damage arising from their use.")
MODS = "只留已編號颱風、西太平洋、起報後 {h} 小時內；6 小時一點，座標取到 0.1 度、風速取整數。"
MISSING = -1e99


def iso(t):
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s):
    t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def urls(mirror, t):
    d = f"{t:%Y%m%d}/{t:%H}z/ifs/0p25/enfo"
    return [f"{mirror}/{d}/{t:%Y%m%d%H}0000-{s}h-enfo-tf.bufr" for s in STEPS]


def candidates(now, prev_init):
    t = now - timedelta(hours=MIN_AGE)
    t = t.replace(minute=0, second=0, microsecond=0) - timedelta(hours=t.hour % 12)
    out = []
    while (now - t).total_seconds() / 3600 <= BACK:
        if prev_init and t <= prev_init:
            break
        out.append(t)
        t -= timedelta(hours=12)
    return out


class Bad(Exception):
    """抓到了但解不開：程式要改，紅燈。"""


def decode(raw):
    """BUFR → [{"id","name","init","members":[[(h, lat, lon, wind)...], ...]}]。每個訊息是一顆颱風、每個 subset 是一組成員。"""
    import eccodes as ec                        # 只有 Actions 那一步裝了
    out = []
    with tempfile.NamedTemporaryFile(suffix=".bufr") as tf:
        tf.write(raw)
        tf.flush()
        with open(tf.name, "rb") as f:
            while True:
                h = ec.codes_bufr_new_from_file(f)
                if h is None:
                    break
                try:
                    ec.codes_set(h, "unpack", 1)
                    ns = ec.codes_get(h, "numberOfSubsets")

                    def arr(k):
                        # 壓縮的 BUFR：全部成員同值時只回一個，要自己攤開
                        a = list(ec.codes_get_array(h, k))
                        return a * ns if len(a) == 1 else a
                    init = datetime(ec.codes_get(h, "#1#year"), ec.codes_get(h, "#1#month"), ec.codes_get(h, "#1#day"),
                                    ec.codes_get(h, "#1#hour"), tzinfo=timezone.utc)
                    tracks = [[] for _ in range(ns)]
                    # 起報時刻：#2# 是各成員分析場裡的颱風位置，#1# 風速是那時的最大風
                    la, lo, w = arr("#2#latitude"), arr("#2#longitude"), arr("#1#windSpeedAt10M")
                    for j in range(ns):
                        tracks[j].append((0, la[j], lo[j], w[j]))
                    i = 1
                    while True:
                        try:
                            tp = arr(f"#{i}#timePeriod")
                        except Exception:
                            break
                        la, lo = arr(f"#{2 * i + 2}#latitude"), arr(f"#{2 * i + 2}#longitude")
                        w = arr(f"#{i + 1}#windSpeedAt10M")
                        for j in range(ns):
                            tracks[j].append((tp[j], la[j], lo[j], w[j]))
                        i += 1
                    out.append({"id": str(ec.codes_get(h, "#1#stormIdentifier")).strip(),
                                "name": str(ec.codes_get(h, "#1#longStormName")).strip(),
                                "init": init, "members": tracks})
                except Bad:
                    raise
                except Exception as e:
                    raise Bad(f"BUFR 解不開：{e}")
                finally:
                    ec.codes_release(h)
    if not out:
        raise Bad("檔案裡沒有任何颱風訊息")
    return out


def ok(v):
    return v is not None and v > MISSING and v == v


def simplify(storms):
    """只留已編號、西太平洋看得到的颱風；成員路徑截到 HOURS、遇到缺值就結束。"""
    keep = []
    for st in storms:
        m = re.match(r"(\d+)([A-Z])$", st["id"])
        if not m or int(m.group(1)) >= 70:
            continue
        tracks = []
        for tr in st["members"]:
            flat = []
            for h, la, lo, w in tr:
                if h is None or h > HOURS or not (ok(la) and ok(lo)) or abs(la) > 90:
                    break
                flat += [int(round(h)), int(round(la * 10)), int(round((lo % 360) * 10)), int(round(w)) if ok(w) else -1]
            if len(flat) >= 8:                  # 至少兩個點才畫得成線
                tracks.append(flat)
        if not tracks:
            continue
        pts = [(t[k + 1] / 10, t[k + 2] / 10) for t in tracks for k in range(0, len(t), 4)]
        if not any(0 <= la <= 50 and 100 <= lo <= 180 for la, lo in pts):
            continue
        obs = [round(sum(t[1] for t in tracks) / len(tracks) / 10, 1), round(sum(t[2] for t in tracks) / len(tracks) / 10, 1)]
        keep.append({"id": st["id"], "name": st["name"], "obs": obs, "n": len(tracks), "tracks": tracks})
    return keep


def fetch(url, timeout=60):
    """文字以外的 bytes；404 回 None；SlowDown／5xx 退避重試（2、4 秒），其他丟出去。"""
    err = None
    for k in range(3):
        try:
            with urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; data-fetch/1.0)"}), timeout=timeout) as r:
                b = r.read(MAX_BYTES + 1)
            if len(b) > MAX_BYTES:
                raise Bad("檔案大得不像颱風路徑檔")
            if not b.startswith(b"BUFR"):
                raise URLError("回應不是 BUFR（可能是限流的錯誤頁）")
            return b
        except HTTPError as e:
            if e.code in (403, 404):            # S3 鏡像對不存在的檔也回 403
                return None
            err = e
            if e.code < 500 and e.code != 429:
                break
        except (URLError, TimeoutError, OSError) as e:
            err = e
        time.sleep(2 ** (k + 1))
    raise err


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def write(doc):
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, OUT)


def fail(prev, now, msg):
    doc = dict(prev) if prev else {"v": 1, "storms": []}
    doc["error"], doc["tried"] = msg, iso(now)
    write(doc)
    return 1, msg


def run(now, getter=fetch, decoder=decode):
    prev = load(OUT) or {}
    try:
        prev_init = parse_iso(prev["init"]) if prev.get("init") else None
    except Exception:
        prev_init = None
    if prev.get("error") and prev.get("tried"):
        try:
            if (now - parse_iso(prev["tried"])).total_seconds() < RETRY_MIN * 60:
                return 0, "上一輪出錯，一小時內不重試"
        except Exception:
            pass
    cands = candidates(now, prev_init)
    if not cands:
        return 0, "手上已是最新一報"
    errs = []
    for t in cands:
        got = None
        for mi, mirror in enumerate(MIRRORS):
            neterr = False
            for u in urls(mirror, t):
                try:
                    b = getter(u)
                except Bad as e:
                    return fail(prev, now, f"{t:%m/%d %H}Z：{e}")
                except Exception as e:
                    errs.append(f"{mirror.split('/')[2]} {t:%m/%d %H}Z {getattr(e, 'code', '') or type(e).__name__}")
                    neterr = True
                    break
                if b is not None:
                    got = b
                    break
            if got is not None or not neterr:
                break                           # 官方站說沒有＝還沒出；鏡像只會更晚，不必再問
        if got is None:
            continue
        try:
            storms = simplify(decoder(got))
        except Bad as e:
            return fail(prev, now, f"{t:%m/%d %H}Z：{e}")
        write({"v": 1, "src": "ECMWF", "model": "IFS ENS", "init": iso(t), "hours": HOURS,
               "members": max([s["n"] for s in storms] or [0]), "fetched": iso(now), "tried": iso(now), "error": "",
               "storms": storms, "cite": CITE.format(y=t.year), "mods": MODS.format(h=HOURS)})
        return 0, f"{t:%m/%d %H}Z：{len(storms)} 顆（{', '.join(s['id'] + ' ' + s['name'] for s in storms) or '西太平洋沒有已編號颱風'}）"
    if errs:
        doc = dict(prev) if prev else {"v": 1, "storms": []}
        doc["error"], doc["tried"] = "ECMWF 連不上：" + "；".join(errs[:4]), iso(now)
        write(doc)
        return 0, doc["error"]
    ty = load(TYF) or {}
    stale = prev_init is None or (now - prev_init).total_seconds() / 3600 > BACK
    if stale and (ty.get("count") or 0) > 0:
        return fail(prev, now, f"氣象署有颱風，ECMWF 卻 {BACK:.0f} 小時都沒有颱風路徑檔（網址或檔名可能改了）")
    if prev.get("error"):
        doc = dict(prev)
        doc["error"], doc["tried"] = "", iso(now)
        write(doc)
    return 0, "還沒有更新的一報（沒有熱帶氣旋時 ECMWF 不出檔）"


def main():
    code, msg = run(datetime.now(timezone.utc))
    print(("錯誤：" if code else "") + msg)
    return code


# ───────────────────────── 離線自我測試 ─────────────────────────
def selftest(real=None):
    global OUT, TYF
    okn = 0

    def check(cond, msg):
        nonlocal okn
        if not cond:
            print("✗", msg)
            sys.exit(1)
        okn += 1
        print("  ✓", msg)

    now = datetime(2026, 10, 24, 19, 10, tzinfo=timezone.utc)
    c = candidates(now, None)
    check(c[0] == datetime(2026, 10, 24, 0, tzinfo=timezone.utc), "12Z 起報未滿 7.5 小時不試，從 00Z 開始")
    check(len(c) == 3 and all(t.hour in (0, 12) for t in c), "只試 00、12Z（06、18Z 只到 6 天），往回 48 小時（24/00、23/12、23/00）")
    check(candidates(now, c[0]) == [], "手上已是 00Z → 不送請求")
    check(urls(MIRRORS[0], c[0])[0].endswith("20261024/00z/ifs/0p25/enfo/20261024000000-360h-enfo-tf.bufr"), "檔名照 ECMWF 開放資料的規則")

    MISS = -1e100
    def mem(n, lat0, lon0, dlat, dlon, miss_at=None):
        return [(6 * k, MISS if miss_at is not None and k >= miss_at else lat0 + dlat * k,
                 lon0 + dlon * k, 20.0 + k) for k in range(61)]
    fake = [{"id": "37W", "name": "IN-FA", "init": c[0],
             "members": [mem(1, 10.4, 166.4, 0.3, -0.6), mem(2, 10.5, 166.3, 0.4, -0.5, miss_at=30), mem(3, 10.4, 166.4, 0, 0, miss_at=1)]},
            {"id": "72W", "name": "72W", "init": c[0], "members": [mem(1, 15, 140, 0.2, -0.5)]},
            {"id": "22E", "name": "VANCE", "init": c[0], "members": [mem(1, 13, -103, 0.2, 0.1)]}]
    s = simplify(fake)
    check([x["id"] for x in s] == ["37W"], "只留已編號、西太平洋的颱風（模式生成的 72W、東太平洋的 22E 不畫）")
    tr = s[0]["tracks"]
    check(len(tr) == 2 and s[0]["n"] == 2, "只有起報一點的成員畫不成線，丟掉")
    check(tr[0][0::4][-1] == HOURS and len(tr[0]) == 4 * (HOURS // 6 + 1), f"截到 {HOURS} 小時（第 11～15 天不放）")
    check(tr[1][0::4][-1] == 174, "成員遇到缺值（颱風消散）就結束，不畫到 -1e100")
    check(tr[0][1:3] == [104, 1664] and tr[0][3] == 20, "座標取到 0.1 度（×10 存整數）、風速整數 m/s")

    with tempfile.TemporaryDirectory() as d:
        OUT, TYF = os.path.join(d, "ecens.json"), os.path.join(d, "typhoon.json")
        calls = []

        def g(u):
            calls.append(u)
            return b"BUFR..." if "20261024000000" in u and u.startswith("https://data.ecmwf.int") else None
        code, msg = run(now, g, lambda raw: fake)
        doc = load(OUT)
        check(code == 0 and doc["init"] == "2026-10-24T00:00:00Z" and doc["storms"][0]["id"] == "37W", "抓到 00Z、寫出 37W")
        check(doc["cite"].startswith("© 2026 European Centre") and "CC BY 4.0" in doc["cite"] and "0.1 度" in doc["mods"],
              "附 ECMWF 著作權、授權與修改說明（CC BY 4.0 的條件）")
        check(not any("googleapis" in u or "amazonaws" in u for u in calls), "官方站有檔就不去鏡像")
        calls.clear()
        check(run(now + timedelta(minutes=10), g, lambda raw: fake) == (0, "手上已是最新一報") and calls == [], "手上最新：零請求")

        os.remove(OUT)
        calls.clear()

        def gnet(u):
            calls.append(u)
            if u.startswith("https://data.ecmwf.int"):
                raise URLError("timeout")
            return b"BUFR..." if "googleapis" in u and "20261024000000" in u else None
        code, msg = run(now, gnet, lambda raw: fake)
        check(code == 0 and load(OUT)["init"] == "2026-10-24T00:00:00Z" and any("googleapis" in u for u in calls), "官方站連不上 → 改從 Google 鏡像拿")

        os.remove(OUT)
        code, msg = run(now, lambda u: None, lambda raw: fake)
        check(code == 0 and not os.path.exists(OUT) and "不出檔" in msg, "沒檔、氣象署也沒颱風：安靜（沒有熱帶氣旋時本來就不出檔）")
        with open(TYF, "w") as f:
            json.dump({"count": 1}, f)
        code, msg = run(now, lambda u: None, lambda raw: fake)
        check(code == 1 and "網址" in load(OUT)["error"] and "init" not in load(OUT), "氣象署有颱風、ECMWF 48 小時沒檔 → 紅燈並寫原因")
        check(run(now + timedelta(minutes=10), lambda u: None, lambda raw: fake)[0] == 0, "紅燈後一小時內不重試")

        os.remove(OUT)

        def bad(raw):
            raise Bad("BUFR 解不開：測試")
        code, msg = run(now, g, bad)
        check(code == 1 and "解不開" in load(OUT)["error"], "解不開 → 紅燈、原因寫進檔裡")

    if real:
        st = decode(open(real, "rb").read())
        s = simplify(st)
        check(len(st) > 0 and all(len(x["members"]) > 0 for x in st), f"真檔解得開：{len(st)} 顆（含模式生成）")
        check(all(0 <= t[k + 1] / 10 <= 60 for x in s for t in x["tracks"] for k in range(0, len(t), 4)),
              f"真檔留下 {len(s)} 顆：{', '.join(x['id'] + ' ' + x['name'] + ' ' + str(x['n']) + ' 組' for x in s)}")
    print(f"全部通過（{okn} 項）")
    return 0


if __name__ == "__main__":
    a = sys.argv[1:]
    sys.exit(selftest(a[1] if len(a) > 1 else None) if a[:1] == ["selftest"] else main())
