"""Liquidity-Adjusted Mean-Variance Optimization for long-only CSE portfolios."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from .config import TRADING_DAYS


class InfeasibleOptimization(ValueError):
    """Raised when the requested concentration/liquidity caps cannot sum to 100%."""


@dataclass
class LAMVOConfig:
    risk_aversion: float = 4.0
    liquidity_penalty: float = 2.0
    market_impact_coefficient: float = 0.10
    max_stock_weight: float = 0.10
    max_adtv_fraction: float = 0.20
    execution_days: int = 1
    covariance_shrinkage: float = 0.25
    annualization: int = TRADING_DAYS


@dataclass
class LAMVOResult:
    allocations: pd.DataFrame
    success: bool
    message: str
    objective_value: float
    expected_return: float
    volatility: float
    liquidity_penalty_value: float
    diagnostics: list[str] = field(default_factory=list)


def _capped_start(caps: np.ndarray, scores: np.ndarray | None = None) -> np.ndarray:
    if float(caps.sum()) < 1.0 - 1e-9:
        raise InfeasibleOptimization(
            f"The combined position capacity is only {caps.sum():.1%}. "
            "Add eligible stocks, increase the execution horizon/ADTV fraction, or review missing ADTV."
        )
    n_assets = len(caps)
    if scores is None or not np.isfinite(scores).all() or scores.sum() <= 0:
        scores = np.ones(n_assets)
    scores = np.maximum(np.asarray(scores, dtype=float), 1e-12)
    weights = np.zeros(n_assets)
    remaining = 1.0
    active = np.ones(n_assets, dtype=bool)
    for _ in range(n_assets + 2):
        if remaining <= 1e-12 or not active.any():
            break
        active_scores = scores[active]
        proposal = remaining * active_scores / active_scores.sum()
        active_indices = np.where(active)[0]
        room = caps[active_indices] - weights[active_indices]
        allocations = np.minimum(proposal, room)
        weights[active_indices] += allocations
        remaining = 1.0 - weights.sum()
        active[active_indices[room - allocations <= 1e-12]] = False
    if remaining > 1e-8:
        for index in np.argsort(caps - weights)[::-1]:
            add = min(remaining, caps[index] - weights[index])
            if add > 0:
                weights[index] += add
                remaining -= add
            if remaining <= 1e-10:
                break
    return weights / weights.sum()


def _expected_returns(holdings: pd.DataFrame, returns: pd.DataFrame, tickers: list[str], annualization: int) -> np.ndarray:
    if "expected_return" in holdings and holdings["expected_return"].notna().any():
        supplied = holdings.set_index("ticker")["expected_return"].reindex(tickers)
        if supplied.dropna().abs().median() > 1.5:
            supplied = supplied / 100.0
    else:
        supplied = pd.Series(index=tickers, dtype=float)
    if not returns.empty:
        historical = returns.reindex(columns=tickers).ewm(span=min(126, max(20, len(returns))), adjust=False).mean().iloc[-1]
        historical = historical * annualization
    else:
        historical = pd.Series(0.0, index=tickers)
    return supplied.fillna(historical).fillna(0.0).to_numpy(dtype=float)


def _covariance(returns: pd.DataFrame, tickers: list[str], annualization: int, shrinkage: float) -> np.ndarray:
    aligned = returns.reindex(columns=tickers).fillna(0.0)
    if len(aligned) < 2:
        return np.eye(len(tickers)) * 0.04
    sample = aligned.cov().to_numpy(dtype=float) * annualization
    sample = np.nan_to_num(sample, nan=0.0, posinf=0.0, neginf=0.0)
    diagonal = np.diag(np.diag(sample))
    covariance = (1.0 - shrinkage) * sample + shrinkage * diagonal
    covariance += np.eye(len(tickers)) * 1e-8
    return covariance


def optimize_portfolio(
    holdings: pd.DataFrame,
    returns: pd.DataFrame,
    portfolio_value: float | None = None,
    config: LAMVOConfig | None = None,
) -> LAMVOResult:
    """Run LAMVO with hard 10% and 20%-of-30D-ADTV position caps.

    The liquidity cap is applied to target position value across the selected
    execution horizon: ``target value <= ADTV × participation × days``.
    """
    config = config or LAMVOConfig()
    data = holdings.copy().reset_index(drop=True)
    if data["ticker"].duplicated().any():
        raise ValueError("Each ticker must appear only once in Holdings.")
    tickers = data["ticker"].astype(str).tolist()
    if portfolio_value is None:
        portfolio_value = float(data["market_value"].sum())
    if portfolio_value <= 0:
        raise ValueError("Portfolio value must be greater than zero.")

    current = data["current_weight"].to_numpy(dtype=float)
    current = np.maximum(current, 0.0)
    current = current / current.sum()
    adtv = pd.to_numeric(data.get("adtv_30d_lkr"), errors="coerce").to_numpy(dtype=float)
    missing_adtv = ~np.isfinite(adtv) | (adtv <= 0)
    capacity = config.max_adtv_fraction * adtv * max(int(config.execution_days), 1) / portfolio_value
    capacity[missing_adtv] = config.max_stock_weight
    caps = np.minimum(config.max_stock_weight, np.maximum(capacity, 0.0))
    start = _capped_start(caps, np.minimum(current + 0.01, caps))

    expected = _expected_returns(data, returns, tickers, config.annualization)
    covariance = _covariance(returns, tickers, config.annualization, config.covariance_shrinkage)
    spreads = pd.to_numeric(data.get("bid_ask_spread_pct", 0.01), errors="coerce").fillna(0.01).to_numpy()
    safe_adtv = np.where(missing_adtv, portfolio_value, adtv)
    # A convex surrogate: wider spreads and smaller ADTV make turnover more expensive.
    liquidity_scale = 0.5 * spreads + config.market_impact_coefficient * np.sqrt(
        portfolio_value / np.maximum(safe_adtv, 1.0)
    )

    def components(weights: np.ndarray) -> tuple[float, float, float]:
        portfolio_return = float(expected @ weights)
        variance = float(weights @ covariance @ weights)
        liquidity = float(np.sum(liquidity_scale * np.square(weights - current)))
        return portfolio_return, variance, liquidity

    def objective(weights: np.ndarray) -> float:
        portfolio_return, variance, liquidity = components(weights)
        return -portfolio_return + config.risk_aversion * variance + config.liquidity_penalty * liquidity

    result = minimize(
        objective,
        start,
        method="SLSQP",
        bounds=[(0.0, float(cap)) for cap in caps],
        constraints=[{"type": "eq", "fun": lambda w: float(np.sum(w) - 1.0)}],
        options={"maxiter": 2000, "ftol": 1e-11, "disp": False},
    )
    weights = np.asarray(result.x if result.success else start, dtype=float)
    weights[np.abs(weights) < 1e-10] = 0.0
    weights = weights / weights.sum()
    portfolio_return, variance, liquidity = components(weights)
    allocations = data[["ticker", "sector", "current_weight", "market_value", "adtv_30d_lkr"]].copy()
    allocations["target_weight"] = weights
    allocations["trade_weight"] = allocations["target_weight"] - allocations["current_weight"]
    allocations["target_value"] = allocations["target_weight"] * portfolio_value
    allocations["trade_value"] = allocations["trade_weight"] * portfolio_value
    allocations["max_weight"] = caps
    allocations["liquidity_score"] = liquidity_scale
    allocations["target_adtv_fraction"] = allocations["target_value"] / allocations["adtv_30d_lkr"].replace(0, np.nan)
    diagnostics: list[str] = []
    if missing_adtv.any():
        diagnostics.append(
            f"ADTV was unavailable for {int(missing_adtv.sum())} counter(s); only the {config.max_stock_weight:.0%} name cap was applied to them."
        )
    if (current > caps + 1e-8).any():
        diagnostics.append("Current holdings above target capacity are treated as required reductions.")
    return LAMVOResult(
        allocations=allocations.sort_values("target_weight", ascending=False).reset_index(drop=True),
        success=bool(result.success),
        message=str(result.message),
        objective_value=float(objective(weights)),
        expected_return=portfolio_return,
        volatility=float(np.sqrt(max(variance, 0.0))),
        liquidity_penalty_value=liquidity,
        diagnostics=diagnostics,
    )
