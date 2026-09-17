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
| Text | Click the orb, type into the bar that opens, press Enter. |
| One-shot | `python -m jarvis open chrome` runs a single command and exits. |

### The orb

Double-click **`run.bat`** and a small glowing reactor orb docks to the edge of
your screen — a real circle, not a square window, because Windows lets a layered
window key one colour out to fully transparent. It is always on top of whatever
you are working in, and its colour is the agent's state: dim teal idle, bright
cyan listening, violet acting, green speaking, red muted.

| On the orb | Does |
| --- | --- |
| Click | Opens the command bar; click again to collapse it |
| Drag | Moves the orb; the bar follows |
| Right-click | Menu: open console, mute/unmute, exit |

The bar has a command box with a microphone button beside it — the two ways
of giving a command sit together rather than in opposite corners — and a send
arrow. Up and Down walk back through what you have typed. Its header carries
two more buttons:

**🎙 voice on / 🔇 voice off** cuts the microphone. Off really is off — the
recogniser stops consuming audio rather than transcribing it and discarding the
result, so nothing appears on screen and no CPU is spent decoding speech nobody
asked for. It is labelled with the current state rather than the action, so a
glance answers "is the mic live?", and it shares one flag with `Ctrl+Alt+M` and
the orb menu so the three can never disagree.

**■ end** abandons whatever is running and leaves Jarvis ready for the next
command: a chain drops its remaining steps, a pending confirmation is forgotten,
and anything queued to be spoken is dropped. Same as `Ctrl+Alt+X`, where you can
find it without knowing the shortcut.

The bar shows what was heard, what happened, and how long it took, with a text
box for typing commands. It also pops open by itself whenever a command actually
succeeds, so a spoken command still gives you something to look at. It stays
shut for failed voice commands, which are usually the mic mishearing a stray
noise as a phrase.

| Hotkey | Does |
| --- | --- |
| `Ctrl+Alt+J` | Wake / start listening |
| `Ctrl+Alt+K` | Focus the text box |
| `Ctrl+Alt+X` | End — aborts the running action and stops speaking |
| `Ctrl+Alt+M` | Mute / unmute the microphone |

Slam the mouse into a screen corner to trigger PyAutoGUI's failsafe and abort
anything in flight.

**Jarvis hears your speakers, not just you.** With a video playing, its
dialogue reaches the microphone. So the recogniser does not run until it is
addressed: an acoustic wake-word detector listens for **"hey Jarvis"** at a
fortieth of one CPU core (RTF 0.025, against 0.38 for the recogniser it gates),
and only then is any audio transcribed. A film is never turned into text at
all, so it can never be mistaken for a command.

Once awake there is a short follow-up window, during which speech that does not
clearly match a command is ignored in silence rather than answered with "I
don't know how to ...". Say "Jarvis" and you always get a reply, even if the
reply is that it cannot. Set `speech.wake_engine` to `"none"` to go back to
matching the wake word in the transcript.

---

## What it understands

90+ intents. `python -m jarvis --list` prints the full table with examples.

**Mouse** — click, double click, right click, click at 400 300, move mouse right
200, drag to 900 500

**Clicking by name** — choose AD JAYANTAN, click Sign in, select Guest mode.
Names something you can see rather than a coordinate. It asks Windows'
accessibility tree first, which is exact and fast and covers Explorer, Settings
and most native software; where an app publishes nothing useful — Chrome's
profile picker offers nine elements, none of them the profiles — it falls back
to Windows' own offline OCR, which sees whatever is drawn. Jarvis's own orb and
bar are excluded from the search, or "click open settings" would find the bar
quoting you back and click that. It refuses rather than guesses: a label that
isn't on screen gets "I can't see that", not a click somewhere arbitrary.

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

`play <x>` plays it. There is no URL that opens YouTube's top result directly,
so the search page is fetched and its first video id read out of the inline
JSON, then that video is opened. It costs a round trip to YouTube (~2 s, mostly
their response time) and falls back to the results page if the id can't be
found.

**System** — volume 40, volume up, mute, pause, next track, screenshot, lock the
screen, shutdown, restart, sleep, what time is it, battery, system status

**Files** — create folder reports on desktop, create file notes.txt, open
downloads, delete file `<path>`, find files budget, run command `<cmd>`, what's
in the clipboard

**Meta** — help, stop, repeat, go to sleep, wake up, be quiet, how fast,
goodbye

### Chaining

Several commands in one breath. Jarvis runs them in order and stops at the first
failure, so a dependent step never runs against the wrong window.

```
"open notepad and type hello lakshmanan welcome"
"open chrome then go to youtube"
"select all and copy and press enter"
"minimize and then open spotify"
```

"and" is only a separator when a command verb follows it, so payloads survive:
`search for cats and dogs` stays one search, and `type fish and chips and open
chrome` splits only before `open`. After a step launches an app, the chain waits
for that window to exist before running the next one — otherwise the typing
would land in whatever was focused while the app was still starting.

---

## Why it's fast

Measured on a mid-range laptop, from the last syllable to the action:

| Stage | Cost |
| --- | --- |
| Audio block | 30 ms |
| Vosk incremental decode | ~40 ms, already done when you stop speaking |
| Endpoint (trailing silence) | **180 ms**, our own, not Vosk's |
| Normalise + route | **~30 µs** (`python -m jarvis --benchmark`) |
| Actuate — typing a sentence | **~44 ms**, one clipboard paste |

Five decisions carry most of that:

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
4. **Text is pasted, not typed.** Per-character typing costs a syscall and a
   scheduler slot per key — 430 ms for a sentence at the 10 ms interval slow
   apps need. A clipboard paste is one event the app reads in full: 44 ms
   regardless of length, and exact regardless of how the target drains its
   input queue. It handles Tamil, emoji and symbols that keystroke injection
   mangles, and the previous clipboard contents are handed back afterwards
   (and left alone if the user copied something in the meantime).

   Batched `SendInput` is faster still — a whole sentence in ~60 µs — and it is
   what single characters use, but it cannot be the default. Injected Unicode
   arrives as `VK_PACKET`, and apps that resolve those against the *current*
   keyboard state rather than per message mistype the tail of anything sent
   faster than they drain. The Windows 11 Notepad turns "hello lakshmanan
   welcome" into "hello lakshmanan eeeeeee", and needs 20 ms per character —
   540 ms — before it is reliable. Correct at 44 ms beats wrong at 60 µs.
5. **We decide when you stopped talking.** Vosk's endpointer is tuned for
   dictation and waits out a long pause; for commands that pause *is* the
   latency, because the words are already decoded. Jarvis watches the signal
   level itself — with an adaptive noise floor, so a café and a bedroom both
   work — and finalises after 180 ms of quiet (`speech.endpoint_silence_ms`).

6. **No artificial pauses.** PyAutoGUI adds 100 ms to every primitive and
   animates cursor moves by default; both are turned off.

---

## Configuration

```bat
python -m jarvis --write-config
```

writes `%USERPROFILE%\.jarvis\config.json`. Useful knobs:

```jsonc
{
  "speech": {
    "accuracy": "fast",            // "accurate" mishears far less; ~128 MB
    "wake_words": ["jarvis", "hey jarvis"],
    "always_on": false,            // true = no wake word needed, ever
    "partial_dispatch": true,      // the sub-second path; turn off if it misfires
    "conversation_timeout_s": 12.0,
    "endpoint_silence_ms": 180,    // quiet that ends an utterance; raise if cut off
    "device": null                 // input device index, null = default mic
  },
  "voice_out": { "enabled": true, "rate": 200, "voice_hint": "" },
  "control": {
    "confirm_destructive": true,   // ask before shutdown / delete / run command
    "failsafe": true,              // mouse to a corner aborts
    "paste_threshold": 12,         // text longer than this pastes instead of typing
    "type_interval_s": 0.01        // 0 drops keystrokes in Electron/WebView apps
  },
  "brain": { "enabled": false }    // the optional LLM fallback
}
```

**Hearing you better.** `speech.accuracy` is `"fast"` (a ~40 MB model that
loads in half a second) or `"accurate"` (a ~128 MB wider-graph model that
mishears far less with background noise or an accent). Switching re-downloads
once; both can sit on disk together. `speech.model_url` overrides it with any
Vosk model archive.

**Teaching it new apps.** Drop a `%USERPROFILE%\.jarvis\app_aliases.json`
mapping spoken names to launch targets; it merges over the built-in list.

```json
{ "my editor": "sublime_text", "work vpn": "C:\\Program Files\\VPN\\vpn.exe" }
```

**The LLM fallback.** Off by default. When on, phrases the router can't match
are sent to a model, which rewrites them into one existing command (or answers
`UNKNOWN`). It cannot invent new abilities, and commands the router already
knows never reach it — those still resolve in microseconds with no network.

The catalog sent with each request is ranked by similarity to what was said and
cut to the closest 18 commands. Sending all 91 costs ~1100 tokens a call, which
exhausts a typical free tier in about seven commands; the shortlist is 4.3x
smaller and is also a better prompt.

`provider` is `"anthropic"`, `"ollama"`, or `"openai"` — the last covers Groq,
OpenRouter, Together, Gemini's compatibility endpoint and a local llama.cpp
server, which differ only in `base_url` and model name.

```jsonc
"brain": {
  "enabled": true,
  "provider": "openai",
  "base_url": "https://api.groq.com/openai/v1",
  "model": "qwen/qwen3.8-27b",
  "api_key_env": "GROQ_API_KEY"     // or "api_key", in this file only
}
```

Measured against Groq: commands the router knows, 0.1 ms and no network;
phrases only the planner can handle, 9 of 10, median 893 ms. The tenth turned
the volume the wrong way — a planner that acts on a misreading is worse than
one that declines, and that is not solved.

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

If a captured group is literal content the user dictated — text to type, a
search query, a filename — pass `verbatim=True`. The router then re-extracts the
groups from the original phrasing, so `type Just Do It` types `Just Do It`
rather than the normaliser's `do it`. Control words stay non-verbatim, because
they *want* the normalising (`press tab three times` → `3`).

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
- `create folder`/`create file` with no location land on the Desktop, never in
  Jarvis's own install directory.
- `delete file` removes permanently — it does not go to the Recycle Bin.

---

## Development

```bat
python -m pytest tests -q          :: 268 tests, ~2.5 s
python tools/stress_test.py        :: 21 hardest phrasings, end to end
python -m jarvis --benchmark       :: routing latency per phrase
python -m jarvis --list            :: every registered intent
python -m jarvis --no-voice --no-ui:: headless REPL, no microphone
ruff check jarvis tests
```

`tests/conftest.py` stubs out every side-effecting exit from the package —
mouse, keyboard, `subprocess`, `os.startfile`, `webbrowser` — autouse, for the
whole suite. Tests assert on what *would* have been executed.

This is not belt-and-braces. Jarvis's handlers really do shut Windows down and
really do delete files, and during development a confirmation test dispatched
`shutdown the computer` followed by `yes` and powered the machine off mid-run.
A test for a destructive command asserts on the recorded call, never a real one.

```
jarvis/
  core/      engine (orchestration), router (dispatch), actuator (mouse/keyboard),
             fastinput (batched SendInput typing), focus (who has the keyboard),
             screen (finding things by the words on them)
  speech/    stt (Vosk streaming), wake (acoustic wake word), tts (SAPI5)
  skills/    input_control, apps, window, web, system, files, text_edit, meta
  nlp/       matcher (normalisation, fuzzy), chain (compound commands),
             brain (optional LLM fallback)
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
| Mishears you / reacts to background noise | Set `speech.accuracy` to `"accurate"` |
| "I can't see that on screen" | The label must be visible and spelled as shown; OCR reads what is drawn, not what is scrolled out of view |
| Typed text comes out garbled | Report it — that app drains input unusually; the clipboard path should already handle it |

## Licence

MIT.
