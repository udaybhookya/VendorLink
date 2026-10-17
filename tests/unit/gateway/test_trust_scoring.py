"""Trust-score weights come from config/scoring/trust.json, not from code."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from tests.conftest import CONFIG_DIR
from trust_gateway.observability import trust_for
from vl_core.config import ConfigLoader
from vl_core.schemas import AgentCredential, AgentMetrics, SecurityEvent, TrustScoring

NOW = datetime(2026, 10, 16, tzinfo=UTC)
CRED = AgentCredential(
    agent_id="vendor-agent:maya",
    party_type="vendor",
    on_behalf_of="vendor:maya",
    public_key="AA==",
    issued_at=NOW,
)


def violation(n: int) -> SecurityEvent:
    return SecurityEvent(
        id=f"e{n}",
        ts=NOW,
        type="injection",
        agent_id=CRED.agent_id,
        evidence="",
        action_taken="blocked",
    )


def test_shipped_weights_are_the_model_defaults() -> None:
    assert ConfigLoader(CONFIG_DIR).trust_scoring() == TrustScoring(
        note=ConfigLoader(CONFIG_DIR).trust_scoring().note
    )


def test_weights_change_the_score() -> None:
    metrics = AgentMetrics(calls=10, errors=1)
    events = [violation(1), violation(2)]
    default, _ = trust_for(CRED, "active", metrics, events)
    assert default == 100 - 50 - 3  # 2 × 25 violations, 30 × 10 % errors
    strict, factors = trust_for(
        CRED, "active", metrics, events, TrustScoring(per_violation=40, max_violations=60)
    )
    assert strict == 100 - 60 - 3  # capped at max_violations
    assert {f.name for f in factors} == {"violations", "errors"}
    suspended, _ = trust_for(CRED, "suspended", AgentMetrics(), [], TrustScoring(suspended=10))
    assert suspended == 90


def test_bad_weights_file_is_reported(tmp_path: Path) -> None:
    (tmp_path / "scoring").mkdir()
    (tmp_path / "scoring" / "trust.json").write_text(json.dumps({"suspended": 500}))
    problems = ConfigLoader(tmp_path).validate_all()
    assert any("trust.json" in p for p in problems)
