from pydantic import BaseModel

from config.constants import RiskFlagType, Severity


class RiskFlag(BaseModel):
    flag_type: RiskFlagType
    severity: Severity
    description: str
    recommended_action: str
    source_block: str
    source_question_index: int
