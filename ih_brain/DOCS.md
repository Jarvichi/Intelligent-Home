# Intelligent Home - Brain

## What it does

Serves the House Brain markdown notes to Claude as an MCP server over HTTP, outside Home Assistant core (read-only by default), and provides an admin-only Home Assistant ingress page to browse and search Brain notes.

## Requires

- No other Intelligent Home add-on is required.
- Home Assistant OS or Supervised (add-ons need the Supervisor)

## Set-up

1. Add the Intelligent Home repository in the add-on store and install **Intelligent Home - Brain**.
2. Install the Intelligent Home orchestrator first if you can. When its settings hub is on, it fills shared keys (such as `register_key`, `signing_key` and `ingest_token`) of this add-on automatically.
3. Open the Configuration tab and set `api_token`, `pocket_cloudflare_token`, `pocket_key`.
4. Start the add-on. Choose **Open Web UI** to browse the Brain, or check `/healthz` for MCP service health.

## Options

Set these in the add-on Configuration tab. Required = must be filled before it can work (the settings hub may fill it).

| Option | Required? | What it is |
|---|---|---|
| `brain_dir` | No | Folder with the notes. Default `/share/brain`. |
| `api_token` | Yes | Bearer token callers must send. Empty = everything refused. |
| `codex_todo_token` | No | Separate bearer token for `/codex-todo-mcp` (Claude to-do tools for Codex). Empty = that endpoint refuses all requests. |
| `codex_read_token` | No | Separate bearer token for the narrow Codex endpoint. Empty = that endpoint refuses all requests. |
| `read_only` | No | true (default): write tools refuse. false: writes allowed. |
| `port` | No | Port to listen on. Default 8820 (change the mapped port too). |
| `rate_per_min` | No | Requests per minute (default 120). |
| `apple_notes_dir` | No | Setting `apple_notes_dir`. Default: `/share/apple_notes_staging`. |
| `apple_notes_live` | No | Setting `apple_notes_live`. Default: `false`. |
| `pocket_live` | No | Setting `pocket_live`. Default: `false`. |
| `pocket_interval_min` | No | Setting `pocket_interval_min`. Default: `0`. |
| `pocket_url` | No | Setting `pocket_url`. |
| `pocket_cloudflare_token` | Yes | Setting `pocket_cloudflare_token`. |
| `pocket_key` | Yes | Setting `pocket_key`. |
| `scrub_names` | No | Setting `scrub_names`. |
| `cors_origins` | No | Setting `cors_origins`. |
| `ingest_url` | No | Orchestrator trace address (default `http://172.30.32.1:8790`). |
| `ingest_token` | No | Orchestrator trace token; filled by the Orchestrator when empty. Empty = tracing off. Metadata only. |
| `instructions_mode` | No | Instructions page: `off` (default), `view` (read-only) or `edit`. See "Instructions editor". |
| `instructions_files` | No | Which files from the fixed list the page shows: `house`, `claude_user`, `claude_addon`. Default `house`. |
| `instructions_user_ids` | No | Home Assistant user ids allowed to open the page. Empty = any administrator who can open the Web UI. |

## Troubleshooting

- **401 on every call.** Send the header `Authorization: Bearer <api_token>`. An empty `api_token` refuses everything.
- **Write tools say read-only.** Set `read_only` to false and restart. Check `/status` first.

## Privacy

Nothing leaves your network. The notes are read from the shared folder and sent only to callers that present the api_token. Do not expose the port to the internet.

## More details

### Endpoints
| Path | Returns |
|---|---|
| Home Assistant **Open Web UI** | Admin-only, read-only browse/search/recent/connection views. Requests are served through ingress only (gateway peer `172.30.32.2`). The optional instructions editor is the only part that can write (see "Instructions editor"). |
| `GET /healthz` | `ok` (no token) |
| `GET /status` | version, note count, `read_only`, last index time, whether a token is set (no note content, never the token) |
| `POST /mcp` | MCP JSON-RPC 2.0 (`initialize`, `ping`, `tools/list`, `tools/call`); also accepted on `POST /`. Send `Authorization: Bearer <api_token>`. Notifications get HTTP 202. |

Rate limit: `rate_per_min` (429). Kill switch: file `/data/STOP` (503). A request body over 512 KB is refused (413).

### Tools
`brain_help`, `brain_index`, `brain_search` (query, area, limit), `brain_read` (path), `brain_list` (area), `brain_recent` (limit), `brain_links` (path), `brain_graph`, `brain_image`, `brain_text`, `brain_file`, `brain_files`, `brain_write` (path, content, author, summary), `brain_append` (path, text or content, author).

`POST /codex-mcp` uses the separate `codex_read_token` and exposes only `brain_search` and `brain_read`. Both operate only on Markdown notes whose front matter explicitly says `share: true`; notes under `inbox/`, hidden paths, attachments, and all writes are excluded. A missing or invalid token is refused. Note content returned by search/read is sent to the hosted Codex model when used in a turn.

Rules kept from the component: note paths must end in `.md`, may not contain `..` or start with a dot, and must stay inside `brain_dir`; hidden folders are never listed or read; attachments are limited by type and size; writes are refused if they look like they hold a password, token or key, are over 60 KB, keep the old version in `.history/` (last 20) and add a line to `_log.md`; `updated:` front matter is set on save.

### Read-only by default
With `read_only` true (the default) `brain_write` and `brain_append` return an error result saying the server is read-only. Set it to false only when Claude should write through this add-on.

### Switching Claude over
1. Install the add-on, set `api_token` to a long random text, start it, check `/status`.
2. Add a second MCP server next to the old one: `claude mcp add --transport http house-brain-new http://<host>:8820/mcp --header "Authorization: Bearer <api_token>"`. Test a search.
3. When it answers the same, set `read_only` to false if Claude should write, then remove the old entry and re-add the new one under the old name (`house-brain`). The Home Assistant component stays installed as a fallback.


The rich Brain interface can open directly from the Home Assistant sidebar. Enable **Show in sidebar** on the app page. Its title is **Brain** and access is restricted to administrators. This does not change other dashboard access or the app options. Background-worker reports stay inside Intelligent Home Manager.


Notes open as readable pages. The reader supports common Markdown headings, emphasis, lists/tasks,
quotes, fenced/indented code, tables and links. Tables show labelled row details in narrow readers.
**View Markdown source** retains the exact complete note, including front matter. Internal links use
known note paths, filenames or unique titles; the previous-note button returns through a link chain.
Raw HTML stays text and image references stay labelled references: the reader makes no automatic
external image or embed requests. Attachment previews, extended Markdown constructs and ambiguous
aliases are not a replacement for the existing Wiki attachment workflow. Keep that workflow until
its parity is verified. The UI remains read-only and administrator-only; no access options change.

### Instructions editor

A page to read and edit the assistant's standing-instructions file from the Web UI. It is off until you turn it on.

**Warning: the page needs a wider folder mapping.** The files live in the Home Assistant config folder. An add-on cannot be given one file, only the whole folder. To use the page the owner must add `homeassistant_config:rw` under `map:` in `config.yaml` (it is deliberately not there by default) and rebuild/reinstall. After that the add-on can read and write the whole config folder, though this code only ever opens the files in the list below. With `instructions_mode: view` the mapping can be `homeassistant_config:ro`.

Files (the complete list; nothing else can be opened):

| id | File inside the add-on | Notes |
|---|---|---|
| `house` | `/homeassistant/CLAUDE.md` | editable in `edit` mode | <!-- privacy-scan: ignore LOCAL_TERM -->
| `claude_user` | `/homeassistant/.claudecode/CLAUDE.user.md` | editable in `edit` mode |
| `claude_addon` | `/homeassistant/.claudecode/CLAUDE.md` | always view-only |

Turn it on:
1. Add the folder mapping (above). Restart the add-on.
2. Set `instructions_mode` to `view` or `edit`. Optional: put your Home Assistant user id in `instructions_user_ids`.
3. Open the Web UI. Select **Instructions editor**.

To edit: change the text. Select **Preview changes**. Read the changes. Select **Save**.

Safety rules:
- **Who can open it.** Requests are served only through Home Assistant ingress (gateway peer `172.30.32.2`), the panel is administrator-only, and each request must carry the signed-in user's id (`X-Remote-User-Id`, added by the Supervisor). The add-on cannot see the administrator flag itself; `instructions_user_ids` narrows access to named users. There is no token and no route on the MCP port (8820).
- **Saving.** A save needs JSON, the page's own header and a same-origin browser request, so another web site cannot trigger it. A save is refused unless the same text was previewed first (HTTP 428), when the file changed since you opened it (409, your edits stay on the page), when the text is empty, has NUL characters, is not UTF-8 or is over 128 KiB (400).
- **Backups.** Before the file is replaced, the previous text is written to `/data/instructions/backups/` (private to the add-on, mode 0600). If the backup cannot be written, nothing is saved. The newest 50 per file are kept. **Load** on the page puts a backup into the editor; it is saved only after preview and save.
- **Save log.** `/data/instructions/audit.jsonl` holds time, file id, sizes and the backup name. It never holds text. Newest 500 entries.
- **Replacing the file.** The new text is written to a temporary file beside the original and renamed over it, keeping the file mode and owner. Symlinked files and folders are refused.

Limits: another program that writes the file at the same instant as a save can still lose its change (the check and the rename are two steps). Line endings are saved as the browser sends them (`\n`).

### Codex to-do tools

`POST /codex-todo-mcp` uses its own `codex_todo_token` and exposes four tools on one list, `todo.claude_to_do`, and nothing else: `todo_list` (paged, searchable), `todo_get`, `todo_add`, `todo_note` (appends a dated line to the description) and `todo_complete`. It uses this add-on's Home Assistant access (`homeassistant_api`), so Codex gets no Home Assistant credential. It cannot delete items, change other lists or call other services. Add it in the Codex add-on's `mcp_servers` with the URL `http://<host>:8820/codex-todo-mcp` and the token.
