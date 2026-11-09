# PlayStation trophy names

`ih_games` can use PSNAWP and your PSN session to look up the names of newly earned trophies. The add-on still uses the existing Home Assistant PSN sensors for trophy totals and the currently played game.

## Configure the add-on

1. Open **Settings → Add-ons → IH Games → Configuration**.
2. Add your PSN Online ID to that player's `psn_online_id` field. This is the public Online ID shown on your PlayStation profile, not the email address used to sign in.
3. Set `psn_npsso` in the add-on options (the field is a password field). Keep it private. Do not put it in chat, a shared note, or source control.
4. Ensure `psn_trophy_names` is included in `providers_enabled`, then save and restart the add-on.

To obtain the NPSSO value, sign in to PlayStation Network in a browser and open:

`https://ca.account.sony.com/api/v1/ssocookie`

The response contains a property named `npsso`. Copy only its value into the add-on's `psn_npsso` option. Treat it like a password: it grants access to your PSN session. If the account signs out or Sony expires the session, obtain a new value and replace it in the add-on configuration.

The provider checks PSN only after the existing trophy-count sensors report an increase while a game is currently playing. It looks up earned trophy names for that title and adds named events to the normal `ih_games` feed. If no NPSSO or Online ID is configured, it does nothing. API lookup failures are logged by error type and retried on a later run; the token is never included in logs.

## Privacy and limits

The NPSSO value is read from the add-on options at runtime and is not written into provider state or logs. Trophy details depend on Sony's API and the privacy settings on the PSN account. This provider has not been validated against a live PSN account until you configure your own token.
