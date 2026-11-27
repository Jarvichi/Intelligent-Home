# Changelog

## 0.10.3
- Compare the current task detail page with a redesigned view: readable summary and plan, comments with an inline composer, grouped related work and actions, and collapsible activity.

## 0.10.2

- Show pending reminder delivery and persistent backup status. Use the actual reminder date in delayed notifications.

## 0.10.1
- Keep overdue reminder notifications in a durable queue until the inbox accepts them, even after the task reopens. Retry failed daily backups and run them independently of mirror and reminder failures.

## 0.10.0
- **Raised by** (schema 18-19). Every task has `raised_by`. The page fills it in from the signed-in Home Assistant user; the API uses the `raised_by` the caller sends, or the caller itself ("claude" for the worker and check token, "owner" for the owner token, "system" for the add-on). Existing tasks get their `created_by`. It is shown on the task page and on the board cards, and is read-only after creation. Nothing writes "Name/Date" lines into the description any more (old text stays as history).
- **Links at creation.** `POST /tasks` accepts `blocked_by`, `depends_on` and `relates_to` (lists of task numbers). Every task must exist, otherwise the whole create is refused; each link gets the same history lines as `/relate`.
- **New task panel = the task page's fields.** One field list (`model.TASK_FIELDS`, served on `/api/meta` as `fields`) now builds the New task panel and the edit form on the task page, and decides which fields `POST /tasks` and `POST /tasks/<n>/edit` accept. The panel has every creatable field: title, detail, due date, priority, labels, raised by (automatic), and linked tasks (search by number or title). The label box takes free text and lists the labels already in use as you type. A test fails when the panel, the page and the API disagree.
- **One Save button** on the task page replaces Save priority, Save labels and Save comment. It sends only what changed (title, detail, due date, priority, labels, and the comment text) and shows one saved/failed message. It stays disabled until something changes. The page can now also edit the title, detail and due date.
- **Work this now** (schema 20, new `run_now`). `POST /tasks/<n>/run_now {on?}`: the owner asks a worker to take this task next (To do, Approved, or a plan sent back for revision, with no open question); the page asks "tap again" first. It is recorded in the history. A worker may only clear the flag (`on: false`), which is what it does when the run starts; a move or a close also clears it. Every task summary has `run_now`. The worker side (taking such a task first, ignoring pace and caps) is separate and not in this version.
- Task actions may now contain an underscore (`run_now`).

## 0.9.0
- Page: a "Links to other tasks" card on every task (kind, other task number, title and stage; open it with a tap; two-tap Remove for blocked by / depends on / relates to; Add form with kind and task number; API errors such as a loop or a missing task show in the card). Links seen from the other end (Blocks, Needed by) are listed too and are removed from the task that owns them.
- Board cards show "Blocked by #n" (the lowest open blocker), and the Waiting-for filter has a new "Blocked by another task" option.
- Every task summary (`GET /tasks`, the board, and `GET /tasks/<n>`) now has `blocked` (true while a blocked-by / depends-on task is open) and `blocked_by` (that first open task's number, or null).

## 0.8.0
- Task links: a task can be **blocked by** another task, **depend on** another task, or **relate to** another task (new table, schema 16-17). `POST /tasks/<n>/relate {kind, task}` and `POST /tasks/<n>/unrelate {kind, task}`; owner, worker or check. `GET /tasks/<n>` now has `relations` (each with the other task's number, title, stage, and `waiting`) and `blocked` (true while a blocked-by / depends-on task is still open). The other task sees the inverse (`blocks`, `needed_by`); `relates_to` is the same both ways.
- Refused: linking a task to itself, a missing task, the same link twice, and a loop (A waits on B waits on A, also through other tasks). Both tasks get a history line.
- Merge moves the links of a merged task to the kept task (links between the two merged tasks are dropped).
- Links are information only: they do not stop a task from being moved or approved. The page does not show them yet (API only).

## 0.7.0
- Reminders (schema 15, new `remind_at`). `POST /tasks/<n>/remind {date, note?}` (owner or worker; the date must be after today) sets a reminder and closes the task with the reason "Reminder set". On that date (from `reminder_hour`, default 08:00) the task returns to To do with high priority, its history says so, and one `task_reminder` event goes to the Claude inbox (`inbox_url`, `inbox_token`) so Claude messages the owner. If the inbox cannot be reached the task waits and is retried every pass, and is reopened anyway one day late. `GET /reminders` lists the waiting ones. A plain close can never use "Reminder set". Reopening the task cancels the reminder.
- Page: a "Remind me later" card (date and note) on open tasks, and "Reminder on <date>" on a closed task and its card.

## 0.6.1
- Find duplicates, and the duplicate check when a task is created, ignore child tasks: the children of one parent share a title stem on purpose, so they were offered as duplicates of each other.

## 0.6.0
- Child tasks (schema 14, new `parent_id`). `POST /tasks/<n>/children {title, description?}` adds a child under an Approved or Implementing task (owner or worker). The child is created straight in Approved because it inherits the parent's approval, and takes the parent's priority, due date, labels and verify-by. A parent still in Approved moves to Implementing. One level only, at most 12 children.
- The parent cannot go to Verifying (or Done) while any child is open; Done and Closed both count as finished. While children are open the parent waits for them (`waiting_on: children`, a new value for the Waiting-for filter). When the last child finishes the parent gets a "children_finished" note.
- `GET /tasks/<n>` has `children` and `parent`; every task summary has `parent_id`, `children_open` and `children_total`. Tasks in a parent/child group cannot be merged.
- Page: a "Replan (back to Planning)" button (two taps) on Approved, Implementing and Verifying tasks. It moves the task back to Planning, flagged for the worker, with a standing reason (look at the current state of the house and code first, then write a new plan for what is still needed) plus whatever is in the comment box.
- Page: a "Part of" card on a child, a "Child tasks" card on the parent, "x of y child tasks done" on cards, and a Waiting-for option.

## 0.5.0
- Page: a "New task" button opens a panel (not a browser dialog) with Title, Detail, Due date and Priority. Save, Cancel and Esc; errors show in the panel. The quick title box stays. Labels will follow with the labels field.

## 0.4.0
- Labels: optional free-text labels on a task (new column, schema 12; existing tasks have none). Send `labels` (a list of words) on `POST /tasks` and `POST /tasks/<n>/edit`; every task and list reply carries `labels` as a list. A label is lowercase letters, digits and dashes, up to 24 characters, at most 8 per task; spaces are trimmed, repeats dropped. `GET /labels` lists the labels in use with counts. Merging keeps the labels of all the merged tasks (up to 8).
- Page: label chips on cards and on the task, an edit box with suggestions (comma-separated) and a Label filter on the board.

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
