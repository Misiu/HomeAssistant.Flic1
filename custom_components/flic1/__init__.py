"""Flic 1 (first generation) buttons over Home Assistant Bluetooth, no flicd and no dongle."""

from __future__ import annotations

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth.match import BluetoothCallbackMatcher
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant

from .coordinator import Flic1Coordinator

PLATFORMS = [Platform.EVENT, Platform.SENSOR]

type Flic1ConfigEntry = ConfigEntry[Flic1Coordinator]


async def async_setup_entry(hass: HomeAssistant, entry: Flic1ConfigEntry) -> bool:
    coordinator = Flic1Coordinator(hass, entry)
    entry.runtime_data = coordinator
    address = entry.data[CONF_ADDRESS]
    if register := getattr(bluetooth, "async_register_advertisement_callback", None):
        # fires on every advertisement, also identical ones (every press looks the same)
        unsub = register(hass, coordinator.async_on_advertisement, address)
    else:
        unsub = bluetooth.async_register_callback(
            hass, coordinator.async_on_advertisement,
            BluetoothCallbackMatcher({CONF_ADDRESS: address, "connectable": True}),
            bluetooth.BluetoothScanningMode.ACTIVE,
        )
    entry.async_on_unload(unsub)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: Flic1ConfigEntry) -> bool:
    if ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.async_shutdown()
    return ok
