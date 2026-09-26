"""公开演示真实生成的服务器端邀请码与每日总额度。"""

from __future__ import annotations

import hmac
import os
from datetime import datetime, timezone


class QuotaError(RuntimeError):
    """可以安全展示给访客的额度错误。"""


def live_generation_available() -> bool:
    configured = all(
        (
            os.getenv("DEMO_INVITE_CODE"),
            os.getenv("DEMO_DATABASE_URL"),
            os.getenv("DEMO_DAILY_LIMIT"),
            os.getenv("DEEPSEEK_API_KEY"),
        )
    )
    try:
        return configured and int(os.getenv("DEMO_DAILY_LIMIT", "0")) > 0
    except ValueError:
        return False


def reserve_generation(invite_code: str) -> int:
    """在共享 PostgreSQL 中原子占用一次额度；数据库不可用时拒绝请求。"""

    expected = os.getenv("DEMO_INVITE_CODE")
    database_url = os.getenv("DEMO_DATABASE_URL")
    try:
        limit = int(os.getenv("DEMO_DAILY_LIMIT", "0"))
    except ValueError as error:
        raise QuotaError("演示额度配置无效，请联系项目作者") from error
    if not expected or not database_url or limit < 1:
        raise QuotaError("真实生成暂未开放，请先体验公开样例")
    if not hmac.compare_digest(invite_code, expected):
        raise QuotaError("邀请码不正确")

    try:
        import psycopg

        with psycopg.connect(database_url, connect_timeout=5, sslmode="require") as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """INSERT INTO demo_daily_usage (day, used)
                    VALUES (%s, 1)
                    ON CONFLICT (day) DO UPDATE SET used = demo_daily_usage.used + 1
                    WHERE demo_daily_usage.used < %s
                    RETURNING used""",
                    (datetime.now(timezone.utc).date(), limit),
                )
                row = cursor.fetchone()
                if row is None:
                    raise QuotaError("今天的真实生成额度已用完，请体验公开样例")
                return int(row[0])
    except QuotaError:
        raise
    except Exception as error:
        raise QuotaError("额度服务暂不可用，请稍后再试") from error
