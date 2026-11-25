# Data retention and deletion

What KnowledgeKeeper keeps, for how long, and how to delete it sooner. The code
that enforces this is `api/retention.py`; the limits are settings, shown here
with their defaults.

This describes how the software behaves. It is not legal advice. Each
organisation using KnowledgeKeeper should check that these periods fit its own
data-protection obligations (for example under UK GDPR) and tell departing
employees how their interview will be used.

## What is stored

| Data | Contains | Kept for | Setting |
|---|---|---|---|
| Manager's setup interview (Stage 1 session, transcript, Role Intelligence Profile) | Business context, the role, departure circumstances, recipient email addresses | Until the interview sessions expire, normally 72 hours. Extended so it lasts as long as the employee's interview. | `SESSION_TTL_HOURS` |
| Employee's interview (Stage 2 session and transcript) | Everything the departing employee said | 72 hours from when the manager created the employee link | `SESSION_TTL_HOURS` |
| Handover pack (Word or PDF), including its Risk Summary | The synthesised handover document | **365 days from generation**, then deleted automatically | `DOCUMENT_RETENTION_DAYS` |
| Dashboard entry for a handover | Job title, dates, internal ids — no interview content | As long as its pack. A handover that never produced a pack stays listed (as expired) for 30 days after its interviews expire. | `EXPIRED_HANDOVER_VISIBLE_DAYS` |
| Manager account | Email address | Until deleted on request (no self-service account deletion yet) | — |
| Sign-in links | A SHA-256 hash of the one-time token | 15 minutes, then removed | `LOGIN_LINK_TTL_MINUTES` |

Transcripts are the full LangGraph conversation checkpoints. Before this policy
was implemented they were never deleted; the retention sweep now removes any
transcript whose session no longer exists, including old ones.

When a pack is regenerated, the new version replaces the old one as soon as it
finishes. If regeneration fails, the previous pack is kept.

## Deleting a handover sooner

On the dashboard, **Delete** on any handover removes, immediately and
permanently:

- both interview sessions and their transcripts
- the Role Intelligence Profile
- every version of the handover pack
- the dashboard entry

The employee's interview link stops working at once. A pack that is still being
generated when the handover is deleted is discarded and never emailed.

**Not affected:** copies of the pack already emailed to recipients, or
downloaded. KnowledgeKeeper cannot reach those; the manager is told this before
confirming.

## How deletion runs

Every worker runs the retention sweep every 60 minutes
(`RETENTION_SWEEP_INTERVAL_MINUTES`), and also opportunistically when
interviews start. Each step is idempotent, so several workers sweeping at once
is safe. Deletion therefore happens within about an hour of the date shown on
the dashboard.

## Emails

The "pack ready" email to the manager states the date the pack will be deleted.
Emails themselves (sent through SendGrid) are outside this policy: SendGrid
keeps its own activity records, and recipients' inboxes are theirs.
