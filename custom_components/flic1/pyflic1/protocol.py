"""Flic 1 application-layer protocol. Pure functions, no I/O. See notes/spec.md.

All security is SHA-256 MACs keyed with the 12-byte pairing secret h3 (no link encryption).
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass

PUBLIC_MODE_UUID = "f02adfc0-26e7-11e4-9edc-0002a5d5c51b"
# ATT value handles are hard-coded in flicd (no GATT discovery for Flic 1).
HANDLE_CFG = 0x12  # read hello, write verify/config, notify verify replies
HANDLE_EVT = 0x15  # notify button events

# Buttons of an early batch need writes with response (the Linux daemon does this). The Flic app writes without
# response, except for the event acknowledgement (op 0x25), which it sends with response.
_WRITE_REQ_RANGE = (0x80E4DA700000, 0x80E4DA707254)

# Connection parameters that the Linux daemon (flicd) sets for each latency mode:
# (min interval, max interval in 1.25 ms units, slave latency, supervision timeout in 10 ms units).
# The idle wake-up of the button is about max * (latency + 1): 2.0 s, 1.4 s and 2.2 s. So the battery use is about
# the same in all modes while the link is stable. ESPHome proxies connect with 8.75-11.25 ms and latency 0 instead,
# which wakes the button about 100 times more often.
# Every flicd mode has a supervision timeout of only 2-3 idle wake-ups (5 s for "normal"). That is fine for a phone
# next to the button, but not for a proxy at -80 dBm: two lost packets in a row end the link (drops after 25-35 s
# and after 135 s were seen). "stable" is NOT from Flic: it has the interval and latency of "normal" (the same
# idle wake-up and battery use) and a timeout of 20 s, so 10 lost wake-ups are needed to end the link.
# "proxy" has no entry: the ESPHome proxy keeps its own ~10 ms and latency 0. That is the most robust mode and
# it uses the most battery.
CONN_PARAMS = {"normal": (70, 80, 19, 501), "low": (13, 14, 79, 421), "high": (200, 220, 7, 661),
               "stable": (70, 80, 19, 2000)}

OP_VERIFY_REPLY = 0x09
OP_CONFIG = 0x06
OP_ACK = 0x25
HELLO_LEN = 20
EVENT_LEN = 15

# fliclib ClickType
DOWN, UP, CLICK, SINGLE, DOUBLE, HOLD = range(6)
CLICK_NAMES = ("down", "up", "click", "single", "double", "hold")


def sha(*parts: bytes) -> bytes:
    return hashlib.sha256(b"".join(parts)).digest()


def needs_write_response(address: str) -> bool:
    n = int(address.replace(":", ""), 16)
    return _WRITE_REQ_RANGE[0] <= n <= _WRITE_REQ_RANGE[1]


@dataclass
class Keys:
    """Pairing state. h1/h3 come from the Flic cloud, the rest tracks replay protection."""

    h1: bytes  # 8 bytes
    h3: bytes  # 12 bytes
    uuid: str = ""
    serial: str = ""
    color: str = ""
    counter: int = 0
    boot: int = 0
    mask: int = 0

    def __post_init__(self) -> None:
        if len(self.h1) != 8 or len(self.h3) != 12:
            raise ValueError("h1 must be 8 bytes and h3 12 bytes")

    def to_dict(self) -> dict:
        return {
            "h1": self.h1.hex(), "h3": self.h3.hex(), "uuid": self.uuid, "serial": self.serial,
            "color": self.color, "counter": self.counter, "boot": self.boot, "mask": self.mask,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Keys":
        return cls(bytes.fromhex(d["h1"]), bytes.fromhex(d["h3"]), d.get("uuid", ""),
                   d.get("serial", ""), d.get("color", ""), d.get("counter", 0),
                   d.get("boot", 0), d.get("mask", 0))


# --- hello -----------------------------------------------------------------------------------


@dataclass
class Hello:
    raw: bytes
    flag: int  # 0x42 'B' full verify, 0x41 'A' quick if possible, 0x40 '@' quick only,
    #            0x43 pairing mode that asks for a BLE bond (the Flic app calls createBond)
    nonce: bytes  # 8 bytes


def parse_hello(raw: bytes) -> Hello:
    if len(raw) != HELLO_LEN:
        raise ValueError(f"hello must be {HELLO_LEN} bytes, got {len(raw)}")
    return Hello(bytes(raw), raw[0], bytes(raw[1:9]))


def hello_mac_ok(hello: Hello, h3: bytes) -> bool:
    """Newer hello: MAC over bytes 0..16 at 17..19. Older firmware: over bytes 0..13 at 14..16.

    The Linux daemon checks only the first form. The Flic app accepts both.
    """
    r = hello.raw
    return sha(r[:17], h3)[:3] == r[17:20] or sha(r[:14], h3)[:3] == r[14:17]


# --- authenticated host->button packets ---------------------------------------------------------


def auth_packet(op: int, payload: bytes, ctr: int, nonce: bytes, h3: bytes) -> bytes:
    body = bytes([op]) + payload
    mac = sha(body, ctr.to_bytes(4, "little"), nonce, h3)[:4]
    return body + bytes([ctr & 0xFF]) + mac


def ack_payload(keys: Keys) -> bytes:
    """Last accepted event position, so the button can drop what we already have."""
    return keys.counter.to_bytes(4, "little") + keys.boot.to_bytes(4, "little") + bytes([keys.mask & 0xFF])


def config_payload(auto_disconnect: int) -> bytes:
    """Seconds after the last press/release the button may disconnect (0..510, 511 = never)."""
    return bytes(6) + (auto_disconnect & 0x1FF).to_bytes(2, "little")


def ready_frame_ok(frame: bytes, nonce: bytes, rand6: bytes, h3: bytes) -> bool:
    return len(frame) >= EVENT_LEN and frame[0] == 0x40 and frame[1:15] == sha(nonce, rand6, h3)[:14]


# --- events ----------------------------------------------------------------------------------


@dataclass
class RawEvent:
    b: int
    counter: int
    boot: int
    age: int  # td field, seconds since the event [unit unverified]
    queued: bool


def event_mac_ok(frame: bytes, h3: bytes) -> bool:
    return len(frame) >= EVENT_LEN and sha(frame[:11], h3)[:4] == frame[11:15]


def parse_event(frame: bytes) -> RawEvent:
    return RawEvent(
        b=frame[0],
        counter=int.from_bytes(frame[1:4], "little"),
        boot=int.from_bytes(frame[4:7], "little"),
        age=int.from_bytes(frame[7:11], "little"),
        queued=bool(frame[0] & 0x10),
    )


# 0x20 and 0x60/0x70 are status frames (the Flic app reads 0x60/0x70 as connection-parameter reports),
# 0x68 is dropped by both flicd and the app. Never user events.
IGNORED_EVENT_BYTES = frozenset({0x20, 0x60, 0x68, 0x70})


def accept_event(keys: Keys, ev: RawEvent) -> bool:
    """flicd replay filter. Mutates keys (counter, boot, mask) when the event is accepted."""
    if ev.b in IGNORED_EVENT_BYTES:
        return False
    k = (ev.b & 3) if not (ev.b & 8) else 0
    if keys.boot < ev.boot:
        keys.mask = 0
    elif keys.boot > ev.boot:
        return False
    elif keys.counter < ev.counter:
        keys.mask = 0
    elif keys.counter > ev.counter or keys.mask & (1 << k):
        return False
    keys.boot, keys.counter = ev.boot, ev.counter
    keys.mask |= 1 << k
    return True


def classify(b: int) -> list[tuple[int, int]]:
    """(fliclib event opcode, click type) pairs: 4 up/down, 5 click/hold, 6 single/double, 7 s/d/hold."""
    t, bit2, bit3 = b & 3, bool(b & 4), bool(b & 8)
    if bit3:
        kind, p5, p6, p7, p8 = 0, bit2, t == 2, t == 3, False
    else:
        kind, p5, p6, p7, p8 = t, False, False, False, (b & 0xF) == 7
    out: list[tuple[int, int]] = []
    if kind == 1:
        out.append((4, DOWN))
    elif kind == 2:
        out += [(6, SINGLE), (7, SINGLE)]
    elif kind == 3:
        out.append((5, HOLD))
        if not p8:
            out.append((7, HOLD))
    else:
        out.append((4, UP))
        if not p5:
            out.append((5, CLICK))
            if p6:
                out += [(7, SINGLE), (6, SINGLE)]
        elif p6:
            out.append((6, SINGLE))
        if p7:
            out += [(6, DOUBLE), (7, DOUBLE)]
    return out


def needs_ack(b: int) -> bool:
    t = b & 3
    return t in (2, 3) if b & 8 else t == 2


def user_events(b: int) -> list[str]:
    """What HA cares about: down/up from opcode 4, single/double/hold from opcode 7."""
    return [CLICK_NAMES[c] for op, c in classify(b) if op == 4 or op == 7]


# --- full verify (pairing) ----------------------------------------------------------------------


def fv_start(rand8: bytes) -> bytes:
    return b"\x07\x00" + rand8


@dataclass
class FvStep0:
    nonce: bytes  # A
    h2: bytes


def parse_fv0(frame: bytes) -> FvStep0:
    if len(frame) < HELLO_LEN or frame[0] != 0:
        raise ValueError("not a full-verify step 0 frame")
    return FvStep0(bytes(frame[1:9]), bytes(frame[9:17]))


def parse_fv1(frame: bytes) -> str:
    if len(frame) < HELLO_LEN or frame[0] != 1:
        raise ValueError("not a full-verify step 1 frame")
    return bytes(frame[1:17]).hex()


def cloud_request_body(rand8: bytes, nonce_a: bytes) -> str:
    """JSON body of the Flic cloud unlock request."""
    r1 = int.from_bytes(rand8, "little", signed=True)
    r2 = int.from_bytes(nonce_a, "little", signed=True)
    return '{"r1":%d,"r2":%d,"platform":"linux","version":"0.2"}' % (r1, r2)


def fv_finish(r3: int, h1: bytes) -> bytes:
    return b"\x07\x01" + (r3 & 0xFFFFFFFFFFFFFFFF).to_bytes(8, "little") + h1


def parse_cloud_response(text: str, address: str, step0: FvStep0, uuid: str) -> tuple[Keys, int]:
    """Validate the /unlock reply like flicd does and return the new Keys and r3."""
    d = json.loads(text)
    if d["mac_address"].lower() != address.lower():
        raise ValueError("mismatching bdaddr")
    h1, h2, h3 = (bytes.fromhex(d[k]) for k in ("h1", "h2", "h3"))
    # The cloud returns longer values. The Linux daemon and the Flic app use the first 8 / 8 / 12 bytes.
    # h2 and the lengths are not secret. h1 and h3 are never put into an error message.
    if h2[:8] != step0.h2:
        raise ValueError(f"mismatching h2 (cloud {h2[:8].hex()}, {len(h2)} bytes; button {step0.h2.hex()}; "
                         f"h1 {len(h1)} bytes, h3 {len(h3)} bytes)")
    keys = Keys(h1[:8], h3[:12], uuid, d.get("factory_serial", ""), d.get("color", ""))
    return keys, int(d["r3"])


# --- battery log, advertised name and advertised data -----------------------------------------------------
# Battery: the Linux daemon and the Flic app work the same way.
# Host sends op 0x18 (empty). The button answers on handle 0x12 with op 5 `05 | N u16 | t u32 | M u32`
# (N log entries in total, the button can send the last M). The host asks `0x19 | pos u32` from
# pos = max(0, N - M); the button streams `06 | 19 bytes` frames, 285 bytes per request, ask again at each
# 285 bytes until N - pos entries are in. Entries are one byte, 0 = no data.

OP_BATTERY_LOG = 0x18
OP_BATTERY_FROM = 0x19
BATTERY_CHUNK = 19
BATTERY_BLOCK = 285
MFR_ID = 783  # 0x030F, manufacturer data in the advertisement (scan response), firmware >= 12


@dataclass
class Battery:
    percent: int
    raw: int      # mean of the newest <= 8 non-zero entries, each 1100 + 3 * entry (units of flicd and the app)
    samples: int  # entries downloaded


def battery_volts(v: int) -> float:
    """Estimated cell voltage of a raw battery value: the app's scale runs 1136 = 0 % .. 1705 = 100 %, which is 2.0 V .. 3.0 V
    (a CR2032) at 3.6 V / 2048 per count. An inference from that match, not a documented unit."""
    return v * 3.6 / 2048


def battery_percent(v: int) -> int:
    """The piecewise scale of the Flic app for a raw value: 1705+ = 100 %, 1136 and below = 0 %."""
    if v > 1705:
        return 100
    if v > 1584:
        return ((((v - 1584) << 16) // 121 * 72) >> 16) + 28
    if v > 1360:
        return ((((v - 1360) << 16) // 224 * 24) >> 16) + 4
    if v > 1136:
        return (((v - 1136) << 16) // 224 * 4) >> 16
    return 0


def battery_from_samples(samples: bytes) -> Battery | None:
    # a 0 entry separates two batteries: older entries belong to the cell that was replaced (seen on both real
    # buttons: 1319 | 0 | 1700, 1103 | 0 | 1556). As in the Flic app the scan stops there, so a log that
    # ends with the marker (battery changed, no sample yet) is unknown and the advertised value has to answer.
    total = count = 0
    for s in reversed(samples):
        if s == 0:
            break
        total += 1100 + 3 * s
        count += 1
        if count == 8:
            break
    if not count:
        return None
    v = total // count
    return Battery(battery_percent(v), v, len(samples))


def parse_battery_header(frame: bytes) -> tuple[int, int]:
    """Op 5 frame -> (N entries in the log, M entries the button can send)."""
    return int.from_bytes(frame[1:3], "little"), int.from_bytes(frame[7:11], "little")


_NAME = re.compile(r"[Ff](\d{3})([A-Za-z0-9_-]{4})")


@dataclass
class AdvName:
    private: bool  # lowercase first letter (flicd is_private; the Flic app calls it the same)
    firmware: int  # revision, e.g. 23
    tail: bytes    # the last 3 bytes of the BD address, base64url coded in the name


def parse_adv_name(name: str | None) -> AdvName | None:
    """Advertised local name: F|f + 3 digit firmware revision + base64url(last 3 address bytes)."""
    m = _NAME.match(name or "")
    if not m:
        return None
    return AdvName(name[0] == "f", int(m.group(1)), base64.urlsafe_b64decode(m.group(2)))


@dataclass
class AdvData:
    index: int
    boot_battery: int
    time: int
    battery: int
    boot_counter: int


def parse_adv_data(d: bytes) -> AdvData | None:
    """Manufacturer data 0x030F (16 bytes, first byte 0), as the Flic app reads it. The battery unit is an estimate."""
    if len(d) != 16 or d[0] != 0:
        return None
    u16 = lambda i: int.from_bytes(d[i:i + 2], "little")
    return AdvData(d[1], u16(2), u16(4) | u16(6) << 16, u16(8), u16(10) | d[12] << 16)
