# Robot Console AI

`robot-console-ai` is a separate local admin service for AI HAT+ 2 workloads on a Raspberry Pi 5.

It does not replace the classroom `robot-console`. Its job is to manage the extra local AI services on the HQ Pi:

- LLM service
- VLM service
- optional web UI

## What is different in this repo

- Separate project and service name: `robot-console-ai`
- Admin page controls for local AI services only
- Admin page can also:
  - run tests
  - git sync the repo
  - restart `robot-console-ai`
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
- `http://127.0.0.1:8090/healthz`
- `http://127.0.0.1:3000`

You can override the services with `AI_LOCAL_SERVICES_JSON` or simpler env vars:

```bash
HAILO_OLLAMA_SERVICE=hailo-ollama
HAILO_OLLAMA_HEALTH_URL=http://127.0.0.1:8000/hailo/v1/list
OPEN_WEBUI_SERVICE=open-webui
OPEN_WEBUI_HEALTH_URL=http://127.0.0.1:3000
VLM_HEALTH_URL=http://127.0.0.1:8090/healthz
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
- install `vlm-service.service`

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
sudo cp deploy/systemd/vlm-service.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable robot-console-ai
sudo systemctl enable hailo-ollama
sudo systemctl enable vlm-service
```

### 6. Update/restart helper scripts

The bootstrap script also installs:

- `/opt/robot/bin/robot-console-ai-update`
- `/opt/robot/bin/robot-console-ai-restart`

Those are what the Admin page uses for self-update and restart.

## Service files included

- `deploy/systemd/robot-console-ai.service`
- `deploy/systemd/hailo-ollama.service`
- `deploy/systemd/vlm-service.service`
- `deploy/systemd/open-webui.service`

## Repo layout

- `app.py` - main Flask application
- `templates/` - UI
- `static/` - frontend JS and assets
- `deploy/systemd/` - systemd units for this Pi
- `scripts/setup_pi_ai.sh` - Pi bootstrap helper
- `app_vlm.py` - local VLM service shim
- `.env.example` - starting env file for a new host

## Current defaults

- app service name: `robot-console-ai`
- app port: `8080`
- password hash file: `/opt/robot/etc/robot-console-ai.passhash`
- default services:
  - `hailo-ollama`
  - `vlm-service`
  - `open-webui`

## VLM service

This repo now includes a small local VLM HTTP service in `app_vlm.py`.

- health endpoint: `GET /healthz`
- model list endpoint: `GET /v1/models`
- caption endpoint: `POST /v1/caption`
- OpenAI-style chat endpoint: `POST /v1/chat/completions`

The intended backend for this service is the Hailo GenAI stack on AI HAT+ 2.

Raspberry Pi's current AI documentation says VLMs on AI HAT+ 2 should be run through Hailo's `hailo-apps` repository, while LLMs use the Hailo Ollama server. Source:

- [Raspberry Pi AI software docs](https://www.raspberrypi.com/documentation/computers/ai.html)
- [hailo-ai/hailo-apps](https://github.com/hailo-ai/hailo-apps)

The HTTP service here is a thin wrapper. Configure `VLM_BACKEND_CMD` in `.env` to point at an executable that:

- reads a JSON payload from stdin
- returns JSON like `{"text":"..."}` or plain text on stdout

For a Hailo-backed setup, point it at the included wrapper:

```bash
VLM_BACKEND_CMD=/home/matamoe/hailo-apps/venv_hailo_apps/bin/python /opt/robot/robot-console-ai/scripts/hailo_vlm_backend.py
```

By default the wrapper now uses `HAILO_VLM_BACKEND_MODE=direct`, which imports the Hailo Python VLM APIs directly from your `hailo-apps` checkout and processes a single uploaded image plus prompt.

Set:

```bash
HAILO_VLM_BACKEND_MODE=direct
HAILO_VLM_APP_DIR=/home/matamoe/hailo-apps
```

The older shell-command mode is still available if needed. In that case set `HAILO_VLM_COMMAND_TEMPLATE` to the exact Hailo app command for your installed `hailo-apps` version. The wrapper will substitute:

- `{prompt}`
- `{image_path}`
- `{model}`
- `{max_tokens}`

You can also set `HAILO_VLM_APP_DIR` if the command needs to run from inside your `hailo-apps` checkout.

Example payload fields passed to the backend:

- `prompt`
- `image_path`
- `image_base64`
- `image_mime_type`
- `model`
- `max_tokens`

Start it on the Pi with:

```bash
sudo systemctl enable vlm-service
sudo systemctl restart vlm-service
curl --silent http://127.0.0.1:8090/healthz
```

## Robot command brain

This repo can also act as the HQ-side text-to-command brain for your robots.

The intended flow is:

- `robot-console-ai` parses natural-language intent
- it uses the local Hailo LLM when rules are not enough
- it executes safe robot commands against each robot's `robot_ops_web`
- Telegram or robot-side callers can use the same machine API

The current admin page exposes a `Robot Command Brain` panel for:

- selecting a robot from the shared registry
- entering natural-language commands
- parsing the command
- executing the parsed command

There is also a dedicated page for robot and Telegram testing:

- `/admin/robot-control`

That page gives you:

- direct robot-command testing
- audio-file voice-command testing
- Telegram message simulation
- test mode preview without touching the robot
- live mode execution against a connected robot

The current executable action set is:

- robot speech
- sound off
- all stop
- robot master-mode switching
- robot LLM service start/stop
- several camera motions

The per-robot command catalogs also include the broader student library vocabularies from:

- `MataTurboPi` `student_robot_v2`
- `MataTonyPi` `student_robot_v2`
- `MataSpiderPi` `student_robot_v2`
- `MataMentorPi` `student_robot_v3`

Those catalogs are used to help the parser understand commands, even where the robot-side execution API is not yet exposed.

### Brain API

Configure a machine token:

```bash
ROBOT_BRAIN_API_TOKEN=change-me-robot-brain-token
ROBOT_REGISTRY_FILE=/opt/robot/robot-console/robots.json
ROBOT_TEXT_COMMAND_MODEL=qwen2.5-instruct:1.5b
```

Then use:

- `GET /api/brain/catalog`
- `GET /api/brain/robots`
- `POST /api/brain/parse`
- `POST /api/brain/execute`
- `POST /api/brain/voice/command`
- `POST /api/brain/telegram/ingest`

Example:

```bash
curl -s http://127.0.0.1:8080/api/brain/execute \
  -H 'Authorization: Bearer change-me-robot-brain-token' \
  -H 'Content-Type: application/json' \
  -d '{"robot_id":"Mata01","text":"Put Mata01 into lesson mode"}'
```

### Voice routing recommendation

For the current system, the most reliable split is:

- robot: capture audio, wakeword/push-to-talk, send text or audio upstream
- HQ Pi: run ASR/LLM/VLM orchestration and choose the robot command
- robot: execute the command through `robot_ops_web`

That keeps the fragile robot-side speech path as thin as possible while using the HQ Pi for the heavier, easier-to-update intelligence layer.

### Speech-to-text backend

This repo now includes a pluggable HQ-side STT path:

- admin test UI on `/admin/robot-control`
- `POST /api/admin/stt/transcribe`
- `POST /api/admin/voice/command`
- `POST /api/brain/voice/command`

Recommended env:

```bash
STT_BACKEND_CMD="python3 /opt/robot/robot-console-ai/scripts/stt_backend.py"
STT_BACKEND_MODE=mock
STT_DEFAULT_LANGUAGE=en
STT_TRANSCRIBE_TIMEOUT=90
STT_USES_HAILO=0
```

The bundled backend script supports two modes:

- `mock`: returns a fake transcript so the whole HQ voice flow can be tested immediately
- `command`: shells out to an external STT command and captures the transcript

For `command` mode, set:

```bash
STT_BACKEND_MODE=command
STT_COMMAND_TEMPLATE='your-stt-command --input {audio_path} --language {language}'
```

The backend substitutes:

- `{audio_path}`
- `{language}`
- `{prompt}`

This is the seam intended for a future Hailo Whisper wrapper on the HQ Pi.

For Hailo-10H speech recognition, prefer the more accurate `base` variant over `tiny.en` for robot voice commands. The safest setup is to route command mode through the included wrapper script:

```bash
HAILO_APPS_DIR=/home/matamoe/hailo-apps
HAILO_STT_VARIANT=base
HAILO_STT_ARCH=hailo10h
STT_BACKEND_MODE=command
STT_USES_HAILO=1
STT_COMMAND_TEMPLATE='python3 /opt/robot/robot-console-ai/scripts/hailo_stt_wrapper.py --input {audio_path} --language {language}'
```

### Telegram bot

This repo also includes a simple Telegram polling bot:

- script: `scripts/telegram_robot_bot.py`
- unit: `deploy/systemd/telegram-robot-bot.service`

Required env:

```bash
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ALLOWED_CHAT_IDS=12345,67890
TELEGRAM_DEFAULT_ROBOT_ID=Mata01
TELEGRAM_EXECUTION_MODE=live
ROBOT_BRAIN_API_TOKEN=change-me-robot-brain-token
ROBOT_BRAIN_API_BASE_URL=http://127.0.0.1:8080/api/brain
```

Install it on the Pi with:

```bash
sudo cp deploy/systemd/telegram-robot-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable telegram-robot-bot
sudo systemctl restart telegram-robot-bot
```

## Status

This repo is intended as the dedicated AI admin companion to the main classroom `robot-console`.
