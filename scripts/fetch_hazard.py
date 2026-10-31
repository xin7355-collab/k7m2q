#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""地震報告、海嘯資訊（中央氣象署）＋民生示警（國家災害防救科技中心 CAP）→ data/hazard.json，給「示警」頁。

**為什麼要後端**
- 氣象署地震、海嘯資料集有 CORS，前端填了個人授權碼就能即時直連；這份是給**沒填授權碼的同事**的退路。
- 國家災防中心的民生示警平台（水庫放流、淹水、土石流、道路封閉、強風、地震…各機關的 CAP）**沒有 CORS**，
  瀏覽器不能直接讀，只能後端抓（前端另外試轉發服務，抓不到才用這份）。
- 工作流排程實際 1～6 小時才跑一次，所以畫面一律標資料時間，不假裝即時。

**來源（2026-10-31 從 Actions 實測）**
- E-A0015-001 顯著有感地震報告、E-A0016-001 小區域有感地震報告：每筆有震央、深度、規模、各縣市最大震度、
  逐站震度與測站座標（震度擴散動畫要這個）。資料集只留約兩個月、各十幾筆；小區域報告的編號一律是 115000，
  不能拿編號當鍵。
- E-A0014-001 海嘯資訊：海嘯消息／警訊／警報／警報解除；有威脅時 TsunamiWave.WarningArea 是六個沿海分區的
  預估到達時間與浪高，TsuStation 是潮位站實測。
- 民生示警 JSONAtomFeed.ashx：最近一週的 CAP 摘要（含已過期、已解除的），平台限制同一來源 3 秒一次。

**只留畫面用得到的欄位**：PGA／PGV 分量、波形圖網址都丟掉（一筆從 9 KB 剩 3 KB）。解析（震度換算、沿海分區、
示警範圍）只在前端一份——前端即時直連拿到的是同樣的原始格式。

**失敗時**：一個來源抓不到就沿用上一份的那一段並記下原因；沒設授權碼是「還沒設定」，安靜寫 hasKey:false、
示警照抓（不用金鑰）。全部都抓不到、手上也沒有舊檔才紅燈，不交空檔。

氣象署的憑證缺 Subject Key Identifier，Python 3.13 起預設的嚴格檢查會擋（2026-10-31 實測），
這裡只關掉那一項嚴格規則，憑證鏈與主機名照樣驗。

離線自我測試：python3 scripts/fetch_hazard.py selftest
"""
import json
import os
import ssl
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

KEY = os.environ.get("CWA_API_KEY", "").strip()
OUT = os.environ.get("HAZARD_OUT", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "hazard.json"))
CWA = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/"
NCDR = os.environ.get("NCDR_FEED", "https://alerts.ncdr.nat.gov.tw/JSONAtomFeed.ashx")
TW = timezone(timedelta(hours=8))
UA = "Mozilla/5.0 (compatible; data-fetch/1.0)"
LIMITS = {"E-A0015-001": 30, "E-A0016-001": 30, "E-A0014-001": 40}

CTX = ssl.create_default_context()
CTX.verify_flags &= ~getattr(ssl, "VERIFY_X509_STRICT", 0)


def get(url, timeout=25):
    with urlopen(Request(url, headers={"User-Agent": UA, "Accept": "application/json"}), timeout=timeout, context=CTX) as r:
        return r.read().decode("utf-8")


def get_json(url, getter, sleep, tries=3):
    """429／5xx／網路錯退避重試（2、4 秒；平台說 3 秒一次，429 至少等 3.5 秒）。"""
    for i in range(tries):
        try:
            return json.loads(getter(url))
        except HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or i == tries - 1:
                raise
            sleep(max(3.5, 2 ** (i + 1)))
        except (URLError, TimeoutError):
            if i == tries - 1:
                raise
            sleep(2 ** (i + 1))


def cwa_url(ds):
    # 氣象署的 datastore 不收標頭帶的授權碼（2026-10-31 實測預檢回 401），只能放查詢字串；這網址不印出、不寫進檔案
    return CWA + ds + "?" + urlencode({"Authorization": KEY, "format": "JSON", "limit": LIMITS[ds]})


def _clean(o, keys):
    return {k: o[k] for k in keys if isinstance(o, dict) and o.get(k) not in (None, "")}


def trim_eq(r, small):
    """地震報告只留畫面要的：震央、規模、縣市震度、逐站震度與座標、官方網頁與報告圖。"""
    areas = []
    for a in ((r.get("Intensity") or {}).get("ShakingArea") or []):
        sts = [_clean(s, ("StationName", "StationID", "StationLatitude", "StationLongitude", "SeismicIntensity", "EpicenterDistance"))
               for s in a.get("EqStation") or []]
        areas.append({**_clean(a, ("AreaDesc", "CountyName", "AreaIntensity", "InfoStatus")), "EqStation": sts})
    out = _clean(r, ("EarthquakeNo", "IssueTime", "ReportType", "ReportColor", "ReportContent", "ReportImageURI", "ShakemapImageURI", "Web"))
    out["EarthquakeInfo"] = r.get("EarthquakeInfo") or {}
    out["Intensity"] = {"ShakingArea": areas}
    out["_s"] = 1 if small else 0
    return out


def trim_tsu(r):
    out = _clean(r, ("IssueTime", "ValidTime", "ReportColor", "ReportContent", "ReportNo", "ReportType", "TsunamiNo", "Web"))
    out["EarthquakeInfo"] = r.get("EarthquakeInfo") or {}
    if r.get("TsunamiWave"):
        out["TsunamiWave"] = r["TsunamiWave"]
    return out


def trim_cap(e):
    """CAP 摘要攤平成字串欄位。生效／失效時間是「2026/10/31 上午 11:28:40」這種寫法，原樣留給前端解析。"""
    g = lambda o, *ks: next((o[k] for k in ks if isinstance(o, dict) and o.get(k) not in (None, "")), "")
    return {
        "id": g(e, "id"), "title": g(e, "title"), "updated": g(e, "updated"),
        "author": g(e.get("author") or {}, "name"),
        "link": g(e.get("link") or {}, "@href", "href"),
        "summary": g(e.get("summary") or {}, "#text", "text") if isinstance(e.get("summary"), dict) else str(e.get("summary") or ""),
        "category": g(e.get("category") or {}, "@term", "term") if isinstance(e.get("category"), dict) else str(e.get("category") or ""),
        "msgType": g(e, "msgType"), "effective": g(e, "effective"), "expires": g(e, "expires"),
    }


def eq_key(r):
    i = r.get("EarthquakeInfo") or {}
    p = i.get("Epicenter") or {}
    return f'{i.get("OriginTime")}|{p.get("EpicenterLatitude")}|{p.get("EpicenterLongitude")}'


def load_prev():
    try:
        with open(OUT, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def run(getter=get, sleep=time.sleep, now=None):
    now = now or datetime.now(TW)
    prev = load_prev() or {}
    err, got, bad_key = {}, 0, False
    eq, tsu = None, None
    if KEY:
        try:
            big = get_json(cwa_url("E-A0015-001"), getter, sleep)
            small = get_json(cwa_url("E-A0016-001"), getter, sleep)
            rows = [trim_eq(r, False) for r in (big.get("records") or {}).get("Earthquake") or []]
            rows += [trim_eq(r, True) for r in (small.get("records") or {}).get("Earthquake") or []]
            seen, eq = set(), []
            for r in sorted(rows, key=lambda r: (r.get("EarthquakeInfo") or {}).get("OriginTime") or "", reverse=True):
                k = eq_key(r)
                if k not in seen:
                    seen.add(k)
                    eq.append(r)
            got += 1
        except HTTPError as e:
            # 授權碼被拒：紅燈提醒換 Secret，但示警不用金鑰，照樣抓完寫出
            bad_key = e.code in (401, 403)
            err["eq"] = "授權碼被拒" if bad_key else f"HTTP {e.code}"
        except Exception as e:                                   # 網路、格式：沿用上一份
            err["eq"] = type(e).__name__
        try:
            if bad_key:
                raise HTTPError("", 401, "授權碼被拒", {}, None)
            t = get_json(cwa_url("E-A0014-001"), getter, sleep)
            tsu = [trim_tsu(r) for r in (t.get("records") or {}).get("Tsunami") or []]
            got += 1
        except HTTPError as e:
            err["tsu"] = f"HTTP {e.code}"
        except Exception as e:
            err["tsu"] = type(e).__name__
    try:
        sleep(0)
        f = get_json(NCDR, getter, sleep)
        entries = f.get("entry") or []
        if isinstance(entries, dict):                            # 只有一則時是物件不是陣列
            entries = [entries]
        cap = {"fetched": now.isoformat(timespec="seconds"), "entries": [trim_cap(e) for e in entries if isinstance(e, dict)]}
        got += 1
    except Exception as e:
        cap = None
        err["cap"] = f"HTTP {e.code}" if isinstance(e, HTTPError) else type(e).__name__

    if eq is None:
        eq = prev.get("eq") or []
    if tsu is None:
        tsu = prev.get("tsu") or []
    if cap is None:
        cap = prev.get("cap") or {"fetched": "", "entries": []}
    if not got and not (prev.get("eq") or (prev.get("cap") or {}).get("entries")):
        print("::error::地震、海嘯、示警全部抓不到，手上也沒有舊檔：不寫空檔。原因：" + json.dumps(err, ensure_ascii=False))
        return 1
    doc = {"v": 1, "updated": now.isoformat(timespec="seconds"), "hasKey": bool(KEY), "eq": eq, "tsu": tsu, "cap": cap,
           "err": err,
           "cite": "地震、海嘯：中央氣象署開放資料；示警：國家災害防救科技中心民生示警公開資料平台（各機關 CAP）"}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, OUT)
    print(f"寫出 {OUT}：地震 {len(eq)} 筆、海嘯 {len(tsu)} 報、示警 {len(cap['entries'])} 則"
          + ("" if KEY else "（沒有授權碼：只抓示警）") + (f"；沒抓到的沿用上一份：{err}" if err else ""))
    if bad_key:
        print("::error::氣象署授權碼被拒：到 repo Settings › Secrets 換 CWA_API_KEY（示警已照常寫出）")
        return 1
    return 0


def selftest():
    global OUT, KEY
    import tempfile
    okn = 0

    def check(cond, msg):
        nonlocal okn
        if not cond:
            print("✗", msg)
            sys.exit(1)
        okn += 1
        print("  ✓", msg)

    st = {"pga": {"EWComponent": 1}, "pgv": {}, "StationName": "和平", "StationID": "EHP", "InfoStatus": "observe",
          "BackAzimuth": 150.6, "EpicenterDistance": 17.17, "SeismicIntensity": "3級", "StationLatitude": 24.309,
          "StationLongitude": 121.749, "WaveImageURI": "https://x/3-EHP.gif"}
    big = {"EarthquakeNo": 115074, "IssueTime": "2026-10-30T04:40:25+08:00", "ReportColor": "綠色",
           "ReportContent": "10/30-04:37臺灣東部海域發生規模4.4有感地震", "Web": "https://scweb.cwa.gov.tw/x",
           "ReportImageURI": "https://scweb.cwa.gov.tw/a.png", "ReportRemark": "長長的備註",
           "EarthquakeInfo": {"OriginTime": "2026-10-30T04:37:43+08:00", "FocalDepth": 22.7,
                              "Epicenter": {"Location": "花蓮縣政府東北方  29.6  公里", "EpicenterLatitude": 24.17, "EpicenterLongitude": 121.83},
                              "EarthquakeMagnitude": {"MagnitudeType": "芮氏規模", "MagnitudeValue": 4.4}},
           "Intensity": {"ShakingArea": [{"AreaDesc": "花蓮縣地區", "CountyName": "花蓮縣", "InfoStatus": "observe", "AreaIntensity": "3級", "EqStation": [st]},
                                         {"AreaDesc": "最大震度3級地區", "CountyName": "花蓮縣、宜蘭縣", "AreaIntensity": "3級"}]}}
    small = json.loads(json.dumps(big))
    small["EarthquakeNo"] = 115000
    small["EarthquakeInfo"]["OriginTime"] = "2026-10-28T14:19:23+08:00"
    dup = json.loads(json.dumps(big))                          # 兩個資料集偶爾會有同一個地震
    t = trim_eq(big, False)
    s0 = t["Intensity"]["ShakingArea"][0]["EqStation"][0]
    check("pga" not in s0 and "WaveImageURI" not in s0 and s0["StationLatitude"] == 24.309 and s0["SeismicIntensity"] == "3級",
          "地震：丟掉 PGA／波形圖，留測站座標與震度")
    check(t["_s"] == 0 and "ReportRemark" not in t and t["Intensity"]["ShakingArea"][1]["EqStation"] == []
          and t["Intensity"]["ShakingArea"][1]["CountyName"] == "花蓮縣、宜蘭縣", "沒有測站的「最大震度 N 級地區」也留著")
    tsu_warn = {"IssueTime": "2025-07-30T11:40:00+08:00", "ValidTime": {"EndTime": "2025-07-30T19:40:00+08:00"}, "ReportColor": "紅色",
                "ReportContent": "…", "ReportNo": "第4報", "ReportType": "海嘯警報", "TsunamiNo": 114008, "Web": "https://x",
                "EarthquakeInfo": {"OriginTime": "2025-07-30T07:24:00+08:00", "Epicenter": {"Location": "俄羅斯 堪察加半島東部外海",
                                   "EpicenterLatitude": 52.5, "EpicenterLongitude": 160.3}, "EarthquakeMagnitude": {"MagnitudeValue": 8.7}},
                "TsunamiWave": {"WarningArea": [{"AreaColor": "黃色", "AreaName": "東南沿海地區", "ArrivalTime": "2025-07-30T13:19:00+08:00",
                                                 "WaveHeight": "0.3至1公尺", "InfoStatus": "predict"}], "TsuStation": []}}
    tt = trim_tsu(tsu_warn)
    check(tt["TsunamiWave"]["WarningArea"][0]["ArrivalTime"] == "2025-07-30T13:19:00+08:00" and tt["ValidTime"]["EndTime"].startswith("2025-07-30T19"),
          "海嘯：分區到達時間、浪高、有效時間都留著")
    cap_e = {"id": "WRA_ReservoirWarn_1", "title": "水庫放流", "updated": "2026-10-31T11:28:40+08:00", "author": {"name": "水利署"},
             "link": {"@rel": "alternate", "@href": "https://alerts.ncdr.nat.gov.tw/x.cap"},
             "summary": {"@type": "html", "#text": "水利署訊寶山第二水庫:(洩洪至石井溪):自由溢流,影響範圍:新竹縣，寶山鄉"},
             "category": {"@term": "水庫放流"}, "status": "Actual", "msgType": "Update",
             "effective": "2026/10/31 上午 11:28:40", "expires": "2026/10/31 下午 02:29:00"}
    c = trim_cap(cap_e)
    check(c == {"id": "WRA_ReservoirWarn_1", "title": "水庫放流", "updated": "2026-10-31T11:28:40+08:00", "author": "水利署",
                "link": "https://alerts.ncdr.nat.gov.tw/x.cap", "summary": "水利署訊寶山第二水庫:(洩洪至石井溪):自由溢流,影響範圍:新竹縣，寶山鄉",
                "category": "水庫放流", "msgType": "Update", "effective": "2026/10/31 上午 11:28:40", "expires": "2026/10/31 下午 02:29:00"},
          "示警：CAP 摘要攤平成字串，時間原樣留給前端")

    calls = []

    def fake(eq_big=(big, dup), eq_small=(small,), tsu=(tsu_warn,), cap=(cap_e,), fail=()):
        def g(url):
            calls.append(url)
            for k, code in fail:
                if k in url:
                    raise HTTPError(url, code, "x", {}, None) if code else URLError("down")
            if "E-A0015-001" in url:
                return json.dumps({"records": {"Earthquake": list(eq_big)}})
            if "E-A0016-001" in url:
                return json.dumps({"records": {"Earthquake": list(eq_small)}})
            if "E-A0014-001" in url:
                return json.dumps({"records": {"Tsunami": list(tsu)}})
            return json.dumps({"entry": list(cap) if len(cap) != 1 else cap[0]})
        return g

    with tempfile.TemporaryDirectory() as td:
        OUT = os.path.join(td, "hazard.json")
        KEY = "TESTKEY"
        nap = []
        check(run(fake(), sleep=nap.append) == 0, "有授權碼：四份都抓到就寫出")
        d = json.load(open(OUT, encoding="utf-8"))
        check(len(d["eq"]) == 2 and d["eq"][0]["_s"] == 0 and d["eq"][1]["_s"] == 1, "兩個地震資料集合併、同一個地震只留一筆、新的在前")
        check(len(d["tsu"]) == 1 and len(d["cap"]["entries"]) == 1 and d["hasKey"] is True and d["err"] == {},
              "只有一則示警時（物件不是陣列）也收進來")
        check("TESTKEY" not in open(OUT, encoding="utf-8").read(), "授權碼不會寫進公開的資料檔")
        check(all("limit=" in u for u in calls if "cwa.gov.tw" in u), "氣象署只要最近幾十筆（limit），不拉整個資料集")

        before = d
        check(run(fake(fail=[("E-A0014-001", 0), ("ncdr", 503)]), sleep=nap.append) == 0, "海嘯、示警抓不到：照樣寫出（回 0）")
        d = json.load(open(OUT, encoding="utf-8"))
        check(d["tsu"] == before["tsu"] and d["cap"] == before["cap"] and set(d["err"]) == {"tsu", "cap"},
              "抓不到的那段沿用上一份，並記下哪段、為什麼")
        check(any(x >= 3.5 for x in nap), "示警平台回 503／429 時至少等 3.5 秒再試（平台限 3 秒一次）")
        n0 = len(calls)
        check(run(fake(fail=[("E-A0015-001", 401)]), sleep=nap.append) == 1, "授權碼被拒：紅燈，提醒換 Secret")
        d = json.load(open(OUT, encoding="utf-8"))
        check(d["err"].get("eq") == "授權碼被拒" and len(d["cap"]["entries"]) == 1 and len(d["eq"]) == 2
              and not any("E-A0014-001" in u for u in calls[n0:]), "授權碼被拒時不再打氣象署，示警照樣寫出、地震沿用上一份")

        KEY = ""
        calls.clear()
        check(run(fake(), sleep=nap.append) == 0 and not any("cwa.gov.tw" in u for u in calls), "沒設授權碼：不打氣象署，示警照抓")
        d = json.load(open(OUT, encoding="utf-8"))
        check(d["hasKey"] is False and len(d["eq"]) == 2, "沒設授權碼時地震沿用上一份（不清空）")

        os.remove(OUT)
        check(run(fake(fail=[("ncdr", 0)]), sleep=nap.append) == 1 and not os.path.exists(OUT),
              "全部抓不到、也沒有舊檔：紅燈，不交空檔")
    print(f"全部通過（{okn} 項）")
    return 0


if __name__ == "__main__":
    sys.exit(selftest() if sys.argv[1:] == ["selftest"] else run())
