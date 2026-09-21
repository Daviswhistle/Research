from __future__ import annotations

from research_pipeline.models import ResearchCandidate, ResearchQuery


def _number(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _unit(value: object) -> float:
    return min(max(_number(value), 0.0), 1.0)


class OpportunityAxisScorer:
    """Score separate axes without pretending they form a single objective."""

    name = "opportunity_axes"

    def score(self, query: ResearchQuery, candidate: ResearchCandidate):
        del query
        payload = candidate.payload
        facts = payload.get("facts", {})
        hypotheses = payload.get("hypotheses", {})
        validation = payload.get("validation", {})
        risks = payload.get("risks", {})

        signals = max(_number(facts.get("independent_revenue_signals")), 0.0)
        evidence_quality = min(signals / 4.0, 1.0)

        cash = max(_number(validation.get("cash_usd")), 0.0)
        hours = max(_number(validation.get("human_hours")), 0.0)
        days = max(_number(validation.get("feedback_days")), 0.0)

        cash_ease = 1.0 / (1.0 + cash / 100.0)
        human_ease = 1.0 / (1.0 + hours / 8.0)
        feedback_speed = 1.0 / (1.0 + days / 7.0)

        capital_risk = _unit(risks.get("capital_at_risk"))
        platform_risk = _unit(risks.get("platform_policy"))
        legal_risk = _unit(risks.get("legal"))
        safety = 1.0 - max(capital_risk, platform_risk, legal_risk)

        demand_strength = _unit(hypotheses.get("demand_strength"))
        automation_fit = _unit(hypotheses.get("automation_fit"))
        repeatability = _unit(hypotheses.get("repeatability"))
        distribution_access = _unit(hypotheses.get("distribution_access"))
        upside_convexity = _unit(hypotheses.get("upside_convexity"))

        feasibility_floor = min(
            demand_strength,
            evidence_quality,
            automation_fit,
            safety,
        )

        return {
            "demand_strength": demand_strength,
            "evidence_quality": evidence_quality,
            "automation_fit": automation_fit,
            "repeatability": repeatability,
            "distribution_access": distribution_access,
            "upside_convexity": upside_convexity,
            "cash_ease": cash_ease,
            "human_ease": human_ease,
            "feedback_speed": feedback_speed,
            "safety": safety,
            "feasibility_floor": feasibility_floor,
        }
