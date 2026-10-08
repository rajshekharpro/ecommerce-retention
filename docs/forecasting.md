# Sales forecasting

The forecast uses complete calendar months of completed-order revenue. A zero-sales month must be supplied explicitly; missing months, duplicate months, negative revenue, and fewer than 30 observed months are rejected. Six monthly predictions follow the last observed month.

Three fixed candidates are compared:

- **Seasonal naive:** revenue equals the corresponding month one year earlier, with recursive repetition beyond a year.
- **Seasonal naive with additive drift:** the year-ago corresponding month plus the difference between average revenue in the latest 12 months and the previous 12 months, clipped at zero. This estimates annual growth in dollar units and avoids extrapolating an exponential store-launch ramp. Each origin computes its drift using only its own history.
- **Ridge seasonal trend:** a regularized regression of `log(1 + revenue)` on a linear time trend plus annual sine/cosine harmonics at frequencies one and two. Time is measured in years and ridge alpha is 1. Forecasts are transformed with `exp(prediction) - 1` and floored at zero. Without a retransformation bias correction, these are point forecasts on the transformed scale rather than guaranteed conditional means.

Evaluation uses an expanding 24-month initial training window. Each subsequent origin predicts up to six months, retaining only horizons with observed actuals. Every fit has access only to months before its forecast origin. The selected model has the smallest pooled rolling-origin MAE; WAPE produces the same ranking because all candidates share the same observations. RMSE and horizon-specific errors are also exported. Recent origins contribute fewer horizons, and overlapping folds are correlated. With 45 observed months there are 21 origins and 16 observations for the six-month horizon.

The selected model refits using all observed monthly revenue and produces the next six months. Rolling errors are used for selection **and** reporting; there is no independent forecast test set, so these errors can be optimistic after selection. The exact chosen model and all comparison metrics are stored in `artifacts/reports/forecast_metrics.json`. Every origin, prediction, target, and residual is in `artifacts/reports/forecast_backtest.csv` for auditing or Tableau analysis.

The displayed interval uses the historical residual 10th and 90th percentiles for each horizon, added to the new point forecast. It is widened to contain the point forecast and clipped at zero. These are **approximate nominal 80% empirical ranges**, not guaranteed coverage or formal confidence intervals. The folds are limited and overlapping, and their selected-model residuals can understate uncertainty. Correlated monthly forecast errors mean summing the monthly bounds does not create a valid interval for six-month total sales.

The Tableau export contains separate Actual and Forecast rows. Actual rows populate `revenue` only; Forecast rows populate `forecast`, `lower`, and `upper` only. This prevents actual revenue and future estimates from being silently blended in totals. The next forecast period should always be shown as a projection. The demonstration's future synthetic transaction generation is not used as a forecast input or evaluation target.
