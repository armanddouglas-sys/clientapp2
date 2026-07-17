"""Streamlit entry point for the CSE Liquidity-Adjusted Portfolio Manager."""

from __future__ import annotations

from io import BytesIO

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from cse_lamvo.compliance import ComplianceLimits, check_compliance, tax_adjusted_returns
from cse_lamvo.config import DEFAULT_SCENARIOS, SOURCE_LINKS
from cse_lamvo.data_engineering import WorkbookValidationError, load_portfolio_workbook, returns_matrix
from cse_lamvo.optimizer import InfeasibleOptimization, LAMVOConfig, optimize_portfolio
from cse_lamvo.reporting import build_analysis_workbook, build_text_report
from cse_lamvo.risk import (
    active_share,
    macro_stress_test,
    normalized_benchmark_levels,
    portfolio_return_series,
    proposed_trade_blotter,
    risk_metrics,
    settlement_schedule,
    tracking_error,
)
from cse_lamvo.sample_data import demonstration_workbook, upload_template


st.set_page_config(page_title="CSE Portfolio Lab", page_icon="◈", layout="wide", initial_sidebar_state="expanded")

NAVY = "#102A43"
BLUE = "#2F80ED"
TEAL = "#00A6A6"
RED = "#D64550"
GOLD = "#F2C94C"
GREY = "#627D98"

st.markdown(
    """
    <style>
    .stApp {background: #F5F7FA; color: #102A43;}
    [data-testid="stSidebar"] {background: #102A43;}
    [data-testid="stSidebar"] * {color: #F7FAFC;}
    [data-testid="stSidebar"] input {color: #102A43;}
    [data-testid="stMetric"] {background: white; border: 1px solid #D9E2EC; border-radius: 12px; padding: 12px 16px;}
    .hero {background: linear-gradient(115deg,#102A43 0%,#1F4E78 70%,#007C91 100%); padding: 28px 32px; border-radius: 16px; color: white; margin-bottom: 18px;}
    .hero h1 {font-size: 2.05rem; margin: 0 0 4px 0; color: white;}
    .hero p {margin: 0; color: #D9EAF2;}
    .eyebrow {font-size: .76rem; letter-spacing: .16em; text-transform: uppercase; color: #9FB3C8; font-weight: 700;}
    .note {background: #E8F1FA; border-left: 4px solid #2F80ED; border-radius: 6px; padding: 10px 14px; color: #243B53;}
    div[data-testid="stDataFrame"] {background: white; border-radius: 10px;}
    .stTabs [data-baseweb="tab-list"] {gap: 4px; background: white; border-radius: 10px; padding: 4px;}
    .stTabs [data-baseweb="tab"] {border-radius: 8px; padding: 9px 16px;}
    </style>
    """,
    unsafe_allow_html=True,
)


def fmt_lkr(value: float) -> str:
    value = float(value)
    if abs(value) >= 1_000_000_000:
        return f"LKR {value / 1_000_000_000:.2f}bn"
    if abs(value) >= 1_000_000:
        return f"LKR {value / 1_000_000:.2f}mn"
    return f"LKR {value:,.0f}"


def fmt_pct(value: float) -> str:
    return "N/A" if pd.isna(value) else f"{value:.2%}"


def dataframe(frame: pd.DataFrame, *, height: int = 420, percentages: list[str] | None = None) -> None:
    percentages = percentages or []
    formats = {column: st.column_config.NumberColumn(format="%.2f%%") for column in percentages if column in frame}
    shown = frame.copy()
    for column in percentages:
        if column in shown:
            shown[column] = shown[column] * 100.0
    st.dataframe(shown, width="stretch", height=height, hide_index=True, column_config=formats)


def benchmark_weight_series(holdings: pd.DataFrame, column: str) -> pd.Series:
    series = holdings.set_index("ticker")[column].clip(lower=0).astype(float)
    if series.sum() > 1.0 + 1e-8:
        series = series / series.sum()
    elif series.sum() < 1.0 - 1e-8:
        series.loc["OTHER_BENCHMARK_CONSTITUENTS"] = 1.0 - series.sum()
    return series


@st.cache_data(show_spinner=False)
def parse_workbook(data: bytes):
    return load_portfolio_workbook(data)


demo_bytes = demonstration_workbook()
template_bytes = upload_template()

with st.sidebar:
    st.markdown("### CSE Portfolio Lab")
    st.caption("Institutional analytics · Sri Lankan equities")
    uploaded = st.file_uploader("Client portfolio workbook", type=["xlsx", "xlsm"])
    source_bytes = uploaded.getvalue() if uploaded is not None else demo_bytes
    if uploaded is None:
        st.info("Showing synthetic demonstration data.")
    c1, c2 = st.columns(2)
    c1.download_button("Template", template_bytes, "CSE_Portfolio_Template.xlsx", width="stretch")
    c2.download_button("Demo", demo_bytes, "CSE_LAMVO_Demo.xlsx", width="stretch")

    st.markdown("---")
    st.markdown("#### Optimization policy")
    risk_aversion = st.slider("Risk aversion", 0.5, 15.0, 4.0, 0.5)
    liquidity_penalty = st.slider("Liquidity penalty", 0.0, 15.0, 2.0, 0.25)
    max_stock_weight = st.slider("Maximum stock weight", 0.02, 0.25, 0.10, 0.01, format="%.0f%%")
    adtv_fraction = st.slider("Maximum ADTV participation", 0.05, 1.0, 0.20, 0.05, format="%.0f%%")
    execution_days = st.number_input("Execution horizon (market days)", 1, 20, 1)
    market_impact = st.slider("Market-impact coefficient", 0.0, 0.50, 0.10, 0.01)

    st.markdown("#### Risk & settlement")
    margin_rate = st.slider("Clearing margin assumption", 0.0, 0.50, 0.15, 0.01, format="%.0f%%")
    rate_shock = st.number_input("Combined stress: CBSL hike (bp)", 0, 1000, 300, step=25)
    fx_shock = st.number_input("Combined stress: LKR depreciation (%)", 0.0, 50.0, 15.0, step=1.0, format="%.1f")
    inflation_shock = st.number_input("Combined stress: inflation shock (pp)", 0.0, 20.0, 5.0, step=0.5)

    st.markdown("#### Compliance & tax")
    sector_limit = st.slider("Sector exposure limit", 0.10, 0.60, 0.25, 0.01, format="%.0f%%")
    related_limit = st.slider("Related-group limit", 0.10, 0.50, 0.20, 0.01, format="%.0f%%")
    illiquid_limit = st.slider("Secondary-board limit", 0.0, 0.50, 0.15, 0.01, format="%.0f%%")
    dividend_wht = st.slider("Dividend WHT", 0.0, 0.40, 0.15, 0.01, format="%.0f%%")
    transaction_cost = st.number_input("All-in equity transaction cost", 0.0, 0.05, 0.0112, step=0.0001, format="%.4f")
    other_levy = st.number_input("Other statutory levy", 0.0, 0.05, 0.0, step=0.0001, format="%.4f")

try:
    workbook = parse_workbook(source_bytes)
except (WorkbookValidationError, ValueError, OSError) as error:
    st.error(f"The workbook could not be loaded: {error}")
    st.stop()

holdings = workbook.holdings.copy()
portfolio_value = float(holdings["market_value"].sum())
asset_returns = returns_matrix(workbook.adjusted_prices)
current_weights = holdings.set_index("ticker")["current_weight"]
current_series = portfolio_return_series(asset_returns, current_weights)
current_risk = risk_metrics(current_series)

optimizer_config = LAMVOConfig(
    risk_aversion=risk_aversion,
    liquidity_penalty=liquidity_penalty,
    market_impact_coefficient=market_impact,
    max_stock_weight=max_stock_weight,
    max_adtv_fraction=adtv_fraction,
    execution_days=int(execution_days),
)
optimization_error = ""
try:
    optimization = optimize_portfolio(holdings, asset_returns, portfolio_value, optimizer_config)
    allocations = optimization.allocations
    target_weights = allocations.set_index("ticker")["target_weight"]
except (InfeasibleOptimization, ValueError) as error:
    optimization = None
    optimization_error = str(error)
    allocations = holdings[["ticker", "sector", "current_weight", "market_value", "adtv_30d_lkr"]].copy()
    allocations["target_weight"] = allocations["current_weight"]
    allocations["trade_weight"] = 0.0
    allocations["target_value"] = allocations["market_value"]
    allocations["trade_value"] = 0.0
    allocations["max_weight"] = max_stock_weight
    allocations["liquidity_score"] = np.nan
    allocations["target_adtv_fraction"] = allocations["target_value"] / allocations["adtv_30d_lkr"]
    target_weights = current_weights

target_series = portfolio_return_series(asset_returns, target_weights)
target_risk = risk_metrics(target_series)
aspi_weights = benchmark_weight_series(holdings, "aspi_weight")
sl20_weights = benchmark_weight_series(holdings, "sl20_weight")
benchmark_summary = {
    "aspi_active_share": active_share(target_weights, aspi_weights),
    "sl20_active_share": active_share(target_weights, sl20_weights),
    "aspi_tracking_error": float("nan"),
    "sl20_tracking_error": float("nan"),
}
if not workbook.benchmarks.empty:
    levels = workbook.benchmarks.set_index("date")
    benchmark_summary["aspi_tracking_error"] = tracking_error(target_series, levels["aspi"])
    benchmark_summary["sl20_tracking_error"] = tracking_error(target_series, levels["sp_sl20"])

custom_scenarios = dict(DEFAULT_SCENARIOS)
custom_scenarios["Combined domestic stress"] = {
    "rate_hike_bps": float(rate_shock),
    "lkr_depreciation_pct": float(fx_shock),
    "inflation_shock_pp": float(inflation_shock),
}
stresses, stress_contributions = macro_stress_test(holdings, target_weights, custom_scenarios)

proposed_trades = proposed_trade_blotter(allocations, pd.Timestamp.today(), transaction_cost)
trade_source = workbook.trades if not workbook.trades.empty else proposed_trades
settled_trades, settlement = settlement_schedule(trade_source, workbook.holidays, margin_rate)

limits = ComplianceLimits(
    single_issuer_limit=max_stock_weight,
    sector_limit=sector_limit,
    related_group_limit=related_limit,
    illiquid_board_limit=illiquid_limit,
    max_adtv_fraction=adtv_fraction,
    execution_days=int(execution_days),
)
current_compliance = check_compliance(holdings, current_weights, limits, portfolio_value)
target_compliance = check_compliance(holdings, target_weights, limits, portfolio_value)
planned_turnover = float(allocations["trade_value"].abs().sum() / portfolio_value) if portfolio_value else 0.0
tax_detail, tax_summary = tax_adjusted_returns(
    holdings,
    dividend_wht_rate=dividend_wht,
    transaction_cost_rate=transaction_cost,
    planned_turnover=planned_turnover,
    other_statutory_levy_rate=other_levy,
)

st.markdown(
    """
    <div class="hero">
      <div class="eyebrow">Liquidity-adjusted portfolio intelligence</div>
      <h1>CSE Portfolio Lab</h1>
      <p>Portfolio construction, benchmark risk, T+2 liquidity, domestic stress testing and compliance controls.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

if workbook.warnings:
    with st.expander(f"Data warnings ({len(workbook.warnings)})"):
        for warning in workbook.warnings:
            st.warning(warning)
if optimization_error:
    st.error("Optimization is infeasible under the selected caps. " + optimization_error)

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Portfolio value", fmt_lkr(portfolio_value))
k2.metric("Holdings", f"{len(holdings)}")
k3.metric("Annualized volatility", fmt_pct(current_risk.annual_volatility))
k4.metric("ASPI active share", fmt_pct(benchmark_summary["aspi_active_share"]))
breach_count = int((target_compliance["status"] == "BREACH").sum())
k5.metric("Target compliance", "PASS" if breach_count == 0 else f"{breach_count} breaches")

tabs = st.tabs(["Overview", "LAMVO", "Risk & Benchmarks", "T+2 Settlement", "Compliance & Tax", "Data Quality", "Report"])

with tabs[0]:
    st.subheader("Portfolio overview")
    left, right = st.columns([1, 1])
    sector = holdings.groupby("sector", as_index=False)["market_value"].sum()
    sector["weight"] = sector["market_value"] / sector["market_value"].sum()
    fig = px.pie(sector, values="market_value", names="sector", hole=0.58, color_discrete_sequence=px.colors.qualitative.Safe)
    fig.update_traces(textposition="inside", textinfo="percent")
    fig.update_layout(title="Current sector exposure", margin=dict(l=10, r=10, t=50, b=10), legend_title="GICS sector")
    left.plotly_chart(fig, width="stretch")

    top = holdings.nlargest(10, "current_weight").sort_values("current_weight")
    bar = px.bar(top, x="current_weight", y="ticker", orientation="h", color="sector", color_discrete_sequence=px.colors.qualitative.Safe)
    bar.update_layout(title="Largest positions", xaxis_tickformat=".0%", margin=dict(l=10, r=10, t=50, b=10), showlegend=False)
    right.plotly_chart(bar, width="stretch")
    summary_cols = ["ticker", "sector", "board", "quantity", "avg_cost", "current_price", "market_value", "current_weight", "unrealised_pnl", "adtv_30d_lkr"]
    dataframe(holdings[summary_cols].sort_values("market_value", ascending=False), percentages=["current_weight"])

with tabs[1]:
    st.subheader("Liquidity-Adjusted Mean-Variance Optimization")
    st.markdown(
        '<div class="note">The target is long-only and fully invested. Each target position is capped at the lower of the configured name limit and the selected share of 30-day ADTV across the execution horizon.</div>',
        unsafe_allow_html=True,
    )
    if optimization is not None:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Target expected return", fmt_pct(optimization.expected_return))
        m2.metric("Target volatility", fmt_pct(optimization.volatility), delta=f"{optimization.volatility - current_risk.annual_volatility:+.2%}")
        m3.metric("Gross rebalance", fmt_lkr(allocations["trade_value"].abs().sum()))
        m4.metric("Solver", "Optimal" if optimization.success else "Fallback")
        for message in optimization.diagnostics:
            st.caption(message)
    comparison = allocations.sort_values("target_weight", ascending=False).head(20).melt(
        id_vars="ticker", value_vars=["current_weight", "target_weight"], var_name="allocation", value_name="weight"
    )
    fig = px.bar(
        comparison,
        x="ticker",
        y="weight",
        color="allocation",
        barmode="group",
        color_discrete_map={"current_weight": GREY, "target_weight": BLUE},
    )
    fig.update_layout(title="Current vs LAMVO target", yaxis_tickformat=".0%", legend_title="")
    st.plotly_chart(fig, width="stretch")
    dataframe(
        allocations[["ticker", "sector", "current_weight", "target_weight", "trade_value", "max_weight", "adtv_30d_lkr", "target_adtv_fraction", "liquidity_score"]],
        percentages=["current_weight", "target_weight", "max_weight", "target_adtv_fraction"],
    )

with tabs[2]:
    st.subheader("Risk and benchmark diagnostics")
    r1, r2, r3, r4 = st.columns(4)
    r1.metric("Historical max drawdown", fmt_pct(current_risk.max_drawdown))
    r2.metric("ASPI tracking error", fmt_pct(benchmark_summary["aspi_tracking_error"]))
    r3.metric("S&P SL20 tracking error", fmt_pct(benchmark_summary["sl20_tracking_error"]))
    r4.metric("S&P SL20 active share", fmt_pct(benchmark_summary["sl20_active_share"]))
    left, right = st.columns([1.35, 1])
    if not current_series.empty:
        wealth = pd.DataFrame(
            {
                "Current portfolio": (1 + current_series).cumprod(),
                "LAMVO target": (1 + target_series).cumprod(),
            }
        ).reset_index(names="date")
        if not workbook.benchmarks.empty:
            index_levels = workbook.benchmarks.set_index("date")
            for column, label in [("aspi", "ASPI"), ("sp_sl20", "S&P SL20")]:
                normalized = normalized_benchmark_levels(index_levels[column])
                if not normalized.dropna().empty:
                    wealth = wealth.merge(normalized.rename(label), left_on="date", right_index=True, how="left")
        line = px.line(wealth, x="date", y=[c for c in wealth.columns if c != "date"], color_discrete_sequence=[NAVY, BLUE, GOLD, TEAL])
        line.update_layout(title="Normalized performance", yaxis_title="Growth of 1.00", legend_title="")
        left.plotly_chart(line, width="stretch")
    stress_chart = px.bar(
        stresses.sort_values("estimated_portfolio_return"),
        x="estimated_portfolio_return",
        y="scenario",
        orientation="h",
        color="estimated_portfolio_return",
        color_continuous_scale=[RED, GOLD, TEAL],
    )
    stress_chart.update_layout(title="Domestic macro stress impact", xaxis_tickformat=".1%", coloraxis_showscale=False)
    right.plotly_chart(stress_chart, width="stretch")
    st.caption("Stress betas are scenario sensitivities per +100 bp rates, +10% USD/LKR and +5 pp inflation. Upload stock-level betas to override sector defaults.")
    dataframe(stresses, percentages=["estimated_portfolio_return"])
    with st.expander("Stress attribution by security"):
        dataframe(stress_contributions, percentages=["stressed_return", "portfolio_contribution"])

with tabs[3]:
    st.subheader("T+2 settlement liquidity")
    source_label = "uploaded trade blotter" if not workbook.trades.empty else "LAMVO proposed trades"
    st.caption(f"Based on the {source_label}. Uploaded settlement dates override calculated T+2 dates; Holidays sheet dates are excluded.")
    s1, s2, s3 = st.columns(3)
    s1.metric("Peak cash lockup", fmt_lkr(settlement["buy_cash_locked"].max() if not settlement.empty else 0))
    s2.metric("Peak pending sale proceeds", fmt_lkr(settlement["sale_proceeds_pending"].max() if not settlement.empty else 0))
    s3.metric("Peak margin requirement", fmt_lkr(settlement["margin_requirement"].max() if not settlement.empty else 0))
    if not settlement.empty:
        cash = settlement.melt(id_vars="date", value_vars=["buy_cash_locked", "sale_proceeds_pending", "margin_requirement"], var_name="cash_type", value_name="lkr")
        fig = px.area(cash, x="date", y="lkr", color="cash_type", color_discrete_sequence=[BLUE, TEAL, GOLD])
        fig.update_layout(title="Unsettled cash and clearing requirement", yaxis_tickformat=",")
        st.plotly_chart(fig, width="stretch")
    dataframe(settled_trades)
    with st.expander("Daily settlement schedule", expanded=True):
        dataframe(settlement)

with tabs[4]:
    st.subheader("Compliance and tax-adjusted returns")
    basis = st.radio("Compliance basis", ["LAMVO target", "Current portfolio"], horizontal=True)
    compliance = target_compliance if basis == "LAMVO target" else current_compliance
    breaches = compliance[compliance["status"] == "BREACH"]
    if breaches.empty:
        st.success("No breaches under the configured rule set.")
    else:
        st.error(f"{len(breaches)} breach(es) require review.")
    dataframe(compliance, percentages=["actual", "limit", "headroom"])
    st.caption("Limits must be approved by the AMC compliance officer for the specific SEC-licensed product, trust deed and client mandate.")
    st.markdown("#### Tax and transaction-cost bridge")
    t1, t2, t3, t4 = st.columns(4)
    t1.metric("Gross dividend", fmt_lkr(tax_summary["gross_dividend"]))
    t2.metric("Dividend WHT", fmt_lkr(tax_summary["dividend_wht"]))
    t3.metric("Net dividend", fmt_lkr(tax_summary["net_dividend"]))
    t4.metric("Net return on cost", fmt_pct(tax_summary["net_return_on_cost"]))
    bridge = pd.DataFrame(
        {
            "component": ["Capital return", "Gross dividend", "Dividend WHT", "Transaction costs", "Other levies"],
            "value": [
                tax_summary["capital_return_lkr"],
                tax_summary["gross_dividend"],
                -tax_summary["dividend_wht"],
                -tax_summary["transaction_cost"],
                -tax_summary["other_statutory_levy"],
            ],
        }
    )
    fig = go.Figure(go.Waterfall(x=bridge["component"], y=bridge["value"], measure=["relative"] * len(bridge), connector={"line": {"color": "#9FB3C8"}}))
    fig.update_layout(title="Return after WHT and local trading charges", yaxis_title="LKR")
    st.plotly_chart(fig, width="stretch")
    with st.expander("Security-level tax detail"):
        dataframe(tax_detail[["ticker", "gross_dividend", "dividend_wht", "net_dividend", "capital_return_lkr", "net_total_return_lkr"]])

with tabs[5]:
    st.subheader("Data quality and corporate actions")
    stale = workbook.adjusted_prices["stale_price"].mean() if "stale_price" in workbook.adjusted_prices and not workbook.adjusted_prices.empty else 0.0
    q1, q2, q3, q4 = st.columns(4)
    q1.metric("Price observations", f"{len(workbook.adjusted_prices):,}")
    q2.metric("Zero-volume / stale", fmt_pct(stale))
    q3.metric("Corporate actions", f"{len(workbook.corporate_actions)}")
    q4.metric("Missing ADTV", f"{holdings['adtv_30d_lkr'].isna().sum()}")
    st.markdown(
        '<div class="note">Missing and zero-volume prices are forward-filled for return alignment, but the original volume remains zero and each observation is flagged as stale. Corporate actions are backward-adjusted to avoid artificial XD/XR/split gaps.</div>',
        unsafe_allow_html=True,
    )
    dataframe(workbook.corporate_actions)
    quality = (
        workbook.adjusted_prices.groupby("ticker")
        .agg(observations=("date", "count"), stale_observations=("stale_price", "sum"), last_price=("close", "last"), adtv_30d_lkr=("volume", lambda x: np.nan))
        .reset_index()
    ) if not workbook.adjusted_prices.empty else pd.DataFrame()
    if not quality.empty:
        quality["stale_pct"] = quality["stale_observations"] / quality["observations"]
        quality = quality.drop(columns="adtv_30d_lkr").merge(holdings[["ticker", "adtv_30d_lkr"]], on="ticker", how="left")
        dataframe(quality, percentages=["stale_pct"])
    with st.expander("Adjusted price audit sample"):
        dataframe(workbook.adjusted_prices.tail(500))

with tabs[6]:
    st.subheader("Client-ready output")
    client_name = st.text_input("Client / portfolio name", value="Confidential Client Portfolio")
    risk_summary = {
        "annual_return": target_risk.annual_return,
        "annual_volatility": target_risk.annual_volatility,
        "max_drawdown": target_risk.max_drawdown,
    }
    report_text = build_text_report(
        client_name,
        holdings,
        allocations,
        risk_summary,
        benchmark_summary,
        stresses,
        target_compliance,
        settlement,
        tax_summary,
        methodology_note="LAMVO minimizes expected risk and turnover cost while enforcing long-only, full-investment, issuer and ADTV capacity constraints.",
    )
    st.text_area("Portfolio report", report_text, height=620)
    report_workbook = build_analysis_workbook(
        holdings,
        allocations,
        target_compliance,
        stresses,
        stress_contributions,
        settlement,
        workbook.adjusted_prices,
        report_text,
    )
    d1, d2 = st.columns(2)
    d1.download_button("Download text report", report_text, "CSE_Portfolio_Report.txt", "text/plain", width="stretch")
    d2.download_button(
        "Download analysis workbook",
        report_workbook,
        "CSE_Portfolio_Analysis.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        width="stretch",
    )
    with st.expander("Methodology and official references"):
        st.markdown(
            "The 15% dividend WHT default is based on the IRD 2025/26 tax chart. The 1.12% transaction-cost default is the CSE-published all-in rate for equity trades up to LKR 100 million. Compliance parameters remain editable because limits vary by scheme, trust deed and mandate."
        )
        for label, url in SOURCE_LINKS.items():
            st.markdown(f"- [{label}]({url})")
        st.warning("Decision-support only. Obtain current tax, legal, compliance and clearing-member confirmation before execution.")
