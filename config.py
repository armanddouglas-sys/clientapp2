"""Shared constants and CSE-oriented defaults.

The regulatory defaults are deliberately editable. SEC rules, trust deeds,
client mandates and tax treatment can differ by fund and investor type.
"""

from __future__ import annotations

TRADING_DAYS = 252

GICS_SECTOR_ALIASES = {
    "BANK": "Banks",
    "BANKS": "Banks",
    "CAPITAL GOODS": "Capital Goods",
    "DIVERSIFIED FINANCIAL": "Diversified Financials",
    "DIVERSIFIED FINANCIALS": "Diversified Financials",
    "FOOD BEVERAGE & TOBACCO": "Food, Beverage & Tobacco",
    "FOOD, BEVERAGE & TOBACCO": "Food, Beverage & Tobacco",
    "MATERIAL": "Materials",
    "MATERIALS": "Materials",
    "CONSUMER SERVICES": "Consumer Services",
    "RETAILING": "Retailing",
    "UTILITIES": "Utilities",
    "ENERGY": "Energy",
    "INSURANCE": "Insurance",
    "REAL ESTATE": "Real Estate",
    "TRANSPORTATION": "Transportation",
    "TELECOMMUNICATION SERVICES": "Telecommunication Services",
    "COMMERCIAL & PROFESSIONAL SERVICES": "Commercial & Professional Services",
    "CONSUMER DURABLES & APPAREL": "Consumer Durables & Apparel",
    "HEALTH CARE EQUIPMENT & SERVICES": "Health Care Equipment & Services",
    "HOUSEHOLD & PERSONAL PRODUCTS": "Household & Personal Products",
    "SOFTWARE & SERVICES": "Software & Services",
}


# Return sensitivity for standardized local macro shocks. Values are scenario
# assumptions, not forecasts: rate = per +100 bp; FX = per +10% USD/LKR;
# inflation = per +5 percentage points.
SECTOR_STRESS_BETAS = {
    "Banks": (-0.035, -0.015, -0.025),
    "Diversified Financials": (-0.050, -0.020, -0.030),
    "Capital Goods": (-0.030, 0.015, -0.025),
    "Materials": (-0.025, 0.040, -0.015),
    "Food, Beverage & Tobacco": (-0.020, -0.030, -0.030),
    "Consumer Services": (-0.035, -0.045, -0.040),
    "Retailing": (-0.030, -0.050, -0.040),
    "Transportation": (-0.025, -0.035, -0.030),
    "Real Estate": (-0.060, -0.020, -0.030),
    "Insurance": (-0.020, 0.000, -0.020),
    "Utilities": (-0.020, -0.040, -0.025),
    "Energy": (-0.020, 0.030, -0.010),
    "Telecommunication Services": (-0.025, -0.025, -0.020),
    "Commercial & Professional Services": (-0.025, 0.010, -0.020),
    "Consumer Durables & Apparel": (-0.025, 0.045, -0.020),
    "Health Care Equipment & Services": (-0.020, -0.035, -0.025),
    "Household & Personal Products": (-0.020, -0.040, -0.030),
    "Software & Services": (-0.030, 0.050, -0.015),
    "Unknown": (-0.030, -0.015, -0.025),
}


DEFAULT_SCENARIOS = {
    "CBSL tightening": {"rate_hike_bps": 100.0, "lkr_depreciation_pct": 0.0, "inflation_shock_pp": 0.0},
    "LKR depreciation": {"rate_hike_bps": 0.0, "lkr_depreciation_pct": 10.0, "inflation_shock_pp": 0.0},
    "Inflation resurgence": {"rate_hike_bps": 0.0, "lkr_depreciation_pct": 0.0, "inflation_shock_pp": 5.0},
    "Combined domestic stress": {"rate_hike_bps": 300.0, "lkr_depreciation_pct": 15.0, "inflation_shock_pp": 5.0},
}


SOURCE_LINKS = {
    "SEC CIS Code 2022": "https://www.sec.gov.lk/wp-content/uploads/2022/06/CIS-Code-2022.pdf",
    "SEC Market Intermediaries Rules 2022": "https://www.sec.gov.lk/wp-content/uploads/2022/06/2271-09_E-Market-Intermediaries-final-2.pdf",
    "IRD Tax Chart 2025/26": "https://www.ird.gov.lk/en/publications/SitePages/tax_chart_2526.aspx?menuid=1404",
    "CSE transaction costs": "https://www.cse.lk/common/how-much-does-it-cost-to-invest-in-the-stock-market",
    "CSE Listing Rules – Further Issues": "https://cdn.cse.lk/pdf/Listing-Rules-Section-5-Further-Issues-of-Securities-of-a-Listed-Entity.pdf",
}
