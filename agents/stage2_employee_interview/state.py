import operator
from typing import Annotated, Any, Dict, List, Optional

from langchain_core.messages import BaseMessage
from typing_extensions import TypedDict

from models.risk_flags import RiskFlag
from models.role_intelligence_profile import RoleIntelligenceProfile


class Stage2State(TypedDict):
    session_id: str
    stage1_session_id: str
    profile: Optional[RoleIntelligenceProfile]
    current_phase: str  # "role_orientation" | "knowledge_blocks" | "closing_sequence"
    current_block: str
    current_question_index: int
    current_block_index: int  # index into block_order list
    block_order: List[str]
    block_depths: Dict[str, str]  # block name -> "full" or "light"
    followup_count: int
    pending_followup: Optional[str]
    answers: Dict[str, Any]  # keyed by "{block}.{index}"
    conversation_history: Annotated[List[BaseMessage], operator.add]
    risk_flags: List[RiskFlag]
    last_agent_message: str
    session_complete: bool
