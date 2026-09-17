"""Always-on-top heads-up display and text console.

Tkinter rather than Qt on purpose: it ships with Python, starts in ~40 ms and
adds no install weight. The HUD is a status light and a text box — it does not
need a widget toolkit.

Thread safety: every bus callback arrives on a worker thread, so all of them
marshal onto the Tk thread with `after(0, ...)`.
"""
from __future__ import annotations

import contextlib
import logging
import tkinter as tk
from tkinter import font as tkfont

from jarvis.bus import BUS, COMMAND, ERROR, HEARD, RESULT, STATE

log = logging.getLogger("jarvis.hud")

BG = "#0b0f14"
FG = "#e6edf3"
DIM = "#7d8590"
OK = "#3fb950"
BAD = "#f85149"

STATE_COLORS = {
    "idle": "#30363d",
    "listening": "#22d3ee",
    "acting": "#a371f7",
    "speaking": "#3fb950",
    "muted": "#f85149",
}


class HUD:
    def __init__(self, engine) -> None:
        self.engine = engine
        cfg = engine.config.ui
        self.cfg = cfg

        self.root = tk.Tk()
        self.root.title("Jarvis")
        self.root.overrideredirect(True)          # frameless
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", cfg.opacity)
        self.root.configure(bg=BG)
        self._place()

        mono = tkfont.Font(family="Consolas", size=10)
        big = tkfont.Font(family="Segoe UI", size=12)

        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=14, pady=(10, 2))

        self.orb = tk.Canvas(
            header, width=14, height=14, bg=BG, highlightthickness=0
        )
        self.orb.pack(side="left")
        self._orb_id = self.orb.create_oval(2, 2, 12, 12, fill=STATE_COLORS["idle"], width=0)

        self.state_label = tk.Label(
            header, text="idle", bg=BG, fg=DIM, font=mono, anchor="w"
        )
        self.state_label.pack(side="left", padx=(8, 0))

        self.timing = tk.Label(header, text="", bg=BG, fg=DIM, font=mono, anchor="e")
        self.timing.pack(side="right")

        self.heard = tk.Label(
            self.root,
            text="Say “Jarvis” or type below",
            bg=BG,
            fg=FG,
            font=big,
            anchor="w",
            justify="left",
            wraplength=cfg.width - 28,
        )
        self.heard.pack(fill="x", padx=14, pady=(2, 2))

        self.status = tk.Label(
            self.root, text="", bg=BG, fg=DIM, font=mono, anchor="w",
            wraplength=cfg.width - 28, justify="left",
        )
        self.status.pack(fill="x", padx=14)

        self.entry = tk.Entry(
            self.root,
            bg="#161b22",
            fg=FG,
            insertbackground=cfg.accent,
            relief="flat",
            font=mono,
        )
        self.entry.pack(fill="x", padx=14, pady=(6, 12), ipady=5)
        self.entry.bind("<Return>", self._on_submit)
        self.entry.bind("<Escape>", lambda _e: self.entry.delete(0, "end"))
        self.entry.bind("<Up>", self._on_history)

        # Drag the HUD by its body, since there is no title bar.
        for widget in (self.root, self.heard, self.status, header, self.state_label):
            widget.bind("<Button-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)

        self.root.bind("<Control-Alt-k>", lambda _e: self.focus_entry())

        BUS.on(STATE, lambda s: self._ui(self._set_state, s))
        BUS.on(HEARD, lambda p: self._ui(self._set_heard, p))
        BUS.on(COMMAND, lambda p: self._ui(self._set_command, p))
        BUS.on(RESULT, lambda r: self._ui(self._set_result, r))
        BUS.on(ERROR, lambda m: self._ui(self._set_error, m))

        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(200, self._tick)

    # --- placement and dragging -------------------------------------------

    def _place(self) -> None:
        cfg = self.cfg
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        margin = cfg.margin
        x = margin if "left" in cfg.corner else sw - cfg.width - margin
        y = margin if "top" in cfg.corner else sh - cfg.height - margin - 40
        self.root.geometry(f"{cfg.width}x{cfg.height}+{x}+{y}")

    def _drag_start(self, event) -> None:
        self._drag_origin = (event.x_root, event.y_root)
        self._win_origin = (self.root.winfo_x(), self.root.winfo_y())

    def _drag_move(self, event) -> None:
        if not hasattr(self, "_drag_origin"):
            return
        dx = event.x_root - self._drag_origin[0]
        dy = event.y_root - self._drag_origin[1]
        self.root.geometry(f"+{self._win_origin[0] + dx}+{self._win_origin[1] + dy}")

    # --- bus handlers ------------------------------------------------------

    def _ui(self, fn, payload) -> None:
        # RuntimeError here means the window is already torn down; nothing to do.
        with contextlib.suppress(RuntimeError):
            self.root.after(0, fn, payload)

    def _set_state(self, state: str) -> None:
        self.orb.itemconfig(self._orb_id, fill=STATE_COLORS.get(state, DIM))
        self.state_label.config(text=state)

    def _set_heard(self, payload: dict) -> None:
        text = payload.get("text", "")
        if not text:
            return
        self.heard.config(text=text, fg=FG if payload.get("final") else DIM)

    def _set_command(self, payload: dict) -> None:
        self.heard.config(text=payload.get("text", ""), fg=FG)
        self.status.config(text=f"→ {payload.get('source', '')}", fg=DIM)

    def _set_result(self, result) -> None:
        message = result.say or result.detail or ("done" if result.ok else "failed")
        self.status.config(text=message, fg=OK if result.ok else BAD)
        ms = result.data.get("elapsed_ms")
        intent = result.data.get("intent", "")
        self.timing.config(text=f"{intent}  {ms} ms" if ms is not None else "")

    def _set_error(self, message: str) -> None:
        self.status.config(text=str(message), fg=BAD)

    # --- input -------------------------------------------------------------

    def _on_submit(self, _event=None) -> None:
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, "end")
        self._history = getattr(self, "_history", [])
        self._history.append(text)
        # Run off the Tk thread so a slow action never freezes the HUD.
        import threading

        threading.Thread(
            target=self.engine.submit, args=(text, "text"), daemon=True
        ).start()

    def _on_history(self, _event=None) -> None:
        history = getattr(self, "_history", [])
        if history:
            self.entry.delete(0, "end")
            self.entry.insert(0, history[-1])

    def focus_entry(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.entry.focus_force()

    # --- loop --------------------------------------------------------------

    def _tick(self) -> None:
        if not self.engine.running:
            self.close()
            return
        self.root.after(250, self._tick)

    def run(self) -> None:
        self.root.mainloop()

    def close(self) -> None:
        try:
            self.root.quit()
            self.root.destroy()
        except Exception:
            pass
