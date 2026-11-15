# Changelog

## 0.1.0
- First version. Tasks with one number and one stage: To do, Planning, Approved, Implementing, Verifying, Done, and Closed (needs a reason: won't do, duplicate of a task, already done, dropped).
- Who moves what is enforced: a worker writes the plan (To do to Planning) and does the work; only the owner approves a plan (one Approve step); a check result or the owner finishes Verifying; closing always needs a reason.
- "Needs input" is a flag, not a stage: at most one open question set per task (choice, multi, yes/no, text) with a plain line saying what happens after the answer. The answer carries on the same task. Comments are notes on the task.
- Duplicate check on create: a new task whose title nearly matches an open task is stored, linked and closed as a duplicate at once.
- Old identifiers (to-do uid, queue id, ask number) are kept per task and can be looked up.
- Own database (`/data/ih.db` through ihlib.db, daily snapshot), token API with two tokens (worker and owner), full history per task.
- Board page (ingress): one column per stage, Closed folded away, search + Stage + Waiting for + Clear, task page with plan, question form, Approve / Reject, close with a reason, history.
- Optional one-way mirror to a Home Assistant to-do list (`mirror: off | dry | live`, off by default).
- `tools/migrate.py`: maps the old tag-based to-do list and work queue to stages and writes a report. Dry by default.
