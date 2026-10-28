import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"


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
                                    "\u200babcd efgh ijkl mnop ",            # zero-width + thin space
                                    "ABCD EFGH IJKL MNOP"])
def test_pasted_app_password_is_normalised_not_rejected(tmp_path, pasted):
    import os
    import sys

    env = tmp_path / "vertex1.env"
    env.write_text("JARVIS_IMAP_USER=me@vertex1brokers.com\n")
    out = subprocess.run([sys.executable, "-c", _settings_writer(), str(env)], capture_output=True, text=True,
                         env={**os.environ, "SET_JARVIS_IMAP_PASSWORD": pasted, "VERTEX1_MX": "aspmx.l.google.com"},
                         cwd=ROOT, check=True).stdout
    assert "SKIPPED" not in out
    assert "JARVIS_IMAP_PASSWORD=abcdefghijklmnop" in env.read_text()


def test_normal_password_is_refused_with_a_reason(tmp_path):
    import os
    import sys

    env = tmp_path / "vertex1.env"
    env.write_text("")
    out = subprocess.run([sys.executable, "-c", _settings_writer(), str(env)], capture_output=True, text=True,
                         env={**os.environ, "SET_JARVIS_IMAP_PASSWORD": "Summer2026!"}, cwd=ROOT, check=True).stdout
    assert "looks like your normal Google password" in out and "IMAP_PASSWORD" not in env.read_text()


def test_mailbox_not_at_google_gets_its_own_server_and_password(tmp_path):
    """A Google login on a company address whose mail lives elsewhere: imap.gmail.com can never work."""
    import os
    import sys

    env = tmp_path / "vertex1.env"
    env.write_text("JARVIS_IMAP_HOST=imap.gmail.com\nJARVIS_IMAP_USER=me@vertex1brokers.com\n")
    out = subprocess.run([sys.executable, "-c", _settings_writer(), str(env)], capture_output=True, text=True,
                         env={**os.environ, "SET_JARVIS_IMAP_PASSWORD": "Webmail-Pass9", "VERTEX1_MX": "mx1.titan.email"},
                         cwd=ROOT, check=True).stdout
    saved = env.read_text()
    assert "handled by Titan Email" in out and "SKIPPED" not in out
    assert "JARVIS_IMAP_HOST=imap.titan.email" in saved and "JARVIS_IMAP_PASSWORD=Webmail-Pass9" in saved


@pytest.mark.parametrize("mx,kind,host", [
    (["aspmx.l.google.com", "alt1.aspmx.l.google.com"], "google", "imap.gmail.com"),
    (["vertex1brokers-com.mail.protection.outlook.com"], "microsoft", "outlook.office365.com"),
    (["mx.zoho.eu"], "mailbox", "imap.zoho.eu"),
    (["mail.vertex1brokers.com"], "unknown", None),
    ([], "none", None),
])
def test_mail_provider_from_mx(mx, kind, host):
    from jarvis.core import mailhost

    m = mailhost.classify("Simba@Vertex1Brokers.com", mx)
    assert (m["kind"], m["imap_host"], m["domain"]) == (kind, host, "vertex1brokers.com")


def test_update_reinstalls_when_old_update_file_overwrote_code():
    text = (DEPLOY / "vertex1-update").read_text()
    assert 'tree_sum > "$WORK/installed_sum"' in text and "DRIFT=1" in text


@pytest.mark.parametrize("url", ["https://script.google.com/macros/s/AKfy-c_1/exec",
                                 "https://script.google.com/a/macros/vertex1brokers.com/s/AKfy-c_1/exec"])
def test_google_bridge_link_and_key_are_saved(tmp_path, url):
    import os
    import sys

    env = tmp_path / "vertex1.env"
    env.write_text("")
    out = subprocess.run([sys.executable, "-c", _settings_writer(), str(env)], capture_output=True, text=True,
                         env={**os.environ, "SET_JARVIS_GOOGLE_BRIDGE_URL": url,
                              "SET_JARVIS_GOOGLE_BRIDGE_KEY": "ab" * 32}, cwd=ROOT, check=True).stdout
    assert "SKIPPED" not in out
    saved = env.read_text()
    assert "JARVIS_GOOGLE_BRIDGE_URL=" + url in saved
    assert "JARVIS_GOOGLE_BRIDGE_KEY=" + "ab" * 32 in saved
