from pydantic import ValidationError

from mtg_evaluator.classification.schema import CardClassification


def validate_classification(
    raw: dict,
) -> tuple[bool, list[str], CardClassification | None]:
    try:
        classification = CardClassification.model_validate(raw)
        return True, [], classification
    except ValidationError as exc:
        errors = [
            f"{' -> '.join(str(loc) for loc in e['loc'])}: {e['msg']}"
            for e in exc.errors()
        ]
        return False, errors, None
