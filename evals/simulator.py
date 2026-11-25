"""Headless Stage 2 interview runner.

Drives the real compiled Stage 2 LangGraph — the graph the API serves — turn by
turn against a simulated employee, recording a full transcript with per-turn
metadata. Each employee message goes through the same secret redaction the
API applies before it reaches graph state, so what is scored is what a real
session would store.

Scripted mode installs deterministic fakes through `config.llm_provider`; live
mode leaves the real models in place.
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage

import api.session_manager as session_manager
from agents.redaction import redact_secrets
from config import llm_provider
from evals.employee import LiveEmployee, ScriptedEmployee
from evals.fakes import FakeAnalystLLM, FakeInterviewerLLM
from evals.objectives import v1_objective
from evals.personas import Persona
from models.role_intelligence_profile import RoleIntelligenceProfile

logger = logging.getLogger(__name__)

FIXTURES_PATH = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "sample_role_profiles.json"
MAX_TURNS_DEFAULT = 200


@dataclass
class Turn:
    role: str                      # "agent" | "employee"
    text: str
    phase: str
    block: str
    question_index: int
    objective_id: Optional[str] = None
    is_followup: bool = False
    revealed_fact_ids: List[str] = field(default_factory=list)
    interjection: Optional[str] = None   # kind, for injected employee messages
    redactions: int = 0


@dataclass
class InterviewRun:
    persona_id: str
    live: bool
    turns: List[Turn]
    final_state: Dict[str, Any]
    stage3_context: str
    completed: bool
    hit_turn_limit: bool = False

    @property
    def agent_turns(self) -> List[Turn]:
        return [t for t in self.turns if t.role == "agent"]

    @property
    def employee_turns(self) -> List[Turn]:
        return [t for t in self.turns if t.role == "employee"]


def load_profile(profile_id: str) -> RoleIntelligenceProfile:
    with open(FIXTURES_PATH) as f:
        return RoleIntelligenceProfile.model_validate(json.load(f)[profile_id])


def _objective_for(state: dict) -> Optional[str]:
    # v2 sessions will name the objective in state; v1 is mapped by question.
    return state.get("current_objective") or v1_objective(
        state.get("current_block", ""), state.get("current_question_index", 0)
    )


def _stage3_context(state: dict) -> str:
    """The exact text Stage 3 would be given — what the pack is written from."""
    from agents.stage3_document_generation.prompts import build_context_block

    if not state.get("profile"):
        return ""
    return build_context_block(
        profile=state["profile"],
        conversation_history=state.get("conversation_history", []),
        risk_flags=state.get("risk_flags", []),
        answers=state.get("answers", {}),
        block_order=state.get("block_order", []),
        block_depths=state.get("block_depths", {}),
    )


def run_interview(
    persona: Persona,
    live: bool = False,
    max_turns: int = MAX_TURNS_DEFAULT,
    employee: Optional[Any] = None,
    stop_after_answers: Optional[int] = None,
) -> InterviewRun:
    """Run one headless interview and return the transcript and final state.

    `stop_after_answers` simulates an employee who walks away part-way.
    """
    from agents.stage2_employee_interview.graph import build_stage2_graph

    profile = load_profile(persona.profile_id)
    previous_store = session_manager._store
    store = session_manager.InMemorySessionStore()
    session_manager._store = store
    stage1_id = store.create_session(stage=1)
    store.store_profile(stage1_id, profile)

    if employee is None:
        employee = LiveEmployee(persona) if live else ScriptedEmployee(persona)
    if not live:
        llm_provider.set_llm_factories(
            interviewer=lambda max_tokens: FakeInterviewerLLM(),
            analyst=lambda max_tokens: FakeAnalystLLM(persona),
        )

    interjections = {i.at_answer: i for i in persona.interjections}
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
        "last_agent_message": "",
        "session_complete": False,
    }

    turns: List[Turn] = []
    completed = hit_limit = False
    try:
        for _ in graph.stream(initial_state, config, stream_mode="values"):
            pass

        previous_key = None
        answers_sent = 0
        while len(turns) < max_turns:
            state = graph.get_state(config).values
            if state.get("session_complete"):
                completed = True
                break
            if stop_after_answers is not None and answers_sent >= stop_after_answers:
                break

            phase = state.get("current_phase", "")
            block = state.get("current_block", "")
            index = state.get("current_question_index", 0)
            objective = _objective_for(state)
            question = state.get("last_agent_message", "")
            key = (block, index)
            is_followup = key == previous_key
            previous_key = key
            turns.append(Turn("agent", question, phase, block, index, objective, is_followup))

            answers_sent += 1
            injected = interjections.get(answers_sent)
            if injected and injected.kind in ("meta", "pause"):
                message, reveals = injected.text, []
            else:
                message = employee.answer(question, block, index, is_followup, objective)
                reveals = list(getattr(employee, "last_reveals", []))
                if injected:
                    message = f"{message} {injected.text}"

            redaction = redact_secrets(message)  # as POST /message does
            turns.append(Turn(
                "employee", redaction.text, phase, block, index, objective, is_followup,
                revealed_fact_ids=reveals,
                interjection=injected.kind if injected else None,
                redactions=redaction.count,
            ))
            graph.update_state(config, {"conversation_history": [HumanMessage(content=redaction.text)]})
            for _ in graph.stream(None, config, stream_mode="values"):
                pass
        else:
            hit_limit = True
            logger.warning("persona=%s hit the %d-turn safety limit", persona.id, max_turns)

        final_state = dict(graph.get_state(config).values)
        context = _stage3_context(final_state)
    finally:
        if not live:
            llm_provider.reset_llm_factories()
        session_manager._store = previous_store

    return InterviewRun(
        persona_id=persona.id,
        live=live,
        turns=turns,
        final_state=final_state,
        stage3_context=context,
        completed=completed,
        hit_turn_limit=hit_limit,
    )
