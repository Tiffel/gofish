#!/usr/bin/env python3
"""gofish - sound-triggered fishing helper for WoW running under Proton/Wine.

Presses a key to cast, listens to WoW's own audio stream (PipeWire/PulseAudio),
and presses the same key again when the bobber splash is heard.
"""
import argparse
import random
import re
import signal
import subprocess
import sys
import time

import numpy as np

RATE = 44100
CHUNK_SEC = 0.05
CHUNK_SAMPLES = int(RATE * CHUNK_SEC)
CHUNK_BYTES = CHUNK_SAMPLES * 2  # s16le mono


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def parse_range(text):
    try:
        if "-" in text:
            lo, hi = (float(x) for x in text.split("-", 1))
        else:
            lo = hi = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid range '{text}', use e.g. 0.3-0.8")
    if lo < 0 or hi < lo:
        raise argparse.ArgumentTypeError(f"invalid range '{text}'")
    return lo, hi


def find_stream(app_name):
    """Return the sink-input index of the application's audio stream, or None."""
    try:
        out = subprocess.run(["pactl", "list", "sink-inputs"], capture_output=True,
                             text=True, check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        log(f"pactl failed: {e}")
        return None
    current = None
    for line in out.splitlines():
        m = re.match(r"^Sink Input #(\d+)", line)
        if m:
            current = m.group(1)
            continue
        m = re.match(r'\s*application\.name = "(.*)"', line)
        if m and m.group(1) == app_name and current is not None:
            return current
    return None


class Audio:
    """Reads WoW's audio stream via parec and yields RMS per chunk."""

    def __init__(self, app_name):
        self.app_name = app_name
        self.proc = None

    def start(self):
        while True:
            idx = find_stream(self.app_name)
            if idx is not None:
                break
            log(f"No audio stream for '{self.app_name}' found. Is WoW running "
                f"with sound enabled? Retrying in 3 s...")
            time.sleep(3)
        log(f"Listening to audio stream #{idx}")
        self.proc = subprocess.Popen(
            ["parec", f"--monitor-stream={idx}", "--format=s16le",
             f"--rate={RATE}", "--channels=1", "--raw", "--latency-msec=20"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def stop(self):
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None

    def read_rms(self):
        """Blocking read of one chunk; restarts parec if the stream vanished."""
        while True:
            if self.proc is None:
                self.start()
            data = self.proc.stdout.read(CHUNK_BYTES)
            if data and len(data) == CHUNK_BYTES:
                a = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
                return float(np.sqrt(np.mean(a * a)))
            log("Audio stream ended (WoW restarted?). Reconnecting...")
            self.stop()
            time.sleep(1)


def active_window_name():
    try:
        return subprocess.run(["xdotool", "getactivewindow", "getwindowname"],
                              capture_output=True, text=True, timeout=2).stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        return ""


class Bot:
    def __init__(self, args):
        self.a = args
        self.audio = Audio(args.app_name)
        self.casts = self.catches = self.timeouts = 0
        self.started = time.monotonic()
        self.trace = open(args.trace, "w") if args.trace else None

    def mark(self, text):
        if self.trace:
            self.trace.write(f"{time.monotonic() - self.started:9.2f} EVENT {text}\n")
            self.trace.flush()

    def rms(self):
        r = self.audio.read_rms()
        if self.trace:
            self.trace.write(f"{time.monotonic() - self.started:9.2f} {r:.4f}\n")
        return r

    def drain(self, seconds):
        """Keep reading audio (so nothing backs up) for the given duration."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.rms()

    def wait_for_focus(self):
        """Block until WoW is focused. Return True if we had to pause."""
        warned = False
        while active_window_name() != self.a.window:
            if not warned:
                log(f"'{self.a.window}' is not the active window - paused. "
                    f"Click into WoW to continue.")
                warned = True
            self.drain(0.5)
        if warned:
            log("WoW focused again, resuming in 1 s.")
            self.drain(1.0)
        return warned

    def press(self):
        self.wait_for_focus()
        self.mark("keypress")
        subprocess.run(["xdotool", "key", self.a.key], check=False)

    def limits_reached(self):
        if self.a.max_casts and self.casts >= self.a.max_casts:
            log(f"Reached --max-casts {self.a.max_casts}.")
            return True
        if self.a.max_minutes and time.monotonic() - self.started >= self.a.max_minutes * 60:
            log(f"Reached --max-minutes {self.a.max_minutes}.")
            return True
        return False

    def listen_for_splash(self):
        """Return (heard, waited_seconds, peak_rms)."""
        t0 = time.monotonic()
        peak = 0.0
        streak = 0
        while True:
            rms = self.rms()
            peak = max(peak, rms)
            waited = time.monotonic() - t0 + self.a.ignore
            if rms >= self.a.threshold:
                streak += 1
                if streak >= self.a.hold:
                    return True, waited, peak
            else:
                streak = 0
            if waited >= self.a.timeout:
                return False, waited, peak

    def run(self):
        self.audio.start()
        log(f"Starting in {self.a.countdown} s - click into WoW now!")
        self.drain(self.a.countdown)
        while not self.limits_reached():
            self.press()
            cast_time = time.monotonic()
            self.casts += 1
            self.drain(self.a.ignore)
            heard, waited, peak = self.listen_for_splash()
            if heard:
                self.drain(random.uniform(*self.a.react))
                if self.wait_for_focus():
                    # Bobber is gone after the pause; pressing now would cast a
                    # new line and the following "cast" would reel it in again.
                    # The bobber may still be out: wait until the full timeout
                    # since the cast has passed, so the next press casts fresh
                    # instead of clicking the old bobber.
                    remaining = cast_time + self.a.timeout - time.monotonic()
                    log(f"Cast {self.casts}: splash heard, but WoW lost focus -> "
                        f"missed, waiting {max(remaining, 0):.0f} s for the bobber "
                        f"to expire")
                    self.timeouts += 1
                    if remaining > 0:
                        self.drain(remaining)
                    self.drain(random.uniform(*self.a.recast))
                    continue
                self.press()
                self.catches += 1
                log(f"Cast {self.casts}: splash after {waited:.1f} s "
                    f"(peak {peak:.4f}) -> looted")
            else:
                self.timeouts += 1
                log(f"Cast {self.casts}: no splash within {self.a.timeout:.0f} s "
                    f"(peak {peak:.4f}) -> recasting")
            self.drain(random.uniform(*self.a.recast))

    def summary(self):
        mins = (time.monotonic() - self.started) / 60
        log(f"Done. {self.casts} casts, {self.catches} splashes, "
            f"{self.timeouts} timeouts in {mins:.1f} min.")


def calibrate(args):
    """Listen only; print loudness bars and per-event peaks. Never presses keys."""
    audio = Audio(args.app_name)
    audio.start()
    log("Calibration mode: no keys are pressed. Fish manually in WoW. Ctrl+C to stop.")
    log(f"Current threshold: {args.threshold}  ('|' marks it on the bar)")
    scale = 400
    mark = min(int(args.threshold * scale), 60)
    quiet_peak = 0.0
    event_peak = 0.0
    event_start = None
    try:
        while True:
            rms = audio.read_rms()
            n = min(int(rms * scale), 60)
            bar = "".join("#" if i < n else ("|" if i == mark else " ")
                          for i in range(max(n, mark + 1)))
            if rms >= 0.001 or event_start is not None:
                print(f"{rms:.4f} {bar}", flush=True)
            if rms >= 0.002:
                if event_start is None:
                    event_start = time.monotonic()
                    event_peak = 0.0
                event_peak = max(event_peak, rms)
            elif event_start is not None:
                dur = time.monotonic() - event_start
                hit = "ABOVE" if event_peak >= args.threshold else "below"
                log(f"Sound event: {dur:.2f} s, peak {event_peak:.4f} ({hit} threshold)")
                event_start = None
            else:
                quiet_peak = max(quiet_peak, rms)
    except KeyboardInterrupt:
        pass
    finally:
        audio.stop()
    log(f"Highest background level seen: {quiet_peak:.4f}")
    log("Pick a threshold clearly above the cast-sound peaks and clearly below "
        "the splash peaks.")


def main():
    p = argparse.ArgumentParser(description="Sound-triggered WoW fishing helper.")
    p.add_argument("--key", default="space", help="xdotool key name (default: space)")
    p.add_argument("--threshold", type=float, default=0.025,
                   help="RMS loudness that counts as splash (default: 0.025)")
    p.add_argument("--hold", type=int, default=3,
                   help="consecutive 50 ms chunks above threshold needed (default: 3)")
    p.add_argument("--ignore", type=float, default=2.0,
                   help="seconds to ignore audio after casting (default: 2.0)")
    p.add_argument("--timeout", type=float, default=32.0,
                   help="seconds to wait for a splash before recasting; must exceed the fishing channel time (default: 32)")
    p.add_argument("--react", type=parse_range, default=(0.3, 0.8),
                   help="random delay before looting, e.g. 0.3-0.8 (default)")
    p.add_argument("--recast", type=parse_range, default=(1.0, 2.0),
                   help="random delay before next cast, e.g. 1.0-2.0 (default)")
    p.add_argument("--max-casts", type=int, default=0, help="stop after N casts (0 = no limit)")
    p.add_argument("--max-minutes", type=float, default=0,
                   help="stop after M minutes (0 = no limit)")
    p.add_argument("--countdown", type=float, default=5.0,
                   help="seconds before the first cast (default: 5)")
    p.add_argument("--window", default="World of Warcraft",
                   help="exact title of the WoW window (default: 'World of Warcraft')")
    p.add_argument("--app-name", default="World of Warcraft",
                   help="application.name of WoW's audio stream (default: 'World of Warcraft')")
    p.add_argument("--trace", default="",
                   help="write every loudness value with timestamps to this file")
    p.add_argument("--calibrate", action="store_true",
                   help="only listen and show loudness, never press keys")
    args = p.parse_args()

    def on_sigterm(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, on_sigterm)

    if args.calibrate:
        calibrate(args)
        return

    bot = Bot(args)
    try:
        bot.run()
    except KeyboardInterrupt:
        print()
        log("Stopped by user.")
    finally:
        bot.audio.stop()
        bot.summary()


if __name__ == "__main__":
    sys.exit(main())
