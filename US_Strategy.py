#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
美股日K策略選股：掃描 S&P 500，將訊號寫入 US_Strategy.json。

日K選股為 S1 / S2 / S3 / S4；策略選股為 D1–D6（短線日K）。
每次執行會合併當日結果（新增、不整檔覆蓋），並刪除超過 10 日前的紀錄。
時間一律使用台灣時區 Asia/Taipei。
"""

from __future__ import annotations

import json
import math
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import pandas as pd
import yfinance as yf

TZ_TAIPEI = ZoneInfo("Asia/Taipei")
ROOT = Path(__file__).resolve().parent
OUTPUT_JSON = ROOT / "US_Strategy.json"
UNIVERSE_NAME = "S&P 500"

SP500_CSV_URLS = [
    "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv",
    "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
]

FALLBACK_TICKERS = {
    "AAPL": "Apple",
    "MSFT": "Microsoft",
    "NVDA": "NVIDIA",
    "AMZN": "Amazon",
    "GOOGL": "Alphabet",
    "META": "Meta Platforms",
    "TSLA": "Tesla",
    "BRK-B": "Berkshire Hathaway",
    "JPM": "JPMorgan Chase",
    "V": "Visa",
    "UNH": "UnitedHealth",
    "XOM": "Exxon Mobil",
    "JNJ": "Johnson & Johnson",
    "AVGO": "Broadcom",
    "LLY": "Eli Lilly",
    "WMT": "Walmart",
    "MA": "Mastercard",
    "PG": "Procter & Gamble",
    "HD": "Home Depot",
    "COST": "Costco",
    "ORCL": "Oracle",
    "NFLX": "Netflix",
    "AMD": "AMD",
    "CRM": "Salesforce",
    "KO": "Coca-Cola",
    "PEP": "PepsiCo",
    "BAC": "Bank of America",
    "CSCO": "Cisco",
    "ABBV": "AbbVie",
    "CVX": "Chevron",
}

BATCH_SIZE = 40
DOWNLOAD_RETRIES = 3
LOOKBACK_CALENDAR_DAYS = "1y"
MIN_BARS = 11
JSON_RETENTION_DAYS = 10
MIN_CLOSE_PRICE = 30.0
MIN_VOLUME = 2_000_000
ATR_PERIOD = 14
RVOL_LOOKBACK = 20
D1_MIN_RVOL = 2.0
D1_MIN_ATR = 0.50
D1_MIN_ABS_CHANGE = 0.02
D1_TOP_RVOL = 20
D3_RANGE_ATR_MULT = 1.5
D3_CLOSE_LOC_EXTREME = 0.80
D4_RSI_PERIOD = 2
D4_RSI_OVERSOLD = 10.0
D4_RSI_OVERBOUGHT = 90.0
D4_SMA_PERIOD = 200
D5_CHANNEL_DAYS = 20
D5_MIN_RVOL = 1.5
D6_MIN_ABS_CHANGE = 0.02
D6_MIN_RVOL = 1.5
STRATEGY_SORT_ORDER = {
    "S1": 0,
    "S2": 1,
    "S3": 2,
    "S4": 3,
    "D1": 10,
    "D2": 11,
    "D3": 12,
    "D4": 13,
    "D5": 14,
    "D6": 15,
}


def log(message: str) -> None:
    """
    輸出帶台灣時間的進度訊息。

    @param message 要顯示的文字
    """
    now = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now} TST] {message}", flush=True)


def now_taipei() -> datetime:
    """
    取得目前台灣時間。

    @returns timezone-aware datetime
    """
    return datetime.now(TZ_TAIPEI)


def to_yf_symbol(symbol: str) -> str:
    """
    將指數代碼轉成 yfinance 格式（例如 BRK.B -> BRK-B）。

    @param symbol 原始代碼
    @returns yfinance 代碼
    """
    return str(symbol).strip().replace(".", "-")


def load_universe() -> dict[str, str]:
    """
    載入 S&P 500 成分股；失敗時改用流動性較高的備援清單。

    @returns {代碼: 公司名稱}
    """
    headers = {"User-Agent": "Mozilla/5.0 US_Strategy_View/1.0"}

    try:
        df = pd.read_csv(SP500_CSV_URLS[0], storage_options=headers)
        symbol_col = "Symbol" if "Symbol" in df.columns else df.columns[0]
        name_col = "Security" if "Security" in df.columns else df.columns[1]
        mapping = {
            to_yf_symbol(row[symbol_col]): str(row[name_col])
            for _, row in df.iterrows()
            if pd.notna(row[symbol_col])
        }
        if mapping:
            log(f"已載入 S&P 500 成分股 {len(mapping)} 檔")
            return mapping
    except Exception as exc:
        log(f"CSV 成分股下載失敗：{exc}")

    try:
        tables = pd.read_html(SP500_CSV_URLS[1], storage_options=headers)
        df = tables[0]
        mapping = {
            to_yf_symbol(row["Symbol"]): str(row["Security"])
            for _, row in df.iterrows()
            if pd.notna(row["Symbol"])
        }
        if mapping:
            log(f"已從 Wikipedia 載入 S&P 500 成分股 {len(mapping)} 檔")
            return mapping
    except Exception as exc:
        log(f"Wikipedia 成分股下載失敗：{exc}")

    log(f"改用備援清單 {len(FALLBACK_TICKERS)} 檔")
    return dict(FALLBACK_TICKERS)


def chunked(items: list[str], size: int) -> list[list[str]]:
    """
    將清單切成固定大小的批次。

    @param items 原始清單
    @param size 每批數量
    @returns 批次清單
    """
    return [items[i : i + size] for i in range(0, len(items), size)]


def flatten_ohlcv(raw: pd.DataFrame, ticker: str) -> pd.DataFrame | None:
    """
    從 yfinance 下載結果取出單一股票的 OHLCV。

    @param raw yfinance 回傳的 DataFrame
    @param ticker 代碼
    @returns 標準化後的日K，失敗則為 None
    """
    if raw is None or raw.empty:
        return None

    frame = raw
    if isinstance(raw.columns, pd.MultiIndex):
        level0 = set(raw.columns.get_level_values(0))
        level1 = set(raw.columns.get_level_values(1))
        if ticker in level0:
            frame = raw[ticker]
        elif ticker in level1:
            frame = raw.xs(ticker, axis=1, level=1)
        else:
            return None

    rename = {str(col).title(): str(col).title() for col in frame.columns}
    frame = frame.rename(columns=rename)
    needed = ["Open", "High", "Low", "Close", "Volume"]
    lower_map = {str(col).lower(): col for col in frame.columns}
    selected = {}
    for name in needed:
        key = name.lower()
        if key not in lower_map:
            return None
        selected[name] = frame[lower_map[key]]

    out = pd.DataFrame(selected).dropna(how="any")
    out = out[out["High"] >= out["Low"]]
    if len(out) < MIN_BARS:
        return None
    return out.sort_index()


def download_history(tickers: list[str]) -> dict[str, pd.DataFrame]:
    """
    分批下載日K，失敗的批次會改為逐檔重試。

    @param tickers 代碼清單
    @returns {代碼: 日K DataFrame}
    """
    history: dict[str, pd.DataFrame] = {}
    batches = chunked(tickers, BATCH_SIZE)
    for index, batch in enumerate(batches, start=1):
        log(f"下載日K {index}/{len(batches)}（{len(batch)} 檔）")
        raw = _download_batch(batch)
        found = set()
        if raw is not None:
            for ticker in batch:
                df = flatten_ohlcv(raw, ticker)
                if df is not None:
                    history[ticker] = df
                    found.add(ticker)
        missing = [ticker for ticker in batch if ticker not in found]
        if missing:
            log(f"批次缺 {len(missing)} 檔，改為逐檔下載")
            for ticker in missing:
                one = _download_batch([ticker])
                df = flatten_ohlcv(one, ticker) if one is not None else None
                if df is not None:
                    history[ticker] = df
                time.sleep(0.15)
        time.sleep(0.4)
    return history


def _download_batch(tickers: list[str]) -> pd.DataFrame | None:
    """
    以 yfinance 下載一批日K。

    @param tickers 代碼清單
    @returns DataFrame 或 None
    """
    last_error: Exception | None = None
    for attempt in range(1, DOWNLOAD_RETRIES + 1):
        try:
            raw = yf.download(
                tickers=tickers,
                period=LOOKBACK_CALENDAR_DAYS,
                interval="1d",
                auto_adjust=True,
                group_by="ticker",
                threads=True,
                progress=False,
                timeout=60,
            )
            if raw is not None and not raw.empty:
                return raw
        except Exception as exc:
            last_error = exc
            log(f"下載失敗（第 {attempt} 次）：{exc}")
            time.sleep(1.5 * attempt)
    if last_error:
        log(f"本批最終失敗：{last_error}")
    return None


def pct_change(close: pd.Series) -> pd.Series:
    """
    計算相對前一日收盤的漲跌幅。

    @param close 收盤價
    @returns 漲跌幅（小數）
    """
    return close.pct_change()


def is_green_bar(row: pd.Series) -> bool:
    """
    綠K：收盤高於開盤（上漲K）。

    @param row 單日 OHLCV
    """
    return float(row["Close"]) > float(row["Open"])


def is_red_bar(row: pd.Series) -> bool:
    """
    紅K：收盤低於開盤（下跌K）。此專案採美股慣例，紅跌綠漲。

    @param row 單日 OHLCV
    """
    return float(row["Close"]) < float(row["Open"])


def candle_range(row: pd.Series) -> float:
    """
    整根K線高低差。

    @param row 單日 OHLCV
    @returns High - Low
    """
    return float(row["High"]) - float(row["Low"])


def candle_range_over_low(row: pd.Series) -> float:
    """
    K 線振幅相對最低價的比例：(最高 − 最低) / 最低。

    @param row 單日 OHLCV
    @returns 小數比例；最低價 <= 0 時為 0
    """
    low = float(row["Low"])
    if low <= 0:
        return 0.0
    return candle_range(row) / low


def lower_shadow_ratio(row: pd.Series) -> float:
    """
    下影線佔整根K線的比例。

    @param row 單日 OHLCV
    @returns 0~1，無高低差時為 0
    """
    rng = candle_range(row)
    if rng <= 0:
        return 0.0
    lower = min(float(row["Open"]), float(row["Close"])) - float(row["Low"])
    return lower / rng


def upper_shadow_ratio(row: pd.Series) -> float:
    """
    上影線佔整根K線的比例。

    @param row 單日 OHLCV
    @returns 0~1，無高低差時為 0
    """
    rng = candle_range(row)
    if rng <= 0:
        return 0.0
    upper = float(row["High"]) - max(float(row["Open"]), float(row["Close"]))
    return upper / rng


def round_pct(value: float) -> float:
    """
    將小數漲跌幅轉成百分比並四捨五入到小數 2 位。

    @param value 小數漲跌幅
    @returns 百分比數字
    """
    return round(float(value) * 100.0, 2)


def wilder_rma(series: pd.Series, period: int) -> pd.Series:
    """
    Wilder 平滑平均（RMA），用於 ATR / RSI。

    @param series 數值序列
    @param period 週期
    @returns 平滑後序列
    """
    return series.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()


def true_range(df: pd.DataFrame) -> pd.Series:
    """
    計算 True Range。

    @param df 日K
    @returns 每日 True Range
    """
    prev_close = df["Close"].shift(1)
    high_low = df["High"] - df["Low"]
    high_prev = (df["High"] - prev_close).abs()
    low_prev = (df["Low"] - prev_close).abs()
    return pd.concat([high_low, high_prev, low_prev], axis=1).max(axis=1)


def calc_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    """
    計算 Average True Range。

    @param df 日K
    @param period ATR 週期
    @returns ATR 序列
    """
    return wilder_rma(true_range(df), period)


def calc_rsi(close: pd.Series, period: int = D4_RSI_PERIOD) -> pd.Series:
    """
    計算 Wilder RSI。

    @param close 收盤價
    @param period RSI 週期
    @returns RSI 序列（0–100）
    """
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = wilder_rma(gain, period)
    avg_loss = wilder_rma(loss, period)
    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    rsi = rsi.mask(avg_loss.eq(0) & avg_gain.gt(0), 100.0)
    rsi = rsi.mask(avg_loss.eq(0) & avg_gain.eq(0), 50.0)
    return rsi.astype(float)


def close_location_ratio(row: pd.Series) -> float | None:
    """
    收盤在當日振幅中的位置：(收盤 − 最低) / (最高 − 最低)。

    @param row 單日 OHLCV
    @returns 0–1；無振幅則為 None
    """
    rng = candle_range(row)
    if rng <= 0:
        return None
    return (float(row["Close"]) - float(row["Low"])) / rng


def prior_avg_volume(df: pd.DataFrame, lookback: int) -> float | None:
    """
    計算不含今日的前 N 日平均成交量。

    @param df 日K
    @param lookback 回看日數
    @returns 均量；資料不足或均量 <= 0 則為 None
    """
    if len(df) < lookback + 1:
        return None
    avg_vol = float(df["Volume"].iloc[-(lookback + 1) : -1].mean())
    if avg_vol <= 0 or not math.isfinite(avg_vol):
        return None
    return avg_vol


def latest_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> float | None:
    """
    取最新一根 ATR。

    @param df 日K
    @param period ATR 週期
    @returns ATR；無法計算則為 None
    """
    if len(df) < period + 1:
        return None
    value = float(calc_atr(df, period).iloc[-1])
    if not math.isfinite(value) or value <= 0:
        return None
    return value


def finite_pct(value: Any) -> float | None:
    """
    將 pct_change 結果轉成可寫入 JSON 的百分比。

    @param value 小數漲跌幅（可能為 NaN / inf）
    @returns 百分比，無法計算則為 None
    """
    if value is None or pd.isna(value):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return round_pct(number)


def bars_on_or_before(df: pd.DataFrame, day: str | None) -> pd.DataFrame:
    """
    取出指定交易日（含）以前的日K。未指定日期則回傳原資料。

    @param df 日K
    @param day YYYY-MM-DD；None 表示不裁切
    @returns 裁切後的日K
    """
    if not day:
        return df
    cutoff = str(day)[:10]
    try:
        dates = df.index.strftime("%Y-%m-%d")
    except AttributeError:
        dates = pd.to_datetime(df.index).strftime("%Y-%m-%d")
    return df[dates <= cutoff]


def daily_change_metrics(df: pd.DataFrame, as_of: str | None = None) -> dict[str, float]:
    """
    計算指定交易日（含）以前最新兩根日K相對前一日收盤的漲跌幅（百分比）。

    昨日為倒數第二根K線，今日為該切點的最新一根K線。所有策略都會寫入這兩個欄位，
    讓網頁「昨日漲跌／今日漲跌」都能顯示。無法計算（K線不足或非有限值）時不寫入該鍵。

    @param df 日K
    @param as_of 交易日 YYYY-MM-DD；None 則用整段資料的最新兩根
    @returns 含 yesterday_change_pct / today_change_pct 的字典
    """
    metrics: dict[str, float] = {}
    frame = bars_on_or_before(df, as_of)
    close = frame["Close"]
    if len(close) < 2:
        return metrics
    chg = close.pct_change()
    yesterday = finite_pct(chg.iloc[-2])
    today = finite_pct(chg.iloc[-1])
    if yesterday is not None:
        metrics["yesterday_change_pct"] = yesterday
    if today is not None:
        metrics["today_change_pct"] = today
    return metrics


def signal_date(df: pd.DataFrame) -> str:
    """
    最新一根日K對應的美股交易日。

    @param df 日K
    @returns YYYY-MM-DD
    """
    idx = df.index[-1]
    if hasattr(idx, "strftime"):
        return idx.strftime("%Y-%m-%d")
    return str(idx)[:10]


def make_pick(
    *,
    taipei: datetime,
    symbol: str,
    name: str,
    strategy: str,
    side: str,
    strategy_desc: str,
    df: pd.DataFrame,
    metrics: dict[str, Any],
) -> dict[str, Any]:
    """
    組成一筆選股紀錄。

    `metrics` 一律合併最新兩根日K的昨日／今日漲跌幅，策略專用數字再疊加上去。

    @returns JSON 物件
    """
    last = df.iloc[-1]
    merged_metrics = {**daily_change_metrics(df), **metrics}
    return {
        "date": taipei.strftime("%Y-%m-%d"),
        "time": taipei.strftime("%H:%M:%S"),
        "timezone": "Asia/Taipei",
        "symbol": symbol,
        "name": name,
        "strategy": strategy,
        "side": side,
        "strategy_desc": strategy_desc,
        "signal_date": signal_date(df),
        "close": round(float(last["Close"]), 4),
        "metrics": merged_metrics,
    }


def _hit_list(metrics: dict[str, Any] | None) -> list[dict[str, Any]]:
    """
    取出影線命中清單。

    @param metrics 策略 metrics
    @returns hits 陣列
    """
    hits = (metrics or {}).get("hits")
    if not isinstance(hits, list):
        return []
    return [item for item in hits if isinstance(item, dict)]


def _latest_hit_date(hits: list[dict[str, Any]]) -> str:
    """
    命中清單中最晚的日期。

    @param hits 影線命中
    @returns YYYY-MM-DD；沒有日期則為空字串
    """
    dates = [str(item.get("date") or "")[:10] for item in hits]
    return max(dates) if dates else ""


def _avg_hit_ratio(hits: list[dict[str, Any]]) -> float:
    """
    命中清單的平均影線占比。

    @param hits 影線命中
    @returns 平均 ratio
    """
    ratios: list[float] = []
    for item in hits:
        try:
            ratios.append(float(item.get("ratio") or 0.0))
        except (TypeError, ValueError):
            continue
    return sum(ratios) / len(ratios) if ratios else 0.0


def choose_s2_side(
    lower_hits: list[dict[str, Any]],
    upper_hits: list[dict[str, Any]],
) -> str | None:
    """
    S2 只選一個方向。

    僅下影線達標則買進，僅上影線達標則賣空；兩邊都達標時，保留最近一次命中日的方向
    （日期相同則比命中次數、再比平均影線占比）。

    @param lower_hits 下影線命中
    @param upper_hits 上影線命中
    @returns `buy` / `short`；兩邊都不足則為 None
    """
    buy_ok = len(lower_hits) >= 2
    short_ok = len(upper_hits) >= 2
    if buy_ok and not short_ok:
        return "buy"
    if short_ok and not buy_ok:
        return "short"
    if not buy_ok:
        return None
    buy_rank = (
        _latest_hit_date(lower_hits),
        len(lower_hits),
        _avg_hit_ratio(lower_hits),
    )
    short_rank = (
        _latest_hit_date(upper_hits),
        len(upper_hits),
        _avg_hit_ratio(upper_hits),
    )
    return "buy" if buy_rank >= short_rank else "short"


def choose_exclusive_pick(
    buy_pick: dict[str, Any],
    short_pick: dict[str, Any],
) -> dict[str, Any]:
    """
    同一策略買賣同時存在時只留一筆。

    S2 比最近影線命中日（再比次數、平均占比）；其餘策略看今日漲跌：
    上漲留買進、下跌留賣空。

    @param buy_pick 買進紀錄
    @param short_pick 賣空紀錄
    @returns 保留的那一筆
    """
    strategy = buy_pick.get("strategy") or short_pick.get("strategy")
    if strategy == "S2":
        buy_metrics = buy_pick.get("metrics")
        short_metrics = short_pick.get("metrics")
        side = choose_s2_side(
            _hit_list(buy_metrics if isinstance(buy_metrics, dict) else None),
            _hit_list(short_metrics if isinstance(short_metrics, dict) else None),
        )
        return buy_pick if side == "buy" else short_pick
    today: float | None = None
    for item in (buy_pick, short_pick):
        metrics = item.get("metrics")
        if not isinstance(metrics, dict) or "today_change_pct" not in metrics:
            continue
        try:
            today = float(metrics["today_change_pct"])
            break
        except (TypeError, ValueError):
            continue
    if today is not None and today < 0:
        return short_pick
    return buy_pick


def keep_single_side_per_strategy(picks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    同一選股日、同一檔、同一策略只保留一個方向。

    @param picks 選股清單
    @returns 每個 (date, symbol, strategy) 最多一筆
    """
    grouped: dict[tuple[str, str, str], dict[str, dict[str, Any]]] = {}
    order: list[tuple[str, str, str]] = []
    for item in picks:
        key = (
            str(item.get("date") or ""),
            str(item.get("symbol") or ""),
            str(item.get("strategy") or ""),
        )
        if key not in grouped:
            grouped[key] = {}
            order.append(key)
        side = str(item.get("side") or "")
        grouped[key][side] = item
    resolved: list[dict[str, Any]] = []
    for key in order:
        sides = grouped[key]
        buy_pick = sides.get("buy")
        short_pick = sides.get("short")
        if buy_pick is not None and short_pick is not None:
            resolved.append(choose_exclusive_pick(buy_pick, short_pick))
        elif buy_pick is not None:
            resolved.append(buy_pick)
        elif short_pick is not None:
            resolved.append(short_pick)
        else:
            resolved.extend(sides.values())
    return resolved


def eval_s1(df: pd.DataFrame, taipei: datetime, symbol: str, name: str) -> list[dict[str, Any]]:
    """
    S1 紅綠K反轉。

    買進：昨日紅K且跌幅 < -3%，今日綠K且漲幅 > 3%，且今高 <= 昨高 * 1.05。
    賣空：昨日綠K且漲幅 > 3%，今日紅K且跌幅 < -3%，且今低 >= 昨低 * 0.95。

    @returns 符合條件的選股
    """
    chg = pct_change(df["Close"])
    yesterday = df.iloc[-2]
    today = df.iloc[-1]
    y_chg = float(chg.iloc[-2])
    t_chg = float(chg.iloc[-1])
    y_high = float(yesterday["High"])
    y_low = float(yesterday["Low"])
    t_high = float(today["High"])
    t_low = float(today["Low"])
    picks: list[dict[str, Any]] = []

    if (
        is_red_bar(yesterday)
        and y_chg < -0.03
        and is_green_bar(today)
        and t_chg > 0.03
        and t_high <= y_high * 1.05
    ):
        picks.append(
            make_pick(
                taipei=taipei,
                symbol=symbol,
                name=name,
                strategy="S1",
                side="buy",
                strategy_desc="S1(買進：紅K+綠K反轉)：昨日紅K下跌超過3%，今日綠K上漲超過3%，且今高<=昨高*1.05",
                df=df,
                metrics={
                    "yesterday_change_pct": round_pct(y_chg),
                    "today_change_pct": round_pct(t_chg),
                    "yesterday_open": round(float(yesterday["Open"]), 4),
                    "yesterday_close": round(float(yesterday["Close"]), 4),
                    "yesterday_high": round(y_high, 4),
                    "today_open": round(float(today["Open"]), 4),
                    "today_close": round(float(today["Close"]), 4),
                    "today_high": round(t_high, 4),
                },
            )
        )

    if (
        is_green_bar(yesterday)
        and y_chg > 0.03
        and is_red_bar(today)
        and t_chg < -0.03
        and t_low >= y_low * 0.95
    ):
        picks.append(
            make_pick(
                taipei=taipei,
                symbol=symbol,
                name=name,
                strategy="S1",
                side="short",
                strategy_desc="S1(賣空：綠K+紅K反轉)：昨日綠K上漲超過3%，今日紅K下跌超過3%，且今低>=昨低*0.95",
                df=df,
                metrics={
                    "yesterday_change_pct": round_pct(y_chg),
                    "today_change_pct": round_pct(t_chg),
                    "yesterday_open": round(float(yesterday["Open"]), 4),
                    "yesterday_close": round(float(yesterday["Close"]), 4),
                    "yesterday_low": round(y_low, 4),
                    "today_open": round(float(today["Open"]), 4),
                    "today_close": round(float(today["Close"]), 4),
                    "today_low": round(t_low, 4),
                },
            )
        )
    return picks


def eval_s2(df: pd.DataFrame, taipei: datetime, symbol: str, name: str) -> list[dict[str, Any]]:
    """
    S2 影線（支撐／壓力）。

    買進：近 10 日內至少 2 根下影線占比 > 50%，且該根 K 的 (最高−最低)/最低 >= 3%。
    賣空：近 10 日內至少 2 根上影線占比 > 50%，且該根 K 的 (最高−最低)/最低 >= 3%。
    兩邊同時達標時只保留最近一次命中影線的方向。

    @returns 符合條件的選股（同一檔最多一筆）
    """
    window = df.tail(10)
    lower_hits = []
    upper_hits = []
    for idx, row in window.iterrows():
        day = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]
        lower = lower_shadow_ratio(row)
        upper = upper_shadow_ratio(row)
        range_pct = candle_range_over_low(row)
        if range_pct < 0.03:
            continue
        if lower > 0.5:
            lower_hits.append(
                {
                    "date": day,
                    "ratio": round(lower * 100.0, 2),
                    "range_pct": round(range_pct * 100.0, 2),
                }
            )
        if upper > 0.5:
            upper_hits.append(
                {
                    "date": day,
                    "ratio": round(upper * 100.0, 2),
                    "range_pct": round(range_pct * 100.0, 2),
                }
            )

    side = choose_s2_side(lower_hits, upper_hits)
    if side == "buy":
        return [
            make_pick(
                taipei=taipei,
                symbol=symbol,
                name=name,
                strategy="S2",
                side="buy",
                strategy_desc="S2(買進：2下影線)：近10日內至少2根下影線佔比超過50%，且該根K振幅(最高-最低)/最低>=3%",
                df=df,
                metrics={
                    "lookback_days": 10,
                    "hit_count": len(lower_hits),
                    "hits": lower_hits,
                },
            )
        ]
    if side == "short":
        return [
            make_pick(
                taipei=taipei,
                symbol=symbol,
                name=name,
                strategy="S2",
                side="short",
                strategy_desc="S2(賣空：2上影線)：近10日內至少2根上影線佔比超過50%，且該根K振幅(最高-最低)/最低>=3%",
                df=df,
                metrics={
                    "lookback_days": 10,
                    "hit_count": len(upper_hits),
                    "hits": upper_hits,
                },
            )
        ]
    return []


def eval_s3(df: pd.DataFrame, taipei: datetime, symbol: str, name: str) -> list[dict[str, Any]]:
    """
    S3 爆量反轉。

    買進：近3日最低價 = 近5日最低價，今日量 > 前5日均量的2倍，且上漲超過3%。
    賣空：近3日最高價 = 近5日最高價，今日量 > 前5日均量的2倍，且下跌超過3%。

    @returns 符合條件的選股
    """
    last5 = df.tail(5)
    last3 = df.tail(3)
    today = df.iloc[-1]
    prev5_vol = df["Volume"].iloc[-6:-1]
    if len(prev5_vol) < 5:
        return []
    avg_vol = float(prev5_vol.mean())
    if avg_vol <= 0:
        return []

    t_chg = float(df["Close"].pct_change().iloc[-1])
    vol_ratio = float(today["Volume"]) / avg_vol
    low3 = float(last3["Low"].min())
    low5 = float(last5["Low"].min())
    high3 = float(last3["High"].max())
    high5 = float(last5["High"].max())
    volume_ok = float(today["Volume"]) > avg_vol * 2.0

    picks: list[dict[str, Any]] = []
    if low3 <= low5 + 1e-9 and volume_ok and t_chg > 0.03:
        picks.append(
            make_pick(
                taipei=taipei,
                symbol=symbol,
                name=name,
                strategy="S3",
                side="buy",
                strategy_desc="S3(買進：爆量+向上反轉)：近3日最低點為近5日最低點，今日量大於前5日均量2倍，且上漲超過3%",
                df=df,
                metrics={
                    "today_change_pct": round_pct(t_chg),
                    "volume": int(today["Volume"]),
                    "prev5_avg_volume": int(round(avg_vol)),
                    "volume_ratio": round(vol_ratio, 2),
                    "low3": round(low3, 4),
                    "low5": round(low5, 4),
                },
            )
        )
    if high3 >= high5 - 1e-9 and volume_ok and t_chg < -0.03:
        picks.append(
            make_pick(
                taipei=taipei,
                symbol=symbol,
                name=name,
                strategy="S3",
                side="short",
                strategy_desc="S3(賣空：爆量+向下反轉)：近3日最高點為近5日最高點，今日量大於前5日均量2倍，且下跌超過3%",
                df=df,
                metrics={
                    "today_change_pct": round_pct(t_chg),
                    "volume": int(today["Volume"]),
                    "prev5_avg_volume": int(round(avg_vol)),
                    "volume_ratio": round(vol_ratio, 2),
                    "high3": round(high3, 4),
                    "high5": round(high5, 4),
                },
            )
        )
    return picks


def eval_s4(df: pd.DataFrame, taipei: datetime, symbol: str, name: str) -> list[dict[str, Any]]:
    """
    S4 最新兩根日K單日大幅漲跌。

    買進：最新 2 根日K收盤價漲跌幅中，至少 1 根 <= -30%。
    賣空：最新 2 根日K收盤價漲跌幅中，至少 1 根 >= +30%。

    漲跌幅相對各根K線的前一日收盤。若兩根分別大跌與大漲，只保留較晚那根（今日優先）的方向。

    @returns 符合條件的選股（同一檔最多一筆）
    """
    chg = pct_change(df["Close"])
    if len(chg) < 3:
        return []
    y_chg = float(chg.iloc[-2])
    t_chg = float(chg.iloc[-1])
    y_ok = math.isfinite(y_chg)
    t_ok = math.isfinite(t_chg)
    if not y_ok and not t_ok:
        return []

    yesterday = df.iloc[-2]
    today = df.iloc[-1]
    picks: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {
        "yesterday_open": round(float(yesterday["Open"]), 4),
        "yesterday_close": round(float(yesterday["Close"]), 4),
        "today_open": round(float(today["Open"]), 4),
        "today_close": round(float(today["Close"]), 4),
    }
    if y_ok:
        metrics["yesterday_change_pct"] = round_pct(y_chg)
    if t_ok:
        metrics["today_change_pct"] = round_pct(t_chg)

    today_drop = t_ok and t_chg <= -0.30
    today_rise = t_ok and t_chg >= 0.30
    yest_drop = y_ok and y_chg <= -0.30
    yest_rise = y_ok and y_chg >= 0.30
    has_drop = today_drop or yest_drop
    has_rise = today_rise or yest_rise
    if has_drop and has_rise:
        if today_drop:
            has_rise = False
        elif today_rise:
            has_drop = False
        else:
            has_drop = yest_drop
            has_rise = yest_rise

    if has_drop:
        picks.append(
            make_pick(
                taipei=taipei,
                symbol=symbol,
                name=name,
                strategy="S4",
                side="buy",
                strategy_desc="S4(買進：最新的2個日K的個股收盤價，其中1個收盤價漲跌幅小於含-30%以上)：最新兩根日K相對前一日收盤，至少一根跌幅達 30%（含）以上",
                df=df,
                metrics=metrics,
            )
        )

    elif has_rise:
        picks.append(
            make_pick(
                taipei=taipei,
                symbol=symbol,
                name=name,
                strategy="S4",
                side="short",
                strategy_desc="S4(賣空：最新的2個日K的個股收盤價，其中1個收盤價漲跌幅大於含30%以上)：最新兩根日K相對前一日收盤，至少一根漲幅達 30%（含）以上",
                df=df,
                metrics=metrics,
            )
        )
    return picks


def eval_d1(df: pd.DataFrame, taipei: datetime, symbol: str, name: str) -> list[dict[str, Any]]:
    """
    D1 相對量 In Play（短線日K）。

    文獻：Aziz《How to Day Trade for a Living》；Zarattini, Barbon, Aziz,
    *A Profitable Day Trading Strategy for the U.S. Equity Market*（SSRN 4729284）。
    條件：相對成交量 >= 2、ATR(14) > $0.50、|漲跌幅| >= 2%。
    收盤在當日振幅上半為買進，下半為賣空。全市場掃描後另保留 RVOL 前 20 名。

    @returns 符合條件的選股
    """
    avg_vol = prior_avg_volume(df, RVOL_LOOKBACK)
    atr_value = latest_atr(df)
    if avg_vol is None or atr_value is None:
        return []
    today = df.iloc[-1]
    t_chg = float(df["Close"].pct_change().iloc[-1])
    if not math.isfinite(t_chg) or abs(t_chg) < D1_MIN_ABS_CHANGE:
        return []
    if atr_value <= D1_MIN_ATR:
        return []
    rvol = float(today["Volume"]) / avg_vol
    if rvol < D1_MIN_RVOL:
        return []
    clv = close_location_ratio(today)
    if clv is None:
        return []
    side = "buy" if clv >= 0.5 else "short"
    direction = "上半（偏多延續）" if side == "buy" else "下半（偏空延續）"
    return [
        make_pick(
            taipei=taipei,
            symbol=symbol,
            name=name,
            strategy="D1",
            side=side,
            strategy_desc=(
                f"D1(短線：相對量 In Play)：今日量為前{RVOL_LOOKBACK}日均量 "
                f"{D1_MIN_RVOL:g} 倍以上、ATR(14)>${D1_MIN_ATR:g}、"
                f"|漲跌幅|>={D1_MIN_ABS_CHANGE * 100:.0f}%，收盤在當日振幅{direction}"
            ),
            df=df,
            metrics={
                "rvol": round(rvol, 2),
                "atr14": round(atr_value, 4),
                "clv_pct": round(clv * 100.0, 2),
                "volume": int(today["Volume"]),
                "prev20_avg_volume": int(round(avg_vol)),
            },
        )
    ]


def eval_d2(df: pd.DataFrame, taipei: datetime, symbol: str, name: str) -> list[dict[str, Any]]:
    """
    D2 NR7 波動收縮突破（短線日K）。

    文獻：Crabel (1990) *Day Trading with Short Term Price Patterns and Opening Range Breakout*；
    StockCharts ChartSchool Narrow Range Day (NR7)；Connors & Raschke (1995) *Street Smarts*。
    今日振幅嚴格小於前 6 日每一日。收盤高於當日中點為買進，低於為賣空。

    @returns 符合條件的選股
    """
    if len(df) < 7:
        return []
    window = df.tail(7)
    ranges = (window["High"] - window["Low"]).astype(float)
    today_range = float(ranges.iloc[-1])
    prior_min = float(ranges.iloc[:-1].min())
    if not math.isfinite(today_range) or today_range <= 0:
        return []
    if not math.isfinite(prior_min) or today_range >= prior_min:
        return []
    today = df.iloc[-1]
    yesterday = df.iloc[-2]
    midpoint = (float(today["High"]) + float(today["Low"])) / 2.0
    inside_day = (
        float(today["High"]) < float(yesterday["High"])
        and float(today["Low"]) > float(yesterday["Low"])
    )
    side = "buy" if float(today["Close"]) >= midpoint else "short"
    direction = "高於中點，觀察突破今高" if side == "buy" else "低於中點，觀察跌破今低"
    return [
        make_pick(
            taipei=taipei,
            symbol=symbol,
            name=name,
            strategy="D2",
            side=side,
            strategy_desc=(
                "D2(短線：NR7 收縮突破)：今日振幅為近 7 日最窄，"
                f"收盤{direction}"
                + ("；同時為 Inside Day" if inside_day else "")
            ),
            df=df,
            metrics={
                "range": round(today_range, 4),
                "prior6_min_range": round(prior_min, 4),
                "midpoint": round(midpoint, 4),
                "inside_day": inside_day,
                "breakout_high": round(float(today["High"]), 4),
                "breakout_low": round(float(today["Low"]), 4),
            },
        )
    ]


def eval_d3(df: pd.DataFrame, taipei: datetime, symbol: str, name: str) -> list[dict[str, Any]]:
    """
    D3 趨勢日延續（寬幅日 + 收盤極端位置）。

    文獻：Connors & Raschke (1995) *Street Smarts* 趨勢日／振幅擴張延續。
    今日振幅 >= 1.5 × ATR(14)；收盤在最高 20% 買進、最低 20% 賣空。

    @returns 符合條件的選股
    """
    atr_value = latest_atr(df)
    if atr_value is None:
        return []
    today = df.iloc[-1]
    day_range = candle_range(today)
    if day_range < D3_RANGE_ATR_MULT * atr_value:
        return []
    clv = close_location_ratio(today)
    if clv is None:
        return []
    if clv >= D3_CLOSE_LOC_EXTREME:
        side = "buy"
        loc_text = "最高 20%，觀察延續走高"
    elif clv <= 1.0 - D3_CLOSE_LOC_EXTREME:
        side = "short"
        loc_text = "最低 20%，觀察延續走低"
    else:
        return []
    return [
        make_pick(
            taipei=taipei,
            symbol=symbol,
            name=name,
            strategy="D3",
            side=side,
            strategy_desc=(
                f"D3(短線：趨勢日延續)：今日振幅>=1.5×ATR(14)，收盤在當日振幅{loc_text}"
            ),
            df=df,
            metrics={
                "atr14": round(atr_value, 4),
                "range": round(day_range, 4),
                "range_atr_ratio": round(day_range / atr_value, 2),
                "clv_pct": round(clv * 100.0, 2),
            },
        )
    ]


def eval_d4(df: pd.DataFrame, taipei: datetime, symbol: str, name: str) -> list[dict[str, Any]]:
    """
    D4 RSI(2) 均值回歸（趨勢內極端超買超賣）。

    文獻：Connors *Short-Term Trading Strategies That Work*；Quantpedia RSI(2)。
    買進：RSI(2) <= 10 且收盤 > SMA(200)。賣空：RSI(2) >= 90 且收盤 < SMA(200)。

    @returns 符合條件的選股
    """
    if len(df) < D4_SMA_PERIOD + D4_RSI_PERIOD:
        return []
    close = df["Close"]
    rsi_val = float(calc_rsi(close, D4_RSI_PERIOD).iloc[-1])
    sma200 = float(close.rolling(D4_SMA_PERIOD).mean().iloc[-1])
    last_close = float(close.iloc[-1])
    if not math.isfinite(rsi_val) or not math.isfinite(sma200):
        return []
    if rsi_val <= D4_RSI_OVERSOLD and last_close > sma200:
        side = "buy"
        desc = (
            "D4(短線：RSI(2) 均值回歸)：RSI(2)<=10 且收盤高於 SMA(200)，"
            "上升趨勢內超賣，觀察隔日反彈"
        )
    elif rsi_val >= D4_RSI_OVERBOUGHT and last_close < sma200:
        side = "short"
        desc = (
            "D4(短線：RSI(2) 均值回歸)：RSI(2)>=90 且收盤低於 SMA(200)，"
            "下降趨勢內超買，觀察隔日回落"
        )
    else:
        return []
    return [
        make_pick(
            taipei=taipei,
            symbol=symbol,
            name=name,
            strategy="D4",
            side=side,
            strategy_desc=desc,
            df=df,
            metrics={
                "rsi2": round(rsi_val, 2),
                "sma200": round(sma200, 4),
            },
        )
    ]


def eval_d5(df: pd.DataFrame, taipei: datetime, symbol: str, name: str) -> list[dict[str, Any]]:
    """
    D5 20 日通道突破（Donchian／Turtle 簡化 + 量能確認）。

    文獻：Donchian Channel；Turtle Traders；George & Hwang (2004) 52-week high 動能。
    買進：今日高 = 近 20 日高、量比 >= 1.5、收陽。賣空：今日低 = 近 20 日低、量比 >= 1.5、收陰。
    買進與賣空互斥；極端情況同時成立時只保留收盤方向（綠K買進／紅K賣空）。

    @returns 符合條件的選股（同一檔最多一筆）
    """
    if len(df) < D5_CHANNEL_DAYS:
        return []
    avg_vol = prior_avg_volume(df, D5_CHANNEL_DAYS)
    if avg_vol is None:
        return []
    today = df.iloc[-1]
    rvol = float(today["Volume"]) / avg_vol
    if rvol < D5_MIN_RVOL:
        return []
    window = df.tail(D5_CHANNEL_DAYS)
    high20 = float(window["High"].max())
    low20 = float(window["Low"].min())
    t_high = float(today["High"])
    t_low = float(today["Low"])
    picks: list[dict[str, Any]] = []
    metrics = {
        "rvol": round(rvol, 2),
        "volume": int(today["Volume"]),
        "prev20_avg_volume": int(round(avg_vol)),
        "high20": round(high20, 4),
        "low20": round(low20, 4),
    }
    if t_high >= high20 - 1e-9 and is_green_bar(today):
        picks.append(
            make_pick(
                taipei=taipei,
                symbol=symbol,
                name=name,
                strategy="D5",
                side="buy",
                strategy_desc=(
                    "D5(短線：20日通道突破)：今日最高價為近 20 日最高，"
                    "量比>=1.5 且收陽，觀察短線動能延續"
                ),
                df=df,
                metrics=metrics,
            )
        )
    elif t_low <= low20 + 1e-9 and is_red_bar(today):
        picks.append(
            make_pick(
                taipei=taipei,
                symbol=symbol,
                name=name,
                strategy="D5",
                side="short",
                strategy_desc=(
                    "D5(短線：20日通道跌破)：今日最低價為近 20 日最低，"
                    "量比>=1.5 且收陰，觀察短線動能延續"
                ),
                df=df,
                metrics=metrics,
            )
        )
    return picks


def eval_d6(df: pd.DataFrame, taipei: datetime, symbol: str, name: str) -> list[dict[str, Any]]:
    """
    D6 連續動能（短線日K）。

    文獻：Aziz ABCD／Bull Flag；連續兩日同向突破。
    買進：近 2 日漲幅皆 > 2%、量比 >= 1.5、今日收盤突破昨高。
    賣空：近 2 日跌幅皆 < -2%、量比 >= 1.5、今日收盤跌破昨低。
    買進與賣空互斥（連續上漲與連續下跌不可能同時成立）。

    @returns 符合條件的選股（同一檔最多一筆）
    """
    if len(df) < RVOL_LOOKBACK + 2:
        return []
    avg_vol = prior_avg_volume(df, RVOL_LOOKBACK)
    if avg_vol is None:
        return []
    chg = pct_change(df["Close"])
    y_chg = float(chg.iloc[-2])
    t_chg = float(chg.iloc[-1])
    if not math.isfinite(y_chg) or not math.isfinite(t_chg):
        return []
    today = df.iloc[-1]
    yesterday = df.iloc[-2]
    rvol = float(today["Volume"]) / avg_vol
    if rvol < D6_MIN_RVOL:
        return []
    picks: list[dict[str, Any]] = []
    metrics = {
        "rvol": round(rvol, 2),
        "volume": int(today["Volume"]),
        "prev20_avg_volume": int(round(avg_vol)),
        "yesterday_high": round(float(yesterday["High"]), 4),
        "yesterday_low": round(float(yesterday["Low"]), 4),
    }
    if (
        y_chg > D6_MIN_ABS_CHANGE
        and t_chg > D6_MIN_ABS_CHANGE
        and float(today["Close"]) > float(yesterday["High"])
    ):
        picks.append(
            make_pick(
                taipei=taipei,
                symbol=symbol,
                name=name,
                strategy="D6",
                side="buy",
                strategy_desc=(
                    "D6(短線：連續動能)：近 2 日漲幅皆超過 2%，量比>=1.5，"
                    "且今日收盤突破昨高"
                ),
                df=df,
                metrics=metrics,
            )
        )
    elif (
        y_chg < -D6_MIN_ABS_CHANGE
        and t_chg < -D6_MIN_ABS_CHANGE
        and float(today["Close"]) < float(yesterday["Low"])
    ):
        picks.append(
            make_pick(
                taipei=taipei,
                symbol=symbol,
                name=name,
                strategy="D6",
                side="short",
                strategy_desc=(
                    "D6(短線：連續動能)：近 2 日跌幅皆超過 2%，量比>=1.5，"
                    "且今日收盤跌破昨低"
                ),
                df=df,
                metrics=metrics,
            )
        )
    return picks


def passes_common_filters(df: pd.DataFrame) -> bool:
    """
    各策略共用選股門檻：最新K線收盤價大於 30 元，且成交量大於 2,000,000。

    @param df 日K
    @returns 通過門檻則為 True
    """
    last = df.iloc[-1]
    return float(last["Close"]) > MIN_CLOSE_PRICE and float(last["Volume"]) > MIN_VOLUME


def scan_ticker(df: pd.DataFrame, taipei: datetime, symbol: str, name: str) -> list[dict[str, Any]]:
    """
    對單一股票執行 S1–S4 日K策略與 D1–D6 短線日K策略選股。

    未通過共用價格／成交量門檻者不進入選股名單。

    @returns 該檔所有命中訊號
    """
    if not passes_common_filters(df):
        return []
    picks: list[dict[str, Any]] = []
    picks.extend(eval_s1(df, taipei, symbol, name))
    picks.extend(eval_s2(df, taipei, symbol, name))
    picks.extend(eval_s3(df, taipei, symbol, name))
    picks.extend(eval_s4(df, taipei, symbol, name))
    picks.extend(eval_d1(df, taipei, symbol, name))
    picks.extend(eval_d2(df, taipei, symbol, name))
    picks.extend(eval_d3(df, taipei, symbol, name))
    picks.extend(eval_d4(df, taipei, symbol, name))
    picks.extend(eval_d5(df, taipei, symbol, name))
    picks.extend(eval_d6(df, taipei, symbol, name))
    return keep_single_side_per_strategy(picks)


def limit_d1_top_rvol(picks: list[dict[str, Any]], limit: int = D1_TOP_RVOL) -> list[dict[str, Any]]:
    """
    D1 只保留相對成交量最高的前 N 名（文獻：In Play 前 20 檔）。

    @param picks 全部選股
    @param limit 保留檔數
    @returns 過濾後清單
    """
    d1 = [item for item in picks if item.get("strategy") == "D1"]
    others = [item for item in picks if item.get("strategy") != "D1"]
    d1_sorted = sorted(
        d1,
        key=lambda item: float((item.get("metrics") or {}).get("rvol") or 0.0),
        reverse=True,
    )
    return others + d1_sorted[:limit]


def sort_picks(picks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    依台灣日期（新到舊）、策略（S1–S4 後接 D1–D6）、方向、代碼排序。

    @param picks 選股清單
    @returns 排序後清單
    """
    side_order = {"buy": 0, "short": 1}
    return sorted(
        picks,
        key=lambda item: (
            -(parse_pick_date(item.get("date")) or date.min).toordinal(),
            STRATEGY_SORT_ORDER.get(item.get("strategy"), 99),
            side_order.get(item.get("side"), 9),
            item.get("symbol", ""),
        ),
    )


def parse_pick_date(value: Any) -> date | None:
    """
    將選股紀錄的台灣日期解析為 date。

    @param value YYYY-MM-DD 字串或其他值
    @returns 解析成功則為日期，否則為 None
    """
    text = str(value or "").strip()[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def load_existing_picks() -> list[dict[str, Any]]:
    """
    讀取既有 US_Strategy.json 的選股清單。

    檔案不存在或格式無法解析時回傳空清單。

    @returns 既有 picks
    """
    if not OUTPUT_JSON.exists():
        return []
    try:
        data = json.loads(OUTPUT_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log(f"讀取既有 JSON 失敗，改以本次結果重建：{exc}")
        return []
    picks = data.get("picks") if isinstance(data, dict) else None
    if not isinstance(picks, list):
        return []
    return [item for item in picks if isinstance(item, dict)]


def merge_and_retain_picks(
    existing: list[dict[str, Any]],
    today_picks: list[dict[str, Any]],
    today: date,
) -> tuple[list[dict[str, Any]], int]:
    """
    以當日掃描結果取代同一台灣日期的舊紀錄，並刪除超過保留天數的資料。

    保留條件：`date >= today - JSON_RETENTION_DAYS`（含當日與第 10 日前當日）。

    @param existing 既有選股
    @param today_picks 本次掃描結果
    @param today 台灣日期
    @returns (保留後清單, 因過期或日期無效而刪除的筆數)
    """
    today_str = today.isoformat()
    others = [item for item in existing if str(item.get("date", "")) != today_str]
    cutoff = today - timedelta(days=JSON_RETENTION_DAYS)
    kept_old: list[dict[str, Any]] = []
    removed = 0
    for item in others:
        item_date = parse_pick_date(item.get("date"))
        if item_date is None or item_date < cutoff:
            removed += 1
            continue
        kept_old.append(item)
    merged = keep_single_side_per_strategy(kept_old + list(today_picks))
    return merged, removed


def fill_missing_change_metrics(
    picks: list[dict[str, Any]],
    history: dict[str, pd.DataFrame],
) -> int:
    """
    為缺少昨日／今日漲跌的既有選股補上百分比。

    依該筆 `signal_date` 裁切日K，避免把「今天」的漲跌寫進舊日期紀錄。

    @param picks 選股清單（會就地更新）
    @param history {代碼: 日K}
    @returns 補上至少一個漲跌欄位的筆數
    """
    filled = 0
    for item in picks:
        metrics = item.get("metrics")
        if not isinstance(metrics, dict):
            metrics = {}
            item["metrics"] = metrics
        need_yesterday = "yesterday_change_pct" not in metrics
        need_today = "today_change_pct" not in metrics
        if not need_yesterday and not need_today:
            continue
        df = history.get(str(item.get("symbol", "")))
        if df is None:
            continue
        computed = daily_change_metrics(df, item.get("signal_date"))
        changed = False
        if need_yesterday and "yesterday_change_pct" in computed:
            metrics["yesterday_change_pct"] = computed["yesterday_change_pct"]
            changed = True
        if need_today and "today_change_pct" in computed:
            metrics["today_change_pct"] = computed["today_change_pct"]
            changed = True
        if changed:
            filled += 1
    return filled


def write_json(payload: dict[str, Any]) -> None:
    """
    將選股結果寫入 US_Strategy.json。

    @param payload 完整 JSON 物件
    """
    OUTPUT_JSON.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log(f"已寫入 {OUTPUT_JSON.name}（保留 {payload['meta']['pick_count']} 筆）")


def main() -> int:
    """
    執行選股並輸出 JSON。

    @returns 程式結束碼
    """
    taipei = now_taipei()
    log("開始美股策略選股（日K S1–S4＋短線 D1–D6）")
    universe = load_universe()
    tickers = list(universe.keys())
    history = download_history(tickers)
    failed = [symbol for symbol in tickers if symbol not in history]

    picks: list[dict[str, Any]] = []
    for symbol, df in history.items():
        picks.extend(scan_ticker(df, taipei, symbol, universe.get(symbol, symbol)))

    picks = limit_d1_top_rvol(picks)
    today_picks = sort_picks(picks)
    kept, removed = merge_and_retain_picks(
        load_existing_picks(),
        today_picks,
        taipei.date(),
    )
    kept = sort_picks(kept)
    backfilled = fill_missing_change_metrics(kept, history)
    if backfilled:
        log(f"已為 {backfilled} 筆舊紀錄補上昨日／今日漲跌")
    if removed:
        log(f"已刪除超過 {JSON_RETENTION_DAYS} 日前資料 {removed} 筆")
    log(f"當日 {len(today_picks)} 筆，JSON 保留近 {JSON_RETENTION_DAYS} 日共 {len(kept)} 筆")
    payload = {
        "meta": {
            "date": taipei.strftime("%Y-%m-%d"),
            "time": taipei.strftime("%H:%M:%S"),
            "timezone": "Asia/Taipei",
            "universe": UNIVERSE_NAME,
            "scanned": len(tickers),
            "success": len(history),
            "failed_count": len(failed),
            "failed": failed,
            "today_pick_count": len(today_picks),
            "pick_count": len(kept),
            "retention_days": JSON_RETENTION_DAYS,
            "pruned_count": removed,
            "note": "日期與時間為台灣時區；signal_date 為最新日K的美股交易日。S1–S4 為日K選股，D1–D6 為策略選股（短線日K）。僅保留近 10 日（含當日）選股，超過 10 日前的紀錄會刪除。",
        },
        "picks": kept,
    }
    write_json(payload)
    log("選股完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
