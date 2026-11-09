# Steam achievement names

IH Games can look up achievement names for the game currently shown by Home Assistant's Steam Online integration.

## Configure Steam

1. In Home Assistant, open **Settings → Apps → Intelligent Home Games → Configuration**.
2. In the matching player entry, set `steam_profile_url` to your Steam profile URL. You do not need to look up the 64-bit Steam ID; the provider resolves a vanity URL through Steam's Web API. Alternatively, set `steam_id` to your 17-digit SteamID64.
3. Add `steam_achievements` to `providers_enabled`, keeping the other providers.
4. Set `steam_api_key` in the add-on options and save. The player entry also needs `steam_prefix` (or an explicit `steam_now_playing_entity`) so the provider can read the current game and App ID from the Steam Online sensor.

The Steam Online integration already uses a Steam Web API key, but its configuration is separate from the IH Games add-on. If you already added a key to the Home Assistant integration, enter that same key in the add-on's steam_api_key password field. Do not send the key in chat or put it in source control.

To find a SteamID64, open Steam, choose your username menu, then **Account details**. Steam shows the ID beneath the account name. The Home Assistant Steam integration documentation describes these setup steps: https://www.home-assistant.io/integrations/steam_online/

Keep your Steam profile's game details public so Steam can return achievement data. IH Games only asks for achievement data for the current App ID reported by the now-playing sensor.

## Behavior and limits

- The provider checks the current App ID at most once every five minutes and caches that game's achievement schema for seven days.
- On the first successful lookup for a game, it stores the already-unlocked achievements as a quiet baseline. Later newly unlocked achievements are added to the normal Games feed and permanent history.
- It needs a Steam profile URL or SteamID64 and an API key. If either is missing, it stays idle. The key is not logged or written into provider state.
- Games without a published achievement schema, a private game-details profile, or unavailable Steam API data may return no names. Provider logs include only the exception type, not request URLs or keys.

Steam's official Web API documents [ResolveVanityURL](https://partner.steamgames.com/doc/webapi/ISteamUser#ResolveVanityURL), [GetPlayerAchievements](https://partner.steamgames.com/doc/webapi/ISteamUserStats#GetPlayerAchievements) and [GetSchemaForGame](https://partner.steamgames.com/doc/webapi/ISteamUserStats#GetSchemaForGame). Live lookup remains unverified until the configured account plays a game and earns an achievement after its initial baseline.
