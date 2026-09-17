"""Entry point: `python -m jarvis`."""
from __future__ import annotations

import argparse
import logging
import sys
import time

from jarvis import logging_setup
from jarvis.bus import BUS, RESULT
from jarvis.config import CONFIG_PATH, Config

log = logging.getLogger("jarvis")

BANNER = r"""
    __  ___    ____  _    __ _   _____
   |  |/   |  / __ \| |  / /| | / ___/     voice + text desktop control
   |     /| | / /_/ /| | / / | | \__ \     hotkey: {ptt}  |  stop: {stop}
  _|  |\/ | |/ _, _/ | |/ /  | |___/ /     say "jarvis" then your command
 /___/   |_/_/ |_|   |___/   |_/____/
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jarvis", description="A fast voice- and text-controlled desktop agent."
    )
    parser.add_argument("command", nargs="*", help="run one command and exit")
    parser.add_argument("--no-voice", action="store_true", help="disable the microphone")
    parser.add_argument("--no-speak", action="store_true", help="disable spoken replies")
    parser.add_argument("--no-ui", action="store_true", help="run headless in the terminal")
    parser.add_argument(
        "--always-on", action="store_true", help="act on every phrase, no wake word"
    )
    parser.add_argument("--brain", action="store_true", help="enable the LLM fallback")
    parser.add_argument("--log-level", default=None, help="DEBUG, INFO, WARNING")
    parser.add_argument(
        "--write-config", action="store_true", help="write a default config and exit"
    )
    parser.add_argument("--list", action="store_true", help="list known commands and exit")
    parser.add_argument(
        "--benchmark", action="store_true", help="time the router over sample phrases"
    )
    parser.add_argument(
        "--check-wake", action="store_true",
        help="listen and show the live wake-word score, to test your microphone",
    )
    return parser


def apply_flags(cfg: Config, args) -> None:
    if args.no_voice:
        cfg.speech.enabled = False
    if args.no_speak:
        cfg.voice_out.enabled = False
    if args.no_ui:
        cfg.ui.enabled = False
    if args.always_on:
        cfg.speech.always_on = True
    if args.brain:
        cfg.brain.enabled = True
    if args.log_level:
        cfg.log_level = args.log_level


def cmd_list() -> int:
    from jarvis.skills import load_all_skills
    from jarvis.skills.base import REGISTRY

    load_all_skills()
    width = max(len(i.name) for i in REGISTRY)
    for item in sorted(REGISTRY, key=lambda i: i.name):
        example = item.examples[0] if item.examples else item.name.replace("_", " ")
        print(f"{item.name:<{width}}  {example:<34}  {item.description}")
    print(f"\n{len(REGISTRY)} intents registered.")
    return 0


def cmd_benchmark() -> int:
    from jarvis.core.router import Router
    from jarvis.nlp.matcher import normalize
    from jarvis.skills.base import Context

    cfg = Config()
    ctx = Context(config=cfg, speak=lambda _t: None)
    router = Router(ctx)
    phrases = [
        "open chrome", "click", "double click", "scroll down 5", "type hello world",
        "search for python decorators", "volume 40", "minimize", "select all",
        "press ctrl s", "what time is it", "go to youtube", "this is not a command",
    ]
    print(f"{'phrase':<38} {'intent':<18} {'route µs':>9}")
    print("-" * 68)
    total = 0.0
    for phrase in phrases:
        # Time routing only, with the handler stubbed out via a dry normalise +
        # pattern sweep, so we measure dispatch and not the side effects.
        start = time.perf_counter()
        text = normalize(phrase)
        hit = next((i for i in router.intents if i.match(text)), None)
        micros = (time.perf_counter() - start) * 1e6
        total += micros
        print(f"{phrase:<38} {(hit.name if hit else '-'):<18} {micros:9.1f}")
    print("-" * 68)
    print(f"{'mean':<38} {'':<18} {total / len(phrases):9.1f}")
    return 0


def cmd_check_wake(cfg: Config, seconds: float = 20.0) -> int:
    """Show the wake-word score live, so a silent Jarvis can be diagnosed.

    When the detector does not fire there is nothing to see: no transcript, no
    HUD update, no log line. That is correct -- it is the whole point of the
    gate -- but it is indistinguishable from the app having crashed. This makes
    the gate visible for as long as you watch it.
    """
    import time

    import numpy as np
    import sounddevice as sd

    from jarvis.speech.wake import FRAME_SAMPLES, WakeWord

    detector = WakeWord(cfg.speech.wake_model, cfg.speech.wake_threshold)
    if not detector.start():
        print("The wake-word detector could not load. Is openwakeword installed?")
        return 1

    print(f"Listening for {cfg.speech.wake_model!r} for {seconds:.0f}s.")
    print(f"Say it a few times. It fires at {cfg.speech.wake_threshold:.2f}.")
    print()

    best = 0.0
    fired = 0
    deadline = time.monotonic() + seconds
    with sd.RawInputStream(
        samplerate=cfg.speech.sample_rate, blocksize=FRAME_SAMPLES,
        device=cfg.speech.device, dtype="int16", channels=1,
    ) as stream:
        while time.monotonic() < deadline:
            data, _overflowed = stream.read(FRAME_SAMPLES)
            frame = np.frombuffer(bytes(data), dtype=np.int16)
            score = max(detector._model.predict(frame).values())
            best = max(best, score)
            bar = "#" * int(score * 40)
            hit = "  <-- FIRED" if score >= detector.threshold else ""
            if score > 0.02 or hit:
                print(f"  {score:5.3f} |{bar:<40}|{hit}")
            if score >= detector.threshold:
                fired += 1
                detector.reset()

    print()
    print(f"best score {best:.3f}, fired {fired} time(s)")
    if fired:
        print("Working. Say it the same way to Jarvis.")
    elif best > cfg.speech.wake_threshold * 0.5:
        print("Close but under the line. Lower speech.wake_threshold in")
        print("~/.jarvis/config.json, or say it a little more distinctly.")
    else:
        print("Not detected. Check the microphone, or set speech.wake_engine")
        print('to "none" to go back to matching the wake word in the transcript.')
    return 0


def run_once(cfg: Config, words: list[str]) -> int:
    from jarvis.core.engine import Engine

    cfg.speech.enabled = False
    cfg.ui.enabled = False
    engine = Engine(cfg)
    engine.start()
    result = engine.submit(" ".join(words), source="cli")
    engine.stop()
    if result is None:
        return 1
    print(result.say or result.detail or ("ok" if result.ok else "failed"))
    return 0 if result.ok else 1


def run_headless(engine) -> int:
    """Terminal REPL for when the HUD is off (or Tk is unavailable)."""
    BUS.on(RESULT, lambda r: print(f"  {r.say or r.detail or ('ok' if r.ok else 'failed')}"))
    print("Type a command, or 'exit' to quit.")
    while engine.running:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if line.lower() in ("exit", "quit"):
            break
        if line:
            engine.submit(line, source="text")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.list:
        return cmd_list()
    if args.benchmark:
        logging_setup.setup("WARNING")
        return cmd_benchmark()

    cfg = Config.load()
    apply_flags(cfg, args)
    logging_setup.setup(cfg.log_level)

    if args.check_wake:
        return cmd_check_wake(cfg)

    if args.write_config:
        cfg.save()
        print(f"Wrote {CONFIG_PATH}")
        return 0

    if args.command:
        return run_once(cfg, args.command)

    if sys.stdout is not None:  # absent under pythonw, the silent launch path
        print(BANNER.format(ptt=cfg.hotkeys.push_to_talk, stop=cfg.hotkeys.panic_stop))

    from jarvis.core.engine import Engine

    engine = Engine(cfg)
    engine.start()

    try:
        if cfg.ui.enabled:
            try:
                from jarvis.ui.hud import HUD

                HUD(engine).run()
            except Exception:
                log.exception("HUD failed to start; falling back to the terminal")
                return run_headless(engine)
        else:
            return run_headless(engine)
    except KeyboardInterrupt:
        pass
    finally:
        engine.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
