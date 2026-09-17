# Jarvis

A voice- and text-controlled desktop agent for Windows. Say or type what you
want; it clicks, types, selects, scrolls, opens apps, manages windows, searches
the web and drives the system — typically in under 400 ms from the last syllable
to the action.

```
you: "jarvis, open chrome"          -> Chrome is focused or launched
you: "search for python decorators" -> Google opens with the query
you: "scroll down 5"                -> 5 wheel notches
you: "select 3 words"               -> selection extends
you: "type meeting notes for friday"-> pasted into the focused field
you: "volume 40"                    -> exactly 40 %
you: "shutdown"                     -> "Confirm shutdown? Say yes or no."
```

Everything runs locally. Speech recognition, wake word, matching and
text-to-speech are all offline; no audio ever leaves the machine. The optional
LLM fallback is the single exception, and it is off by default.

---

## Install

Requires Windows 10/11 and Python 3.10+.

```bat
git clone https://github.com/lakshmanan272/agent-jarvis.git
cd agent-jarvis
python -m pip install -r requirements.txt
python -m jarvis
```

The first run downloads a ~40 MB speech model to `%USERPROFILE%\.jarvis\models`.
That happens once and takes a few seconds.

Or just double-click **`run.bat`**, which creates a virtual environment,
installs dependencies and starts the agent.

---

## Using it

| Input | How |
| --- | --- |
| Voice | Say **"Jarvis"**, then your command. Follow-ups need no wake word for 12 s. |
| Voice (hands-free) | `python -m jarvis --always-on` acts on every phrase. |
| Hotkey | `Ctrl+Alt+J` wakes it without speaking a wake word. |
| Text | Type into the HUD box at the bottom right and press Enter. |
| One-shot | `python -m jarvis open chrome` runs a single command and exits. |

| Hotkey | Does |
| --- | --- |
| `Ctrl+Alt+J` | Wake / start listening |
| `Ctrl+Alt+K` | Focus the text box |
| `Ctrl+Alt+X` | Panic stop — aborts the running action and stops speaking |
| `Ctrl+Alt+M` | Mute / unmute the microphone |

Slam the mouse into a screen corner to trigger PyAutoGUI's failsafe and abort
anything in flight.

---

## What it understands

90+ intents. `python -m jarvis --list` prints the full table with examples.

**Mouse** — click, double click, right click, click at 400 300, move mouse right
200, drag to 900 500

**Keyboard** — type `<anything>`, press enter, press ctrl s, press tab 3 times,
delete 5 words, new line, question mark, spell jarvis

**Selection and editing** — select all, select 3 words, select the line, copy,
paste, cut, undo, redo, save, uppercase, title case, replace cat with dog,
join lines, trim

**Apps and windows** — open chrome, launch vscode, switch to notepad, close
chrome, minimize, maximize, snap to the left, show desktop, alt tab, task view,
move to the other monitor, what apps are open

**Web** — search for `<x>`, search cats on youtube, play lofi beats, open
youtube, go to github.com, new tab, close tab, reopen tab, tab 3, refresh, back,
find pricing on page, bookmark this page, incognito

**System** — volume 40, volume up, mute, pause, next track, screenshot, lock the
screen, shutdown, restart, sleep, what time is it, battery, system status

**Files** — create folder reports on desktop, create file notes.txt, open
downloads, delete file `<path>`, find files budget, run command `<cmd>`, what's
in the clipboard

**Meta** — help, stop, repeat, go to sleep, wake up, be quiet, how fast,
goodbye

---

## Why it's fast

Measured on a mid-range laptop, from the last syllable to the action:

| Stage | Cost |
| --- | --- |
| Audio block | 30 ms |
| Vosk incremental decode | ~40 ms, already done when you stop speaking |
| Endpoint (trailing silence) | ~250 ms |
| Normalise + route | **21 µs** (`python -m jarvis --benchmark`) |
| Actuate | 5–40 ms |

Four decisions carry most of that:

1. **Streaming recognition, not batch.** Vosk decodes as audio arrives. Whisper
   is more accurate but only starts work once you stop talking, which costs
   400–900 ms on a short command.
2. **Partial dispatch.** Short unambiguous commands — `click`, `scroll down`,
   `copy` — fire from an interim hypothesis without waiting for the silence
   timeout at all. Commands with a free-text tail (`type ...`, `search ...`)
   correctly wait for the final result.
3. **Regex routing, no model in the loop.** Intent matching is a sweep over
   compiled patterns. The LLM is a *fallback* for phrases that miss, and it only
   rewrites them into a command the router already knows.
4. **No artificial pauses.** PyAutoGUI adds 100 ms to every primitive and
   animates cursor moves by default; both are turned off. Long strings go
   through the clipboard, which is constant time instead of one keystroke per
   character.

---

## Configuration

```bat
python -m jarvis --write-config
```

writes `%USERPROFILE%\.jarvis\config.json`. Useful knobs:

```jsonc
{
  "speech": {
    "wake_words": ["jarvis", "hey jarvis"],
    "always_on": false,            // true = no wake word needed, ever
    "partial_dispatch": true,      // the sub-second path; turn off if it misfires
    "conversation_timeout_s": 12.0,
    "device": null                 // input device index, null = default mic
  },
  "voice_out": { "enabled": true, "rate": 200, "voice_hint": "" },
  "control": {
    "confirm_destructive": true,   // ask before shutdown / delete / run command
    "failsafe": true,              // mouse to a corner aborts
    "paste_threshold": 24          // text longer than this pastes instead of typing
  },
  "brain": { "enabled": false }    // the optional LLM fallback
}
```

**Teaching it new apps.** Drop a `%USERPROFILE%\.jarvis\app_aliases.json`
mapping spoken names to launch targets; it merges over the built-in list.

```json
{ "my editor": "sublime_text", "work vpn": "C:\\Program Files\\VPN\\vpn.exe" }
```

**The LLM fallback.** Off by default. When on, phrases the router can't match
are sent to a model, which rewrites them into one existing command (or answers
`UNKNOWN`). It cannot invent new abilities.

```bat
set ANTHROPIC_API_KEY=sk-ant-...
python -m jarvis --brain
```

Set `brain.provider` to `ollama` to keep even that step local.

---

## Adding a skill

Drop a module into `jarvis/skills/`; it is imported and registered at startup.

```python
from jarvis.core import actuator as act
from jarvis.skills.base import ActionResult, intent

@intent(
    r"^zoom\s+(?P<direction>in|out)(?:\s+(?P<count>\d+))?$",
    name="zoom",
    description="Zoom the focused app",
    examples=("zoom in", "zoom out 3"),
)
def do_zoom(ctx, direction="in", count=None, **_):
    for _ in range(int(count or 1)):
        act.hotkey("ctrl", "+" if direction == "in" else "-")
    return ActionResult(ok=True, say="")
```

Patterns are matched against **normalised** text: lower case, no punctuation,
filler words and the wake word already stripped, spelled-out numbers converted
to digits. Write them plainly and skip the politeness.

Return `needs_confirm="..."` instead of acting to make a command ask first; the
handler is re-invoked with `_confirmed=True` on a spoken "yes".

---

## Safety

- Destructive intents — shutdown, restart, delete, run command — require a
  spoken or typed confirmation. Set `control.confirm_destructive` to `false` to
  skip that, at your own risk.
- `Ctrl+Alt+X` and the corner failsafe both abort mid-action.
- Jarvis deafens its own microphone while speaking, so it never transcribes and
  executes its own replies.
- Repeat counts are capped (50 key presses, 200 selection steps) so a misheard
  number can't run away.

---

## Development

```bat
python -m pytest tests -q          :: 88 tests, ~0.3 s
python -m jarvis --benchmark       :: routing latency per phrase
python -m jarvis --list            :: every registered intent
python -m jarvis --no-voice --no-ui:: headless REPL, no microphone
ruff check jarvis tests
```

Tests patch the actuator module-wide, so a failing test can never type into
whatever window happens to be focused.

```
jarvis/
  core/      engine (orchestration), router (dispatch), actuator (mouse/keyboard)
  speech/    stt (Vosk streaming), tts (SAPI5)
  skills/    input_control, apps, window, web, system, files, text_edit, meta
  nlp/       matcher (normalisation, fuzzy), brain (optional LLM fallback)
  ui/        hud (always-on-top overlay + text console)
```

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| No transcripts | Check the mic isn't muted; set `speech.device` to an index from `python -c "import sounddevice;print(sounddevice.query_devices())"` |
| Model download fails | Download the zip from [alphacephei.com/vosk/models](https://alphacephei.com/vosk/models) and unzip into `%USERPROFILE%\.jarvis\models` |
| Hotkeys do nothing | Another app owns the combo, or the target window is elevated — run Jarvis as administrator |
| Commands fire twice | Set `speech.partial_dispatch` to `false` |
| It hears itself | Use headphones, or set `voice_out.enabled` to `false` |

## Licence

MIT.
