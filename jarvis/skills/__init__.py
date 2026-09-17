"""Skill package. Importing a module registers its intents as a side effect."""
from __future__ import annotations

import importlib
import logging
import pkgutil

log = logging.getLogger("jarvis.skills")

_loaded = False
# Order matters only for readability; the router sorts by priority afterwards.
_SKILL_MODULES = (
    "meta",
    "input_control",
    "apps",
    "window",
    "web",
    "system",
    "files",
    "text_edit",
)


def load_all_skills() -> None:
    global _loaded
    if _loaded:
        return
    for name in _SKILL_MODULES:
        importlib.import_module(f"jarvis.skills.{name}")
    # Pick up any user-dropped skill modules that aren't in the list above.
    for mod in pkgutil.iter_modules(__path__):
        if mod.name not in _SKILL_MODULES and mod.name != "base":
            importlib.import_module(f"jarvis.skills.{mod.name}")
    _loaded = True
    log.debug("skills loaded")
