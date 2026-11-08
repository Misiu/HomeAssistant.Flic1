"""Test the Flic cloud access of the Flic Button integration."""

import json

import aiohttp
import pytest
from pytest_homeassistant.test_util.aiohttp import AiohttpClientMocker

from homeassistant.components.flic_button.cloud import (
    UNLOCK_URL,
    FlicCloudConnectionError,
    FlicCloudError,
    async_get_cloud_unlock,
)
from homeassistant.core import HomeAssistant

BUTTON_UUID = "00112233445566778899aabbccddeeff"
UNLOCK = UNLOCK_URL.format(button_uuid=BUTTON_UUID)
RANDOM_BYTES = bytes(range(8))
BUTTON_NONCE = bytes(range(8, 16))


async def test_unlock_sends_the_request(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test the request to the Flic cloud and that its reply is passed on as it is."""
    aioclient_mock.post(UNLOCK, text='{"mac_address": "aa:bb:cc:dd:ee:f3"}')

    unlock = async_get_cloud_unlock(hass)
    reply = await unlock(BUTTON_UUID, RANDOM_BYTES, BUTTON_NONCE)

    assert reply == '{"mac_address": "aa:bb:cc:dd:ee:f3"}'
    assert aioclient_mock.call_count == 1
    _method, _url, data, headers = aioclient_mock.mock_calls[0]
    body = json.loads(data)
    assert body["r1"] == int.from_bytes(RANDOM_BYTES, "little", signed=True)
    assert body["r2"] == int.from_bytes(BUTTON_NONCE, "little", signed=True)
    assert sorted(body) == ["platform", "r1", "r2", "version"]
    assert headers["Content-Type"] == "application/json"


@pytest.mark.parametrize("status", [401, 403, 404, 500])
async def test_unlock_refused(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, status: int
) -> None:
    """Test that any answer other than OK is reported as a cloud error."""
    aioclient_mock.post(UNLOCK, status=status)

    unlock = async_get_cloud_unlock(hass)
    with pytest.raises(FlicCloudError) as err:
        await unlock(BUTTON_UUID, RANDOM_BYTES, BUTTON_NONCE)

    assert type(err.value) is FlicCloudError
    assert str(status) in str(err.value)


@pytest.mark.parametrize("exc", [aiohttp.ClientError, TimeoutError])
async def test_unlock_unreachable(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, exc: type[Exception]
) -> None:
    """Test that a network failure is reported as such."""
    aioclient_mock.post(UNLOCK, exc=exc)

    unlock = async_get_cloud_unlock(hass)
    with pytest.raises(FlicCloudConnectionError):
        await unlock(BUTTON_UUID, RANDOM_BYTES, BUTTON_NONCE)
