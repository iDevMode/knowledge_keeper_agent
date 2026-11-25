"""The scripted-eval ratchet.

`evals/gate.json` holds, per persona, the scripted scores the current harness
achieves. CI fails if a change makes any of them worse. When a change makes
them better, regenerate it (`python -m evals.run_eval --update-gate`) and
commit the new file, so the improvement is locked in.

Only deterministic scripted scores belong here; live runs vary.
"""

import json
from pathlib import Path
from typing import Dict, List

from evals.scorecard import Scorecard

GATE_PATH = Path(__file__).resolve().parent / "gate.json"

# metric -> True if higher is better
GATED_METRICS: Dict[str, bool] = {
    "recall": True,
    "risk_recall": True,
    "probe_precision": True,
    "facts_per_employee_turn": True,
    "decline_respected": True,
    "core_reached_at_half": True,
    "core_reached": True,
    "single_question_compliance": True,
    "coverage": True,
    "risk_duplicate_rate": False,
    "redundant_question_rate": False,
    "non_answers_filed": False,
    "secrets_in_pack": False,
    "concerns_in_pack": False,
    "sensitive_in_pack": False,
}

_TOLERANCE = 1e-9


def snapshot(cards: List[Scorecard]) -> Dict[str, Dict[str, float]]:
    return {
        c.persona_id: {
            m: getattr(c, m) for m in GATED_METRICS if getattr(c, m) is not None
        }
        for c in cards
    }


def write_gate(cards: List[Scorecard]) -> None:
    GATE_PATH.write_text(json.dumps(snapshot(cards), indent=2, sort_keys=True) + "\n")


def regressions(cards: List[Scorecard]) -> List[str]:
    """Human-readable list of metrics that got worse than the gate."""
    if not GATE_PATH.exists():
        return [f"{GATE_PATH.name} is missing — run `python -m evals.run_eval --update-gate`"]
    gate = json.loads(GATE_PATH.read_text())
    problems = []
    for persona_id, current in snapshot(cards).items():
        expected = gate.get(persona_id)
        if expected is None:
            problems.append(f"{persona_id}: not in the gate — run --update-gate")
            continue
        for metric, floor in expected.items():
            value = current.get(metric)
            if value is None:
                problems.append(f"{persona_id}.{metric}: no longer reported (was {floor})")
                continue
            higher_is_better = GATED_METRICS[metric]
            worse = value < floor - _TOLERANCE if higher_is_better else value > floor + _TOLERANCE
            if worse:
                problems.append(f"{persona_id}.{metric}: {value:.3f} is worse than the gate's {floor:.3f}")
    return problems
