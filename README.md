# Flic 1 for Home Assistant

Use your first-generation **Flic 1** buttons in Home Assistant over Bluetooth. You do not need the `flicd`
daemon or a USB dongle. The integration connects to the buttons through **ESPHome Bluetooth proxies**.

Status: works on real hardware (two buttons with firmware 23, ESPHome proxies, Home Assistant 2026.10).

This integration is only for Flic 1. Flic does not sell this model any more, so the integration is meant for people who
already own the buttons. For Flic 2, Flic Duo and Flic Twist use the official `flic_button` integration of Home Assistant. This project is not made or supported by Flic (Shortcut Labs).

## Important: a button works only with the proxy it was paired through

When you pair a Flic 1 through an ESPHome Bluetooth proxy, the button is paired with **that one proxy**.
Later it works only through that proxy, and only when the button is in range of it. Any other proxy cannot
connect to the button: it is refused with `GATT error 133`.

Flic confirmed the reason: a Flic 1 accepts its pairing key only from a client with the same Bluetooth address
that was used for pairing, and every proxy has its own address.

What this means for you:

- Hold the button next to the proxy you want to use while you pair it, and keep that proxy close to the button.
- More proxies do **not** give more range for a Flic 1. Roaming between proxies does not work.
- To move a button to another proxy, remove the button in Home Assistant and pair it again next to the new proxy.
- If you replace a proxy (new ESP32, so a new Bluetooth address), pair the buttons again.
- If you have several proxies, Home Assistant picks one by signal strength. When it is not the paired one,
  connecting fails and the log explains it. You can make the other proxies passive (`bluetooth_proxy: active: false`)
  so that only the paired proxy can connect.

## Requirements

- At least one ESPHome Bluetooth proxy with `bluetooth_proxy: active: true`. Passive scanners (for example
  Shelly devices) cannot hold a Bluetooth connection, so they cannot be used.
- A recent ESPHome firmware on the proxy. The integration asks the proxy for gentler connection settings
  after it connects. An old firmware cannot do this and uses more battery.
- Internet access once, while pairing (see "Pairing and the Flic cloud").

## Install

1. Copy the folder `custom_components/flic1` to `/config/custom_components/flic1` in Home Assistant.
   (With HACS you can add this repository as a custom repository of type Integration.)
2. Restart Home Assistant.
3. Optional, for logs: set `custom_components.flic1: debug` and `bleak_esphome: debug` under `logger`, `logs`.

## Pair a button

1. Go to Settings, Devices & services, Add integration, and choose **Flic 1**.
2. Hold the button for 7 seconds, next to the proxy you want to use, until its LED flashes.
3. Wait until Home Assistant has paired the button, then press it once to test.

The integration creates two entities for each button:

- an **event entity** with the events `down`, `up`, `single`, `double` and `hold`
- a **battery sensor** (diagnostic)

Every press is also fired as the bus event `flic1_event`, with the fields `address`, `type`, `queued` and `age`.

## Options

- **Seconds of silence before the button disconnects**: `511` (default) keeps the link up all the time, so a press
  reaches Home Assistant in about 0.3 to 0.5 seconds. A lower value lets the button hang up and connect again at
  the next press.
- **Link mode**: `stable` (default) uses the timings of the Flic software with a longer link timeout of 20 seconds,
  because a proxy with a weak signal cannot keep the original 5 seconds. `normal`, `low` and `high` are the original Flic
  modes. `proxy` keeps the proxy's own settings: it is the most robust, but it uses the most battery.
- **Ignore presses older than N seconds** (default 5): a press that waited for a link is delivered late. A press from
  15 seconds ago must not switch your light now.

## How it works

- A button advertises after a press. Home Assistant hears it, asks a connectable proxy to connect, and verifies the
  button with the keys from pairing. Presses that the button queued while it had no link are delivered first.
- With the default setting the link then stays up. A button that loses its link advertises again until it is connected.
- Battery: the percentage follows the voltage that the button advertises (live, 2.0 V = 0 %, 3.0 V = 100 %). The
  button's own battery log is read every 3 hours as a fallback and for the sensor attributes.
- Safety nets: failed connects back off (2 seconds, doubling up to 30 seconds). A button that advertises while the
  link looks up is checked with a read, and the link is rebuilt if the read fails.
- A proxy connects to one device at a time. Other integrations that keep retrying unreachable devices (e-paper tags,
  thermometers) can delay or block the buttons. See Troubleshooting.

## Pairing and the Flic cloud

The keys of a Flic 1 are handed out by the Flic cloud. While pairing, the integration sends the request
`POST https://api.flic.io/api/v1/buttons/<button id>/unlock` with data from the pairing handshake. You do not need
a Flic account. The reply contains the keys, which are stored in the Home Assistant config entry.
After pairing, everything works without internet.

## Troubleshooting

- Pairing ends with `button did not answer during pairing`: the button connected and the Flic cloud answered, but
  the button never replied to the last pairing packet. This was seen on a button that had failed to pair before. It
  paired at once after the battery was taken out for a few seconds. Do that, hold the button for 7 seconds and try again.
- `GATT error 133 right after the request`: the proxy that Home Assistant picked is not the one the button was paired
  through. Pair the button again next to the proxy you want, or make the other proxies passive.
- `Timeout waiting for connect response` again and again: the proxy is busy or stuck, for example because other
  integrations keep retrying connections. Restart the proxy. For a lasting fix, give the buttons their own proxy
  and pair them again next to it. A second proxy only helps if the buttons are paired through it.
- `disconnected after N s` as a warning: the link was lost. With the setting `511` the button never hangs up by itself.
- A press works only after a long time, the button flashes red, and the log shows `a press waited N s for a link`: the
  proxy stops scanning while a connection attempt is pending, so it does not hear the button. Disable the failing
  entries of other integrations or give the buttons their own proxy. The worst moment is right after a Home Assistant
  restart, when all integrations reconnect at once.
- `weak signal`: below -80 dBm links drop easily. Put the proxy closer to the button.

## Brand images

`custom_components/flic1/brand/` holds `icon`, `logo` and `dark_logo`, each also as `@2x`. Home Assistant 2026.3 and
newer shows them. Older versions show a placeholder. Home Assistant caches brand images, so after an update you may
need to restart it and refresh the browser. Sources and how to rebuild: `brand-src/SOURCES.md`.

## Tests

```
python -m unittest discover -s tests
```

The tests use only the standard library. The fake button in the tests is written from `notes/spec.md` and from real
logs. It protects against typos and known bugs, but it cannot find a mistake in the spec itself.

## Layout

- `custom_components/flic1/pyflic1/`: protocol (`protocol.py`), Bluetooth session (`client.py`), cloud call (`cloud.py`)
- `custom_components/flic1/`: Home Assistant part (config flow, coordinator, event and battery entities)
- `notes/spec.md`: description of the protocol and what was seen on real buttons
- `tools/build_brand.py`: builds the brand images

The protocol was reconstructed from the Flic Linux daemon and the Flic Android app, and checked on real buttons.
This repository contains no vendor binaries and no decompiled vendor code.

## Thanks

The protocol and the description in `notes/spec.md` come from reverse engineering: studying the official Flic
software and testing with real buttons. Many thanks to Emil from Flic for his help and for the permission to
publish this work.

## License

MIT, see `LICENSE`.
