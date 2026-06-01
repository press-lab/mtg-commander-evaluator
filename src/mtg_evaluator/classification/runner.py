import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert

from mtg_evaluator.classification.base import BaseClassifier
from mtg_evaluator.classification.card_input import CardInput
from mtg_evaluator.classification.schema import CardClassification
from mtg_evaluator.classification.validator import validate_classification
from mtg_evaluator.config import settings
from mtg_evaluator.db.connection import get_session
from mtg_evaluator.db.models import (
    Card,
    CardClassificationJob,
    CardClassification as DBClassification,
    CardFunction,
    CardArchetypeScore,
    CardPowerScore,
    CardBracketScore,
    CardSynergyHook,
    EDHRecCardStats,
    JobStatus,
)


@dataclass
class RunStats:
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0


def get_classifier() -> BaseClassifier:
    provider = settings.classifier.lower()
    if provider == "deepseek":
        from mtg_evaluator.classification.anthropic_classifier import (
            AnthropicClassifier,
        )

        return AnthropicClassifier(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_anthropic_base_url,
            model=settings.deepseek_model,
        )
    elif provider == "anthropic":
        from mtg_evaluator.classification.anthropic_classifier import (
            AnthropicClassifier,
        )

        return AnthropicClassifier()
    elif provider == "stub":
        from mtg_evaluator.classification.stub_classifier import StubClassifier

        return StubClassifier()
    else:
        raise ValueError(
            f"Unknown classifier: {provider!r}. Use 'deepseek', 'anthropic', or 'stub'."
        )


def _card_to_input(card: Card) -> CardInput:
    return CardInput(
        oracle_id=card.oracle_id,
        name=card.name,
        type_line=card.type_line,
        oracle_text=card.oracle_text or "",
        mana_cost=card.mana_cost or "",
        cmc=float(card.cmc),
        colors=list(card.colors or []),
        color_identity=list(card.color_identity or []),
        power=card.power,
        toughness=card.toughness,
        loyalty=card.loyalty,
        keywords=[kw.keyword for kw in card.keywords],
    )


def _store_classification(
    session: Session,
    job: CardClassificationJob,
    classification: CardClassification,
    classifier_version: str,
    raw_dict: dict,
    is_valid: bool,
    errors: list[str] | None = None,
) -> None:
    now = datetime.utcnow()

    db_cls = DBClassification(
        oracle_id=job.oracle_id,
        job_id=job.id,
        classified_at=now,
        classifier_version=classifier_version,
        raw_output=raw_dict,
        is_valid=is_valid,
        validation_errors={"errors": errors} if errors else None,
    )
    session.add(db_cls)
    session.flush()

    if is_valid and classification:
        for fn in classification.functions:
            session.add(
                CardFunction(
                    oracle_id=job.oracle_id,
                    classification_id=db_cls.id,
                    function_name=fn,
                )
            )

        archetype_dict = classification.archetype_fit.model_dump(exclude_none=True)
        for archetype, score in archetype_dict.items():
            session.add(
                CardArchetypeScore(
                    oracle_id=job.oracle_id,
                    classification_id=db_cls.id,
                    archetype=archetype,
                    score=score,
                )
            )

        role_dict = classification.role_power.model_dump(exclude_none=True)
        for role, score in role_dict.items():
            session.add(
                CardPowerScore(
                    oracle_id=job.oracle_id,
                    classification_id=db_cls.id,
                    role=role,
                    score=score,
                )
            )
        session.add(
            CardPowerScore(
                oracle_id=job.oracle_id,
                classification_id=db_cls.id,
                role="general",
                score=classification.general_commander_power,
            )
        )

        bracket_dict = classification.bracket_fit.model_dump(exclude_none=True)
        for bracket, score in bracket_dict.items():
            session.add(
                CardBracketScore(
                    oracle_id=job.oracle_id,
                    classification_id=db_cls.id,
                    bracket_level=bracket,
                    score=score,
                )
            )

        for hook in classification.commander_context_notes:
            session.add(
                CardSynergyHook(
                    oracle_id=job.oracle_id,
                    classification_id=db_cls.id,
                    hook=hook,
                )
            )

    job.status = JobStatus.completed
    job.completed_at = now


def _process_job(
    job_id: str, classifier: BaseClassifier, reclassify: bool
) -> tuple[str, bool, str]:
    """Returns (job_id, success, message). Runs in a thread."""
    for attempt in range(settings.classifier_retry_attempts):
        try:
            with get_session() as session:
                job = session.get(CardClassificationJob, job_id)
                if job is None:
                    return job_id, False, "job not found"

                if not reclassify:
                    existing = session.scalars(
                        select(DBClassification)
                        .where(DBClassification.oracle_id == job.oracle_id)
                        .where(DBClassification.is_valid == True)  # noqa: E712
                    ).first()
                    if existing:
                        job.status = JobStatus.completed
                        return job_id, True, "skipped (already classified)"

                card = session.get(Card, job.oracle_id)
                if card is None:
                    job.status = JobStatus.failed
                    job.error_message = "card not found in cards table"
                    return job_id, False, "card not found"

                job.status = JobStatus.running
                job.started_at = datetime.utcnow()
                job.attempt_count += 1
                session.flush()

                card_input = _card_to_input(card)

            classification = classifier.classify(card_input)
            raw_dict = classification.model_dump()
            is_valid, errors, _ = validate_classification(raw_dict)

            with get_session() as session:
                job = session.get(CardClassificationJob, job_id)
                _store_classification(
                    session,
                    job,
                    classification,
                    classifier.version,
                    raw_dict,
                    is_valid,
                    errors,
                )

            return job_id, True, "ok"

        except Exception as exc:
            if attempt < settings.classifier_retry_attempts - 1:
                time.sleep(settings.classifier_retry_delay_s * (2**attempt))
                continue

            try:
                with get_session() as session:
                    job = session.get(CardClassificationJob, job_id)
                    if job:
                        job.status = JobStatus.failed
                        job.error_message = str(exc)[:1000]
                        job.completed_at = datetime.utcnow()
            except Exception:
                pass

            return job_id, False, str(exc)[:200]

    return job_id, False, "max retries exceeded"


def run_classification(
    limit: int | None = None,
    reclassify: bool = False,
    dry_run: bool = False,
    edhrec_only: bool = True,
) -> RunStats:
    stats = RunStats()

    with get_session() as session:
        query = (
            select(CardClassificationJob)
            .where(
                CardClassificationJob.status.in_([JobStatus.pending, JobStatus.failed])
            )
            .order_by(CardClassificationJob.created_at)
        )
        if edhrec_only:
            query = query.where(
                CardClassificationJob.oracle_id.in_(select(EDHRecCardStats.oracle_id))
            )
        if limit:
            query = query.limit(limit)
        jobs = session.scalars(query).all()
        job_ids = [j.id for j in jobs]
        stats.total = len(job_ids)

    classifier = get_classifier()

    if dry_run:
        print(f"Dry run: would process {stats.total} jobs with {classifier.version}")
        avg_input_tokens = 650
        avg_output_tokens = 280
        total_input = stats.total * avg_input_tokens
        total_output = stats.total * avg_output_tokens
        print(f"Estimated tokens: {total_input:,} input, {total_output:,} output")
        if "deepseek" in classifier.version:
            cost = (total_input / 1_000_000 * 0.14) + (total_output / 1_000_000 * 0.28)
            print(f"Estimated cost (DeepSeek Flash, no cache): ${cost:.2f}")
            cost_cached = (total_input / 1_000_000 * 0.003) + (
                total_output / 1_000_000 * 0.28
            )
            print(f"Estimated cost (DeepSeek Flash, with cache): ${cost_cached:.2f}")
        elif "anthropic" in classifier.version:
            if "haiku" in classifier.version:
                cost = (total_input / 1_000_000 * 0.80) + (
                    total_output / 1_000_000 * 4.0
                )
            else:
                cost = (total_input / 1_000_000 * 3.0) + (
                    total_output / 1_000_000 * 15.0
                )
            print(f"Estimated cost: ${cost:.2f}")
        return stats

    print(
        f"Starting classification: {stats.total} jobs | classifier: {classifier.version} | workers: {settings.classifier_workers}"
    )

    with ThreadPoolExecutor(max_workers=settings.classifier_workers) as pool:
        futures = {
            pool.submit(_process_job, job_id, classifier, reclassify): job_id
            for job_id in job_ids
        }

        for i, future in enumerate(as_completed(futures), 1):
            job_id, success, message = future.result()
            if success:
                if "skipped" in message:
                    stats.skipped += 1
                else:
                    stats.succeeded += 1
            else:
                stats.failed += 1

            if i % 100 == 0 or i == stats.total:
                print(
                    f"  [{i}/{stats.total}] ok={stats.succeeded} skip={stats.skipped} fail={stats.failed}"
                )

    return stats
