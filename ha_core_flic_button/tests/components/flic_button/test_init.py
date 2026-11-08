"""Test the Flic Button integration init."""

from datetime import timedelta
from typing import Any
from unittest.mock import MagicMock

from bleak import BleakError
from freezegun.api import FrozenDateTimeFactory
from pyflic_ble import DeviceType, FlicAuthenticationError
import pytest
from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed

from homeassistant.components.flic_button.const import (
    CONF_PAIRING_ID,
    CONF_SIG_BITS,
    DOMAIN,
    POSITION_SAVE_DELAY,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from . import (
    TEST_FLIC1_PAIRING_KEY,
    TEST_PAIRING_ID,
    create_flic2_service_info,
    setup_integration,
)


async def test_setup_entry_success(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_flic_client: MagicMock,
    mock_ble_device_from_address: MagicMock,
    mock_bluetooth_register_callback: MagicMock,
) -> None:
    """Test successful setup entry."""
    await setup_integration(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.LOADED
    mock_flic_client.start.assert_called_once()


async def test_setup_entry_device_not_available(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_flic_client: MagicMock,
    mock_no_ble_device_from_address: MagicMock,
    mock_bluetooth_register_callback: MagicMock,
) -> None:
    """Test setup entry when device is not available (BLE device not found)."""
    await setup_integration(hass, mock_config_entry)

    # Entry should still load (device will connect when available)
    assert mock_config_entry.state is ConfigEntryState.LOADED
    # start() should not be called when no BLE device available
    mock_flic_client.start.assert_not_called()


@pytest.mark.parametrize(
    "side_effect",
    [
        pytest.param(BleakError("Connection failed"), id="bleak_error"),
        pytest.param(
            FlicAuthenticationError("Quick verify timeout"), id="verify_timeout"
        ),
    ],
)
async def test_setup_entry_initial_connection_fails(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_flic_client: MagicMock,
    mock_ble_device_from_address: MagicMock,
    mock_bluetooth_register_callback: MagicMock,
    side_effect: Exception,
) -> None:
    """Test setup entry retries and stops the client when connection fails."""
    mock_flic_client.start.side_effect = side_effect

    await setup_integration(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY
    mock_flic_client.stop.assert_called_once()


async def test_unload_entry(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_flic_client: MagicMock,
    mock_ble_device_from_address: MagicMock,
    mock_bluetooth_register_callback: MagicMock,
) -> None:
    """Test unloading entry."""
    await setup_integration(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.LOADED

    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED
    mock_flic_client.stop.assert_called_once()


@pytest.mark.parametrize("device_type", [DeviceType.TWIST])
async def test_setup_entry_with_twist_device(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_flic_client: MagicMock,
    mock_no_ble_device_from_address: MagicMock,
    mock_bluetooth_register_callback: MagicMock,
) -> None:
    """Test setup entry with Twist device type."""
    await setup_integration(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.LOADED


async def test_bluetooth_callback_sets_ble_device(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_flic_client: MagicMock,
    mock_no_ble_device_from_address: MagicMock,
    mock_bluetooth_register_callback: MagicMock,
) -> None:
    """Test Bluetooth callback updates BLE device on the client."""
    await setup_integration(hass, mock_config_entry)

    # The bluetooth callback is the second positional arg passed to register
    bt_callback = mock_bluetooth_register_callback.call_args[0][1]

    service_info = create_flic2_service_info()
    bt_callback(service_info, MagicMock())

    mock_flic_client.set_ble_device.assert_called_once_with(
        service_info.device, service_info.manufacturer_data
    )


@pytest.mark.parametrize("device_type", [DeviceType.FLIC1])
async def test_flic1_event_position_is_saved_and_restored(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    freezer: FrozenDateTimeFactory,
    mock_config_entry: MockConfigEntry,
    mock_flic_client: MagicMock,
    mock_flic_client_class: MagicMock,
    mock_ble_device_from_address: MagicMock,
    mock_bluetooth_register_callback: MagicMock,
) -> None:
    """Test that the event counter of a Flic 1 survives a restart."""
    await setup_integration(hass, mock_config_entry)
    assert mock_flic_client_class.call_args.kwargs["pairing_id"] == TEST_PAIRING_ID

    # The client reports the position of every press
    save = mock_flic_client.register_credentials_callback.call_args.args[0]
    save(TEST_PAIRING_ID + 1, TEST_FLIC1_PAIRING_KEY, 0b10)
    save(TEST_PAIRING_ID + 2, TEST_FLIC1_PAIRING_KEY, 0b11)
    freezer.tick(timedelta(seconds=POSITION_SAVE_DELAY + 1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert hass_storage[f"{DOMAIN}.{mock_config_entry.entry_id}"]["data"] == {
        CONF_PAIRING_ID: TEST_PAIRING_ID + 2,
        CONF_SIG_BITS: 0b11,
    }
    # The config entry stays as it is: changing it would reload the integration
    assert mock_config_entry.data[CONF_PAIRING_ID] == TEST_PAIRING_ID
    assert mock_flic_client_class.call_count == 1

    await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    kwargs = mock_flic_client_class.call_args.kwargs
    assert (kwargs["pairing_id"], kwargs["sig_bits"]) == (TEST_PAIRING_ID + 2, 0b11)


@pytest.mark.parametrize("device_type", [DeviceType.FLIC1])
async def test_flic1_event_position_is_removed_with_the_entry(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    freezer: FrozenDateTimeFactory,
    mock_config_entry: MockConfigEntry,
    mock_flic_client: MagicMock,
    mock_ble_device_from_address: MagicMock,
    mock_bluetooth_register_callback: MagicMock,
) -> None:
    """Test that the stored position does not outlive the config entry."""
    await setup_integration(hass, mock_config_entry)
    save = mock_flic_client.register_credentials_callback.call_args.args[0]
    save(TEST_PAIRING_ID + 1, TEST_FLIC1_PAIRING_KEY, 0b10)
    freezer.tick(timedelta(seconds=POSITION_SAVE_DELAY + 1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    key = f"{DOMAIN}.{mock_config_entry.entry_id}"
    assert key in hass_storage

    await hass.config_entries.async_remove(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert key not in hass_storage


@pytest.mark.parametrize("device_type", [DeviceType.FLIC2, DeviceType.TWIST])
async def test_event_position_is_only_stored_for_flic1(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_flic_client: MagicMock,
    mock_ble_device_from_address: MagicMock,
    mock_bluetooth_register_callback: MagicMock,
) -> None:
    """Test that the other devices keep their pairing data in the config entry."""
    await setup_integration(hass, mock_config_entry)

    mock_flic_client.register_credentials_callback.assert_not_called()
