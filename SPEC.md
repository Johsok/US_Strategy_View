# US Strategy View 規格說明

本專案用 Python + `yfinance` 掃描 **S&P 500** 日K，依 S1 / S2 / S3 產出當日選股，寫入 `US_Strategy.json`，再以 `index.html` 深色頁面顯示。GitHub Actions 於**台灣時間每天 05:00**自動執行，並可部署到 GitHub Pages。

---

## 1. 檔案結構

| 檔案 | 用途 |
| --- | --- |
| `US_Strategy.py` | 選股主程式，寫入 JSON |
| `US_Strategy.json` | 當日選股結果（每次執行會覆寫） |
| `index.html` | 深色模式檢視頁，讀取 JSON |
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
| `strategy` | `S1` / `S2` / `S3` |
| `side` | `buy`（買進）或 `short`（賣空） |
| `signal_date` | 最新日K的美股交易日 |
| `name` | 公司名稱 |
| `close` | 最新收盤價 |
| `metrics` | 該策略的驗證數字 |

`meta` 另有掃描檔數、成功／失敗數、宇宙名稱。

---

## 3. 選股規則（日K）

K線顏色採**美股慣例：綠漲、紅跌**（收盤 > 開盤為綠K，收盤 < 開盤為紅K）。漲跌幅為相對**前一日收盤**。

### S1 紅K + 綠K 反轉

- **買進**：昨日為紅K且跌幅 `< -3%`，今日為綠K且漲幅 `> 3%`。
- **賣空**：昨日為綠K且漲幅 `> 3%`，今日為紅K且跌幅 `< -3%`。

原文「S1 賣空：紅K(上漲)大於 -3%」依反轉語意實作為**紅K下跌超過 3%**。

### S2 影線

影線比例 = 影線長度 /（最高價 − 最低價）。無高低差的K線不計。

- **買進**：近 10 日內，至少 2 根K線的**下影線**占比 `> 50%`。
- **賣空**：近 10 日內，至少 2 根K線的**上影線**占比 `> 50%`。

### S3 爆量反轉

「前 5 日均量」**不含今日**。

- **買進**：近 3 日最低價 = 近 5 日最低價，今日成交量 `>` 前 5 日均量的 2 倍，且今日上漲 `> 3%`。
- **賣空**：近 3 日最高價 = 近 5 日最高價，今日成交量 `>` 前 5 日均量的 2 倍，且今日下跌超過 3%（漲跌幅 `< -3%`）。

同一檔股票可同時命中多個策略，會各寫一筆。

掃描宇宙預設為 S&P 500（優先讀 [constituents.csv](https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv)，失敗則改 Wikipedia，再失敗則用程式內建備援清單）。

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
| 選股為 0 筆 | 當日可能本來就沒有股票符合條件，屬正常 |
| Yahoo 下載大量失敗 | 稍後手動再跑；或看 `meta.failed`。GitHub 資料中心 IP 偶發被 Yahoo 限制 |
| 想改掃描清單 | 編輯 `US_Strategy.py` 的 `load_universe()` / `FALLBACK_TICKERS` |

---

## 7. 免責

本工具僅供研究與排程展示，不是投資建議。美股有缺口、停牌、調整價差，訊號可能與券商K線略有出入。
