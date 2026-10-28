import subprocess
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"


@pytest.mark.parametrize("script", ["install.sh", "vertex1-connect", "vertex1-update", "vertex1-backup"])
def test_deploy_scripts_parse(script):
    subprocess.run(["bash", "-n", str(DEPLOY / script)], check=True)


def test_update_timer_is_installed():
    install = (DEPLOY / "install.sh").read_text()
    assert "vertex1-update.timer" in install
    assert (DEPLOY / "systemd" / "vertex1-update.timer").exists()
    assert (DEPLOY / "systemd" / "vertex1-update.service").exists()


def test_backup_timer_is_installed():
    install = (DEPLOY / "install.sh").read_text()
    assert "vertex1-backup.timer" in install and "/usr/local/bin/vertex1-backup" in install
    assert (DEPLOY / "systemd" / "vertex1-backup.timer").exists()


def _settings_writer() -> str:
    """The Python block vertex1-connect runs to save answers into the env file."""
    text = (DEPLOY / "vertex1-connect").read_text()
    start = text.index("<<'EOF'\n") + len("<<'EOF'\n")
    return text[start:text.index("\nEOF\n", start)]


@pytest.mark.parametrize("pasted", ["abcd efgh ijkl mnop",        # non-breaking spaces from the browser
                                    "​abcd efgh ijkl mnop ",            # zero-width + thin space
                                    "ABCD EFGH IJKL MNOP"])
def test_pasted_app_password_is_normalised_not_rejected(tmp_path, pasted):
    import os
    import sys

    env = tmp_path / "vertex1.env"
    env.write_text("JARVIS_IMAP_USER=me@vertex1brokers.com\n")
    out = subprocess.run([sys.executable, "-c", _settings_writer(), str(env)], capture_output=True, text=True,
                         env={**os.environ, "SET_JARVIS_IMAP_PASSWORD": pasted}, check=True).stdout
    assert "SKIPPED" not in out
    assert "JARVIS_IMAP_PASSWORD=abcdefghijklmnop" in env.read_text()


def test_normal_password_is_refused_with_a_reason(tmp_path):
    import os
    import sys

    env = tmp_path / "vertex1.env"
    env.write_text("")
    out = subprocess.run([sys.executable, "-c", _settings_writer(), str(env)], capture_output=True, text=True,
                         env={**os.environ, "SET_JARVIS_IMAP_PASSWORD": "Summer2026!"}, check=True).stdout
    assert "looks like your normal Google password" in out and "IMAP_PASSWORD" not in env.read_text()
