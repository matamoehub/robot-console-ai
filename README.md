# Robot Console AI

`robot-console-ai` is a separate local admin service for AI HAT+ 2 workloads on a Raspberry Pi 5.

It does not replace the classroom `robot-console`. Its job is to manage the extra local AI services on the HQ Pi:

- LLM service
- VLM service
- optional web UI

## What is different in this repo

- Separate project and service name: `robot-console-ai`
- Admin page controls for local AI services only
- Deploy assets for:
  - `robot-console-ai.service`
  - `hailo-ollama.service`
  - optional `open-webui.service`
- Pi bootstrap script with:
  - package install
  - SSH key generation
  - virtualenv setup
  - service installation

## Local AI services

The Admin page exposes status and start/stop/restart actions for:

- `hailo-ollama`
- `vlm-service`
- `open-webui` (optional)

The default health checks are:

- `http://127.0.0.1:8000/hailo/v1/list`
- `http://127.0.0.1:3000`

You can override the services with `AI_LOCAL_SERVICES_JSON` or simpler env vars:

```bash
HAILO_OLLAMA_SERVICE=hailo-ollama
HAILO_OLLAMA_HEALTH_URL=http://127.0.0.1:8000/hailo/v1/list
OPEN_WEBUI_SERVICE=open-webui
OPEN_WEBUI_HEALTH_URL=http://127.0.0.1:3000
```

## Raspberry Pi AI HAT+ 2 notes

This repo follows the Raspberry Pi AI documentation for AI HAT+ 2:

- install `dkms`
- install `hailo-h10-all`
- install the Hailo GenAI Debian package
- run `hailo-ollama`

Official reference:

- [Raspberry Pi AI documentation](https://www.raspberrypi.com/documentation/computers/ai.html)

Relevant official commands from that doc:

```bash
sudo apt install dkms
sudo apt install hailo-h10-all
sudo dpkg -i hailo_gen_ai_model_zoo_5.1.1_arm64.deb
hailo-ollama
curl --silent http://localhost:8000/hailo/v1/list
```

## New Pi setup

Use the bootstrap script:

```bash
cd /opt/robot/robot-console-ai
./scripts/setup_pi_ai.sh
```

It will:

- install base packages
- generate an SSH key if one does not exist
- create `/opt/robot/bin`, `/opt/robot/etc`, `/opt/robot/logs`
- create `.venv`
- install Python requirements
- install systemd units from `deploy/systemd`

## Manual setup summary

### 1. Generate SSH key

```bash
ssh-keygen -t ed25519 -C "robot-console-ai" -f ~/.ssh/id_ed25519
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/id_ed25519
cat ~/.ssh/id_ed25519.pub
```

### 2. Clone repo

```bash
cd /opt/robot
git clone git@github.com:matamoehub/robot-console-ai.git
cd /opt/robot/robot-console-ai
```

### 3. Python env

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate
```

### 4. Create env file

Start from:

```bash
cp .env.example .env
```

### 5. Install services

```bash
sudo cp deploy/systemd/robot-console-ai.service /etc/systemd/system/
sudo cp deploy/systemd/hailo-ollama.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable robot-console-ai
sudo systemctl enable hailo-ollama
```

## Service files included

- `deploy/systemd/robot-console-ai.service`
- `deploy/systemd/hailo-ollama.service`
- `deploy/systemd/open-webui.service`

## Repo layout

- `app.py` - main Flask application
- `templates/` - UI
- `static/` - frontend JS and assets
- `deploy/systemd/` - systemd units for this Pi
- `scripts/setup_pi_ai.sh` - Pi bootstrap helper
- `.env.example` - starting env file for a new host

## Current defaults

- app service name: `robot-console-ai`
- app port: `8080`
- password hash file: `/opt/robot/etc/robot-console-ai.passhash`
- default services:
  - `hailo-ollama`
  - `vlm-service`
  - `open-webui`

## Status

This repo is intended as the dedicated AI admin companion to the main classroom `robot-console`.
