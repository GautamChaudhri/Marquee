"""Regression coverage for durable job-platform hardening."""

from __future__ import annotations

from marquee import database


def test_engine_connect_args_apply_timeouts_only_to_asyncpg(monkeypatch):
    monkeypatch.setattr(database.settings, "DB_LOCK_TIMEOUT_MS", 10_000)
    monkeypatch.setattr(database.settings, "DB_IDLE_TXN_TIMEOUT_MS", 300_000)
    monkeypatch.setattr(database.settings, "MARQUEE_PROCESS_ROLE", "worker")

    assert database._engine_connect_args("sqlite+aiosqlite:///test.db") == {}
    assert database._engine_connect_args("postgresql+asyncpg://localhost/marquee") == {
        "server_settings": {
            "application_name": "marquee:worker",
            "lock_timeout": "10000",
            "idle_in_transaction_session_timeout": "300000",
        }
    }


