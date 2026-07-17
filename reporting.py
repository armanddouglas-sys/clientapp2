"""Client-ready text and Excel reporting helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO

import pandas as pd


def lkr(value: float) -> str:
    return f"LKR {value:,.0f}"


def pct(value: float) -> str:
    return "N/A" if pd.isna(value) else f"{value:.2%}"


def build_text_report(
    client_name: str,
    holdings: pd.DataFrame,
    allocations: pd.DataFrame,
    risk_summary: dict[str, float],
    benchmark_summary: dict[str, float],
    stresses: pd.DataFrame,
    compliance: pd.DataFrame,
    settlement: pd.DataFrame,
    tax_summary: dict[str, float],
    methodology_note: str = "",
) -> str:
    value = float(holdings["market_value"].sum())
    breaches = compliance[compliance["status"].eq("BREACH")]
    largest = holdings.nlargest(5, "current_weight")
    trades = allocations.reindex(columns=["ticker", "trade_value", "target_weight"]).copy()
    buys = trades[trades["trade_value"] > 0].nlargest(5, "trade_value")
    sells = trades[trades["trade_value"] < 0].nsmallest(5, "trade_value")
    max_buy_lock = float(settlement["buy_cash_locked"].max()) if not settlement.empty else 0.0
    max_margin = float(settlement["margin_requirement"].max()) if not settlement.empty else 0.0

    lines = [
        "CSE LIQUIDITY-ADJUSTED PORTFOLIO REVIEW",
        f"Client: {client_name or 'Confidential client'}",
        f"Generated: {datetime.now(timezone.utc).strftime('%d %b %Y %H:%M UTC')}",
        "",
        "EXECUTIVE SUMMARY",
        f"Portfolio market value: {lkr(value)}",
        f"Historical annualized volatility: {pct(risk_summary.get('annual_volatility', float('nan')))}",
        f"Historical maximum drawdown: {pct(risk_summary.get('max_drawdown', float('nan')))}",
        f"ASPI tracking error: {pct(benchmark_summary.get('aspi_tracking_error', float('nan')))}",
        f"S&P SL20 tracking error: {pct(benchmark_summary.get('sl20_tracking_error', float('nan')))}",
        f"ASPI active share: {pct(benchmark_summary.get('aspi_active_share', float('nan')))}",
        f"S&P SL20 active share: {pct(benchmark_summary.get('sl20_active_share', float('nan')))}",
        f"Compliance status: {'PASS' if breaches.empty else f'{len(breaches)} breach(es)'}",
        "",
        "LARGEST CURRENT POSITIONS",
    ]
    lines.extend(
        f"- {row.ticker}: {pct(row.current_weight)} | {lkr(row.market_value)} | {row.sector}"
        for row in largest.itertuples()
    )
    lines.extend(["", "LAMVO REBALANCING — LARGEST BUYS"])
    lines.extend(
        [f"- {row.ticker}: buy {lkr(row.trade_value)} → target {pct(row.target_weight)}" for row in buys.itertuples()]
        or ["- No material buys"]
    )
    lines.extend(["", "LAMVO REBALANCING — LARGEST SELLS"])
    lines.extend(
        [f"- {row.ticker}: sell {lkr(abs(row.trade_value))} → target {pct(row.target_weight)}" for row in sells.itertuples()]
        or ["- No material sells"]
    )
    lines.extend(["", "MACRO STRESS TESTS"])
    lines.extend(
        f"- {row.scenario}: estimated portfolio impact {pct(row.estimated_portfolio_return)}"
        for row in stresses.itertuples()
    )
    lines.extend(
        [
            "",
            "T+2 LIQUIDITY",
            f"Peak buy cash lockup: {lkr(max_buy_lock)}",
            f"Peak clearing margin assumption: {lkr(max_margin)}",
            "",
            "TAX AND COST ADJUSTMENT",
            f"Gross dividend: {lkr(tax_summary.get('gross_dividend', 0.0))}",
            f"Dividend WHT: {lkr(tax_summary.get('dividend_wht', 0.0))}",
            f"Net dividend: {lkr(tax_summary.get('net_dividend', 0.0))}",
            f"Estimated transaction cost: {lkr(tax_summary.get('transaction_cost', 0.0))}",
            f"Net total return on cost: {pct(tax_summary.get('net_return_on_cost', float('nan')))}",
            "",
            "COMPLIANCE EXCEPTIONS",
        ]
    )
    if breaches.empty:
        lines.append("- No breaches under the configured rule set.")
    else:
        lines.extend(
            f"- {row.category} / {row.test}: actual {pct(row.actual)}, limit {pct(row.limit)}"
            for row in breaches.itertuples()
        )
    lines.extend(
        [
            "",
            "IMPORTANT",
            "This is a decision-support output, not investment, tax or legal advice. Stress results are sensitivity estimates, not forecasts. Compliance settings must be approved by the AMC compliance officer against the relevant SEC rules, trust deed and client mandate.",
        ]
    )
    if methodology_note:
        lines.extend(["", "METHODOLOGY NOTE", methodology_note])
    return "\n".join(lines)


def build_analysis_workbook(
    holdings: pd.DataFrame,
    allocations: pd.DataFrame,
    compliance: pd.DataFrame,
    stresses: pd.DataFrame,
    stress_contributions: pd.DataFrame,
    settlement: pd.DataFrame,
    adjusted_prices: pd.DataFrame,
    report_text: str,
) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        holdings.to_excel(writer, sheet_name="Portfolio Summary", index=False)
        allocations.to_excel(writer, sheet_name="LAMVO Allocation", index=False)
        compliance.to_excel(writer, sheet_name="Compliance", index=False)
        stresses.to_excel(writer, sheet_name="Macro Stress", index=False)
        stress_contributions.to_excel(writer, sheet_name="Stress Attribution", index=False)
        settlement.to_excel(writer, sheet_name="T2 Settlement", index=False)
        adjusted_prices.to_excel(writer, sheet_name="Adjusted Prices", index=False)
        pd.DataFrame({"report": report_text.splitlines()}).to_excel(writer, sheet_name="Text Report", index=False)
        workbook = writer.book
        for sheet in workbook.worksheets:
            sheet.freeze_panes = "A2"
            sheet.auto_filter.ref = sheet.dimensions
            sheet.sheet_view.showGridLines = False
            for cell in sheet[1]:
                cell.font = cell.font.copy(bold=True, color="FFFFFF")
                cell.fill = cell.fill.copy(fill_type="solid", fgColor="16324F")
            for column_cells in sheet.columns:
                values = [str(cell.value) if cell.value is not None else "" for cell in column_cells[:200]]
                width = min(max(max(map(len, values), default=8) + 2, 10), 34)
                sheet.column_dimensions[column_cells[0].column_letter].width = width
    return output.getvalue()
