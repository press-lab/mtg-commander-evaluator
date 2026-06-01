from __future__ import annotations

import json
from importlib.resources import files
from typing import Any


def _load_game_changer_data() -> dict[str, Any]:
    data_file = files("mtg_evaluator.data").joinpath("game_changers.json")
    return json.loads(data_file.read_text(encoding="utf-8"))


GAME_CHANGER_DATA = _load_game_changer_data()
GAME_CHANGERS: frozenset[str] = frozenset(GAME_CHANGER_DATA["cards"])
GAME_CHANGER_VERSION: str = GAME_CHANGER_DATA["version"]
GAME_CHANGER_SOURCE: str = GAME_CHANGER_DATA["source"]


def is_game_changer(name: str) -> bool:
    """Return True when the card name or front face is on the Game Changers list."""
    if name in GAME_CHANGERS:
        return True
    if " // " in name and name.split(" // ", 1)[0] in GAME_CHANGERS:
        return True
    return False
