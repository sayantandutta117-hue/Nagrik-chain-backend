"""
Officer Integrity Score Service
================================
Computes a live integrity score for government officers based on:
- Rejection rate vs baseline (spike detection)
- Processing delay pattern (bribery proxy)
- Bribe report count
- Decoy application pass-rate
- Manual overturn rate (higher-authority reversals)

The score affects simulated promotion/salary flags and is stored
per-officer in the audit database for court-ready export.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


from sqlalchemy.orm import Session


from ..models import OfficerProfile




@dataclass
class IntegrityReport:
    officer_id: str
    integrity_score: float
    flags: list[str]
    recommended_action: str
    raw_signals: dict[str, Any]


class IntegrityScoreEngine:
    """
    Produces a 0-100 integrity score. Score starts at 100 and degrades
    on each signal of irregularity. Each penalty is bounded so a single
    bad signal cannot zero-out an otherwise clean officer.
    """

    REJECTION_SPIKE_THRESHOLD = 0.15   # delta above baseline triggers flag
    DELAY_BASELINE_HOURS = 48.0
    MAX_BRIBE_REPORTS_BEFORE_FLAG = 2
    DECOY_FAILURE_RATE_THRESHOLD = 0.10  # >10% decoy failures = flag
    OVERTURN_RATE_THRESHOLD = 0.05      # >5% decisions overturned = flag

    def compute(self, profile: OfficerProfile) -> IntegrityReport:
        score = 100.0
        flags: list[str] = []

        # -- Rejection spike penalty (max -30 pts) --
        delta = profile.rejection_rate_30d - profile.rejection_rate_baseline
        if delta > self.REJECTION_SPIKE_THRESHOLD:
            penalty = min(30.0, delta * 150)
            score -= penalty
            flags.append(
                f"Rejection rate {profile.rejection_rate_30d:.1%} is "
                f"{delta:.1%} above baseline — possible selective delay/bribery pattern"
            )

        # -- Processing delay penalty (max -20 pts) --
        if profile.average_delay_hours > self.DELAY_BASELINE_HOURS:
            excess_ratio = (profile.average_delay_hours - self.DELAY_BASELINE_HOURS) / self.DELAY_BASELINE_HOURS
            penalty = min(20.0, math.log1p(excess_ratio) * 15)
            score -= penalty
            flags.append(
                f"Average processing delay {profile.average_delay_hours:.1f}h "
                f"exceeds {self.DELAY_BASELINE_HOURS:.0f}h baseline"
            )

        # -- Bribe reports penalty (max -30 pts) --
        if profile.bribe_reports > 0:
            penalty = min(30.0, profile.bribe_reports * 12)
            score -= penalty
            flags.append(f"{profile.bribe_reports} encrypted bribe report(s) filed against this officer")

        # -- Decoy failure penalty (max -15 pts) --
        if profile.decoy_total > 0:
            decoy_rate = profile.decoy_failures / profile.decoy_total
            if decoy_rate > self.DECOY_FAILURE_RATE_THRESHOLD:
                penalty = min(15.0, decoy_rate * 60)
                score -= penalty
                flags.append(
                    f"Failed {profile.decoy_failures}/{profile.decoy_total} decoy applications "
                    f"({decoy_rate:.1%}) — indicates awareness of real vs decoy submissions"
                )

        # -- Manual overturn rate penalty (max -10 pts) --
        if profile.total_decisions > 0:
            overturn_rate = profile.manual_overturns / profile.total_decisions
            if overturn_rate > self.OVERTURN_RATE_THRESHOLD:
                penalty = min(10.0, overturn_rate * 80)
                score -= penalty
                flags.append(
                    f"{overturn_rate:.1%} of decisions overturned by higher authority "
                    f"({profile.manual_overturns}/{profile.total_decisions})"
                )

        score = max(0.0, round(score, 2))

        if score < 50:
            action = "SUSPEND_PENDING_INVESTIGATION"
        elif score < 70:
            action = "FLAG_FOR_SUPERVISOR_REVIEW"
        elif score < 85:
            action = "MONITOR_ELEVATED"
        else:
            action = "CLEAR"

        return IntegrityReport(
            officer_id=profile.officer_id,
            integrity_score=score,
            flags=flags,
            recommended_action=action,
            raw_signals={
                "rejection_rate_30d": profile.rejection_rate_30d,
                "rejection_rate_baseline": profile.rejection_rate_baseline,
                "average_delay_hours": profile.average_delay_hours,
                "bribe_reports": profile.bribe_reports,
                "decoy_failures": profile.decoy_failures,
                "decoy_total": profile.decoy_total,
                "manual_overturns": profile.manual_overturns,
                "total_decisions": profile.total_decisions,
            },
        )

    def update_and_score(self, db: Session, officer_id: str, signals: dict[str, Any]) -> IntegrityReport:
        profile = db.query(OfficerProfile).filter_by(officer_id=officer_id).first()
        if not profile:
            profile = OfficerProfile(officer_id=officer_id)
            db.add(profile)

        # Update signals using EMA-style blending for rates
        alpha = 0.2
        if "rejection_rate_30d" in signals:
            profile.rejection_rate_30d = signals["rejection_rate_30d"]
        if "rejection_rate_baseline" in signals:
            profile.rejection_rate_baseline = (
                (1 - alpha) * profile.rejection_rate_baseline
                + alpha * signals["rejection_rate_baseline"]
            )
        if "average_delay_hours" in signals:
            profile.average_delay_hours = (
                (1 - alpha) * profile.average_delay_hours
                + alpha * signals["average_delay_hours"]
            )
        if "bribe_report" in signals and signals["bribe_report"]:
            profile.bribe_reports += 1
        if "decoy_failure" in signals and signals["decoy_failure"]:
            profile.decoy_failures += 1
            profile.decoy_total += 1
        elif "decoy_pass" in signals and signals["decoy_pass"]:
            profile.decoy_total += 1
        if "manual_overturn" in signals and signals["manual_overturn"]:
            profile.manual_overturns += 1
        if "decision_made" in signals and signals["decision_made"]:
            profile.total_decisions += 1

        report = self.compute(profile)
        profile.integrity_score = report.integrity_score
        profile.flagged_for_audit = report.recommended_action != "CLEAR"
        profile.updated_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(profile)
        return report
