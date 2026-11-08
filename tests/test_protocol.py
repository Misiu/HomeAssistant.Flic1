"""Self-checks for pyflic1.protocol. Run: python -m unittest discover -s tests (stdlib only).

The "button" here is a re-implementation of the button side written from notes/spec.md, so these tests
catch typos and drift, not disagreements between the spec and a real button. Real-button validation is
the next step (btsnoop capture / live test).
"""

import hashlib
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components", "flic1"))

from pyflic1 import protocol as p  # noqa: E402

H1 = bytes.fromhex("0102030405060708")
H3 = bytes.fromhex("a0a1a2a3a4a5a6a7a8a9aaab")
NONCE = bytes.fromhex("1112131415161718")
ADDR = "80:e4:da:12:34:56"


def button_hello(flag: int, h3: bytes) -> bytes:
    body = bytes([flag]) + NONCE + bytes(range(0x20, 0x28))
    return body + hashlib.sha256(body + h3).digest()[:3]


def button_check_packet(pkt: bytes, nonce: bytes, h3: bytes) -> tuple[int, bytes, int]:
    """Button side of the host->button MAC. Returns op, payload, ctr_low or raises."""
    op, payload, ctr, mac = pkt[0], pkt[1:-5], pkt[-5], pkt[-4:]
    assert hashlib.sha256(bytes([op]) + payload + (ctr).to_bytes(4, "little") + nonce + h3).digest()[:4] == mac
    return op, payload, ctr


def button_event(b: int, counter: int, boot: int, age: int, h3: bytes) -> bytes:
    data = bytes([b]) + counter.to_bytes(3, "little") + boot.to_bytes(3, "little") + age.to_bytes(4, "little")
    return data + hashlib.sha256(data + h3).digest()[:4]


class Hello(unittest.TestCase):
    def test_mac(self):
        h = p.parse_hello(button_hello(0x41, H3))
        self.assertTrue(p.hello_mac_ok(h, H3))
        self.assertFalse(p.hello_mac_ok(h, bytes(12)))
        self.assertEqual(h.nonce, NONCE)
        old = bytearray(button_hello(0x41, H3))      # older firmware: MAC over bytes 0..13 at 14..16
        old[14:17] = hashlib.sha256(bytes(old[:14]) + H3).digest()[:3]
        old[17:20] = b"\x00\x00\x00"
        self.assertTrue(p.hello_mac_ok(p.parse_hello(bytes(old)), H3))

    def test_len(self):
        with self.assertRaises(ValueError):
            p.parse_hello(b"\x41" * 19)

    def test_write_response_range(self):
        self.assertTrue(p.needs_write_response("80:E4:DA:70:00:00"))
        self.assertTrue(p.needs_write_response("80:e4:da:70:72:54"))
        self.assertFalse(p.needs_write_response("80:e4:da:70:72:55"))
        self.assertFalse(p.needs_write_response(ADDR))


class QuickVerify(unittest.TestCase):
    def test_payloads_and_packets(self):
        keys = p.Keys(H1, H3, counter=7, boot=3, mask=5)
        ack = p.ack_payload(keys)
        self.assertEqual(ack, (7).to_bytes(4, "little") + (3).to_bytes(4, "little") + b"")
        self.assertEqual(p.config_payload(10), bytes(6) + (10).to_bytes(2, "little"))
        self.assertEqual(p.config_payload(0x3FF)[6:], (0x1FF).to_bytes(2, "little"))
        rand6 = bytes(range(6))
        for ctr, (op, payload) in enumerate([(9, H1 + rand6), (0x25, ack), (6, p.config_payload(10))]):
            pkt = p.auth_packet(op, payload, ctr, NONCE, H3)
            self.assertEqual(len(pkt), len(payload) + 6)
            self.assertEqual(button_check_packet(pkt, NONCE, H3), (op, payload, ctr))

    def test_ready_frame(self):
        rand6 = b"abcdef"
        good = b"\x40" + hashlib.sha256(NONCE + rand6 + H3).digest()[:14]
        self.assertTrue(p.ready_frame_ok(good, NONCE, rand6, H3))
        self.assertFalse(p.ready_frame_ok(good[:-1] + b"\x00", NONCE, rand6, H3))
        self.assertFalse(p.ready_frame_ok(b"\x41" + good[1:], NONCE, rand6, H3))


class Events(unittest.TestCase):
    def test_mac_and_parse(self):
        frame = button_event(0x1A, 0x010203, 0x040506, 9, H3)
        self.assertTrue(p.event_mac_ok(frame, H3))
        self.assertFalse(p.event_mac_ok(frame, bytes(12)))
        ev = p.parse_event(frame)
        self.assertEqual((ev.counter, ev.boot, ev.age, ev.queued), (0x010203, 0x040506, 9, True))

    def test_replay_filter(self):
        k = p.Keys(H1, H3)
        mk = lambda b, c, boot: p.RawEvent(b, c, boot, 0, False)
        self.assertTrue(p.accept_event(k, mk(0x09, 1, 1)))      # first event
        self.assertFalse(p.accept_event(k, mk(0x09, 1, 1)))     # same type again: replay
        self.assertTrue(p.accept_event(k, mk(0x01, 1, 1)))      # same counter, other class (down): accept
        self.assertFalse(p.accept_event(k, mk(0x01, 1, 1)))
        self.assertFalse(p.accept_event(k, mk(0x09, 0, 1)))     # older counter
        self.assertTrue(p.accept_event(k, mk(0x09, 2, 1)))      # newer counter resets mask
        self.assertFalse(p.accept_event(k, mk(0x09, 9, 0)))     # older boot
        self.assertTrue(p.accept_event(k, mk(0x09, 1, 2)))      # newer boot wins even with lower counter
        self.assertEqual((k.boot, k.counter), (2, 1))
        for b in (0x20, 0x60, 0x68, 0x70):                      # status frames, never user events
            self.assertFalse(p.accept_event(k, mk(b, 5, 2)))

    def test_mask_uses_class_zero_when_bit3_set(self):
        k = p.Keys(H1, H3)
        mk = lambda b: p.RawEvent(b, 1, 1, 0, False)
        self.assertTrue(p.accept_event(k, mk(0x0A)))            # bit3 set -> k = 0
        self.assertEqual(k.mask, 1)
        self.assertFalse(p.accept_event(k, mk(0x0B)))           # also k = 0 -> duplicate

    def test_classify_table(self):
        names = lambda b: [(op, p.CLICK_NAMES[c]) for op, c in p.classify(b)]
        self.assertEqual(names(0x01), [(4, "down")])
        self.assertEqual(names(0x02), [(6, "single"), (7, "single")])
        self.assertEqual(names(0x03), [(5, "hold"), (7, "hold")])
        self.assertEqual(names(0x07), [(5, "hold")])
        self.assertEqual(names(0x00), [(4, "up"), (5, "click")])
        self.assertEqual(names(0x08), [(4, "up"), (5, "click")])
        self.assertEqual(names(0x0A), [(4, "up"), (5, "click"), (7, "single"), (6, "single")])
        self.assertEqual(names(0x0E), [(4, "up"), (6, "single")])
        self.assertEqual(names(0x0B), [(4, "up"), (5, "click"), (6, "double"), (7, "double")])
        self.assertEqual(names(0x0F), [(4, "up"), (6, "double"), (7, "double")])

    def test_user_events_and_ack(self):
        self.assertEqual(p.user_events(0x01), ["down"])
        self.assertEqual(p.user_events(0x0A), ["up", "single"])
        self.assertEqual(p.user_events(0x0B), ["up", "double"])
        self.assertEqual(p.user_events(0x03), ["hold"])
        self.assertEqual([p.needs_ack(b) for b in (0x00, 0x01, 0x02, 0x03, 0x08, 0x0A, 0x0B)],
                         [False, False, True, False, False, True, True])


class FullVerify(unittest.TestCase):
    def test_flow(self):
        rand8 = bytes(range(8))
        self.assertEqual(p.fv_start(rand8), b"\x07\x00" + rand8)
        h2 = bytes.fromhex("f0e0d0c0b0a09080")
        step0 = p.parse_fv0(b"\x00" + NONCE + h2 + bytes(3))
        self.assertEqual((step0.nonce, step0.h2), (NONCE, h2))
        uuid = bytes(range(16)).hex()
        self.assertEqual(p.parse_fv1(b"\x01" + bytes(range(16)) + bytes(3)), uuid)

        body = p.cloud_request_body(rand8, NONCE)
        d = json.loads(body)
        self.assertEqual(d["r1"], int.from_bytes(rand8, "little", signed=True))
        self.assertEqual(d["r2"], int.from_bytes(NONCE, "little", signed=True))
        self.assertEqual(sorted(d), ["platform", "r1", "r2", "version"])
        self.assertEqual(body, '{"r1":%d,"r2":%d,"platform":"linux","version":"0.2"}' % (d["r1"], d["r2"]))

        reply = json.dumps({"mac_address": ADDR.upper(), "h1": H1.hex(), "h2": h2.hex(), "h3": H3.hex(),
                            "r3": "-5", "button_curve_key": "x", "button_curve_key_signature": "y",
                            "color": "black", "factory_serial": "AB12-C34567"})
        keys, r3 = p.parse_cloud_response(reply, ADDR, step0, uuid)
        self.assertEqual((keys.h1, keys.h3, keys.serial, keys.color, r3), (H1, H3, "AB12-C34567", "black", -5))
        self.assertEqual(p.fv_finish(r3, keys.h1), b"\x07\x01" + (2**64 - 5).to_bytes(8, "little") + H1)

    def test_cloud_mismatches(self):
        step0 = p.FvStep0(NONCE, bytes(8))
        base = {"mac_address": ADDR, "h1": H1.hex(), "h2": bytes(8).hex(), "h3": H3.hex(), "r3": "1"}
        with self.assertRaises(ValueError):
            p.parse_cloud_response(json.dumps({**base, "mac_address": "80:e4:da:00:00:01"}), ADDR, step0, "u")
        with self.assertRaises(ValueError):
            p.parse_cloud_response(json.dumps({**base, "h2": "ff" * 8}), ADDR, step0, "u")

    def test_cloud_returns_long_values(self):
        """Seen on real hardware: the cloud's h1/h2/h3 are longer, only the first 8/8/12 bytes count."""
        h2 = bytes.fromhex("f0e0d0c0b0a09080")
        step0 = p.FvStep0(NONCE, h2)
        long = lambda b, n: (b + bytes(range(n - len(b)))).hex()      # same prefix, 32 bytes total
        reply = {"mac_address": ADDR, "h1": long(H1, 32), "h2": long(h2, 32), "h3": long(H3, 32), "r3": 7}
        keys, r3 = p.parse_cloud_response(json.dumps(reply), ADDR, step0, "u")
        self.assertEqual((keys.h1, keys.h3, r3), (H1, H3, 7))
        with self.assertRaises(ValueError) as cm:                       # message carries the diagnostics
            p.parse_cloud_response(json.dumps({**reply, "h2": long(bytes(8), 32)}), ADDR, step0, "u")
        self.assertIn("32 bytes", str(cm.exception))
        with self.assertRaises(ValueError):                             # shorter than the protocol needs
            p.parse_cloud_response(json.dumps({**reply, "h3": H3[:8].hex()}), ADDR, step0, "u")

    def test_conn_params_are_valid(self):
        """BLE rule: supervision timeout must exceed 2 x (1 + latency) x max interval."""
        for mode, (lo, hi, latency, timeout) in p.CONN_PARAMS.items():
            self.assertLessEqual(lo, hi, mode)
            self.assertLess(2 * (1 + latency) * hi * 1.25, timeout * 10, mode)
            self.assertLessEqual(latency, 499, mode)

    def test_battery_percentage(self):
        pct = lambda s: p.battery_from_samples(bytes(s)).percent
        # expected values from the app's piecewise linear scale (float, floored): 1705+ = 100,
        # 1584..1705 = 28 + 72*(v-1584)/121, 1360..1584 = 4 + 24*(v-1360)/224, 1136..1360 = 4*(v-1136)/224
        self.assertEqual(pct([158] * 8), 26)                 # v = 1574
        self.assertEqual(pct([200]), 97)                     # v = 1700
        self.assertEqual(pct([255] * 3), 100)                # v = 1865
        self.assertEqual(pct([120]), 14)                     # v = 1460
        self.assertEqual(p.battery_from_samples(bytes([120])).raw, 1460)
        self.assertEqual(pct([10]), 0)                       # v = 1130, below the scale
        self.assertEqual(pct([60] * 8), 2)                   # v = 1280
        self.assertEqual(pct([128]), 17)                     # v = 1484
        self.assertEqual(pct([170]), 43)                     # v = 1610, middle band
        self.assertIsNone(p.battery_from_samples(b""))
        self.assertIsNone(p.battery_from_samples(bytes([5, 0])))   # newest entry is the battery-change marker
        # real logs 2026-10-22: old cell | marker | new cell (tails of the two buttons)
        old = bytes.fromhex("48474749")
        self.assertIsNone(p.battery_from_samples(old + b"\x00"))      # 16:05, nothing sampled since the change
        new1 = p.battery_from_samples(old + bytes([0, 0xC8, 0xD5]))     # 20:47: raw 1719, the old cell is not averaged in
        self.assertEqual((new1.raw, new1.percent), (1719, 100))
        new2 = p.battery_from_samples(bytes([1, 1, 0, 0x98, 0xB2]))
        self.assertEqual((new2.raw, new2.percent), (1595, 34))

    def test_battery_volts_match_the_scale_ends(self):
        self.assertAlmostEqual(p.battery_volts(1136), 2.0, delta=0.01)       # 0 %
        self.assertAlmostEqual(p.battery_volts(1705), 3.0, delta=0.01)       # 100 %
        self.assertAlmostEqual(p.battery_volts(1719), 3.02, delta=0.01)      # the fresh cell read on 2026-10-22

    def test_battery_percent_scale(self):
        # real buttons 2026-10-22: advertised 1711 (fresh cell); 1586 advertised vs log 1556 = 25 % read in the same minute
        self.assertEqual([p.battery_percent(v) for v in (1711, 1706, 1705, 1586, 1556, 1314, 1136, 900)],
                         [100, 100, 100, 29, 25, 3, 0, 0])

    def test_battery_uses_newest_eight_nonzero(self):
        old_low = [20] * 5                                     # ignored: older than the newest 8
        bat = p.battery_from_samples(bytes(old_low + [158] * 8))
        self.assertEqual((bat.percent, bat.raw, bat.samples), (26, 1574, 13))
        zero_stops = p.battery_from_samples(bytes([200, 200, 0, 158, 158]))   # a zero inside ends the scan backwards
        self.assertEqual(zero_stops.raw, 1574)

    def test_battery_header(self):
        frame = b"\x05" + (300).to_bytes(2, "little") + (999).to_bytes(4, "little") + (290).to_bytes(4, "little")
        self.assertEqual(p.parse_battery_header(frame.ljust(20, b"\0")), (300, 290))

    def test_adv_name_and_data_from_real_buttons(self):
        a = p.parse_adv_name("f023dRNV")
        self.assertEqual((a.private, a.firmware, a.tail.hex()), (True, 23, "751355"))
        b = p.parse_adv_name("F023dJ_c")
        self.assertEqual((b.private, b.firmware, b.tail.hex()), (False, 23, "749fdc"))
        self.assertIsNone(p.parse_adv_name("Flic"))
        self.assertIsNone(p.parse_adv_name(None))
        d = p.parse_adv_data(bytes.fromhex("0003ec05af0400002506020000dae480"))
        self.assertEqual((d.index, d.boot_battery, d.time, d.battery, d.boot_counter), (3, 1516, 1199, 1573, 2))
        self.assertIsNone(p.parse_adv_data(b"\x01" * 16))
        self.assertIsNone(p.parse_adv_data(b"\x00" * 13))

    def test_keys_roundtrip(self):
        k = p.Keys(H1, H3, "u", "s", "c", 1, 2, 3)
        self.assertEqual(p.Keys.from_dict(json.loads(json.dumps(k.to_dict()))), k)
        with self.assertRaises(ValueError):
            p.Keys(H1, H3[:11])


if __name__ == "__main__":
    unittest.main()
