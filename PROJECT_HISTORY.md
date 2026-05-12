# Robot Console AI Project History

## Overview

This file summarizes the repository history from the initial scaffold through the current Slack, voice, and robot-brain features.

The project evolved in roughly three stages:

1. HQ AI admin service setup
2. robot command brain and testing tools
3. voice and chat ingress with operational hardening

## Commit Timeline

### Initial HQ admin service

- `4c94a0b` Initial AI admin service scaffold
  - established the Flask service skeleton
  - created the foundation for a separate `robot-console-ai` deployment target

- `3d46919` Add AI admin update and test controls
  - added self-test and update/restart support from the admin service

- `f598a38` Add admin LLM and VLM test UI with local service wiring
  - introduced the first local AI admin testing flows

- `70a3554` Use direct Hailo VLM backend by default
  - made the VLM wrapper prefer a direct Hailo-backed execution path

- `4272341` Use dropdowns for LLM and VLM model selection
  - improved admin usability for model testing

- `20ec6d6` Show request timing in admin LLM and VLM responses
  - exposed latency/timing information in the admin test tools

- `8ab9ddc` Parse streamed admin responses into readable output
  - made streamed model responses easier to read

- `8127f46` Add Hailo-aware admin test tools
  - aligned admin testing with the AI HAT+ 2 environment

- `0a2bc5b` Add Hailo mode toggle for admin LLM and VLM tests
  - introduced explicit mode control for the Hailo resource split

- `07bc681` Highlight and gate Hailo mode in admin UI
  - prevented invalid operations when the system was in the wrong mode

- `ae79aeb` Allow WebP uploads in VLM admin form
  - broadened supported image input formats for the VLM path

- `eb7ba06` Optimize admin LLM test flow
  - refined the admin LLM interaction flow

### Robot command brain and Telegram support

- `03a9746` Add robot command brain and Telegram control
  - introduced the HQ robot brain concept
  - added Telegram-based control ingress

- `b5ac291` Add robot control page and Telegram test/live ingest
  - created a dedicated robot control interface
  - formalized preview/test versus live execution

- `59dde6d` Format service logs for readability
  - improved log readability in the UI

- `2a3a7b0` Add preview-only test robot to command executor
  - made no-risk executor testing possible

- `08832c3` Format robot control results for readability
  - improved operator-facing command results

- `48bdb97` Support multi-step robot command plans
  - allowed a single utterance to become multiple robot steps

- `9e936e4` Bump version to 1.2
  - version marker for the expanded robot brain feature set

- `d657ea8` Add preview test robots for multiple robot types
  - broadened preview coverage across robot families

- `2e20571` Show available family commands in robot control results
  - surfaced catalog visibility to users

- `084f85e` Add audit logging for robot actions and intent details
  - created the dedicated robot brain audit trail

- `123fb0e` Remove shadowed parsed payload in robot control template
  - cleaned up a template-level payload bug

- `8f9050c` Show planned commands in Telegram preview
  - made Telegram preview mode more transparent

### HQ voice pipeline

- `8ef3758` Add HQ speech-to-text voice command flow
  - added the main HQ-side voice command architecture

- `5fbca0a` Show parser input text in voice test output
  - improved debugging for voice-command parse behavior

- `c5ff480` Normalize uploaded STT audio to WAV and bump version to 1.3
  - added robust audio normalization before transcription

- `3d3c223` Prefer base variant for Hailo STT examples
  - updated recommended Hailo STT defaults

- `704cdc9` Add Hailo STT wrapper config defaults
  - formalized wrapper-related configuration

- `b889cdc` Add STT logging and route service output to journal
  - improved observability for STT operations

- `4486b01` Fix Hailo STT wrapper to run from the correct repo root
  - corrected wrapper execution context

- `eb0185f` Improve robot brain parsing for chained voice commands
  - refined rule behavior for multi-part spoken commands

- `18e7a35` Fix chained robot command parsing
  - corrected issues in multi-step command handling

### Slack integration and operational cleanup

- `4cb4ca1` Add Slack bot ingress for robot control
  - added Slack Events API ingress to the robot brain

- `729ffaf` Log Slack event ignore reasons and replies
  - improved Slack debugging and operational visibility

- `e031225` Update nav mode pill on Hailo mode changes
  - fixed stale Hailo mode indicators in the UI

- `de7b0de` Route AI robot speech through LLM remote control
  - initially changed speech routing through robot remote-control mode

- `fc400c7` Compact robot audit logs
  - reduced oversized audit payloads

- `f101957` Send say commands directly to robot API
  - corrected the speech path to use `/api/cmd/say` directly
  - removed dependence on an admin mode switch for basic speech

- `18c800e` Compact robot brain API responses
  - began trimming oversized public robot brain responses

- `81c6a49` Enable catalog-only robot commands and eye parsing
  - allowed catalog-derived commands to execute through remote control
  - added rule-based eye-command parsing for phrases like eye color changes

## Key Architectural Shifts

### Shift 1: from service admin tool to AI orchestration layer

The earliest commits were primarily about:

- service scaffolding
- local AI testing
- deployment and admin operations

The repository then expanded into a full HQ orchestration service rather than staying a thin process manager.

### Shift 2: from single commands to structured robot plans

The robot brain work moved the project from:

- simple text command handling

to:

- structured parse results
- preview versus live execution
- multi-step plans
- family-specific command catalogs

### Shift 3: from admin-only UI to multi-ingress control

With Telegram, voice, and Slack support, the same robot brain became reusable across:

- admin UI
- machine APIs
- Telegram
- Slack
- uploaded audio / STT

That is the main design pattern of the current repo.

## Current State

At the current head, the repository provides:

- HQ AI service control
- LLM and VLM admin testing
- robot command parsing and execution
- multi-step command plans
- STT-backed voice command flow
- Telegram ingress
- Slack ingress
- audit logging and operational diagnostics

The major remaining boundary is robot-side capability availability. The HQ layer can parse and route more commands than some robots may currently expose through `robot_ops_web`.
