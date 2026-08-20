#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
美股日K策略選股：掃描 S&P 500，將訊號寫入 US_Strategy.json。

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
LOOKBACK_CALENDAR_DAYS = "3mo"
MIN_BARS = 11
JSON_RETENTION_DAYS = 10
MIN_CLOSE_PRICE = 30.0
MIN_VOLUME = 2_000_000


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

    @returns 符合條件的選股
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

    picks: list[dict[str, Any]] = []
    if len(lower_hits) >= 2:
        picks.append(
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
        )
    if len(upper_hits) >= 2:
        picks.append(
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
        )
    return picks


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

    漲跌幅相對各根K線的前一日收盤。同一檔可同時命中買進與賣空。

    @returns 符合條件的選股
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

    has_drop = (y_ok and y_chg <= -0.30) or (t_ok and t_chg <= -0.30)
    has_rise = (y_ok and y_chg >= 0.30) or (t_ok and t_chg >= 0.30)

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

    if has_rise:
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
    對單一股票執行 S1 / S2 / S3 / S4。

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
    return picks


def sort_picks(picks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    依台灣日期（新到舊）、策略、方向、代碼排序。

    @param picks 選股清單
    @returns 排序後清單
    """
    strategy_order = {"S1": 0, "S2": 1, "S3": 2, "S4": 3}
    side_order = {"buy": 0, "short": 1}
    return sorted(
        picks,
        key=lambda item: (
            -(parse_pick_date(item.get("date")) or date.min).toordinal(),
            strategy_order.get(item.get("strategy"), 9),
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
    return kept_old + list(today_picks), removed


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
    log("開始美股策略選股")
    universe = load_universe()
    tickers = list(universe.keys())
    history = download_history(tickers)
    failed = [symbol for symbol in tickers if symbol not in history]

    picks: list[dict[str, Any]] = []
    for symbol, df in history.items():
        picks.extend(scan_ticker(df, taipei, symbol, universe.get(symbol, symbol)))

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
            "note": "日期與時間為台灣時區；signal_date 為最新日K的美股交易日。僅保留近 10 日（含當日）選股，超過 10 日前的紀錄會刪除。",
        },
        "picks": kept,
    }
    write_json(payload)
    log("選股完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
