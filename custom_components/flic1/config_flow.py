"""Config flow: add the integration, hold the button for 7 s, it pairs.

Pairing needs the internet once: the Flic cloud gives the keys of the button.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from functools import partial
from typing import Any

from bleak import BleakError
import voluptuous as vol
from bleak_retry_connector import (
    BleakClientWithServiceCache,
    close_stale_connections_by_address,
    establish_connection,
)
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak, async_discovered_service_info
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
)

from .const import (
    CONF_AUTO_DISCONNECT,
    CONF_FIRMWARE,
    CONF_KEYS,
    CONF_LATENCY,
    CONF_QUEUED_TIMEOUT,
    DEFAULT_AUTO_DISCONNECT,
    DEFAULT_LATENCY,
    DEFAULT_QUEUED_TIMEOUT,
    DOMAIN,
    FLIC2_UUID,
    FLIC_OUI,
    PAIR_TIMEOUT,
    PAIR_WAIT,
)
from .pyflic1 import protocol as p
from .pyflic1.client import FlicError, Flic1Session, NotPairingMode
from .pyflic1.cloud import CloudError, unlock

_LOGGER = logging.getLogger(__name__)


class NoDevice(Exception):
    """No button in pairing mode showed up in time."""


def _is_flic1(info: BluetoothServiceInfoBleak) -> bool:
    """Any of: the pairing-mode service UUID, the Flic OUI, or the GAP name "Fxxx" (firmware revision).

    The name rule comes from the Flic app: it reads the revision from a device name "F" + 3 digits and tells
    users to remove phone pairings of devices "starting with FXXX". Over-matching is fine, the hello decides.
    """
    uuids = [str(u).lower() for u in info.service_uuids]
    if FLIC2_UUID in uuids:
        return False
    return (p.PUBLIC_MODE_UUID in uuids or info.address.upper().startswith(FLIC_OUI)
            or re.fullmatch(r"[Ff]\d{3}", info.name or "") is not None)


def _error_key(exc: BaseException) -> str:
    if isinstance(exc, NoDevice):
        return "no_devices_found"
    if isinstance(exc, CloudError):
        return "cloud_error"
    if isinstance(exc, (BleakError, TimeoutError)):
        return "cannot_connect"
    return "pairing_failed"


class Flic1ConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return Flic1OptionsFlow()

    def __init__(self) -> None:
        self._task: asyncio.Task[dict[str, Any]] | None = None
        self._data: dict[str, Any] = {}
        self._error = "pairing_failed"

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._task is None:
            self._task = self.hass.async_create_task(self._async_find_and_pair(), eager_start=False)
        if not self._task.done():
            return self.async_show_progress(step_id="user", progress_action="pairing", progress_task=self._task)
        if (exc := self._task.exception()) is None:
            self._data = self._task.result()
            return self.async_show_progress_done(next_step_id="finish")
        _LOGGER.error("Flic 1 pairing failed", exc_info=exc)
        self._error = _error_key(exc)
        return self.async_show_progress_done(next_step_id="failed")

    async def async_step_failed(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_abort(reason=self._error)

    async def async_step_finish(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(self._data[CONF_ADDRESS])
        self._abort_if_unique_id_configured()
        keys = self._data[CONF_KEYS]
        return self.async_create_entry(
            title=f"Flic 1 {keys['serial'] or self._data[CONF_ADDRESS][-5:]}", data=self._data
        )

    def _candidates(self, known: set[str], skipped: dict[str, float]) -> list[BluetoothServiceInfoBleak]:
        """Flic 1 style devices that advertised in the last 5 s, not configured, not recently rejected."""
        now = time.monotonic()
        return [
            info for info in async_discovered_service_info(self.hass, connectable=True)
            if info.address not in known and skipped.get(info.address, 0) < now
            and now - info.time < 5 and _is_flic1(info)
        ]

    async def _async_find_and_pair(self) -> dict[str, Any]:
        known = self._async_current_ids(include_ignore=False)
        skipped: dict[str, float] = {}
        last_error: Exception | None = None
        logged: dict[str, str] = {}
        deadline = time.monotonic() + PAIR_WAIT
        while time.monotonic() < deadline:
            for info in self._candidates(known, skipped):
                # log what the device advertises whenever it changes: this helps with bug reports
                desc = (f"name={info.name!r} uuids={sorted(str(u) for u in info.service_uuids)} "
                        f"mfr={ {k: v.hex() for k, v in info.manufacturer_data.items()} } "
                        f"svc={ {k: v.hex() for k, v in info.service_data.items()} }")
                if logged.get(info.address) != desc:
                    logged[info.address] = desc
                    _LOGGER.info("Flic-like advertisement %s rssi=%s via %s %s", info.address, info.rssi, info.source, desc)
            if self._candidates(known, skipped):
                await asyncio.sleep(1.5)  # let other buttons and proxies report, the closest one wins
                candidates = self._candidates(known, skipped)
                if candidates:
                    info = max(candidates, key=lambda i: i.rssi)
                    _LOGGER.info("Trying %s (rssi %s) out of %d candidate(s)", info.address, info.rssi, len(candidates))
                    try:
                        return await self._async_pair(info)
                    except NotPairingMode as err:
                        # a button that is merely advertising (pressed, not held for 7 s yet): look again soon
                        _LOGGER.info("%s: %s", info.address, err)
                        skipped[info.address] = time.monotonic() + 4
                    except (BleakError, TimeoutError, FlicError, CloudError) as err:
                        # maybe not a Flic 1 at all (another 80:E4:DA device) or a bad radio moment
                        _LOGGER.warning("%s: pairing attempt failed: %r", info.address, err)
                        last_error = err
                        skipped[info.address] = time.monotonic() + 10
                    continue
            await asyncio.sleep(1)
        if last_error is not None:
            raise last_error
        nearby = sorted(async_discovered_service_info(self.hass, connectable=True), key=lambda i: i.rssi, reverse=True)[:8]
        _LOGGER.warning(
            "No Flic 1 in pairing mode seen. Strongest connectable devices: %s",
            "; ".join(f"{i.address} {i.name!r} rssi={i.rssi} uuids={[str(u) for u in i.service_uuids]}" for i in nearby),
        )
        raise NoDevice

    async def _async_pair(self, info: BluetoothServiceInfoBleak) -> dict[str, Any]:
        await close_stale_connections_by_address(info.address)
        client = await establish_connection(BleakClientWithServiceCache, info.device, info.address, max_attempts=3)
        try:
            session = Flic1Session(client, info.address)
            async with asyncio.timeout(PAIR_TIMEOUT):
                await session.open()
                keys = await session.full_verify(partial(unlock, async_get_clientsession(self.hass)))
        finally:
            with contextlib.suppress(BleakError, TimeoutError):
                await client.disconnect()
        data = {CONF_ADDRESS: info.address, CONF_KEYS: keys.to_dict()}
        if name := p.parse_adv_name(info.name):
            data[CONF_FIRMWARE] = name.firmware
        return data


class Flic1OptionsFlow(OptionsFlow):
    """Auto-disconnect seconds (511 keeps the link up) and latency mode. Applies from the next connection."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data={
                CONF_AUTO_DISCONNECT: int(user_input[CONF_AUTO_DISCONNECT]),
                CONF_LATENCY: user_input[CONF_LATENCY],
                CONF_QUEUED_TIMEOUT: int(user_input[CONF_QUEUED_TIMEOUT]),
            })
        current = self.config_entry.options.get(CONF_AUTO_DISCONNECT, DEFAULT_AUTO_DISCONNECT)
        latency = self.config_entry.options.get(CONF_LATENCY, DEFAULT_LATENCY)
        queued = self.config_entry.options.get(CONF_QUEUED_TIMEOUT, DEFAULT_QUEUED_TIMEOUT)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(CONF_AUTO_DISCONNECT, default=current): NumberSelector(
                    NumberSelectorConfig(min=0, max=511, step=1, mode=NumberSelectorMode.BOX)
                ),
                vol.Required(CONF_LATENCY, default=latency): SelectSelector(
                    SelectSelectorConfig(options=["stable", "normal", "low", "high", "proxy"], translation_key=CONF_LATENCY)
                ),
                vol.Required(CONF_QUEUED_TIMEOUT, default=queued): NumberSelector(
                    NumberSelectorConfig(min=0, max=3600, step=1, mode=NumberSelectorMode.BOX)
                ),
            }),
        )
