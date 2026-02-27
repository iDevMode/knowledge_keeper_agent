from typing import List, Optional

from pydantic import BaseModel

from config.constants import (
    CultureType,
    DepartureType,
    ExperienceLevel,
    GoodTerms,
    HireType,
    RoleType,
)


class RoleIntelligenceProfile(BaseModel):
    # Business Context
    company_name: Optional[str] = None
    industry: str
    team_structure: str
    key_tools: List[str]
    culture_type: CultureType
    previous_knowledge_loss: Optional[str] = None

    # Vacant Role
    job_title: str
    department: str
    tenure: str
    reports_to: str
    direct_reports: Optional[str] = None
    role_type: RoleType
    role_type_weighting: Optional[str] = None
    immediate_risk: str
    undocumented_areas: Optional[str] = None
    key_external_relationships: Optional[str] = None
    system_access_gaps: Optional[str] = None

    # Replacement
    hire_type: HireType
    replacement_experience_level: ExperienceLevel
    most_important_context: str
    success_definition_90_days: str
    overlap_period: Optional[str] = None

    # Knowledge Priorities
    priority_1: str
    priority_2: str
    priority_3: str
    supporting_categories: List[str] = []

    # Output Preferences
    document_destination: str
    recipients: List[str]
    confidential_sections: Optional[str] = None
    existing_template: Optional[str] = None

    # Departure Context
    departure_type: DepartureType
    leaving_on_good_terms: GoodTerms
    employee_aware: bool
    sensitivity_flags: Optional[str] = None
    notice_period: str

    # Agent Instruction Flags (auto-generated)
    agent_flags: List[str] = []
