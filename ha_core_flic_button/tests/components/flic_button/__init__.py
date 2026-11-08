"""Tests for the Flic Button integration."""

from __future__ import annotations

from pyflic_ble import DeviceType
from pyflic_ble.const import FLIC1_SERVICE_UUID, FLIC_SERVICE_UUID, TWIST_SERVICE_UUID
from pytest_homeassistant.common import MockConfigEntry
from pytest_homeassistant.components.bluetooth import (
    generate_advertisement_data,
    generate_ble_device,
)

from homeassistant.components.bluetooth.models import BluetoothServiceInfoBleak
from homeassistant.core import HomeAssistant

# Test Bluetooth addresses (one per supported device type)
FLIC1_ADDRESS = "AA:BB:CC:DD:EE:F3"
FLIC2_ADDRESS = "AA:BB:CC:DD:EE:F0"
DUO_ADDRESS = "AA:BB:CC:DD:EE:F1"
TWIST_ADDRESS = "AA:BB:CC:DD:EE:F2"

# Test serial numbers (prefix determines device type, except for a Flic 1)
FLIC1_SERIAL = "AH25-D00214"
FLIC2_SERIAL = "B12345"
DUO_SERIAL = "D12345"
TWIST_SERIAL = "T12345"

ADDRESS_FOR_DEVICE_TYPE: dict[DeviceType, str] = {
    DeviceType.FLIC1: FLIC1_ADDRESS,
    DeviceType.FLIC2: FLIC2_ADDRESS,
    DeviceType.DUO: DUO_ADDRESS,
    DeviceType.TWIST: TWIST_ADDRESS,
}

SERIAL_FOR_DEVICE_TYPE: dict[DeviceType, str] = {
    DeviceType.FLIC1: FLIC1_SERIAL,
    DeviceType.FLIC2: FLIC2_SERIAL,
    DeviceType.DUO: DUO_SERIAL,
    DeviceType.TWIST: TWIST_SERIAL,
}

MODEL_NAME_FOR_DEVICE_TYPE: dict[DeviceType, str] = {
    DeviceType.FLIC1: "Flic 1",
    DeviceType.FLIC2: "Flic 2",
    DeviceType.DUO: "Flic Duo",
    DeviceType.TWIST: "Flic Twist",
}

# Test pairing credentials
TEST_PAIRING_ID = 12345
TEST_PAIRING_KEY = bytes(16)  # 16 zero bytes
TEST_FLIC1_PAIRING_KEY = bytes(range(20))  # h1 (8 bytes) + h3 (12 bytes)
TEST_SIG_BITS = 0
TEST_BATTERY_LEVEL = 800  # Raw battery level (0-1024)
TEST_BUTTON_UUID = bytes(16)  # 16 zero bytes for testing


def _service_info(
    name: str, address: str, service_uuid: str
) -> BluetoothServiceInfoBleak:
    """Build a BluetoothServiceInfoBleak for a Flic device."""
    return BluetoothServiceInfoBleak(
        name=name,
        address=address,
        device=generate_ble_device(address=address, name=name),
        rssi=-60,
        manufacturer_data={},
        service_data={},
        service_uuids=[service_uuid],
        source="local",
        advertisement=generate_advertisement_data(
            local_name=name,
            service_uuids=[service_uuid],
        ),
        connectable=True,
        time=0,
        tx_power=-127,
    )


def create_flic1_service_info(*, pairing_mode: bool = True) -> BluetoothServiceInfoBleak:
    """Create a Flic 1 BluetoothServiceInfoBleak for testing.

    A Flic 1 that belongs to a host advertises a name that starts with a lowercase f.
    """
    return _service_info(
        "F023dJ_c" if pairing_mode else "f023dRNV", FLIC1_ADDRESS, FLIC1_SERVICE_UUID
    )


def create_flic2_service_info() -> BluetoothServiceInfoBleak:
    """Create a Flic 2 BluetoothServiceInfoBleak for testing."""
    return _service_info("Flic 2", FLIC2_ADDRESS, FLIC_SERVICE_UUID)


def create_duo_service_info() -> BluetoothServiceInfoBleak:
    """Create a Flic Duo BluetoothServiceInfoBleak for testing."""
    return _service_info("Flic Duo", DUO_ADDRESS, FLIC_SERVICE_UUID)


def create_twist_service_info() -> BluetoothServiceInfoBleak:
    """Create a Flic Twist BluetoothServiceInfoBleak for testing."""
    return _service_info("Flic Twist", TWIST_ADDRESS, TWIST_SERVICE_UUID)


def service_info_for_device_type(device_type: DeviceType) -> BluetoothServiceInfoBleak:
    """Return service info matching a device type."""
    if device_type is DeviceType.TWIST:
        return create_twist_service_info()
    if device_type is DeviceType.DUO:
        return create_duo_service_info()
    if device_type is DeviceType.FLIC1:
        return create_flic1_service_info()
    return create_flic2_service_info()


async def setup_integration(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    """Set up the Flic Button integration for tests."""
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
