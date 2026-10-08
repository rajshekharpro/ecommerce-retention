"""Reproducible demo generation and strict input validation."""
from pathlib import Path
import json
import numpy as np
import pandas as pd

OBSERVATION_END = "2026-10-01"


def generate_demo(output_dir: Path, n_customers: int = 5000, seed: int = 42):
    if n_customers < 100:
        raise ValueError("Use at least 100 customers for a meaningful demo.")
    rng = np.random.default_rng(seed)
    start, end = pd.Timestamp("2023-01-01"), pd.Timestamp(OBSERVATION_END)
    # Increasing acquisition volume, with a full range of customer tenures.
    days = (end - start).days
    signup_offsets = (rng.beta(1.25, 1.0, n_customers) * (days - 1)).astype(int)
    channels = rng.choice(["Organic", "Paid Search", "Social", "Referral", "Email"],
                          n_customers, p=[.31, .25, .19, .15, .10])
    countries = rng.choice(["United States", "United Kingdom", "Canada", "Australia"],
                           n_customers, p=[.49, .23, .17, .11])
    customers, orders = [], []
    categories = ["Home & Living", "Beauty", "Electronics", "Fashion", "Sports"]
    prices = np.array([65, 36, 145, 72, 89])
    for i, offset in enumerate(signup_offsets):
        cid = f"C{i+1:06d}"
        signup = start + pd.Timedelta(days=int(offset))
        channel = str(channels[i])
        customers.append((cid, signup, channel, str(countries[i])))
        weekly_rate = float(np.clip(rng.gamma(1.7, .20), .035, 1.4))
        spend_factor = float(rng.lognormal(0, .45))
        category_weights = rng.dirichlet(np.ones(5) * .8)
        channel_hazard = {"Organic": .009, "Paid Search": .015, "Social": .020,
                          "Referral": .006, "Email": .008}[channel]
        # Latent engagement is never exported or used as a training feature.
        state = 0  # engaged / cooling / dormant
        week = signup
        while week < end:
            age = (week - signup).days
            draw = rng.random()
            if state == 0 and draw < channel_hazard * (1.4 if age > 240 else 1):
                state = 1
            elif state == 1:
                if draw < .10:
                    state = 2
                elif draw > .95:
                    state = 0
            elif state == 2 and draw < .012:
                state = 0
            seasonal = 1 + (.50 if week.month in [11, 12] else .10 if week.month in [6, 7] else 0)
            lifecycle = 1.20 if age < 30 else 1.0
            state_factor = [1.0, .28, .012][state]
            count = rng.poisson(weekly_rate * seasonal * lifecycle * state_factor)
            for _ in range(count):
                order_date = week + pd.Timedelta(days=int(rng.integers(0, 7)))
                if order_date >= end:
                    continue
                category = int(rng.choice(5, p=category_weights))
                amount = max(5, round(float(prices[category] * spend_factor * rng.lognormal(0, .35)), 2))
                status = str(rng.choice(["completed", "cancelled", "refunded"], p=[.93, .04, .03]))
                orders.append((f"O{len(orders)+1:08d}", cid, order_date, amount, status, categories[category]))
            week += pd.Timedelta(days=7)
    users = pd.DataFrame(customers, columns=["customer_id", "signup_date", "acquisition_channel", "country"])
    purchases = pd.DataFrame(orders, columns=["order_id", "customer_id", "order_date", "amount", "status", "category"])
    purchases = purchases.sort_values(["order_date", "order_id"]).reset_index(drop=True)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    users.to_csv(output_dir / "customers.csv", index=False, date_format="%Y-%m-%d")
    purchases.to_csv(output_dir / "orders.csv", index=False, date_format="%Y-%m-%d")
    (output_dir / "metadata.json").write_text(json.dumps({
        "synthetic": True, "seed": seed, "currency": "USD", "observation_start": str(start.date()),
        "observation_end_exclusive": OBSERVATION_END, "customers": len(users), "orders": len(purchases),
        "note": "Fictional behavior for demonstrating methods; no measured business impact."
    }, indent=2), encoding="utf-8")
    return users, purchases


def validate_data(customers: pd.DataFrame, orders: pd.DataFrame, observation_end: str):
    customers, orders = customers.copy(), orders.copy()
    schemas = [(customers, {"customer_id", "signup_date", "acquisition_channel", "country"}, "customers"),
               (orders, {"order_id", "customer_id", "order_date", "amount", "status", "category"}, "orders")]
    for frame, required, name in schemas:
        missing = required - set(frame.columns)
        if missing:
            raise ValueError(f"{name} missing columns: {sorted(missing)}")
        if frame.empty or frame[list(required)].isna().any().any():
            raise ValueError(f"{name} must be nonempty with no null required fields")
    if customers.customer_id.duplicated().any() or orders.order_id.duplicated().any():
        raise ValueError("Customer and order primary keys must be unique")
    if not orders.customer_id.isin(customers.customer_id).all():
        raise ValueError("Order references an unknown customer")
    for frame, col in [(customers, "signup_date"), (orders, "order_date")]:
        parsed = pd.to_datetime(frame[col], errors="raise", utc=True).dt.tz_localize(None)
        if not parsed.eq(parsed.dt.normalize()).all():
            raise ValueError(f"{col} must contain UTC calendar dates without times")
        frame[col] = parsed
    cutoff = pd.Timestamp(observation_end)
    if cutoff != cutoff.normalize() or cutoff.tzinfo is not None:
        raise ValueError("observation_end must be a timezone-naive UTC calendar date")
    if (customers.signup_date >= cutoff).any() or (orders.order_date >= cutoff).any():
        raise ValueError("Timestamps must precede the exclusive observation end")
    signup = customers.set_index("customer_id").signup_date
    if (orders.order_date < orders.customer_id.map(signup)).any():
        raise ValueError("Order cannot precede customer signup")
    orders["amount"] = pd.to_numeric(orders.amount, errors="raise")
    if not np.isfinite(orders.amount).all() or (orders.amount < 0).any():
        raise ValueError("Order amounts must be finite and nonnegative")
    if not orders.status.isin(["completed", "cancelled", "refunded"]).all():
        raise ValueError("Unknown order status: use completed, cancelled or refunded")
    return customers, orders


def load_data(raw_dir: Path, observation_end: str = OBSERVATION_END):
    root = Path(raw_dir)
    return validate_data(pd.read_csv(root / "customers.csv", dtype={"customer_id": str}),
                         pd.read_csv(root / "orders.csv", dtype={"customer_id": str, "order_id": str}),
                         observation_end)
