# Robot Console AI Architecture

## Purpose

`robot-console-ai` is the HQ-side AI control service for a Raspberry Pi 5 with AI HAT+ 2.

It is separate from the classroom `robot-console`. Its role is to:

- manage local AI services on the HQ Pi
- provide an admin and test UI
- expose machine APIs for LLM, VLM, STT, and robot command control
- translate natural-language commands into safe robot API requests
- provide channel and voice ingress paths such as Telegram, Slack, and uploaded audio

## Major Components

### 1. Flask Application

The main service is implemented in [app.py](/Users/john/Documents/Code/robot-console-ai/app.py).

Responsibilities:

- login/session handling for the admin UI
- admin endpoints for service control, logs, config, testing, and updates
- Hailo mode switching between LLM and VLM workloads
- robot brain endpoints for parse, execute, voice, Telegram, and Slack flows
- audit logging for robot actions

The app is intended to run as:

- `robot-console-ai.service`
- default port `8080`

### 2. Robot Brain

The robot command layer lives in [robot_brain.py](/Users/john/Documents/Code/robot-console-ai/robot_brain.py).

Responsibilities:

- define robot family catalogs and capabilities
- normalize robot types from the shared registry
- parse natural-language robot commands
- support rule-based parsing first
- fall back to local LLM parsing when rules are insufficient
- produce structured intents and multi-step command plans

The parser distinguishes between:

- directly executable actions such as `say`, `soundoff`, `allstop`, `master_mode`, camera actions, and `llm_service`
- catalog-derived commands that can be routed through remote control
- unknown commands

### 3. Local AI Service Layer

The HQ Pi hosts and coordinates several local services:

- Hailo Ollama for LLM
- local VLM wrapper service
- optional Open WebUI

Admin functionality includes:

- service health display
- start/stop/restart actions
- Hailo mode detection
- model listing and test requests

The VLM service wrapper lives in [app_vlm.py](/Users/john/Documents/Code/robot-console-ai/app_vlm.py).

### 4. Voice Command Pipeline

Voice support is HQ-side.

Flow:

1. receive uploaded or provided audio
2. normalize audio to WAV if needed
3. run the STT backend
4. parse transcript with robot brain
5. preview or execute the resulting robot command

Supporting files:

- [scripts/stt_backend.py](/Users/john/Documents/Code/robot-console-ai/scripts/stt_backend.py)
- [scripts/hailo_stt_wrapper.py](/Users/john/Documents/Code/robot-console-ai/scripts/hailo_stt_wrapper.py)

### 5. Chat / Channel Ingress

The same robot brain is exposed through multiple ingress paths.

Current paths:

- admin robot-control page
- Telegram bot
- Slack Events API webhook
- direct machine API calls

Telegram ingress uses:

- [scripts/telegram_robot_bot.py](/Users/john/Documents/Code/robot-console-ai/scripts/telegram_robot_bot.py)

Slack ingress is handled in [app.py](/Users/john/Documents/Code/robot-console-ai/app.py) and currently:

- verifies Slack signatures
- filters allowed channels
- processes `app_mention` and message events
- replies in a Slack thread

### 6. Frontend

The web UI is server-rendered with Flask templates and augmented with JavaScript.

Key files:

- [templates/base.html](/Users/john/Documents/Code/robot-console-ai/templates/base.html)
- [templates/admin.html](/Users/john/Documents/Code/robot-console-ai/templates/admin.html)
- [templates/robot_control.html](/Users/john/Documents/Code/robot-console-ai/templates/robot_control.html)
- [static/admin.js](/Users/john/Documents/Code/robot-console-ai/static/admin.js)

Main UI areas:

- service control dashboard
- Hailo mode display and switching
- LLM test panel
- VLM test panel
- robot command brain panel
- voice and Telegram simulation tools

## Execution Model

### Robot Registry

Robots are loaded from the shared registry file configured by:

- `ROBOT_REGISTRY_FILE`

Each robot entry typically contains:

- `id`
- `base_url`
- `token`
- `type`
- optional host/IP metadata

The registry is the source of truth for per-robot auth and routing.

### Natural-Language Execution Path

Standard flow:

1. caller submits text
2. app loads robot registry
3. parser resolves target robot and intent
4. if rules are insufficient, optional LLM parsing runs
5. app executes the intent against the target robot(s)
6. caller receives structured JSON or formatted chat reply
7. audit log entry is written

### Robot API Strategy

The HQ service executes only a bounded set of actions directly.

Examples:

- `POST /api/cmd/say`
- `POST /api/cmd/soundoff`
- `POST /api/cmd/allstop`
- `POST /api/admin/master-mode/activate`
- `POST /api/camera/...`

For catalog-derived commands, the HQ service can route through:

- `POST /api/remote/control`

This provides a generic bridge for commands that the parser understands but that do not yet have a dedicated HQ execution mapping.

## Logging

There are two main logging layers.

### Service Runtime Logs

Systemd routes service output to the journal for:

- `robot-console-ai`
- `vlm-service`
- optional related services

### Robot Brain Audit Log

Robot actions are appended to a dedicated audit file configured by:

- `ROBOT_BRAIN_AUDIT_LOG`

This log is intended to capture:

- parse outcomes
- execute outcomes
- voice command flows
- Telegram/Slack ingress events

Recent work has reduced payload size to avoid logging the full robot registry on every command.

## Deployment Layout

Important files:

- [deploy/systemd/robot-console-ai.service](/Users/john/Documents/Code/robot-console-ai/deploy/systemd/robot-console-ai.service)
- [deploy/systemd/hailo-ollama.service](/Users/john/Documents/Code/robot-console-ai/deploy/systemd/hailo-ollama.service)
- [deploy/systemd/vlm-service.service](/Users/john/Documents/Code/robot-console-ai/deploy/systemd/vlm-service.service)
- [deploy/systemd/open-webui.service](/Users/john/Documents/Code/robot-console-ai/deploy/systemd/open-webui.service)
- [scripts/setup_pi_ai.sh](/Users/john/Documents/Code/robot-console-ai/scripts/setup_pi_ai.sh)

Expected host layout:

- repo in `/opt/robot/robot-console-ai`
- logs in `/opt/robot/logs`
- env file in repo root unless overridden

## Current Constraints

The HQ service is only as capable as the robot-side `robot_ops_web` APIs it can call.

Known architectural constraint:

- the parser may understand more robot-family commands than the HQ executor can call directly
- in those cases, either a generic remote-control bridge is used or robot-side APIs must be added

Examples include:

- eye control
- movement control beyond the directly mapped safe actions

## Recommended Mental Model

Think of `robot-console-ai` as four systems combined:

1. an HQ AI service manager
2. a robot command parser
3. a small orchestration API
4. a bridge between human channels and robot-side web APIs

That framing matches the codebase more accurately than thinking of it as a single “chat bot” or a single “robot web app”.
