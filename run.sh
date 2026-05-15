#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  source .venv/bin/activate
  pip install -U pip wheel
  pip install -r requirements.txt
else
  source .venv/bin/activate
fi
mkdir -p static/vendor/bootstrap
[ -f /usr/share/javascript/bootstrap5/bootstrap.min.css ] && cp /usr/share/javascript/bootstrap5/bootstrap.min.css static/vendor/bootstrap/
[ -f /usr/share/javascript/bootstrap5/bootstrap.bundle.min.js ] && cp /usr/share/javascript/bootstrap5/bootstrap.bundle.min.js static/vendor/bootstrap/
exec gunicorn -w 1 --threads 4 -b 0.0.0.0:8080 --timeout 180 --access-logfile - "app:APP"
