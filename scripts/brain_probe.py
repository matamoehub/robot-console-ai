#!/usr/bin/env python3
"""brain_probe.py — test the robot-console-ai brain endpoints end to end.

Verifies /api/brain/stt (and optionally /api/brain/tts) against a running
console, reports the transcript, the backend_mode (mock vs command/Hailo),
and latency. Run it on the HQ Pi (or any host that can reach the console) to
confirm the Hailo Whisper path works and measure how fast it is BEFORE wiring
the robot to use it.

Usage:
  export ROBOT_BRAIN_API_TOKEN=...            # same token the console uses
  python3 scripts/brain_probe.py --wav sample.wav
  python3 scripts/brain_probe.py --wav sample.wav --loops 5      # warm latency
  python3 scripts/brain_probe.py --tts "Hello, I am Turbo"       # test TTS too
  python3 scripts/brain_probe.py --url http://127.0.0.1:8080 --wav sample.wav

If you have no sample WAV, record one on the HQ Pi:
  arecord -f S16_LE -r 16000 -c 1 -d 4 sample.wav   # speak for 4 seconds
"""

import argparse
import os
import sys
import time

try:
    import requests
except ImportError:
    print("This probe needs 'requests' (pip install requests).", file=sys.stderr)
    sys.exit(2)


def _headers(token):
    return {"Authorization": f"Bearer {token}"} if token else {}


def probe_stt(url, token, wav_path, language, loops):
    endpoint = url.rstrip("/") + "/api/brain/stt"
    print(f"\n== STT: POST {endpoint} ==")
    print(f"   wav={wav_path}  ({os.path.getsize(wav_path)} bytes)")
    times = []
    for i in range(loops):
        with open(wav_path, "rb") as f:
            files = {"audio": (os.path.basename(wav_path), f, "audio/wav")}
            data = {"language": language} if language else {}
            t0 = time.perf_counter()
            try:
                r = requests.post(endpoint, headers=_headers(token),
                                  files=files, data=data, timeout=120)
            except Exception as e:
                print(f"   [{i+1}] request failed: {e}")
                return
        dt = (time.perf_counter() - t0) * 1000
        times.append(dt)
        try:
            j = r.json()
        except Exception:
            print(f"   [{i+1}] HTTP {r.status_code} non-JSON: {r.text[:200]}")
            continue
        if r.status_code != 200 or not j.get("ok", False):
            print(f"   [{i+1}] HTTP {r.status_code} error: {j}")
            if r.status_code == 401:
                print("   -> token mismatch. Set ROBOT_BRAIN_API_TOKEN to the "
                      "console's value (or pass --token).")
            return
        mode = j.get("backend_mode", "?")
        server_ms = j.get("elapsed_ms", "?")
        print(f"   [{i+1}] round-trip {dt:7.0f} ms | server {server_ms} ms | "
              f"backend_mode={mode} | text={j.get('text','')!r}")

    if times:
        best = min(times)
        avg = sum(times) / len(times)
        print(f"   round-trip: best {best:.0f} ms, avg {avg:.0f} ms over {loops}")
        print("   NOTE: backend_mode=mock means the console is NOT running real "
              "STT — enable the Hailo backend (see --help notes below).")


def probe_tts(url, token, text):
    endpoint = url.rstrip("/") + "/api/brain/tts"
    print(f"\n== TTS: POST {endpoint} ==")
    t0 = time.perf_counter()
    try:
        r = requests.post(endpoint, headers=_headers(token),
                          json={"text": text}, timeout=60)
    except Exception as e:
        print(f"   request failed: {e}")
        return
    dt = (time.perf_counter() - t0) * 1000
    ctype = r.headers.get("content-type", "")
    if r.status_code != 200:
        print(f"   HTTP {r.status_code}: {r.text[:200]}")
        return
    if "audio" in ctype or "octet-stream" in ctype:
        out = "/tmp/brain_probe_tts.wav"
        with open(out, "wb") as f:
            f.write(r.content)
        print(f"   round-trip {dt:.0f} ms | {len(r.content)} bytes audio -> {out}")
        print(f"   play it: aplay {out}")
    else:
        print(f"   round-trip {dt:.0f} ms | content-type={ctype} | {r.text[:200]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=os.environ.get("CONSOLE_URL", "http://127.0.0.1:8080"),
                    help="console base URL (default $CONSOLE_URL or http://127.0.0.1:8080)")
    ap.add_argument("--token", default=os.environ.get("ROBOT_BRAIN_API_TOKEN", ""),
                    help="Bearer token (default $ROBOT_BRAIN_API_TOKEN)")
    ap.add_argument("--wav", help="WAV file to transcribe via /api/brain/stt")
    ap.add_argument("--language", default="en")
    ap.add_argument("--loops", type=int, default=3, help="STT repetitions (warm latency)")
    ap.add_argument("--tts", metavar="TEXT", help="also test /api/brain/tts with this text")
    args = ap.parse_args()

    print(f"console : {args.url}")
    print(f"token   : {'set' if args.token else 'MISSING (unauthenticated)'}")

    if not args.wav and not args.tts:
        print("\nNothing to do. Pass --wav sample.wav and/or --tts \"some text\".")
        return 1
    if args.wav:
        if not os.path.isfile(args.wav):
            print(f"WAV not found: {args.wav}", file=sys.stderr)
            return 1
        probe_stt(args.url, args.token, args.wav, args.language, args.loops)
    if args.tts:
        probe_tts(args.url, args.token, args.tts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
