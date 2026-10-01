"""Dependency-free TF-IDF + multinomial logistic regression spend classifier baseline."""

from __future__ import annotations

import math
import re
from collections import Counter
from random import Random

TokenVector = dict[int, float]
_TOKEN = re.compile(r"[a-z][a-z0-9]+")


class TfidfLogisticClassifier:
    """Small sparse SGD implementation for reproducible offline baseline experiments."""

    def __init__(self, *, epochs: int = 40, learning_rate: float = 0.08, l2: float = 0.0005) -> None:
        self.epochs = epochs
        self.learning_rate = learning_rate
        self.l2 = l2
        self.classes: list[str] = []
        self.vocabulary: dict[str, int] = {}
        self.idf: list[float] = []
        self.weights: list[list[float]] = []

    @staticmethod
    def _tokens(text: str) -> list[str]:
        return _TOKEN.findall(text.casefold())

    def _vector(self, text: str) -> TokenVector:
        counts = Counter(self._tokens(text))
        vector = {
            self.vocabulary[token]: (1 + math.log(count)) * self.idf[self.vocabulary[token]]
            for token, count in counts.items()
            if token in self.vocabulary
        }
        norm = math.sqrt(sum(value * value for value in vector.values()))
        return {index: value / norm for index, value in vector.items()} if norm else {}

    def fit(self, texts: list[str], labels: list[str], *, seed: int = 17) -> TfidfLogisticClassifier:
        if not texts or len(texts) != len(labels) or len(set(labels)) < 2:
            raise ValueError("Training needs equally sized text/label lists and at least two classes")
        self.classes = sorted(set(labels))
        doc_frequency = Counter(token for text in texts for token in set(self._tokens(text)))
        self.vocabulary = {token: index for index, token in enumerate(sorted(doc_frequency))}
        count = len(texts)
        self.idf = [math.log((1 + count) / (1 + doc_frequency[token])) + 1 for token in self.vocabulary]
        vectors = [self._vector(text) for text in texts]
        label_indexes = [self.classes.index(label) for label in labels]
        class_count, feature_count = len(self.classes), len(self.vocabulary)
        self.weights = [[0.0] * feature_count for _ in self.classes]
        bias = [0.0] * class_count
        rng = Random(seed)  # noqa: S311 - deterministic experiment shuffling, not cryptography
        for epoch in range(self.epochs):
            order = list(range(count))
            rng.shuffle(order)
            rate = self.learning_rate / (1 + epoch * 0.025)
            for row_index in order:
                vector = vectors[row_index]
                logits = [
                    bias[c] + sum(self.weights[c][i] * v for i, v in vector.items())
                    for c in range(class_count)
                ]
                peak = max(logits)
                exp_logits = [math.exp(value - peak) for value in logits]
                total = sum(exp_logits)
                probabilities = [value / total for value in exp_logits]
                for class_index in range(class_count):
                    error = probabilities[class_index] - (class_index == label_indexes[row_index])
                    bias[class_index] -= rate * error
                    weights = self.weights[class_index]
                    for feature_index, value in vector.items():
                        weights[feature_index] -= rate * (error * value + self.l2 * weights[feature_index])
        return self

    def predict_proba(self, text: str) -> dict[str, float]:
        if not self.weights:
            raise RuntimeError("Fit the classifier before prediction")
        vector = self._vector(text)
        logits = [sum(self.weights[c][i] * v for i, v in vector.items()) for c in range(len(self.classes))]
        peak = max(logits)
        exp_logits = [math.exp(value - peak) for value in logits]
        total = sum(exp_logits)
        return {label: score / total for label, score in zip(self.classes, exp_logits, strict=True)}

    def predict(self, text: str) -> str:
        probabilities = self.predict_proba(text)
        return max(probabilities, key=lambda label: probabilities[label])
