import enum
from datetime import datetime, date
from typing import Optional

from sqlalchemy import (
    String, Text, Integer, Boolean, Numeric, DateTime, Date,
    ForeignKey, Enum as SAEnum, text, UniqueConstraint, Index,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class IngestionStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class JobStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class CardIngestionRun(Base):
    __tablename__ = "card_ingestion_runs"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    bulk_type: Mapped[str] = mapped_column(String(64), nullable=False)
    card_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[IngestionStatus] = mapped_column(
        SAEnum(IngestionStatus, name="ingestion_status"), nullable=False,
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    raw_cards: Mapped[list["RawScryfallCard"]] = relationship(back_populates="ingestion_run")


class RawScryfallCard(Base):
    __tablename__ = "raw_scryfall_cards"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    oracle_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    scryfall_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    ingestion_run_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("card_ingestion_runs.id"), nullable=False,
    )
    raw_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    oracle_text_checksum: Mapped[str] = mapped_column(String(64), nullable=False)

    ingestion_run: Mapped["CardIngestionRun"] = relationship(back_populates="raw_cards")

    __table_args__ = (
        Index("ix_raw_scryfall_cards_run_oracle", "ingestion_run_id", "oracle_id"),
    )


class Card(Base):
    __tablename__ = "cards"

    oracle_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    scryfall_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    mana_cost: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cmc: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    type_line: Mapped[str] = mapped_column(Text, nullable=False)
    oracle_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    colors: Mapped[list] = mapped_column(ARRAY(String), nullable=False, server_default="{}")
    color_identity: Mapped[list] = mapped_column(ARRAY(String), nullable=False, server_default="{}")
    power: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    toughness: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    loyalty: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    is_legendary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_creature: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_planeswalker: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_land: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_instant: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_sorcery: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    layout: Mapped[str] = mapped_column(String(32), nullable=False)
    rarity: Mapped[str] = mapped_column(String(16), nullable=False)
    set_code: Mapped[str] = mapped_column(String(8), nullable=False)
    scryfall_uri: Mapped[str] = mapped_column(Text, nullable=False)
    image_uri: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    oracle_text_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    legalities: Mapped[list["CardLegality"]] = relationship(back_populates="card")
    faces: Mapped[list["CardFace"]] = relationship(back_populates="card", order_by="CardFace.face_index")
    keywords: Mapped[list["CardKeyword"]] = relationship(back_populates="card")
    classification_jobs: Mapped[list["CardClassificationJob"]] = relationship(back_populates="card")
    classifications: Mapped[list["CardClassification"]] = relationship(back_populates="card")


class CardLegality(Base):
    __tablename__ = "card_legalities"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    oracle_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("cards.oracle_id"), nullable=False,
    )
    format: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)

    card: Mapped["Card"] = relationship(back_populates="legalities")

    __table_args__ = (UniqueConstraint("oracle_id", "format", name="uq_card_legalities_oracle_format"),)


class CardFace(Base):
    __tablename__ = "card_faces"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    oracle_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("cards.oracle_id"), nullable=False,
    )
    face_index: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    mana_cost: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    type_line: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    oracle_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    power: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    toughness: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    loyalty: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    colors: Mapped[list] = mapped_column(ARRAY(String), nullable=False, server_default="{}")

    card: Mapped["Card"] = relationship(back_populates="faces")

    __table_args__ = (UniqueConstraint("oracle_id", "face_index", name="uq_card_faces_oracle_face"),)


class CardKeyword(Base):
    __tablename__ = "card_keywords"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    oracle_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("cards.oracle_id"), nullable=False,
    )
    keyword: Mapped[str] = mapped_column(Text, nullable=False)

    card: Mapped["Card"] = relationship(back_populates="keywords")

    __table_args__ = (UniqueConstraint("oracle_id", "keyword", name="uq_card_keywords_oracle_keyword"),)


class CardClassificationJob(Base):
    __tablename__ = "card_classification_jobs"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    oracle_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("cards.oracle_id"), nullable=False, index=True,
    )
    status: Mapped[JobStatus] = mapped_column(
        SAEnum(JobStatus, name="job_status"), nullable=False, default=JobStatus.pending,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    oracle_text_checksum: Mapped[str] = mapped_column(String(64), nullable=False)

    card: Mapped["Card"] = relationship(back_populates="classification_jobs")
    classification: Mapped[Optional["CardClassification"]] = relationship(back_populates="job")


class CardClassification(Base):
    __tablename__ = "card_classifications"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    oracle_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("cards.oracle_id"), nullable=False, index=True,
    )
    job_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("card_classification_jobs.id"), nullable=False,
    )
    classified_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    classifier_version: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_output: Mapped[dict] = mapped_column(JSONB, nullable=False)
    is_valid: Mapped[bool] = mapped_column(Boolean, nullable=False)
    validation_errors: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    card: Mapped["Card"] = relationship(back_populates="classifications")
    job: Mapped["CardClassificationJob"] = relationship(back_populates="classification")
    functions: Mapped[list["CardFunction"]] = relationship(back_populates="classification")
    archetype_scores: Mapped[list["CardArchetypeScore"]] = relationship(back_populates="classification")
    power_scores: Mapped[list["CardPowerScore"]] = relationship(back_populates="classification")
    bracket_scores: Mapped[list["CardBracketScore"]] = relationship(back_populates="classification")
    synergy_hooks: Mapped[list["CardSynergyHook"]] = relationship(back_populates="classification")


class CardFunction(Base):
    __tablename__ = "card_functions"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    oracle_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("cards.oracle_id"), nullable=False, index=True,
    )
    classification_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("card_classifications.id"), nullable=False,
    )
    function_name: Mapped[str] = mapped_column(String(64), nullable=False)

    classification: Mapped["CardClassification"] = relationship(back_populates="functions")


class CardArchetypeScore(Base):
    __tablename__ = "card_archetype_scores"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    oracle_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("cards.oracle_id"), nullable=False, index=True,
    )
    classification_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("card_classifications.id"), nullable=False,
    )
    archetype: Mapped[str] = mapped_column(String(64), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)

    classification: Mapped["CardClassification"] = relationship(back_populates="archetype_scores")


class CardPowerScore(Base):
    __tablename__ = "card_power_scores"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    oracle_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("cards.oracle_id"), nullable=False, index=True,
    )
    classification_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("card_classifications.id"), nullable=False,
    )
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)

    classification: Mapped["CardClassification"] = relationship(back_populates="power_scores")


class CardBracketScore(Base):
    __tablename__ = "card_bracket_scores"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    oracle_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("cards.oracle_id"), nullable=False, index=True,
    )
    classification_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("card_classifications.id"), nullable=False,
    )
    bracket_level: Mapped[str] = mapped_column(String(16), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)

    classification: Mapped["CardClassification"] = relationship(back_populates="bracket_scores")


class CardSynergyHook(Base):
    __tablename__ = "card_synergy_hooks"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    oracle_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("cards.oracle_id"), nullable=False, index=True,
    )
    classification_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("card_classifications.id"), nullable=False,
    )
    hook: Mapped[str] = mapped_column(String(128), nullable=False)

    classification: Mapped["CardClassification"] = relationship(back_populates="synergy_hooks")


class Archetype(Base):
    __tablename__ = "archetypes"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    typical_colors: Mapped[list] = mapped_column(ARRAY(String), nullable=False, server_default="{}")
    key_mechanics: Mapped[list] = mapped_column(ARRAY(String), nullable=False, server_default="{}")


class CommanderRulesContext(Base):
    __tablename__ = "commander_rules_context"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    rule_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    rule_value: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)


class BracketContext(Base):
    __tablename__ = "bracket_context"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    bracket_level: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    philosophy: Mapped[str] = mapped_column(Text, nullable=False)
    key_signals: Mapped[dict] = mapped_column(JSONB, nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)


class Decklist(Base):
    __tablename__ = "decklists"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    commander_oracle_id: Mapped[Optional[str]] = mapped_column(
        UUID(as_uuid=False), ForeignKey("cards.oracle_id"), nullable=True,
    )
    partner_oracle_id: Mapped[Optional[str]] = mapped_column(
        UUID(as_uuid=False), ForeignKey("cards.oracle_id"), nullable=True,
    )
    colors: Mapped[list] = mapped_column(ARRAY(String), nullable=False, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    raw_list: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    deck_cards: Mapped[list["DeckCard"]] = relationship(back_populates="decklist")
    evaluations: Mapped[list["DeckEvaluation"]] = relationship(back_populates="decklist")


class DeckCard(Base):
    __tablename__ = "deck_cards"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    decklist_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("decklists.id"), nullable=False,
    )
    oracle_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("cards.oracle_id"), nullable=False,
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_commander: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    decklist: Mapped["Decklist"] = relationship(back_populates="deck_cards")


class EDHRecCardStats(Base):
    __tablename__ = "edhrec_card_stats"

    oracle_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("cards.oracle_id"), primary_key=True,
    )
    card_name: Mapped[str] = mapped_column(Text, nullable=False)
    num_decks: Mapped[int] = mapped_column(Integer, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class DeckEvaluation(Base):
    __tablename__ = "deck_evaluations"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    decklist_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("decklists.id"), nullable=False,
    )
    evaluated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    bracket_estimate: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    archetype_guess: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    legality_issues: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    structural_notes: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    synergy_score: Mapped[Optional[float]] = mapped_column(Numeric(4, 2), nullable=True)
    improvement_suggestions: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")

    decklist: Mapped["Decklist"] = relationship(back_populates="evaluations")


class SpellbookCombo(Base):
    __tablename__ = "spellbook_combos"

    spellbook_id: Mapped[str] = mapped_column(Text, primary_key=True)
    card_count: Mapped[int] = mapped_column(Integer, nullable=False)
    bracket_tag: Mapped[Optional[str]] = mapped_column(String(4), nullable=True)
    is_commander_legal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    results_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    cards: Mapped[list["SpellbookComboCard"]] = relationship(back_populates="combo")


class SpellbookComboCard(Base):
    __tablename__ = "spellbook_combo_cards"

    id: Mapped[str] = mapped_column(Text, primary_key=True,
                                    server_default=text("gen_random_uuid()::text"))
    combo_id: Mapped[str] = mapped_column(Text, ForeignKey("spellbook_combos.spellbook_id",
                                                            ondelete="CASCADE"), nullable=False,
                                          index=True)
    oracle_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True, index=True)
    card_name: Mapped[str] = mapped_column(Text, nullable=False)

    combo: Mapped["SpellbookCombo"] = relationship(back_populates="cards")
