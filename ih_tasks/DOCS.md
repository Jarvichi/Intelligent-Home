# Intelligent Home - Tasks

## What it does

Keeps ONE list of tasks. Each task has one short number, one stage and a full history.

| Stage | Meaning | Who moves it on |
|---|---|---|
| To do | Nothing planned yet | A worker, by writing a plan |
| Planning | A plan exists. It waits for the owner | **Only the owner**: Approve. Or Reject (stays in Planning, with a comment), or Close |
| Approved | The owner said yes | A worker starts |
| Implementing | Work in progress | A worker, when the work is ready to check |
| Verifying | The result is being checked | An automated check with its result, or the owner's tick when it needs the owner's eyes |
| Done | Finished | - |
| Closed | Not to be done. **Needs a reason**: won't do, duplicate of a task, already done, dropped | The owner can reopen it |

Rules the add-on enforces (a request that breaks one is refused):

- **One approval step.** Planning to Approved is the only gate, and only the owner can pass it. It is refused while the task has open questions: answers come first, approval last.
- **"Needs input" is a flag, not a stage.** A task has at most one open question set. Question types: `choice`, `multi`, `yesno`, `text`. Each set carries a plain line saying what happens after the answer. The answer is stored on the SAME task, which stays in its stage. In Planning the task is flagged for the worker to update the plan.
- **Comments are notes on the task.** They never create another task and never change the stage.
- **Going back.** Approved, Implementing or Verifying can go back to Planning with a reason; the plan must then be approved again. Verifying can go back to Implementing with a reason (the check failed). An approved plan cannot be edited in place.
- **Closing** needs one of the four reasons. `duplicate` needs the number of the task it repeats. A worker must also give a detail for won't do and dropped. Only the owner reopens a Closed or Done task.
- **Duplicates on create.** A new task whose title nearly matches an OPEN task is still stored (nothing is lost), then closed at once as a duplicate of that task, with a line in both histories. Send `allow_duplicate: true` to skip the check.
- **Merge.** Open tasks that are the same piece of work can be merged into one (see "Merge and duplicates"). An approval is never lost in a merge.
- **History.** Every change writes one history line (who, when, from, to, text) in the same database transaction.
- **Old identifiers.** A task can carry the ids it had in an older system (`todo_uid`, `queue_id`, `ok_number`, `wy_id`) and can be found by them.

## Who is who

| Caller | How | Can do |
|---|---|---|
| Owner | The page in Home Assistant (ingress, admin sign-in), or the API with `owner_token` | Everything a person does: approve, reject, answer, comment, close, reopen, tick Verifying, import |
| Worker | The API with `api_token` | Create, write the plan, start, hand over to Verifying, ask, comment, close with a reason, attach results |
| Check | The API with `api_token` and `"actor": "check"` | Finish Verifying with a result, or send it back with a reason |

The `api_token` can never act as the owner. If both tokens are set to the same text, the owner token is ignored.

**Limit:** the tokens stop mistakes, not a determined program. Anything that can read this add-on's options can read both tokens. Keep `owner_token` out of worker prompts and scripts.

## Requires

- Home Assistant OS or Supervised (add-ons need the Supervisor).

## Set-up

1. Install **Intelligent Home - Tasks**.
2. Set `api_token` to a long random text. Workers use it.
3. Leave `owner_token` empty unless a tool outside the page must act as you (for example the migration tool). Remove it again afterwards.
4. Start the add-on.
5. In the add-on's Info tab, check **Start on boot**, **Watchdog** and **Auto update** are on.
6. Open **IH · Tasks** in the sidebar.

## Options

| Option | Default | Meaning |
|---|---|---|
| `api_token` | empty | Bearer token for workers and checks. Empty = the API refuses everything except `/healthz`. |
| `owner_token` | empty | Bearer token that acts as the owner. Empty = only the page can approve. |
| `mirror` | `off` | `off`, `dry` (log what would change) or `live`. See "Mirror". |
| `mirror_entity` | empty | The to-do list to mirror to, for example `todo.my_list`. |
| `mirror_minutes` | 5 | Minutes between mirror passes (1 to 120). |

## The page

- A board: one column per stage. Closed is folded away under the board.
- Filter: a search box with a result count, then **Stage** and **Waiting for** selects and **Clear** on one row.
- "Add" makes a task in To do.
- A task opens on its own page: plan, the question form when input is needed, **Approve** and **Reject** (only in Planning with no open questions), a comment box, close with a reason, reopen, links and the history.
- Approve, close, reopen and merge need two taps (no pop-up dialog).
- **Merge** turns merge mode on: every open card gets a checkbox (tap anywhere on the card). With 2 or more selected, a bar at the bottom of the screen shows the task to keep (you can change it) and **Merge N tasks into #X**. Cancel or **Stop merging** leaves the mode.
- **Find duplicates** lists groups of open tasks with nearly the same title. Each group has **Merge group into #X** and **Not duplicates** (remembered: the group is not offered again).

## Merge and duplicates

A merge keeps ONE task and folds the others into it. Who: the owner or a worker (the same as closing). Everything happens in one database transaction: all of it or none of it.

For each merged task:

- Its comments are copied to the kept task, marked `From #N:`.
- Its open question set moves to the kept task when that task has none. When the kept task already has open questions, the merged task's questions are added as a comment and withdrawn.
- Its links are copied (the same link is not added twice).
- Its old ids (`todo_uid`, `queue_id`, `ok_number`, `wy_id`) MOVE to the kept task, so a look-up by an old id finds the kept task.
- The kept task takes the earliest due date, the highest priority and the labels of all the merged tasks.
- The merged task is closed with the reason duplicate of the kept task. Both histories get a line.
- Its plan and description are NOT copied. They stay readable on the closed task.

Rules:

- Only open tasks can be merged (not Done, not Closed). At most 20 tasks in one merge.
- The stages may differ. The default task to keep is the one in the most advanced stage, then the lowest number.
- **An approval is never lost.** A task that is Approved, Implementing or Verifying cannot be merged into a task in an earlier stage: keep that task instead. The page greys out the choices that would be refused.

**Undo:** open the merged task (under Closed) and reopen it, as for any closed task (owner only). It comes back in To do. What the merge copied stays on the kept task, and the old ids stay with the kept task; move them back by hand (`POST /tasks/<n>/legacy` does not take an id from another task).

**Find duplicates** compares the titles of open tasks with the same measure as the duplicate check on create (threshold 0.72). Labels that only say where a task came from are ignored: `Handoff:`, `Handoff from <name>:`, `OK:`, `OK #12:`, `Reopened:` (and `Plan:`, `Built:`, `Waiting:`, `Todo:`, `IH:`, `Check:`, `Review:`). A group is built around its lowest-numbered task: every other member matches that task.

## API

Port 8818. Send `Authorization: Bearer <token>` and JSON. Unknown fields are refused. Task text is never written to the add-on log.

| Call | Body | Notes |
|---|---|---|
| `GET /healthz` | | Open. |
| `GET /status` | | Counts per stage, schema version, mirror state. |
| `GET /board` | | Columns with task summaries. |
| `GET /tasks?stage=a,b&needs_input=1&waiting=owner&q=text` | | Summaries. `waiting` is `owner`, `worker` or `check`. |
| `GET /tasks/<n>` | | The whole task: plan, open question set, past ones, links, old ids, history. |
| `GET /tasks/<n>/history` | | The history only. |
| `GET /legacy?kind=todo_uid&value=...` | | The task that carries an old id. |
| `POST /tasks` | `title`, `description?`, `due?` (YYYY-MM-DD), `priority?` (`high`, `normal` default, `low`), `labels?` (list of words), `source?`, `legacy?`, `allow_duplicate?` | Reply: `task`, `duplicate_of`, `similar`, `existing`. |
| `POST /tasks/<n>/move` | `to`, `reason?`, `plan?`, `result?`, `verify_by?` | `plan` only with a move to Planning. `verify_by` (`check` or `owner`) with a move to Planning or Verifying. |
| `POST /tasks/<n>/plan` | `plan`, `verify_by?` | Worker, in Planning: a rewritten plan (after a rejection or an answer). |
| `POST /tasks/<n>/reject` | `comment` | Owner, in Planning. |
| `POST /tasks/<n>/close` | `reason`, `detail?`, `duplicate_of?` | `reason` is `wont_do`, `duplicate`, `already_done` or `dropped`. |
| `POST /tasks/<n>/comment` | `text` | |
| `GET /labels` | | Labels in use: `labels` `[{label, count}]`, most used first. |
| `POST /tasks/<n>/edit` | `title?`, `description?`, `due?`, `priority?`, `labels?` (a list replaces the old labels; `[]` clears them) | Workers take `high` tasks before `normal` before `low`, whatever the stage. |
| `POST /tasks/<n>/ask` | `questions` `[{id?, text, type, options?}]`, `after` | Worker. `409` if a set is already open. |
| `POST /tasks/<n>/answer` | `answers` `{id: value}`, `note?` | Owner. Every question must be answered. |
| `POST /tasks/<n>/withdraw` | `reason` | Takes the open question set back. |
| `POST /tasks/<n>/link` | `kind` (`pr`, `result`, `note`, `other`), `url?`, `text?` | Attach a result or a pull request link. |
| `POST /tasks/<n>/legacy` | `legacy` `[{kind, value}]` | |
| `POST /tasks/merge` | `keep` (task number), `merge` (list of task numbers) | Owner or worker. Reply: `task` (the kept task), `merged`. `409` when a task is not open or an approval would be lost. |
| `GET /tasks/duplicates` | | `groups`: each has `ids`, `score` (lowest title match in the group), `keep` (the default task to keep) and `tasks` (summaries). |
| `POST /tasks/duplicates/dismiss` | `ids` (2 or more task numbers) | Owner or worker. Remembers that these tasks are not duplicates of each other. |
| `POST /import` | `item` | Owner token only. Used by the migration tool. |

Errors: `400` bad request, `401` no or wrong token, `403` this caller may not do that, `404` no such task, `409` the task is in the wrong state.

Each task in a reply has `needs_input` (true or false) and `waiting_on` (`owner`, `worker`, `check` or empty): who has to act next.

## Mirror (one way)

Other readers (a wall display, a chat bot) may still read a Home Assistant to-do list. The mirror copies each task to one list item: the title, the due date and one status line such as `Task #12 · Planning · needs your input`. A finished task is ticked.

- The task database is the truth. Nothing is read back from the list into a task.
- Items that belong to no task are never touched. An item you delete by hand is not made again.
- A task that came from the old list (it carries a `todo_uid`) updates that same item. **Its long old description is replaced by the one status line.** Run the migration first: it copies the old description into the task.
- `dry` logs counts only (never titles). Try `dry` before `live`.
- Not mirrored: the plan, questions, comments, history.

## Migration tool

`tools/migrate.py` maps an older tag-based to-do list and work queue to tasks. It is **dry by default**: it reads, maps and writes a report. It never changes the old list or the queue.

```
python3 tools/migrate.py --queue-cmd "python3 /path/to/work_queue.py list --json" --owner-name NAME --report tasks_migration_report.md
python3 tools/migrate.py --live --api http://HOST:8818 --token-file FILE ...   # after you have read the report
```

- The live run needs `owner_token` (in a file, not on the command line), because import can place a task in Approved.
- The live run can be repeated: a task that already carries the same old to-do uid or queue id is left alone.
- The migration never places a task past Approved, and only in Approved when the old item has an approval that has not run yet.
- The mapping rules are listed at the top of `tools/migrate_map.py` and tested in `tests/test_migrate_map.py`.
- The report holds your task titles. Keep it at home.

## Data

- `/data/ih.db`: tasks, history, question sets, links, old ids, "not duplicates" pairs, mirror bookkeeping. A snapshot is taken once a day into `/data/backup` (newest 3 kept).
- No secrets are stored in the database. Tokens stay in the add-on options.
- Restore: stop the add-on, copy a snapshot over `/data/ih.db`, delete `ih.db-wal` and `ih.db-shm`, start the add-on.

## Tests

`python3 -m unittest discover -s ih_tasks/tests` (temporary folders only; no network apart from a local test server).

## Not tested yet

- The container image build.
- The page inside the real Home Assistant ingress frame, on a phone, and in dark mode.
- Merge mode and Find duplicates were checked in a browser against a local test server (made-up tasks, 1280 and 393 px wide), not inside Home Assistant and not on a real phone.
- Find duplicates on a large list: it compares every pair of open tasks (checked with about 10 tasks; a few hundred open tasks will take longer).
- The mirror after a merge, when both tasks came from the old list (each task is pinned to its own list item before the old ids move; unit-tested in the store only).
- The mirror against a real to-do list (the plan step is unit-tested; the Home Assistant calls are tested with a fake).
