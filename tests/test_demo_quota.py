import pytest
import psycopg

from demo_quota import QuotaError, live_generation_available, reserve_generation


def test_demo_live_is_disabled_without_complete_server_config(monkeypatch) -> None:
    for name in ("DEMO_INVITE_CODE", "DEMO_DATABASE_URL", "DEMO_DAILY_LIMIT"):
        monkeypatch.delenv(name, raising=False)
    assert live_generation_available() is False
    with pytest.raises(QuotaError, match="暂未开放"):
        reserve_generation("anything")


def test_wrong_invite_never_reaches_database(monkeypatch) -> None:
    monkeypatch.setenv("DEMO_INVITE_CODE", "private-code")
    monkeypatch.setenv("DEMO_DATABASE_URL", "postgresql://invalid")
    monkeypatch.setenv("DEMO_DAILY_LIMIT", "2")
    with pytest.raises(QuotaError, match="邀请码不正确"):
        reserve_generation("wrong-code")


def test_zero_daily_limit_disables_live_generation(monkeypatch) -> None:
    monkeypatch.setenv("DEMO_INVITE_CODE", "private-code")
    monkeypatch.setenv("DEMO_DATABASE_URL", "postgresql://invalid")
    monkeypatch.setenv("DEMO_DAILY_LIMIT", "0")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-placeholder")
    assert live_generation_available() is False


def test_daily_quota_rejects_when_atomic_reservation_returns_no_row(monkeypatch) -> None:
    monkeypatch.setenv("DEMO_INVITE_CODE", "private-code")
    monkeypatch.setenv("DEMO_DATABASE_URL", "postgresql://test")
    monkeypatch.setenv("DEMO_DAILY_LIMIT", "2")

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, sql, parameters):
            assert "ON CONFLICT" in sql
            assert parameters[1] == 2

        def fetchone(self):
            return None

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def cursor(self):
            return FakeCursor()

    monkeypatch.setattr(psycopg, "connect", lambda *_args, **_kwargs: FakeConnection())
    with pytest.raises(QuotaError, match="已用完"):
        reserve_generation("private-code")
