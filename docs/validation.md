# Verification record

The bundled demonstration uses seed 42, 5,000 fictional customers and 121,222 orders. Data collection ends exclusively at October 1, 2026. The most recent model scores cover 3,503 eligible customers.

## Automated checks

`python -m pytest -q`: **30 passed, 1 skipped**. The skipped test requires optional `lxml` for Tableau schema validation; the same official schema validation was run successfully through the standalone builder in an environment with `lxml`.

- Point-in-time feature boundaries, eligibility and fully observed label windows.
- Future order modifications leave past features unchanged.
- Invalid keys, missing foreign keys, negative amounts and chronology errors are rejected.
- SQL and Pandas reconcile shared lifecycle features, signup cohorts and monthly sales. SQL labels match Pandas on an explicit boundary fixture.
- Temporal split maturity and model feature exclusions, deterministic scoring and missing-category handling.
- Rolling-origin forecasting, seasonal drift behavior, forecast horizon and nonnegative ordered interval bounds.
- Tableau datasource/field references, dashboard targets, packaged paths and embedded CSVs.
- JavaScript syntax checked with `node --check dashboard/app.js`.

The final full pipeline completed, producing all model, report, dashboard and Tableau outputs. `artifacts/reports/data_validation.json` records agreement on 3,503 customer rows, 12 shared numeric SQL features, 1,035 cohort cells and 45 complete sales months.

## Browser checks

The dashboard was checked in a real browser at desktop and mobile breakpoints, including:

- All four navigation views.
- Acquisition channel + risk filtering: Referral and High gives 289 customers and $15,016.40 exposure, agreeing with the scored CSV.
- Customer ID search, empty results, disabled export on empty results, sorting by revenue exposure and pagination.
- Customer detail modal, behavioral reason and recommended action.
- Filtered CSV download: one searched customer produces one matching exported row.
- Cohort range selection, observed-cohort weighted rates and blank future cells.
- Sales range selection: 24 historical months plus six forecast months renders 30 points.
- Scenario slider: 25% of $165,605.45 exposure displays $41,401 after rounding.
- Updated data refresh: content-hashed data URL loads the current $3.3 million six-month forecast.
- No browser errors or warnings were reported during the interaction checks.

`docs/dashboard-preview.jpg` shows the finished browser dashboard.

## Native Tableau limitation

Both `.twb` and packaged `.twbx` XML pass the official Tableau 2026.1 XSD. Package integrity and byte-for-byte source copies pass. Tableau is not installed in this environment, so native opening, query execution, dashboard rendering and filter interactions remain unverified. Use Tableau Desktop 2026.1 or newer and follow `docs/tableau.md` for the first native review.

These checks establish the reproducible demonstration's behavior. Synthetic-data evaluation does not establish real-world accuracy or intervention impact.
