"""Constants for the Flic 1 integration."""

DOMAIN = "flic1"
CONF_KEYS = "keys"
EVENT_NAME = f"{DOMAIN}_event"

CONF_AUTO_DISCONNECT = "auto_disconnect"
# Seconds after the last press/release before the button hangs up. 511 = never: the link stays up
# (flicd docs: same battery use when stable) so presses are instant. Lower it to free the proxy slot.
DEFAULT_AUTO_DISCONNECT = 511
CONF_BATTERY = "battery"
CONF_FIRMWARE = "firmware"
BATTERY_INTERVAL = 3 * 3600  # seconds between log downloads (flicd: 3 h)
CONF_QUEUED_TIMEOUT = "queued_timeout"
# Events the button queued while no link existed are delivered on the next connect. Older than this many
# seconds they are not fired (a click from 15 s ago must not toggle the light now). 0 = live events only.
DEFAULT_QUEUED_TIMEOUT = 5
CONF_LATENCY = "latency"
DEFAULT_LATENCY = "stable"  # flicd "normal" (<=100 ms click latency, idle wake-up every ~2 s) with a 20 s link timeout
MAX_SESSION = 120.0  # cap for one connection when auto-disconnect is not "never"
RETRY_COOLDOWN = 2.0  # seconds to ignore advertisements after a failed attempt (a new press must retry fast)
MAX_RETRY_COOLDOWN = 30.0  # the cooldown doubles with every failure in a row up to this (127 failed connects in 3 min seen)
LOW_BATTERY = 15  # percent, the Flic app warns below this
LINK_GRACE = 10.0  # seconds after "ready" before a connectable advert makes us doubt the link
PROBE_INTERVAL = 30.0  # at most one liveness probe per this many seconds
PROBE_TIMEOUT = 8.0  # a GATT read that takes longer than this means the link is dead
REFUSAL_HINT_EVERY = 600.0  # seconds between two "button refuses this proxy" warnings
STALE_WARN = 10  # a dropped queued press older than this many seconds is worth one warning per session
MAINTENANCE_DELAY = 30.0  # battery log read this long after connecting (the Flic app waits 30 s, flicd 60 s)
PAIR_WAIT = 60.0  # how long the config flow waits for a button in pairing mode
PAIR_TIMEOUT = 45.0

FLIC_OUI = "80:E4:DA"
FLIC2_UUID = "00420000-8f59-4420-870d-84f3b617e493"
