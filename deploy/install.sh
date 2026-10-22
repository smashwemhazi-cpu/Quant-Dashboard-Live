#!/usr/bin/env bash
# Install or upgrade Vertex 1 (JARVIS + DONNA) on Ubuntu 22.04/24.04. Run as root from the
# unpacked release folder:  sudo bash deploy/install.sh
# Safe to re-run: code is replaced, your config and databases are kept.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "run with sudo"; exit 1; }
SRC="$(cd "$(dirname "$0")/.." && pwd)"

apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip nginx >/dev/null

id vertex1 &>/dev/null || useradd --system --home /var/lib/vertex1 --shell /usr/sbin/nologin vertex1
install -d -o vertex1 -g vertex1 -m 750 /var/lib/vertex1 /var/lib/vertex1/data /var/lib/vertex1/generated /var/lib/vertex1/tradingview
install -d -o root -g vertex1 -m 750 /etc/vertex1 /opt/vertex1

# code: replace atomically, keep the previous release for rollback
rm -rf /opt/vertex1/app.new
cp -a "$SRC" /opt/vertex1/app.new
[[ -d /opt/vertex1/app ]] && { rm -rf /opt/vertex1/app.prev; mv /opt/vertex1/app /opt/vertex1/app.prev; }
mv /opt/vertex1/app.new /opt/vertex1/app
chown -R root:vertex1 /opt/vertex1/app && chmod -R g+rX,o-rwx /opt/vertex1/app

[[ -d /opt/vertex1/venv ]] || python3 -m venv /opt/vertex1/venv
/opt/vertex1/venv/bin/pip install -q --upgrade pip
/opt/vertex1/venv/bin/pip install -q -r /opt/vertex1/app/requirements-server.txt

# config templates - never overwrite real config
[[ -f /etc/vertex1/vertex1.env ]] || install -o root -g vertex1 -m 640 "$SRC/deploy/vertex1.env.example" /etc/vertex1/vertex1.env
[[ -f /etc/vertex1/secrets.toml ]] || install -o root -g vertex1 -m 640 "$SRC/deploy/secrets.toml.example" /etc/vertex1/secrets.toml
install -d -o root -g vertex1 -m 750 /opt/vertex1/app/.streamlit
ln -sf /etc/vertex1/secrets.toml /opt/vertex1/app/.streamlit/secrets.toml

cp "$SRC"/deploy/systemd/*.service /etc/systemd/system/
[[ -f /etc/nginx/sites-available/vertex1 ]] || cp "$SRC/deploy/nginx/vertex1.conf" /etc/nginx/sites-available/vertex1
systemctl daemon-reload

echo
echo "Installed to /opt/vertex1/app (previous release kept in /opt/vertex1/app.prev)."
echo "Next:"
echo "  1. Fill in /etc/vertex1/vertex1.env and /etc/vertex1/secrets.toml"
echo "  2. sudo systemctl enable --now vertex1-donna vertex1-scheduler vertex1-console"
echo "  3. Lead monitor first login (interactive, once):"
echo "     sudo -u vertex1 bash -c 'set -a; . /etc/vertex1/vertex1.env; cd /opt/vertex1/app; /opt/vertex1/venv/bin/python -m donna.lead_monitor'"
echo "     then: sudo systemctl enable --now vertex1-leads"
echo "  4. Edit /etc/nginx/sites-available/vertex1 (domains), enable it, run certbot for TLS"
echo "  5. Test: curl -s http://127.0.0.1:8080/health ; sudo -u vertex1 bash -c 'set -a; . /etc/vertex1/vertex1.env; cd /opt/vertex1/app; /opt/vertex1/venv/bin/python -m donna.channels test discord:reports'"
