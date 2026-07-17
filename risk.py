"""Benchmark risk, T+2 settlement and Sri Lankan macro stress analytics."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import DEFAULT_SCENARIOS, SECTOR_STRESS_BETAS, TRADING_DAYS


@dataclass
class RiskMetrics:
    annual_return: float
    annual_volatility: float
    downside_volatility: float
    max_drawdown: float
    var_95: float


def portfolio_return_series(returns: pd.DataFrame, weights: pd.Series) -> pd.Series:
    if returns.empty:
        return pd.Series(dtype=float, name="portfolio_return")
    aligned_weights = weights.reindex(returns.columns).fillna(0.0)
    series = returns.fillna(0.0).mul(aligned_weights, axis=1).sum(axis=1)
    series.name = "portfolio_return"
    return series


def risk_metrics(series: pd.Series, annualization: int = TRADING_DAYS) -> RiskMetrics:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if clean.empty:
        return RiskMetrics(0.0, 0.0, 0.0, 0.0, 0.0)
    wealth = (1.0 + clean).cumprod()
    drawdown = wealth / wealth.cummax() - 1.0
    downside = clean[clean < 0]
    return RiskMetrics(
        annual_return=float((1.0 + clean.mean()) ** annualization - 1.0),
        annual_volatility=float(clean.std(ddof=1) * np.sqrt(annualization)),
        downside_volatility=float(downside.std(ddof=1) * np.sqrt(annualization)) if len(downside) > 1 else 0.0,
        max_drawdown=float(drawdown.min()),
        var_95=float(clean.quantile(0.05)),
    )


def tracking_error(portfolio_returns: pd.Series, benchmark_levels: pd.Series, annualization: int = TRADING_DAYS) -> float:
    benchmark_returns = pd.to_numeric(benchmark_levels, errors="coerce").pct_change(fill_method=None)
    aligned = pd.concat([portfolio_returns, benchmark_returns.rename("benchmark")], axis=1).dropna()
    if len(aligned) < 2:
        return float("nan")
    active = aligned.iloc[:, 0] - aligned["benchmark"]
    return float(active.std(ddof=1) * np.sqrt(annualization))


def normalized_benchmark_levels(benchmark_levels: pd.Series) -> pd.Series:
    """Normalize valid benchmark levels to 1.0 without failing on blank inputs."""
    numeric = pd.to_numeric(benchmark_levels, errors="coerce")
    valid = numeric.dropna()
    if valid.empty:
        return pd.Series(dtype=float, index=numeric.index, name=benchmark_levels.name)
    base = float(valid.iloc[0])
    if not np.isfinite(base) or base == 0:
        return pd.Series(dtype=float, index=numeric.index, name=benchmark_levels.name)
    return numeric / base


def active_share(portfolio_weights: pd.Series, benchmark_weights: pd.Series) -> float:
    names = portfolio_weights.index.union(benchmark_weights.index)
    portfolio = portfolio_weights.reindex(names).fillna(0.0)
    benchmark = benchmark_weights.reindex(names).fillna(0.0)
    return float(0.5 * (portfolio - benchmark).abs().sum())


def add_market_days(date: pd.Timestamp, days: int, holidays: pd.DatetimeIndex | None = None) -> pd.Timestamp:
    current = pd.Timestamp(date).normalize()
    holiday_set = set(pd.DatetimeIndex([] if holidays is None else holidays).normalize())
    moved = 0
    while moved < days:
        current += pd.Timedelta(days=1)
        if current.weekday() < 5 and current not in holiday_set:
            moved += 1
    return current


def settlement_schedule(
    trades: pd.DataFrame,
    holidays: pd.DatetimeIndex | None = None,
    margin_rate: float = 0.15,
    settlement_days: int = 2,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Calculate T+2 cash lockups, pending sale proceeds and clearing margin.

    Uploaded settlement dates override calculated dates. The margin rate is an
    AMC planning assumption and should be reconciled to the clearing member.
    """
    if trades.empty:
        empty_trades = trades.copy()
        empty_schedule = pd.DataFrame(
            columns=["date", "buy_cash_locked", "sale_proceeds_pending", "gross_unsettled", "margin_requirement"]
        )
        return empty_trades, empty_schedule
    blotter = trades.copy()
    blotter["trade_date"] = pd.to_datetime(blotter["trade_date"]).dt.normalize()
    if "settlement_date" not in blotter:
        blotter["settlement_date"] = pd.NaT
    blotter["settlement_date"] = pd.to_datetime(blotter["settlement_date"], errors="coerce").dt.normalize()
    calculated = blotter["trade_date"].map(lambda d: add_market_days(d, settlement_days, holidays))
    blotter["settlement_date"] = blotter["settlement_date"].fillna(calculated)
    if "fees" not in blotter:
        blotter["fees"] = 0.0
    blotter["cash_value"] = np.where(
        blotter["side"].str.upper().eq("BUY"),
        blotter["gross_value"] + blotter["fees"].fillna(0.0),
        blotter["gross_value"] - blotter["fees"].fillna(0.0),
    )
    start = blotter["trade_date"].min()
    end = blotter["settlement_date"].max()
    dates = pd.date_range(start, end, freq="D")
    records = []
    for date in dates:
        if date.weekday() >= 5 or (holidays is not None and date.normalize() in set(holidays.normalize())):
            continue
        unsettled = blotter[(blotter["trade_date"] <= date) & (blotter["settlement_date"] > date)]
        buys = unsettled[unsettled["side"].str.upper().eq("BUY")]
        sells = unsettled[unsettled["side"].str.upper().eq("SELL")]
        gross = float(unsettled["gross_value"].abs().sum())
        records.append(
            {
                "date": date,
                "buy_cash_locked": float(buys["cash_value"].sum()),
                "sale_proceeds_pending": float(sells["cash_value"].sum()),
                "gross_unsettled": gross,
                "margin_requirement": gross * margin_rate,
            }
        )
    return blotter, pd.DataFrame(records)


def proposed_trade_blotter(
    allocations: pd.DataFrame,
    trade_date: pd.Timestamp,
    transaction_cost_rate: float = 0.0112,
    minimum_trade_lkr: float = 1_000.0,
) -> pd.DataFrame:
    proposed = allocations.loc[allocations["trade_value"].abs() >= minimum_trade_lkr, ["ticker", "trade_value"]].copy()
    proposed["trade_date"] = pd.Timestamp(trade_date).normalize()
    proposed["settlement_date"] = pd.NaT
    proposed["side"] = np.where(proposed["trade_value"] >= 0, "BUY", "SELL")
    proposed["gross_value"] = proposed["trade_value"].abs()
    proposed["fees"] = proposed["gross_value"] * transaction_cost_rate
    return proposed[["trade_date", "settlement_date", "ticker", "side", "gross_value", "fees"]]


def _stock_betas(holdings: pd.DataFrame) -> pd.DataFrame:
    betas = holdings[["ticker", "sector"]].copy()
    mapped = betas["sector"].map(lambda s: SECTOR_STRESS_BETAS.get(s, SECTOR_STRESS_BETAS["Unknown"]))
    betas[["rate_beta", "fx_beta", "inflation_beta"]] = pd.DataFrame(mapped.tolist(), index=betas.index)
    for column in ["rate_beta", "fx_beta", "inflation_beta"]:
        if column in holdings:
            betas[column] = pd.to_numeric(holdings[column], errors="coerce").fillna(betas[column])
    return betas


def macro_stress_test(
    holdings: pd.DataFrame,
    weights: pd.Series | None = None,
    scenarios: dict[str, dict[str, float]] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Estimate scenario drawdowns using editable sector/stock sensitivities."""
    scenarios = scenarios or DEFAULT_SCENARIOS
    data = holdings.copy().reset_index(drop=True)
    if weights is None:
        weights = data.set_index("ticker")["current_weight"]
    betas = _stock_betas(data)
    betas["weight"] = betas["ticker"].map(weights).fillna(0.0)
    scenario_rows: list[dict[str, float | str]] = []
    contribution_rows: list[dict[str, float | str]] = []
    for name, shock in scenarios.items():
        rate_units = float(shock.get("rate_hike_bps", 0.0)) / 100.0
        fx_units = float(shock.get("lkr_depreciation_pct", 0.0)) / 10.0
        inflation_units = float(shock.get("inflation_shock_pp", 0.0)) / 5.0
        stock_return = (
            betas["rate_beta"] * rate_units
            + betas["fx_beta"] * fx_units
            + betas["inflation_beta"] * inflation_units
        )
        contribution = betas["weight"] * stock_return
        scenario_rows.append(
            {
                "scenario": name,
                "rate_hike_bps": shock.get("rate_hike_bps", 0.0),
                "lkr_depreciation_pct": shock.get("lkr_depreciation_pct", 0.0),
                "inflation_shock_pp": shock.get("inflation_shock_pp", 0.0),
                "estimated_portfolio_return": float(contribution.sum()),
                "estimated_drawdown_lkr_per_100m": float(contribution.sum() * 100_000_000),
            }
        )
        for idx, row in betas.iterrows():
            contribution_rows.append(
                {
                    "scenario": name,
                    "ticker": row["ticker"],
                    "sector": row["sector"],
                    "stressed_return": float(stock_return.iloc[idx]),
                    "portfolio_contribution": float(contribution.iloc[idx]),
                }
            )
    scenarios_frame = pd.DataFrame(scenario_rows).sort_values("estimated_portfolio_return")
    contributions = pd.DataFrame(contribution_rows)
    return scenarios_frame, contributions
