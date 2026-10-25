#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""景點、港口、海象即時影像清單（WFU「台灣景點人潮即時影像」部落格整理）→ data/wfu.json，給「即時影像 › 景點海象」。

**為什麼**
使用者 10/26 要求加入 monitor1.wfuapp.com。TDX 只有道路監視器，看不到港口、海岸、河川；這個部落格整理了
1 千多支景點、港口、颱風海象、水庫與河川的即時影像，正好補上颱風天最想看的海邊。

**來源格式（2026-10-26 實測公開 JSON 摘要 /feeds/posts/default?alt=json，共 1,085 則）**
每則內文有一個 <p class="empty_p" data-group data-program data-id | data-channel | data-gif>：
- data-id：YouTube 影片 ID（約 364 則）
- data-channel：YouTube 頻道 ID（約 92 則），看它「現在正在播」的直播
- data-gif：政府監視器的單張截圖網址（約 595 則：水利署河川、臺北市交通…）
沒有座標，只有分類標籤（縣市、颱風海象、港口…），所以不上地圖、照縣市分組。

**YouTube 先驗兩道**
1. 官方 oEmbed：404＝影片已下架或轉私人，不收；401／403＝擁有者禁止嵌入，標成只能外連（到 YouTube 看）。
2. 影片頁的播放狀態（v94）：oEmbed 分不出「直播已經結束」。2026-10-26 抽樣 25 支，14 支是結束的直播
   （YouTube 寫「無法播放這部直播影片的錄影存檔」），部落格還留著舊影片 ID。確定結束的改用同頻道「現在的直播」
   （頻道 live_stream 嵌入；頻道還在播就看得到），頁面上找不到頻道的才剔除。
YouTube 對資料中心 IP 常要求「確認不是機器人」，那種判斷不了的照收；網路錯也照收——
寧可讓使用者點了才發現，也不要整批誤刪。

**只留手機看得到的**
截圖網址要 https、不是內網位址（http 的影像在 https 網頁裡會被瀏覽器擋）。

輸出 data/wfu.json：
  {"v":1, "src":"WFU", "updated":"…", "total":N, "dropped":{"http":…,"private":…,"gone":…,"nosrc":…},
   "yt":{"ok":…,"noembed":…,"gone":…,"relive":…,"ended":…,"unchecked":…},
   "items":[[標題, 縣市, 種類, 來源, 原文網址, [標籤…]], …], "cite":"…"}
  種類：y＝YouTube 影片（可嵌入）、x＝YouTube 影片（禁止嵌入，只能外連）、c＝YouTube 頻道直播、i＝單張截圖

離線自我測試：python3 scripts/fetch_wfu.py selftest
"""
import ipaddress
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

FEED = os.environ.get("WFU_FEED", "https://monitor1.wfuapp.com/feeds/posts/default")
OUT = os.environ.get("WFU_OUT", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "wfu.json"))
YT_SEC = float(os.environ.get("WFU_YT_SEC", "480"))      # YouTube 驗證的時間預算；超過的先照收、標成沒驗
PAGE = 150                                               # Blogger 摘要一頁最多 150 則
TW = timezone(timedelta(hours=8))
UA = "Mozilla/5.0 (compatible; data-fetch/1.0)"
# 標籤裡的縣市（部落格用「台」字、新竹／嘉義不分縣市、連江寫馬祖）
COUNTIES = ["基隆", "台北", "新北", "桃園", "新竹", "苗栗", "台中", "彰化", "南投", "雲林", "嘉義", "台南",
            "高雄", "屏東", "宜蘭", "花蓮", "台東", "澎湖", "金門", "馬祖"]


def now_iso():
    return datetime.now(TW).isoformat(timespec="seconds")


def get(url, timeout=30, headers=None):
    with urlopen(Request(url, headers={"User-Agent": UA, **(headers or {})}), timeout=timeout) as r:
        return r.read(20_000_000).decode("utf-8", "replace")


def https_ok(u):
    try:
        p = urlsplit(u)
    except ValueError:
        return False, "nosrc"
    if p.scheme != "https" or not p.hostname:
        return False, "http"
    try:
        if ipaddress.ip_address(p.hostname).is_private:
            return False, "private"
    except ValueError:
        pass
    return True, ""


def parse_entry(e):
    """一則摘要 → (標題, 縣市, 種類, 來源, 原文網址, 標籤) 或 (None, 濾掉的原因)。"""
    c = ((e.get("content") or e.get("summary") or {}).get("$t")) or ""
    m = re.search(r'<p class="empty_p"([^>]*)>', c)
    if not m:
        return None, "nosrc"                     # 篩選器、分類索引頁，不是影像
    a = {}
    for k, v in re.findall(r'data-([\w-]+)="([^"]*)"', m.group(1)):
        a.setdefault(k, v)                       # 同名屬性（data-temp 會重複）只取第一個
    if re.fullmatch(r"[\w-]{11}", a.get("id", "")):
        kind, src = "y", a["id"]
    elif re.fullmatch(r"UC[\w-]{22}", a.get("channel", "")):
        kind, src = "c", a["channel"]
    elif a.get("gif"):
        ok, why = https_ok(a["gif"])
        if not ok:
            return None, why
        if urlsplit(a["gif"]).hostname.endswith("googleusercontent.com"):
            return None, "nosrc"                 # 部落格自己的靜態縮圖，不是即時影像
        kind, src = "i", a["gif"]
    else:
        return None, "nosrc"
    labels = [x.get("term", "") for x in e.get("category") or []]
    county = next((x for x in labels if x in COUNTIES), "")
    tags = [x for x in labels if x and x not in COUNTIES and x != "影像" and not x.startswith("_") and not x.endswith("區")]
    title = re.sub(r"\s*即時影像\s*$", "", (e.get("title") or {}).get("$t", "")).strip()
    link = next((l.get("href", "") for l in e.get("link") or [] if l.get("rel") == "alternate"), "")
    if not title:
        return None, "nosrc"
    return (title, county, kind, src, link, tags), ""


def fetch_all(getter=get):
    ents, start = [], 1
    while True:
        d = json.loads(getter(f"{FEED}?alt=json&max-results={PAGE}&start-index={start}"))
        es = (d.get("feed") or {}).get("entry") or []
        ents += es
        if len(es) < PAGE:
            return ents
        start += PAGE
        if start > 20000:                        # 保險絲：摘要格式變了也不會無限翻頁
            return ents


def oembed(vid, timeout=15):
    """回傳 HTTP 狀態碼；網路錯回 0（不知道，不當成下架）。"""
    try:
        with urlopen(Request("https://www.youtube.com/oembed?format=json&url="
                             + quote(f"https://www.youtube.com/watch?v={vid}", safe=""),
                             headers={"User-Agent": UA}), timeout=timeout) as r:
            return r.status
    except HTTPError as e:
        return e.code
    except Exception:
        return 0


def watch(vid, timeout=20):
    """影片頁的播放狀態 → (狀態, 頻道 ID)。狀態：ended＝直播已結束、無法播放；ok＝可以播；unknown＝判斷不了。
    只認「播放狀態 UNPLAYABLE、而且是直播內容、而且現在沒在播」才算結束——
    同樣是 UNPLAYABLE 的「這個國家／地區無法觀看」不能誤判成結束（Actions 在美國，臺灣限定的直播會這樣）。"""
    try:
        h = get(f"https://www.youtube.com/watch?v={vid}", timeout,
                {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36",
                 "Accept-Language": "zh-TW,zh;q=0.9", "Cookie": "CONSENT=YES+cb; SOCS=CAI"})
    except Exception:
        return "unknown", ""
    st = re.search(r'"playabilityStatus":\{"status":"(\w+)"', h)
    ch = re.search(r'"channelId":"(UC[\w-]{22})"', h)
    content = re.search(r'"isLiveContent":(true|false)', h)
    now = re.search(r'"isLiveNow":(true|false)', h)
    ch = ch.group(1) if ch else ""
    if st and st.group(1) == "UNPLAYABLE" and content and content.group(1) == "true" and now and now.group(1) == "false":
        return "ended", ch
    if st and st.group(1) == "OK":
        return "ok", ch
    return "unknown", ch


def load_prev():
    try:
        with open(OUT, encoding="utf-8") as f:
            d = json.load(f)
        return d if d.get("v") == 1 else None
    except Exception:
        return None


def run(getter=get, check=oembed, clock=time.monotonic, sleep=time.sleep, look=watch):
    try:
        ents = fetch_all(getter)
    except Exception as e:
        if load_prev():
            # 部落格暫時連不到：舊清單照用（畫面有更新時間），下一輪再抓
            print(f"摘要抓不到（{type(e).__name__}），保留上一份清單")
            return 0
        print(f"錯誤：摘要抓不到（{type(e).__name__}），手上也沒有舊清單")
        return 1
    items, dropped = [], {"http": 0, "private": 0, "gone": 0, "nosrc": 0}
    for e in ents:
        it, why = parse_entry(e)
        if it:
            items.append(list(it))
        else:
            dropped[why] += 1
    yt = {"ok": 0, "noembed": 0, "gone": 0, "relive": 0, "ended": 0, "unchecked": 0}
    end = clock() + YT_SEC
    keep = []
    for it in items:
        if it[2] == "y":
            if clock() > end:
                yt["unchecked"] += 1
            else:
                st = check(it[3])
                sleep(0.2)                       # 一秒五支，別一口氣打滿
                if st == 404:
                    yt["gone"] += 1
                    dropped["gone"] += 1
                    continue
                if st in (401, 403):
                    it[2] = "x"
                    yt["noembed"] += 1
                elif st == 200:
                    w, ch = look(it[3])
                    sleep(0.3)
                    if w == "ended" and ch:
                        it[2], it[3] = "c", ch           # 直播重開會換影片 ID，看頻道「現在的直播」
                        yt["relive"] += 1
                    elif w == "ended":
                        yt["ended"] += 1
                        dropped["gone"] += 1
                        continue
                    else:
                        yt["ok" if w == "ok" else "unchecked"] += 1
                else:
                    yt["unchecked"] += 1
        keep.append(it)
    if not keep:
        print(f"錯誤：摘要有 {len(ents)} 則，卻整理不出任何一支影像（格式可能改了）；不覆寫舊檔")
        return 1
    keep.sort(key=lambda x: (COUNTIES.index(x[1]) if x[1] in COUNTIES else len(COUNTIES), x[0]))
    doc = {"v": 1, "src": "WFU", "updated": now_iso(), "total": len(keep), "dropped": dropped, "yt": yt,
           "items": keep,
           "cite": "清單整理：WFU「台灣景點人潮即時影像」（monitor1.wfuapp.com）。畫面版權屬各影像提供者。"}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT + ".tmp", "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(OUT + ".tmp", OUT)
    print(f"寫出 {len(keep)} 支（YouTube 可播 {yt['ok']}、禁止嵌入 {yt['noembed']}、已下架 {yt['gone']}、"
          f"直播結束改看頻道 {yt['relive']}、結束又找不到頻道 {yt['ended']}、判斷不了 {yt['unchecked']}；"
          f"濾掉 http {dropped['http']}、內網 {dropped['private']}、不是影像 {dropped['nosrc']}）")
    return 0


def selftest():
    global OUT, YT_SEC
    import tempfile
    okn = 0

    def check(cond, msg):
        nonlocal okn
        if not cond:
            print("✗", msg)
            sys.exit(1)
        okn += 1
        print("  ✓", msg)

    def ent(title, p, labels, href="https://monitor1.wfuapp.com/2024/01/x.html"):
        return {"title": {"$t": title}, "category": [{"term": t} for t in labels],
                "link": [{"rel": "alternate", "href": href}],
                "content": {"$t": f'<img src="https://blogger.googleusercontent.com/a.jpg"/><p class="empty_p" {p}></p><ul><li>說明</li></ul>'}}
    E = [
        ent("基隆海洋廣場即時影像", 'data-group="基隆" data-program="海洋廣場" data-id="abcdefghijk"', ["影像", "基隆", "港口", "颱風海象", "_站務"]),
        ent("南投某景點即時影像", 'data-group="彰投" data-program="x" data-id="gone0000000"', ["影像", "南投", "景點"]),
        ent("台東海岸即時影像", 'data-group="花東" data-program="x" data-id="noemb000000"', ["影像", "台東", "颱風海象", "東海岸"]),
        ent("台中大里塗城即時影像", 'data-group="台中" data-program="大里塗城(YT)" data-channel="UCE8omMvpPxleZ42Mk2MHQhQ"', ["影像", "台中", "路況"]),
        ent("花蓮馬太鞍溪橋即時影像", 'data-group="花東" data-program="馬太鞍溪橋" data-gif="https://fmg.wra.gov.tw/singlefmg/new/7703/newbig.jpg"', ["影像", "花蓮", "橋樑", "颱風海象"]),
        ent("台北大巨蛋即時影像", 'data-group="台北1" data-temp="gy6fjztAkKQ" data-temp="https://x/y.jpg" data-gif="https://cctv.bote.gov.taipei:8502/jpg/382"', ["影像", "台北", "地標", "信義區"]),
        ent("舊的 http 截圖即時影像", 'data-group="x" data-gif="http://117.56.220.2/jpg/1"', ["影像", "新北"]),
        ent("內網截圖即時影像", 'data-group="x" data-gif="https://192.168.1.5/jpg/1"', ["影像", "新北"]),
        ent("只有縮圖的即時影像", 'data-group="x" data-gif="https://blogger.googleusercontent.com/img/a/AVvX"', ["影像", "新北"]),
        {"title": {"$t": "外島即時影像篩選器"}, "category": [], "link": [], "content": {"$t": "<style>.x{}</style>"}},
        ent("沒有縣市的即時影像", 'data-group="x" data-id="nocounty000"', ["影像", "景點"]),
    ]

    def feed(es):
        return json.dumps({"feed": {"entry": es}})

    pages = []

    def g1(url):
        pages.append(url)
        return feed(E)
    status = {"gone0000000": 404, "noemb000000": 403, "nocounty000": 0}
    checked = []

    def oe(vid):
        checked.append(vid)
        return status.get(vid, 200)
    watchs = {"abcdefghijk": ("ok", "UCaaaaaaaaaaaaaaaaaaaaaa")}

    def lk(vid):
        return watchs.get(vid, ("unknown", ""))

    with tempfile.TemporaryDirectory() as tmp:
        OUT = os.path.join(tmp, "wfu.json")
        check(run(g1, oe, sleep=lambda s: None, look=lk) == 0, "正常跑完、寫出檔案")
        d = load_prev()
        names = [x[0] for x in d["items"]]
        check(d["total"] == 6 and "南投某景點" not in names, f"留 6 支、已下架的 YouTube 剔除：{names}")
        it = {x[0]: x for x in d["items"]}
        check(it["基隆海洋廣場"][1:4] == ["基隆", "y", "abcdefghijk"], "YouTube 影片：縣市取標籤、種類 y、來源是影片 ID；標題去掉「即時影像」")
        check(it["基隆海洋廣場"][5] == ["港口", "颱風海象"], f"標籤去掉縣市、「影像」、底線開頭的內部分類：{it['基隆海洋廣場'][5]}")
        check(it["台東海岸"][2] == "x", "擁有者禁止嵌入（403）→ 只能外連")
        check(it["台中大里塗城"][2:4] == ["c", "UCE8omMvpPxleZ42Mk2MHQhQ"], "頻道直播 → 種類 c")
        check(it["花蓮馬太鞍溪橋"][2:4] == ["i", "https://fmg.wra.gov.tw/singlefmg/new/7703/newbig.jpg"], "政府截圖 → 種類 i")
        check(it["台北大巨蛋"][3] == "https://cctv.bote.gov.taipei:8502/jpg/382" and "信義區" not in it["台北大巨蛋"][5],
              "重複的 data-temp 不干擾；行政區標籤不留")
        check(d["dropped"] == {"http": 1, "private": 1, "gone": 1, "nosrc": 2}, f"濾掉的各記一筆（部落格縮圖、篩選器頁算不是影像）：{d['dropped']}")
        check(d["yt"] == {"ok": 1, "noembed": 1, "gone": 1, "relive": 0, "ended": 0, "unchecked": 1}, f"oEmbed 連不到的照收、記成沒驗：{d['yt']}")
        check(it["沒有縣市的"][1] == "" and names[-1] == "沒有縣市的", "沒有縣市標籤的排最後")
        check(names.index("基隆海洋廣場") < names.index("台北大巨蛋") < names.index("台中大里塗城"), "照縣市（北到南）排序")
        check(d["cite"].startswith("清單整理：WFU"), "附出處")

        # 一頁剛好滿 150 則要翻下一頁
        pages.clear()
        many = [ent(f"景點{i}即時影像", f'data-gif="https://a.gov.tw/{i}.jpg"', ["影像", "宜蘭"]) for i in range(PAGE + 3)]

        def g2(url):
            pages.append(url)
            s = int(re.search(r"start-index=(\d+)", url).group(1))
            return feed(many[s - 1:s - 1 + PAGE])
        run(g2, oe, sleep=lambda s: None, look=lk)
        check(len(pages) == 2 and load_prev()["total"] == PAGE + 3, "超過 150 則會翻頁抓完")

        # 時間預算用完：剩下的 YouTube 先照收、標成沒驗
        tick = iter(range(0, 10000, 100))
        YT_SEC = 150
        yts = [ent(f"直播{i}即時影像", f'data-id="vid{i:08d}"', ["影像", "基隆"]) for i in range(5)]
        checked.clear()
        run(lambda u: feed(yts), oe, clock=lambda: next(tick), sleep=lambda s: None, look=lk)
        d = load_prev()
        # 第一支驗過 oEmbed、影片頁判斷不了；其餘 4 支超過預算沒驗——5 支都照收
        check(len(checked) == 1 and d["yt"]["unchecked"] == 5 and d["total"] == 5, f"超過預算的先照收：{d['yt']}")
        YT_SEC = 480

        # 直播已結束：找得到頻道的改看頻道現在的直播；找不到的剔除；判斷不了（要確認不是機器人）的照收
        lv = [ent("結束有頻道即時影像", 'data-id="endedch0000"', ["影像", "基隆"]),
              ent("結束沒頻道即時影像", 'data-id="endednoch00"', ["影像", "基隆"]),
              ent("要確認機器人即時影像", 'data-id="botcheck000"', ["影像", "基隆"]),
              ent("正在播即時影像", 'data-id="livenow0000"', ["影像", "基隆"])]
        watchs.update({"endedch0000": ("ended", "UCbbbbbbbbbbbbbbbbbbbbbb"), "endednoch00": ("ended", ""),
                       "livenow0000": ("ok", "UCcccccccccccccccccccccc")})
        run(lambda u: feed(lv), oe, sleep=lambda s: None, look=lk)
        d = load_prev()
        it = {x[0]: x for x in d["items"]}
        check(it["結束有頻道"][2:4] == ["c", "UCbbbbbbbbbbbbbbbbbbbbbb"], "直播已結束、頁面有頻道：改成看頻道現在的直播")
        check("結束沒頻道" not in it and d["yt"]["ended"] == 1, "直播已結束又找不到頻道：剔除")
        check(it["要確認機器人"][2] == "y" and it["正在播"][2] == "y", "判斷不了的、正在播的：照原影片收")
        check(d["yt"] == {"ok": 1, "noembed": 0, "gone": 0, "relive": 1, "ended": 1, "unchecked": 1}, f"計數：{d['yt']}")

        def gdown(url):
            raise URLError("timeout")
        before = open(OUT, encoding="utf-8").read()
        check(run(gdown, oe) == 0 and open(OUT, encoding="utf-8").read() == before, "部落格連不到、手上有舊清單：不紅燈、不覆寫")
        os.remove(OUT)
        check(run(gdown, oe) == 1 and not os.path.exists(OUT), "連不到也沒有舊清單：紅燈，不交空檔")
        check(run(lambda u: feed([E[9]]), oe) == 1 and not os.path.exists(OUT), "格式變了、一支都整理不出來：紅燈，不交空檔")
    print(f"全部通過（{okn} 項）")
    return 0


if __name__ == "__main__":
    sys.exit(selftest() if sys.argv[1:] == ["selftest"] else run())
