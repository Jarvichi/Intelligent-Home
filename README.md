# Intelligent Home add-ons

Small, generic Home Assistant add-ons. Each one does one job and keeps working if the others are off.

| Add-on | What it does |
|---|---|
| `ih_orchestrator` | Health and status page for the other modules, plus an optional signed job scheduler that talks to an MQTT broker you run yourself. |
| `ih_discord` | Discord commands as a module: shopping list and house status. Dry-run by default. |
| `ih_jobs` | Scheduled housekeeping jobs (backup store, weather fetch) as a module. Dry-run by default. |

## Add the repository in Home Assistant

1. Open Settings, then Add-ons (Apps), then the store.
2. Open the three-dot menu, then Repositories.
3. Paste the URL of this repository and select Add.
4. Install the add-on you want, set its options, then start it.

## Build on first install

No `image:` is set in any `config.yaml`. Home Assistant builds each add-on locally from its `Dockerfile`.
The first install and each update build on your own device. On a small board this can take several minutes.

## Privacy design

- No secret and no house detail is stored in this repository.
- Tokens, entity names, channel names, people and other house values are **options** of each add-on.
  You set them in the add-on's Configuration tab. They are stored by Home Assistant on your device only.
- Each add-on ships an `options.example.json` with empty or generic values.
- Optional house-specific plugins are not part of this repository. The code runs without them.
- Every export is scanned for private addresses, identifiers, e-mail addresses, phone numbers and secret-shaped strings before it is committed.

## Updates

Keep **auto-update OFF**. Read the change list, then update by hand. Back up first.
New versions start in dry-run mode where an add-on can act on the outside world; check the add-on README before you switch anything live.

## Licence

MIT. See `LICENSE`.
