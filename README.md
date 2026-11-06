# gofish

Sound-triggered fishing helper for World of Warcraft (WoW Forever) running under
Proton/Wine on Linux.

It presses one key to cast, listens **only to WoW's own audio stream**, and
presses the same key again when the bobber splash is heard. It is meant to be
used together with a "one button fishing" addon, where the same key casts and
loots.

> Most private servers forbid bots and may ban accounts. Check the server rules;
> use at your own risk.

## How it works

1. Finds WoW's audio stream via `pactl list sink-inputs` (`application.name = "World of Warcraft"`).
2. Records only that stream with `parec --monitor-stream=<id>` (mono, 44.1 kHz). Music,
   Discord etc. from other apps are not included.
3. Computes loudness (RMS) every 50 ms.
4. Loop: press key (cast) → ignore audio for 2 s (cast sound) → wait until loudness
   exceeds the threshold for 3 chunks in a row (splash) → random 0.3–0.8 s delay →
   press key (loot) → random 1–2 s delay → cast again.
   No splash within 32 s → cast again.
5. Before every key press it checks that WoW is the active window. If not, it pauses
   (it never types into another window) and resumes when you click into WoW.
   If focus was lost between splash and loot, the catch is skipped and the bot
   waits until `--timeout` seconds have passed since that cast, so the bobber has
   expired before it casts again. Otherwise the next press would hit the old,
   still active bobber instead of casting.

## Prerequisites

- Linux with **PipeWire** (with `pipewire-pulse`) or **PulseAudio**
- An **X11 session or Wayland with XWayland** (WoW under Proton runs via XWayland).
  Tested on KDE Plasma Wayland.
- Packages (Arch Linux):

  ```fish
  sudo pacman -S --needed python python-numpy xdotool libpulse
  ```

  `libpulse` provides `pactl` and `parec`.
- WoW must stay the **focused window** while the bot runs. Keys are sent via
  xdotool (XTEST); WoW ignores keys sent to background windows.
- A one-button fishing addon in WoW, bound to the key you use (default `space`).
  It must also loot when the bobber is ready; auto-loot should be enabled. Example https://www.curseforge.com/wow/addons/forever-fishing

### WoW settings

- Sound: **Sound effects on**, **Music off**, **Ambient sounds off**.
  Turn off error speech and emote sounds if possible.
- Enable "sound in background" if available (helps during calibration).
- Fish somewhere quiet: other players, mobs, and weather add noise.
- Zoom the camera in a bit and face the water. The splash is a positional sound and gets
  quieter if the bobber is far away.

## Quick start

```fish
cd ~/develop/gofish

# 1. Check that WoW's audio stream is found (WoW must be running and have played a sound)
pactl list sink-inputs | grep -E 'Sink Input #|application.name'

# 2. Calibrate: no keys pressed, fish manually and watch the levels
python3 gofish.py --calibrate

# 3. Supervised test run: 10 casts
python3 gofish.py --max-casts 10

# 4. Normal run, stop with Ctrl+C
python3 gofish.py
```

After starting, you have 5 seconds (`--countdown`) to click into the WoW window.

## Options

| Option | Default | Meaning |
|---|---|---|
| `--key` | `space` | xdotool key name used to cast and to loot (e.g. `space`, `F5`, `ctrl+f`, `1`) |
| `--threshold` | `0.025` | RMS loudness counted as a splash |
| `--hold` | `3` | consecutive 50 ms chunks above threshold needed |
| `--ignore` | `2.0` | seconds to ignore audio right after casting (cast sound) |
| `--timeout` | `32` | seconds to wait for a splash before recasting. Must be longer than the fishing channel (bobber lifetime), otherwise the recast press hits the still-active bobber |
| `--react` | `0.3-0.8` | random delay (s) between splash and loot key press |
| `--recast` | `1.0-2.0` | random delay (s) before the next cast |
| `--max-casts` | `0` | stop after N casts (0 = unlimited) |
| `--max-minutes` | `0` | stop after M minutes (0 = unlimited) |
| `--countdown` | `5` | seconds before the first cast |
| `--window` | `World of Warcraft` | exact title of the WoW window |
| `--app-name` | `World of Warcraft` | `application.name` of WoW's audio stream |
| `--trace` | – | write every loudness value and key press to a file (for debugging) |
| `--calibrate` | off | only listen and show loudness; never press keys |

### Examples

```fish
# Use F5 as the fishing key
python3 gofish.py --key F5

# Fish for one hour, react a bit slower
python3 gofish.py --max-minutes 60 --react 0.5-1.2

# Noisy place: higher threshold, splash must last 150 ms
python3 gofish.py --threshold 0.03 --hold 3

# Start immediately (e.g. when started from a hotkey while WoW is focused)
python3 gofish.py --countdown 0
```

## Calibration (do this on every new computer)

The right threshold depends on WoW's in-game volume, the location, and the client.
System/master volume of your speakers does **not** matter, because the stream is
captured before the output volume is applied. WoW's own in-game volume sliders do matter.

1. Start WoW, go to the water, and set the sound options as described above.
2. Check that the audio stream is found:

   ```fish
   pactl list sink-inputs | grep -E 'Sink Input #|application.name'
   ```

   You should see `application.name = "World of Warcraft"`. If the name is different
   (e.g. `wine64-preloader` or `Wow.exe`), pass it with `--app-name "<name>"` to every command.
   If nothing is listed, WoW hasn't opened its audio output yet. Play a sound in game and check again.
3. Check the window title:

   ```fish
   sleep 3; xdotool getactivewindow getwindowname
   ```

   Click into WoW within 3 seconds. If it isn't exactly `World of Warcraft`, pass it
   with `--window "<title>"`.
4. Run calibration and fish **manually** 3–5 times:

   ```fish
   python3 gofish.py --calibrate
   ```

   Each sound prints a bar, followed by a summary line such as:

   ```
   [15:50:02] Sound event: 0.90 s, peak 0.0106 (below threshold)   <- cast
   [15:50:14] Sound event: 2.40 s, peak 0.1003 (ABOVE threshold)   <- splash
   ```

   Stop with Ctrl+C.
5. Choose the threshold:
   - **cast peak** = highest peak of the cast events
   - **splash peak** = lowest peak of the splash events
   - Use a value in between, preferably about **1.5–2× the cast peak** and well below
     the lowest splash peak.

   Reference values from the original setup: background `0.0000`, cast up to `0.021`,
   splash `0.07–0.10` (calibration run: casts `0.017–0.021`, splashes `0.073–0.089`) → threshold `0.04` was too high in practice (one splash peaked at only `0.042`), so the default is `0.025` with `--hold 3`.
6. Check the timings too:
   - If the cast sound lasts longer than 2 s, raise `--ignore`.
   - If short clicks cause false triggers, raise `--hold`.
7. Test with a limit and watch it:

   ```fish
   python3 gofish.py --threshold <your value> --max-casts 10
   ```

   Each cast prints a line with the peak loudness; use it to fine-tune:
   - Many `no splash ... recasting` although the bobber splashed → threshold too high
     (compare with the printed peak).
   - Loots too early, right after casting → threshold too low or `--ignore` too short.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `No audio stream for 'World of Warcraft' found` | WoW not running, or sound disabled in WoW, or a different `application.name` → see Calibration step 2 |
| `... is not the active window - paused` | Click into WoW. Check the title with `xdotool getactivewindow getwindowname` and use `--window` |
| Keys do nothing | WoW must be focused. Test: `sleep 3; xdotool key space` and click into WoW. On pure Wayland without XWayland, xdotool cannot work |
| Splash never detected | Threshold too high, sound effects off, or bobber too far away/camera too far out. Run `--calibrate` |
| Triggers on other sounds | Turn off music/ambient, move to a quieter spot, raise `--threshold` or `--hold` |
| `peak` in the cast line is lower than in `--calibrate` | Expected: the bot stops listening as soon as the splash is detected, so it shows the peak *up to detection*, not the full splash peak |
| WoW restarted | The bot reconnects to the new audio stream automatically |

## Files

- `gofish.py` – the bot (single file, no extra Python packages besides numpy)
- `README.md` – this file
