"""Deterministic demonstration workbook and blank upload template."""

from __future__ import annotations

from io import BytesIO

import numpy as np
import pandas as pd


SECURITIES = [
    ("COMB.N0000", "Banks", "Main Board"),
    ("HNB.N0000", "Banks", "Main Board"),
    ("SAMP.N0000", "Banks", "Main Board"),
    ("JKH.N0000", "Capital Goods", "Main Board"),
    ("HAYL.N0000", "Capital Goods", "Main Board"),
    ("DIAL.N0000", "Telecommunication Services", "Main Board"),
    ("CARS.N0000", "Capital Goods", "Main Board"),
    ("MELS.N0000", "Food, Beverage & Tobacco", "Main Board"),
    ("SUN.N0000", "Household & Personal Products", "Main Board"),
    ("HELA.N0000", "Consumer Durables & Apparel", "Main Board"),
    ("ACL.N0000", "Capital Goods", "Main Board"),
    ("LIOC.N0000", "Energy", "Main Board"),
    ("CIC.N0000", "Materials", "Main Board"),
    ("SOFT.N0000", "Software & Services", "Diri Savi Board"),
    ("RCL.N0000", "Materials", "Main Board"),
]


def _write(sheets: dict[str, pd.DataFrame]) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=name, index=False)
            sheet = writer.book[name]
            sheet.freeze_panes = "A2"
            sheet.sheet_view.showGridLines = False
            for cell in sheet[1]:
                cell.font = cell.font.copy(bold=True, color="FFFFFF")
                cell.fill = cell.fill.copy(fill_type="solid", fgColor="16324F")
            for column_cells in sheet.columns:
                values = [str(c.value) if c.value is not None else "" for c in column_cells[:100]]
                sheet.column_dimensions[column_cells[0].column_letter].width = min(
                    max(max(map(len, values), default=8) + 2, 12), 30
                )
    return output.getvalue()


def demonstration_workbook() -> bytes:
    """Create a synthetic portfolio for UI demonstration; not market data."""
    rng = np.random.default_rng(20260717)
    dates = pd.bdate_range(end="2026-07-16", periods=260)
    price_rows: list[dict] = []
    holdings_rows: list[dict] = []
    base_prices = np.linspace(18, 285, len(SECURITIES))
    quantities = np.array([18000, 12000, 10000, 25000, 8000, 85000, 3500, 12000, 15000, 18000, 9000, 8000, 9500, 12000, 7000])
    aspi_weights = np.array([0.09, 0.07, 0.04, 0.10, 0.06, 0.07, 0.03, 0.06, 0.025, 0.015, 0.02, 0.03, 0.02, 0.005, 0.015])
    sl20_weights = np.array([0.10, 0.08, 0.05, 0.12, 0.07, 0.09, 0.03, 0.07, 0.03, 0.02, 0.02, 0.04, 0.02, 0.0, 0.01])
    market_factor = rng.normal(0.00025, 0.009, len(dates))
    stock_returns: dict[str, np.ndarray] = {}
    for i, (ticker, sector, board) in enumerate(SECURITIES):
        returns = 0.65 * market_factor + rng.normal(0.00015 + i * 0.000005, 0.010 + i * 0.00015, len(dates))
        prices = base_prices[i] * np.exp(np.cumsum(returns))
        stock_returns[ticker] = returns
        adtv = 18_000_000 + i * 9_500_000
        mean_volume = adtv / prices[-1]
        volumes = rng.lognormal(np.log(max(mean_volume, 100)), 0.55, len(dates)).astype(int)
        zero_mask = rng.random(len(dates)) < (0.02 + 0.008 * i)
        volumes[zero_mask] = 0
        observed_prices = prices.copy()
        observed_prices[zero_mask] = np.nan
        spread = 0.004 + 0.0012 * i
        for date, close, volume in zip(dates, observed_prices, volumes):
            mid = close if np.isfinite(close) else np.nan
            price_rows.append(
                {
                    "Date": date,
                    "Ticker": ticker,
                    "Close": round(mid, 2) if np.isfinite(mid) else np.nan,
                    "Volume": int(volume),
                    "Bid": round(mid * (1 - spread / 2), 2) if np.isfinite(mid) else np.nan,
                    "Ask": round(mid * (1 + spread / 2), 2) if np.isfinite(mid) else np.nan,
                }
            )
        final_price = float(prices[-1])
        holdings_rows.append(
            {
                "Ticker": ticker,
                "Quantity": int(quantities[i]),
                "Avg_Cost": round(final_price * rng.uniform(0.78, 1.08), 2),
                "Current_Price": round(final_price, 2),
                "Sector": sector,
                "Board": board,
                "Related_Group": "Illustrative Group A" if ticker in {"HAYL.N0000", "DIAL.N0000"} else "",
                "ADTV_30D_LKR": float(adtv),
                "Bid_Ask_Spread_Pct": spread,
                "ASPI_Weight": aspi_weights[i],
                "SL20_Weight": sl20_weights[i],
                "Dividend_Per_Share": round(rng.uniform(0.2, 6.0), 2),
                "Expected_Return": round(rng.uniform(0.07, 0.18), 4),
            }
        )

    aspi_returns = market_factor + rng.normal(0, 0.0015, len(dates))
    sl20_returns = market_factor * 0.9 + rng.normal(0, 0.0012, len(dates))
    benchmarks = pd.DataFrame(
        {
            "Date": dates,
            "ASPI": 17000 * np.exp(np.cumsum(aspi_returns)),
            "SP_SL20": 5100 * np.exp(np.cumsum(sl20_returns)),
        }
    )
    actions = pd.DataFrame(
        [
            {"Date": dates[-100], "Ticker": "COMB.N0000", "Action_Type": "Cash Dividend", "Cash_Dividend": 4.5},
            {"Date": dates[-80], "Ticker": "JKH.N0000", "Action_Type": "Subdivision", "Split_Ratio": "2:1"},
            {
                "Date": dates[-55],
                "Ticker": "HAYL.N0000",
                "Action_Type": "Rights Issue",
                "Rights_Ratio": "1:4",
                "Rights_Price": round(base_prices[4] * 0.8, 2),
            },
            {"Date": dates[-35], "Ticker": "SUN.N0000", "Action_Type": "Scrip Dividend", "Scrip_Ratio": "1:20"},
        ]
    )
    trades = pd.DataFrame(
        [
            {"Trade_Date": dates[-1], "Ticker": "COMB.N0000", "Side": "BUY", "Gross_Value": 1_500_000, "Fees": 16_800},
            {"Trade_Date": dates[-1], "Ticker": "SOFT.N0000", "Side": "SELL", "Gross_Value": 650_000, "Fees": 7_280},
        ]
    )
    holidays = pd.DataFrame({"Date": [pd.Timestamp("2026-07-20")], "Description": ["Illustrative market holiday"]})
    return _write(
        {
            "Holdings": pd.DataFrame(holdings_rows),
            "Prices": pd.DataFrame(price_rows),
            "CorporateActions": actions,
            "Benchmarks": benchmarks,
            "Trades": trades,
            "Holidays": holidays,
        }
    )


def upload_template() -> bytes:
    instructions = pd.DataFrame(
        {
            "Sheet": ["Holdings", "Prices", "CorporateActions", "Benchmarks", "Trades", "Holidays"],
            "Required": ["Yes", "Recommended", "Optional", "Recommended", "Optional", "Optional"],
            "Notes": [
                "Required: Ticker, Quantity, Current_Price. Include ADTV and benchmark weights for full analytics.",
                "Long format: Date, Ticker, Close, Volume; Bid and Ask optional. Wide close-price layout is also accepted.",
                "Action types: Cash Dividend, Rights Issue, Subdivision, Scrip Dividend. Ratios use new:existing.",
                "Date, ASPI, SP_SL20 index levels.",
                "Trade_Date, Ticker, Side, Gross_Value; Settlement_Date and Fees optional.",
                "Date column. Used to calculate T+2 market days.",
            ],
        }
    )
    holdings = pd.DataFrame(
        [
            {
                "Ticker": "EXAMPLE.N0000",
                "Quantity": 10000,
                "Avg_Cost": 95.0,
                "Current_Price": 100.0,
                "Sector": "Banks",
                "Board": "Main Board",
                "Related_Group": "",
                "ADTV_30D_LKR": 25_000_000,
                "Bid_Ask_Spread_Pct": 0.01,
                "ASPI_Weight": 0.03,
                "SL20_Weight": 0.04,
                "Dividend_Per_Share": 3.0,
                "Expected_Return": 0.12,
                "Rate_Beta": "",
                "FX_Beta": "",
                "Inflation_Beta": "",
            }
        ]
    )
    prices = pd.DataFrame(
        [
            {"Date": "2026-07-15", "Ticker": "EXAMPLE.N0000", "Close": 99.0, "Volume": 250000, "Bid": 98.5, "Ask": 99.5},
            {"Date": "2026-07-16", "Ticker": "EXAMPLE.N0000", "Close": 100.0, "Volume": 300000, "Bid": 99.5, "Ask": 100.5},
        ]
    )
    actions = pd.DataFrame(
        columns=["Date", "Ticker", "Action_Type", "Cash_Dividend", "Rights_Ratio", "Rights_Price", "Split_Ratio", "Scrip_Ratio"]
    )
    benchmarks = pd.DataFrame(columns=["Date", "ASPI", "SP_SL20"])
    trades = pd.DataFrame(columns=["Trade_Date", "Settlement_Date", "Ticker", "Side", "Gross_Value", "Fees"])
    holidays = pd.DataFrame(columns=["Date", "Description"])
    return _write(
        {
            "Instructions": instructions,
            "Holdings": holdings,
            "Prices": prices,
            "CorporateActions": actions,
            "Benchmarks": benchmarks,
            "Trades": trades,
            "Holidays": holidays,
        }
    )
