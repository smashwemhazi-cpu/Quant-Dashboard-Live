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
