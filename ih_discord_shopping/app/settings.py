"""Settings for the Discord commands (shared by the stand-alone service and the ih_discord add-on).
Every deployment-specific detail (channel names or numbers, entity ids, people, shop names, aisle lists, folders) lives here as a SETTING
with a neutral default. Code never contains such a detail. A deployment supplies values:
  - the add-on builds them from its options (main.py, load_options),
  - the stand-alone service reads settings.local.json next to this file (git-ignored).
Empty default = that feature is simply not offered."""
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))
LOCAL_FILE = os.path.join(HERE, 'settings.local.json')

# generic grocery departments (name, regex of words); a deployment may replace them (setting "aisles")
DEFAULT_AISLES = [
    ["Frozen", "frozen|ice cream|fish fingers|chips|peas|ice lolly"],
    ["Household", "kitchen roll|foil|tissue|toilet|dishwasher|washing|bin bag|batter|soap|shampoo|shaving|nappies|toothpaste|cleaner|bleach|sponge|cling"],
    ["Drinks", "juice|squash|wine|beer|water|cola|lemonade"],
    ["Cupboard", "rice|pasta|spaghetti|noodle|tin|canned|sauce|ketchup|mustard|mayo|oil|vinegar|stock|gravy|seasoning|spice|sugar|flour|honey|jam|cereal|oats|seeds|soy|coffee|tea|biscuit|crisps|beans|tuna|peanut"],
    ["Dairy & chilled", "milk|cheese|butter|yog|cream|egg|houmous|hummus"],
    ["Meat & fish", "chicken|beef|pork|mince|sausage|bacon|ham|fish|salmon|prawn|turkey|lamb"],
    ["Bakery", "bread|roll|bagel|wrap|cake|croissant|muffin|crumpet|pitta|naan"],
    ["Fruit & veg", "fruit|apple|banana|lime|lemon|orange|onion|garlic|potato|carrot|tomato|salad|lettuce|pepper|mushroom|avocado|berr|grape|veg|herb|ginger|chilli|cucumber|courgette|broccoli|spinach"],
]
DEFAULT_AISLE_ORDER = ["Fruit & veg", "Bakery", "Meat & fish", "Dairy & chilled", "Cupboard", "Drinks", "Frozen", "Household", "Other"]

DEFAULTS = {
    'root': '',                          # folder that holds the optional files below; '' = no files are read or written
    'user_agent': 'DiscordBot (ih-discord, 1.0)',
    'log_name': 'service',               # stand-alone service: logs/<log_name>.log
    'service_features': ['shopping', 'house', 'braininbox'],   # stand-alone service: features it starts
    'allowed_file': '',                  # stand-alone service: file (relative to root) with allowed Discord user ids, one per line
    'token_from_ha': False,              # stand-alone service: read the bot token from Home Assistant's discord integration (needs root)
    # channels: a NAME (looked up) or a channel NUMBER (used as it is)
    'channel_shopping': 'shopping',
    'channel_house': '',                 # '' = the house commands are not offered
    'channel_house_file': '',             # optional JSON file (relative to root) {"house": "<channel>"} that overrides channel_house
    'channel_brain_inbox': 'brain-inbox',   # plugin feature braininbox only
    # shopping
    'shopping_list_entity': 'todo.shopping_list',
    'meal_plan_script': '',              # HA script (name without "script.") run quietly before a shopping list is built; '' = none
    'shop_names': '',                    # extra shop names for "I'm at <shop>", regex alternatives, e.g. "acme|corner store"
    'aisles': DEFAULT_AISLES,
    'aisle_order': DEFAULT_AISLE_ORDER,
    'aisle_order_file': '',              # optional JSON {order, rank} (relative to root) learned from past shops
    'meal_items_file': '',               # optional JSON list of meal-plan item names (relative to root)
    'receipts_dir': '',                  # folder (relative to root) for receipt photos; '' = photos are not handled
    'receipt_reply': 'Got the receipt ({n} photo{s}).',
    # house commands
    'command_event': 'ih_discord_command',   # HA event fired for every accepted command
    'presence': {},                      # {"alex": "switch.alex_home"}: who is home / went out answers; {} = not offered
    'house_entities': {},                # optional keys: alarm, boiler, thermostat, bin_next, bin_days, free_power_events, free_power_banked
    'status_device': {},                 # optional {"label": "Tablet", "battery": "sensor.x", "seen": "sensor.y"}
    'aliases': {},                       # extra words per command, e.g. {"status": "tablet|boiler", "free": "tariff"}
    'catchup_cmd': [],                   # command line for "catchup on/off/status" ({sub}, {root} are filled in); [] = not offered
    'catchup_on_args': [],               # appended for "catchup on" ({who} is filled in)
    'catchup_label': 'catch-up',
}


def merge(over=None):
    """DEFAULTS overlaid with `over` (unknown keys are kept; wrong types are ignored for the known list/dict/str keys)."""
    s = {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    for k, v in (over or {}).items():
        if k in DEFAULTS and not isinstance(v, type(DEFAULTS[k])) and not (isinstance(DEFAULTS[k], bool)):
            continue
        s[k] = v
    return s


def load(path=None):
    """Settings from a JSON file (default settings.local.json beside this file; missing file = defaults)."""
    try:
        return merge(json.load(open(path or LOCAL_FILE)))
    except (OSError, ValueError):
        return merge({})


def of(ctx):
    """The settings of a context (tests and old callers may have none: defaults)."""
    return getattr(ctx, 'cfg', None) or merge({})


def path_of(root, rel):
    """root + rel, or None when either is empty (the optional file is then not used)."""
    return os.path.join(root, rel) if root and rel else None
