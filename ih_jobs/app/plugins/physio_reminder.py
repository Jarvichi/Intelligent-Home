"""physio_reminder: once a day at a set time, if a presence entity is 'on', DM a Discord user a short mixed list of exercises.
Every house value comes from settings (JSON in the add-on option plugins[].settings):
  user_id          Discord user id to DM
  presence_entity  entity that must be 'on' (e.g. a presence switch)
  at               time of day HH:MM (overrides the default schedule)
  exercises        list of exercise texts
  pick             how many to send (default 4); yesterday's set is avoided where possible
  openers/closers  lists of message first/last lines (one picked at random)
  token_option     name of the add-on option holding the bot token (default discord_token)
Dry: logs the message it would send; no DM, no state change."""
import random

NAME = 'physio_reminder'
SCHEDULE = {'kind': 'daily', 'at': '19:30'}
DEFAULTS = {'user_id': '', 'presence_entity': '', 'exercises': [], 'pick': 4, 'openers': ['Physio time. Today\'s menu:'],
            'closers': ['Slow and controlled.'], 'token_option': 'discord_token'}


def compose(s, last, rng=random):
    ex = [str(e) for e in s['exercises']]
    n = max(1, min(int(s['pick']), len(ex)))
    pool = [e for e in ex if e not in (last or [])]
    pick = rng.sample(pool if len(pool) >= n else ex, n)
    opener = rng.choice(s['openers'] or DEFAULTS['openers'])
    closer = rng.choice(s['closers'] or DEFAULTS['closers'])
    return opener + '\n' + '\n'.join('• ' + e for e in pick) + '\n' + closer, pick


def run(ctx):
    s = ctx.settings
    if not s['user_id'] or not s['presence_entity'] or not s['exercises']:
        ctx.log('not configured (needs user_id, presence_entity, exercises in settings)')
        return False
    today = ctx.now.date().isoformat()
    st = ctx.state.load('state')
    if st.get('last_sent_day') == today:
        ctx.log('already sent today, skipped')
        return True
    if ctx.ha.state(s['presence_entity']) != 'on':
        ctx.log('presence entity is not on, skipped')
        return True
    msg, pick = compose(s, st.get('last_pick'))
    if not ctx.discord.dm(s['user_id'], msg, token_option=s['token_option']):
        return True   # dry run (logged by the helper)
    ctx.state.save('state', {'last_sent_day': today, 'last_pick': pick})
    ctx.log('sent %d exercises' % len(pick))
    return True
