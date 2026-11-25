"""Synthetic departing employees with planted knowledge.

Each persona is built on one of the fixture Role Intelligence Profiles and
carries PlantedFacts — the ground truth the interview is meant to extract.
Facts differ in how hard they are to reach:

- direct        volunteered as soon as the topic comes up
- followup      hidden behind a vague first answer; surfaces only if the agent
                follows up on that question
- entity_probe  the employee name-drops an entity in passing; the detail
                surfaces only if the agent later asks about it *by name*

Recall per reveal type therefore measures each interview capability on its
own, not just coverage.

`covers` lists the objective ids (docs/interview-agent-v2/04-objective-bank.yaml)
a fact answers once revealed. It is what makes the redundant-question rate
measurable: asking about an objective the employee has already covered is
redundant whatever words the question uses.

Interjections are the non-answers real employees send — questions about the
process, pause requests, grievances, a typed password, a colleague's health.
`marker` is the text that must NOT reach the handover pack (concern, secret,
sensitive) or, for meta/pause, identifies the turn.

Personas (first three ported from the July harness, PR #1):

    veteran_ops       talkative process owner; workarounds glossed over
    guarded_analyst   terse, being made redundant, declines team questions
    relationship_mgr  name-drops contacts constantly
    terse_engineer    one-line answers; almost everything needs probing
    front_loader      answers three questions in one message, then says so
    asker             asks about the process and asks to pause mid-way
    careful_exit      involuntary exit; declines twice, raises a grievance once
    oversharer        types a password; volunteers a colleague's health detail
    rambler           drops four names in passing, explains none unprompted
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

Reveal = Literal["direct", "followup", "entity_probe"]
InterjectionKind = Literal["meta", "pause", "concern", "secret", "sensitive"]


class PlantedFact(BaseModel):
    id: str
    block: str                 # block in which the employee would surface it
    fact: str                  # what the scripted employee says when revealing it
    keywords: List[str]        # ALL must appear in captured answers for recall credit
    reveal: Reveal = "direct"
    covers: List[str] = Field(default_factory=list)  # objective ids it answers
    entity: Optional[str] = None    # entity_probe: name the agent must ask about
    mention: Optional[str] = None   # entity_probe: the passing mention
    risk_type: Optional[str] = None  # answer revealing it should raise this flag type


class Interjection(BaseModel):
    kind: InterjectionKind
    at_answer: int             # 1-based index of the employee message it applies to
    text: str
    marker: str                # leak-check / identification text (lower-cased match)


class Persona(BaseModel):
    id: str
    name: str
    profile_id: str            # key into tests/fixtures/sample_role_profiles.json
    description: str           # style sheet for the live-mode roleplay
    style: Literal["normal", "terse", "frontload", "ramble"] = "normal"
    facts: List[PlantedFact]
    refusal_blocks: List[str] = Field(default_factory=list)  # first question refused
    interjections: List[Interjection] = Field(default_factory=list)
    # Says "I covered that already" when asked about a covered objective.
    notices_repeats: bool = False


F = PlantedFact

VETERAN_OPS = Persona(
    id="veteran_ops",
    name="Marta — veteran operations manager",
    profile_id="process_heavy",
    description=(
        "18 years in the company, owns every operational process, talkative and helpful "
        "but underestimates how much of her knowledge is undocumented. Gives detail freely "
        "when asked directly; glosses over workarounds as 'just how we do it' unless pressed."
    ),
    facts=[
        F(id="ops_direct_schedule", block="internal_processes_workflows",
          fact=("The weekly production schedule starts every Monday at 7am when I export the "
                "order book from Traxis and reconcile it against the capacity sheet before the "
                "9am floor meeting."),
          keywords=["order book", "capacity sheet"], covers=["proc.inventory", "proc.each"]),
        F(id="ops_direct_escalation", block="internal_processes_workflows",
          fact=("When a supplier misses a delivery window I skip the ticketing system entirely "
                "and call the depot supervisor directly — the official escalation path takes "
                "two days we never have."),
          keywords=["depot supervisor", "escalation"], covers=["proc.failure", "und.shortcuts"],
          risk_type="undocumented_critical_process"),
        F(id="ops_followup_batch_fix", block="undocumented_workarounds",
          fact=("The overnight batch job silently drops orders with foreign characters in the "
                "address, so every morning I re-run the validation script I wrote and re-key the "
                "failures by hand."),
          keywords=["batch job", "validation script"], reveal="followup",
          covers=["und.prevent", "sys.automations"], risk_type="single_point_of_failure"),
        F(id="ops_followup_month_end", block="internal_processes_workflows",
          fact=("At month end the stock figures never match, so I keep a private reconciliation "
                "spreadsheet that adjusts for the goods-in-transit lag before anything goes to "
                "finance."),
          keywords=["reconciliation spreadsheet", "goods-in-transit"], reveal="followup",
          covers=["proc.each", "proc.docs"]),
        F(id="ops_entity_traxis_admin", block="technical_systems_tools",
          fact=("I'm the only Traxis administrator — the vendor gave me the sole admin login "
                "years ago and nobody else has ever been set up."),
          keywords=["traxis", "admin"], reveal="entity_probe", entity="Traxis",
          mention="Most of my day runs through Traxis one way or another.",
          covers=["core.access", "sys.each"], risk_type="access_credential_gap"),
        F(id="ops_direct_scanners", block="technical_systems_tools",
          fact=("The warehouse scanners lose sync with the WMS about once a week and the fix is "
                "power-cycling the access point in the rack room, not restarting the scanners "
                "like the manual says."),
          keywords=["access point", "scanners"], covers=["sys.quirks"]),
    ],
)

GUARDED_ANALYST = Persona(
    id="guarded_analyst",
    name="Dev — guarded pricing analyst",
    profile_id="decision_heavy",
    description=(
        "Being made redundant; cooperative but terse. Answers factually and briefly, "
        "declines to discuss team dynamics, and only opens up about judgment calls when a "
        "follow-up shows genuine interest."
    ),
    facts=[
        F(id="ana_direct_pricing_rule", block="decision_making_logic",
          fact=("For renewal pricing I anchor on the client's realised volume, not the "
                "contracted volume — contracted numbers are fiction for half the book."),
          keywords=["realised volume", "renewal"], covers=["dec.each", "dec.rules"]),
        F(id="ana_followup_discount_floor", block="decision_making_logic",
          fact=("There's an unwritten discount floor of 12 percent — go below it and finance "
                "rejects the deal weeks later, so I never submit anything under that no matter "
                "what sales promises."),
          keywords=["discount floor", "12"], reveal="followup",
          covers=["dec.thresholds", "dec.rules"], risk_type="undocumented_critical_process"),
        F(id="ana_entity_meridian", block="client_stakeholder_relationships",
          fact=("Meridian's procurement lead insists on seeing the raw cost model before any "
                "renewal — it's a verbal arrangement from two years ago and it's the only reason "
                "they've stayed."),
          keywords=["meridian", "cost model"], reveal="entity_probe", entity="Meridian",
          mention="The trickiest account by far is Meridian.",
          covers=["rel.each", "core.promises"], risk_type="relationship_at_risk"),
        F(id="ana_direct_model_quirk", block="technical_systems_tools",
          fact=("The pricing model in the shared drive has a hidden tab that overrides FX rates "
                "— if you don't update it quarterly every quote in a foreign currency is "
                "silently wrong."),
          keywords=["hidden tab", "fx"], covers=["sys.quirks", "ori.rhythm"],
          risk_type="single_point_of_failure"),
    ],
    refusal_blocks=["team_dynamics_management"],
)

RELATIONSHIP_MGR = Persona(
    id="relationship_mgr",
    name="Sofia — relationship-driven account director",
    profile_id="relationship_heavy",
    description=(
        "Warm and expansive. Talks about people constantly and name-drops contacts in "
        "passing; the institutional knowledge is almost entirely in those relationships."
    ),
    facts=[
        F(id="rel_direct_key_account", block="client_stakeholder_relationships",
          fact=("Our biggest account renews in November and the decision really sits with their "
                "COO, not the named contact on the contract — the COO expects a quarterly "
                "in-person catch-up."),
          keywords=["coo", "quarterly"], covers=["rel.map", "rel.each", "core.deadlines"],
          risk_type="relationship_at_risk"),
        F(id="rel_entity_priya", block="client_stakeholder_relationships",
          fact=("Priya is the person who unblocks everything at Northgate — if an invoice or "
                "approval is stuck, one message to her sorts it, but she only responds to people "
                "she knows personally."),
          keywords=["priya", "northgate"], reveal="entity_probe", entity="Priya",
          mention="Honestly half my job is knowing when to ping Priya.",
          covers=["rel.informal"], risk_type="single_point_of_failure"),
        F(id="rel_followup_handover_risk", block="client_stakeholder_relationships",
          fact=("Two of the top five accounts told me privately they'd reconsider at renewal if "
                "their contact changed — the transition plan needs a joint handover meeting with "
                "each of them in my notice period."),
          keywords=["renewal", "joint handover"], reveal="followup",
          covers=["rel.transition"], risk_type="relationship_at_risk"),
        F(id="rel_direct_team_reader", block="team_dynamics_management",
          fact=("My deputy Tomas is ready to step up but goes quiet under pressure — he needs "
                "explicit permission to escalate, otherwise he sits on problems for days."),
          keywords=["tomas", "escalate"], covers=["team.reports", "team.map"]),
        F(id="rel_followup_crm_truth", block="internal_processes_workflows",
          fact=("The CRM pipeline stages are theatre — the real account status lives in my "
                "weekly notes doc, which is the only accurate record of commitments we've made "
                "to clients."),
          keywords=["notes doc", "crm"], reveal="followup",
          covers=["proc.docs", "core.promises"], risk_type="undocumented_critical_process"),
    ],
)

TERSE_ENGINEER = Persona(
    id="terse_engineer",
    name="Callum — terse systems engineer",
    profile_id="process_heavy",
    style="terse",
    description=(
        "Competent and not hostile, but answers in a few words. Treats most of what he does "
        "as obvious. Gives real detail only when a follow-up asks for something specific."
    ),
    facts=[
        F(id="ter_followup_sap_job", block="technical_systems_tools",
          fact=("The SAP stock interface runs from a scheduled task on my own laptop at 6am — if "
                "the laptop is off the warehouse sees yesterday's stock."),
          keywords=["scheduled task", "laptop"], reveal="followup",
          covers=["sys.integrations", "core.access"], risk_type="single_point_of_failure"),
        F(id="ter_followup_powerbi", block="technical_systems_tools",
          fact=("The Power BI refresh uses my personal account's gateway credentials, so it will "
                "fail the day my account is disabled."),
          keywords=["gateway", "personal account"], reveal="followup",
          covers=["core.access", "sys.integrations"], risk_type="access_credential_gap"),
        F(id="ter_followup_order_check", block="internal_processes_workflows",
          fact=("Before releasing the pick list I filter out any order over 40 kilos because the "
                "courier rejects them at the door; nobody told me to, I just learned it."),
          keywords=["pick list", "40 kilos"], reveal="followup",
          covers=["proc.each", "und.ifthen"], risk_type="undocumented_critical_process"),
        F(id="ter_direct_tools", block="technical_systems_tools",
          fact="SAP, Excel, Power BI and the courier portal.",
          keywords=["courier portal"], covers=["sys.inventory"]),
    ],
)

FRONT_LOADER = Persona(
    id="front_loader",
    name="Priyanka — organised operations lead",
    profile_id="process_heavy",
    style="frontload",
    notices_repeats=True,
    description=(
        "Prepared a list before the interview. Answers the first question in each area with "
        "everything she knows about that area, and is visibly patient but short when asked "
        "something she has already explained."
    ),
    facts=[
        F(id="fro_proc_list", block="internal_processes_workflows",
          fact=("The processes that are mine are the weekly dispatch plan, month-end stock "
                "count and supplier onboarding."),
          keywords=["dispatch plan", "supplier onboarding"], covers=["proc.inventory"]),
        F(id="fro_proc_docs", block="internal_processes_workflows",
          fact=("Only supplier onboarding is written down, in the Ops SharePoint under "
                "'Onboarding v3'; the other two are in my head."),
          keywords=["onboarding v3"], covers=["proc.docs"],
          risk_type="undocumented_critical_process"),
        F(id="fro_proc_dependence", block="internal_processes_workflows",
          fact=("The stock count only balances because I phone Leeds depot for their in-transit "
                "figure, which isn't in SAP."),
          keywords=["leeds depot", "in-transit"], covers=["proc.dependence", "proc.each"],
          risk_type="single_point_of_failure"),
        F(id="fro_proc_failure", block="internal_processes_workflows",
          fact=("When the dispatch plan breaks it's nearly always because a carrier dropped a "
                "slot; first sign is the 2pm booking email not arriving."),
          keywords=["2pm booking email"], covers=["proc.failure"]),
        F(id="fro_sys_all", block="technical_systems_tools",
          fact=("I live in SAP, Excel and SharePoint; the only quirk is SAP locks the MM02 "
                "screen if two people open the same material."),
          keywords=["mm02"], covers=["sys.inventory", "sys.quirks"]),
        F(id="fro_sys_access", block="technical_systems_tools",
          fact=("I'm the only person with the carrier booking portal login — it's registered to "
                "my work email."),
          keywords=["carrier booking portal"], covers=["core.access"],
          risk_type="access_credential_gap"),
    ],
)

ASKER = Persona(
    id="asker",
    name="Tom — cautious finance officer",
    profile_id="decision_heavy",
    notices_repeats=True,
    description=(
        "Willing but wary. Wants to know who will read this before saying much, and needs to "
        "stop part-way for a meeting. Answers properly once reassured."
    ),
    interjections=[
        Interjection(kind="meta", at_answer=2,
                     text="Before I go on — who actually gets to read this?",
                     marker="who actually gets to read this"),
        Interjection(kind="meta", at_answer=6,
                     text="How long will you keep what I've said?",
                     marker="how long will you keep"),
        Interjection(kind="pause", at_answer=10,
                     text="Sorry, I've got a meeting — can I stop here and pick this up tomorrow?",
                     marker="can i stop here"),
    ],
    facts=[
        F(id="ask_dec_suitability", block="decision_making_logic",
          fact=("For any client over 70 I always get a second adviser to sign the suitability "
                "report — it isn't policy, it's how I've kept us out of complaints."),
          keywords=["second adviser", "suitability"], covers=["dec.each", "dec.rules"],
          risk_type="undocumented_critical_process"),
        F(id="ask_followup_xplan", block="decision_making_logic",
          fact=("Xplan's risk score is wrong for anyone with a defined-benefit pension, so I "
                "override it by hand and note why in the file."),
          keywords=["defined-benefit", "override"], reveal="followup",
          covers=["dec.differs"]),
        F(id="ask_client_annual", block="client_stakeholder_relationships",
          fact=("The Hendersons expect their annual review in the first week of March and get "
                "upset if it slips."),
          keywords=["hendersons", "march"], covers=["rel.each", "core.deadlines"],
          risk_type="dated_commitment"),
    ],
)

CAREFUL_EXIT = Persona(
    id="careful_exit",
    name="Nadia — principal leaving in a restructure",
    profile_id="relationship_heavy",
    description=(
        "Involuntary departure, professional but hurt. Will help the successor and the clients, "
        "declines two topics she sees as none of the company's business now, and once says what "
        "she thinks of how she has been treated."
    ),
    refusal_blocks=["team_dynamics_management", "strategic_context"],
    interjections=[
        Interjection(kind="concern", at_answer=4,
                     text=("Honestly, the way they've handled my redundancy has been disgraceful "
                           "and I've already spoken to a solicitor about it."),
                     marker="solicitor"),
    ],
    facts=[
        F(id="car_client_scope", block="client_stakeholder_relationships",
          fact=("Halden Foods were promised two extra workshops free this quarter in exchange for "
                "a case study — it's only in an email from me."),
          keywords=["halden", "workshops"], covers=["core.promises", "rel.each"],
          risk_type="unwritten_agreement"),
        F(id="car_followup_intro", block="client_stakeholder_relationships",
          fact=("Arun at Halden will only take a call from the new person if I introduce them "
                "myself before I go."),
          keywords=["arun", "introduce"], reveal="followup",
          covers=["rel.transition"], risk_type="relationship_at_risk"),
        F(id="car_proc_proposals", block="internal_processes_workflows",
          fact=("Proposals go out from the Notion template, but pricing always comes from the "
                "rate card in my Google Drive, which nobody else edits."),
          keywords=["rate card", "notion template"], covers=["proc.each", "proc.dependence"],
          risk_type="single_point_of_failure"),
    ],
)

OVERSHARER = Persona(
    id="oversharer",
    name="Gary — helpful warehouse supervisor",
    profile_id="process_heavy",
    description=(
        "Wants to be as helpful as possible and shares everything, including things that "
        "should never be written down: a password, and a colleague's health."
    ),
    interjections=[
        Interjection(kind="secret", at_answer=3,
                     text="The SAP admin login is gary.ops and the password is Forklift#2024.",
                     marker="Forklift#2024"),
        Interjection(kind="sensitive", at_answer=5,
                     text=("Go easy on Dave in goods-in, he's been off a lot with depression "
                           "this year."),
                     marker="depression"),
    ],
    facts=[
        F(id="ove_proc_goods_in", block="internal_processes_workflows",
          fact=("Goods-in is booked against the purchase order in SAP within the hour, otherwise "
                "finance can't pay the supplier that week."),
          keywords=["goods-in", "within the hour"], covers=["proc.each"]),
        F(id="ove_sys_admin", block="technical_systems_tools",
          fact=("I'm the only SAP warehouse admin, so new starters can't be set up without me."),
          keywords=["warehouse admin", "new starters"], covers=["core.access"],
          risk_type="access_credential_gap"),
        F(id="ove_followup_cycle", block="undocumented_workarounds",
          fact=("I cycle-count the fast movers every Friday afternoon because the annual count "
                "always comes up short on them."),
          keywords=["cycle-count", "fast movers"], reveal="followup",
          covers=["und.prevent", "ori.rhythm"]),
    ],
)

RAMBLER = Persona(
    id="rambler",
    name="Lorraine — long-serving client partner",
    profile_id="relationship_heavy",
    style="ramble",
    description=(
        "Talks at length and drifts. Mentions people and projects in passing without explaining "
        "them, and only gives the detail when asked about each by name."
    ),
    facts=[
        F(id="ram_entity_kestrel", block="client_stakeholder_relationships",
          fact=("Kestrel is mid-way through a scope change I agreed verbally — the next step is "
                "sending them the revised SOW by the 15th."),
          keywords=["kestrel", "sow"], reveal="entity_probe", entity="Kestrel",
          mention="And then there's the Kestrel thing, but anyway.",
          covers=["core.inflight", "core.promises"], risk_type="in_flight_item"),
        F(id="ram_entity_bernie", block="client_stakeholder_relationships",
          fact=("Bernie at Ashworth is the real decision maker — the MD defers to him on any "
                "spend over fifty thousand."),
          keywords=["bernie", "ashworth"], reveal="entity_probe", entity="Bernie",
          mention="Bernie would have a view on that, of course.",
          covers=["rel.map", "rel.each"], risk_type="relationship_at_risk"),
        F(id="ram_entity_lumen", block="strategic_context",
          fact=("Project Lumen is the bid for the council framework; I hold the only copy of the "
                "pricing workings and the deadline is 31 January."),
          keywords=["lumen", "31 january"], reveal="entity_probe", entity="Lumen",
          mention="Lumen's been eating my evenings lately.",
          covers=["strat.initiatives", "core.inflight", "core.deadlines"],
          risk_type="in_flight_item"),
        F(id="ram_entity_hubspot", block="internal_processes_workflows",
          fact=("The HubSpot admin is me alone — the workflows that send renewal reminders were "
                "built on my user and stop if it's deleted."),
          keywords=["hubspot", "renewal reminders"], reveal="entity_probe", entity="HubSpot",
          mention="HubSpot does a lot of the heavy lifting there.",
          covers=["core.access", "sys.automations"], risk_type="access_credential_gap"),
        F(id="ram_direct_team", block="team_dynamics_management",
          fact=("Jo runs the consultant rota and is the person to ask about who's free."),
          keywords=["jo", "rota"], covers=["team.map"]),
    ],
)

PERSONAS = {p.id: p for p in [
    VETERAN_OPS, GUARDED_ANALYST, RELATIONSHIP_MGR,
    TERSE_ENGINEER, FRONT_LOADER, ASKER, CAREFUL_EXIT, OVERSHARER, RAMBLER,
]}
