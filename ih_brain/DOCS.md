# Intelligent Home - Brain

## What it does

Serves the House Brain markdown notes to Claude as an MCP server over HTTP, outside Home Assistant core (read-only by default). Stand-alone family member.

## Requires

- No other Intelligent Home add-on is required.
- Home Assistant OS or Supervised (add-ons need the Supervisor)

## Set-up

1. Add the Intelligent Home repository in the add-on store and install **Intelligent Home - Brain**.
2. Install the Intelligent Home orchestrator first if you can. When its settings hub is on, it fills shared keys (such as `register_key`, `signing_key` and `ingest_token`) of this add-on automatically.
3. Open the Configuration tab and set `api_token`, `pocket_cloudflare_token`, `pocket_key`.
4. Start the add-on and check `/healthz` and the log.

## Options

Set these in the add-on Configuration tab. Required = must be filled before it can work (the settings hub may fill it).

| Option | Required? | What it is |
|---|---|---|
| `brain_dir` | No | Folder with the notes. Default `/share/brain`. |
| `api_token` | Yes | Bearer token callers must send. Empty = everything refused. |
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
| `cors_origins` | No | Origins allowed to call the server from a browser page, comma separated (e.g. `http://host:8123`). Default: empty (no CORS). `*` is ignored. |
| `scrub_names` | No | Names to replace before notes are pushed, as `Name=Replacement,Other=Repl`. Default: empty (no name replacement; IPs, MACs, ids and emails are always scrubbed). |

## Troubleshooting

- **401 on every call.** Send the header `Authorization: Bearer <api_token>`. An empty `api_token` refuses everything.
- **Write tools say read-only.** Set `read_only` to false and restart. Check `/status` first.

## Privacy

Nothing leaves your network. The notes are read from the shared folder and sent only to callers that present the api_token. Do not expose the port to the internet.

## More details

### Endpoints
| Path | Returns |
|---|---|
| `GET /healthz` | `ok` (no token) |
| `GET /status` | version, note count, `read_only`, last index time, whether a token is set (no note content, never the token) |
| `POST /mcp` | MCP JSON-RPC 2.0 (`initialize`, `ping`, `tools/list`, `tools/call`); also accepted on `POST /`. Send `Authorization: Bearer <api_token>`. Notifications get HTTP 202. |

Rate limit: `rate_per_min` (429). Kill switch: file `/data/STOP` (503). A request body over 512 KB is refused (413).

### Tools
`brain_help`, `brain_index`, `brain_search` (query, area, limit), `brain_read` (path), `brain_list` (area), `brain_recent` (limit), `brain_links` (path), `brain_graph`, `brain_image`, `brain_text`, `brain_file`, `brain_files`, `brain_write` (path, content, author, summary), `brain_append` (path, text or content, author).

Rules kept from the component: note paths must end in `.md`, may not contain `..` or start with a dot, and must stay inside `brain_dir`; hidden folders are never listed or read; attachments are limited by type and size; writes are refused if they look like they hold a password, token or key, are over 60 KB, keep the old version in `.history/` (last 20) and add a line to `_log.md`; `updated:` front matter is set on save.

### Read-only by default
With `read_only` true (the default) `brain_write` and `brain_append` return an error result saying the server is read-only. Set it to false only when Claude should write through this add-on.

### Switching Claude over
1. Install the add-on, set `api_token` to a long random text, start it, check `/status`.
2. Add a second MCP server next to the old one: `claude mcp add --transport http house-brain-new http://<host>:8820/mcp --header "Authorization: Bearer <api_token>"`. Test a search.
3. When it answers the same, set `read_only` to false if Claude should write, then remove the old entry and re-add the new one under the old name (`house-brain`). The Home Assistant component stays installed as a fallback.
