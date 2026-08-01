import operator
from typing import Annotated, Any, Dict, List, Optional

from langchain_core.messages import BaseMessage
from typing_extensions import TypedDict

from models.role_intelligence_profile import RoleIntelligenceProfile


class Stage1State(TypedDict):
    session_id: str
    business_name: str
    current_block: str
    current_question_index: int
    answers: Dict[str, Any]
    conversation_history: Annotated[List[BaseMessage], operator.add]
    role_intelligence_profile: Optional[RoleIntelligenceProfile]
    profile_confirmed: bool
    session_complete: bool
    followup_count: int
    pending_followup: Optional[str]
    last_agent_message: str
    # Field names that failed validation on the last profile generation attempt.
    # Empty/absent means the profile validated and can go to review.
    profile_generation_errors: List[str]
    # Number of profile generation attempts made, to bound the clarification loop.
    profile_generation_attempts: int
