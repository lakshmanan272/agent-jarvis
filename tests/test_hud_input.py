"""The command box.

Tk has no placeholder, so the hint is grey text the widget genuinely holds.
That makes every read of the box a hazard: without care, an untouched bar
submits "Type a command and press Enter" as a command.
"""
from __future__ import annotations

import pytest

tk = pytest.importorskip("tkinter")


@pytest.fixture
def hud(no_real_input):
    """A real HUD against a real Tk, torn down after each test."""
    from jarvis.config import Config
    from jarvis.core.engine import Engine
    from jarvis.ui.hud import HUD

    cfg = Config()
    cfg.speech.enabled = False
    cfg.voice_out.enabled = False
    engine = Engine(cfg)
    try:
        widget = HUD(engine)
    except tk.TclError:  # no display
        pytest.skip("no display available")
    yield widget
    widget.close()
    engine.stop()


def test_placeholder_is_not_a_command(hud):
    hud._show_placeholder()
    assert hud.entry.get() != ""      # the widget really holds the hint
    assert hud.typed_text() == ""     # ...and nobody mistakes it for input


def test_submitting_an_untouched_box_does_nothing(hud):
    submitted = []
    hud.engine.submit = lambda text, source="text": submitted.append(text)
    hud._show_placeholder()
    hud._on_submit()
    assert submitted == []


def test_focusing_clears_the_hint(hud):
    hud._show_placeholder()
    hud._on_entry_focus()
    assert hud.entry.get() == ""
    assert hud._placeholder_showing is False


def test_leaving_an_empty_box_restores_the_hint(hud):
    hud._on_entry_focus()
    hud._on_entry_blur()
    assert hud._placeholder_showing is True


def test_leaving_a_filled_box_keeps_what_was_typed(hud):
    hud._on_entry_focus()
    hud.entry.insert(0, "open chrome")
    hud._on_entry_blur()
    assert hud.typed_text() == "open chrome"
    assert hud._placeholder_showing is False


def test_typed_text_is_stripped(hud):
    hud._on_entry_focus()
    hud.entry.insert(0, "   open chrome   ")
    assert hud.typed_text() == "open chrome"


def test_clearing_resets_the_flag(hud):
    hud._show_placeholder()
    hud.clear_entry()
    assert hud.entry.get() == ""
    assert hud._placeholder_showing is False


def test_end_clears_the_box(hud):
    from jarvis.core import actuator

    hud._on_entry_focus()
    hud.entry.insert(0, "half typed")
    hud.end_task()
    assert hud.typed_text() == ""
    actuator.clear_abort()


# --- history ---------------------------------------------------------------


def test_history_walks_back_and_forward(hud):
    hud.engine.submit = lambda text, source="text": None
    for phrase in ("open chrome", "scroll down", "select all"):
        hud._on_entry_focus()
        hud.entry.insert(0, phrase)
        hud._on_submit()

    hud._on_history()
    assert hud.entry.get() == "select all"
    hud._on_history()
    assert hud.entry.get() == "scroll down"
    hud._on_history_forward()
    assert hud.entry.get() == "select all"


def test_history_stops_at_the_oldest(hud):
    hud.engine.submit = lambda text, source="text": None
    hud._on_entry_focus()
    hud.entry.insert(0, "only one")
    hud._on_submit()
    for _ in range(5):
        hud._on_history()
    assert hud.entry.get() == "only one"


def test_history_on_an_empty_log_is_harmless(hud):
    hud._on_history()
    hud._on_history_forward()
    assert hud.typed_text() == ""


# --- the microphone button -------------------------------------------------


def test_the_mic_button_shows_the_state_not_the_action(hud):
    hud._refresh_voice_button()
    live = hud.mic_btn.cget("text")
    hud.toggle_voice()
    muted = hud.mic_btn.cget("text")
    assert live != muted
    assert hud.engine.is_muted is True
    hud.toggle_voice()
    assert hud.mic_btn.cget("text") == live
    assert hud.engine.is_muted is False


# --- the box has to survive a long reply ------------------------------------
# A screen description is a paragraph. With the body packed before the input
# row, Tk squeezed the row -- which comes last -- clean off the bottom of the
# fixed-height window: the reply was on screen and the box to type the next
# command was not, so the bar looked like it had stopped accepting input.

LONG_REPLY = (
    "The user is viewing Google Chrome in guest mode, which displays a profile "
    "selection screen. Behind this overlay, a web page appears to show academic "
    "results or placement data for a student named Lakshmanan, including course "
    "codes and overall scores."
)


def _result(say: str):
    from jarvis.skills.base import ActionResult

    return ActionResult(ok=True, say=say, data={"elapsed_ms": 1.0, "intent": "x"})


def _entry_fits(hud) -> bool:
    # `update`, not `update_idletasks`: a freshly deiconified window is not
    # reported as mapped until the map event itself has been processed.
    hud.bar.update()
    entry_bottom = hud.entry.winfo_rooty() + hud.entry.winfo_height()
    return hud.entry.winfo_ismapped() and entry_bottom <= (
        hud.bar.winfo_rooty() + hud.bar.winfo_height()
    )


def test_the_box_stays_visible_under_a_paragraph(hud):
    hud.show_bar(take_focus=False)
    assert _entry_fits(hud)
    hud._set_result(_result(LONG_REPLY))
    assert _entry_fits(hud), "the command box was pushed off the bottom"


def test_the_bar_grows_for_a_long_reply_and_shrinks_back(hud):
    hud.show_bar(take_focus=False)
    compact = hud.bar.winfo_height()
    hud._set_result(_result(LONG_REPLY))
    assert hud.bar.winfo_height() > compact
    hud._set_result(_result("Done."))
    assert hud.bar.winfo_height() == compact


def test_the_bar_never_takes_over_the_screen(hud):
    hud.show_bar(take_focus=False)
    hud._set_result(_result("word " * 4000))
    assert hud.bar.winfo_height() <= hud.root.winfo_screenheight() * 0.5


def test_a_bar_the_user_dragged_is_left_where_they_put_it(hud):
    hud.show_bar(take_focus=False)
    hud._user_moved_bar = True
    hud.bar.geometry("+50+50")
    hud.bar.update_idletasks()
    hud._set_result(_result(LONG_REPLY))
    hud.bar.update_idletasks()
    assert (hud.bar.winfo_x(), hud.bar.winfo_y()) == (50, 50)
