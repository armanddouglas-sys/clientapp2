# CSE Portfolio Lab

An institutional-style Streamlit application for analyzing Excel-based Colombo Stock Exchange portfolios. It combines CSE-oriented data cleaning, liquidity-adjusted optimization, benchmark risk, T+2 liquidity, domestic macro stress tests, configurable compliance controls and tax-adjusted reporting.

## What it does

- Uploads a multi-sheet `.xlsx` client portfolio workbook.
- Handles long- or wide-format daily prices.
- Forward-fills missing/zero-volume price observations while preserving stale-price flags.
- Backward-adjusts price history for cash dividends (XD), rights issues (XR), scrip dividends/bonus issues and subdivisions.
- Runs a long-only Liquidity-Adjusted Mean-Variance Optimization (LAMVO).
- Enforces full investment and a target position cap equal to the lower of:
  - the configured issuer weight (10% by default); and
  - 20% of 30-day ADTV across the selected execution horizon.
- Calculates active share and tracking error against ASPI and S&P Sri Lanka 20.
- Models T+2 buy cash lockups, pending sale proceeds and an editable clearing-margin assumption.
- Runs CBSL rate, USD/LKR and inflation sensitivity scenarios.
- Checks issuer, sector, related-group, secondary-board, long-only and liquidity-capacity rules.
- Calculates dividend returns after WHT and portfolio return after local trading charges.
- Exports a client-ready text report and a complete Excel analysis pack.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

The app opens with synthetic demonstration data. Download the blank template from the sidebar, populate it, then upload it.

## Workbook schema

| Sheet | Status | Minimum columns | Notes |
| --- | --- | --- | --- |
| `Holdings` | Required | `Ticker`, `Quantity`, `Current_Price` | For full analytics also include sector, 30D ADTV, benchmark weights and dividend data. |
| `Prices` | Recommended | `Date`, `Ticker`, `Close`, `Volume` | `Bid` and `Ask` are optional. A wide sheet with `Date` plus ticker columns is accepted for close prices. |
| `CorporateActions` | Optional | `Date`, `Ticker`, `Action_Type` | Supported: cash dividend, rights issue, subdivision, scrip dividend/bonus. |
| `Benchmarks` | Recommended | `Date`, `ASPI`, `SP_SL20` | Index levels used for tracking error. |
| `Trades` | Optional | `Trade_Date`, `Ticker`, `Side`, `Gross_Value` | If omitted, the LAMVO rebalance becomes the settlement blotter. |
| `Holidays` | Optional | `Date` | Excluded from T+2 market-day calculations. |

Useful optional `Holdings` columns:

`Avg_Cost`, `Sector`, `Board`, `Related_Group`, `ADTV_30D_LKR`, `Bid`, `Ask`, `Bid_Ask_Spread_Pct`, `ASPI_Weight`, `SL20_Weight`, `Dividend_Per_Share`, `Expected_Return`, `Rate_Beta`, `FX_Beta`, `Inflation_Beta`.

Benchmark weights and return assumptions may be entered as decimals or percentage points. Corporate-action ratios use `new shares : existing shares`: a 1-for-4 rights issue is `1:4`; a 2-for-1 subdivision is `2:1`.

## LAMVO formulation

The optimizer minimizes:

\[
-\mu^T w + \gamma w^T\Sigma w + \lambda\sum_i L_i(w_i-w_{0,i})^2
\]

where:

- \(\mu\) is the annual expected-return vector;
- \(\Sigma\) is a shrunk annual covariance matrix;
- \(w_0\) is the current portfolio;
- \(L_i\) increases with the bid-ask spread and decreases with ADTV;
- \(\gamma\) is risk aversion; and
- \(\lambda\) is the liquidity penalty.

Constraints:

\[
\sum_i w_i=1,\quad w_i\ge0,\quad
w_i\le\min\left(w_{max},\frac{p\times ADTV_i\times d}{V}\right)
\]

with participation rate \(p\), execution days \(d\), and total portfolio value \(V\). If the sum of all target capacities is below 100%, the app stops the optimization and explains how to restore feasibility.

## Corporate-action adjustments

- Cash dividend: prior prices are multiplied by \((P_{t-1}-D)/P_{t-1}\).
- Rights issue: prior prices are multiplied by \(TERP/P_{t-1}\), where \(TERP=(P_{t-1}+rP_r)/(1+r)\).
- Subdivision: prior prices are divided by the new-to-old share ratio.
- Scrip/bonus: prior prices are divided by \(1+r\).

Raw volume is never forward-filled. A price carried across a missing or zero-volume day is explicitly marked stale.

## Compliance and tax configuration

The app is a configurable control layer, not a legal determination. A Sri Lankan AMC should configure the rules to the relevant SEC-licensed product, current directives, SEC-approved trust deed and client mandate. The supplied issuer, sector, related-group and secondary-board thresholds are operating defaults only.

The default 15% dividend WHT follows the [IRD 2025/26 tax chart](https://www.ird.gov.lk/en/publications/SitePages/tax_chart_2526.aspx?menuid=1404). The default 1.12% all-in transaction cost follows the [CSE investor cost page](https://www.cse.lk/common/how-much-does-it-cost-to-invest-in-the-stock-market) for equity trades up to LKR 100 million. For larger trades or investor-specific tax treatment, replace the defaults with the applicable rates.

Additional primary references:

- [SEC Collective Investment Scheme Code 2022](https://www.sec.gov.lk/wp-content/uploads/2022/06/CIS-Code-2022.pdf)
- [SEC Market Intermediaries Rules 2022](https://www.sec.gov.lk/wp-content/uploads/2022/06/2271-09_E-Market-Intermediaries-final-2.pdf)
- [CSE Listing Rules — Further Issues of Securities](https://cdn.cse.lk/pdf/Listing-Rules-Section-5-Further-Issues-of-Securities-of-a-Listed-Entity.pdf)

## Tests

The core analytics have no Streamlit dependency. Run:

```bash
python -m unittest discover -s tests -v
```

## Production notes

- Replace uploaded daily sheets with a licensed CSE market-data feed if deploying for live dealing.
- Reconcile T+2 holidays and margin parameters with the clearing member/CSE Clear.
- Store client workbooks in an encrypted, access-controlled data layer; the sample app processes uploads in memory.
- Add authentication, role-based access, audit logs and maker-checker approval before production use.
- Validate optimizer assumptions, corporate actions and tax treatment independently before executing orders.

This software is decision support only and does not constitute investment, legal, tax or compliance advice.
