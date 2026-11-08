"""Event entity: down, up, single, double, hold."""

from __future__ import annotations

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import Flic1ConfigEntry
from .coordinator import Flic1Coordinator
from .entity import device_info
from .pyflic1 import protocol as p


async def async_setup_entry(
    hass: HomeAssistant, entry: Flic1ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([Flic1Button(entry.runtime_data, entry.title)])


class Flic1Button(EventEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "button"
    _attr_device_class = EventDeviceClass.BUTTON
    _attr_event_types = ["down", "up", "single", "double", "hold"]

    def __init__(self, coordinator: Flic1Coordinator, title: str) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{coordinator.address}_button"
        self._attr_device_info = device_info(coordinator, title)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._coordinator.async_add_listener(self._on_event))

    @callback
    def _on_event(self, name: str, ev: p.RawEvent) -> None:
        self._trigger_event(name, {"queued": ev.queued, "age": ev.age})
        self.async_write_ha_state()
