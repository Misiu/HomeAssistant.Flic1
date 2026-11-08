"""Async flow of Flic1Session against a fake bleak client that plays the button (from notes/spec.md)."""

import asyncio
import hashlib
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components", "flic1"))

from pyflic1 import protocol as p  # noqa: E402
from pyflic1.client import FlicError, Flic1Session, NotPairingMode  # noqa: E402

ADDR = "80:e4:da:12:34:56"
H1, H3 = bytes(range(1, 9)), bytes(range(0xA0, 0xAC))
UUID = bytes(range(0x30, 0x40))
sha = lambda *a: hashlib.sha256(b"".join(a)).digest()


class FakeButton:
    """Plays the button side. Records what the host wrote."""

    def __init__(self, flag=0x41, paired=True, nonce=b"N" * 8, h2=b"\x22" * 8):
        self.flag, self.paired, self.nonce, self.h2 = flag, paired, nonce, h2
        self.cb: dict[int, object] = {}
        self.writes: list[bytes] = []
        self.auth: list[tuple[int, bytes, int]] = []
        self.responses: list[bool] = []
        self.rand8 = b""
        self.silent = False
        self.services = []
        self.log = b""         # battery log, one byte per entry
        self.cap = 10 ** 6      # entries the button can still send

    def hello(self) -> bytes:
        body = bytes([self.flag]) + self.nonce + bytes(8)
        return body + sha(body, H3)[:3]

    async def read_gatt_char(self, handle):
        assert handle == p.HANDLE_CFG
        return bytearray(self.hello())

    async def start_notify(self, handle, cb):
        self.cb[handle] = cb

    def push(self, handle, data: bytes) -> None:
        asyncio.get_running_loop().call_soon(self.cb[handle], None, bytearray(data))

    async def write_gatt_char(self, handle, data, response=False):
        assert handle == p.HANDLE_CFG
        data = bytes(data)
        self.writes.append(data)
        self.responses.append(response)
        if self.silent:
            return
        if data[:2] == b"\x07\x00":
            self.rand8 = data[2:10]
            self.push(p.HANDLE_CFG, b"\x00" + self.nonce + self.h2 + bytes(3))
            self.push(p.HANDLE_CFG, b"\x01" + UUID + bytes(3))
        elif data[:2] == b"\x07\x01":
            assert data[2:10] == (77).to_bytes(8, "little") and data[10:18] == H1
            self.paired = True
            self.push(p.HANDLE_CFG, b"\x02" + bytes(19))
        else:
            op, payload, ctr, mac = data[0], data[1:-5], data[-5], data[-4:]
            n = len(self.auth)
            assert ctr == n and sha(bytes([op]) + payload, n.to_bytes(4, "little"), self.nonce, H3)[:4] == mac
            self.auth.append((op, payload, ctr))
            if op == 9:
                assert payload[:8] == H1
                self.push(p.HANDLE_EVT, b"\x40" + sha(self.nonce, payload[8:14], H3)[:14])
            elif op == 0x18:
                n = len(self.log)
                hdr = b"\x05" + n.to_bytes(2, "little") + (1000).to_bytes(4, "little") + min(n, self.cap).to_bytes(4, "little")
                self.push(p.HANDLE_CFG, hdr.ljust(20, b"\x00"))
            elif op == 0x19:
                start = int.from_bytes(payload[:4], "little")
                end = min(len(self.log), start + 285)
                for i in range(start, end, 19):
                    self.push(p.HANDLE_CFG, (b"\x06" + self.log[i:min(i + 19, end)]).ljust(20, b"\x00"))

    def event(self, b, counter, boot, age=0) -> None:
        d = bytes([b]) + counter.to_bytes(3, "little") + boot.to_bytes(3, "little") + age.to_bytes(4, "little")
        self.push(p.HANDLE_EVT, d + sha(d, H3)[:4])


class Quick(unittest.IsolatedAsyncioTestCase):
    async def test_events_ack_and_replay(self):
        btn = FakeButton()
        seen, saved = [], []
        s = Flic1Session(btn, ADDR, on_event=lambda n, e: seen.append((n, e.queued)), on_keys=lambda k: saved.append(k.counter))
        await s.open()
        keys = p.Keys(H1, H3, counter=4, boot=2)
        await s.quick_verify(keys)
        self.assertEqual([a[0] for a in btn.auth], [9, 0x25, 6])
        self.assertEqual(btn.auth[1][1], (4).to_bytes(4, "little") + (2).to_bytes(4, "little") + b"\x00")
        self.assertEqual(btn.auth[2][1], bytes(6) + (10).to_bytes(2, "little"))
        self.assertEqual(btn.responses, [False, True, False])       # only the ack is written with response

        btn.event(0x01, 5, 2)            # down
        btn.event(0x1A, 5, 2, age=3)     # queued release, bit3 + single click
        btn.event(0x1A, 5, 2)            # replay of the same event
        btn.event(0x0B, 6, 2)            # double click
        btn.event(0x0B, 3, 2)            # old counter
        await asyncio.sleep(0.05)
        self.assertEqual(seen, [("down", False), ("up", True), ("single", True), ("up", False), ("double", False)])
        self.assertEqual(saved, [5, 5, 6])
        self.assertEqual((keys.counter, keys.boot), (6, 2))
        acks = [a for a in btn.auth if a[0] == 0x25]
        self.assertEqual(len(acks), 3)   # initial + single + double
        self.assertEqual(acks[2][1], (6).to_bytes(4, "little") + (2).to_bytes(4, "little") + b"\x01")

    async def test_bad_event_hash_ignored(self):
        btn = FakeButton()
        seen = []
        s = Flic1Session(btn, ADDR, on_event=lambda n, e: seen.append(n))
        await s.open()
        await s.quick_verify(p.Keys(H1, H3))
        d = bytes([0x01]) + bytes([1, 0, 0, 1, 0, 0]) + bytes(4)
        btn.push(p.HANDLE_EVT, d + b"\x00" * 4)
        await asyncio.sleep(0.02)
        self.assertEqual(seen, [])

    async def test_wrong_keys(self):
        btn = FakeButton()
        s = Flic1Session(btn, ADDR)
        await s.open()
        with self.assertRaises(FlicError):
            await s.quick_verify(p.Keys(H1, bytes(12)))

    async def test_no_ready(self):
        btn = FakeButton()
        btn.silent = True
        s = Flic1Session(btn, ADDR)
        await s.open()
        with self.assertRaises(FlicError):
            await s.quick_verify(p.Keys(H1, H3), timeout=0.05)

    async def test_write_response_for_early_batch(self):
        btn = FakeButton()
        s = Flic1Session(btn, "80:e4:da:70:10:00")
        await s.open()
        await s.quick_verify(p.Keys(H1, H3))
        self.assertEqual(btn.responses, [True, True, True])

    async def test_set_latency(self):
        btn = FakeButton()
        s = Flic1Session(btn, ADDR)
        self.assertFalse(await s.set_latency("normal"))     # backend without the call: not fatal
        calls = []

        async def setter(*a):
            calls.append(a)

        btn.set_connection_params = setter
        self.assertTrue(await s.set_latency("low"))
        self.assertEqual(calls, [(13, 14, 79, 421)])

        async def broken(*a):
            raise RuntimeError("old firmware")

        btn.set_connection_params = broken
        self.assertFalse(await s.set_latency("normal"))

    async def test_status_frames_are_ignored_before_the_mac_check(self):
        btn = FakeButton()
        seen = []
        s = Flic1Session(btn, ADDR, on_event=lambda n, e: seen.append(n))
        await s.open()
        await s.quick_verify(p.Keys(H1, H3))
        btn.push(p.HANDLE_EVT, b"\x70" + bytes(14))      # seen on hardware: 0x70 with a MAC that never matches
        btn.push(p.HANDLE_EVT, b"\x60" + bytes(14))
        await asyncio.sleep(0.02)
        self.assertEqual(seen, [])

    async def test_set_latency_proxy_leaves_parameters(self):
        btn = FakeButton()
        calls = []

        async def setter(*a):
            calls.append(a)

        btn.set_connection_params = setter
        self.assertFalse(await Flic1Session(btn, ADDR).set_latency("proxy"))
        self.assertEqual(calls, [])
        self.assertTrue(await Flic1Session(btn, ADDR).set_latency("stable"))
        self.assertEqual(calls, [p.CONN_PARAMS["stable"]])

    async def test_bad_hello_length(self):
        btn = FakeButton()
        btn.hello = lambda: b"\x41" * 5
        with self.assertRaises(FlicError):
            await Flic1Session(btn, ADDR).open()


class BatteryLog(unittest.IsolatedAsyncioTestCase):
    async def _ready(self, btn):
        s = Flic1Session(btn, ADDR)
        await s.open()
        await s.quick_verify(p.Keys(H1, H3))
        return s

    async def test_short_log(self):
        btn = FakeButton()
        btn.log = bytes([0] * 5 + [100] * 12 + [158] * 8)
        s = await self._ready(btn)
        bat = await s.read_battery(timeout=2)
        self.assertEqual((bat.percent, bat.raw, bat.samples), (26, 1574, 25))
        self.assertEqual([a[0] for a in btn.auth][3:], [0x18, 0x19])
        self.assertEqual(btn.auth[4][1], (0).to_bytes(4, "little"))

    async def test_long_log_needs_a_second_request_and_skips_old_entries(self):
        btn = FakeButton()
        btn.log = bytes([50] * 10 + [158] * 290)          # 300 entries, the button can send the last 290
        btn.cap = 290
        s = await self._ready(btn)
        bat = await s.read_battery(timeout=2)
        self.assertEqual((bat.percent, bat.samples), (26, 290))
        reqs = [int.from_bytes(a[1][:4], "little") for a in btn.auth if a[0] == 0x19]
        self.assertEqual(reqs, [10, 10 + 285])

    async def test_empty_log_and_silence(self):
        btn = FakeButton()
        s = await self._ready(btn)
        self.assertIsNone(await s.read_battery(timeout=2))
        btn.silent = True
        with self.assertRaises(FlicError):
            await s.read_battery(timeout=0.05)


class Full(unittest.IsolatedAsyncioTestCase):
    async def _pair(self, btn, mac=ADDR):
        calls = []

        async def unlock(uuid, body):
            d = json.loads(body)
            calls.append((uuid, d))
            self.assertEqual(sorted(d), ["platform", "r1", "r2", "version"])
            self.assertEqual(d["r1"], int.from_bytes(btn.rand8, "little", signed=True))
            self.assertEqual(d["r2"], int.from_bytes(btn.nonce, "little", signed=True))
            return json.dumps({"mac_address": mac, "h1": H1.hex(), "h2": btn.h2.hex(), "h3": H3.hex(), "r3": "77",
                               "color": "white", "factory_serial": "SN1"})

        s = Flic1Session(btn, ADDR)
        await s.open()
        return s, calls, await s.full_verify(unlock)

    async def test_pair_from_public_mode(self):
        btn = FakeButton(flag=0x42, paired=False)
        s, calls, keys = await self._pair(btn)
        self.assertEqual((keys.h1, keys.h3, keys.serial, keys.color, keys.uuid), (H1, H3, "SN1", "white", UUID.hex()))
        self.assertEqual(calls[0][0], UUID.hex())
        self.assertTrue(btn.paired)
        await asyncio.sleep(0.01)
        self.assertEqual([a[0] for a in btn.auth], [0x25, 6])   # authenticated with the button nonce, counters 0,1
        seen = []
        s._on_event = lambda n, e: seen.append(n)
        btn.event(0x02, 1, 1)
        await asyncio.sleep(0.02)
        self.assertEqual(seen, ["single"])

    async def test_refuses_private_button(self):
        btn = FakeButton(flag=0x40)
        s = Flic1Session(btn, ADDR)
        await s.open()
        with self.assertRaises(NotPairingMode):
            await s.full_verify(None)

    async def test_cloud_mac_mismatch(self):
        with self.assertRaises(FlicError):
            await self._pair(FakeButton(flag=0x42), mac="80:e4:da:00:00:01")

    async def test_button_silent(self):
        btn = FakeButton(flag=0x42)
        btn.silent = True
        s = Flic1Session(btn, ADDR)
        await s.open()
        with self.assertRaises(FlicError):
            await s.full_verify(None, timeout=0.05)


if __name__ == "__main__":
    unittest.main()
