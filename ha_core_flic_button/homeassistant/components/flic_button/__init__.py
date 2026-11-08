"""The Flic Button integration."""

from dataclasses import dataclass
from functools import partial

from bleak import BleakError
from pyflic_ble import (
    DeviceType,
    FlicAuthenticationError,
    FlicClient,
    FlicProtocolError,
    PushTwistMode,
)

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth.match import BluetoothCallbackMatcher
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.storage import Store

from .const import (
    CONF_DEVICE_TYPE,
    CONF_PAIRING_ID,
    CONF_PAIRING_KEY,
    CONF_PUSH_TWIST_MODE,
    CONF_SERIAL_NUMBER,
    CONF_SIG_BITS,
    DOMAIN,
    POSITION_SAVE_DELAY,
    STORAGE_VERSION,
)

PLATFORMS: list[Platform] = [
    Platform.EVENT,
    Platform.NUMBER,
    Platform.SENSOR,
]


@dataclass
class FlicButtonData:
    """Runtime data for a Flic Button config entry."""

    client: FlicClient
    serial_number: str | None


type FlicButtonConfigEntry = ConfigEntry[FlicButtonData]


def _position_store(hass: HomeAssistant, entry: ConfigEntry) -> Store[dict[str, int]]:
    """Return the store with the last event position of a Flic 1."""
    return Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")


@callback
def _async_save_position(
    store: Store[dict[str, int]], pairing_id: int, pairing_key: bytes, sig_bits: int
) -> None:
    """Save the position of a Flic 1 up to which events were received.

    The pairing key does not change; the pairing id and the signature bits carry the
    event counter, the boot counter and the events received.
    """
    store.async_delay_save(
        lambda: {CONF_PAIRING_ID: pairing_id, CONF_SIG_BITS: sig_bits},
        POSITION_SAVE_DELAY,
    )


async def async_setup_entry(hass: HomeAssistant, entry: FlicButtonConfigEntry) -> None:
    """Set up Flic Button from a config entry."""

    address: str = entry.data[CONF_ADDRESS]
    ble_device = bluetooth.async_ble_device_from_address(
        hass, address.upper(), connectable=True
    )
    pairing_key = bytes.fromhex(entry.data[CONF_PAIRING_KEY])
    serial_number = entry.data.get(CONF_SERIAL_NUMBER)
    device_type = DeviceType(entry.data[CONF_DEVICE_TYPE])
    pairing_id: int = entry.data[CONF_PAIRING_ID]
    sig_bits: int = entry.data.get(CONF_SIG_BITS, 0)
    position_store = (
        _position_store(hass, entry) if device_type is DeviceType.FLIC1 else None
    )
    if position_store and (saved := await position_store.async_load()):
        pairing_id = saved[CONF_PAIRING_ID]
        sig_bits = saved[CONF_SIG_BITS]
    push_twist_mode = PushTwistMode(
        entry.options.get(CONF_PUSH_TWIST_MODE, PushTwistMode.DEFAULT)
    )

    client = FlicClient(
        address=address,
        ble_device=ble_device,
        pairing_id=pairing_id,
        pairing_key=pairing_key,
        serial_number=serial_number,
        device_type=device_type,
        sig_bits=sig_bits,
        push_twist_mode=push_twist_mode,
    )

    entry.runtime_data = FlicButtonData(
        client=client,
        serial_number=serial_number,
    )
    entry.async_on_unload(client.stop)
    if position_store:
        entry.async_on_unload(
            client.register_credentials_callback(
                partial(_async_save_position, position_store)
            )
        )

    if ble_device:
        try:
            await client.start()
        # The library raises FlicAuthenticationError only for a verify timeout
        except (
            TimeoutError,
            BleakError,
            FlicAuthenticationError,
            FlicProtocolError,
        ) as err:
            raise ConfigEntryNotReady(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"address": address},
            ) from err

    @callback
    def _async_bluetooth_callback(
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        """Handle Bluetooth updates for connection/reconnection."""
        client.set_ble_device(service_info.device, service_info.manufacturer_data)

    entry.async_on_unload(
        bluetooth.async_register_callback(
            hass,
            _async_bluetooth_callback,
            BluetoothCallbackMatcher({CONF_ADDRESS: address}),
            bluetooth.BluetoothScanningMode.ACTIVE,
        )
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Reload entry when options change (e.g. push_twist_mode)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))


async def _async_update_listener(
    hass: HomeAssistant, entry: FlicButtonConfigEntry
) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: FlicButtonConfigEntry) -> None:
    """Unload a config entry."""
    await hass.config_entries.async_unload_platforms_or_raise(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: FlicButtonConfigEntry) -> None:
    """Remove the stored event position of a Flic 1 with its config entry."""
    await _position_store(hass, entry).async_remove()
