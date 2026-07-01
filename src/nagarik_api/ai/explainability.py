def explain_decision(decision: str, features: dict, reasons: list[str]) -> dict:
    ranked = sorted(features.items(), key=lambda item: abs(float(item[1])) if isinstance(item[1], (int, float)) else 0, reverse=True)
    return {
        "decision": decision,
        "reasons": reasons,
        "shap_ready_feature_importance": ranked[:10],
        "fairness_notes": [
            "No protected attribute is required for the deterministic eligibility rule path",
            "Production model decisions must be monitored for group-level drift",
        ],
    }
