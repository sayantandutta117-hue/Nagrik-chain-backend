from ..ai.explainability import explain_decision


class EligibilityEngine:
    def decide(self, scheme: str, facts: dict) -> dict:
        reasons: list[str] = []
        decision = "manual_review"
        confidence = 0.55

        if scheme == "scholarship":
            if facts.get("student") and facts.get("annual_income", 99999999) <= 250000:
                decision = "auto_approve"
                confidence = 0.91
                reasons.append("Student status verified and income is within scholarship threshold")
            else:
                decision = "reject"
                confidence = 0.78
                reasons.append("Scholarship facts did not satisfy mandatory rules")
        elif scheme == "pension":
            if facts.get("age", 0) >= 60 and not facts.get("death_registered", False):
                decision = "auto_approve"
                confidence = 0.93
                reasons.append("Age threshold met and no death certificate is registered")
            else:
                reasons.append("Pension requires age >= 60 and active life status")
        elif scheme == "crop_insurance":
            if facts.get("satellite_drought_index", 0) >= 0.7:
                decision = "auto_approve"
                confidence = 0.88
                reasons.append("Satellite drought index crossed payout threshold")
            else:
                decision = "assisted_review"
                confidence = 0.64
                reasons.append("Drought signal is below automatic payout threshold")
        else:
            reasons.append("Scheme is not configured for automatic decision")

        return {
            "decision": decision,
            "confidence": confidence,
            "reasons": reasons,
            "explainability": explain_decision(decision, facts, reasons),
        }
