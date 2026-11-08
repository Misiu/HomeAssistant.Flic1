"""Sensor platform for the Flic Button integration."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import override

from pyflic_ble import DeviceType
from pyflic_ble.const import FLIC1_BATTERY_RAW_TO_VOLTS

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfElectricPotential
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import FlicButtonConfigEntry, FlicButtonData
from .entity import FlicButtonEntity

PARALLEL_UPDATES = 0


def flic1_battery_percentage(voltage: float) -> int:
    """Return the charge of a Flic 1 in percent, on the scale of the Flic app.

    The button reports its battery as a number (1136 is 2.0 V, 1705 is 3.0 V); the
    app maps it piecewise: it is steep near full and flat near empty.
    """
    raw = round(voltage / FLIC1_BATTERY_RAW_TO_VOLTS)
    if raw > 1705:
        return 100
    if raw > 1584:
        return ((((raw - 1584) << 16) // 121 * 72) >> 16) + 28
    if raw > 1360:
        return ((((raw - 1360) << 16) // 224 * 24) >> 16) + 4
    if raw > 1136:
        return (((raw - 1136) << 16) // 224 * 4) >> 16
    return 0


@dataclass(frozen=True, kw_only=True)
class FlicSensorEntityDescription(SensorEntityDescription):
    """Describes a Flic sensor, computed from the battery voltage."""

    value_fn: Callable[[float], float | int]


SENSOR_DESCRIPTIONS: tuple[FlicSensorEntityDescription, ...] = (
    FlicSensorEntityDescription(
        key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=flic1_battery_percentage,
    ),
    FlicSensorEntityDescription(
        key="battery_voltage",
        translation_key="battery_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
        value_fn=lambda voltage: voltage,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FlicButtonConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Flic Button sensors."""
    data = entry.runtime_data

    # Only a Flic 1 keeps its battery value current: the library reads the battery
    # log of the button in the background
    if data.client.device_type is not DeviceType.FLIC1:
        return

    async_add_entities(
        FlicButtonSensorEntity(data, description) for description in SENSOR_DESCRIPTIONS
    )


class FlicButtonSensorEntity(FlicButtonEntity, SensorEntity):
    """Representation of a Flic button sensor."""

    entity_description: FlicSensorEntityDescription

    def __init__(
        self, data: FlicButtonData, description: FlicSensorEntityDescription
    ) -> None:
        """Initialize the sensor."""
        super().__init__(data)
        self.entity_description = description
        self._attr_unique_id = f"{self._client.address}-{description.key}"

    @property
    @override
    def native_value(self) -> float | int | None:
        """Return the value of the sensor."""
        voltage = self._client.state.battery_voltage
        if voltage is None:
            return None
        return self.entity_description.value_fn(voltage)
