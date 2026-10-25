#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""座標 → 縣市、鄉鎮區的離線查詢表 assets/region.json，給「即時影像」的區域搜尋。

**為什麼**
使用者 10/26 要「區域搜尋」。監視器清單（TDX）的國道、省道沒有縣市欄位，景點清單沒有座標；
每支監視器自己的經緯度是唯一可靠的線索，所以要一張「這個點在哪個縣市、哪個鄉鎮區」的對照。
執行時不連網路（颱風天可能斷線），所以預先做成格網跟程式一起快取。

**資料來源**
g0v twgeojson 的 twTown1982.topo.json（內政部鄉鎮界線，簡化過；檔名的 1982 是座標系統，不是年份）。
裡面的縣市還是升格前的寫法，這裡換成現在的：台北縣→新北市、桃園縣→桃園市、台中縣→台中市、
台南縣→台南市、高雄縣→高雄市（這幾個縣的鄉鎮市一律改成「區」），另外頭份、員林已改制為市，
高雄縣三民鄉改名那瑪夏區。「(海)」是同一個區的海域部分，併回去。

**格網**
0.005°（約 500 公尺）一格，北緯 21.85–26.45、東經 118.10–122.10（含金門、馬祖、澎湖、綠島、蘭嶼）。
0.01° 時基隆仁愛區、安樂區這種幾公里寬的區會判錯（格子中心偏到隔壁區），所以用半格；檔案約 75 KB。
格子中心落在哪個鄉鎮的多邊形就記那個鄉鎮；海上的格子離陸地 5 格（約 2.5 公里）內的，記最近的鄉鎮
（港口、海堤、跨海大橋上的監視器座標常落在海上一點點）。每一列用「長度、編號」交替的連續編碼存。

重產：python3 scripts/build_region.py build
離線自我測試：python3 scripts/build_region.py selftest
"""
import json
import os
import sys
from urllib.request import Request, urlopen

SRC = os.environ.get("REGION_SRC", "https://raw.githubusercontent.com/g0v/twgeojson/master/json/twTown1982.topo.json")
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "region.json")
LAT0, LNG0, D, NY, NX = 21.85, 118.10, 0.005, 920, 800
COAST_FILL = 5                                          # 海上幾格內（約 2.5 公里）算最近的鄉鎮

UPGRADED = {"台北縣": "新北市", "桃園縣": "桃園市", "台中縣": "台中市", "台南縣": "台南市", "高雄縣": "高雄市"}
RENAMED = {("高雄市", "三民鄉"): "那瑪夏區", ("苗栗縣", "頭份鎮"): "頭份市", ("彰化縣", "員林鎮"): "員林市"}


def modern_name(county, town):
    """升格前的縣市、鄉鎮名 → 現在的名稱。"""
    town = town.replace("(海)", "")
    c = UPGRADED.get(county, county)
    key = (c, town)
    if key in RENAMED:
        return c, RENAMED[key]
    if county in UPGRADED and town[-1:] in "市鎮鄉":
        town = town[:-1] + "區"
    return c, town


def decode_topology(topo):
    """TopoJSON → [(縣市, 鄉鎮, [環, …]), …]；環是 [(經, 緯), …]。"""
    tf = topo.get("transform") or {"scale": [1, 1], "translate": [0, 0]}
    sx, sy = tf["scale"]
    tx, ty = tf["translate"]
    arcs = []
    for arc in topo["arcs"]:
        x = y = 0
        pts = []
        for dx, dy in arc:
            x += dx
            y += dy
            pts.append((x * sx + tx, y * sy + ty))
        arcs.append(pts)

    def ring(ids):
        out = []
        for i in ids:
            a = arcs[i] if i >= 0 else arcs[~i][::-1]
            out.extend(a if not out else a[1:])
        return out
    feats = []
    for g in next(iter(topo["objects"].values()))["geometries"]:
        p = g.get("properties") or {}
        polys = g["arcs"] if g["type"] == "MultiPolygon" else [g["arcs"]] if g["type"] == "Polygon" else []
        rings = [ring(r) for poly in polys for r in poly]
        if rings:
            feats.append((p.get("COUNTYNAME", ""), p.get("TOWNNAME", ""), rings))
    return feats


def inside(rings, x, y):
    """偶奇規則：外環與內環（湖、飛地）一起算。"""
    c = False
    for r in rings:
        n = len(r)
        j = n - 1
        for i in range(n):
            xi, yi = r[i]
            xj, yj = r[j]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                c = not c
            j = i
    return c


def build_grid(feats, lat0=LAT0, lng0=LNG0, d=D, ny=NY, nx=NX, fill=COAST_FILL):
    names, idx = [], {}
    shapes = []
    for county, town, rings in feats:
        key = modern_name(county, town)
        if key not in idx:
            idx[key] = len(names)
            names.append(list(key))
        xs = [p[0] for r in rings for p in r]
        ys = [p[1] for r in rings for p in r]
        shapes.append((idx[key], min(xs), max(xs), min(ys), max(ys), rings))
    grid = [[-1] * nx for _ in range(ny)]
    for j in range(ny):
        y = lat0 + (j + 0.5) * d
        row = [s for s in shapes if s[3] <= y <= s[4]]
        for i in range(nx):
            x = lng0 + (i + 0.5) * d
            for k, x0, x1, y0, y1, rings in row:
                if x0 <= x <= x1 and inside(rings, x, y):
                    grid[j][i] = k
                    break
    # 海上靠岸的格子：一圈一圈往外找最近的陸地
    if fill:
        src = [r[:] for r in grid]
        for j in range(ny):
            for i in range(nx):
                if src[j][i] >= 0:
                    continue
                best = None
                for r in range(1, fill + 1):
                    for jj in range(max(0, j - r), min(ny, j + r + 1)):
                        for ii in range(max(0, i - r), min(nx, i + r + 1)):
                            k = src[jj][ii]
                            if k >= 0:
                                dd = (jj - j) ** 2 + (ii - i) ** 2
                                if best is None or dd < best[0]:
                                    best = (dd, k)
                    if best:
                        break
                if best:
                    grid[j][i] = best[1]
    return names, grid


def rle(row):
    out, cur, n = [], row[0], 0
    for v in row:
        if v == cur:
            n += 1
        else:
            out += [n, cur]
            cur, n = v, 1
    return out + [n, cur]


def unrle(a):
    out = []
    for k in range(0, len(a), 2):
        out += [a[k + 1]] * a[k]
    return out


def lookup(doc, lat, lng):
    j = int((lat - doc["lat0"]) / doc["d"])
    i = int((lng - doc["lng0"]) / doc["d"])
    if not (0 <= j < doc["ny"] and 0 <= i < doc["nx"]):
        return None
    k = unrle(doc["rows"][j])[i]
    return doc["towns"][k] if k >= 0 else None


def build():
    with urlopen(Request(SRC, headers={"User-Agent": "Mozilla/5.0 (compatible; data-fetch/1.0)"}), timeout=120) as r:
        topo = json.loads(r.read())
    feats = decode_topology(topo)
    names, grid = build_grid(feats)
    doc = {"v": 1, "lat0": LAT0, "lng0": LNG0, "d": D, "ny": NY, "nx": NX, "towns": names,
           "rows": [rle(r) for r in grid],
           "cite": "鄉鎮界線：內政部（g0v twgeojson 簡化版）"}
    # 幾個已知點對一下，名稱換錯或格網偏了直接停，不要寫出錯的表
    for lat, lng, want in [(25.0330, 121.5654, ["台北市", "信義區"]), (25.0136, 121.4628, ["新北市", "板橋區"]),
                           (22.6116, 120.3004, ["高雄市", "苓雅區"]), (22.6260, 120.3600, ["高雄市", "鳳山區"]),
                           (25.1276, 121.7392, ["基隆市", "仁愛區"]), (24.4366, 118.3186, ["金門縣", "金城鎮"]),
                           (23.5654, 119.5793, ["澎湖縣", "馬公市"]), (26.1600, 119.9500, ["連江縣", "南竿鄉"])]:
        got = lookup(doc, lat, lng)
        if got != want:
            print(f"錯誤：({lat}, {lng}) 應該是 {want}，表上是 {got}；不寫出")
            return 1
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    print(f"寫出 {OUT}：{len(names)} 個鄉鎮區，{os.path.getsize(OUT) // 1024} KB")
    return 0


def selftest():
    okn = 0

    def check(cond, msg):
        nonlocal okn
        if not cond:
            print("✗", msg)
            sys.exit(1)
        okn += 1
        print("  ✓", msg)

    check(modern_name("台北縣", "板橋市") == ("新北市", "板橋區"), "升格前的縣：縣名換新、鄉鎮市改成區")
    check(modern_name("高雄縣", "三民鄉") == ("高雄市", "那瑪夏區"), "改名的區（三民鄉 → 那瑪夏區）")
    check(modern_name("苗栗縣", "頭份鎮") == ("苗栗縣", "頭份市"), "改制為市的鎮（頭份）")
    check(modern_name("基隆市", "中山區(海)") == ("基隆市", "中山區"), "「(海)」併回同一個區")
    check(modern_name("宜蘭縣", "頭城鎮") == ("宜蘭縣", "頭城鎮"), "沒升格的縣不動")
    # 兩個相鄰的方塊（量化座標＋位移），右邊那塊中間挖一個洞（湖）
    topo = {"type": "Topology", "transform": {"scale": [0.01, 0.01], "translate": [120, 23]},
            "arcs": [[[0, 0], [10, 0], [0, 10], [-10, 0], [0, -10]],
                     [[10, 0], [10, 0], [0, 10], [-10, 0], [0, -10]],
                     [[14, 4], [2, 0], [0, 2], [-2, 0], [0, -2]]],
            "objects": {"l": {"type": "GeometryCollection", "geometries": [
                {"type": "Polygon", "arcs": [[0]], "properties": {"COUNTYNAME": "台北縣", "TOWNNAME": "板橋市"}},
                {"type": "Polygon", "arcs": [[1], [2]], "properties": {"COUNTYNAME": "宜蘭縣", "TOWNNAME": "頭城鎮"}}]}}}
    feats = decode_topology(topo)
    check(len(feats) == 2 and feats[0][2][0][0] == (120.0, 23.0) and feats[1][2][0][1] == (120.2, 23.0), "TopoJSON 差分座標還原正確")
    names, grid = build_grid(feats, lat0=22.98, lng0=119.98, d=0.01, ny=16, nx=26, fill=0)
    doc = {"lat0": 22.98, "lng0": 119.98, "d": 0.01, "ny": 16, "nx": 26, "towns": names, "rows": [rle(r) for r in grid]}
    check(lookup(doc, 23.05, 120.05) == ["新北市", "板橋區"], "左邊方塊裡 → 新北市板橋區")
    check(lookup(doc, 23.02, 120.18) == ["宜蘭縣", "頭城鎮"], "右邊方塊裡 → 頭城鎮")
    check(lookup(doc, 23.05, 120.15) is None, "湖（內環）裡不算陸地")
    check(lookup(doc, 22.995, 120.05) is None, "不補海岸時，海上就是海上")
    names, grid = build_grid(feats, lat0=22.90, lng0=119.98, d=0.01, ny=24, nx=26, fill=2)
    doc = {"lat0": 22.90, "lng0": 119.98, "d": 0.01, "ny": 24, "nx": 26, "towns": names, "rows": [rle(r) for r in grid]}
    check(lookup(doc, 22.995, 120.05) == ["新北市", "板橋區"], "海上靠岸 2 格內補最近的鄉鎮（海堤、港口上的監視器）")
    check(lookup(doc, 22.905, 120.05) is None, "離岸超過 2 格不補")
    check(lookup(doc, 30, 120) is None, "格網外回 None")
    row = [-1, -1, 3, 3, 3, -1, 5]
    check(unrle(rle(row)) == row and rle(row) == [2, -1, 3, 3, 1, -1, 1, 5], "連續編碼來回一致")
    if os.path.exists(OUT):
        doc = json.load(open(OUT, encoding="utf-8"))
        check(lookup(doc, 25.0330, 121.5654) == ["台北市", "信義區"] and lookup(doc, 22.6260, 120.3600) == ["高雄市", "鳳山區"],
              "現有的 assets/region.json：台北 101 在信義區、鳳山在高雄市鳳山區")
    print(f"全部通過（{okn} 項）")
    return 0


if __name__ == "__main__":
    sys.exit(selftest() if sys.argv[1:] == ["selftest"] else build() if sys.argv[1:] == ["build"] else
             (print("用法：python3 scripts/build_region.py build | selftest") or 2))
