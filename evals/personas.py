"""Synthetic employee personas with planted knowledge.

Each persona is built on one of the fixture Role Intelligence Profiles and
carries a set of PlantedFacts — the ground truth the interview is supposed to
extract. Facts differ in how hard they are to reach:

- direct: volunteered as soon as the relevant block is asked about
- followup: hidden behind a vague first answer; only surfaces if the agent
  asks a follow-up on that question
- entity_probe: the employee name-drops an entity in passing; the payload
  only surfaces if the agent later asks about that entity by name (entity
  memory + sweep)

Recall per reveal type is therefore a direct measurement of each interview
capability, not just of overall coverage.
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class PlantedFact(BaseModel):
    id: str
    block: str  # block in which the employee would surface it
    fact: str  # the sentence the scripted employee says when revealing it
    keywords: List[str]  # ALL must appear in captured answers for recall credit
    reveal: Literal["direct", "followup", "entity_probe"] = "direct"
    # entity_probe only: the entity the agent must ask about by name, and the
    # passing mention the employee drops earlier in the block
    entity: Optional[str] = None
    mention: Optional[str] = None
    # If set, the answer containing this fact should raise a risk flag of this type
    risk_type: Optional[str] = None


class Persona(BaseModel):
    id: str
    name: str
    profile_id: str  # key into tests/fixtures/sample_role_profiles.json
    description: str  # style sheet for the live-mode roleplay model
    facts: List[PlantedFact]
    # Blocks where the first question gets a refusal (the agent must respect it)
    refusal_blocks: List[str] = Field(default_factory=list)


VETERAN_OPS = Persona(
    id="veteran_ops",
    name="Marta — veteran operations manager",
    profile_id="process_heavy",
    description=(
        "18 years in the company, owns every operational process, talkative and "
        "helpful but underestimates how much of her knowledge is undocumented. "
        "Gives detail freely when asked directly; glosses over workarounds as "
        "'just how we do it' unless pressed."
    ),
    facts=[
        PlantedFact(
            id="ops_direct_schedule",
            block="internal_processes_workflows",
            fact=(
                "The weekly production schedule starts every Monday at 7am when I "
                "export the order book from Traxis and reconcile it against the "
                "capacity sheet before the 9am floor meeting."
            ),
            keywords=["order book", "capacity sheet"],
            reveal="direct",
        ),
        PlantedFact(
            id="ops_direct_escalation",
            block="internal_processes_workflows",
            fact=(
                "When a supplier misses a delivery window I skip the ticketing "
                "system entirely and call the depot supervisor directly — the "
                "official escalation path takes two days we never have."
            ),
            keywords=["depot supervisor", "escalation"],
            reveal="direct",
            risk_type="undocumented_critical_process",
        ),
        PlantedFact(
            id="ops_followup_batch_fix",
            block="undocumented_workarounds",
            fact=(
                "The overnight batch job silently drops orders with foreign "
                "characters in the address, so every morning I re-run the "
                "validation script I wrote and re-key the failures by hand."
            ),
            keywords=["batch job", "validation script"],
            reveal="followup",
            risk_type="single_point_of_failure",
        ),
        PlantedFact(
            id="ops_followup_month_end",
            block="internal_processes_workflows",
            fact=(
                "At month end the stock figures never match, so I keep a private "
                "reconciliation spreadsheet that adjusts for the goods-in-transit "
                "lag before anything goes to finance."
            ),
            keywords=["reconciliation spreadsheet", "goods-in-transit"],
            reveal="followup",
        ),
        PlantedFact(
            id="ops_entity_traxis_admin",
            block="technical_systems_tools",
            fact=(
                "I'm the only Traxis administrator — the vendor gave me the sole "
                "admin login years ago and nobody else has ever been set up."
            ),
            keywords=["traxis", "admin"],
            reveal="entity_probe",
            entity="Traxis",
            mention="Most of my day runs through Traxis one way or another.",
            risk_type="access_credential_gap",
        ),
        PlantedFact(
            id="ops_direct_forklift",
            block="technical_systems_tools",
            fact=(
                "The warehouse scanners lose sync with the WMS about once a week "
                "and the fix is power-cycling the access point in the rack room, "
                "not restarting the scanners like the manual says."
            ),
            keywords=["access point", "scanners"],
            reveal="direct",
        ),
    ],
)

GUARDED_ANALYST = Persona(
    id="guarded_analyst",
    name="Dev — guarded pricing analyst",
    profile_id="decision_heavy",
    description=(
        "Being made redundant and cooperative but terse. Answers factually and "
        "briefly, declines to discuss team dynamics, and only opens up about "
        "judgment calls when a follow-up shows genuine interest."
    ),
    facts=[
        PlantedFact(
            id="ana_direct_pricing_rule",
            block="decision_making_logic",
            fact=(
                "For renewal pricing I anchor on the client's realised volume, "
                "not the contracted volume — contracted numbers are fiction for "
                "half the book."
            ),
            keywords=["realised volume", "renewal"],
            reveal="direct",
        ),
        PlantedFact(
            id="ana_followup_discount_floor",
            block="decision_making_logic",
            fact=(
                "There's an unwritten discount floor of 12 percent — go below it "
                "and finance rejects the deal weeks later, so I never submit "
                "anything under that no matter what sales promises."
            ),
            keywords=["discount floor", "12"],
            reveal="followup",
            risk_type="undocumented_critical_process",
        ),
        PlantedFact(
            id="ana_entity_meridian",
            block="client_stakeholder_relationships",
            fact=(
                "Meridian's procurement lead insists on seeing the raw cost model "
                "before any renewal — it's a verbal arrangement from two years "
                "ago and it's the only reason they've stayed."
            ),
            keywords=["meridian", "cost model"],
            reveal="entity_probe",
            entity="Meridian",
            mention="The trickiest account by far is Meridian.",
            risk_type="relationship_at_risk",
        ),
        PlantedFact(
            id="ana_direct_model_quirk",
            block="technical_systems_tools",
            fact=(
                "The pricing model in the shared drive has a hidden tab that "
                "overrides FX rates — if you don't update it quarterly every "
                "quote in a foreign currency is silently wrong."
            ),
            keywords=["hidden tab", "fx"],
            reveal="direct",
            risk_type="single_point_of_failure",
        ),
    ],
    refusal_blocks=["team_dynamics_management"],
)

RELATIONSHIP_MGR = Persona(
    id="relationship_mgr",
    name="Sofia — relationship-driven account director",
    profile_id="relationship_heavy",
    description=(
        "Leaving on good terms, warm and expansive. Talks about people "
        "constantly and name-drops contacts in passing; the institutional "
        "knowledge is almost entirely in those relationships."
    ),
    facts=[
        PlantedFact(
            id="rel_direct_key_account",
            block="client_stakeholder_relationships",
            fact=(
                "Our biggest account renews in November and the decision really "
                "sits with their COO, not the named contact on the contract — "
                "the COO expects a quarterly in-person catch-up."
            ),
            keywords=["coo", "quarterly"],
            reveal="direct",
            risk_type="relationship_at_risk",
        ),
        PlantedFact(
            id="rel_entity_priya",
            block="client_stakeholder_relationships",
            fact=(
                "Priya is the person who unblocks everything at Northgate — if "
                "an invoice or approval is stuck, one message to her sorts it, "
                "but she only responds to people she knows personally."
            ),
            keywords=["priya", "northgate"],
            reveal="entity_probe",
            entity="Priya",
            mention="Honestly half my job is knowing when to ping Priya.",
            risk_type="single_point_of_failure",
        ),
        PlantedFact(
            id="rel_followup_handover_risk",
            block="client_stakeholder_relationships",
            fact=(
                "Two of the top five accounts told me privately they'd reconsider "
                "at renewal if their contact changed — the transition plan needs "
                "a joint handover meeting with each of them in my notice period."
            ),
            keywords=["renewal", "joint handover"],
            reveal="followup",
            risk_type="relationship_at_risk",
        ),
        PlantedFact(
            id="rel_direct_team_reader",
            block="team_dynamics_management",
            fact=(
                "My deputy Tomas is ready to step up but goes quiet under "
                "pressure — he needs explicit permission to escalate, otherwise "
                "he sits on problems for days."
            ),
            keywords=["tomas", "escalate"],
            reveal="direct",
        ),
        PlantedFact(
            id="rel_followup_crm_truth",
            block="internal_processes_workflows",
            fact=(
                "The CRM pipeline stages are theatre — the real account status "
                "lives in my weekly notes doc, which is the only accurate record "
                "of commitments we've made to clients."
            ),
            keywords=["notes doc", "crm"],
            reveal="followup",
            risk_type="undocumented_critical_process",
        ),
    ],
)

PERSONAS = {p.id: p for p in [VETERAN_OPS, GUARDED_ANALYST, RELATIONSHIP_MGR]}
