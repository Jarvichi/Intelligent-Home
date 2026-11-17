# Changelog

## 0.3.0
- Priority: every task has a priority, `high`, `normal` (the default) or `low` (new column, schema 11; existing tasks become normal). Set it with `priority` on `POST /tasks` and `POST /tasks/<n>/edit`; it is in every task and list reply. The page shows it on the task and has a "Save priority" control. Merging keeps the most urgent priority of the merged tasks.

## 0.2.0
- Merge: `POST /tasks/merge {keep, merge}` folds open tasks into one. The merged tasks' comments (marked "From #N:"), links and old ids move to the task that is kept, an open question set moves over when the kept task has none (otherwise it becomes a comment), the earliest due date wins, and each merged task is closed as a duplicate of the kept one. One database transaction; a line in both histories. Owner or worker, as for close.
- An approval is never lost in a merge: a task that is Approved or further on cannot be merged into a task in an earlier stage. The default task to keep is the one in the most advanced stage.
- Find duplicates: `GET /tasks/duplicates` groups open tasks whose titles nearly match. `POST /tasks/duplicates/dismiss {ids}` remembers "not duplicates" (new table, schema 10).
- Titles are compared without labels such as "Handoff:", "Handoff from <name>:", "OK:" and "Reopened:" (also for the duplicate check on create).
- Page: a Merge mode with a checkbox on every open card and a bar at the bottom of the screen (pick the task to keep, two taps to merge); a Find duplicates list with "Merge group" and "Not duplicates".

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
