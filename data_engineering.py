"""Excel ingestion, CSE corporate-action adjustment and illiquidity handling."""

from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import BinaryIO, Mapping

import numpy as np
import pandas as pd

from .config import GICS_SECTOR_ALIASES


class WorkbookValidationError(ValueError):
    """Raised when an uploaded portfolio workbook cannot be interpreted."""


@dataclass
class PortfolioWorkbook:
    holdings: pd.DataFrame
    prices: pd.DataFrame
    adjusted_prices: pd.DataFrame
    benchmarks: pd.DataFrame
    corporate_actions: pd.DataFrame
    trades: pd.DataFrame
    holidays: pd.DatetimeIndex
    warnings: list[str] = field(default_factory=list)


COLUMN_ALIASES = {
    "symbol": "ticker",
    "security": "ticker",
    "stock": "ticker",
    "qty": "quantity",
    "shares": "quantity",
    "holding": "quantity",
    "average_cost": "avg_cost",
    "cost_price": "avg_cost",
    "market_price": "current_price",
    "close_price": "current_price",
    "price": "current_price",
    "30d_adtv": "adtv_30d_lkr",
    "adtv": "adtv_30d_lkr",
    "average_daily_traded_value": "adtv_30d_lkr",
    "spread": "bid_ask_spread_pct",
    "aspi": "aspi_weight",
    "sl20": "sl20_weight",
    "sp_sl20_weight": "sl20_weight",
    "gics_sector": "sector",
    "board_name": "board",
    "group": "related_group",
    "cost": "avg_cost",
}


def _normalise_columns(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result.columns = [
        str(c).strip().lower().replace(" ", "_").replace("-", "_").replace("/", "_")
        for c in result.columns
    ]
    return result.rename(columns={c: COLUMN_ALIASES.get(c, c) for c in result.columns})


def _sheet(sheets: Mapping[str, pd.DataFrame], *names: str) -> pd.DataFrame:
    lowered = {str(k).strip().lower().replace(" ", "_"): v for k, v in sheets.items()}
    for name in names:
        if name in lowered:
            return lowered[name].copy()
    return pd.DataFrame()


def _numeric(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    for column in columns:
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def canonical_sector(value: object) -> str:
    if pd.isna(value) or not str(value).strip():
        return "Unknown"
    raw = str(value).strip()
    return GICS_SECTOR_ALIASES.get(raw.upper(), raw.title())


def clean_holdings(raw: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    warnings: list[str] = []
    holdings = _normalise_columns(raw).dropna(how="all")
    required = {"ticker", "quantity", "current_price"}
    missing = required - set(holdings.columns)
    if missing:
        raise WorkbookValidationError(
            "Holdings sheet is missing required columns: " + ", ".join(sorted(missing))
        )

    holdings["ticker"] = holdings["ticker"].astype(str).str.strip().str.upper()
    holdings = holdings[holdings["ticker"].ne("") & holdings["ticker"].ne("NAN")].copy()
    holdings = _numeric(
        holdings,
        [
            "quantity",
            "avg_cost",
            "current_price",
            "adtv_30d_lkr",
            "bid",
            "ask",
            "bid_ask_spread_pct",
            "aspi_weight",
            "sl20_weight",
            "dividend_per_share",
            "expected_return",
            "rate_beta",
            "fx_beta",
            "inflation_beta",
        ],
    )
    if holdings[["quantity", "current_price"]].isna().any().any():
        raise WorkbookValidationError("Quantity and current_price must be numeric for every holding.")
    if (holdings["quantity"] < 0).any():
        raise WorkbookValidationError("Negative quantities are not supported in this long-only tool.")
    if (holdings["current_price"] <= 0).any():
        raise WorkbookValidationError("Current prices must be greater than zero.")

    defaults: dict[str, object] = {
        "avg_cost": holdings["current_price"],
        "sector": "Unknown",
        "board": "Main Board",
        "related_group": "",
        "adtv_30d_lkr": np.nan,
        "bid_ask_spread_pct": np.nan,
        "aspi_weight": 0.0,
        "sl20_weight": 0.0,
        "dividend_per_share": 0.0,
    }
    for column, default in defaults.items():
        if column not in holdings:
            holdings[column] = default
    holdings["sector"] = holdings["sector"].map(canonical_sector)
    holdings["board"] = holdings["board"].fillna("Unknown").astype(str).str.strip()
    holdings["related_group"] = holdings["related_group"].fillna("").astype(str).str.strip()
    if "bid" in holdings and "ask" in holdings:
        mid = (holdings["bid"] + holdings["ask"]) / 2.0
        derived = (holdings["ask"] - holdings["bid"]) / mid.replace(0, np.nan)
        holdings["bid_ask_spread_pct"] = holdings["bid_ask_spread_pct"].fillna(derived)
    holdings["bid_ask_spread_pct"] = holdings["bid_ask_spread_pct"].fillna(0.01).clip(lower=0)
    holdings["market_value"] = holdings["quantity"] * holdings["current_price"]
    portfolio_value = float(holdings["market_value"].sum())
    if portfolio_value <= 0:
        raise WorkbookValidationError("The portfolio market value must be greater than zero.")
    holdings["current_weight"] = holdings["market_value"] / portfolio_value
    holdings["unrealised_pnl"] = (
        holdings["current_price"] - holdings["avg_cost"].fillna(holdings["current_price"])
    ) * holdings["quantity"]

    for benchmark in ["aspi_weight", "sl20_weight"]:
        holdings[benchmark] = holdings[benchmark].fillna(0.0).clip(lower=0)
        total = holdings[benchmark].sum()
        if total > 1.5:  # likely supplied as percentage points
            holdings[benchmark] /= 100.0
    if holdings["adtv_30d_lkr"].isna().any():
        warnings.append("Some 30-day ADTV values were missing; price-sheet turnover will be used where available.")
    return holdings.reset_index(drop=True), warnings


def parse_prices(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame(columns=["date", "ticker", "close", "volume", "bid", "ask"])
    prices = _normalise_columns(raw).dropna(how="all")
    if "date" not in prices:
        raise WorkbookValidationError("Prices sheet must contain a Date column.")
    if "ticker" not in prices:
        value_columns = [c for c in prices.columns if c != "date"]
        prices = prices.melt(id_vars="date", value_vars=value_columns, var_name="ticker", value_name="close")
    if "close" not in prices:
        for candidate in ["current_price", "closing_price", "last"]:
            if candidate in prices:
                prices = prices.rename(columns={candidate: "close"})
                break
    if "close" not in prices:
        raise WorkbookValidationError("Prices sheet requires Close values (long or wide layout).")
    prices["date"] = pd.to_datetime(prices["date"], errors="coerce").dt.normalize()
    prices["ticker"] = prices["ticker"].astype(str).str.strip().str.upper()
    for column, default in {"volume": 0.0, "bid": np.nan, "ask": np.nan}.items():
        if column not in prices:
            prices[column] = default
    prices = _numeric(prices, ["close", "volume", "bid", "ask"])
    prices = prices.dropna(subset=["date", "ticker"]).sort_values(["ticker", "date"])
    return prices[["date", "ticker", "close", "volume", "bid", "ask"]].reset_index(drop=True)


def fill_illiquid_days(prices: pd.DataFrame) -> pd.DataFrame:
    """Forward-fill missing CSE prices while retaining zero-volume/stale flags."""
    if prices.empty:
        return prices.copy()
    dates = pd.DatetimeIndex(sorted(prices["date"].dropna().unique()))
    tickers = sorted(prices["ticker"].dropna().unique())
    full = pd.MultiIndex.from_product([tickers, dates], names=["ticker", "date"])
    expanded = prices.set_index(["ticker", "date"]).reindex(full).reset_index()
    expanded["raw_price_missing"] = expanded["close"].isna()
    for column in ["close", "bid", "ask"]:
        expanded[column] = expanded.groupby("ticker", sort=False)[column].ffill()
    expanded["volume"] = expanded["volume"].fillna(0.0)
    expanded["stale_price"] = expanded["raw_price_missing"] | expanded["volume"].eq(0)
    return expanded.sort_values(["ticker", "date"]).reset_index(drop=True)


def _parse_ratio(value: object, kind: str) -> float:
    if pd.isna(value):
        return np.nan
    if isinstance(value, str) and ":" in value:
        left, right = value.split(":", 1)
        left_num, right_num = float(left), float(right)
        if right_num == 0:
            return np.nan
        return left_num / right_num
    number = float(value)
    if kind == "split" and 0 < number < 1:
        return 1.0 / number
    return number


def clean_corporate_actions(raw: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "date",
        "ticker",
        "action_type",
        "cash_dividend",
        "rights_ratio",
        "rights_price",
        "split_ratio",
        "scrip_ratio",
    ]
    if raw.empty:
        return pd.DataFrame(columns=columns)
    actions = _normalise_columns(raw).dropna(how="all")
    required = {"date", "ticker", "action_type"}
    if required - set(actions.columns):
        raise WorkbookValidationError("CorporateActions requires Date, Ticker and Action_Type columns.")
    actions["date"] = pd.to_datetime(actions["date"], errors="coerce").dt.normalize()
    actions["ticker"] = actions["ticker"].astype(str).str.strip().str.upper()
    actions["action_type"] = (
        actions["action_type"].astype(str).str.strip().str.upper().str.replace(" ", "_")
    )
    for column in columns[3:]:
        if column not in actions:
            actions[column] = np.nan
    actions = _numeric(actions, ["cash_dividend", "rights_price"])
    actions["rights_ratio"] = actions["rights_ratio"].map(lambda x: _parse_ratio(x, "rights"))
    actions["split_ratio"] = actions["split_ratio"].map(lambda x: _parse_ratio(x, "split"))
    actions["scrip_ratio"] = actions["scrip_ratio"].map(lambda x: _parse_ratio(x, "scrip"))
    return actions.dropna(subset=["date", "ticker"])[columns].sort_values(["ticker", "date"])


def adjust_for_corporate_actions(prices: pd.DataFrame, actions: pd.DataFrame) -> pd.DataFrame:
    """Build a backward-adjusted series across XD, XR, scrip and subdivisions.

    Ratios use ``new shares : existing shares``. For example, a 1-for-4
    rights issue is ``1:4`` and a 2-for-1 subdivision is ``2:1``.
    """
    adjusted = prices.copy()
    if adjusted.empty:
        adjusted["adjustment_factor"] = pd.Series(dtype=float)
        adjusted["adjusted_close"] = pd.Series(dtype=float)
        return adjusted
    adjusted["adjustment_factor"] = 1.0
    if actions.empty:
        adjusted["adjusted_close"] = adjusted["close"]
        return adjusted

    for action in actions.itertuples(index=False):
        ticker_mask = adjusted["ticker"].eq(action.ticker)
        prior_mask = ticker_mask & adjusted["date"].lt(action.date)
        prior = adjusted.loc[prior_mask & adjusted["close"].notna()].sort_values("date")
        if prior.empty:
            continue
        previous_close = float(prior.iloc[-1]["close"])
        action_type = str(action.action_type).upper()
        factor = 1.0
        if action_type in {"CASH_DIVIDEND", "DIVIDEND", "XD"}:
            dividend = float(action.cash_dividend) if pd.notna(action.cash_dividend) else 0.0
            factor = max(previous_close - dividend, 0.0) / previous_close
        elif action_type in {"RIGHTS", "RIGHTS_ISSUE", "XR"}:
            ratio = float(action.rights_ratio) if pd.notna(action.rights_ratio) else 0.0
            rights_price = float(action.rights_price) if pd.notna(action.rights_price) else previous_close
            terp = (previous_close + ratio * rights_price) / (1.0 + ratio)
            factor = terp / previous_close
        elif action_type in {"SUBDIVISION", "STOCK_SPLIT", "SPLIT"}:
            split_ratio = float(action.split_ratio) if pd.notna(action.split_ratio) else 1.0
            factor = 1.0 / split_ratio if split_ratio > 0 else 1.0
        elif action_type in {"SCRIP", "SCRIP_DIVIDEND", "BONUS"}:
            scrip_ratio = float(action.scrip_ratio) if pd.notna(action.scrip_ratio) else 0.0
            factor = 1.0 / (1.0 + scrip_ratio)
        if np.isfinite(factor) and factor > 0:
            adjusted.loc[prior_mask, "adjustment_factor"] *= factor
    adjusted["adjusted_close"] = adjusted["close"] * adjusted["adjustment_factor"]
    return adjusted


def clean_benchmarks(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame(columns=["date", "aspi", "sp_sl20"])
    benchmark = _normalise_columns(raw).dropna(how="all")
    benchmark = benchmark.rename(columns={"sl20": "sp_sl20", "s&p_sl20": "sp_sl20"})
    if "date" not in benchmark:
        raise WorkbookValidationError("Benchmarks sheet requires a Date column.")
    benchmark["date"] = pd.to_datetime(benchmark["date"], errors="coerce").dt.normalize()
    for column in ["aspi", "sp_sl20"]:
        if column not in benchmark:
            benchmark[column] = np.nan
    benchmark = _numeric(benchmark, ["aspi", "sp_sl20"])
    return benchmark[["date", "aspi", "sp_sl20"]].dropna(subset=["date"]).sort_values("date")


def clean_trades(raw: pd.DataFrame) -> pd.DataFrame:
    columns = ["trade_date", "settlement_date", "ticker", "side", "gross_value", "fees"]
    if raw.empty:
        return pd.DataFrame(columns=columns)
    trades = _normalise_columns(raw).dropna(how="all")
    required = {"trade_date", "ticker", "side", "gross_value"}
    if required - set(trades.columns):
        raise WorkbookValidationError("Trades requires Trade_Date, Ticker, Side and Gross_Value.")
    if "settlement_date" not in trades:
        trades["settlement_date"] = pd.NaT
    if "fees" not in trades:
        trades["fees"] = 0.0
    trades["trade_date"] = pd.to_datetime(trades["trade_date"], errors="coerce").dt.normalize()
    trades["settlement_date"] = pd.to_datetime(trades["settlement_date"], errors="coerce").dt.normalize()
    trades["ticker"] = trades["ticker"].astype(str).str.strip().str.upper()
    trades["side"] = trades["side"].astype(str).str.strip().str.upper()
    trades = _numeric(trades, ["gross_value", "fees"])
    return trades[columns].dropna(subset=["trade_date", "ticker", "gross_value"])


def _update_adtv_from_prices(holdings: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    if prices.empty:
        return holdings
    turnover = prices.assign(turnover=prices["close"] * prices["volume"])
    adtv = (
        turnover.sort_values("date")
        .groupby("ticker", as_index=False)
        .tail(30)
        .groupby("ticker")["turnover"]
        .mean()
    )
    result = holdings.copy()
    derived = result["ticker"].map(adtv)
    result["adtv_30d_lkr"] = result["adtv_30d_lkr"].fillna(derived)
    return result


def load_portfolio_workbook(source: str | Path | bytes | BinaryIO) -> PortfolioWorkbook:
    """Load and validate the multi-sheet CSE portfolio workbook."""
    if isinstance(source, bytes):
        source = BytesIO(source)
    excel = pd.ExcelFile(source)
    sheets = {name: pd.read_excel(excel, sheet_name=name) for name in excel.sheet_names}
    holdings_raw = _sheet(sheets, "holdings", "portfolio")
    if holdings_raw.empty:
        raise WorkbookValidationError("Workbook must include a non-empty Holdings sheet.")
    holdings, warnings = clean_holdings(holdings_raw)
    prices = fill_illiquid_days(parse_prices(_sheet(sheets, "prices", "market_data", "daily_prices")))
    actions = clean_corporate_actions(_sheet(sheets, "corporateactions", "corporate_actions", "actions"))
    adjusted = adjust_for_corporate_actions(prices, actions)
    holdings = _update_adtv_from_prices(holdings, adjusted)
    if holdings["adtv_30d_lkr"].isna().any():
        warnings.append("ADTV is still missing for one or more holdings; optimization will use only the 10% cap for them.")
    benchmarks = clean_benchmarks(_sheet(sheets, "benchmarks", "indices", "benchmark"))
    trades = clean_trades(_sheet(sheets, "trades", "trade_blotter", "orders"))
    holidays_raw = _sheet(sheets, "holidays", "market_holidays")
    holidays = pd.DatetimeIndex([])
    if not holidays_raw.empty:
        holidays_frame = _normalise_columns(holidays_raw)
        date_column = "date" if "date" in holidays_frame else holidays_frame.columns[0]
        holidays = pd.DatetimeIndex(pd.to_datetime(holidays_frame[date_column], errors="coerce").dropna()).normalize()
    return PortfolioWorkbook(holdings, prices, adjusted, benchmarks, actions, trades, holidays, warnings)


def returns_matrix(adjusted_prices: pd.DataFrame) -> pd.DataFrame:
    if adjusted_prices.empty:
        return pd.DataFrame()
    pivot = adjusted_prices.pivot(index="date", columns="ticker", values="adjusted_close").sort_index()
    # fill_method=None prevents pandas from silently manufacturing prices.
    return pivot.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan).fillna(0.0)
