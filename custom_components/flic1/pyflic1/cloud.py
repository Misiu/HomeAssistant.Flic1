"""The one Flic cloud call that pairing a Flic 1 needs. The internet is only used while pairing."""

from __future__ import annotations

import aiohttp

URL = "https://api.flic.io/api/v1/buttons/{uuid}/unlock"
HEADERS = {
    "User-Agent": "fliclib-linux-hci/2.0.1",
    "Accept": "application/json",
    "Content-Type": "application/json",
}


class CloudError(Exception):
    """The Flic cloud refused the request or could not be reached."""


async def unlock(session: aiohttp.ClientSession, uuid_hex: str, body: str, timeout: float = 15.0) -> str:
    """Ask the Flic cloud for the pairing keys of the button with this UUID. Returns the reply text."""
    try:
        async with session.post(
            URL.format(uuid=uuid_hex), data=body.encode(), headers=HEADERS,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise CloudError(f"HTTP {resp.status}: {text[:200]}")
            return text
    except (aiohttp.ClientError, TimeoutError) as err:
        raise CloudError(str(err)) from err
