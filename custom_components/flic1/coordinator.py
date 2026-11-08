"""Connects to a Flic 1 whenever it advertises, verifies it and passes its events on to Home Assistant.

A button advertises after a press, or after it lost its link. Then Home Assistant picks a connectable
Bluetooth proxy, the quick verify runs, and the queued and live presses arrive. With the default
AUTO_DISCONNECT of 511 the button never hangs up, so the link stays up. With a lower value the button
hangs up after that many seconds of silence and connects again at the next press.

A button in private mode only answers the proxy it was paired through (see README).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable

from bleak import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr

from .const import (
    BATTERY_INTERVAL,
    CONF_AUTO_DISCONNECT,
    CONF_BATTERY,
    CONF_FIRMWARE,
    CONF_KEYS,
    CONF_LATENCY,
    CONF_QUEUED_TIMEOUT,
    DEFAULT_AUTO_DISCONNECT,
    DEFAULT_LATENCY,
    DEFAULT_QUEUED_TIMEOUT,
    DOMAIN,
    EVENT_NAME,
    LINK_GRACE,
    LOW_BATTERY,
    MAINTENANCE_DELAY,
    MAX_RETRY_COOLDOWN,
    MAX_SESSION,
    PROBE_INTERVAL,
    PROBE_TIMEOUT,
    REFUSAL_HINT_EVERY,
    RETRY_COOLDOWN,
    STALE_WARN,
)
from .pyflic1 import protocol as p
from .pyflic1.client import FlicError, Flic1Session

_LOGGER = logging.getLogger(__name__)

type EventListener = Callable[[str, p.RawEvent], None]


class Flic1Coordinator:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass, self.entry = hass, entry
        self.address: str = entry.data[CONF_ADDRESS]
        self.keys = p.Keys.from_dict(entry.data[CONF_KEYS])
        self.connected = False
        self.battery: dict | None = entry.data.get(CONF_BATTERY)
        self.firmware: int | None = entry.data.get(CONF_FIRMWARE)
        self.adv: p.AdvData | None = None
        self._battery_listeners: list[Callable[[], None]] = []
        self._task: asyncio.Task | None = None
        self._cooldown_until = 0.0
        self._failures = 0
        self._t_adv = self._t_ready = self._t_last = 0.0
        self._rssi: int | None = None
        self._source = ""
        self._session: Flic1Session | None = None
        self._closed: asyncio.Event | None = None
        self._probe_at = self._hint_at = 0.0
        self._stale_warned = False
        self._listeners: list[EventListener] = []

    @callback
    def async_add_listener(self, listener: EventListener) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @callback
    def async_add_battery_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._battery_listeners.append(listener)
        return lambda: self._battery_listeners.remove(listener)

    @callback
    def async_on_advertisement(self, info: bluetooth.BluetoothServiceInfoBleak) -> None:
        if (name := p.parse_adv_name(info.name)) and name.firmware != self.firmware:
            self._set_firmware(name.firmware)
        if (data := info.manufacturer_data.get(p.MFR_ID)) and (adv := p.parse_adv_data(bytes(data))):
            self.adv = adv
            if adv.battery:
                self._set_battery(p.battery_percent(adv.battery), adv.battery, "advertisement")
        self._rssi = info.rssi
        if not info.connectable:
            return
        if self._task and not self._task.done():
            self._check_link()
            return
        if time.monotonic() < self._cooldown_until:
            return
        self._t_adv = time.monotonic()
        self._source = info.source
        _LOGGER.info("%s advertisement via %s rssi=%s name=%s adv_battery=%s, connecting", self.address, info.source,
                     info.rssi, info.name, self.adv.battery if self.adv else None)
        if info.rssi < -80:
            _LOGGER.warning("%s weak signal (%s dBm): links drop easily, put an ESPHome proxy closer", self.address, info.rssi)
        self._task = self.hass.async_create_background_task(
            self._async_session(info.device), name=f"flic1 {self.address}"
        )

    async def _async_session(self, device) -> None:
        client = None
        battery_task = None
        closed = self._closed = asyncio.Event()
        self._stale_warned = False
        auto_disconnect = int(self.entry.options.get(CONF_AUTO_DISCONNECT, DEFAULT_AUTO_DISCONNECT))
        try:
            client = await establish_connection(
                BleakClientWithServiceCache,
                device,
                self.address,
                disconnected_callback=lambda _c: closed.set(),
                max_attempts=2,
                ble_device_callback=lambda: bluetooth.async_ble_device_from_address(
                    self.hass, self.address, connectable=True
                ),
            )
            t_conn = time.monotonic()
            session = Flic1Session(
                client, self.address, on_event=self._dispatch, on_keys=self._save_keys,
                auto_disconnect=auto_disconnect,
            )
            await session.open()
            t_open = time.monotonic()
            await session.quick_verify(self.keys)
            self._t_ready = self._t_last = time.monotonic()
            self._failures = 0
            # verify ran on the proxy's fast parameters; now go easy on the button's battery
            await session.set_latency(self.entry.options.get(CONF_LATENCY, DEFAULT_LATENCY))
            self.connected = True
            self._session = session
            _LOGGER.info(
                "%s verified. timing: advert->connected %.0f ms, ->hello+notify %.0f ms, ->ready %.0f ms (total %.0f ms)",
                self.address, (t_conn - self._t_adv) * 1000, (t_open - t_conn) * 1000,
                (self._t_ready - t_open) * 1000, (self._t_ready - self._t_adv) * 1000,
            )
            if time.time() - (self.battery or {}).get("log_time", 0) > BATTERY_INTERVAL:
                battery_task = self.hass.async_create_background_task(
                    self._async_battery(session), name=f"flic1 battery {self.address}"
                )
            async with asyncio.timeout(None if auto_disconnect >= 511 else MAX_SESSION):
                await closed.wait()
            now = time.monotonic()
            # with 511 the button never hangs up by itself, so this is a lost link (RF, a competing central, a proxy restart)
            _LOGGER.log(logging.WARNING if auto_disconnect >= 511 else logging.INFO,
                        "%s disconnected after %.0f s, %.0f s since the last event, last advert rssi %s",
                        self.address, now - self._t_ready, now - self._t_last, self._rssi)
        except (TimeoutError, BleakError, FlicError) as err:
            _LOGGER.warning("%s session ended: %r", self.address, err)
            if "ESP_GATT_ERROR" in str(err):
                self._hint_refused()
            self._back_off()
        except Exception:  # noqa: BLE001 - a bug must not turn every advertisement into a new crashing session
            _LOGGER.exception("%s unexpected error in session", self.address)
            self._back_off()
        finally:
            self.connected = False
            self._session = self._closed = None
            if battery_task:
                battery_task.cancel()
            if client:
                with contextlib.suppress(BleakError, TimeoutError):
                    await client.disconnect()

    @callback
    def _check_link(self) -> None:
        """A button that advertises (connectable) while we think we are connected may have lost the link without anyone
        telling us, e.g. a proxy whose Bluetooth stack hung. Probe it with a plain GATT read, a dead link times out."""
        now = time.monotonic()
        if not (self.connected and self._session and now - self._t_ready > LINK_GRACE and now >= self._probe_at):
            return
        self._probe_at = now + PROBE_INTERVAL
        self.hass.async_create_background_task(self._async_probe(self._session), name=f"flic1 probe {self.address}")

    async def _async_probe(self, session: Flic1Session) -> None:
        try:
            async with asyncio.timeout(PROBE_TIMEOUT):
                await session.client.read_gatt_char(p.HANDLE_CFG)
        except (TimeoutError, BleakError) as err:
            _LOGGER.warning("%s advertises although the link looks up and a read failed (%r): dropping the link",
                            self.address, err)
            if self._closed:
                self._closed.set()

    def _hint_refused(self) -> None:
        now = time.monotonic()
        if now < self._hint_at:
            return
        self._hint_at = now + REFUSAL_HINT_EVERY
        find = getattr(bluetooth, "async_scanner_by_source", None)
        scanner = find(self.hass, self._source) if find and self._source else None
        _LOGGER.warning(
            "%s refuses the connection (GATT error 133 right after the request). A Flic 1 only works with the "
            "Bluetooth proxy it was paired through, and this advertisement came via %s. Pair the button again next "
            "to the proxy you want to use (remove and add the integration, then hold the button for 7 s), or make "
            "the other proxies passive (bluetooth_proxy: active: false).", self.address,
            getattr(scanner, "name", None) or self._source or "an unknown proxy")

    def _back_off(self) -> None:
        self._failures += 1
        self._cooldown_until = time.monotonic() + min(RETRY_COOLDOWN * 2 ** (self._failures - 1), MAX_RETRY_COOLDOWN)

    async def _async_battery(self, session: Flic1Session) -> None:
        await asyncio.sleep(MAINTENANCE_DELAY)  # queued events, their acks and the first presses go first
        try:
            bat = await session.read_battery()
        except (TimeoutError, BleakError, FlicError) as err:
            _LOGGER.warning("%s battery read failed: %r", self.address, err)
            return
        if bat is None:
            _LOGGER.info("%s battery log is empty", self.address)
            return
        _LOGGER.info("%s battery %d%% (raw %d from %d log entries; advertised raw %s)", self.address,
                     bat.percent, bat.raw, bat.samples, self.adv.battery if self.adv else None)
        # the log gains an entry only about once an hour, the advertised value is live: the log never overrides it
        log = {"log_percent": bat.percent, "log_raw": bat.raw, "samples": bat.samples, "log_time": time.time()}
        if (self.battery or {}).get("source") == "advertisement":
            self._store_battery({**self.battery, **log})
        else:
            self._set_battery(bat.percent, bat.raw, "log", log)

    def _set_battery(self, percent: int, raw: int, source: str, extra: dict | None = None) -> None:
        old = self.battery or {}
        if old.get("source") == source and old.get("percent") == percent and not extra:
            return  # adverts repeat the same value, persist only changes
        if percent < LOW_BATTERY <= old.get("percent", 100):
            _LOGGER.warning("%s battery low (%d%%): replace it, a weak cell makes links drop and connects fail",
                            self.address, percent)
        keep = {k: v for k, v in old.items() if k.startswith("log_") or k == "samples"}
        self._store_battery({**keep, "percent": percent, "raw": raw, "source": source, "time": time.time(), **(extra or {})})

    def _store_battery(self, battery: dict) -> None:
        self.battery = battery
        self.hass.config_entries.async_update_entry(self.entry, data={**self.entry.data, CONF_BATTERY: battery})
        for listener in list(self._battery_listeners):
            listener()

    def _set_firmware(self, firmware: int) -> None:
        self.firmware = firmware
        self.hass.config_entries.async_update_entry(self.entry, data={**self.entry.data, CONF_FIRMWARE: firmware})
        registry = dr.async_get(self.hass)
        if device := registry.async_get_device(identifiers={(DOMAIN, self.address)}):
            registry.async_update_device(device.id, sw_version=str(firmware))

    def _save_keys(self, keys: p.Keys) -> None:
        self.hass.config_entries.async_update_entry(
            self.entry, data={**self.entry.data, CONF_KEYS: keys.to_dict()}
        )

    def _dispatch(self, name: str, ev: p.RawEvent) -> None:
        self._t_last = time.monotonic()
        limit = float(self.entry.options.get(CONF_QUEUED_TIMEOUT, DEFAULT_QUEUED_TIMEOUT))
        if ev.queued and ev.age > limit:
            if ev.age > STALE_WARN and not self._stale_warned:
                self._stale_warned = True  # the oldest press of a burst comes first
                _LOGGER.warning(
                    "%s a press waited %s s for a link and is dropped (limit %s s): the proxy did not hear the button "
                    "or was busy connecting to other devices, which pauses its scanning", self.address, ev.age, limit)
            else:
                _LOGGER.debug("%s dropped stale queued event %s (age %s s > %s s)", self.address, name, ev.age, limit)
            return
        _LOGGER.info("%s -> HA event %s, %.0f ms after session ready", self.address, name,
                     (time.monotonic() - self._t_ready) * 1000)
        self.hass.bus.async_fire(
            EVENT_NAME, {"address": self.address, "type": name, "queued": ev.queued, "age": ev.age}
        )
        for listener in list(self._listeners):
            listener(name, ev)

    async def async_shutdown(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
