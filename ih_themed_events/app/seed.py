"""The two events the house already has, as records. Loaded once when the database has no events (never overwrites).
Halloween is described only: it is live in Home Assistant by hand and stays that way (migrating it is a separate task).
Entity ids come from the live config (automation halloween_mode_switch) on 18 Nov 2026."""
HALLOWEEN = {
    'slug': 'halloween', 'name': 'Halloween', 'icon': 'mdi:pumpkin', 'status': 'live',
    'when': {'annual': {'month': 10, 'day': 31, 'days': 1}},
    'light_group': 'light.halloween_lights',
    'lights': ['light.uplighter', 'light.floor_lamp', 'light.mood_light', 'light.mood_light_2', 'light.ceiling_light', 'light.spots', 'light.plinth',
               'light.squirrel', 'light.spot', 'light.spot_4', 'light.signify_netherlands_b_v_ltw013', 'light.signify_netherlands_b_v_ltw013_2'],
    'extra_switches': ['switch.sonoff_s60zbtpg'],
    'speakers': ['media_player.dining_room_dining_room', 'media_player.snug_snug', 'media_player.kitchen_2'],
    'sounds_folder': '/media/halloween',
    'pause_automations': ['automation.motion_lights_kitchen', 'automation.motion_lights_living_room'],
    'effects': [{'id': 'blackout', 'name': 'Power-cut scare', 'kind': 'sound_and_lights', 'sound': 'scream.mp3', 'min_gap_minutes': 10, 'max_gap_minutes': 15},
                {'id': 'hall_creak', 'name': 'Hall creak and laugh', 'kind': 'sound_and_lights', 'sound': 'evil_laugh.wav', 'min_gap_minutes': 10, 'max_gap_minutes': 15}],
    'look': {}, 'auto_off': '02:30',
    'notes': 'Live by hand since 30 Sep 2026. This record only describes it. The kitchen is the main speaker and never gets effects.',
}
NYE = {
    'slug': 'nye', 'name': 'New Year\'s Eve', 'icon': 'mdi:party-popper', 'status': 'planned',
    'when': {'annual': {'month': 12, 'day': 31, 'days': 1}},
    'light_group': '', 'lights': [], 'extra_switches': [],
    'speakers': ['media_player.kitchen_2', 'media_player.dining_room_dining_room', 'media_player.snug_snug'],
    'sounds_folder': '/media/nye',
    'pause_automations': ['automation.motion_lights_kitchen', 'automation.motion_lights_living_room'],
    'effects': [{'id': 'midnight_burst', 'name': 'Midnight burst', 'kind': 'sound_and_lights', 'sound': 'cheer.mp3', 'at': '00:00'}],
    'look': {'rgb': [255, 190, 60], 'brightness': 220, 'transition': 2}, 'auto_off': '02:00',
    'notes': 'Decided 14 Nov 2026: kitchen, dining room and snug; midnight burst (white flash, then gold and colour ~5 s, cheer on dining room or snug only); Apple Music through Apple Home (the owner makes that automation). Helpers nye_* already exist in configuration.yaml. Lights to use: to be chosen (fill in lights or light_group).',
}
NYE_CHECKLIST = [
    {'text': 'Choose the lights for kitchen, dining room and snug (fill in the event)', 'days_before': 30},
    {'text': 'Fetch the cheer sound into /media/nye', 'days_before': 21},
    {'text': 'Review and install the generated helpers, scripts and automations (own approval)', 'days_before': 14},
    {'text': 'the owner makes the Apple Home music automation for the nye_music switch', 'days_before': 14},
    {'text': 'Trial run with a few people', 'days_before': 3},
    {'text': 'Fill in the review (what worked, what to change)', 'days_before': -1},
]
HALLOWEEN_CHECKLIST = [{'text': 'Fill in the review (what worked, what to change)', 'days_before': -1}]


def load(store):
    """Insert the two records if the database holds no events yet."""
    if store.counts()['events']:
        return False
    store.create(HALLOWEEN, 'system', HALLOWEEN_CHECKLIST)
    store.create(NYE, 'system', NYE_CHECKLIST)
    return True
