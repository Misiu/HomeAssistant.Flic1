"""Battery sensor: percentage from the advertised voltage (live), falling back to the battery log (read every 3 h)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory, PERCENTAGE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import Flic1ConfigEntry
from .coordinator import Flic1Coordinator
from .entity import device_info
from .pyflic1 import protocol as p


async def async_setup_entry(
    hass: HomeAssistant, entry: Flic1ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([Flic1Battery(entry.runtime_data, entry.title)])


class Flic1Battery(SensorEntity):
    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: Flic1Coordinator, title: str) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{coordinator.address}_battery"
        self._attr_device_info = device_info(coordinator, title)

    @property
    def native_value(self) -> int | None:
        return (self._coordinator.battery or {}).get("percent")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        bat = self._coordinator.battery or {}
        return {
            "raw": bat.get("raw"),
            "voltage_estimate": round(p.battery_volts(bat["raw"]), 2) if bat.get("raw") else None,
            "source": bat.get("source"),
            "read_at": datetime.fromtimestamp(bat["time"], UTC).isoformat() if bat.get("time") else None,
            "log_percent": bat.get("log_percent"),  # the log lags: it gains an entry about once an hour
            "log_raw": bat.get("log_raw"),
            "log_entries": bat.get("samples"),
        }

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._coordinator.async_add_battery_listener(self._refresh))

    @callback
    def _refresh(self) -> None:
        self.async_write_ha_state()
