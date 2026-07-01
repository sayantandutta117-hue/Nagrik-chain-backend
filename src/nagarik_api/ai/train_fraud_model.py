from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .fraud_features import FraudFeatureEngineer


def main() -> None:
    parser = argparse.ArgumentParser(description="Train Nagarik Chain fraud detection model")
    parser.add_argument("--dataset", required=True, help="CSV dataset matching fraud_dataset_schema.json")
    parser.add_argument("--out-dir", default="models/fraud", help="Model registry directory")
    parser.add_argument("--version", default=None, help="Model version, defaults to UTC timestamp")
    parser.add_argument("--backend", choices=("xgboost", "lightgbm"), default="xgboost")
    args = parser.parse_args()

    version = args.version or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = Path(args.out_dir) / version
    output_dir.mkdir(parents=True, exist_ok=True)

    artifact = train_model(Path(args.dataset), args.backend)
    joblib = _optional_import("joblib")
    joblib.dump(artifact["model"], output_dir / "model.joblib")
    metadata = {
        "version": version,
        "backend": args.backend,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "feature_names": artifact["feature_names"],
        "metrics": artifact["metrics"],
        "dataset": str(Path(args.dataset).resolve()),
    }
    (output_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    (Path(args.out_dir) / "latest").write_text(version, encoding="utf-8")
    print(json.dumps({"model_dir": str(output_dir), "metrics": artifact["metrics"]}, indent=2))


def train_model(dataset_path: Path, backend: str) -> dict[str, Any]:
    pandas = _optional_import("pandas")
    train_test_split = _optional_import("sklearn.model_selection").train_test_split
    metrics = _optional_import("sklearn.metrics")

    frame = pandas.read_csv(dataset_path)
    engineer = FraudFeatureEngineer()
    missing = set(engineer.dataset_columns) - set(frame.columns)
    if missing:
        raise ValueError(f"Dataset is missing required columns: {sorted(missing)}")

    rows = frame.to_dict(orient="records")
    x = engineer.transform_many(rows)
    y = frame["label"].astype(int).tolist()
    x_train, x_test, y_train, y_test = train_test_split(
        x,
        y,
        test_size=0.2,
        random_state=42,
        stratify=y if len(set(y)) > 1 else None,
    )
    model = _build_model(backend)
    model.fit(x_train, y_train)
    probabilities = model.predict_proba(x_test)[:, 1]
    predictions = [1 if probability >= 0.5 else 0 for probability in probabilities]
    return {
        "model": model,
        "feature_names": list(engineer.feature_names),
        "metrics": {
            "roc_auc": float(metrics.roc_auc_score(y_test, probabilities)) if len(set(y_test)) > 1 else None,
            "precision": float(metrics.precision_score(y_test, predictions, zero_division=0)),
            "recall": float(metrics.recall_score(y_test, predictions, zero_division=0)),
            "f1": float(metrics.f1_score(y_test, predictions, zero_division=0)),
        },
    }


def _build_model(backend: str) -> Any:
    if backend == "lightgbm":
        lightgbm = _optional_import("lightgbm")
        return lightgbm.LGBMClassifier(
            n_estimators=300,
            learning_rate=0.04,
            max_depth=-1,
            subsample=0.9,
            colsample_bytree=0.9,
            objective="binary",
            random_state=42,
        )
    xgboost = _optional_import("xgboost")
    return xgboost.XGBClassifier(
        n_estimators=300,
        learning_rate=0.04,
        max_depth=5,
        subsample=0.9,
        colsample_bytree=0.9,
        objective="binary:logistic",
        eval_metric="logloss",
        random_state=42,
    )


def _optional_import(module_name: str) -> Any:
    import importlib

    return importlib.import_module(module_name)


if __name__ == "__main__":
    main()
