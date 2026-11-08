"""Shared test setup. The Syntellicore-based features keep their own tests, so the suite runs with the CRM switched
on; tests of the switched-off behaviour turn it off themselves (JARVIS_SYNTELLICORE=off)."""
import pytest


@pytest.fixture(autouse=True)
def _syntellicore_on(monkeypatch):
    monkeypatch.setenv("JARVIS_SYNTELLICORE", "on")
