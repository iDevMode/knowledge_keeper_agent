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

const STAGE2_QUESTIONS = [
  // Role orientation
  `Thanks for taking the time to do this — it's genuinely valuable.\n\nTo start, could you describe your role in your own words? What does a typical week look like for you?`,
  `What are the most important things you do that wouldn't happen if you weren't here?`,
  `Who are the key people you work with most closely, internally and externally?`,
  `Are there any recurring meetings, reports, or deadlines that are solely your responsibility?`,
  `If you could only pass on three things to your replacement, what would they be?`,
  // Knowledge blocks
  `Let's dig into your core processes.\n\nWalk me through the most critical process you own from start to finish.`,
  `Are there any steps in that process that are different from how they're officially documented?`,
  `What tends to go wrong with this process, and how do you handle it when it does?`,
  `Are there any tools or systems where you're the only person who really knows how they work?`,
  `Tell me about the key relationships you've built in this role — clients, partners, stakeholders. Who would your replacement need to build rapport with quickly?`,
  `Are there any in-flight projects or initiatives that will need to be handed over?`,
  `What's the current status of each, and what are the next critical milestones?`,
  `Are there any workarounds, shortcuts, or unofficial processes you've developed that aren't written down anywhere?`,
  `What are the things that only you know — the stuff that would be lost if we didn't capture it today?`,
  // Closing
  `We're nearly done. A few closing questions.\n\nIs there anything we haven't covered that you think your replacement absolutely needs to know?`,
  `If you could give your replacement one piece of advice for their first month, what would it be?`,
  `Is there anything you'd want flagged as sensitive or confidential in the handover document?`,
  `Any final thoughts before we wrap up?`,
]

const STAGE2_BLOCKS = [
  { name: 'role_orientation', until: 5 },
  { name: 'core_processes', until: 8 },
  { name: 'tools_and_systems', until: 9 },
  { name: 'key_relationships', until: 10 },
  { name: 'in_flight_projects', until: 12 },
  { name: 'undocumented_workarounds', until: 14 },
  { name: 'closing', until: 999 },
]

function getStage2Block(questionIndex) {
  for (const block of STAGE2_BLOCKS) {
    if (questionIndex < block.until) return block.name
  }
  return 'closing'
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

export async function createStage2Session(stage1SessionId) {
  await delay(800)
  const sessionId = createSession(2, stage1SessionId)
  const greeting = `Hi there. Thanks for agreeing to take part in this knowledge capture session — I really appreciate your time.\n\nThis conversation is designed to help capture the important knowledge you carry in your role, so that your team and your successor have the best possible foundation going forward. There are no right or wrong answers — I'm just here to listen and ask the right questions.\n\nEverything you share will be used to create a handover document. You'll have the opportunity to flag anything as confidential.\n\nShall we begin?`
  return { session_id: sessionId, message: greeting }
}

export async function sendMessage(sessionId, message) {
  await delay(800 + Math.random() * 1200)

  const session = sessions[sessionId]
  if (!session) throw new Error('Session not found')

  const questions = session.stage === 1 ? STAGE1_QUESTIONS : STAGE2_QUESTIONS
  const idx = session.questionIndex

  // Stage 2 risk flags — add some at specific question indices
  if (session.stage === 2 && [6, 8, 13].includes(idx)) {
    session.riskFlagCount++
  }

  if (idx >= questions.length) {
    session.complete = true

    if (session.stage === 1) {
      return {
        message: `Thank you — I now have everything I need.\n\nI've generated a Role Intelligence Profile based on your answers. Please review the summary and, if everything looks correct, you can share the employee interview link with the departing team member.`,
        session_complete: true,
        profile: MOCK_PROFILE,
      }
    }

    return {
      message: `Thank you so much for your time and openness today. The knowledge you've shared is incredibly valuable and will make a real difference for your team and successor.\n\nYour handover document is ready to be generated. You can choose your preferred format below.`,
      session_complete: true,
      profile: null,
    }
  }

  session.questionIndex++

  return {
    message: questions[idx],
    session_complete: false,
    profile: null,
  }
}

export async function getSessionStatus(sessionId) {
  const session = sessions[sessionId]
  if (!session) throw new Error('Session not found')

  const getBlock = session.stage === 1 ? getStage1Block : getStage2Block

  return {
    session_id: sessionId,
    stage: session.stage,
    session_complete: session.complete,
    current_block: getBlock(session.questionIndex),
    current_question_index: session.questionIndex,
    risk_flag_count: session.riskFlagCount,
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

*Generated by KnowledgeKeeper | Nukode*
`

// Store generated blob URLs for cleanup
const _blobUrls = {}

export async function generateDocument(sessionId, format) {
  await delay(3000)
  const docId = 'mock-doc-' + Math.random().toString(36).slice(2, 8)

  // Create a downloadable blob
  const blob = new Blob([MOCK_DOCUMENT_MARKDOWN], { type: 'text/markdown;charset=utf-8' })
  const blobUrl = URL.createObjectURL(blob)
  _blobUrls[docId] = blobUrl

  return {
    document_id: docId,
    download_url: blobUrl,
  }
}

export function getDownloadUrl(documentId) {
  return _blobUrls[documentId] || '#'
}
