from app.models import WikiNode
from app.schemas import ScoringResponse


DEFAULT_WEIGHTS = {
    "credibility_score": 30.0,
    "parse_quality": 20.0,
    "financial_reconciliation": 25.0,
    "has_ticker": 10.0,
    "has_related": 15.0,
}


def evaluate_value_score(node: WikiNode, weights: dict[str, float] | None = None) -> ScoringResponse:
    config = {**DEFAULT_WEIGHTS, **(weights or {})}
    meta = node.yaml_meta
    score = 0.0
    rationale: list[str] = []

    credibility = float(meta.get("credibility_score", 0.0))
    score += credibility * config["credibility_score"]
    rationale.append(f"Source credibility contributes {credibility:.2f}.")

    parse_quality = meta.get("parse_quality")
    quality_factor = 1.0 if parse_quality in {"ok", "skipped"} else 0.35
    score += quality_factor * config["parse_quality"]
    rationale.append(f"Parse quality is {parse_quality}.")

    reconciliation = meta.get("financial_reconciliation", {}).get("status")
    reconciliation_factor = 1.0 if reconciliation in {"ok", "skipped"} else 0.25
    score += reconciliation_factor * config["financial_reconciliation"]
    rationale.append(f"Financial reconciliation status is {reconciliation}.")

    if meta.get("ticker"):
        score += config["has_ticker"]
        rationale.append("Ticker detected.")
    if meta.get("related"):
        score += config["has_related"]
        rationale.append("Related links are present.")

    final_score = round(min(100.0, score), 2)
    return ScoringResponse(
        node_id=node.id,
        score=final_score,
        grade=_grade(final_score),
        rationale=rationale,
        config_snapshot=config,
    )


def _grade(score: float) -> str:
    if score >= 85:
        return "A"
    if score >= 70:
        return "B"
    if score >= 55:
        return "C"
    return "D"

