"""Headless Stage 2 interview runner.

Drives the real compiled LangGraph — the same graph the API serves — turn by
turn against a simulated employee, recording a full transcript plus per-turn
metadata (phase, block, follow-up or not, which planted facts the answer
revealed). Scripted mode swaps the LLMs for deterministic fakes; live mode
leaves the real models in place.
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage

from api.session_manager import InMemorySessionStore, set_session_store
from config import llm_provider
from evals.employee import LiveEmployee, ScriptedEmployee
from evals.fakes import FakeClassifierLLM, FakePrimaryLLM
from evals.personas import Persona
from models.role_intelligence_profile import RoleIntelligenceProfile

logger = logging.getLogger(__name__)

FIXTURES_PATH = Path(__file__).parent.parent / "tests" / "fixtures" / "sample_role_profiles.json"

MAX_TURNS_DEFAULT = 150


@dataclass
class Turn:
    role: str  # "agent" | "employee"
    text: str
    phase: str
    block: str
    question_index: int
    is_followup: bool = False
    revealed_fact_ids: List[str] = field(default_factory=list)


@dataclass
class InterviewRun:
    persona_id: str
    turns: List[Turn]
    final_state: Dict[str, Any]
    completed: bool
    hit_turn_limit: bool = False

    @property
    def agent_turns(self) -> List[Turn]:
        return [t for t in self.turns if t.role == "agent"]


def _load_profile(profile_id: str) -> RoleIntelligenceProfile:
    with open(FIXTURES_PATH) as f:
        return RoleIntelligenceProfile.model_validate(json.load(f)[profile_id])


def run_interview(
    persona: Persona,
    live: bool = False,
    max_turns: int = MAX_TURNS_DEFAULT,
    employee: Optional[Any] = None,
) -> InterviewRun:
    """Run one full headless interview and return the transcript + state."""
    # Import here so scripted runs never touch langchain_anthropic.
    from agents.stage2_employee_interview.graph import build_stage2_graph

    profile = _load_profile(persona.profile_id)

    store = InMemorySessionStore()
    stage1_id = store.create_session(stage=1)
    store.store_profile(stage1_id, profile)
    set_session_store(store)

    if employee is None:
        employee = LiveEmployee(persona) if live else ScriptedEmployee(persona)
    if not live:
        llm_provider.set_llm_factories(
            primary=lambda max_tokens: FakePrimaryLLM(),
            classifier=lambda max_tokens: FakeClassifierLLM(persona),
        )

    session_id = f"eval-{persona.id}"
    graph = build_stage2_graph()  # fresh MemorySaver per run
    config = {"configurable": {"thread_id": session_id}}

    initial_state = {
        "session_id": session_id,
        "stage1_session_id": stage1_id,
        "profile": None,
        "current_phase": "role_orientation",
        "current_block": "role_orientation",
        "current_question_index": 0,
        "current_block_index": 0,
        "block_order": [],
        "block_depths": {},
        "followup_count": 0,
        "pending_followup": None,
        "answers": {},
        "conversation_history": [],
        "risk_flags": [],
        "entities": {},
        "sweep_questions": [],
        "question_count": 0,
        "question_budget": 0,
        "last_agent_message": "",
        "session_complete": False,
    }

    turns: List[Turn] = []
    completed = False
    hit_limit = False
    try:
        for _ in graph.stream(initial_state, config, stream_mode="values"):
            pass

        prev_question_key = None
        while len(turns) < max_turns:
            state = graph.get_state(config).values
            if state.get("session_complete"):
                completed = True
                break

            phase = state.get("current_phase", "")
            block = state.get("current_block", "")
            index = state.get("current_question_index", 0)
            agent_text = state.get("last_agent_message", "")

            # Same (block, index) asked twice in a row means the second one is
            # a follow-up — the index only moves when the interview advances.
            question_key = (block, index)
            is_followup = question_key == prev_question_key
            prev_question_key = question_key

            turns.append(
                Turn(
                    role="agent", text=agent_text, phase=phase,
                    block=block, question_index=index, is_followup=is_followup,
                )
            )

            answer = employee.answer(agent_text, block, index, is_followup)
            turns.append(
                Turn(
                    role="employee", text=answer, phase=phase,
                    block=block, question_index=index, is_followup=is_followup,
                    revealed_fact_ids=list(getattr(employee, "last_reveals", [])),
                )
            )

            graph.update_state(
                config, {"conversation_history": [HumanMessage(content=answer)]}
            )
            for _ in graph.stream(None, config, stream_mode="values"):
                pass
        else:
            hit_limit = True
            logger.warning("persona=%s hit the %d-turn safety limit", persona.id, max_turns)

        final_state = dict(graph.get_state(config).values)
    finally:
        if not live:
            llm_provider.reset_llm_factories()
        set_session_store(None)

    return InterviewRun(
        persona_id=persona.id,
        turns=turns,
        final_state=final_state,
        completed=completed,
        hit_turn_limit=hit_limit,
    )
