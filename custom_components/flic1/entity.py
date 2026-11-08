"""Device info shared by the entities of one button."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo

from .const import DOMAIN
from .coordinator import Flic1Coordinator


def device_info(coordinator: Flic1Coordinator, title: str) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, coordinator.address)},
        connections={(CONNECTION_BLUETOOTH, coordinator.address)},
        manufacturer="Shortcut Labs",
        model="Flic 1",
        name=title,
        serial_number=coordinator.keys.serial or None,
        sw_version=str(coordinator.firmware) if coordinator.firmware else None,  # the app shows 23, the name has 023
    )
