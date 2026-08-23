# US Strategy View 規格說明

本專案用 Python + `yfinance` 掃描 **S&P 500** 日K，依 **日K選股** S1 / S2 / S3 / S4 與 **策略選股** D1–D6（次日當沖觀察）產出選股（須先通過最新K線收盤價 `> 30` 且成交量 `> 2M`），寫入 `US_Strategy.json`（每日新增、不整檔覆蓋，累積近 10 日並刪除更舊資料），再以 `index.html` 深色頁面顯示（**預設只看最新一日**，可用日期範圍查舊資料；頂部分頁切換日K選股／策略選股）。GitHub Actions 於**台灣時間每天 05:00**自動執行，並可部署到 GitHub Pages。

---

## 1. 檔案結構

| 檔案 | 用途 |
| --- | --- |
| `US_Strategy.py` | 選股主程式（S1–S4 日K選股、D1–D6 策略選股），將當日結果新增至 JSON，並刪除超過 10 日前的資料 |
| `US_Strategy.json` | 近 10 日選股結果（每日新增、不整檔覆蓋；超過 10 日前的資料會刪除） |
| `index.html` | 深色模式檢視頁；預設只顯示最新一日，可用日期範圍查舊資料；「日K選股／策略選股」分頁 |
| `requirements.txt` | Python 套件 |
| `.github/workflows/us-strategy.yml` | GitHub 每日排程 |
| `.nojekyll` | 讓 GitHub Pages 不要用 Jekyll 處理檔案 |

---

## 2. JSON 欄位

每次執行會寫入台灣時區（`Asia/Taipei`）的時間。一筆選股至少包含：

| 欄位 | 說明 |
| --- | --- |
| `date` | 台灣選股日期 `YYYY-MM-DD` |
| `time` | 台灣選股時間 `HH:MM:SS` |
| `symbol` | 美股代碼 |
| `strategy_desc` | 策略說明 |
| `strategy` | `S1` / `S2` / `S3` / `S4`（日K選股）或 `D1`–`D6`（策略選股／次日當沖觀察） |
| `side` | `buy`（買進）或 `short`（賣空） |
| `signal_date` | 最新日K的美股交易日 |
| `name` | 公司名稱（仍寫入 JSON，網頁表格不顯示此欄） |
| `close` | 最新收盤價 |
| `metrics` | 該策略的驗證數字。**所有策略**一律含 `yesterday_change_pct`、`today_change_pct`（相對前一日收盤的百分比），供表格「昨日漲跌／今日漲跌」顯示 |

`meta` 另有掃描檔數、成功／失敗數、宇宙名稱、當日筆數 `today_pick_count`、保留天數 `retention_days`、本次刪除筆數 `pruned_count`。`pick_count` 為檔案內近 10 日合計筆數。

### 2.1 JSON 資料保留（每日新增、刪除 10 日前）

每次執行 `US_Strategy.py` 時：

1. 讀取既有 `US_Strategy.json` 的 `picks`（檔案不存在或損毀則視為空白）。
2. **新增當日結果，不整檔覆蓋**：其他日期的紀錄全部保留；僅以本次台灣日期的掃描結果，取代同一 `date` 的舊紀錄（同一日重複執行不會堆疊重複）。
3. 依每筆 `date`（台灣選股日期 `YYYY-MM-DD`）刪除**超過 10 日前**的資料：保留 `date >= 執行日 − 10 日`（含當日與第 10 日前當日），其餘刪除。日期無法解析的紀錄一併刪除，避免無效資料堆積。
4. 將保留後的清單寫回 JSON，避免檔案無限增大。

例：執行日為 `2026-08-19` 時，保留 `2026-08-09`（含）之後的選股，刪除 `2026-08-08` 及更早的紀錄。同一日重複執行只更新當日，不會覆蓋其他日期。

### 2.2 網頁顯示（最新一日 + 日期範圍）

`index.html` 讀取整個 `US_Strategy.json`，但畫面規則如下：

1. **預設只顯示最新一日**：以 `meta.date`（本次台灣選股日期）為準，只列出該日的 `picks`。當日 0 筆時顯示空狀態，不會自動改顯示前一日。
2. **日期範圍**：工具列提供起日／迄日，可查 JSON 內仍保留的舊資料（最多近 10 日）。統計數字、策略／買賣篩選與表格都依目前選取的範圍重算。
3. **最新一日按鈕**：一鍵把起迄日重設回 `meta.date`。
4. 表格另顯示「選股日」（台灣 `date`）與「K線日」（`signal_date`），方便跨日比對。
5. **昨日漲跌／今日漲跌**：每筆選股都顯示最新兩根日K相對前一日收盤的漲跌幅（來自 `metrics.yesterday_change_pct` / `metrics.today_change_pct`）。S1–S4 與 D1–D6 皆寫入這兩個欄位，不得因策略不同而空白。重新掃描時，若近 10 日舊紀錄缺少這兩個欄位，會依該筆 `signal_date` 從日K補上。

可查區間下限為檔案內最早的 `picks.date`，上限為 `meta.date` 與檔案內最晚選股日的較大者。

### 2.3 頁面呈現規則

1. **不顯示頁尾統計說明**：頁面最下方**不放**「掃描宇宙：S&P 500　成功 x / x　失敗 x　JSON 保留近 10 日　可查詢 …」這類說明列，之後也不再加回。
2. **表格不用內捲軸**：表格完整展開列出全部資料，不設 `max-height`、不用容器內捲軸，捲動一律交給**頁面外捲軸**；表頭捲動時吸附在視窗頂端（sticky）。
3. **頂部漸層捲動進度條**：與 [US-FinanceView](https://johsok.github.io/US-FinanceView/) 相同的特效——頁面最上方固定一條 4px 進度條，背景為 `linear-gradient(90deg, #00f260, #0575e6)` 漸層並帶光暈，以 `scroll` 事件搭配 `requestAnimationFrame` 依頁面捲動比例更新寬度。
4. **表格欄位**：顯示「代碼、策略、方向、條件、收盤、選股日、K線日、昨日漲跌、今日漲跌、驗證數據」。**不顯示「公司」欄**（JSON 仍保留 `name`，搜尋可用）。
5. **「條件」欄寬度為原先的 2 倍**（約 `640px`），以完整顯示策略說明。
6. **標題列置中**：表頭「代碼、策略、方向、條件、收盤、選股日、K線日、昨日漲跌、今日漲跌、驗證數據」一律水平置中。
7. **分頁**：標題下方提供兩個小分頁。**日K選股**只列出 S1–S4；**策略選股**只列出 D1–D6（次日當沖觀察名單）。統計卡片與策略篩選按鈕隨分頁切換；買進／賣空、日期範圍、搜尋共用。分頁選擇會記在瀏覽器 `localStorage`。

---

## 3. 選股規則（日K）

K線顏色採**美股慣例：綠漲、紅跌**（收盤 > 開盤為綠K，收盤 < 開盤為紅K）。漲跌幅為相對**前一日收盤**。

### 3.0 各策略共用門檻

S1 / S2 / S3 / S4 / D1–D6 **皆須先通過**以下條件，才會進入選股名單（與個別策略訊號為「且」關係）：

- 最新一根日K的**收盤價 `> 30` 元**
- 最新一根日K的**成交量 `> 2,000,000`（2M 股）**

任一條件不成立，該檔當次掃描不產出任何策略紀錄。

### S1 紅綠K反轉

三項條件為「且」關係。漲跌幅為相對前一日收盤。

- **買進 `buy`**
  - 昨日紅K，且跌幅 `< -3%`
  - 今日綠K，且漲幅 `> 3%`
  - 今高 `<=` 昨高 `* 1.05`
- **賣空 `short`**
  - 昨日綠K，且漲幅 `> 3%`
  - 今日紅K，且跌幅 `< -3%`
  - 今低 `>=` 昨低 `* 0.95`

`metrics` 另寫入昨／今開收，以及買進的 `yesterday_high` / `today_high`、賣空的 `yesterday_low` / `today_low` 供驗證。

### S2 影線（支撐／壓力）

影線比例 = 影線長度 /（最高價 − 最低價）。無高低差的K線不計。振幅條件為該根K的 `(最高 − 最低) / 最低 >= 3%`；影線占比與振幅須**同一根K同時成立**才計入 1 次。

- **買進 `buy`**：近 10 日內，至少 2 根K線的**下影線**占比 `> 50%`，且該根K振幅 `(最高 − 最低) / 最低 >= 3%`。
- **賣空 `short`**：近 10 日內，至少 2 根K線的**上影線**占比 `> 50%`，且該根K振幅 `(最高 − 最低) / 最低 >= 3%`。

`metrics.hits` 每筆含 `date`、`ratio`（影線占比 %）、`range_pct`（振幅 %）。

### S3 爆量反轉

「前 5 日均量」**不含今日**。

- **買進**：近 3 日最低價 = 近 5 日最低價，今日成交量 `>` 前 5 日均量的 2 倍，且今日上漲 `> 3%`。
- **賣空**：近 3 日最高價 = 近 5 日最高價，今日成交量 `>` 前 5 日均量的 2 倍，且今日下跌超過 3%（漲跌幅 `< -3%`）。

### S4 最新兩根日K單日大幅漲跌

看**最新 2 根日K**（倒數第一、倒數第二）各自相對**前一日收盤**的漲跌幅。「小於含 -30% 以上／大於含 30% 以上」表示**達 30%（含）以上**（買進 `<= -30%`，賣空 `>= +30%`）。兩根之中**至少 1 根**符合即可。

- **買進**：`S4(買進：最新的2個日K的個股收盤價，其中1個收盤價漲跌幅小於含-30%以上)` —— 昨日或今日漲跌幅 `<= -30%`。
- **賣空**：`S4(賣空：最新的2個日K的個股收盤價，其中1個收盤價漲跌幅大於含30%以上)` —— 昨日或今日漲跌幅 `>= +30%`。

同一檔若兩根分別大跌與大漲，可同時寫入買進與賣空各一筆。`metrics` 另寫入 `yesterday_open`、`yesterday_close`、`today_open`、`today_close` 供驗證。

同一檔股票可同時命中多個策略（含日K 與策略選股），會各寫一筆。

日K 下載區間為 **1 年**（供 D4 的 SMA(200) 與 ATR／相對量計算）。資料不足的個股會跳過對應策略，不影響其他策略。

掃描宇宙預設為 S&P 500（優先讀 [constituents.csv](https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv)，失敗則改 Wikipedia，再失敗則用程式內建備援清單）。

### 3.1 策略選股（D1–D6，次日當沖觀察）

網頁「策略選股」分頁使用 D1–D6。這六條是依主流當沖教材與可公開文獻，挑出**期望值較高、且可用日K 在收盤後排出次日觀察名單**的選股法（真正進場仍在次日盤中，例如 5 分鐘 ORB）。掃描時同樣通過 3.0 共用門檻。

#### D1 相對量 In Play

來源：[Aziz《How to Day Trade for a Living》](https://traderlion.com/trading-books/how-to-day-trade-for-a-living/)（Stocks in Play／Alpha Predator）；Zarattini, Barbon, Aziz, [*A Profitable Day Trading Strategy for the U.S. Equity Market*](https://doi.org/10.2139/ssrn.4729284)（SSRN 4729284；5 分鐘 ORB 搭配 In Play，前 20 檔相對量組合淨績效與 Sharpe 顯著優於未篩選宇宙）。

三項為「且」關係，方向由收盤在當日振幅中的位置決定：

- 今日成交量 `>=` 前 20 日均量（不含今日）的 **2 倍**
- `ATR(14) > 0.50` 美元
- 今日漲跌幅絕對值 `>= 2%`
- **買進**：收盤位於當日振幅上半 `(Close − Low) / (High − Low) >= 0.5`（偏多 ORB）
- **賣空**：收盤位於當日振幅下半（偏空 ORB）

全市場掃描後，D1 **只保留相對成交量最高的前 20 筆**（對齊文獻 top 20 Stocks in Play）。`metrics` 含 `rvol`、`atr14`、`clv_pct`、`volume`、`prev20_avg_volume`。

#### D2 NR7 波動收縮突破

來源：Crabel (1990) *Day Trading with Short Term Price Patterns and Opening Range Breakout*；[StockCharts ChartSchool：Narrow Range Day (NR7)](https://chartschool.stockcharts.com/table-of-contents/trading-strategies-and-models/trading-strategies/narrow-range-day-nr7)；Connors & Raschke (1995) *Street Smarts*（NR7 後突破進場）。原理：波動收縮後常接擴張。

- 今日振幅 `(High − Low)` **嚴格小於**前 6 日每一日的振幅（近 7 日最窄）
- **買進**：收盤 `>=` 當日中點 `(High + Low) / 2`，次日觀察突破今高
- **賣空**：收盤 `<` 當日中點，次日觀察跌破今低

`metrics` 含 `range`、`prior6_min_range`、`midpoint`、`inside_day`（今日完全包在昨日內則為 true，屬更高信念）、`breakout_high` / `breakout_low`。

#### D3 趨勢日延續

來源：Connors & Raschke (1995) *Street Smarts* 趨勢日／寬幅日延續：大振幅且收盤靠近當日極端，隔日延續機率較高。

- 今日振幅 `(High − Low) >= 1.5 × ATR(14)`
- **買進**：收盤在當日振幅 **最高 20%**（`(Close − Low) / (High − Low) >= 0.80`）
- **賣空**：收盤在當日振幅 **最低 20%**（`<= 0.20`）

`metrics` 含 `atr14`、`range`、`range_atr_ratio`、`clv_pct`。

#### D4 RSI(2) 均值回歸

來源：Larry Connors *Short-Term Trading Strategies That Work*；業界常見 [RSI(2) 短線回歸](https://www.quantifiedstrategies.com/) 實作。在長期趨勢內等待極短週期 RSI 極端，隔日回歸期望值較高。

- **買進**：`RSI(2) <= 10` 且收盤 `>` `SMA(200)`（上升趨勢內超賣）
- **賣空**：`RSI(2) >= 90` 且收盤 `<` `SMA(200)`（下降趨勢內超買）

K 線不足 200 根者不產出 D4。`metrics` 含 `rsi2`、`sma200`。

#### D5 20 日通道突破

來源：Donchian Channel／Turtle Traders 突破系統；動能文獻 George & Hwang (2004) *The 52-Week High and Momentum Investing*（靠近前高的動能溢價）。以 20 日通道作當沖前篩，並要求量能確認以降低假突破。

- **買進**：今日最高價 = 近 20 日最高價，今日量 `>=` 前 20 日均量的 1.5 倍，且收陽（綠K）
- **賣空**：今日最低價 = 近 20 日最低價，今日量 `>=` 前 20 日均量的 1.5 倍，且收陰（紅K）

`metrics` 含 `rvol`、`volume`、`prev20_avg_volume`、`high20`、`low20`。

#### D6 連續動能（Gap-and-Go／ABCD 日K前篩）

來源：Aziz ABCD／Bull Flag（連續強勢後的隔日延續）；Ross Cameron / Warrior Trading **Gap-and-Go**（強勢股 + 量能，作為次日開盤動能觀察）。用日K 在收盤後找出已連續走強／走弱的標的。

- **買進**：昨日與今日漲幅皆 `> 2%`，今日量 `>=` 前 20 日均量的 1.5 倍，且今日收盤 `>` 昨日最高價
- **賣空**：昨日與今日跌幅皆 `< -2%`，今日量 `>=` 前 20 日均量的 1.5 倍，且今日收盤 `<` 昨日最低價

`metrics` 含 `rvol`、`volume`、`prev20_avg_volume`、`yesterday_high` / `yesterday_low`。

---

## 4. 本機執行

需要 Python 3.10+（建議 3.12）。

```bash
cd US_Strategy_View
python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python US_Strategy.py
```

不要直接雙擊 `index.html`（瀏覽器會擋本地 JSON）。請在專案目錄開本機伺服器：

```bash
python -m http.server 8000
```

瀏覽器開啟：`http://localhost:8000/`

---

## 5. GitHub 整體設定（自動跑 Python + 顯示網頁）

目標：每天台灣 05:00 跑 `US_Strategy.py` → 更新 `US_Strategy.json` → 用 GitHub Pages 公開 `index.html`。

### 5.1 建立 GitHub 儲存庫並推檔

1. 登入 [GitHub](https://github.com)，右上角 **New repository**。
2. Repository name 例如 `US_Strategy_View`。可選 Public 或 Private（Private 的 GitHub Pages 需要付費方案）。
3. **不要**勾選 Add a README（避免與本地檔案衝突）。
4. Create repository。
5. 在本機專案執行（把 `你的帳號` 換成實際帳號）：

```bash
cd US_Strategy_View
git init
git add US_Strategy.py US_Strategy.json index.html requirements.txt SPEC.md .gitignore .nojekyll .github
git commit -m "feat: 美股日K策略選股與每日排程"
git branch -M main
git remote add origin https://github.com/你的帳號/US_Strategy_View.git
git push -u origin main
```

### 5.2 打開 GitHub Actions 寫入權限（才能 commit JSON）

1. 開啟 repo → **Settings** → **Actions** → **General**。
2. **Actions permissions**：選 **Allow all actions and reusable workflows**。
3. 同一頁往下 **Workflow permissions**：
   - 選 **Read and write permissions**
   - 勾選 **Allow GitHub Actions to create and approve pull requests**（可選，本專案非必須）
4. Save。

若未給寫入權限，排程能跑 Python，但 `git push` 會失敗，網頁會一直看到舊 JSON。

### 5.3 打開 GitHub Pages（讓 index.html 上線）

1. repo → **Settings** → **Pages**。
2. **Build and deployment** → Source 選 **GitHub Actions**（不要選 Deploy from a branch）。
3. 第一次執行 workflow 時，GitHub 會建立 `github-pages` environment。
4. 到 **Settings** → **Environments** → **github-pages**：
   - 若有 **Required reviewers**，第一次部署會卡住等你批准。個人專案建議拿掉 reviewers，或手動批准。
   - 不需設定 Secrets。

部署成功後網址通常是：

`https://你的帳號.github.io/US_Strategy_View/`

（若 repo 名稱不是 `US_Strategy_View`，路徑改成該 repo 名。若這是帳號的 `帳號.github.io` 特殊 repo，則在網域根目錄。）

### 5.4 第一次請「手動」跑一次（不要只等隔天）

排程最早要等到下一個台灣 05:00。請先手動確認：

1. repo 上方 **Actions**。
2. 左側選 **Daily US Strategy**。
3. 右側 **Run workflow** → 分支選 `main` → **Run workflow**。
4. 點進該次 run，確認兩個 job：
   - `scan`：安裝套件、執行 `US_Strategy.py`、commit `US_Strategy.json`
   - `pages`：把 `index.html` + `US_Strategy.json` 佈到 Pages
5. 兩個都是綠色後，打開 Pages 網址，應看到台灣日期／時間與選股卡片。

`scan` 可能要 3～10 分鐘（下載約 500 檔日K）。Yahoo 偶發擋 IP 時，程式會分批重試；部分代碼失敗會寫在 JSON 的 `meta.failed`。

### 5.5 每日 05:00 如何對應 GitHub cron

GitHub cron 只用 **UTC**。

| 台灣時間 | UTC | yml |
| --- | --- | --- |
| 每天 05:00 | 前一日 21:00 | `cron: "0 21 * * *"` |

夏令期間美股約在台灣時間隔日 04:00 收盤，05:00 通常已有完整日K；冬令時美股約 05:00 收盤，若 JSON 常缺當日K，把 cron 改成 `0 22 * * *`（台灣 06:00）即可。

可在 [crontab.guru](https://crontab.guru/) 檢查。

### 5.6 工作流程實際做什麼

`.github/workflows/us-strategy.yml` 會：

1. 被 **排程** 或 **手動 Run workflow** 觸發（不會因為你一般 push 程式碼而重複掃股）。
2. 用 Python 3.12 安裝 `requirements.txt`。
3. 執行 `python US_Strategy.py`。
4. 若 JSON 有變化，以 `github-actions[bot]` 提交並 push 回 `main`。
5. 上傳 `index.html` + `US_Strategy.json` 到 GitHub Pages。

HTML 用相對路徑讀 `US_Strategy.json`，與 Python「執行」是分開的：Python 負責產出資料，Pages 只負責靜態顯示。

### 5.7 若 main 有分支保護

若 Settings → Branches 規定 main 不能直接 push，bot 的 `git push` 會失敗。作法擇一：

- 暫時允許 GitHub Actions 寫入 main；或
- 拿掉「限制 administrators / 禁止直接 push」對此 repo 的限制；或
- 改成只部署 Pages、不 commit JSON（頁面仍會更新，但 repo 內 JSON 不會變）。最後一種需改 yml，需要時再調整。

### 5.8 Private repo 注意

- Actions 分鐘數算在帳號額度內。
- Private 的 GitHub Pages 需 GitHub Pro / Team / Education 等方案。免費帳號請用 **Public** repo。

---

## 6. 常見問題

| 狀況 | 處理 |
| --- | --- |
| Actions 顯示 `Resource not accessible by integration` | 5.2 改成 Read and write permissions 後再手動跑一次 |
| `pages` job 失敗、`scan` 成功 | 5.3 將 Pages source 設成 GitHub Actions，並批准 `github-pages` environment |
| 網頁空白或 fetch 失敗 | 確認 Pages 網址路徑正確，且該次部署有包含 `US_Strategy.json` |
| 網頁只看到一天的選股 | 這是預設（最新一日）。用工具列「日期範圍」可查 JSON 內仍保留的舊資料 |
| 選股為 0 筆 | 當日可能本來就沒有股票符合條件（含共用門檻：收盤價 `> 30` 且成交量 `> 2M`），屬正常。可改日期範圍查看其他日。策略選股分頁若為 0，代表當日沒有 D1–D6 命中（D1 另限制最多 20 筆） |
| JSON 越來越大 | 每次執行會新增當日、不整檔覆蓋，並刪除超過 10 日前的 `picks`，見 2.1 |
| Yahoo 下載大量失敗 | 稍後手動再跑；或看 `meta.failed`。GitHub 資料中心 IP 偶發被 Yahoo 限制 |
| 想改掃描清單 | 編輯 `US_Strategy.py` 的 `load_universe()` / `FALLBACK_TICKERS` |
| 昨日漲跌／今日漲跌顯示「—」 | 舊 JSON 可能缺欄。重新執行 `US_Strategy.py` 後，當日每筆 `metrics` 都會寫入這兩個百分比 |
| 策略選股分頁看不到 D1–D6 | 需重新執行 `US_Strategy.py`（或等每日排程）寫入新 JSON；舊檔只有 S1–S4 |

---

## 7. 免責

本工具僅供研究與排程展示，不是投資建議。美股有缺口、停牌、調整價差，訊號可能與券商K線略有出入。D1–D6 為收盤後日K 掃描的**次日當沖觀察名單**，不是自動下單；文獻績效含樣本期、成本與流動性假設，實盤結果會不同。
