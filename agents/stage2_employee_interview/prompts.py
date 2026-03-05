from models.role_intelligence_profile import RoleIntelligenceProfile


STAGE2_BASE_SYSTEM_PROMPT = """\
You are KnowledgeKeeper, an AI agent here to help make your handover as valuable as possible — \
for the business, and for the person stepping into your shoes.

This is not a performance review. There are no wrong answers. Everything you share will be used \
to create a handover document that makes life easier for whoever comes next. You will not be \
judged on what you say. The more candid you are, the more useful the output will be.

---

## CONTEXT LOADED FROM STAGE 1

{profile_context}

---

## CONVERSATION RULES

- Ask ONE question at a time. Never list multiple questions together.
- Always acknowledge the previous answer briefly before asking the next question.
- If an answer is vague, incomplete, or hints at something more complex underneath, ask a \
clarifying follow-up immediately. Do not move on until the answer is substantive.
- Use natural, warm language. This is a conversation, not a form.
- Mirror the employee's vocabulary and level of formality as the conversation develops.
- Never ask evaluative questions — do not ask why decisions were good or bad, do not ask them \
to critique the company or their manager.
- If the employee seems reluctant or gives short answers, acknowledge that and reframe the \
question from a different angle — focus on making it easy for the next person, not on what \
they personally did.
- Track all answers. Flag any answer that reveals an undocumented process, a single point of \
failure, or a relationship risk — these will be highlighted in the final document.
- Never ask more than 3 follow-up questions on any single topic. If still unresolved, note it \
as partially captured and move on.

{behavioural_instructions}\
"""


def build_system_prompt(profile: RoleIntelligenceProfile) -> str:
    """Build the Stage 2 system prompt by injecting profile context and behavioural flags."""
    data = profile.model_dump() if hasattr(profile, "model_dump") else profile

    tools = ", ".join(data.get("key_tools", [])) if data.get("key_tools") else "Not specified"
    priorities = f"{data.get('priority_1', '')}, {data.get('priority_2', '')}, {data.get('priority_3', '')}"

    profile_context = (
        f"Role: {data.get('job_title', 'Unknown')} in {data.get('department', 'Unknown')}\n"
        f"Company: {data.get('company_name') or 'Not specified'} ({data.get('industry', 'Unknown')})\n"
        f"Tenure: {data.get('tenure', 'Unknown')}\n"
        f"Role Type: {data.get('role_type', 'Unknown')}"
        f"{' (' + data['role_type_weighting'] + ')' if data.get('role_type_weighting') else ''}\n"
        f"Key Tools: {tools}\n"
        f"Knowledge Priorities (ranked): {priorities}\n"
        f"Departure Type: {data.get('departure_type', 'Unknown')}\n"
        f"Sensitivity Flags: {data.get('sensitivity_flags') or 'None'}\n"
        f"Notice Period: {data.get('notice_period', 'Unknown')}"
    )

    flags = data.get("agent_flags", [])
    if flags:
        behavioural_instructions = (
            "\n## BEHAVIOURAL INSTRUCTIONS FOR THIS SESSION\n\n"
            + "\n".join(f"- {flag}" for flag in flags)
            + "\n"
        )
    else:
        behavioural_instructions = ""

    return STAGE2_BASE_SYSTEM_PROMPT.format(
        profile_context=profile_context,
        behavioural_instructions=behavioural_instructions,
    )


def build_greeting_message(profile: RoleIntelligenceProfile) -> str:
    """Build a tone-adapted greeting that includes the first role orientation question."""
    data = profile.model_dump() if hasattr(profile, "model_dump") else profile

    departure_type = data.get("departure_type", "voluntary")
    sensitivity = data.get("sensitivity_flags")

    if departure_type == "involuntary" or sensitivity:
        tone_intro = (
            "Thank you for taking the time to do this. I know transitions like this can be a "
            "lot, so I want to keep this as straightforward and comfortable as possible."
        )
    else:
        tone_intro = (
            "Thank you for taking the time to do this — it's genuinely going to make a real "
            "difference for whoever steps into your role next."
        )

    return (
        f"Hello — I'm KnowledgeKeeper, and I'm here to have a conversation about your role "
        f"so we can capture everything that would be valuable for the next person.\n\n"
        f"{tone_intro}\n\n"
        f"There are no wrong answers and nothing you say here will be used to evaluate you. "
        f"This is purely about making the transition as smooth as possible.\n\n"
        f"Let's start with the big picture. Can you describe your role in your own words — "
        f"not the job description version, but what you actually spend your time doing?"
    )


# Phase 1 — Role Orientation (5 questions, q0 is in the greeting)
ROLE_ORIENTATION_QUESTIONS = [
    "Ask them to describe their role in their own words — not the job description version, but what they actually spend their time doing.",
    "Ask what a typical week looks like — what are the recurring tasks, meetings, or responsibilities that happen without fail?",
    "Ask what the most time-consuming part of their role is and why.",
    "Ask what they would say are the two or three things they do that nobody else in the team fully understands or could pick up immediately.",
    "Ask how they would rate the volume of their role — is it steady and predictable, or does it spike at certain times? If it spikes, ask when and why.",
]

# Phase 2 — Knowledge Extraction Blocks (9 blocks)
BLOCK_QUESTIONS = {
    "client_stakeholder_relationships": [
        "Ask them to walk you through their most important client or stakeholder relationships — who are the key people and what is the nature of each relationship?",
        "For each key relationship mentioned: ask what that person or organisation expects, what they value most, and whether there are any sensitivities or history the next person needs to know about.",
        "Ask whether any of these relationships are at risk during a transition — and what the next person should do in the first two weeks to protect them.",
        "Ask whether there are any informal relationships — people they speak to outside of official channels — that the business may not be aware of but that get things done.",
        "Ask if there are any clients or stakeholders who are difficult to deal with and what the effective approach has been.",
        "Ask whether any client agreements, commitments, or expectations have been made verbally that are not documented anywhere.",
    ],
    "internal_processes_workflows": [
        "Ask them to walk you through the key processes they own or are the primary person responsible for — from start to finish.",
        "For each process: ask where it starts, what triggers it, what the steps are, and where it ends.",
        "Ask whether any of these processes are documented anywhere — and if not, why not.",
        "Ask whether there are steps in any process that are only possible because of their specific access, relationships, or knowledge.",
        "Ask whether any processes break down regularly and what they do when that happens.",
        "Ask whether there are any processes they inherited that they changed or adapted — and why they made those changes.",
        "Ask what the most common mistakes are that someone new to this role would make in these processes.",
    ],
    "technical_systems_tools": [
        "Ask them to list every system or tool they use regularly — even ones that seem minor.",
        "For each key system: ask what they use it for, how they use it, and whether there is anything about it that isn't obvious or took them time to learn.",
        "Ask whether there are any integrations between systems that they manage or that only work because of something they set up.",
        "Ask whether any systems have quirks, known bugs, or workarounds that the next person needs to know about.",
        "Ask whether they have any system access, admin rights, or credentials that are not shared with anyone else.",
        "Ask whether there are any automations, scripts, or shortcuts they have built or use that aren't documented.",
    ],
    "decision_making_logic": [
        "Ask them to think of a decision they make regularly that requires judgment rather than just following a rule — and walk you through how they think about it.",
        "Ask what factors they weigh when making that type of decision — what makes them lean one way versus another?",
        "Ask whether there are decisions they make that others in the team would probably make differently — and why they make them the way they do.",
        "Ask what the most important decision they make is — the one where getting it wrong has the biggest consequence.",
        "Ask whether there are any rules of thumb, principles, or mental shortcuts they rely on that they've never formally written down.",
        "Ask what they would tell their replacement to always do, and what to never do, in this role.",
    ],
    "team_dynamics_management": [
        "Ask them to describe the team they work within — who is on it, what everyone does, and how the team functions day to day.",
        "If they manage people: ask about each direct report — their strengths, their development areas, what motivates them, and what management style works best for each person.",
        "Ask whether there are any team dynamics or interpersonal considerations the next person should be aware of — not gossip, but practical context that would help them manage well from day one.",
        "Ask whether there are any team members who are likely to find this transition difficult, and what the best approach would be with them.",
        "Ask what the team's current biggest challenge is and what has been tried so far.",
    ],
    "supplier_vendor_relationships": [
        "Ask them to list the key suppliers or vendors they deal with regularly.",
        "For each key supplier: ask what the relationship is like, who the main contacts are, and whether there is any history or context the next person needs to know.",
        "Ask whether any supplier agreements, rates, or terms have been negotiated informally that are not reflected in written contracts.",
        "Ask whether any suppliers are underperforming and what the current situation is.",
        "Ask whether there are any upcoming supplier reviews, renewals, or decisions that the next person needs to be aware of.",
    ],
    "regulatory_compliance": [
        "Ask what regulatory or compliance requirements apply to their role and how they manage them day to day.",
        "Ask whether there are any upcoming deadlines, renewals, or submissions that the next person needs to action.",
        "Ask whether there are any compliance areas that are currently borderline, under review, or that they have been managing carefully.",
        "Ask whether the compliance processes they follow are fully documented or whether any rely on their personal knowledge or judgment.",
        "Ask whether there is a regulator or external body they have a direct relationship with — and what that relationship is like.",
    ],
    "undocumented_workarounds": [
        "Ask them to think about the things they do that are not in any process document, handbook, or onboarding guide — the stuff they just know.",
        "Ask whether there are any unofficial shortcuts or workarounds they use to get things done faster or more reliably than the official process allows.",
        "Ask whether there are things that broke badly in the past that they now prevent quietly without anyone really knowing — because they fixed the root cause or because they catch it early.",
        'Ask whether there are any "if X happens, always do Y" rules they follow that came from experience but were never written down.',
        "Ask whether there is anything they do that the business would be surprised to learn has never been formally documented.",
    ],
    "strategic_context": [
        "Ask what they understand to be the main strategic priorities for the business or their department right now.",
        "Ask how their role connects to those priorities — what they personally contribute to them.",
        "Ask whether there are any strategic initiatives, projects, or decisions currently in flight that the next person needs to pick up.",
        "Ask whether there are any strategic risks or opportunities they are aware of that they feel the business may be underestimating.",
        "Ask what advice they would give to their replacement about how to have the most impact in this role given the current direction of the business.",
    ],
}

# Phase 3 — Closing sequence (4 questions)
CLOSING_QUESTIONS = [
    'Ask: "Is there anything important about this role that I haven\'t asked you about — anything you think the next person absolutely needs to know?"',
    'Ask: "If you could give your replacement one piece of advice that isn\'t in any document anywhere, what would it be?"',
    'Ask: "Is there anyone in the business or outside it that you think they should speak to in their first two weeks — someone who would be particularly valuable for them to connect with early?"',
    "Thank them genuinely and close the session.",
]

CLOSING_MESSAGE = (
    "Thank you — this has been genuinely valuable. The knowledge you've shared is going to make "
    "a real difference to whoever steps into this role. I'll now compile everything into a "
    "structured handover document. You won't need to do anything else."
)

RISK_FLAG_CLASSIFIER_PROMPT_TEMPLATE = """\
You are a risk flag classifier for an employee knowledge extraction interview. \
Analyse the following answer from a departing employee and identify any risk flags.

Block: {block}
Question index: {question_index}
Question asked: {question}
Answer received: {answer}

Risk flag types to check for:
- single_point_of_failure: A process, relationship, or system that only this person understands or controls
- undocumented_critical_process: Something important that has never been written down
- relationship_at_risk: A client, supplier, or stakeholder relationship that may be fragile during transition
- access_credential_gap: System access or account ownership that has not been formally transferred
- in_flight_item: A project, negotiation, or decision currently in progress that needs immediate handover

Severity levels: critical, high, medium

Respond with ONLY a JSON array of risk flags found (empty array if none). Each flag must have:
{{"flag_type": "...", "severity": "...", "description": "...", "recommended_action": "..."}}

Example: [{{"flag_type": "single_point_of_failure", "severity": "critical", "description": "Only person who knows the SAP batch scheduling workaround", "recommended_action": "Document the workaround step-by-step before departure"}}]

Return [] if no risk flags detected.
"""

SINGLE_QUESTION_REPROMPT = (
    "Please rephrase your response to contain exactly ONE question. "
    "Do not ask multiple questions in a single message."
)
