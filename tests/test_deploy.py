import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"


@pytest.mark.parametrize("script", ["install.sh", "vertex1-connect", "vertex1-update", "vertex1-backup", "vertex1-console-key"])
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


@pytest.mark.parametrize("pasted", ["\x1b[200~38863_AbCdEfGh1234567890\x1b[201~", "^[[200~38863_AbCdEfGh1234567890",
                                    " 38863_AbCd EfGh1234567890 ", "Client ID: 38863_AbCdEfGh1234567890"])
def test_pasted_ctrader_client_id_is_cleaned_not_rejected(tmp_path, pasted):
    import os
    import sys

    env = tmp_path / "vertex1.env"
    env.write_text("")
    out = subprocess.run([sys.executable, "-c", _settings_writer(), str(env)], capture_output=True, text=True,
                         env={**os.environ, "SET_DONNA_CTRADER_CLIENT_ID": pasted}, cwd=ROOT, check=True).stdout
    assert "SKIPPED" not in out
    assert "DONNA_CTRADER_CLIENT_ID=38863_AbCdEfGh1234567890" in env.read_text()


def test_rejected_client_id_says_why(tmp_path):
    import os
    import sys

    env = tmp_path / "vertex1.env"
    env.write_text("")
    out = subprocess.run([sys.executable, "-c", _settings_writer(), str(env)], capture_output=True, text=True,
                         env={**os.environ, "SET_DONNA_CTRADER_CLIENT_ID": "38863_Ab/Cd#Ef"}, cwd=ROOT, check=True).stdout
    assert "SKIPPED DONNA_CTRADER_CLIENT_ID" in out and "starting 38863_" in out and "'/'" in out


def test_auto_update_does_not_print_first_time_setup_steps():
    install = (DEPLOY / "install.sh").read_text()
    gate = install.index("[[ -z ${VERTEX1_SKIP_APT:-} ]] || exit 0")
    assert gate < install.index('echo "Next:"')
    assert "VERTEX1_SKIP_APT=1 bash" in (DEPLOY / "vertex1-update").read_text()
    assert "LogLevel=ERROR" in (DEPLOY / "windows" / "DONNA-Console.bat").read_text()


_PUB = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGq0v3Jm0x5Yb0d9yR7mQm3Vt1rS2cJdXbH1kP8uWQ7e donna-console"


def _console_key(tmp_path, stdin):
    import os

    auth = tmp_path / "ssh" / "authorized_keys"
    run = subprocess.run(["bash", str(DEPLOY / "vertex1-console-key")], input=stdin, capture_output=True, text=True,
                         env={**os.environ, "VERTEX1_AUTH_KEYS": str(auth)})
    return run, auth


def test_console_key_is_locked_to_donnas_link_only(tmp_path):
    run, auth = _console_key(tmp_path, _PUB + "\r\n")  # Windows sends CRLF
    assert run.returncode == 0, run.stderr
    line = auth.read_text().strip()
    assert line.startswith('restrict,port-forwarding,permitopen="127.0.0.1:8080",command="')
    assert "exec sleep infinity" in line and line.endswith(" donna-console")
    assert (auth.stat().st_mode & 0o777) == 0o600
    again, _ = _console_key(tmp_path, _PUB)
    assert again.returncode == 0 and "already" in again.stdout
    assert len(auth.read_text().splitlines()) == 1


def test_console_key_keeps_existing_keys_and_refuses_junk(tmp_path):
    auth = tmp_path / "ssh" / "authorized_keys"
    auth.parent.mkdir()
    auth.write_text("ssh-rsa AAAAB3Nza existing")  # no trailing newline
    run, _ = _console_key(tmp_path, _PUB)
    lines = auth.read_text().splitlines()
    assert run.returncode == 0 and lines[0] == "ssh-rsa AAAAB3Nza existing" and lines[1].startswith("restrict,")
    for junk in ["", "ssh-rsa AAAAB3Nza x", 'ssh-ed25519 AAAA" ,command="sh"', "ssh-ed25519 AAAA donna;rm"]:
        bad, _ = _console_key(tmp_path, junk)
        assert bad.returncode == 2
    assert len(auth.read_text().splitlines()) == 2
    import os   # the console sends the key as arguments (no stdin needed for the password prompt)
    other = _PUB.replace("AAAAIGq0", "AAAAIGq1").split()
    run = subprocess.run(["bash", str(DEPLOY / "vertex1-console-key"), *other], capture_output=True, text=True,
                         env={**os.environ, "VERTEX1_AUTH_KEYS": str(auth)})
    assert run.returncode == 0 and len(auth.read_text().splitlines()) == 3


def test_console_runs_hidden_and_reconnects_with_its_own_key():
    bat = (DEPLOY / "windows" / "DONNA-Console.bat").read_bytes()
    assert b"\r\n" in bat and b"\n" not in bat.replace(b"\r\n", b"")  # CRLF so cmd's goto/labels are reliable
    text = bat.decode()
    for label in ["link", "boot", "stop", "setup", "open", "password", "linked", "launch_hidden", "kill_link"]:
        assert f"\r\n:{label}\r\n" in text
    assert "vertex1-console-key" in text and "BatchMode=yes" in text and "IdentitiesOnly=yes" in text
    assert "donna_console_ed25519" in text and "-WindowStyle Hidden" in text and "goto again" in text
    assert "vertex1-console-key" in (DEPLOY / "install.sh").read_text()


def test_desktop_icon_opens_the_console_with_no_window():
    assert not (DEPLOY / "windows" / "DONNA.vbs").exists()     # Defender flags script launchers: none shipped
    bat = (DEPLOY / "windows" / "DONNA-Console.bat").read_bytes().decode()
    for label in ["quiet", "quiet_open", "failed", "startup_shortcut", "desktop_shortcut"]:
        assert f"\r\n:{label}\r\n" in bat
    assert 'if /i "%~1"=="quiet" goto quiet' in bat and "conhost.exe" in bat and "'--headless '" in bat
    assert "' quiet'" in bat and "' boot'" in bat and "wscript.exe" not in bat.lower()
    quiet = bat[bat.index("\r\n:quiet\r\n"):bat.index("\r\n:boot\r\n")]
    assert "start \"\" powershell" not in quiet and "choice" not in quiet   # nothing that would pop a window
