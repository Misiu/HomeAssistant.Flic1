"""One BLE connection to a Flic 1. Works with any bleak client, including HA's ESPHome proxies.

Order mirrors flicd: read the 20-byte hello (handle 0x12), enable notifications on 0x12 and 0x15,
then quick verify (paired) or full verify (pairing). Events arrive on 0x15 after the ready frame.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any

from . import protocol as p

_LOGGER = logging.getLogger(__name__)


class FlicError(Exception):
    """Protocol level failure (bad hello, MAC mismatch, timeout in the verify exchange)."""


class NotPairingMode(FlicError):
    """The hello flag says the button is private: it only talks to hosts it already knows."""


class Flic1Session:
    def __init__(
        self,
        client: Any,  # bleak.BleakClient
        address: str,
        *,
        on_event: Callable[[str, p.RawEvent], None] | None = None,
        on_keys: Callable[[p.Keys], None] | None = None,
        auto_disconnect: int = 10,
    ) -> None:
        self.client, self.address = client, address
        self.keys: p.Keys | None = None
        self.hello: p.Hello | None = None
        self._on_event, self._on_keys = on_event, on_keys
        self._auto_disconnect = auto_disconnect
        self._cfg: asyncio.Queue[bytes] = asyncio.Queue()
        self._nonce, self._ctr = b"", 0
        self._ready = asyncio.Event()
        self._ready_check: Callable[[bytes], bool] | None = None
        self._write_response = p.needs_write_response(address)
        self._tasks: set[asyncio.Task] = set()
        self._send_lock = asyncio.Lock()

    # --- connection setup ------------------------------------------------------------------

    def _dump_gatt(self) -> None:
        for svc in self.client.services:
            _LOGGER.info("%s GATT service %s", self.address, svc.uuid)
            for ch in svc.characteristics:
                _LOGGER.info("%s   handle 0x%02x %s %s", self.address, ch.handle, ch.uuid, ",".join(ch.properties))

    async def open(self) -> p.Hello:
        _LOGGER.debug("%s opening session", self.address)
        self._dump_gatt()
        raw = bytes(await self.client.read_gatt_char(p.HANDLE_CFG))
        _LOGGER.info("%s hello: %s", self.address, raw.hex())
        try:
            self.hello = p.parse_hello(raw)
        except ValueError as err:
            raise FlicError(str(err)) from err
        await self.client.start_notify(p.HANDLE_CFG, self._on_cfg)
        await self.client.start_notify(p.HANDLE_EVT, self._on_evt)
        return self.hello

    async def _write(self, data: bytes, response: bool = False) -> None:
        _LOGGER.debug("%s -> %s", self.address, data.hex())
        await self.client.write_gatt_char(p.HANDLE_CFG, data, response=response or self._write_response)

    async def _send_auth(self, op: int, payload: bytes, response: bool = False) -> None:
        assert self.keys is not None
        async with self._send_lock:  # ack tasks and the battery read must not reorder counter and write
            pkt = p.auth_packet(op, payload, self._ctr, self._nonce, self.keys.h3)
            self._ctr += 1
            await self._write(pkt, response)

    async def _send_ack(self) -> None:
        assert self.keys is not None
        await self._send_auth(p.OP_ACK, p.ack_payload(self.keys), response=True)  # as the Flic app does

    # --- notifications ---------------------------------------------------------------------

    def _on_cfg(self, _sender: Any, data: bytearray) -> None:
        _LOGGER.debug("%s <- cfg %s", self.address, bytes(data).hex())
        self._cfg.put_nowait(bytes(data))

    def _on_evt(self, _sender: Any, data: bytearray) -> None:
        frame = bytes(data)
        _LOGGER.debug("%s <- evt %s", self.address, frame.hex())
        if not self._ready.is_set():
            if self._ready_check and self._ready_check(frame):
                self._ready.set()
            return
        assert self.keys is not None
        if frame[0] in p.IGNORED_EVENT_BYTES:  # status frames (0x60/0x70 connection parameters), no MAC to check
            return
        if not p.event_mac_ok(frame, self.keys.h3):
            _LOGGER.warning("%s invalid event hash, ignored", self.address)
            return
        ev = p.parse_event(frame)
        if not p.accept_event(self.keys, ev):
            _LOGGER.debug("%s replayed or ignored event 0x%02x", self.address, ev.b)
            return
        if self._on_keys:
            self._on_keys(self.keys)
        for name in p.user_events(ev.b):
            _LOGGER.info("%s event %s (queued=%s age=%s)", self.address, name, ev.queued, ev.age)
            if self._on_event:
                self._on_event(name, ev)
        if p.needs_ack(ev.b):
            task = asyncio.get_running_loop().create_task(self._send_ack())
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _next_cfg(self, op: int, timeout: float) -> bytes:
        async with asyncio.timeout(timeout):
            while True:
                frame = await self._cfg.get()
                if len(frame) >= p.HELLO_LEN and frame[0] == op:
                    return frame

    # --- verification ----------------------------------------------------------------------

    async def quick_verify(self, keys: p.Keys, timeout: float = 10.0) -> None:
        assert self.hello is not None
        if not p.hello_mac_ok(self.hello, keys.h3):
            raise FlicError("hello MAC mismatch: wrong keys or the button was re-paired")
        self.keys, self._nonce, self._ctr = keys, self.hello.nonce, 0
        rand6 = os.urandom(6)
        self._ready_check = lambda f: p.ready_frame_ok(f, self.hello.nonce, rand6, keys.h3)
        await self._send_auth(p.OP_VERIFY_REPLY, keys.h1 + rand6)
        await self._send_ack()
        await self._send_auth(p.OP_CONFIG, p.config_payload(self._auto_disconnect))
        try:
            async with asyncio.timeout(timeout):
                await self._ready.wait()
        except TimeoutError as err:
            raise FlicError("no ready frame after quick verify") from err

    async def set_latency(self, mode: str = "normal") -> bool:
        """Switch the link to flicd's connection parameters for `mode`. Returns False if the proxy cannot.

        Needs ESPHome proxy firmware with connection-params support (bleak-esphome set_connection_params).
        Without it the proxy keeps its ~10 ms / latency 0 parameters: prefer a short auto_disconnect then.
        """
        if mode == "proxy":
            _LOGGER.info("%s: leaving the proxy's own connection parameters", self.address)
            return False
        setter = getattr(self.client, "set_connection_params", None) or getattr(
            getattr(self.client, "_backend", None), "set_connection_params", None
        )
        if setter is None:
            _LOGGER.warning("%s: this Bluetooth backend cannot set connection parameters", self.address)
            return False
        try:
            await setter(*p.CONN_PARAMS[mode])
        except Exception as err:  # noqa: BLE001 - backend specific errors, never fatal
            _LOGGER.warning("%s: set_connection_params failed: %r", self.address, err)
            return False
        _LOGGER.info("%s: connection parameters set to %s %s", self.address, mode, p.CONN_PARAMS[mode])
        return True

    async def read_battery(self, timeout: float = 20.0) -> p.Battery | None:
        """Download the battery log and turn the newest entries into a percentage (see protocol.py)."""
        while not self._cfg.empty():  # frames left over from the verify exchange
            self._cfg.get_nowait()
        samples = bytearray()
        try:
            async with asyncio.timeout(timeout):
                await self._send_auth(p.OP_BATTERY_LOG, b"")
                total, avail = p.parse_battery_header(await self._next_cfg(5, timeout))
                start = max(0, total - avail)
                want = total - start
                _LOGGER.debug("%s battery log: %d entries, button sends from %d", self.address, total, start)
                if want:
                    await self._send_auth(p.OP_BATTERY_FROM, start.to_bytes(4, "little"))
                while len(samples) < want:
                    frame = await self._next_cfg(6, timeout)
                    samples += frame[1:1 + min(p.BATTERY_CHUNK, want - len(samples))]
                    if len(samples) < want and len(samples) % p.BATTERY_BLOCK == 0:
                        await self._send_auth(p.OP_BATTERY_FROM, (start + len(samples)).to_bytes(4, "little"))
        except TimeoutError as err:
            raise FlicError("battery log: the button did not answer") from err
        return p.battery_from_samples(bytes(samples))

    async def full_verify(
        self,
        unlock: Callable[[str, str], Awaitable[str]],
        timeout: float = 25.0,
    ) -> p.Keys:
        """Pair with a button in pairing mode. `unlock(uuid_hex, json_body)` is the Flic cloud call."""
        assert self.hello is not None
        if self.hello.flag not in (0x41, 0x42, 0x43):
            raise NotPairingMode(f"button is not in pairing mode (hello flag 0x{self.hello.flag:02x}), hold it 7 s")
        if self.hello.flag == 0x43:
            # the Flic app calls BluetoothDevice.createBond() for this flag before verifying
            pair = getattr(self.client, "pair", None)
            _LOGGER.warning("%s: hello flag 0x43, button asks for a BLE bond; trying pair()", self.address)
            if pair:
                try:
                    await pair()
                except Exception as err:  # noqa: BLE001 - backend specific, never fatal
                    _LOGGER.warning("%s: pair() failed: %r", self.address, err)
        rand8 = os.urandom(8)
        await self._write(p.fv_start(rand8))
        try:
            step0 = p.parse_fv0(await self._next_cfg(0, timeout))
            uuid = p.parse_fv1(await self._next_cfg(1, timeout))
            text = await unlock(uuid, p.cloud_request_body(rand8, step0.nonce))
            keys, r3 = p.parse_cloud_response(text, self.address, step0, uuid)
            await self._write(p.fv_finish(r3, keys.h1))
            await self._next_cfg(2, timeout)
        except TimeoutError as err:
            raise FlicError("button did not answer during pairing") from err
        except (ValueError, KeyError) as err:
            raise FlicError(f"pairing data rejected: {err}") from err
        self.keys, self._nonce, self._ctr = keys, step0.nonce, 0
        await self._send_ack()
        await self._send_auth(p.OP_CONFIG, p.config_payload(self._auto_disconnect))
        self._ready.set()
        return keys
