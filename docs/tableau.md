# Tableau workbook

`tableau/Retention_Analytics.twbx` is the portable deliverable. It contains an editable workbook, four CSV connections, ten worksheets and two dashboards. Use **Tableau Desktop 2026.1 or newer**. The workbook uses Tableau's documented `ManifestByVersion` mechanism; an older Tableau release cannot open it.

The XML passes Tableau's official 2026.1 XSD and the project's reference/package checks. **It has not been opened or rendered in Tableau:** Tableau Desktop/Reader is unavailable in the build environment. `tableau/validation.json` records this distinction. The working offline browser dashboard is a separate implementation, not evidence of native Tableau rendering.

## Open and explore

1. Open `tableau/Retention_Analytics.twbx` in Tableau Desktop 2026.1+.
2. Select **Retention Overview**. It contains the eligible-customer count, average churn probability, revenue exposure proxy, monthly sales outlook, risk mix, signup cohort heatmap and segment exposure chart.
3. Select **Customer Worklist**. Use the **Risk Band**, **Segment** and **Acquisition Channel** filter cards. The initial risk selection is High. These filters target **Customer Priority** only; the playbook and overview remain portfolio references. Rows are sorted by revenue exposure, descending. Hover over marks for probability, recency and reason; use the horizontal/vertical scrollbars if needed.
4. Select an individual worksheet tab to edit marks, shelves, formatting, aggregation or filters. The original scored CSV is also available in `artifacts/tableau/customer_scores.csv` for analysis and campaign design.

The packaged workbook embeds CSV copies. It does not need a database login, internet connection, Python service or an external analytics extension. A `.twbx` is a ZIP container with its supporting files, as described in [Tableau's packaged workbook documentation](https://help.tableau.com/current/pro/desktop/en-us/save_savework_packagedworkbooks.htm).

## Refresh and move the project

From the project directory, run the project pipeline, then rebuild Tableau assets if you are calling the builder separately:

```powershell
python scripts/build_tableau.py
```

For a custom artifact location:

```powershell
python scripts/build_tableau.py --root C:/path/to/ecommerce-retention --output-dir artifacts
```

The builder reads `customer_scores.csv`, `sales_forecast.csv`, `cohorts.csv` and `monthly_sales.csv` from `<output-dir>/tableau/`. It copies them into `tableau/Data/`, rebuilds both workbooks, and replaces `tableau/validation.json`. The standalone `.twb` uses absolute local paths to those copied CSVs. Rebuild it after moving the project, or open the portable `.twbx`, whose internal connections use relative `Data/` paths. Packaged copies are snapshots: rebuilding is what updates their data.

Preserve a separate copy before making manual Tableau edits; rebuilding replaces the generated workbooks. For manual refresh in Tableau Desktop, use **Data → [data source] → Refresh**. Use **Edit Connection** to select the matching CSV under `tableau/Data/` if Tableau prompts to locate a file.

## Data grain and safe aggregation

| Source | Grain | Measures and aggregation |
|---|---|---|
| Customer lifecycle and churn scores | One eligible customer at the scoring snapshot | `COUNTD(customer_id)`; `AVG(churn_probability)`; `SUM(revenue_at_risk)` |
| Monthly sales and six-month outlook | One observed or forecast month | `SUM(IFNULL(revenue, forecast))`; series color separates Actual/Forecast; `MIN(lower)` and `MAX(upper)` in tooltips |
| Signup cohort purchase retention | One signup cohort × observed month index | `AVG(retention_rate)` within a single cell; `MIN(cohort_size)` in tooltip; no sum of percentages |
| Monthly completed-order revenue | One calendar month | `SUM(revenue)`; `SUM(orders)` |

These are independent sources, avoiding a join that would multiply customer, cohort or monthly rows. To combine multiple signup cohorts at the same age, calculate `SUM(retained_customers) / SUM(cohort_size)` over one row per cohort and month index. Do not average the cohort percentages if cohort sizes differ; do not sum repeated cohort sizes across ages. Do not sum monthly active-customer counts to obtain distinct people over multiple months.

All dates refer to UTC calendar days. The demonstration observes events from January 2023 through September 2026 and scores eligible customers on October 1, 2026. Only completed orders count as purchases and revenue. Customers need at least 90 days since signup and a completed purchase in the preceding 180 days to enter the scoring population. The overview customer count therefore differs from total synthetic signups.

Churn means **no completed order in the following 90 days**, not an account cancellation. Production scores are probabilities from the selected fitted Scikit-learn model. High risk begins at the validation-selected F2 threshold; Medium begins at half that threshold. The exact thresholds are saved in `artifacts/reports/metrics.json`, so a band is not a fixed 50% probability rule. Risk scores are not guaranteed outcomes.

The 90-day revenue proxy is half the preceding 180-day completed-order revenue. Exposure is that proxy multiplied by churn probability. It is a prioritization estimate, not a causal estimate of lost sales or a forecast of recoverable revenue. Recommendations are rule-based segment hypotheses; their impact would require a controlled retention experiment.

Signup cohort retention is the fraction of original signups making a completed purchase in each observed calendar month since signup. Month 0 need not be 100%; a signup does not imply a purchase. Empty future cells are unobserved, not zero retention. A customer can return after an inactive month, so the heatmap is not uninterrupted survival.

## Forecast interpretation and native Tableau forecasting

**Sales Outlook - Python Model** displays the already-computed, six-month Python forecast. The pipeline compares seasonal naive, seasonal naive with annual drift, and a regularized trend/seasonality model through rolling-origin evaluation and selects the lowest MAE. See `artifacts/reports/forecast_metrics.json` for the chosen model, errors and interval method. Bounds are approximate 80% residual ranges; they are not guaranteed calibrated coverage, and the model-selection folds are not an independent forecast test set.

The displayed Python forecast is **not** a forecast fitted by Tableau. A separate **Sales History - Tableau Forecast Ready** worksheet connects to observed monthly sales alone, with a continuous monthly date and aggregated revenue. To compare Tableau's native method:

1. Open that worksheet and choose **Analysis → Forecast → Show Forecast**.
2. In Forecast Options, use a forecast length of exactly six months and review the resulting model and intervals.
3. Use **Analysis → Forecast → Describe Forecast** to inspect the selected model. Keep this as a separate comparison worksheet rather than overwriting the evaluated Python forecast.

Tableau's native forecast uses exponential smoothing and may differ from the pipeline's chosen model. [Tableau explains its forecast method and assumptions here](https://help.tableau.com/current/pro/desktop/en-us/forecast_how_it_works.htm). The provided ready worksheet does not activate or claim to validate that native forecast automatically.

## Validation and first native review

```powershell
python scripts/build_tableau.py --validate-schema
python -m pytest tests/test_tableau.py -q
```

The optional schema check requires `lxml`. The builder otherwise uses only Python's standard library. Validation checks CSV headers, all worksheet datasource/field references, dashboard worksheet references, matching native filter targets, package paths, ZIP integrity and embedded CSV bytes. The test suite also verifies that missing columns fail explicitly and that a deliberately broken field reference is detected.

The official XSD is vendored unchanged under `tableau/schemas/` with its Apache license. Two small local namespace shims resolve the schema's locationless `user` and `xml` imports; they do not replace or relax Tableau's workbook structure. Source: [Tableau Document Schemas](https://github.com/tableau/tableau-document-schemas), `schemas/2026_1/twb_2026.1.0.xsd`. Tableau explicitly distinguishes syntactic validation from successful opening, querying and rendering in its application.

For the first native review, confirm that both dashboards load, all four CSV sources connect, cohort and monthly dates are parsed correctly, the worklist filters update its bars, and portfolio KPIs equal the corresponding CSV aggregations. Check label/scroll behavior at your display scaling and save the workbook from Tableau after that review. Native filter scope can be changed using **Apply to Worksheets**, described in [Tableau's filter documentation](https://help.tableau.com/current/pro/desktop/en-us/filtering.htm).

No workbook is published to Tableau Public/Cloud/Server by the project pipeline.
