# KnowledgeKeeper — Agent Package Notes

Loaded when working under `agents/`. For project-wide constraints see the root `CLAUDE.md`.

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
