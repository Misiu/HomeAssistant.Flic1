"""Test the Flic Button sensor platform."""

from unittest.mock import MagicMock

from pyflic_ble import DeviceType
from pyflic_ble.const import FLIC1_BATTERY_RAW_TO_VOLTS
import pytest
from pytest_homeassistant.common import MockConfigEntry, snapshot_platform
from syrupy.assertion import SnapshotAssertion

from homeassistant.components.flic_button.const import DOMAIN
from homeassistant.components.flic_button.sensor import flic1_battery_percentage
from homeassistant.const import STATE_UNKNOWN, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from . import FLIC1_ADDRESS, setup_integration
from .conftest import TEST_FLIC1_BATTERY_RAW


@pytest.fixture
def platforms() -> list[Platform]:
    """Set up only the sensor platform."""
    return [Platform.SENSOR]


@pytest.mark.usefixtures(
    "mock_flic_client",
    "mock_no_ble_device_from_address",
    "mock_bluetooth_register_callback",
    "entity_registry_enabled_by_default",
)
@pytest.mark.parametrize("device_type", [DeviceType.FLIC1])
async def test_flic1_sensors(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test the battery sensors of a Flic 1."""
    await setup_integration(hass, mock_config_entry)

    await snapshot_platform(hass, entity_registry, snapshot, mock_config_entry.entry_id)


@pytest.mark.usefixtures(
    "mock_no_ble_device_from_address", "mock_bluetooth_register_callback"
)
@pytest.mark.parametrize("device_type", [DeviceType.FLIC1])
async def test_flic1_battery_follows_the_client(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    mock_flic_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test that the battery sensor shows what the client learns from the button."""
    await setup_integration(hass, mock_config_entry)
    entity_id = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, f"{FLIC1_ADDRESS}-battery"
    )
    assert entity_id
    assert hass.states.get(entity_id).state == "67"

    # The battery log is read in the background some time after connecting
    update_state = mock_flic_client.register_state_callback.call_args.args[0]
    mock_flic_client.state.battery_voltage = 1705 * FLIC1_BATTERY_RAW_TO_VOLTS
    update_state(mock_flic_client.state)
    assert hass.states.get(entity_id).state == "100"

    mock_flic_client.state.battery_voltage = None
    update_state(mock_flic_client.state)
    assert hass.states.get(entity_id).state == STATE_UNKNOWN


@pytest.mark.usefixtures(
    "mock_flic_client",
    "mock_no_ble_device_from_address",
    "mock_bluetooth_register_callback",
)
@pytest.mark.parametrize(
    "device_type", [DeviceType.FLIC2, DeviceType.DUO, DeviceType.TWIST]
)
async def test_no_sensors_for_other_devices(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test that only a Flic 1 gets battery sensors."""
    await setup_integration(hass, mock_config_entry)

    assert not er.async_entries_for_config_entry(
        entity_registry, mock_config_entry.entry_id
    )


@pytest.mark.parametrize(
    ("raw", "percentage"),
    [
        (1000, 0),
        (1136, 0),
        (1360, 4),
        (1584, 28),
        (TEST_FLIC1_BATTERY_RAW, 67),
        (1705, 100),
        (1800, 100),
    ],
)
def test_flic1_battery_percentage(raw: int, percentage: int) -> None:
    """Test the scale of the Flic app: 1136 is empty, 1705 is full."""
    assert flic1_battery_percentage(raw * FLIC1_BATTERY_RAW_TO_VOLTS) == percentage
