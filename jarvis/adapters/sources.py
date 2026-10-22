"""Data-source adapters. Agents only ever talk to these interfaces, so swapping
the demo CSVs for the real back office (MT5 Manager / cTrader / CRM / Postgres)
means writing one new adapter - no agent code changes.

Expected columns (map your system's fields onto these):
  clients.csv : client_id, name, country, segment, account_manager, signup_date,
                last_login, last_trade, equity, deposits_30d, deposits_prev_30d,
                withdrawals_30d, trades_30d, trades_prev_30d, open_tickets
  deals.csv   : deal_id, name, source, stage, value, owner, created, last_activity
  executions.csv: ts, account, symbol, side, requested_price, fill_price, pip_size,
                status, error_code, latency_ms, platform
  tasks.csv   : task_id, title, owner, due, status, priority, project
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

import pandas as pd

from jarvis.core.config import settings


_DEMO_DIR = Path(__file__).resolve().parents[1] / "data" / "samples"


class TableSource(Protocol):
    def load(self, table: str) -> pd.DataFrame: ...


class CsvSource:
    def __init__(self, root: str | None = None) -> None:
        self.root = Path(root or settings.data_dir)

    def load(self, table: str) -> pd.DataFrame:
        path = self.root / f"{table}.csv"
        if not path.exists() and self.root == _DEMO_DIR:
            from jarvis.data.make_samples import main as make_samples  # demo mode

            make_samples()
        if not path.exists():
            raise FileNotFoundError(f"no data for '{table}' at {path}")
        return pd.read_csv(path)


class PostgresSource:
    """Reads from the same Postgres the dashboard uses (DATABASE_URL).
    Table names are whitelisted - never interpolate user/AI input into SQL."""

    ALLOWED = {"clients", "deals", "executions", "tasks"}

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def load(self, table: str) -> pd.DataFrame:
        if table not in self.ALLOWED:
            raise ValueError(f"table '{table}' not allowed")
        import psycopg2

        with psycopg2.connect(self.dsn) as conn:
            return pd.read_sql_query(f"SELECT * FROM {table}", conn)  # noqa: S608 (whitelisted)


def default_source() -> TableSource:
    import os

    dsn = os.getenv("JARVIS_DATABASE_URL")
    return PostgresSource(dsn) if dsn else CsvSource()
