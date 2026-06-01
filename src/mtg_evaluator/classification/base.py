from abc import ABC, abstractmethod

from mtg_evaluator.classification.card_input import CardInput
from mtg_evaluator.classification.schema import CardClassification


class BaseClassifier(ABC):
    @abstractmethod
    def classify(self, card: CardInput) -> CardClassification: ...

    @property
    @abstractmethod
    def version(self) -> str: ...
