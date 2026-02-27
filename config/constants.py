from enum import Enum


class KnowledgeBlock(str, Enum):
    CLIENT_STAKEHOLDER_RELATIONSHIPS = "client_stakeholder_relationships"
    INTERNAL_PROCESSES_WORKFLOWS = "internal_processes_workflows"
    TECHNICAL_SYSTEMS_TOOLS = "technical_systems_tools"
    DECISION_MAKING_LOGIC = "decision_making_logic"
    TEAM_DYNAMICS_MANAGEMENT = "team_dynamics_management"
    SUPPLIER_VENDOR_RELATIONSHIPS = "supplier_vendor_relationships"
    REGULATORY_COMPLIANCE = "regulatory_compliance"
    UNDOCUMENTED_WORKAROUNDS = "undocumented_workarounds"
    STRATEGIC_CONTEXT = "strategic_context"


class RiskFlagType(str, Enum):
    SINGLE_POINT_OF_FAILURE = "single_point_of_failure"
    UNDOCUMENTED_CRITICAL_PROCESS = "undocumented_critical_process"
    RELATIONSHIP_AT_RISK = "relationship_at_risk"
    ACCESS_CREDENTIAL_GAP = "access_credential_gap"
    IN_FLIGHT_ITEM = "in_flight_item"


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"


class BlockDepth(str, Enum):
    FULL = "full"
    LIGHT = "light"


class RoleType(str, Enum):
    PROCESS = "process"
    DECISION = "decision"
    RELATIONSHIP = "relationship"
    MIXED = "mixed"


class CultureType(str, Enum):
    FORMAL = "formal"
    INFORMAL = "informal"
    MIXED = "mixed"


class DepartureType(str, Enum):
    VOLUNTARY = "voluntary"
    INVOLUNTARY = "involuntary"


class HireType(str, Enum):
    EXTERNAL = "external"
    INTERNAL = "internal"


class ExperienceLevel(str, Enum):
    JUNIOR = "junior"
    MID = "mid"
    SENIOR = "senior"


class GoodTerms(str, Enum):
    YES = "yes"
    NO = "no"
    UNCLEAR = "unclear"


# Stage 1 block names (in order)
STAGE1_BLOCKS = [
    "business_context",
    "vacant_role",
    "replacement_profile",
    "knowledge_priorities",
    "output_preferences",
    "departure_sensitivity",
]

# Number of questions per Stage 1 block
STAGE1_BLOCK_QUESTION_COUNTS = {
    "business_context": 5,
    "vacant_role": 8,
    "replacement_profile": 5,
    "knowledge_priorities": 2,
    "output_preferences": 4,
    "departure_sensitivity": 4,
}

MAX_FOLLOWUPS_PER_QUESTION = 3
