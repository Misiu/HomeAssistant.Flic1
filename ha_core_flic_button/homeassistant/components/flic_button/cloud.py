"""Flic cloud access for the Flic Button integration.

A Flic 1 is unlocked by the Flic cloud once, when it is paired. Everything after
that (connecting, button events) works without the internet.
"""

from http import HTTPStatus
from typing import Final

import aiohttp
from pyflic_ble import Flic1UnlockRequest, FlicCloudUnlock

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

UNLOCK_URL: Final = "https://api.flic.io/api/v1/buttons/{button_uuid}/unlock"
UNLOCK_TIMEOUT: Final = 15


class FlicCloudError(Exception):
    """The Flic cloud refused the request."""


class FlicCloudConnectionError(FlicCloudError):
    """The Flic cloud could not be reached."""


def async_get_cloud_unlock(hass: HomeAssistant) -> FlicCloudUnlock:
    """Return the callback with which pyflic-ble asks the Flic cloud to unlock a button."""
    session = async_get_clientsession(hass)

    async def async_unlock(
        button_uuid: str, random_bytes: bytes, button_nonce: bytes
    ) -> str:
        """Ask the Flic cloud for the pairing secrets of a button."""
        body = Flic1UnlockRequest(random_bytes, button_nonce).to_json()
        try:
            async with session.post(
                UNLOCK_URL.format(button_uuid=button_uuid),
                data=body,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                timeout=aiohttp.ClientTimeout(total=UNLOCK_TIMEOUT),
            ) as response:
                text = await response.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise FlicCloudConnectionError(str(err)) from err

        if response.status != HTTPStatus.OK:
            raise FlicCloudError(f"HTTP {response.status}")
        return text

    return async_unlock
