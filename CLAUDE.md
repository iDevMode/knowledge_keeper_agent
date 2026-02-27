# KnowledgeKeeper — Claude Code Project Instructions
### Nukode | AI Automation Agency

---

## PROJECT OVERVIEW

**Product Name:** KnowledgeKeeper
**Builder:** Nukode (nukode.co.uk)
**Product Type:** Three-stage AI agent system for institutional knowledge capture during employee departures
**Current Phase:** MVP Build

KnowledgeKeeper is a multi-agent system that captures critical institutional knowledge when an employee leaves a business. It operates across three sequential stages:

- **Stage 1** — Business Interview (Manager/HR configures the agent)
- **Stage 2** — Employee Interview (departing employee is interviewed by the agent)
- **Stage 3** — Document Generation (synthesised handover pack is produced)

The Role Intelligence Profile produced in Stage 1 is passed as structured context into Stage 2 before the employee session begins. Stage 3 is triggered automatically on Stage 2 completion.

---

## REPOSITORY STRUCTURE

```
knowledgekeeper/
├── agents/
│   ├── stage1_business_interview/
│   │   ├── graph.py              # LangGraph state machine for Stage 1
│   │   ├── nodes.py              # Individual node definitions
│   │   ├── prompts.py            # Stage 1 system prompt and question blocks
│   │   └── state.py              # Stage 1 state schema
│   ├── stage2_employee_interview/
│   │   ├── graph.py              # LangGraph state machine for Stage 2
│   │   ├── nodes.py              # Node definitions including follow-up logic
│   │   ├── prompts.py            # Stage 2 system prompt and all question blocks
│   │   ├── blocks.py             # Nine modular knowledge extraction blocks
│   │   └── state.py              # Stage 2 state schema including risk flag tracker
│   └── stage3_document_generation/
│       ├── generator.py          # Document synthesis logic
│       ├── prompts.py            # Stage 3 generation prompt
│       └── templates/            # Output document templates
│           ├── full_handover.md
│           └── risk_summary.md
├── models/
│   ├── role_intelligence_profile.py   # Pydantic model for Stage 1 output
│   ├── risk_flags.py                  # Risk flag enum and tagging logic
│   └── knowledge_blocks.py            # Block priority and routing logic
├── api/
│   ├── routes.py                 # FastAPI route definitions
│   ├── session_manager.py        # Session creation, linking Stage 1 → Stage 2
│   └── webhooks.py               # Stage completion triggers
├── output/
│   ├── exporters/
│   │   ├── pdf_exporter.py
│   │   ├── word_exporter.py
│   │   └── notion_exporter.py    # Phase 2
│   └── formatters/
│       └── document_formatter.py
├── tests/
│   ├── test_stage1.py
│   ├── test_stage2.py
│   ├── test_stage3.py
│   └── fixtures/
│       └── sample_role_profiles.json
├── config/
│   ├── settings.py               # Environment config, model selection
│   └── constants.py              # Block names, flag types, priority enums
├── CLAUDE.md                     # This file
├── requirements.txt
├── .env.example
└── README.md
```

---

## TECH STACK

| Layer | Technology |
|---|---|
| Agent Framework | LangGraph |
| LLM | Claude claude-sonnet-4-6 (primary), Claude Haiku (follow-up classification) |
| API Layer | FastAPI |
| Data Validation | Pydantic v2 |
| Document Output (MVP) | Python-docx, WeasyPrint (PDF) |
| Document Output (Phase 2) | Notion API, Confluence API, SharePoint API |
| Session Storage | Redis (session state between stages) |
| Database | PostgreSQL (role profiles, interview transcripts, documents) |
| Deployment | Docker + Railway or Render for MVP |

---

## CORE ARCHITECTURE PRINCIPLES

**1. Stages are isolated conversation sessions**
Stage 1 and Stage 2 must never share a live conversation thread. They are separate LangGraph graph instances, each with their own session ID. The Role Intelligence Profile is the only data passed between them — via Redis or database lookup, never via a shared context window.

**2. The Role Intelligence Profile is the system's spine**
Every decision in Stage 2 — which blocks to run at full depth, which to run lightly, what tone to use, which risk categories to prioritise — is derived from the Role Intelligence Profile. Treat it as the single source of truth for a session. Its Pydantic schema must be strictly validated before Stage 2 activates.

**3. One question at a time — always**
Both Stage 1 and Stage 2 agents must never output more than one question per turn. This is a hard constraint enforced at the node level, not just in the prompt. Add an output validation step that checks for multiple question marks and re-routes if detected.

**4. Follow-up logic is a separate classification step**
Do not rely on the main LLM to decide whether a follow-up is needed. Use a lightweight classifier node (Claude Haiku) that receives the last question and answer and returns a structured decision: `{needs_followup: bool, reason: str, suggested_followup: str}`. The main graph uses this to route to a follow-up node or advance to the next question.

**5. Risk flags run as a parallel branch**
In Stage 2, risk flag detection runs as a parallel LangGraph branch on every answer. It does not block the main conversation flow. It appends to a `risk_flags: List[RiskFlag]` field in the state. These are surfaced in Section 2 of the final document.

**6. Stage 3 is a single synthesis call**
Do not stream Stage 3 section by section. Compile the full context (Role Intelligence Profile + Stage 1 transcript summary + Stage 2 full transcript + risk flags) and generate the complete document in one call. This gives the LLM full context for coherent cross-referencing between sections.

---

## LANGGRAPH GRAPH DESIGN

### Stage 1 Graph

```
START
  → greeting_node
  → [block_a] business_context (5 questions)
  → [block_b] vacant_role (8 questions)
  → [block_c] replacement_profile (5 questions)
  → [block_d] knowledge_priorities (prioritisation exercise)
  → [block_e] output_preferences (4 questions)
  → [block_f] departure_sensitivity (4 questions)
  → profile_generation_node       ← generates Role Intelligence Profile
  → profile_review_node           ← presents profile for manager confirmation
  → [conditional] corrections_node OR finalise_node
  → session_close_node
END
```

Each block node contains:
- The current question index within that block
- An answer store
- A route to the follow-up classifier
- A route back to the next question or next block

### Stage 2 Graph

```
START
  → load_profile_node             ← loads Role Intelligence Profile from Redis/DB
  → greeting_node                 ← tone adapted from departure sensitivity flags
  → [phase_1] role_orientation    ← always runs, 5 questions
  → block_router_node             ← determines block order and depth from profile
  → [dynamic blocks in priority order]
      → priority_1_block (full depth — all questions)
      → priority_2_block (full depth — all questions)
      → priority_3_block (full depth — all questions)
      → remaining_selected_blocks (light touch — questions 1-3 only)
      → undocumented_workarounds_block (always full depth)
  → [phase_3] closing_sequence    ← 4 closing questions always run
  → session_complete_node
END

Parallel branch (runs on every answer):
  → risk_flag_classifier_node     ← appends to risk_flags in state
```

### Follow-up Sub-graph (called from any question node in Stage 2)

```
answer_received
  → followup_classifier_node      ← Haiku: needs_followup? + suggested question
  → [conditional]
      needs_followup AND followup_count < 3
        → followup_question_node
        → answer_received (loop)
      OR
      no_followup OR followup_count >= 3
        → advance_to_next_question
```

---

## STATE SCHEMAS

### Stage 1 State

```python
class Stage1State(TypedDict):
    session_id: str
    business_name: str
    current_block: str
    current_question_index: int
    answers: Dict[str, Any]
    conversation_history: List[BaseMessage]
    role_intelligence_profile: Optional[RoleIntelligenceProfile]
    profile_confirmed: bool
    session_complete: bool
```

### Stage 2 State

```python
class Stage2State(TypedDict):
    session_id: str
    stage1_session_id: str
    profile: RoleIntelligenceProfile
    current_block: str
    current_question_index: int
    followup_count: int
    block_order: List[str]          # derived from profile priority ranking
    block_depths: Dict[str, str]    # "full" or "light" per block
    answers: Dict[str, List[str]]   # keyed by block name
    conversation_history: List[BaseMessage]
    risk_flags: List[RiskFlag]
    session_complete: bool
```

### Role Intelligence Profile

```python
class RoleIntelligenceProfile(BaseModel):
    # Business Context
    company_name: Optional[str]
    industry: str
    team_structure: str
    key_tools: List[str]
    culture_type: Literal["formal", "informal", "mixed"]
    previous_knowledge_loss: Optional[str]

    # Vacant Role
    job_title: str
    department: str
    tenure: str
    reports_to: str
    direct_reports: Optional[str]
    role_type: Literal["process", "decision", "relationship", "mixed"]
    role_type_weighting: Optional[str]
    immediate_risk: str
    undocumented_areas: Optional[str]
    key_external_relationships: Optional[str]
    system_access_gaps: Optional[str]

    # Replacement
    hire_type: Literal["external", "internal"]
    replacement_experience_level: Literal["junior", "mid", "senior"]
    most_important_context: str
    success_definition_90_days: str
    overlap_period: Optional[str]

    # Knowledge Priorities
    priority_1: str
    priority_2: str
    priority_3: str
    supporting_categories: List[str]

    # Output Preferences
    document_destination: str
    recipients: List[str]
    confidential_sections: Optional[str]
    existing_template: Optional[str]

    # Departure Context
    departure_type: Literal["voluntary", "involuntary"]
    leaving_on_good_terms: Literal["yes", "no", "unclear"]
    employee_aware: bool
    sensitivity_flags: Optional[str]
    notice_period: str

    # Agent Instruction Flags (auto-generated)
    agent_flags: List[str]
```

### Risk Flag

```python
class RiskFlag(BaseModel):
    flag_type: Literal[
        "single_point_of_failure",
        "undocumented_critical_process",
        "relationship_at_risk",
        "access_credential_gap",
        "in_flight_item"
    ]
    severity: Literal["critical", "high", "medium"]
    description: str
    recommended_action: str
    source_block: str
    source_question_index: int
```

---

## PROMPT FILES

All prompts live in `prompts.py` within each stage directory. They are never hardcoded inline in graph nodes. Prompts are constructed dynamically using the Role Intelligence Profile at runtime.

The full prompt content for all three stages is documented in:
`/docs/knowledge-extraction-agent-prompt.md`

Key prompt construction rules:
- Stage 2 system prompt is assembled at session start by injecting the serialised Role Intelligence Profile into the base prompt template
- Agent instruction flags from the profile are appended as a `## BEHAVIOURAL INSTRUCTIONS FOR THIS SESSION` block at the end of the Stage 2 system prompt
- Block question lists are injected as the relevant block node is entered — not all at once at session start

---

## ENVIRONMENT VARIABLES

```env
# LLM
ANTHROPIC_API_KEY=
PRIMARY_MODEL=claude-sonnet-4-6
CLASSIFIER_MODEL=claude-haiku-4-5-20251001

# Database
DATABASE_URL=postgresql://...
REDIS_URL=redis://...

# Session
SESSION_TTL_HOURS=72
STAGE1_TO_STAGE2_LINK_TTL_HOURS=168

# Output
DEFAULT_OUTPUT_FORMAT=docx        # docx | pdf
NOTION_API_KEY=                   # Phase 2
CONFLUENCE_API_KEY=               # Phase 2
SHAREPOINT_CLIENT_ID=             # Phase 2

# API
API_SECRET_KEY=
ALLOWED_ORIGINS=

# Environment
ENVIRONMENT=development           # development | staging | production
LOG_LEVEL=INFO
```

---

## DEVELOPMENT CONVENTIONS

**Naming**
- Node functions: `snake_case` with `_node` suffix e.g. `greeting_node`, `risk_flag_classifier_node`
- State fields: `snake_case`
- Block names: match exactly the nine knowledge categories defined in `constants.py`
- Graph files: one graph per file, graph object always named `graph`

**Error Handling**
- If the LLM returns a malformed response at any node, retry once with an explicit format instruction before raising
- If Stage 1 profile validation fails, return the specific validation errors to the session and ask the manager to clarify — do not silently fill defaults
- If the follow-up classifier call fails, default to `needs_followup: false` and advance — never block the main conversation for a classifier failure

**Testing**
- Every block in Stage 2 must have a unit test with a sample answer set covering: clear answer (no followup needed), vague answer (followup needed), refusal (move on), and partial answer (flag as gap)
- Stage 3 document generation tests use fixture Role Intelligence Profiles from `tests/fixtures/sample_role_profiles.json`
- Include at least one fixture for each role type: process-heavy, decision-heavy, relationship-heavy

**Logging**
- Log session ID, stage, block, and question index on every LLM call
- Log all risk flags as they are detected with their source block
- Never log raw conversation content in production — log metadata only

---

## MVP SCOPE — WHAT IS IN AND OUT

**In scope for MVP:**
- Stage 1 chat interface (web)
- Stage 2 chat interface (web, separate session link)
- Stage 3 document output as Word (.docx) and PDF
- Risk flag detection and Risk Summary section
- Role Intelligence Profile generation and manager review step
- Follow-up classifier logic
- Email delivery of completed document to recipients

**Out of scope for MVP (Phase 2):**
- Notion, Confluence, SharePoint integrations
- WhatsApp or email-based interview interface
- Multi-language support
- Analytics dashboard
- Bulk/concurrent session management for enterprise
- Existing template ingestion and matching

---

## KNOWN DESIGN DECISIONS AND RATIONALE

**Why two separate session links rather than one conversation?**
Keeping Manager and Employee sessions fully separate prevents any awkwardness, ensures the employee can be candid, and gives the manager a review checkpoint between stages. It also makes the product feel more considered and enterprise-appropriate.

**Why a separate Haiku classifier for follow-up decisions?**
Relying on the main model to self-decide when to follow up creates inconsistency — sometimes it will, sometimes it won't, depending on subtle prompt drift across a long conversation. A dedicated lightweight classifier with a structured boolean output is more reliable and cheaper to run at scale.

**Why generate Stage 3 in a single call rather than section by section?**
Cross-referencing between sections (e.g. a risk flag in Section 2 referencing a relationship in Section 5) requires the full context to be present at generation time. Section-by-section generation loses this coherence.

**Why Redis for session state between stages?**
The gap between Stage 1 and Stage 2 could be hours or days (the manager completes Stage 1, then shares the link with the employee). Redis with a TTL handles this naturally without keeping a database connection warm.

---

## USEFUL COMMANDS

```bash
# Install dependencies
pip install -r requirements.txt

# Run development server
uvicorn api.routes:app --reload --port 8000

# Run tests
pytest tests/ -v

# Run Stage 1 in isolation (CLI for testing)
python -m agents.stage1_business_interview.graph --mode cli

# Run Stage 2 with a fixture profile (CLI for testing)
python -m agents.stage2_employee_interview.graph --mode cli --profile tests/fixtures/sample_role_profiles.json --profile-id relationship_heavy

# Generate a test document from fixture transcripts
python -m agents.stage3_document_generation.generator --stage1 tests/fixtures/stage1_transcript.json --stage2 tests/fixtures/stage2_transcript.json
```

---

## CURRENT STATUS

| Component | Status |
|---|---|
| Stage 1 prompt | ✅ Complete |
| Stage 2 prompt + all blocks | ✅ Complete |
| Stage 3 document generation prompt | ✅ Complete |
| Role Intelligence Profile schema | ✅ Defined |
| Risk Flag schema | ✅ Defined |
| LangGraph graphs | 🔲 Not started |
| Follow-up classifier node | 🔲 Not started |
| FastAPI routes | 🔲 Not started |
| Document exporters | 🔲 Not started |
| Frontend chat interface | 🔲 Not started |

---

*KnowledgeKeeper | Nukode | CLAUDE.md v1.0*