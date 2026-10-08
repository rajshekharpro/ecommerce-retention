PRAGMA foreign_keys = ON;
CREATE TABLE customers (
    customer_id TEXT PRIMARY KEY,
    signup_date TEXT NOT NULL,
    acquisition_channel TEXT NOT NULL,
    country TEXT NOT NULL
);
CREATE TABLE orders (
    order_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(customer_id),
    order_date TEXT NOT NULL,
    amount REAL NOT NULL CHECK(amount >= 0),
    status TEXT NOT NULL CHECK(status IN ('completed','cancelled','refunded')),
    category TEXT NOT NULL
);
CREATE INDEX idx_orders_customer_date ON orders(customer_id, order_date);
CREATE INDEX idx_orders_status_date ON orders(status, order_date);
