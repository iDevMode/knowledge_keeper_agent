/**
 * Mock API layer — simulates full KnowledgeKeeper conversation flow.
 * Each stage progresses through scripted questions with realistic delays.
 */

const delay = (ms) => new Promise((r) => setTimeout(r, ms))

// ── Stage 1 conversation script ──────────────────────────────────────────────

const STAGE1_QUESTIONS = [
  // Block A: Business Context
  `Great, let's begin. First, I'd like to understand your organisation.\n\nWhat is the name of your company?`,
  `Thanks. What industry does the company operate in?`,
  `How would you describe the team structure around this role? For example, how large is the immediate team, and how does it fit within the wider organisation?`,
  `What are the key tools, platforms, or systems this person uses day-to-day?`,
  `How would you describe the company culture — formal, informal, or a mix of both?`,
  // Block B: Vacant Role
  `Now let's talk about the role itself.\n\nWhat is the job title of the departing employee?`,
  `Which department do they sit in?`,
  `How long have they been in this role?`,
  `Who do they report to?`,
  `Do they have any direct reports? If so, how many?`,
  `Would you describe this role as primarily process-oriented, decision-oriented, relationship-oriented, or a mix?`,
  `What do you see as the most immediate risk if this knowledge isn't captured?`,
  `Are there any areas of work that you suspect are largely undocumented?`,
  // Block C: Replacement Profile
  `Let's talk about the replacement.\n\nWill the replacement be an external hire or an internal transfer?`,
  `What experience level do you expect the replacement to be — junior, mid-level, or senior?`,
  `What's the single most important piece of context the replacement needs on day one?`,
  `How would you define success for the replacement in their first 90 days?`,
  `Is there any overlap period planned between the departing employee and their replacement?`,
  // Block D: Knowledge Priorities
  `Now I'd like you to rank your knowledge capture priorities.\n\nFrom the following categories, which is your top priority?\n1. Core processes & workflows\n2. Key relationships & stakeholders\n3. Decision-making context\n4. Tools & systems knowledge\n5. In-flight projects\n6. Undocumented workarounds\n\nPlease tell me your top 3 in order.`,
  // Block E: Output Preferences
  `Nearly there. A few questions about the output.\n\nWhere will this handover document live? For example, Google Drive, SharePoint, Notion, or somewhere else?`,
  `Who should receive the final document?`,
  `Are there any sections that should be marked as confidential or restricted?`,
  `Is there an existing handover template you'd like us to follow?`,
  // Block F: Departure Sensitivity
  `Finally, some questions about the departure context. These help me tailor the tone of the employee interview.\n\nIs this a voluntary or involuntary departure?`,
  `Would you say the employee is leaving on good terms?`,
  `Is the employee aware that this knowledge capture process is happening?`,
  `What is the notice period or remaining time before they leave?`,
]

const STAGE1_BLOCKS = [
  { name: 'business_context', until: 5 },
  { name: 'vacant_role', until: 13 },
  { name: 'replacement_profile', until: 18 },
  { name: 'knowledge_priorities', until: 19 },
  { name: 'output_preferences', until: 23 },
  { name: 'departure_sensitivity', until: 27 },
  { name: 'profile_generation', until: 999 },
]

function getStage1Block(questionIndex) {
  for (const block of STAGE1_BLOCKS) {
    if (questionIndex < block.until) return block.name
  }
  return 'profile_generation'
}

// ── Stage 2 conversation script ──────────────────────────────────────────────
//
// Structured to showcase the Phase 2 interview intelligence: entity-aware
// questions that call back to names mentioned earlier, an explicit information-
// gain follow-up, a dedicated entity-sweep turn before closing, honest
// section-of-total progress, and risk flags surfacing as they are detected.
// Each turn carries the metadata the real /status endpoint returns.

const STAGE2_SECTION_TOTAL = 6

const STAGE2_TURNS = [
  // ── Section 1 · Role orientation ──
  { block: 'role_orientation', section: 1,
    text: `To start, could you describe your role in your own words — not the job-description version, but what you actually spend your time on?` },
  { block: 'role_orientation', section: 1,
    text: `What does a typical week look like — the recurring things that happen without fail?` },
  { block: 'role_orientation', section: 1,
    text: `What are the two or three things you do that nobody else on the team could pick up immediately?` },
  { block: 'role_orientation', section: 1,
    text: `Where does your time actually go — what's the most time-consuming part of the role?` },
  { block: 'role_orientation', section: 1,
    text: `Does the workload spike at certain times? If so, when, and why?` },

  // ── Section 2 · Core processes (micro-summary opener + follow-up + risk) ──
  { block: 'core_processes', section: 2,
    text: `That gives me a clear picture of the day-to-day — thank you. We're moving into your core processes now (section 2 of 6, about a quarter of the way through).\n\nWalk me through the single most critical process you own, from start to finish.` },
  { block: 'core_processes', section: 2, followup: true,
    text: `Just to dig a little deeper on that — are there any steps that only work because of your specific access, relationships, or knowledge?` },
  { block: 'core_processes', section: 2, risk: true,
    text: `You mentioned that part isn't fully documented. If you were unavailable for a week, what would break first — and who would even notice?` },
  { block: 'core_processes', section: 2,
    text: `When this process goes wrong, how do you catch it — and how would someone new know what to look for?` },

  // ── Section 3 · Systems & access (risk: sole admin) ──
  { block: 'tools_and_systems', section: 3,
    text: `Great — that's your core processes captured. We're about a third of the way through now (section 3 of 6).\n\nWhich systems or tools do you use daily that a successor would need access to on day one?` },
  { block: 'tools_and_systems', section: 3, risk: true,
    text: `Are there any systems where you hold the only admin login, or credentials that have never been shared with anyone else?` },
  { block: 'tools_and_systems', section: 3,
    text: `Any quirks, known bugs, or "you just have to know" details about those systems that aren't written down?` },

  // ── Section 4 · Key relationships (entity-aware callback + risk) ──
  { block: 'key_relationships', section: 4,
    text: `Halfway there (section 4 of 6) — let's talk about people.\n\nWho are the key relationships — clients, partners, stakeholders — that the next person will need to build quickly?` },
  { block: 'key_relationships', section: 4, risk: true,
    text: `You mentioned Sarah Chen at Premier Bank earlier. What does that relationship actually depend on, and is it at risk during a transition?` },
  { block: 'key_relationships', section: 4,
    text: `Are there any informal relationships — people you go to off the record who quietly get things done?` },

  // ── Section 5 · Undocumented workarounds ──
  { block: 'undocumented_workarounds', section: 5,
    text: `Nearly through the main topics now (section 5 of 6).\n\nWhat are the things you just know — shortcuts or workarounds you use that aren't written down anywhere?` },
  { block: 'undocumented_workarounds', section: 5,
    text: `Any "if X happens, always do Y" rules you follow from experience that were never formally documented?` },

  // ── Entity sweep · circles back to something mentioned in passing ──
  { block: 'entity_sweep', section: 6, sweep: true,
    text: `Before we close — earlier you mentioned the reconciliation macro in passing, but we never dug into it. Who else knows how it works, and where does it actually live?` },

  // ── Section 6 · Closing ──
  { block: 'closing', section: 6,
    text: `Is there anything important we haven't covered that your successor absolutely needs to know?` },
  { block: 'closing', section: 6,
    text: `If you could give them one piece of advice for their first month — something that isn't in any document — what would it be?` },
  { block: 'closing', section: 6,
    text: `Last one: is there anything here you'd want flagged as sensitive or confidential in the handover document?` },
]

function stage2Turn(questionIndex) {
  return STAGE2_TURNS[Math.min(questionIndex, STAGE2_TURNS.length - 1)]
}

// ── Mock profile ─────────────────────────────────────────────────────────────

const MOCK_PROFILE = {
  company_name: 'Acme Corp',
  industry: 'Financial Services',
  team_structure: 'Team of 8 within Operations department',
  key_tools: ['Salesforce', 'Jira', 'Confluence', 'Slack'],
  culture_type: 'mixed',
  previous_knowledge_loss: null,
  job_title: 'Senior Operations Manager',
  department: 'Operations',
  tenure: '4 years',
  reports_to: 'VP Operations',
  direct_reports: '3',
  role_type: 'mixed',
  role_type_weighting: null,
  immediate_risk: 'Loss of client relationship context and process knowledge',
  undocumented_areas: 'Monthly reconciliation workarounds',
  key_external_relationships: 'Key banking partners, regulatory contacts',
  system_access_gaps: 'Legacy reporting tool admin access',
  hire_type: 'external',
  replacement_experience_level: 'mid',
  most_important_context: 'Client escalation protocols',
  success_definition_90_days: 'Maintain all client relationships, complete Q2 reconciliation',
  overlap_period: '2 weeks',
  priority_1: 'Core processes & workflows',
  priority_2: 'Key relationships & stakeholders',
  priority_3: 'Undocumented workarounds',
  supporting_categories: ['Decision-making context', 'In-flight projects'],
  document_destination: 'Google Drive',
  recipients: ['VP Operations', 'HR Director'],
  confidential_sections: 'Salary discussions, client contract details',
  existing_template: null,
  departure_type: 'voluntary',
  leaving_on_good_terms: 'yes',
  employee_aware: true,
  sensitivity_flags: null,
  notice_period: '4 weeks',
  agent_flags: ['tone_warm', 'depth_full_processes', 'flag_relationship_risk'],
}

// ── Session state ────────────────────────────────────────────────────────────

const sessions = {}

function createSession(stage, stage1Id) {
  const id = 'mock-' + Math.random().toString(36).slice(2, 10)
  sessions[id] = {
    stage,
    questionIndex: 0,
    complete: false,
    riskFlagCount: 0,
    stage1Id: stage1Id || null,
  }
  return id
}

// ── Exported mock API functions ──────────────────────────────────────────────

export async function createStage1Session() {
  await delay(600)
  const sessionId = createSession(1)
  const greeting = `Welcome to KnowledgeKeeper. I'm here to help you set up a knowledge capture session for a departing employee.\n\nThis will take around 10–15 minutes. I'll ask you a series of questions about the role, the business context, and your priorities. At the end, I'll generate a Role Intelligence Profile that will guide the employee interview.\n\nLet's get started.`
  return { session_id: sessionId, message: greeting }
}

export async function createStage2Session(inviteToken, _consentAcknowledged = true) {
  await delay(800)
  const sessionId = createSession(2, inviteToken)
  const greeting = `Hi there. Thanks for agreeing to take part in this knowledge capture session — I really appreciate your time.\n\nThis conversation is designed to help capture the important knowledge you carry in your role, so that your team and your successor have the best possible foundation going forward. There are no right or wrong answers — I'm just here to listen and ask the right questions.\n\nEverything you share will be used to create a handover document. You'll have the opportunity to flag anything as confidential.\n\nShall we begin?`
  return { session_id: sessionId, message: greeting }
}

export async function sendMessage(sessionId, message) {
  await delay(800 + Math.random() * 1200)

  const session = sessions[sessionId]
  if (!session) throw new Error('Session not found')

  const total = session.stage === 1 ? STAGE1_QUESTIONS.length : STAGE2_TURNS.length
  const idx = session.questionIndex

  if (idx >= total) {
    session.complete = true

    if (session.stage === 1) {
      return {
        message: `Thank you — I now have everything I need.\n\nI've generated a Role Intelligence Profile based on your answers. Please review the summary and, if everything looks correct, you can share the employee interview link with the departing team member.`,
        session_complete: true,
        profile: MOCK_PROFILE,
        invite_token: 'mock-invite-' + sessionId,
        manager_token: 'mock-manager-' + sessionId,
      }
    }

    return {
      message: `Thank you so much for your time and openness today. The knowledge you've shared is incredibly valuable and will make a real difference for your team and successor.\n\nYour answers are being compiled into a structured handover document now — the manager will receive it automatically. You don't need to do anything else.`,
      session_complete: true,
      profile: null,
    }
  }

  let messageText
  if (session.stage === 1) {
    messageText = STAGE1_QUESTIONS[idx]
  } else {
    const turn = STAGE2_TURNS[idx]
    messageText = turn.text
    // Risk flags surface as the agent detects them, exactly as the real
    // parallel risk-classifier branch would.
    if (turn.risk) session.riskFlagCount++
  }

  session.questionIndex++

  return {
    message: messageText,
    session_complete: false,
    profile: null,
  }
}

function stage2Progress(session) {
  const idx = Math.min(session.questionIndex, STAGE2_TURNS.length - 1)
  const turn = stage2Turn(idx)
  const percent = session.complete
    ? 100
    : Math.min(Math.round(((idx + 1) / STAGE2_TURNS.length) * 100), 99)
  return {
    phase: turn.block === 'closing' || turn.block === 'entity_sweep' ? 'closing_sequence' : 'knowledge_blocks',
    section_number: turn.section,
    section_total: STAGE2_SECTION_TOTAL,
    percent,
    questions_asked: session.questionIndex,
    question_budget: STAGE2_TURNS.length + 6,
  }
}

export async function getSessionStatus(sessionId) {
  const session = sessions[sessionId]
  if (!session) throw new Error('Session not found')

  const currentBlock =
    session.stage === 1 ? getStage1Block(session.questionIndex) : stage2Turn(session.questionIndex).block

  return {
    session_id: sessionId,
    stage: session.stage,
    session_complete: session.complete,
    current_block: currentBlock,
    current_question_index: session.questionIndex,
    progress: session.stage === 2 ? stage2Progress(session) : null,
  }
}

export async function getSessionHistory(sessionId) {
  await delay(300)
  const session = sessions[sessionId]
  // Demo mock keeps no server-side transcript; return empty so the page falls
  // back to its "welcome back" path. Real backend returns the full transcript.
  if (!session) return { session_id: sessionId, stage: 0, session_complete: false, messages: [] }
  const currentBlock =
    session.stage === 1 ? getStage1Block(session.questionIndex) : stage2Turn(session.questionIndex).block
  return {
    session_id: sessionId,
    stage: session.stage,
    session_complete: session.complete,
    current_block: currentBlock,
    messages: [],
  }
}

// ── Mock document content ────────────────────────────────────────────────────

const MOCK_DOCUMENT_MARKDOWN = `# KnowledgeKeeper — Handover Document

**Employee:** Senior Operations Manager
**Department:** Operations
**Company:** Acme Corp
**Generated:** ${new Date().toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' })}

---

## Section 1: Role Overview

The Senior Operations Manager role sits within a team of 8 in the Operations department, reporting directly to the VP Operations with 3 direct reports. The role has been held for 4 years and is classified as a mixed role encompassing process management, decision-making, and relationship management.

### Key Responsibilities
- Ownership of the monthly reconciliation process end-to-end
- Client escalation management and resolution
- Oversight of quarterly reporting cycles
- Management of banking partner relationships
- Team coordination across 3 direct reports

---

## Section 2: Risk Summary

| # | Risk Flag | Severity | Description | Recommended Action |
|---|-----------|----------|-------------|--------------------|
| 1 | Single Point of Failure | Critical | Monthly reconciliation process relies entirely on undocumented workarounds developed over 4 years | Document all workarounds immediately; assign shadow resource |
| 2 | Access Credential Gap | High | Legacy reporting tool admin access held solely by departing employee | Transfer admin access before departure; document login procedures |
| 3 | Relationship at Risk | High | Key banking partner relationships built on personal rapport over 4 years | Schedule introduction meetings with replacement within first 2 weeks |

---

## Section 3: Core Processes & Workflows

### Monthly Reconciliation Process
1. Pull raw transaction data from Salesforce (first Monday of each month)
2. Cross-reference against banking partner statements (received via email from 3 contacts)
3. Run custom Excel macro (stored in personal OneDrive — needs to be transferred)
4. Flag discrepancies over £500 threshold for manual review
5. Submit reconciliation report to VP Operations by 10th of each month

**Known workaround:** Step 3 officially uses the standard finance template, but the employee developed a custom macro 3 years ago that catches formatting inconsistencies the standard template misses. This macro has never been documented or shared.

**Common failure point:** Banking partner statements occasionally arrive late. The employee has built direct relationships with 2 contacts at the bank who will expedite on request — this relationship context needs to be passed to the replacement.

### Client Escalation Protocol
1. Escalation received via Jira ticket or direct Slack message
2. Severity assessed using internal framework (documented in Confluence)
3. For P1/P2: direct phone call to client within 2 hours
4. Resolution tracked in Jira with client copied on updates
5. Post-mortem documented for any P1 incident

---

## Section 4: Key Relationships & Stakeholders

### Internal
- **VP Operations (direct manager):** Weekly 1:1 on Thursdays, expects written updates for anything escalated
- **Finance Director:** Monthly reconciliation handoff, prefers email over Slack
- **Product Team Lead:** Regular sync on operational impact of product changes

### External
- **Sarah Chen (Premier Bank):** Primary contact for reconciliation queries, responds fastest on mobile
- **David Morrison (Regulatory Affairs, FCA):** Annual compliance review contact, formal communication style preferred
- **3 key enterprise clients:** Quarterly business reviews, personal relationships built over 3+ years

---

## Section 5: Tools & Systems

| Tool | Access Level | Notes |
|------|-------------|-------|
| Salesforce | Admin | Primary CRM — custom dashboards need to be shared |
| Jira | Project Admin | Manages all ops tickets and escalation workflows |
| Confluence | Space Admin | Operations knowledge base owner |
| Legacy Reporting Tool | Sole Admin | Critical gap — only person with admin credentials |
| Slack | Standard | Key channels: #ops-escalations, #finance-sync, #leadership |

---

## Section 6: In-Flight Projects

### Q2 System Migration (60% complete)
- **Status:** In progress, on track
- **Next milestone:** UAT testing begins March 15
- **Risk:** Delay could impact monthly reconciliation process
- **Key contact:** IT Project Manager (James Wells)

### Client Onboarding Process Redesign (30% complete)
- **Status:** Discovery phase
- **Next milestone:** Stakeholder review March 28
- **Risk:** Low — can be paused and handed over
- **Key contact:** Product Team Lead

---

## Section 7: Undocumented Workarounds

1. **Reconciliation macro** — Custom Excel macro that catches formatting issues the standard template misses. Stored in personal OneDrive. Needs to be moved to shared drive and documented.
   [GAP: The macro's source logic was never fully explained — schedule a working session to document it before departure.]

2. **Escalation fast-track** — For urgent client escalations, bypasses the standard Jira workflow by messaging the VP Operations directly on Slack with a specific emoji prefix (🔴) to signal immediate attention needed.

3. **Banking statement chase** — When monthly statements are late, contacts Sarah Chen directly on mobile rather than going through the bank's official channels. Response time drops from 3 days to same-day.

---

## Section 8: Advice for Successor

> "The most important thing is building relationships with the banking partners early. The processes you can learn, but the trust takes time. Book face-to-face meetings in your first two weeks — don't rely on email. And for the reconciliation process, don't trust the official documentation until you've run through it once with the macro. It catches things the standard process misses."

---

## Appendix: 90-Day Success Criteria

- [ ] Maintain all existing client relationships without escalation
- [ ] Complete Q2 reconciliation cycle independently
- [ ] Obtain admin access to all systems listed in Section 5
- [ ] Meet all key stakeholders (internal and external) within first 3 weeks
- [ ] Shadow at least one full reconciliation cycle before going solo

---

*Generated by KnowledgeKeeper | Nukode — extract-then-compose synthesis, 3 risk flags (de-duplicated), QA review score 0.91/1.0.*
`

// Store generated blob URLs for cleanup
const _blobUrls = {}
const _managerDocs = {} // stage1SessionId -> { document_id, status }

function _createMockDocument() {
  const docId = 'mock-doc-' + Math.random().toString(36).slice(2, 8)
  const blob = new Blob([MOCK_DOCUMENT_MARKDOWN], { type: 'text/markdown;charset=utf-8' })
  _blobUrls[docId] = URL.createObjectURL(blob)
  return docId
}

export async function getManagerOverview(stage1SessionId) {
  await delay(400)

  // Simulate the auto-generated document appearing shortly after completion
  if (!_managerDocs[stage1SessionId]) {
    _managerDocs[stage1SessionId] = {
      document_id: _createMockDocument(),
      status: 'complete',
    }
  }
  const doc = _managerDocs[stage1SessionId]

  return {
    stage1_session_id: stage1SessionId,
    stage2_status: 'complete',
    risk_flag_count: 3,
    document_id: doc.document_id,
    document_status: doc.status,
    download_url: _blobUrls[doc.document_id],
    document_error: null,
    delivery_email: _deliveryEmails[stage1SessionId] || null,
  }
}

export async function managerGenerateDocument(stage1SessionId) {
  await delay(3000)
  const docId = _createMockDocument()
  _managerDocs[stage1SessionId] = { document_id: docId, status: 'complete' }
  return {
    document_id: docId,
    download_url: _blobUrls[docId],
    status: 'complete',
  }
}

const _deliveryEmails = {}

export async function setDeliveryEmail(stage1SessionId, token, email) {
  await delay(400)
  _deliveryEmails[stage1SessionId] = email
  return { ok: true, emailed: Boolean(_managerDocs[stage1SessionId]) }
}

export async function deleteEngagement(stage1SessionId) {
  await delay(500)
  delete _managerDocs[stage1SessionId]
  delete _deliveryEmails[stage1SessionId]
  return { ok: true, deleted: true }
}

export function getDownloadUrl(documentId) {
  return _blobUrls[documentId] || '#'
}
