-- 在托管 PostgreSQL 中执行一次；应用只使用此表原子统计每日真实生成次数。
CREATE TABLE IF NOT EXISTS demo_daily_usage (
    day date PRIMARY KEY,
    used integer NOT NULL CHECK (used >= 0)
);
