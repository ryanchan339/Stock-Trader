from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass

import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import accuracy_score, precision_score, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from stock_trader.features import FEATURE_COLUMNS


@dataclass(frozen=True)
class ModelEvaluation:
    accuracy: float
    precision: float
    roc_auc: float
    train_rows: int
    test_rows: int

    def as_dict(self) -> dict[str, float | int]:
        return {
            "accuracy": self.accuracy,
            "precision": self.precision,
            "roc_auc": self.roc_auc,
            "train_rows": self.train_rows,
            "test_rows": self.test_rows,
        }


def build_model(random_state: int = 42) -> Pipeline:
    return Pipeline(
        steps=[
            ("scale", StandardScaler()),
            (
                "classifier",
                HistGradientBoostingClassifier(
                    learning_rate=0.05,
                    max_iter=250,
                    max_leaf_nodes=15,
                    l2_regularization=0.1,
                    random_state=random_state,
                ),
            ),
        ]
    )


def fit_model(model: Pipeline, train: pd.DataFrame) -> Pipeline:
    with open(os.devnull, "w", encoding="utf-8") as devnull:
        with contextlib.redirect_stderr(devnull):
            model.fit(train[FEATURE_COLUMNS], train["target_outperform_spy"])
    return model


def score_frame(model: Pipeline, frame: pd.DataFrame) -> pd.DataFrame:
    scored = frame[["date", "symbol", "close", "target_outperform_spy", "future_return"]].copy()
    scored["score"] = model.predict_proba(frame[FEATURE_COLUMNS])[:, 1]
    return scored


def evaluate_scores(test: pd.DataFrame, scores: pd.Series) -> ModelEvaluation:
    predictions = (scores >= 0.5).astype(int)
    return ModelEvaluation(
        accuracy=float(accuracy_score(test["target_outperform_spy"], predictions)),
        precision=float(precision_score(test["target_outperform_spy"], predictions, zero_division=0)),
        roc_auc=float(roc_auc_score(test["target_outperform_spy"], scores)),
        train_rows=0,
        test_rows=int(len(test)),
    )
