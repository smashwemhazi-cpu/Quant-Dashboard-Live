import subprocess
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"


@pytest.mark.parametrize("script", ["install.sh", "vertex1-connect", "vertex1-update"])
def test_deploy_scripts_parse(script):
    subprocess.run(["bash", "-n", str(DEPLOY / script)], check=True)


def test_update_timer_is_installed():
    install = (DEPLOY / "install.sh").read_text()
    assert "vertex1-update.timer" in install
    assert (DEPLOY / "systemd" / "vertex1-update.timer").exists()
    assert (DEPLOY / "systemd" / "vertex1-update.service").exists()
