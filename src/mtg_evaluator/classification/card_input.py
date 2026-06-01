from dataclasses import dataclass, field


@dataclass
class CardInput:
    oracle_id: str
    name: str
    type_line: str
    oracle_text: str
    mana_cost: str = ""
    cmc: float = 0.0
    colors: list[str] = field(default_factory=list)
    color_identity: list[str] = field(default_factory=list)
    power: str | None = None
    toughness: str | None = None
    loyalty: str | None = None
    keywords: list[str] = field(default_factory=list)
