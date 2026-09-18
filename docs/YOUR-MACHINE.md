# Jarvis on your laptop

Written for Lakshmanan, from this machine's own settings and Jarvis's own log —
not from general advice. Every number below was measured here.

Last checked: 18 September 2026.

---

## 1. What you are running it on

| | |
|---|---|
| Laptop | HP Pavilion 15-eg3xxx |
| CPU | 13th Gen Intel Core i7-1360P — 12 cores, 16 threads |
| RAM | 15.6 GB, about 5.6 GB free while Jarvis runs |
| Screen | 1920 × 1080 |
| OS | Windows 11 Home Single Language (build 26200) |
| Microphone | Microphone Array (Intel Smart Sound) — the built-in one |
| Drives | C: 63.8 GB free · D: 233 GB free · F: 224 GB free |

**What this means for Jarvis.** The machine is comfortably fast enough; nothing
here is limited by it. Known-command routing measures **0.1 ms median**, and the
only slow things are the ones that wait on a network or on a window appearing.

The microphone is the laptop's built-in array. It also picks up your speakers,
which is why the wake word exists — see §5.

---

## 2. How you actually use it

From 951 commands in the log:

```
type_text      190     select_all      50     scroll         21
open_app       163     press_key       49     save           21
chain          103     click_at        27     copy           21
(unmatched)     78     play_youtube    25     select_count   20
                       web_search      22     power          20
```

**814 of 951 succeeded — 86%.** Median 0.1 ms, 90th percentile 81 ms, slowest
6.2 s (a chain waiting for a window).

The 78 unmatched are the interesting ones, and most are now fixed. What is left
is in §6.

---

## 3. The commands, grouped by what you do most

### Typing and editing — your most used
```
type hello world                 select all             copy / cut / paste
press ctrl s                     select 3 words         undo / redo
save                             select the line        delete
replace cat with dog             uppercase              trim
newline / new paragraph          spell word             join lines
comma / space / tab              go to the top
```

### Opening things
```
open chrome          open downloads        open github.com
open notepad         open desktop          open youtube
open word            open documents        open windows search
open antigravity     create folder reports on desktop
open terminal        create file notes.txt
```
**76 app names known** — 14 of them added today for apps you actually have
(§7). If a name is not known, Jarvis now passes it to Windows, so most things
open anyway.

### Clicking without touching the mouse
```
choose AD JAYANTAN               click the first link
click Sign in                    open the second result
select Guest mode                select the third option
choose Krishna in chrome         click the last tab
```
Three ways this is answered, tried in this order:
1. **Accessibility tree** — exact, instant, works in Explorer, Settings, Office.
2. **OCR** — reads whatever is drawn, ~190 ms. This is how Chrome's profile
   cards are found; Chrome does not publish them to the tree.
3. **Vision** — a model looks at a screenshot. 1.5–6 s, and the screenshot
   leaves your machine. Last resort only.

### Browser
```
search for python decorators     new tab / close tab / reopen tab
play lofi beats                  next tab
go to youtube                    refresh · go back · go forward
find pricing on this page        bookmark this page · incognito
```

### Writing with the model
```
write about actor vijay          what's on screen
start dictation                  stop dictation
private dictation                (types what you say, masked — for passwords)
```

### Antigravity
```
open agent jarvis in antigravity          new antigravity window
compare old.py and new.py in antigravity  antigravity extensions
```
Project name alone is enough — `F:/` is registered as a project root.

### System
```
volume 40 · volume up / down · mute       battery · system status
pause · next track · previous track       screenshot · lock
shutdown / restart / sleep (asks first)   dark mode · open wifi settings
```

### Controlling Jarvis
```
stop            (or the END button — kills a running task)
speak off       go to sleep / wake up      repeat · latency · help
```

### The last resort
```
work out how to turn on dark mode
figure out how to change the wallpaper
```
Screenshot, one decided action, act, look again — up to 8 steps. **Asks before
it starts**, because it drives the mouse on its own judgement and sends
pictures of your screen to a model. Roughly 3–8 s per step.

---

## 4. How to phrase things so they work

These are the patterns that measurably work here.

**Say the whole name, not part of it.**
You have profiles named Lakshman, Lakshman 2007, lakshmanan, lakshmanan 2007,
Lakshmanan and Lakshmanan CEA. `choose laksh` matches five of them.
`choose lakshmanan 2007` matches one.

> Two of your profiles differ only in capitals — `lakshmanan` and
> `Lakshmanan`. Nothing can tell those apart. Use `lakshmanan 2007` or
> `Lakshmanan CEA` instead.

**Typos and Tanglish are fine.** These all work:
```
selct krishna profile      →  clicks Krishna
notepad open pannu         →  open notepad
volume konjam kammi pannu  →  volume down
chrome open pannu          →  open chrome
```
Anything not recognised outright goes to a model, which costs ~600–900 ms. A
known command costs 0.1 ms, so exact phrasing is about six thousand times
faster — but only when you already know it.

**Chain with "and", up to 8 steps.**
```
open chrome and go to youtube and play lofi beats and volume 40
open agent jarvis in antigravity and run command git status
```
A failed step stops the chain — deliberately, so "open notepad and type X"
never types into the wrong window.

**Say what you mean, not how to do it.** `delete all the text` becomes
`select all and delete` by itself.

**Click by what is written, or by position — both work.**
```
click Confirm and continue     (by words — say the whole label)
click the first link           (by position)
select the third option        (by position, answered by looking)
```

---

## 5. Voice

Wake word is **"hey jarvis"**, threshold 0.5, using an acoustic detector
(openWakeWord). Nothing is transcribed, shown or logged until it fires — that
is the point, but it means silence and a crash look identical.

- **Opening the bar counts as the wake word**, so clicking the orb and speaking
  works without saying "hey jarvis".
- Check the detector hears you: `python -m jarvis --check-wake`. It prints a
  live score bar for 20 s. **I have never been able to test this — I cannot
  speak.** If it never fires, lower `speech.wake_threshold` in
  `~/.jarvis/config.json`.
- Speech model is set to **accurate** (the 128 MB one), already downloaded.
- Mishearings in your log: "slect", "hai nicky", "this want to make amends".
  The planner absorbs most of these now.

**Hotkeys**
```
ctrl+alt+j   push to talk        ctrl+alt+x   panic stop
ctrl+alt+k   text box            ctrl+alt+m   mute
```

---

## 6. What still does not work, honestly

| You said | What happens | Why |
|---|---|---|
| `close all tabs` (×4) | fails | Matches close_app, which wants an app name. No such command exists yet. |
| `open omen gaming` (×2) | fails | OMEN Gaming Hub is not installed, or not launchable by that name. |
| `close chrome` (×3) | fails sometimes | Chrome's window title has to match; a minimised window is missed. |

Also true:
- **The model gets facts wrong.** It invented a birth name for Vijay, twice.
  `write about X` produces confident prose that may be untrue. Read it.
- **Autopilot is slow** — 3–8 s per step, so 8 steps is most of a minute.
- **The vision fallback sends a screenshot off the machine.** Turn it off with
  `control.visual_fallback: false` in `~/.jarvis/config.json`.
- **Password dictation keeps the password out of Jarvis's log and display.**
  It does not keep it out of the room.

---

## 7. What changed on your machine today

**14 app aliases added** to `~/.jarvis/app_aliases.json`, for software you
actually have. All verified to resolve:
```
open antigravity   open terminal        open teams        open acrobat
open opera         open kmplayer        open thonny       open copilot
open media player  open quick share     open iclone       open ms teams
open windows terminal                   open km player
```
That file is yours — add to it whenever you install something.

**A second model was configured.** Groq is the primary; Gemini is the fallback,
so a rate-limit no longer means "I don't know how to". Fifteen calls in one
session came back 429 with nothing behind them before this.

---

## 8. Two things to do

1. **Rotate both API keys.** The Groq key was pasted into a chat. The Gemini key
   is hard-coded in `CTRL_AI_Agentic_Desktop_Assistant/ctrl_ai_hud.py` line 65.
   Neither is in this repository — that folder is in `.gitignore` and the keys
   live only in `~/.jarvis/config.json` — but both have been exposed.
2. **Run `python -m jarvis --check-wake`** and say "hey jarvis" a few times.
   It is the one thing about your setup nobody has verified.

---

## Where things live

```
~/.jarvis/config.json         settings, and the API keys
~/.jarvis/app_aliases.json    your own app names
~/.jarvis/jarvis.log          every command, with timings
~/.jarvis/models/             the speech models
F:/agent jarvis/              the code
```

`python -m jarvis --list` prints all 103 commands with an example of each.
