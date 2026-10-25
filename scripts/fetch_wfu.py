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
   （YouTube 寫「無法播放這部直播影片的錄影存檔」），部落格還留著舊影片 ID。
YouTube 對資料中心 IP 常要求「確認不是機器人」，那種判斷不了的照收；網路錯也照收——
寧可讓使用者點了才發現，也不要整批誤刪。

**直播結束之後（v95）**
v94 把結束的一律改成「同頻道現在的直播」，但 151 個頻道裡有 34 個一個頻道掛好幾支鏡頭（例如苗栗通霄的
日落大道和海水浴場同一個頻道），頻道直播只會播其中一支——使用者 10/26 點「日落大道」看到的是錯的或「無法播放」。
- 頻道只掛這一支：改看頻道現在的直播（c）。
- 頻道掛好幾支：不亂猜，標成「已結束」（e），點了到 YouTube 頻道的直播列表自己挑。
- 有 YouTube Data API 金鑰（repo Secret YT_API_KEY）時：從頻道的上傳清單找出「現在正在直播」的影片，
  用標題比對對回每一支鏡頭，對得上就直接換成新的影片 ID（y）。一天約 200 點，免費額度 1 萬點。
  頻道 RSS（/feeds/videos.xml）本來也能找，但 2026-10-26 實測對這些頻道一律回 404／500，不能用。
  金鑰用 X-Goog-Api-Key 標頭傳，不放網址（網址會進錯誤訊息與紀錄）。

**只留手機看得到的**
截圖網址要 https、不是內網位址（http 的影像在 https 網頁裡會被瀏覽器擋）。

輸出 data/wfu.json：
  {"v":1, "src":"WFU", "updated":"…", "total":N, "dropped":{"http":…,"private":…,"gone":…,"nosrc":…},
   "yt":{"ok":…,"noembed":…,"gone":…,"live":…,"relive":…,"ended":…,"unchecked":…,"api":true|false},
   "items":[[標題, 縣市, 種類, 來源, 原文網址, [標籤…]], …], "cite":"…"}
  種類：y＝YouTube 影片（可嵌入）、x＝YouTube 影片（禁止嵌入，只能外連）、c＝YouTube 頻道直播、
        e＝直播已結束（來源是頻道 ID，連到頻道的直播列表）、i＝單張截圖或串流

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
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

FEED = os.environ.get("WFU_FEED", "https://monitor1.wfuapp.com/feeds/posts/default")
OUT = os.environ.get("WFU_OUT", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "wfu.json"))
YT_SEC = float(os.environ.get("WFU_YT_SEC", "480"))      # YouTube 驗證的時間預算；超過的先照收、標成沒驗
PAGE = 150                                               # Blogger 摘要一頁最多 150 則
YT_KEY = os.environ.get("YT_API_KEY", "").strip()        # 選用；沒設就走沒有金鑰的做法
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
    """一則摘要 → (標題, 縣市, 種類, 來源, 原文網址, 標籤, 節目名) 或 (None, 濾掉的原因)。節目名只拿來對 YouTube 標題，不輸出。"""
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
    prog = re.sub(r"\(YT\)|（YT）", "", a.get("program", "")).strip()
    return (title, county, kind, src, link, tags, prog), ""


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


def yt_api(path, params, timeout=20):
    return json.loads(get("https://www.googleapis.com/youtube/v3/" + path + "?" + urlencode(params), timeout,
                          {"X-Goog-Api-Key": YT_KEY}))


def api_videos(ids, api=yt_api):
    """影片 ID → (狀態, 頻道 ID)；狀態 live／ended／noembed／ok。查不到的不回（當成下架）。一次 50 支、1 點。"""
    out = {}
    for k in range(0, len(ids), 50):
        d = api("videos", {"part": "snippet,status,liveStreamingDetails", "id": ",".join(ids[k:k + 50])})
        for v in d.get("items") or []:
            sn, st, ls = v.get("snippet") or {}, v.get("status") or {}, v.get("liveStreamingDetails") or {}
            if st.get("embeddable") is False:
                state = "noembed"
            elif sn.get("liveBroadcastContent") == "live":
                state = "live"
            elif ls.get("actualEndTime"):
                state = "ended"
            else:
                state = "ok"
            out[v["id"]] = (state, sn.get("channelId", ""), sn.get("title", ""))
    return out


def api_lives(chs, api=yt_api):
    """頻道 → [(影片 ID, 標題), …]：上傳清單最新 25 支裡現在正在直播、可嵌入的。頻道一支 1 點。"""
    vids = []
    for ch in chs:
        try:
            d = api("playlistItems", {"part": "contentDetails", "playlistId": "UU" + ch[2:], "maxResults": 25})
        except HTTPError:
            continue                              # 頻道關了、沒有上傳清單
        vids += [x["contentDetails"]["videoId"] for x in d.get("items") or [] if x.get("contentDetails")]
    lives = {}
    for vid, (state, ch, title) in api_videos(vids, api).items():
        if state == "live":
            lives.setdefault(ch, []).append((vid, title))
    return lives


def _norm(s):
    return re.sub(r"[\W_]+", "", (s or "").replace("臺", "台")).lower()


def _lcs(a, b):
    """最長共同子字串長度（標題都很短，直接動態規劃）。"""
    best, prev = 0, [0] * (len(b) + 1)
    for ca in a:
        cur = [0] * (len(b) + 1)
        for j, cb in enumerate(b, 1):
            if ca == cb:
                cur[j] = prev[j - 1] + 1
                best = max(best, cur[j])
        prev = cur
    return best


def match_lives(its, lives):
    """一個頻道的幾支鏡頭 ↔ 這個頻道正在播的幾支直播，用標題對。回傳 {鏡頭序號: 影片 ID}。
    只有一支鏡頭、一支直播時直接對；其他要共同字串至少 2 個字，而且比第二名多，才算對上——對不上寧可不換。"""
    if len(its) == 1 and len(lives) == 1:
        return {0: lives[0][0]}
    out, used = {}, set()
    for k, it in enumerate(its):
        key = _norm(it[6] or it[0].replace(it[1], "", 1))
        sc = sorted(((_lcs(key, _norm(t)), vid) for vid, t in lives if vid not in used), reverse=True)
        if sc and sc[0][0] >= 2 and (len(sc) == 1 or sc[0][0] > sc[1][0]):
            out[k] = sc[0][1]
            used.add(sc[0][1])
    return out


def load_prev():
    try:
        with open(OUT, encoding="utf-8") as f:
            d = json.load(f)
        return d if d.get("v") == 1 else None
    except Exception:
        return None


def run(getter=get, check=oembed, clock=time.monotonic, sleep=time.sleep, look=watch, api=yt_api, key=None):
    key = YT_KEY if key is None else key
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
    yt = {"ok": 0, "noembed": 0, "gone": 0, "live": 0, "relive": 0, "ended": 0, "unchecked": 0, "api": bool(key)}
    end = clock() + YT_SEC
    keep, ended, chan_of = [], [], {}
    states = {}
    if key:
        try:
            states = api_videos([it[3] for it in items if it[2] == "y"], api)
        except Exception as e:
            # 金鑰錯、額度用完：不紅燈，這一輪退回沒有金鑰的做法（金鑰本身不印）
            print(f"YouTube API 用不了（{type(e).__name__}），這一輪改用影片頁判斷")
            key = ""
            yt["api"] = False
    for it in items:
        if it[2] == "c":
            chan_of[id(it)] = it[3]
        if it[2] == "y":
            if key:
                st = states.get(it[3])
                if not st:                           # API 查不到＝已下架或轉私人
                    yt["gone"] += 1
                    dropped["gone"] += 1
                    continue
                state, ch, _ = st
                chan_of[id(it)] = ch
                if state == "noembed":
                    it[2] = "x"
                    yt["noembed"] += 1
                elif state == "ended":
                    ended.append(it)
                else:
                    yt["ok"] += 1
            elif clock() > end:
                yt["unchecked"] += 1
            else:
                st = check(it[3])
                sleep(0.2)                           # 一秒五支，別一口氣打滿
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
                    if ch:
                        chan_of[id(it)] = ch
                    if w == "ended":
                        ended.append(it)
                    else:
                        yt["ok" if w == "ok" else "unchecked"] += 1
                else:
                    yt["unchecked"] += 1
        keep.append(it)
    # 每個頻道掛了幾支鏡頭（原本就是頻道的＋確定結束的）
    by_ch = {}
    for it in keep:
        ch = chan_of.get(id(it))
        if ch and (it[2] == "c" or it in ended):
            by_ch.setdefault(ch, []).append(it)
    mapped = set()
    if key and by_ch:
        try:
            lives = api_lives(list(by_ch), api)
        except Exception as e:
            print(f"YouTube API 找直播失敗（{type(e).__name__}），這一輪不換新的影片 ID")
            lives = {}
        for ch, its in by_ch.items():
            for k, vid in match_lives(its, lives.get(ch) or []).items():
                its[k][2], its[k][3] = "y", vid
                mapped.add(id(its[k]))
                yt["live"] += 1
    out = []
    for it in keep:
        if it in ended and id(it) not in mapped:
            ch = chan_of.get(id(it))
            if not ch:
                yt["ended"] += 1                     # 結束了又不知道頻道：沒地方可連，剔除
                dropped["gone"] += 1
                continue
            if len(by_ch.get(ch) or []) == 1:
                it[2], it[3] = "c", ch               # 頻道只掛這一支：頻道現在的直播就是它
                yt["relive"] += 1
            else:
                it[2], it[3] = "e", ch               # 頻道掛好幾支：不亂猜，到頻道的直播列表自己挑
                yt["ended"] += 1
        out.append(it[:6])
    keep = out
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
    print(f"寫出 {len(keep)} 支（YouTube {'有' if yt['api'] else '沒有'}金鑰：可播 {yt['ok']}、禁止嵌入 {yt['noembed']}、"
          f"已下架 {yt['gone']}、結束後找到新直播 {yt['live']}、結束改看頻道 {yt['relive']}、結束標已結束 {yt['ended']}、"
          f"判斷不了 {yt['unchecked']}；"
          f"濾掉 http {dropped['http']}、內網 {dropped['private']}、不是影像 {dropped['nosrc']}）")
    return 0


def selftest():
    global OUT, YT_SEC, YT_KEY
    import tempfile
    YT_KEY = ""                                   # 自我測試一律不打真的 API
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
        check(d["yt"] == {"ok": 1, "noembed": 1, "gone": 1, "live": 0, "relive": 0, "ended": 0, "unchecked": 1, "api": False},
              f"oEmbed 連不到的照收、記成沒驗：{d['yt']}")
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

        # 直播已結束（沒有金鑰）：頻道只掛這一支 → 看頻道直播；頻道掛好幾支 → 標已結束、連頻道；不知道頻道 → 剔除
        lv = [ent("結束有頻道即時影像", 'data-id="endedch0000"', ["影像", "基隆"]),
              ent("結束沒頻道即時影像", 'data-id="endednoch00"', ["影像", "基隆"]),
              ent("要確認機器人即時影像", 'data-id="botcheck000"', ["影像", "基隆"]),
              ent("正在播即時影像", 'data-id="livenow0000"', ["影像", "基隆"]),
              ent("苗栗通霄日落大道即時影像", 'data-program="日落大道" data-id="sunset00000"', ["影像", "苗栗"]),
              ent("苗栗通霄海水浴場即時影像", 'data-program="通霄海水浴場(YT)" data-channel="UCtongxiao00000000000000"', ["影像", "苗栗"])]
        watchs.update({"endedch0000": ("ended", "UCbbbbbbbbbbbbbbbbbbbbbb"), "endednoch00": ("ended", ""),
                       "livenow0000": ("ok", "UCcccccccccccccccccccccc"), "sunset00000": ("ended", "UCtongxiao00000000000000")})
        run(lambda u: feed(lv), oe, sleep=lambda s: None, look=lk)
        d = load_prev()
        it = {x[0]: x for x in d["items"]}
        check(it["結束有頻道"][2:4] == ["c", "UCbbbbbbbbbbbbbbbbbbbbbb"], "直播已結束、頻道只掛這一支：改成看頻道現在的直播")
        check(it["苗栗通霄日落大道"][2:4] == ["e", "UCtongxiao00000000000000"],
              "直播已結束、頻道還掛別支（通霄日落大道／海水浴場）：不亂換成頻道直播，標已結束")
        check(it["苗栗通霄海水浴場"][2] == "c", "原本就是頻道的照舊")
        check("結束沒頻道" not in it, "直播已結束又找不到頻道：剔除")
        check(it["要確認機器人"][2] == "y" and it["正在播"][2] == "y", "判斷不了的、正在播的：照原影片收")
        check(d["yt"] == {"ok": 1, "noembed": 0, "gone": 0, "live": 0, "relive": 1, "ended": 2, "unchecked": 1, "api": False},
              f"計數：{d['yt']}")
        check(all(len(x) == 6 for x in d["items"]), "節目名只拿來比對，不寫進輸出")

        # 有金鑰：用 API 判斷結束、從頻道上傳清單找現在的直播、用標題對回每支鏡頭
        calls = []

        def api(path, params):
            calls.append((path, dict(params)))
            if path == "videos":
                db = {"sunset00000": {"snippet": {"liveBroadcastContent": "none", "channelId": "UCtongxiao00000000000000"},
                                      "status": {"embeddable": True}, "liveStreamingDetails": {"actualEndTime": "2026-10-01T00:00:00Z"}},
                      "livenow0000": {"snippet": {"liveBroadcastContent": "live", "channelId": "UCcccccccccccccccccccccc"}, "status": {"embeddable": True}},
                      "noemb000000": {"snippet": {"liveBroadcastContent": "live", "channelId": "UCx"}, "status": {"embeddable": False}},
                      "newsunset01": {"snippet": {"liveBroadcastContent": "live", "channelId": "UCtongxiao00000000000000",
                                                  "title": "【LIVE】苗栗通霄 日落大道 即時影像"}, "status": {"embeddable": True}},
                      "newbeach001": {"snippet": {"liveBroadcastContent": "live", "channelId": "UCtongxiao00000000000000",
                                                  "title": "通霄海水浴場 24小時直播"}, "status": {"embeddable": True}},
                      "oldvod00001": {"snippet": {"liveBroadcastContent": "none", "channelId": "UCtongxiao00000000000000"}, "status": {}}}
                return {"items": [dict(db[i], id=i) for i in params["id"].split(",") if i in db]}
            if path == "playlistItems":
                if params["playlistId"] == "UUtongxiao00000000000000":
                    return {"items": [{"contentDetails": {"videoId": v}} for v in ("newbeach001", "oldvod00001", "newsunset01")]}
                raise HTTPError("u", 404, "nf", {}, None)
            raise AssertionError(path)
        lk2 = [lv[3], lv[4], lv[5], ent("禁止嵌入即時影像", 'data-id="noemb000000"', ["影像", "台東"]),
               ent("已刪除即時影像", 'data-id="deleted0000"', ["影像", "台東"])]
        run(lambda u: feed(lk2), oe, sleep=lambda s: None, look=lambda v: (_ for _ in ()).throw(AssertionError("有金鑰不該讀影片頁")),
            api=api, key="k")
        d = load_prev()
        it = {x[0]: x for x in d["items"]}
        check(it["苗栗通霄日落大道"][2:4] == ["y", "newsunset01"] and it["苗栗通霄海水浴場"][2:4] == ["y", "newbeach001"],
              "同頻道兩支鏡頭各自用標題對到正確的新直播")
        check(it["正在播"][2:4] == ["y", "livenow0000"] and it["禁止嵌入"][2] == "x" and "已刪除" not in it,
              "API：正在播照收、禁止嵌入外連、查不到的剔除")
        check(d["yt"]["api"] and d["yt"]["live"] == 2 and d["yt"]["gone"] == 1, f"計數：{d['yt']}")
        check(all(p != "search" for p, _ in calls) and len([c for c in calls if c[0] == "playlistItems"]) == 1,
              "只用便宜的上傳清單＋影片查詢（不用一次 100 點的搜尋）")
        # 金鑰錯或額度用完：退回沒金鑰的做法，不紅燈
        def api_bad(path, params):
            raise HTTPError("u", 403, "quotaExceeded", {}, None)
        check(run(lambda u: feed(lv), oe, sleep=lambda s: None, look=lk, api=api_bad, key="k") == 0
              and load_prev()["yt"]["api"] is False and load_prev()["total"] == 5, "API 用不了：這一輪改用影片頁判斷，照樣寫出")
        one = ["某地", "", "e", "", "", [], "某地"]
        check(match_lives([one], []) == {} and match_lives([one], [("v1", "完全不相干")]) == {0: "v1"},
              "頻道只有一支鏡頭、一支直播時直接對上；沒有直播就不換")

        def gdown(url):
            raise URLError("timeout")
        before = open(OUT, encoding="utf-8").read()
        check(run(gdown, oe) == 0 and open(OUT, encoding="utf-8").read() == before, "部落格連不到、手上有舊清單：不紅燈、不覆寫")
        os.remove(OUT)
        check(run(gdown, oe) == 1 and not os.path.exists(OUT), "連不到也沒有舊清單：紅燈，不交空檔")
        check(run(lambda u: feed([E[9]]), oe) == 1 and not os.path.exists(OUT), "格式變了、一支都整理不出來：紅燈，不交空檔")

    # 金鑰只能走標頭：網址會出現在錯誤訊息與 Actions 紀錄裡
    seen, real_get = [], globals()["get"]

    def spy(url, timeout=30, headers=None):
        seen.append((url, headers or {}))
        return '{"items":[]}'
    globals()["get"], YT_KEY = spy, "SECRET-TEST-KEY"
    try:
        yt_api("videos", {"part": "snippet", "id": "abcdefghijk"})
    finally:
        globals()["get"], YT_KEY = real_get, ""
    check(seen and "SECRET-TEST-KEY" not in seen[0][0] and seen[0][1].get("X-Goog-Api-Key") == "SECRET-TEST-KEY",
          "YouTube API 金鑰放 X-Goog-Api-Key 標頭，不進網址")
    print(f"全部通過（{okn} 項）")
    return 0


if __name__ == "__main__":
    sys.exit(selftest() if sys.argv[1:] == ["selftest"] else run())
