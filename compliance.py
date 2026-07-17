"""Configurable SEC/mandate exposure checks and tax-adjusted return analysis."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class ComplianceLimits:
    single_issuer_limit: float = 0.10
    sector_limit: float = 0.25
    related_group_limit: float = 0.20
    illiquid_board_limit: float = 0.15
    minimum_cash_weight: float = 0.0
    max_adtv_fraction: float = 0.20
    execution_days: int = 1


def _result(category: str, test: str, actual: float, limit: float, rule: str, relation: str = "max") -> dict:
    passed = actual <= limit + 1e-9 if relation == "max" else actual + 1e-9 >= limit
    return {
        "category": category,
        "test": test,
        "status": "PASS" if passed else "BREACH",
        "actual": float(actual),
        "limit": float(limit),
        "headroom": float(limit - actual if relation == "max" else actual - limit),
        "rule_basis": rule,
    }


def check_compliance(
    holdings: pd.DataFrame,
    weights: pd.Series | None = None,
    limits: ComplianceLimits | None = None,
    portfolio_value: float | None = None,
) -> pd.DataFrame:
    """Check a portfolio against configurable AMC mandate/SEC rule parameters.

    These checks are controls, not a legal determination. The AMC compliance
    officer should set limits from the relevant SEC-approved trust deed, client
    mandate and current directives.
    """
    limits = limits or ComplianceLimits()
    data = holdings.copy()
    if weights is None:
        weights = data.set_index("ticker")["current_weight"]
    data["analysis_weight"] = data["ticker"].map(weights).fillna(0.0)
    if portfolio_value is None:
        portfolio_value = float(data["market_value"].sum())
    results: list[dict] = []
    for row in data.itertuples(index=False):
        results.append(
            _result(
                "Issuer exposure",
                str(row.ticker),
                float(row.analysis_weight),
                limits.single_issuer_limit,
                "SEC-approved trust deed / client mandate — configurable",
            )
        )
        adtv = getattr(row, "adtv_30d_lkr", np.nan)
        if pd.notna(adtv) and float(adtv) > 0 and portfolio_value > 0:
            position_fraction = float(row.analysis_weight) * portfolio_value / float(adtv)
            results.append(
                _result(
                    "Liquidity capacity",
                    str(row.ticker),
                    position_fraction,
                    limits.max_adtv_fraction * max(limits.execution_days, 1),
                    "Internal LAMVO liquidity policy",
                )
            )

    for sector, weight in data.groupby("sector")["analysis_weight"].sum().items():
        results.append(
            _result(
                "Sector exposure",
                str(sector),
                float(weight),
                limits.sector_limit,
                "SEC-approved trust deed / client mandate — configurable",
            )
        )
    related = data[data["related_group"].fillna("").astype(str).str.strip().ne("")]
    for group, weight in related.groupby("related_group")["analysis_weight"].sum().items():
        results.append(
            _result(
                "Related issuers",
                str(group),
                float(weight),
                limits.related_group_limit,
                "Connected-party / group exposure policy — configurable",
            )
        )
    illiquid_boards = {"EMPOWER BOARD", "SECOND BOARD", "DIRI SAVI BOARD", "EMPOWER"}
    illiquid_weight = data.loc[
        data["board"].fillna("").astype(str).str.upper().isin(illiquid_boards), "analysis_weight"
    ].sum()
    results.append(
        _result(
            "Board exposure",
            "Empower / Second / Diri Savi",
            float(illiquid_weight),
            limits.illiquid_board_limit,
            "AMC illiquid-security limit — configurable",
        )
    )
    results.append(
        _result(
            "Long-only",
            "No negative weights",
            float(max(-data["analysis_weight"].min(), 0.0)),
            0.0,
            "CSE long-only portfolio mandate",
        )
    )
    results.append(
        _result(
            "Portfolio total",
            "Weights sum to 100%",
            float(abs(data["analysis_weight"].sum() - 1.0)),
            1e-6,
            "Fully invested portfolio constraint",
        )
    )
    return pd.DataFrame(results).sort_values(["status", "category", "actual"], ascending=[True, True, False])


def tax_adjusted_returns(
    holdings: pd.DataFrame,
    dividend_wht_rate: float = 0.15,
    transaction_cost_rate: float = 0.0112,
    planned_turnover: float = 0.0,
    other_statutory_levy_rate: float = 0.0,
) -> tuple[pd.DataFrame, dict[str, float]]:
    """Calculate net dividend income and return after editable local charges.

    ``transaction_cost_rate`` defaults to the CSE-published all-in equity
    charge for trades up to LKR 100m. Above that level, the applicable step-up
    fee/negotiated brokerage should be entered by the user.
    """
    data = holdings.copy()
    for column in ["dividend_per_share", "quantity", "market_value", "avg_cost", "current_price"]:
        data[column] = pd.to_numeric(data.get(column, 0.0), errors="coerce").fillna(0.0)
    data["gross_dividend"] = data["dividend_per_share"] * data["quantity"]
    data["dividend_wht"] = data["gross_dividend"] * dividend_wht_rate
    data["net_dividend"] = data["gross_dividend"] - data["dividend_wht"]
    data["capital_return_lkr"] = (data["current_price"] - data["avg_cost"]) * data["quantity"]
    data["gross_total_return_lkr"] = data["capital_return_lkr"] + data["gross_dividend"]
    data["net_total_return_lkr"] = data["capital_return_lkr"] + data["net_dividend"]
    portfolio_value = float(data["market_value"].sum())
    turnover_value = portfolio_value * max(planned_turnover, 0.0)
    transaction_cost = turnover_value * transaction_cost_rate
    statutory_levy = turnover_value * other_statutory_levy_rate
    total = {
        "portfolio_value": portfolio_value,
        "gross_dividend": float(data["gross_dividend"].sum()),
        "dividend_wht": float(data["dividend_wht"].sum()),
        "net_dividend": float(data["net_dividend"].sum()),
        "capital_return_lkr": float(data["capital_return_lkr"].sum()),
        "gross_total_return_lkr": float(data["gross_total_return_lkr"].sum()),
        "transaction_cost": float(transaction_cost),
        "other_statutory_levy": float(statutory_levy),
        "net_total_return_lkr": float(data["net_total_return_lkr"].sum() - transaction_cost - statutory_levy),
    }
    cost_basis = float((data["avg_cost"] * data["quantity"]).sum())
    total["net_return_on_cost"] = total["net_total_return_lkr"] / cost_basis if cost_basis > 0 else float("nan")
    return data, total
