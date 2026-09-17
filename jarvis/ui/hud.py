"""The floating HUD: a J.A.R.V.I.S-style orb that expands into a command bar.

Two windows, not one:
  - `Orb`  a small always-on-top circle docked to a screen edge. It is a real
    circle, not a square icon with rounded corners — Windows lets a Tk window
    key out one colour as fully click-through-transparent (`-transparentcolor`),
    so anything painted in that colour disappears and the frame around the
    canvas vanishes with it.
  - `Bar`  the status line + text entry, hidden until the orb is clicked.

Keeping them separate windows (rather than one window that resizes) means the
orb never has to reflow or repaint mid-animation when the bar opens; it just
keeps pulsing underneath.

Thread safety: every bus callback arrives on a worker thread, so all of them
marshal onto the Tk thread with `after(0, ...)`.
"""
from __future__ import annotations

import contextlib
import logging
import math
import tkinter as tk
from tkinter import font as tkfont

from jarvis.bus import BUS, COMMAND, ERROR, FOCUS_CONSOLE, HEARD, RESULT, STATE
from jarvis.core import focus

log = logging.getLogger("jarvis.hud")

BG = "#0b0f14"
FG = "#e6edf3"
DIM = "#7d8590"
OK = "#3fb950"
BAD = "#f85149"
ACCENT_ON = "#22d3ee"
ACCENT_OFF = "#f85149"
FIELD = "#161b22"
FIELD_EDGE = "#2a313a"
PLACEHOLDER = "Say “Hey Jarvis”, or type a command"
# What each state means to someone looking at the bar, rather than the
# engine's own vocabulary.
STATE_WORDS = {
    "idle": "say “hey jarvis”",
    "listening": "listening",
    "acting": "working",
    "speaking": "speaking",
    "muted": "microphone off",
}
ENTRY_HINT = "Type a command and press Enter"
CHROMA = "#ff00fe"  # keyed out by -transparentcolor; must appear nowhere else

ORB_SIZE = 78
PULSE_FPS_MS = 40

# Reactor colour per engine state: (ring, core, glow-amplitude 0..1).
STATE_LOOK = {
    "idle": ("#1b6b7a", "#2ea3b8", 0.15),
    "listening": ("#0891b2", "#22d3ee", 0.55),
    "acting": ("#7c3aed", "#a371f7", 0.65),
    "speaking": ("#15803d", "#3fb950", 0.5),
    "muted": ("#991b1b", "#f85149", 0.2),
}


def _hover(widget, over: str, out: str) -> None:
    """Light a control up under the pointer, so it reads as clickable."""
    widget.bind("<Enter>", lambda _e: widget.config(fg=over), add="+")
    widget.bind("<Leave>", lambda _e: widget.config(fg=out), add="+")


def _chip(parent, text: str, bg: str, fg: str, command) -> tk.Label:
    """A small pill-shaped button."""
    chip = tk.Label(
        parent, text=text, bg=bg, fg=fg, cursor="hand2", padx=10, pady=3,
        font=tkfont.Font(family="Consolas", size=10),
    )
    chip.bind("<Button-1>", lambda _e: command())
    return chip


class Orb:
    """The always-visible reactor icon. Click toggles the bar; drag moves it."""

    def __init__(self, hud: HUD) -> None:
        self.hud = hud
        cfg = hud.cfg
        self.root = tk.Tk()
        self.root.title("Jarvis")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=CHROMA)
        with contextlib.suppress(tk.TclError):
            self.root.attributes("-transparentcolor", CHROMA)
        self._place(cfg)

        self.canvas = tk.Canvas(
            self.root, width=ORB_SIZE, height=ORB_SIZE, bg=CHROMA, highlightthickness=0
        )
        self.canvas.pack()

        self._state = "idle"
        self._phase = 0.0
        self._drag_origin: tuple[int, int] | None = None
        self._win_origin: tuple[int, int] | None = None
        self._moved = False

        self.canvas.bind("<Button-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Button-3>", self._context_menu)

        self._draw()
        self.root.after(PULSE_FPS_MS, self._tick)

    def _place(self, cfg) -> None:
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        margin = cfg.margin
        x = margin if "left" in cfg.corner else sw - ORB_SIZE - margin
        y = (sh - ORB_SIZE) // 2 if "left" in cfg.corner or "right" in cfg.corner else margin
        if cfg.corner in ("top-left", "top-right"):
            y = margin
        elif cfg.corner in ("bottom-left", "bottom-right"):
            y = sh - ORB_SIZE - margin - 40
        self.root.geometry(f"{ORB_SIZE}x{ORB_SIZE}+{x}+{y}")

    # --- interaction ---------------------------------------------------

    def _press(self, event) -> None:
        log.debug("orb press at %s,%s", event.x, event.y)
        self._drag_origin = (event.x_root, event.y_root)
        self._win_origin = (self.root.winfo_x(), self.root.winfo_y())
        self._moved = False

    def _motion(self, event) -> None:
        if self._drag_origin is None:
            return
        dx = event.x_root - self._drag_origin[0]
        dy = event.y_root - self._drag_origin[1]
        if abs(dx) > 3 or abs(dy) > 3:
            self._moved = True
        self.root.geometry(f"+{self._win_origin[0] + dx}+{self._win_origin[1] + dy}")
        if self._moved:
            self.hud.reposition_bar()

    def _release(self, _event) -> None:
        log.debug("orb release, moved=%s", self._moved)
        if not self._moved:
            self.hud.toggle_bar()
        self._drag_origin = None

    def _context_menu(self, event) -> None:
        menu = tk.Menu(self.root, tearoff=0, bg="#161b22", fg=FG, activebackground="#22d3ee")
        menu.add_command(label="Open console", command=self.hud.show_bar)
        menu.add_command(
            label="Unmute microphone" if self.hud.engine.is_muted else "Mute microphone",
            command=self.hud.toggle_voice,
        )
        menu.add_separator()
        menu.add_command(label="Exit Jarvis", command=self.hud.request_exit)
        menu.tk_popup(event.x_root, event.y_root)

    # --- rendering -------------------------------------------------------

    def set_state(self, state: str) -> None:
        self._state = state if state in STATE_LOOK else "idle"

    def geometry_box(self) -> tuple[int, int, int, int]:
        """(x, y, width, height) in screen coordinates, for anchoring the bar."""
        return (self.root.winfo_x(), self.root.winfo_y(), ORB_SIZE, ORB_SIZE)

    def _tick(self) -> None:
        self._phase = (self._phase + 1) % 100
        self._draw()
        self.root.after(PULSE_FPS_MS, self._tick)

    def _draw(self) -> None:
        c = self.canvas
        c.delete("all")
        ring, core, amp = STATE_LOOK[self._state]
        cx = cy = ORB_SIZE / 2
        pulse = 1.0 + amp * math.sin(self._phase / 100 * 2 * math.pi) * 0.5

        # Concentric rings mimic an arc reactor: dark housing, a bright ring,
        # a dark gap, then a glowing core with a small highlight for shine.
        c.create_oval(2, 2, ORB_SIZE - 2, ORB_SIZE - 2, fill="#05080c", outline=ring, width=2)
        outer_r = (ORB_SIZE / 2 - 8) * (0.92 + 0.08 * pulse)
        c.create_oval(cx - outer_r, cy - outer_r, cx + outer_r, cy + outer_r,
                      fill="", outline=ring, width=3)
        for i in range(8):
            angle = (i / 8) * 2 * math.pi + self._phase / 100 * math.pi
            x1 = cx + math.cos(angle) * (outer_r - 6)
            y1 = cy + math.sin(angle) * (outer_r - 6)
            x2 = cx + math.cos(angle) * (outer_r - 14)
            y2 = cy + math.sin(angle) * (outer_r - 14)
            c.create_line(x1, y1, x2, y2, fill=ring, width=2)
        core_r = (ORB_SIZE / 2 - 22) * (0.85 + 0.25 * pulse)
        c.create_oval(cx - core_r, cy - core_r, cx + core_r, cy + core_r, fill=core, outline="")
        shine_r = core_r * 0.35
        c.create_oval(cx - shine_r, cy - shine_r * 1.4, cx + shine_r, cy + shine_r * 0.6,
                      fill="#ffffff", outline="", stipple="gray50")

    def destroy(self) -> None:
        with contextlib.suppress(tk.TclError):
            self.root.destroy()


class HUD:
    def __init__(self, engine) -> None:
        self.engine = engine
        self.cfg = engine.config.ui
        self.orb = Orb(self)
        self.root = self.orb.root  # mainloop owner

        self.bar = tk.Toplevel(self.root)
        self.bar.withdraw()
        self.bar.title("Jarvis")
        self.bar.overrideredirect(True)
        self.bar.attributes("-topmost", True)
        self.bar.attributes("-alpha", self.cfg.opacity)
        self.bar.configure(bg=BG)

        mono = tkfont.Font(family="Consolas", size=10)
        big = tkfont.Font(family="Segoe UI", size=13)
        entry_font = tkfont.Font(family="Segoe UI", size=12)
        icon = tkfont.Font(family="Segoe UI Emoji", size=12)

        # --- header: state on the left, controls on the right ---------------
        header = tk.Frame(self.bar, bg=BG)
        header.pack(fill="x", padx=18, pady=(12, 0))

        self.state_dot = tk.Canvas(header, width=12, height=12, bg=BG, highlightthickness=0)
        self.state_dot.pack(side="left", pady=2)
        self._dot_id = self.state_dot.create_oval(
            1, 1, 11, 11, fill=STATE_LOOK["idle"][1], width=0
        )

        self.state_label = tk.Label(
            header, text="idle", bg=BG, fg=DIM, font=mono, anchor="w"
        )
        self.state_label.pack(side="left", padx=(8, 0))

        close_btn = tk.Label(
            header, text="✕", bg=BG, fg=DIM, font=mono, cursor="hand2", padx=4
        )
        close_btn.pack(side="right")
        close_btn.bind("<Button-1>", lambda _e: self.hide_bar())
        _hover(close_btn, FG, DIM)

        self.end_btn = _chip(header, "■ end", "#3d1418", ACCENT_OFF, self.end_task)
        self.end_btn.pack(side="right", padx=(0, 10))

        self.timing = tk.Label(header, text="", bg=BG, fg=DIM, font=mono, anchor="e")
        self.timing.pack(side="right", padx=(0, 12))

        # --- what was heard, and what came of it ----------------------------
        body = tk.Frame(self.bar, bg=BG)
        body.pack(fill="both", expand=True, padx=18, pady=(10, 4))

        self.heard = tk.Label(
            body, text=PLACEHOLDER, bg=BG, fg=FG, font=big,
            anchor="w", justify="left", wraplength=self.cfg.width - 44,
        )
        self.heard.pack(fill="x")

        self.status = tk.Label(
            body, text="", bg=BG, fg=DIM, font=mono, anchor="w",
            wraplength=self.cfg.width - 44, justify="left",
        )
        self.status.pack(fill="x", pady=(4, 0))

        # --- the input row --------------------------------------------------
        row = tk.Frame(self.bar, bg=BG)
        row.pack(fill="x", padx=18, pady=(8, 16))

        # A one-pixel frame behind the entry is how Tk gets a visible border:
        # Entry's own relief options draw a bevel that looks wrong on a dark
        # surface. The inner frame is the field, the outer is its edge.
        self._entry_edge = tk.Frame(row, bg=FIELD_EDGE, padx=1, pady=1)
        self._entry_edge.pack(side="left", fill="x", expand=True)
        field = tk.Frame(self._entry_edge, bg=FIELD)
        field.pack(fill="both", expand=True)

        self.entry = tk.Entry(
            field, bg=FIELD, fg=FG, insertbackground=self.cfg.accent,
            relief="flat", font=entry_font, borderwidth=0,
            highlightthickness=0, insertwidth=2,
        )
        self.entry.pack(fill="x", padx=12, pady=9)
        self.entry.bind("<Return>", self._on_submit)
        self.entry.bind("<Escape>", lambda _e: self.hide_bar())
        self.entry.bind("<Up>", self._on_history)
        self.entry.bind("<Down>", self._on_history_forward)
        self.entry.bind("<FocusIn>", self._on_entry_focus)
        self.entry.bind("<FocusOut>", self._on_entry_blur)

        # Microphone, beside what you would type instead of it: the two ways of
        # giving a command sit together rather than in separate corners.
        self.mic_btn = tk.Label(
            row, text="🎤", bg=FIELD, fg=ACCENT_ON, font=icon,
            cursor="hand2", padx=12, pady=8,
        )
        self.mic_btn.pack(side="left", padx=(8, 0))
        self.mic_btn.bind("<Button-1>", lambda _e: self.toggle_voice())

        self.send_btn = tk.Label(
            row, text="➤", bg=FIELD, fg=DIM, font=icon,
            cursor="hand2", padx=12, pady=8,
        )
        self.send_btn.pack(side="left", padx=(8, 0))
        self.send_btn.bind("<Button-1>", lambda _e: self._on_submit())
        _hover(self.send_btn, self.cfg.accent, DIM)

        for widget in (self.bar, self.heard, self.status, header, self.state_label):
            widget.bind("<Button-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)

        self._history: list[str] = []
        self._history_at = 0
        self._placeholder_showing = False
        self._bar_visible = False
        self._user_moved_bar = False
        self._last_source = ""
        # The window the bar took the keyboard from, owed it back.
        self._displaced: int | None = None
        # Runs on the engine's thread immediately before a command is
        # dispatched, whether it arrived by voice or by typing.
        engine.before_dispatch = self.release_focus

        BUS.on(STATE, lambda s: self._ui(self._set_state, s))
        BUS.on(HEARD, lambda p: self._ui(self._set_heard, p))
        BUS.on(COMMAND, lambda p: self._ui(self._set_command, p))
        BUS.on(RESULT, lambda r: self._ui(self._set_result, r))
        BUS.on(ERROR, lambda m: self._ui(self._set_error, m))
        BUS.on(FOCUS_CONSOLE, lambda _p: self._ui(lambda _p2: self.show_bar(), None))

        self.root.protocol("WM_DELETE_WINDOW", self.request_exit)
        self.root.after(250, self._tick)

    # --- orb <-> bar coordination ------------------------------------------

    def toggle_bar(self) -> None:
        log.debug("toggle_bar, currently visible=%s", self._bar_visible)
        self.hide_bar() if self._bar_visible else self.show_bar()

    def show_bar(self, take_focus: bool = True) -> None:
        """Reveal the command bar.

        `take_focus` is False when the bar opens by itself to report something,
        because a status message must never pull the keyboard out of whatever
        the user is working in.
        """
        log.debug("show_bar take_focus=%s", take_focus)
        if not self._user_moved_bar:
            self.reposition_bar()
        self.bar.deiconify()
        self.bar.lift()
        self.bar.attributes("-topmost", True)
        if take_focus:
            # Remember what we are displacing so `release_focus` can put it
            # back before any command runs. Without this, "type hello" types
            # into this very entry box.
            displaced = focus.foreground()
            if displaced is not None and not focus.is_ours(displaced):
                self._displaced = displaced
                log.debug("displacing %r", focus.title(displaced))
            self.entry.focus_force()
            # Opening the bar is as explicit an address as saying the wake
            # word, so it counts as one. Without this the user opens the bar,
            # speaks, and nothing happens at all -- the recogniser is still
            # waiting to hear "hey Jarvis" and shows nothing while it waits,
            # which is indistinguishable from the app having frozen.
            self.engine.wake(silent=True)
        self._bar_visible = True

    def release_focus(self) -> None:
        """Hand the keyboard back to whatever the bar displaced.

        Called synchronously before a command is dispatched, from whichever
        thread is about to run it — commands act on the window the user was
        using, not on Jarvis.
        """
        if self._displaced is None:
            return
        restored = focus.restore(self._displaced)
        log.debug("release_focus to %r -> %s", focus.title(self._displaced), restored)
        self._displaced = None

    def end_task(self) -> None:
        """Abandon the running command and be ready for the next one.

        The same thing Ctrl+Alt+X does, put where it can be found without
        knowing a shortcut. It clears the text box as well, so whatever is typed
        there does not go on to run once the abort has landed.
        """
        self.clear_entry()
        self.engine.panic()

    def toggle_voice(self) -> None:
        """Mute or unmute the microphone from the bar.

        This flips the same flag the Ctrl+Alt+M hotkey and the orb's menu use,
        so the three controls can never disagree about whether the mic is live.
        """
        self.engine.toggle_mute()
        self._refresh_voice_button()

    def _refresh_voice_button(self) -> None:
        """Show the microphone's real state, not the action clicking would take.

        A glance should answer "is it listening?" without the user having to
        work out whether the label is a state or an instruction.
        """
        muted = self.engine.is_muted
        self.mic_btn.config(
            text="🔇" if muted else "🎤",
            fg=ACCENT_OFF if muted else ACCENT_ON,
            bg="#2a1518" if muted else FIELD,
        )

    def hide_bar(self) -> None:
        self.bar.withdraw()
        self._bar_visible = False

    def reposition_bar(self) -> None:
        ox, oy, ow, oh = self.orb.geometry_box()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        width, height = self.cfg.width, self.cfg.height
        # Prefer the side of the orb with room; default to opening inward
        # from a screen edge so the bar never spills off-screen.
        x = ox + ow - width if ox + ow / 2 > sw / 2 else ox
        x = max(8, min(x, sw - width - 8))
        if oy > sh / 2:
            y = oy - height - 10
        else:
            y = oy + oh + 10
        y = max(8, min(y, sh - height - 8))
        self.bar.geometry(f"{width}x{height}+{x}+{y}")

    def request_exit(self) -> None:
        import threading

        threading.Thread(target=self.engine.stop, daemon=True).start()
        self.close()

    # --- dragging the bar itself --------------------------------------------

    def _drag_start(self, event) -> None:
        self._bar_drag_origin = (event.x_root, event.y_root)
        self._bar_win_origin = (self.bar.winfo_x(), self.bar.winfo_y())

    def _drag_move(self, event) -> None:
        if not hasattr(self, "_bar_drag_origin"):
            return
        dx = event.x_root - self._bar_drag_origin[0]
        dy = event.y_root - self._bar_drag_origin[1]
        self.bar.geometry(f"+{self._bar_win_origin[0] + dx}+{self._bar_win_origin[1] + dy}")
        self._user_moved_bar = True

    # --- bus handlers --------------------------------------------------------

    def _ui(self, fn, payload) -> None:
        with contextlib.suppress(RuntimeError):
            self.root.after(0, fn, payload)

    def _set_state(self, state: str) -> None:
        self.orb.set_state(state)
        self._refresh_voice_button()
        # "idle" says nothing about why nothing is happening. With the wake
        # word gating the recogniser, the honest label is what it is waiting to
        # hear.
        self.state_label.config(
            text=STATE_WORDS.get(state, state),
            fg=DIM if state in ("idle", "muted") else FG,
        )
        # Only meaningful while something is running.
        self.end_btn.config(fg=ACCENT_OFF if state == "acting" else DIM)
        color = STATE_LOOK.get(state, STATE_LOOK["idle"])[1]
        self.state_dot.itemconfig(self._dot_id, fill=color)

    def _set_heard(self, payload: dict) -> None:
        text = payload.get("text", "")
        if not text:
            return
        # Deliberately no auto-show here: partial/final transcripts include
        # every ambient noise the mic mis-hears, and popping the bar open for
        # each one would make it flicker constantly in a noisy room.
        self.heard.config(text=text, fg=FG if payload.get("final") else DIM)

    def _set_command(self, payload: dict) -> None:
        self.heard.config(text=payload.get("text", ""), fg=FG)
        self.status.config(text=f"→ {payload.get('source', '')}", fg=DIM)
        self._last_source = payload.get("source", "")

    def _set_result(self, result) -> None:
        message = result.say or result.detail or ("done" if result.ok else "failed")
        self.status.config(text=message, fg=OK if result.ok else BAD)
        ms = result.data.get("elapsed_ms")
        intent = result.data.get("intent", "")
        self.timing.config(text=f"{intent}  {ms} ms" if ms is not None else "")
        # Pop the bar open for anything the user deliberately triggered (typed
        # or one-shot), and for any *successful* voice action. A voice command
        # that failed is usually the mic mishearing background noise as a
        # phrase ("yeah", "the"...) -- surfacing that would make the bar snap
        # back open right after the user closes it, on every stray sound.
        if not self._bar_visible and (result.ok or self._last_source != "voice"):
            self.show_bar(take_focus=False)

    def _set_error(self, message: str) -> None:
        self.status.config(text=str(message), fg=BAD)
        self.show_bar(take_focus=False)

    # --- input ---------------------------------------------------------------

    def _on_submit(self, _event=None) -> None:
        text = self.typed_text()
        if not text:
            return
        self.clear_entry()
        self._history.append(text)
        self._history_at = len(self._history)
        import threading

        threading.Thread(target=self.engine.submit, args=(text, "text"), daemon=True).start()

    # --- the text box ---------------------------------------------------
    # Tk has no placeholder, so it is grey text the widget is actually holding.
    # Every read of the box has to know that, which is why `typed_text` exists
    # rather than callers touching `entry.get()`.

    def _on_entry_focus(self, _event=None) -> None:
        self._entry_edge.config(bg=self.cfg.accent)
        if self._placeholder_showing:
            self.entry.delete(0, "end")
            self.entry.config(fg=FG)
            self._placeholder_showing = False

    def _on_entry_blur(self, _event=None) -> None:
        self._entry_edge.config(bg=FIELD_EDGE)
        self._show_placeholder()

    def _show_placeholder(self) -> None:
        """Say what the box is for while it is empty and unfocused."""
        if self.entry.get():
            return
        self.entry.insert(0, ENTRY_HINT)
        self.entry.config(fg=DIM)
        self._placeholder_showing = True

    def typed_text(self) -> str:
        """What the user actually typed — never the placeholder."""
        return "" if self._placeholder_showing else self.entry.get().strip()

    def clear_entry(self) -> None:
        self.entry.delete(0, "end")
        self.entry.config(fg=FG)
        self._placeholder_showing = False

    def _on_history_forward(self, _event=None) -> None:
        if not self._history:
            return
        self._history_at = min(self._history_at + 1, len(self._history))
        self.clear_entry()
        if self._history_at < len(self._history):
            self.entry.insert(0, self._history[self._history_at])

    def _on_history(self, _event=None) -> None:
        """Walk back through what was typed, newest first."""
        if not self._history:
            return
        self._history_at = max(0, self._history_at - 1)
        self.clear_entry()
        self.entry.insert(0, self._history[self._history_at])

    def focus_entry(self) -> None:
        self.show_bar()

    # --- loop ------------------------------------------------------------------

    def _tick(self) -> None:
        if not self.engine.running:
            self.close()
            return
        self.root.after(250, self._tick)

    def run(self) -> None:
        self.root.mainloop()

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.bar.destroy()
        with contextlib.suppress(Exception):
            self.root.quit()
            self.root.destroy()
