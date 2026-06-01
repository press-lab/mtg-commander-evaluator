import json

from openai import OpenAI

from mtg_evaluator.classification.base import BaseClassifier
from mtg_evaluator.classification.card_input import CardInput
from mtg_evaluator.classification.prompt import SYSTEM_PROMPT, build_user_prompt
from mtg_evaluator.classification.schema import CardClassification
from mtg_evaluator.classification.validator import validate_classification
from mtg_evaluator.config import settings


class DeepSeekClassifier(BaseClassifier):
    def __init__(self) -> None:
        self._client = OpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
        )
        self._model = settings.deepseek_model

    @property
    def version(self) -> str:
        return f"deepseek/{self._model}"

    def classify(self, card: CardInput) -> CardClassification:
        user_prompt = build_user_prompt(card)

        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
        )

        raw_text = response.choices[0].message.content or ""
        try:
            raw_dict = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"DeepSeek returned invalid JSON: {exc}\n\nRaw: {raw_text[:500]}") from exc

        raw_dict["oracle_id"] = card.oracle_id
        raw_dict["card_name"] = card.name

        is_valid, errors, classification = validate_classification(raw_dict)
        if not is_valid:
            raise ValueError(f"Classification failed validation for {card.name}: {errors}")

        return classification
