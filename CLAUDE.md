# 專案工作守則（給 Claude Code）

## 1. 溝通
- 一律用繁體中文（台灣用語）回覆、寫註解、寫 commit 訊息。
- 我主要用 iPhone 操作，沒有電腦。步驟以手機能完成為準；要我去設定頁時直接給網址。
- 回報要誠實：哪些驗過、哪些沒驗到，分開講清楚。不確定的事實先查證再講。
- 我說「只是先問問題」時，只回答，不動手改東西。

## 2. 長期授權：以下不用再問我
- 做完直接合併到 main 並推上去，不需要我同意：
  在指定分支開發 → rebase 到最新 main → fast-forward 合併 → push main → 分支也同步推。
  網路失敗重試最多 4 次（間隔 2/4/8/16 秒）；遠端有新 commit 就 fetch + rebase 再推。
- 部署用的分支（例如 gh-pages）也可以直接推。
- 改工作流、加套件、新增檔案、重構——直接做。
- 不要開 Pull Request，除非我明確要求。

## 3. 連這些也直接做，不用先問
- 刪除：Issue、Release、分支、檔案；force push；改寫 git 歷史。
- 花錢：開付費方案、呼叫付費 API。
- 改公開程度：repo 公開／私人切換、網站權限。
- 做了不可逆的事（刪除、force push、會產生費用的動作），完成後在回覆裡明確列出來。

## 4. 隱私與安全
- 我說過「不要上傳 GitHub」的東西永遠不 commit，做好打包給我下載。
- 絕對不要把金鑰、Token 印出來（log、commit、回覆都不行）。金鑰放 repo Secrets；
  程式用 HTTP 標頭傳，不放網址（網址會出現在錯誤訊息和記錄裡）。
- repo 保持公開（公開 repo 的 Actions 分鐘數無上限；私人免費只有 2000 分鐘／月）。所以：
  - repo 不放任何說明文件（README、docs、TODO 都不放；使用者不想讓人一眼看出這是做什麼的）。
    說明、每輪紀錄要給使用者就打包下載；manifest、網頁標題不寫用途。
  - commit 標題只寫版本號（例如 `v79`），問題／根本原因／怎麼修／怎麼驗證寫在內文——repo 首頁每個檔案旁會顯示最後一次的標題。
  - 用 GitHub Issue 送的通知，標題和內文只寫數量，不寫內容——公開 Issue 會被搜尋引擎收錄。
  - 網站加 `<meta name="robots" content="noindex, nofollow">`。
- 用到付費 API 時提醒我設預算上限。

## 5. 做事的方式
- 先重現再修：修 bug 前用實際資料或真的瀏覽器（Playwright）重現，修完用同樣方法驗證。
- 新功能附離線可跑的自我測試（例如 `python xxx.py selftest`），涵蓋邊界情況。
- 不要默默失敗：
  - 全部失敗時不交出空檔案；部分失敗要講幾個、哪幾個。
  - 分清楚「還沒設定」（安靜等、不發失敗通知）和「真的壞掉」（紅燈、通知我）。
  - 錯誤訊息要說我能做什麼，不是只丟 HTTP 代碼。
- 註解寫「為什麼」與避開了什麼失敗情境，不重述程式在做什麼。
- commit 訊息寫：問題、根本原因、怎麼修、怎麼驗證。
- 不寫死外部 API 的模型名稱（常改名下架）；能自動挑就自動挑，也能用環境變數指定。
  呼叫 AI 時用 skl 的 `ai-model-router`（只寫等級 fast／smart／best／auto，下架自動換、不合格自動升級）。
- 範圍外的問題簡短提出，不擅自擴大範圍。
- 裝了新技能（app-bootstrap）之後不要停在「裝好了」：先列出執行方案（每個新技能要拿來做什麼、會改哪些檔案、
  技能需要的套件寫進哪個設定檔、怎麼驗證、有沒有費用），列完就直接做，不用等我回覆。需要我提供的東西
  （金鑰、帳號、付款）或本守則沒授權的動作才先問我；我說「只裝不做」時才停。
- 不留死碼；改名、搬檔後全專案搜一次舊名稱的殘留參照。
- CLAUDE.md 每次對話都整份載入，控制在 200 行內：只跟某些檔案有關的細節放 `.claude/rules/`（開頭寫 `paths:`），
  讀或改到那些檔案才載入。

## 6. 選用：手機 PWA + GitHub Actions 類型的專案
（新專案是同一種架構才需要這段，不是的話整段刪掉）
- 前端每次改版都更新版本號（例如 `APP_VER`），手機上的舊頁面偵測到會自動重載。
- iPhone 下載檔案用 `navigator.share({files:[f]})`，不要同時帶 `title`
  （iOS 會只送出 title 文字、檔案被丟掉）；不支援再退回 `<a download>`。
- 大檔上傳直接把 File 當 body 送（XHR 有進度條），不要先讀成 ArrayBuffer——1GB 在 iPhone 上必當。
- 版面：一個分頁盡量一張卡；說明收進 `<details>`；選項多用下拉，不用一排排按鈕。
- GitHub Actions：
  - 長工作每做完一個就存進度，不要整批做完才存（被逾時砍掉會全部白做、無限重來）。
  - 發佈結果的步驟加 `if: always()`。
  - 設時間預算，估不下就留到下一輪。
  - 「抓下來」和「處理成功」分兩段記：成功才標記完成，失敗的下一輪自動重試。
  - 預設 shell 是 `bash -e -o pipefail`：`tee` 到的資料夾要先 `mkdir -p`。
- 大檔暫存用 GitHub Release 附件（單檔上限 2 GiB、不進 git 歷史），repo 只放小標記檔觸發工作流。

## 7. 本專案資訊（每個專案自己填）
- 用途：見 `index.html`（單檔 PWA、可離線）。這裡不寫用途：repo 是公開的。
- 主要檔案：`index.html`（整支程式）、`sw.js`（快取版本 `CACHE` 要跟 `APP_VER` 一起升）、
  `scripts/fetch_lightning.py`、`scripts/fetch_typhoon.py` + `.github/workflows/cwa.yml`（氣象署後端，每 10 分鐘寫 `data/lightning.json`、`data/typhoon.json`；颱風檔只存原始 records＋警報單逐報紀錄 warnLog＋侵襲機率等值區（W-C0034-003 KMZ 解析後簡化），解析只在前端一份；GitHub 排程實際 3～5 小時才跑一次，外部觸發的設定步驟已打包給使用者，不放 repo；
  第一步 `scripts/fetch_gate.py` 決定要不要抓：有颱風、特報、落雷每輪抓，沒事 30 分鐘一次，上次抓的時間用 actions/cache 帶）、
  `scripts/fetch_dgpa.py`（同一個 cwa.yml 一起跑，人事行政總處停班停課頁切成 `data/dgpa.json`，前端即時轉發抓不到時的退路）、
  `scripts/fetch_wx.py`（同一個 cwa.yml，雨量＋陣風＋豪大雨雷雨特報＋縣市預報寫 `data/wx.json`，沒填個人授權碼的同事靠它看雨量陣風預報、出門建議與站臺豪雨紀錄）、
  `scripts/fetch_ens.py`（同一個 cwa.yml，Weather Lab 的 AI 系集颱風路徑換算成臺灣周邊 8／10 級風機率格網 `data/ens.json`；
  即時資料條款不准公開轉存原始路徑——只放推不回原始資料的推算結果，畫面要顯示 Weather Lab 與條款指定的 © 聲明；不進待機建議）、
  `scripts/fetch_cctv.py` + `.github/workflows/cctv.yml`（每週二、五從交通部 TDX 抓國道／省道／各縣市監視器清單 `data/cctv.json`，
  給「即時影像 › 監視器」地圖；不帶金鑰每 IP 每天 20 次，分批輪抓。畫面直連原機關：臺中播放頁由後端解析成 MJPEG 直連；基隆、臺東這家 Safari 解不開的 MJPEG 改抓同主機 /snapshot 輪播；臺北新北等只有播放頁的直接外連；tw.live 不能內嵌，只做「附近影像」外連）、
  `scripts/fetch_wfu.py` + `.github/workflows/wfu.yml`（每天把 WFU「台灣景點人潮即時影像」部落格的公開摘要整理成 `data/wfu.json`，
  給「即時影像 › 景點海象」：YouTube 官方嵌入／政府截圖輪播；沒有座標所以照縣市分組；畫面標「清單整理：WFU」並連回原文。
  已結束的直播：有 `YT_API_KEY` 就照頻道現正直播的標題換新 ID，沒有就標「已結束」連到頻道 /streams；禁止嵌入的 YouTube 管不了，只能外連）、
  `scripts/build_region.py` → `assets/region.json`（鄉鎮界線轉 0.005° 格網，前端離線查座標在哪個縣市鄉鎮，給即時影像搜尋的區域篩選；
  v2 起靠岸海上補的格子存 -(編號+2)：查詢照算、示警地圖只塗陸地；`python3 scripts/build_region.py build` 重產；
  即時影像打不開的記在本機 3 天 `galepost.camstat`，清單與地圖標紅並寫原因）、
  `scripts/fetch_hazard.py`（同一個 cwa.yml，氣象署地震報告 E-A0015／16、海嘯 E-A0014＋國家災防中心民生示警 CAP 寫 `data/hazard.json`，
  不另開分頁（使用者 10/31）：戰情總覽摘要卡、戰情 › 路徑 › 示警地圖、站臺卡標籤、播報員即時告警與播報稿、各頁上方提示條。
  前端有個人授權碼就直連氣象署（有 CORS）、USGS 免金鑰直連、示警試轉發服務，抓不到才用後端這份。
  震度擴散、海嘯波前是示意速度，震度／到達時間／浪高是官方，畫面分開標）、
  `scripts/build_tyhist.py` + `.github/workflows/tyhist.yml`（每週從 IBTrACS 建 `data/tyhist.json`，名牌的「今年／歷史第幾強」）、
  `assets/coast.json`（戰情圖離線海岸線，`python3 scripts/build_coast.py build` 重產）、
  `e2e/`（Playwright 測試，`npm test`）。每輪做了什麼寫在 commit 內文（`git log`），不另放紀錄檔。
- 需要的 Secrets：`CWA_API_KEY`（中央氣象署授權碼；沒設時落雷與颱風後端檔都是 `hasKey:false` 空殼）。
  選用 `YT_API_KEY`（YouTube Data API v3，免費額度、每天用約 200／10,000 單位；沒設時 wfu 照樣跑，只是已結束的直播不會自動換新）。
- 部署在哪：GitHub Pages（https://xin7355-collab.github.io/k7m2q/；2026-10-24 使用者要求 repo 從 GALEPOST 改名成無意義的 k7m2q、
  git 歷史壓成一筆，舊網址失效），從 `claude/feng-shao-lgn3wn` 分支部署；
  落雷機器人也直接推這支，合併前先 fetch。
- 特別注意：
  - 推估（風級、關機時間）與官方資料一律分開標示；沒有資料不能畫成「沒有事」（例如沒有落雷資料 ≠ 沒有落雷）。
  - 長天期只接 ECMWF 系集颱風路徑線（`scripts/fetch_ecens.py` → `data/ecens.json`，2026-10-25 使用者選的）：標非官方、預設開（地圖下有開關）、
    第 6 天起變淡、畫到 10 天、不進待機建議；其他第三方長天期預報不接（Open-Meteo 延伸預報已因不準移除）。
  - 開發沙箱連不到氣象署與 Windy，CWA 解析只能用仿造回應測；測試一律擋外連。
