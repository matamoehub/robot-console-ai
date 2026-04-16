import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path


DEFAULT_HAILO_APPS_DIR = Path(os.environ.get("HAILO_APPS_DIR", "/home/matamoe/hailo-apps")).expanduser()
DEFAULT_VARIANT = (os.environ.get("HAILO_STT_VARIANT", "base").strip() or "base")
DEFAULT_ARCH = (os.environ.get("HAILO_STT_ARCH", "hailo10h").strip() or "hailo10h")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Hailo speech recognition on an audio file.")
    parser.add_argument("--input", required=True, help="Path to the input audio file")
    parser.add_argument("--language", default="en", help="Language hint (currently informational)")
    parser.add_argument("--prompt", default="", help="Prompt hint (currently informational)")
    parser.add_argument("--variant", default=DEFAULT_VARIANT, help="Whisper variant to use")
    parser.add_argument("--arch", default=DEFAULT_ARCH, help="Target Hailo architecture")
    parser.add_argument("--hailo-apps-dir", default=str(DEFAULT_HAILO_APPS_DIR), help="Path to hailo-apps checkout")
    args = parser.parse_args()

    hailo_apps_dir = Path(args.hailo_apps_dir).expanduser()
    setup_script = hailo_apps_dir / "setup_env.sh"
    speech_script = hailo_apps_dir / "hailo_apps/python/standalone_apps/speech_recognition/speech_recognition.py"
    if not setup_script.exists():
        print(f"missing_setup_env:{setup_script}", file=sys.stderr)
        return 1
    if not speech_script.exists():
        print(f"missing_speech_script:{speech_script}", file=sys.stderr)
        return 1

    shell_cmd = (
        f"cd {shlex.quote(str(hailo_apps_dir))} && "
        f". {shlex.quote(str(setup_script))} >/dev/null 2>&1 && "
        f"python3 {shlex.quote(str(speech_script))} "
        f"--audio {shlex.quote(str(Path(args.input).expanduser()))} "
        f"--arch {shlex.quote(str(args.arch))} "
        f"--variant {shlex.quote(str(args.variant))}"
    )
    proc = subprocess.run(
        ["bash", "-lc", shell_cmd],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    if proc.returncode != 0:
        if stdout:
            print(stdout, file=sys.stdout)
        if stderr:
            print(stderr, file=sys.stderr)
        return proc.returncode

    # The Hailo app prints banner/log lines, separator lines, then the transcript.
    transcript = ""
    lines = [line.rstrip() for line in stdout.splitlines()]
    capture_next = False
    for line in lines:
        stripped = line.strip()
        if stripped == "-" * 50:
            if capture_next:
                capture_next = False
            else:
                capture_next = True
            continue
        if capture_next and stripped:
            transcript = stripped
            break
    if not transcript:
        # Fallback: take the last meaningful non-log line that isn't timing/status.
        candidates = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith(("Architecture:", "Variant:", "Encoder:", "Decoder:", "Initializing", "✓", "Loading:", "Transcribing", "Done.")):
                continue
            if stripped.startswith("(") and stripped.endswith(")"):
                continue
            if stripped == "-" * 50:
                continue
            candidates.append(stripped)
        if candidates:
            transcript = candidates[-1]

    if not transcript:
        if stdout:
            print(stdout, file=sys.stderr)
        return 1

    print(transcript)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
