import operator
from typing import Annotated, Dict, List, Optional

from langchain_core.messages import BaseMessage
from typing_extensions import TypedDict

from models.risk_flags import RiskFlag
from models.role_intelligence_profile import RoleIntelligenceProfile


class Stage2State(TypedDict):
    session_id: str
    stage1_session_id: str
    profile: Optional[RoleIntelligenceProfile]
    # "role_orientation" | "knowledge_blocks" | "entity_sweep" | "closing_sequence"
    current_phase: str
    current_block: str
    current_question_index: int
    current_block_index: int  # index into block_order list
    block_order: List[str]
    block_depths: Dict[str, str]  # block name -> "full" or "light"
    followup_count: int
    pending_followup: Optional[str]
    answers: Dict[str, List[str]]  # "{block}.{index}" -> [answer, followup answers...]
    conversation_history: Annotated[List[BaseMessage], operator.add]
    risk_flags: List[RiskFlag]
    # Entity memory: lowercased name -> TrackedEntity dump (plain dicts so any
    # checkpointer can serialise them)
    entities: Dict[str, dict]
    sweep_questions: List[str]  # generated entity-probe instructions
    # Fatigue management: agent questions asked so far (incl. follow-ups) vs
    # the planned budget computed at load_profile
    question_count: int
    question_budget: int
    last_agent_message: str
    session_complete: bool
