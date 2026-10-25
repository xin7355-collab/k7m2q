#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""交通部 TDX 的道路監視器清單（國道、省道、各縣市）→ data/cctv.json，給「即時影像 › 監視器」地圖用。

**為什麼走 TDX、不抓 tw.live**
使用者想在颱風時從地圖點監視器看即時畫面（10/25 貼了 tw.live 的連結）。tw.live 是民間網站：
網頁設了 X-Frame-Options: SAMEORIGIN 不能內嵌，FAQ 也寫「若需嵌入、轉載……請先確認原始來源單位的資料授權」。
畫面本來就是各機關公開的，TDX（運輸資料流通服務）把清單連同座標、影像網址整理好，政府資料開放授權，可以用。
所以清單從 TDX 來、畫面直接連原機關；tw.live 只做外部連結（「附近影像」）。

**額度**
不帶金鑰：每個 IP 每天 20 次（2026-10-25 實測，回應標頭 X-RateLimit-Limit-Day: 20）。要抓 24 份（國道、省道、22 縣市），
一輪抓不完：每輪最多 CCTV_MAX_CALLS 份，先抓最久沒更新的；遇到 429（額度用完）就停、已抓的照樣寫出去，下一輪接著抓。
清單一週才變幾支，這樣兩輪就換完一遍。
- 400：那個縣市在 TDX 沒有監視器資料（實測苗栗、嘉義縣、花蓮回 400），記下來 30 天內不再問。
- 其他錯誤：保留那一份舊清單、記原因；全部都錯才紅燈。

**只留手機看得到的**
網站是 https，http 的影像會被瀏覽器擋（混合內容）；192.168.x 這類內網位址外面連不到；沒座標或座標跑到臺灣外的畫不上地圖。
這些直接濾掉，並記下濾掉幾支。

**播放網頁 → 直連影像（v92）**
有些縣市給的「影像網址」其實是機關自己的播放網頁，手機 App 裡的 <img> 播不了（使用者 10/25 回報只有國道看得到）。
網頁裡寫死真正 MJPEG 網址的（臺中：motoretag…/Showcctv?id=… 裡的 tcnvrN.taichung.gov.tw/…），後端先解析好放第 6 欄；
解析不到的前端直接給「在機關網頁開」的連結。每輪都重新解析（網址可能換 NVR 主機），抓不到的沿用上一輪的結果。

輸出 data/cctv.json：
  {"v":1, "updated":"…", "src":"TDX", "groups":[{"k":"Freeway","name":"國道","at":"…","n":1811,"err":"","none":false,
     "cams":[[緯, 經, "名稱", "影像網址", "截圖網址或空字串"(, "解析出的直連影像")], …]}, …],
   "dropped":{"http":0,"private":13,"nogeo":18}, "resolved":{"ok":309,"fail":0,"kept":0}}

離線自我測試：python3 scripts/fetch_cctv.py selftest
"""
import ipaddress
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

BASE = os.environ.get("CCTV_BASE", "https://tdx.transportdata.tw/api/basic/v2/Road/Traffic/CCTV/")
MAX_CALLS = int(os.environ.get("CCTV_MAX_CALLS", "18"))       # 留兩次給同一個 IP 上別人的呼叫
NONE_DAYS = 30
OUT = os.environ.get("CCTV_OUT", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "cctv.json"))
TW = timezone(timedelta(hours=8))
CITIES = [("Taipei", "臺北市"), ("NewTaipei", "新北市"), ("Taoyuan", "桃園市"), ("Taichung", "臺中市"), ("Tainan", "臺南市"),
          ("Kaohsiung", "高雄市"), ("Keelung", "基隆市"), ("Hsinchu", "新竹市"), ("HsinchuCounty", "新竹縣"),
          ("MiaoliCounty", "苗栗縣"), ("ChanghuaCounty", "彰化縣"), ("NantouCounty", "南投縣"), ("YunlinCounty", "雲林縣"),
          ("ChiayiCounty", "嘉義縣"), ("Chiayi", "嘉義市"), ("PingtungCounty", "屏東縣"), ("YilanCounty", "宜蘭縣"),
          ("HualienCounty", "花蓮縣"), ("TaitungCounty", "臺東縣"), ("KinmenCounty", "金門縣"), ("PenghuCounty", "澎湖縣"),
          ("LienchiangCounty", "連江縣")]
SOURCES = [("Freeway", "國道"), ("Highway", "省道")] + [("City/" + k, v) for k, v in CITIES]
# 臺灣本島、離島（含金門、馬祖、東沙）的大框；跑出去的多半是經緯度填反或填 0
BOX = (20.4, 26.6, 116.5, 122.2)
# 播放網頁的網址樣式 → 網頁裡直連影像的抓法。只收網頁裡明寫的 https 影像網址，不猜
RESOLVERS = [
    (re.compile(r"^https://motoretag\.taichung\.gov\.tw/ATIS_TCC/Device/Showcctv\?id=\w+$"),
     re.compile(r"<img[^>]+src=[\"'](https://[^\"']+)[\"']", re.I)),
]
RESOLVE_SEC = float(os.environ.get("CCTV_RESOLVE_SEC", "240"))   # 解析的時間預算；超過的留給下一輪


def now_iso():
    return datetime.now(TW).isoformat(timespec="seconds")


def parse_iso(s):
    try:
        return datetime.fromisoformat(str(s))
    except Exception:
        return None


def https_ok(u):
    """手機看得到才留：https、不是內網位址。回傳 (ok, 原因)。"""
    if not u:
        return False, "nourl"
    try:
        p = urlsplit(u)
    except ValueError:
        return False, "nourl"
    if p.scheme != "https" or not p.hostname:
        return False, "http"
    try:
        if ipaddress.ip_address(p.hostname).is_private:
            return False, "private"
    except ValueError:
        pass                                    # 是網域名稱，不是 IP
    return True, ""


def cam_name(c):
    """路名＋說明＋里程＋路段，去掉重複；太長截斷（地圖上的提示放不下）。"""
    parts = []
    for k in ("RoadName", "SurveillanceDescription", "LocationMile"):
        v = str(c.get(k) or "").strip()
        if v and not any(v in x or x in v for x in parts):
            parts.append(v)
    rs = c.get("RoadSection") or {}
    if isinstance(rs, dict) and (rs.get("Start") or rs.get("End")):
        seg = f"{rs.get('Start') or ''}→{rs.get('End') or ''}".strip("→")
        if seg and not any(seg in x for x in parts):
            parts.append(f"（{seg}）")
    name = " ".join(parts) or str(c.get("CCTVID") or "監視器")
    return name[:48]


def convert(payload, dropped):
    """TDX 回應 → [[緯, 經, 名稱, 影像, 截圖], …]；濾掉的記在 dropped。"""
    out = []
    for c in (payload or {}).get("CCTVs") or []:
        try:
            la, lo = float(c.get("PositionLat")), float(c.get("PositionLon"))
        except (TypeError, ValueError):
            dropped["nogeo"] += 1
            continue
        if not (BOX[0] <= la <= BOX[1] and BOX[2] <= lo <= BOX[3]):
            dropped["nogeo"] += 1
            continue
        vid, img = c.get("VideoStreamURL") or "", c.get("VideoImageURL") or ""
        ok_v, why_v = https_ok(vid)
        ok_i, why_i = https_ok(img)
        if not ok_v and not ok_i:
            dropped[why_v if why_v != "nourl" else why_i if why_i != "nourl" else "http"] += 1
            continue
        out.append([round(la, 5), round(lo, 5), cam_name(c), vid if ok_v else "", img if ok_i else ""])
    return out


def fetch(path, timeout=60):
    """回傳 (status, payload 或 None)。429／400 交給呼叫端判斷；網路錯丟出去。"""
    req = Request(BASE + path + "?%24format=JSON", headers={"User-Agent": "Mozilla/5.0 (compatible; data-fetch/1.0)",
                                                           "Accept": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read(40_000_000))
    except HTTPError as e:
        if e.code in (400, 404, 429):
            return e.code, None
        raise


def fetch_page(u, timeout=15):
    req = Request(u, headers={"User-Agent": "Mozilla/5.0 (compatible; data-fetch/1.0)"})
    with urlopen(req, timeout=timeout) as r:
        return r.read(200_000).decode("utf-8", "replace")


def resolver_for(u):
    for pat, rx in RESOLVERS:
        if u and pat.match(u):
            return rx
    return None


def resolve(groups, prev_play, page_get=fetch_page, clock=time.monotonic, budget=None):
    """播放網頁解析成直連影像，寫進每支的第 6 欄。回傳 {"ok":…, "fail":…, "kept":…}。
    先解析沒有舊結果的，再更新有舊結果的；超過時間預算就停（沿用舊結果），不讓整個工作逾時白做。"""
    budget = RESOLVE_SEC if budget is None else budget
    todo = []
    for g in groups:
        for c in g.get("cams") or []:
            rx = resolver_for(c[3])
            if not rx:
                continue
            del c[5:]
            old = prev_play.get(c[3], "")
            c.append(old)
            todo.append((bool(old), c, rx))
    todo.sort(key=lambda t: t[0])                    # 沒有舊結果的先
    st = {"ok": 0, "fail": 0, "kept": 0}
    end = clock() + budget
    for had, c, rx in todo:
        if clock() > end:
            st["kept" if had else "fail"] += 1
            continue
        try:
            m = rx.search(page_get(c[3]))
            play = m.group(1) if m else ""
        except Exception:
            play = ""
        if play and https_ok(play)[0]:
            c[5] = play
            st["ok"] += 1
        elif had:
            st["kept"] += 1                          # 這次抓不到，沿用上一輪的
        else:
            st["fail"] += 1
    for g in groups:                                 # 解析不出來的不留空欄
        for c in g.get("cams") or []:
            if len(c) > 5 and not c[5]:
                del c[5:]
    return st


def load_prev():
    try:
        with open(OUT, encoding="utf-8") as f:
            d = json.load(f)
        return d if d.get("v") == 1 else None
    except Exception:
        return None


def order(groups, now):
    """這一輪要抓哪幾份：沒抓過的先、再來是最久沒更新的；30 天內確認過「沒有資料」的跳過。"""
    def key(k):
        g = groups.get(k) or {}
        t = parse_iso(g.get("at")) if g.get("at") else None
        return t.timestamp() if t else 0
    todo = []
    for k, _ in SOURCES:
        g = groups.get(k) or {}
        if g.get("none"):
            t = parse_iso(g.get("at"))
            if t and (now - t).days < NONE_DAYS:
                continue
        todo.append(k)
    return sorted(todo, key=key)


def run(now=None, getter=fetch, sleep=time.sleep, page_get=fetch_page):
    now = now or datetime.now(TW)
    prev = load_prev() or {}
    groups = {g["k"]: g for g in prev.get("groups") or [] if g.get("k")}
    prev_play = {c[3]: c[5] for g in groups.values() for c in g.get("cams") or [] if len(c) > 5 and c[5]}
    dropped = {"http": 0, "private": 0, "nogeo": 0}
    names = dict(SOURCES)
    tried = ok = 0
    errs = []
    for k in order(groups, now)[:MAX_CALLS]:
        tried += 1
        g = dict(groups.get(k) or {"k": k, "name": names[k], "cams": []})
        g["name"] = names[k]
        try:
            st, payload = getter(k)
        except Exception as e:                  # 網路錯：保留舊的那份
            g["err"] = f"{type(e).__name__}"
            errs.append(f"{names[k]}：{g['err']}")
            groups[k] = g
            continue
        if st == 429:
            print(f"額度用完（已抓 {ok} 份），剩下的下一輪再抓")
            tried -= 1
            break
        g["at"] = now.isoformat(timespec="seconds")
        if st in (400, 404):
            g.update({"none": True, "err": "", "cams": [], "n": 0})
        else:
            cams = convert(payload, dropped)
            g.update({"none": False, "err": "", "cams": cams, "n": len(cams)})
            ok += 1
        groups[k] = g
        sleep(1)                                # 一秒一次，別一口氣打滿
    gl = [groups[k] for k, _ in SOURCES if k in groups]
    total = sum(len(g.get("cams") or []) for g in gl)
    if tried and not ok and errs and not any(g.get("cams") for g in gl):
        print("錯誤：全部抓不到，而且手上也沒有舊清單：" + "；".join(errs[:5]))
        return 1
    rs = resolve(gl, prev_play, page_get)
    if rs["fail"] or rs["kept"]:
        # 解析不到不算壞掉：前端會改給「在機關網頁開」的連結。只在紀錄裡講幾支
        print(f"播放網頁解析：成功 {rs['ok']}、沿用上一輪 {rs['kept']}、解析不到 {rs['fail']}（這些在 App 裡改成外連）")
    doc = {"v": 1, "src": "TDX", "updated": now_iso(), "total": total, "dropped": dropped, "resolved": rs, "groups": gl,
           "cite": "監視器清單：交通部 TDX 運輸資料流通服務（政府資料開放授權條款）。即時畫面由各來源機關提供。"}
    # 清單沒變也要寫：每份的抓取時間決定下一輪輪到誰，不寫的話永遠是同一批被重抓、另一批永遠不更新。
    # 一週只跑兩輪，多兩個 commit 沒關係
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT + ".tmp", "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(OUT + ".tmp", OUT)
    print(f"寫出 {total} 支（這一輪抓 {ok} 份；濾掉 http {dropped['http']}、內網 {dropped['private']}、沒座標 {dropped['nogeo']}）"
          + (f"；抓不到：{'；'.join(errs)}" if errs else ""))
    return 0


def selftest():
    global OUT, MAX_CALLS
    import tempfile
    okn = 0

    def check(cond, msg):
        nonlocal okn
        if not cond:
            print("✗", msg)
            sys.exit(1)
        okn += 1
        print("  ✓", msg)

    fw = {"CCTVs": [
        {"CCTVID": "A", "VideoStreamURL": "https://cctvn.freeway.gov.tw/abs2mjpg/bmjpg?camera=10000", "PositionLat": 25.1229931,
         "PositionLon": 121.735695, "RoadName": "國道1號", "LocationMile": "0K+000", "RoadSection": {"Start": "基隆端", "End": "基隆交流道"}},
        {"CCTVID": "B", "VideoStreamURL": "http://old.example.gov.tw/x", "PositionLat": 25.0, "PositionLon": 121.5},
        {"CCTVID": "C", "VideoStreamURL": "https://192.168.1.91/x", "PositionLat": 25.0, "PositionLon": 121.5},
        {"CCTVID": "D", "VideoStreamURL": "https://a.gov.tw/x", "PositionLat": 0, "PositionLon": 0},
        {"CCTVID": "E", "VideoStreamURL": None, "VideoImageURL": "https://nvr1.hccg.gov.tw/zms?monitor=68", "PositionLat": "24.8193",
         "PositionLon": "120.9956", "RoadName": "台68線", "SurveillanceDescription": "中華路與頭前溪(上台68)"},
        {"CCTVID": "F", "VideoStreamURL": "https://x.gov.tw/y", "PositionLat": 24.43, "PositionLon": 118.32, "RoadName": "金門"}]}
    d = {"http": 0, "private": 0, "nogeo": 0}
    cams = convert(fw, d)
    check(len(cams) == 3 and d == {"http": 1, "private": 1, "nogeo": 1}, f"濾掉 http、內網、沒座標各 1（留 3 支，含金門）：{d}")
    check(cams[0][2] == "國道1號 0K+000 （基隆端→基隆交流道）", f"名稱合併路名、里程、路段：{cams[0][2]}")
    check(cams[1][3] == "" and cams[1][4].startswith("https://nvr1"), "只有截圖網址的也留（影像欄空）")
    check(cams[0][0] == 25.12299 and cams[0][1] == 121.7357, "座標取到小數 5 位")

    now = datetime(2026, 10, 25, 20, 0, tzinfo=TW)
    with tempfile.TemporaryDirectory() as tmp:
        OUT = os.path.join(tmp, "cctv.json")
        calls = []

        def g1(k):
            calls.append(k)
            if k == "City/MiaoliCounty":
                return 400, None
            if len(calls) > 5:
                return 429, None
            return 200, fw
        MAX_CALLS = 18
        check(run(now, g1, lambda s: None) == 0, "第一輪：抓 5 份後額度用完，照樣寫出")
        doc = load_prev()
        got = [g["k"] for g in doc["groups"] if g.get("at")]
        check(len(got) == 5 and doc["total"] == 15, f"已抓的 5 份寫進去（{got}）")
        check(doc["cite"].startswith("監視器清單：交通部 TDX"), "附出處")
        calls.clear()

        def g2(k):
            calls.append(k)
            return (400, None) if k == "City/MiaoliCounty" else (200, fw)
        run(now + timedelta(hours=12), g2, lambda s: None)
        check(calls[0] not in got and "Freeway" not in calls[:5], "第二輪先抓沒抓過的，不重抓剛抓過的")
        doc = load_prev()
        check(sum(1 for g in doc["groups"] if g.get("at")) == 5 + MAX_CALLS, f"第二輪再抓 {MAX_CALLS} 份")
        run(now + timedelta(hours=24), g2, lambda s: None)
        doc = load_prev()
        check(all(g.get("at") for g in doc["groups"]) and len(doc["groups"]) == len(SOURCES), "第三輪補完，24 份都有")
        mi = next(g for g in doc["groups"] if g["k"] == "City/MiaoliCounty")
        check(mi["none"] and mi["n"] == 0, "400＝那個縣市沒有資料，記成 none")
        calls.clear()
        run(now + timedelta(days=3), g2, lambda s: None)
        check("City/MiaoliCounty" not in calls and len(calls) == MAX_CALLS, "沒有資料的縣市 30 天內不再問")

        def gerr(k):
            raise URLError("timeout")
        before = open(OUT, encoding="utf-8").read()
        check(run(now + timedelta(days=4), gerr, lambda s: None) == 0, "網路錯但手上有舊清單：不紅燈")
        doc = load_prev()
        check(doc["total"] == json.loads(before)["total"], "網路錯：舊清單照留，支數不變")
        os.remove(OUT)
        check(run(now, gerr, lambda s: None) == 1 and not os.path.exists(OUT), "全部抓不到、也沒有舊清單：紅燈，不交空檔")

        MAX_CALLS = 3
        calls.clear()
        run(now, g2, lambda s: None)
        check(len(calls) == 3, "每輪最多 CCTV_MAX_CALLS 份（留額度給別人）")

        # 播放網頁解析（臺中）：網頁裡的 <img src> 變成第 6 欄；抓不到沿用上一輪；超過時間預算留給下一輪
        tc = {"CCTVs": [{"CCTVID": f"T{i}", "VideoStreamURL": f"https://motoretag.taichung.gov.tw/ATIS_TCC/Device/Showcctv?id=C00000{i}",
                         "PositionLat": 24.1 + i / 100, "PositionLon": 120.6} for i in range(1, 4)]
              + [{"CCTVID": "K", "VideoStreamURL": "https://CCTV.KLCG.GOV.TW:443/9cc4ca1c", "PositionLat": 25.1, "PositionLon": 121.7}]}
        page = '<td>07067中清路</td><img style="x" width=\'352\' src="https://tcnvr8.taichung.gov.tw/3d7db0{n}">'
        pages = []

        def pg_ok(u):
            pages.append(u)
            n = u[-1]
            if n == "3":
                return "<html>維護中</html>"
            return page.format(n=n)
        os.remove(OUT)
        MAX_CALLS = 30
        run(now, lambda k: (200, tc) if k == "City/Taichung" else (400, None), lambda s: None, pg_ok)
        doc = load_prev()
        tcg = next(g for g in doc["groups"] if g["k"] == "City/Taichung")
        check([c[5] if len(c) > 5 else None for c in tcg["cams"]] == ["https://tcnvr8.taichung.gov.tw/3d7db01",
              "https://tcnvr8.taichung.gov.tw/3d7db02", None, None], f"臺中播放網頁解析出 MJPEG 直連；解析不到、不是網頁的不加欄：{tcg['cams']}")
        check(doc["resolved"] == {"ok": 2, "fail": 1, "kept": 0} and len(pages) == 3, f"只解析符合樣式的網頁、計數正確：{doc['resolved']}")

        def pg_down(u):
            raise URLError("timeout")
        run(now + timedelta(days=4), lambda k: (429, None), lambda s: None, pg_down)
        doc = load_prev()
        tcg = next(g for g in doc["groups"] if g["k"] == "City/Taichung")
        check(tcg["cams"][0][5] == "https://tcnvr8.taichung.gov.tw/3d7db01" and doc["resolved"]["kept"] == 2,
              "網頁這次抓不到（TDX 也額度用完）：沿用上一輪解析的直連")
        check(resolve([{"cams": [[0, 0, "x", "https://motoretag.taichung.gov.tw/ATIS_TCC/Device/Showcctv?id=C1", ""]]}], {},
                      lambda u: '<img src="http://tcnvr8.taichung.gov.tw/a">')["fail"] == 1, "解析出 http（手機會擋）不收")
        tick = iter(range(0, 1000, 100))
        st = resolve([{"cams": [[0, 0, "x", f"https://motoretag.taichung.gov.tw/ATIS_TCC/Device/Showcctv?id=C{i}", ""] for i in range(5)]}],
                     {}, lambda u: page.format(n=1), clock=lambda: next(tick), budget=250)
        check(st["ok"] == 2 and st["fail"] == 3, f"超過時間預算就停，剩下的留給下一輪：{st}")
    print(f"全部通過（{okn} 項）")
    return 0


if __name__ == "__main__":
    sys.exit(selftest() if sys.argv[1:] == ["selftest"] else run())
