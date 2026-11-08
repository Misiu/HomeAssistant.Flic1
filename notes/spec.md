# Flic 1 Bluetooth protocol

This is what the integration implements. It was reconstructed by reading the official Flic software (the Linux
daemon `flicd`, the iOS library and the Flic Android app 4.0.17) and checked on real buttons (two Flic 1,
firmware 23, ESPHome Bluetooth proxies). Only facts are recorded here, no vendor code.

Tags: **[seen]** seen on a real button. **[sw]** read in the official Flic software, not yet seen on a button.
**[?]** unknown.

All numbers are little endian. Hex bytes are written as `0x..`.

## 1. Transport

- **Security.** There is no Bluetooth link encryption. All security is in the application layer: SHA-256 MACs
  with a 12-byte secret called `h3`. No Bluetooth bonding is needed on firmware 23. [seen]
- **GATT.** [seen] The table is exactly what the Linux daemon expects. It uses fixed handles and no discovery:

  | handle | UUID | use |
  |---|---|---|
  | service | `f02adfc0-26e7-11e4-9edc-0002a5d5c51b` | the Flic 1 service, also in the advertisement |
  | 0x12 | `cc7efce0-26e8-11e4-8fd2-0002a5d5c51b` | verify and config: read (hello), write, write without response, notify |
  | 0x13 | CCCD of 0x12 | enable notifications (`01 00`) |
  | 0x15 | `06053ec0-26e9-11e4-adc2-0002a5d5c51b` | button events: read, notify |
  | 0x16 | CCCD of 0x15 | enable notifications (`01 00`) |

  The fixed handles work through bleak and ESPHome proxies. The MTU stays 23.
- **Writes.** The Android app writes without response. The exception is the event acknowledgement (op `0x25`),
  which it writes with response. The Linux daemon uses a write with response for buttons whose address is in
  `80:E4:DA:70:00:00` to `80:E4:DA:70:72:54` (probably an early batch with a firmware quirk). [sw]
- **Bonding.** The Android app asks for a Bluetooth bond only for the hello flag `0x43`, and before the quick
  verify on firmware 21 or newer if the button is not bonded (it does not check the result). Newer firmware also
  has an encrypted HID mode. We do not know if bonding is ever required for the plain protocol. On firmware 23
  the flag `0x43` never showed up. [sw]
- The address of the host is never part of a message, a MAC or a key. [sw]

## 2. Advertising

- **Address.** The vendor prefix (OUI) is `80:E4:DA`.
- **Local name.** `F` or `f`, then the firmware revision as 3 digits, then 4 characters: the last 3 bytes of the button
  address as base64url. Example: the last bytes `12 34 56` give `f023EjRW`. The Flic app ignores an advertisement whose
  name does not match the address. [seen]
  - Lowercase `f`: private mode. The button only talks to hosts it knows.
  - Uppercase `F`: public mode. A button that waits for pairing (hold it for 7 seconds) uses it. A paired button was
    also seen in public mode, with the hello flag `0x41`.
- **Service UUID.** The service UUID is in the advertisement. [seen]
- **Manufacturer data** with company id `0x030F` (783), 16 bytes, in the scan response (firmware 12 or newer).
  It can be missing in the first advertisements after a restart. [seen]

  | bytes | field |
  |---|---|
  | 0 | always 0 |
  | 1 | advertisement index |
  | 2..3 | battery value at boot |
  | 4..7 | time, in ticks of 0.625 ms |
  | 8..9 | current battery value (see section 8) |
  | 10..12 | boot counter |
  | 13..15 | the address prefix, reversed (`da e4 80`) |

  Example: `0003ec05af0400002506020000dae480`.
- **When it advertises.** A button advertises after a press. After a lost link it keeps advertising until a host
  connects again. Normally it does not advertise while a host is connected. [seen]

## 3. Hello

The host reads handle 0x12 after connecting. The answer is 20 bytes: [seen]

```
[0]      flag   0x40 '@'  quick verify only (private mode)
                0x41 'A'  quick verify if the button knows the host and the MAC fits, otherwise full verify
                0x42 'B'  full verify (pairing)
                0x43      pairing mode that asks for a Bluetooth bond (Flic app only, not seen on firmware 23)
[1..8]   nonce N of the button
[9..16]  unknown; bytes 9..13 are a prefix of the id of the button's config pack
[17..19] MAC3 = SHA256(hello[0..16] || h3)[0:3]
```

- Older firmware puts the MAC over bytes 0..13 at 14..16. The Flic app accepts both forms, the Linux daemon only the
  first. We accept both. [sw]
- Firmware older than revision 4 uses the flags `0x04` to `0x07`. [sw]
- An unpaired button in pairing mode sends `0x42` and 19 zero bytes: no nonce and no MAC. [seen]

## 4. Packets from the host to the button

```
packet = op(1) | payload(n) | counter_low(1) | mac(4)               n + 6 bytes, written to handle 0x12
mac    = SHA256(op | payload | counter_u32 | N(8) | h3(12))[0:4]
```

`N` is the nonce of the button. The counter starts at 0 on every new connection and grows by 1 with each packet.

## 5. Quick verify (paired button)

1. Check the hello MAC with the stored `h3`. Take `N` from the hello. Choose 6 random bytes `R6`.
2. Send these packets (section 4), one after the other:
   - op `0x09`: `h1(8) | R6(6)`
   - op `0x25` (acknowledgement): `counter_u32 | boot_u32 | mask_u8`, the last accepted event position
   - op `0x06` (config): `00 * 6 | u16(auto_disconnect_seconds & 0x1FF)`. The value 511 means never disconnect.
3. The button answers on handle 0x15 with 15 bytes: `0x40 | SHA256(N | R6 | h3)[0:14]`. This is the "ready" frame.
   Queued events may follow at once.

[seen] The ready frame arrives 25 to 75 ms after the packets. From the advertisement to ready it takes 0.3 to 0.5 s
with a good signal. With a very weak signal (-103 dBm) one connect took 9 s.

The Android app waits 10 seconds for the ready frame.

## 6. Events

Notifications on handle 0x15 are 15 bytes:

```
data = ev[0..10]     mac = ev[11..14] = SHA256(data | h3)[0:4]     (wrong MAC: ignore the frame)
b       = data[0]
counter = u24(data[1..3])      boot = u24(data[4..6])      age = u32(data[7..10])
queued  = bit 4 of b
```

- `age` is in seconds. A press that was queued 12 minutes earlier came with age 725. [seen]
- The frames `0x20`, `0x60`, `0x68` and `0x70` are status frames, never presses. `0x60` and `0x70` carry connection
  parameters. Their MAC never matches, so they must be dropped before the MAC check. The Linux daemon ignores `0x20`, `0x60`
  and `0x68`, the Android app also `0x70`. [seen]
- **Replay filter.** The host stores boot `sb`, counter `sc` and a mask `sm`. With `k = b & 3` (or `k = 0` if bit 3 is set):

  ```
  sb < boot   accept, sm = 0              sb > boot   replay, drop
  same boot:  sc < counter   accept, sm = 0
              sc > counter   drop
              sc = counter   drop if sm has bit k, otherwise accept
  accept: sb = boot, sc = counter, sm |= 1 << k, and store the position
  ```
- **Classification.** With `t = b & 3`. If bit 3 is clear the kind is `t` and `p8 = ((b & 0xF) == 7)`. If bit 3 is set
  the kind is 0 and `p5 = bit 2`, `p6 = (t == 2)`, `p7 = (t == 3)`. The result is a list of (opcode, click) pairs, the
  same as the Linux daemon sends to its clients: opcode 4 = up/down, 5 = click/hold, 6 = single/double, 7 =
  single/double/hold.

  ```
  kind 1:  (4, down)
  kind 2:  (6, single) (7, single)
  kind 3:  (5, hold), and (7, hold) unless p8
  kind 0:  (4, up); if not p5: (5, click) and, if p6, (7, single) (6, single); if p5 and p6: (6, single)
           if p7: (6, double) (7, double)
  ```

  The integration uses `down` and `up` from opcode 4 and `single`, `double`, `hold` from opcode 7.
- **Meaning of the bits** (a guess that fits everything seen): bit 4 = queued, bit 3 = release that was classified,
  bit 2 = held for more than 1 second, `t` = 2 single click or 3 double click.
- **Acknowledgement.** After an accepted event, send op `0x25` (as in section 5) if the kind is 2, or if bit 3 is set and
  `t` is 2 or 3. Without it the button sends the event again. The Android app only does this for firmware 19 or newer. [seen]
- **Timing.** [seen] `down` and `up` arrive at once. `single` arrives 0.2 to 0.4 s after the release (the double-click
  window). A double click is `up` and `double`. `hold` comes with the byte `0x13`. The bytes `0x11`, `0x12`, `0x13`,
  `0x18`, `0x1b` and `0x1e` were seen, and the table above matched every one. Queued events come in a burst right
  after the ready frame.

## 7. Pairing (full verify)

The button must be in pairing mode (hello flag `0x42`, or `0x41` for a button without keys).

```
host   -> button  write 0x12:   07 00 R(8)                     R = random bytes of the host
button -> host    notify 0x12:  00 | A(8) | H2b(8) | ...       A = nonce of the button, H2b = its h2   (20 bytes)
button -> host    notify 0x12:  01 | uuid(16) | ...            (20 bytes)
host   -> cloud   POST https://api.flic.io/api/v1/buttons/<uuid, lowercase hex>/unlock
cloud  -> host    mac_address, h1, h2, h3, r3, color, factory_serial, button_curve_key, ..._signature
host checks       mac_address is the address of the button, h2 starts with H2b
host   -> button  write 0x12:   07 01 r3(8) h1(8)               18 bytes, no MAC
button -> host    notify 0x12:  02 ...                          paired
```

After `02` the host stores `h1` (8 bytes), `h3` (12 bytes), counter 0 and boot 0, uses `A` as the nonce, and sends the
packets op `0x25` and op `0x06` of section 5.

**Cloud request.** [seen]

```
POST /api/v1/buttons/<uuid>/unlock        Host: api.flic.io
Content-Type: application/json            Accept: application/json
{"r1":<int64>,"r2":<int64>,"platform":"linux","version":"0.2"}
```

`r1` is `R` and `r2` is `A`, each read as a signed 64-bit integer. The answer is HTTP 200 with JSON. `h1`, `h2` and `h3`
are hex strings that are **longer** than the protocol needs. Use the first 8, 8 and 12 bytes. `r3` is a decimal number.
HTTP 404 means the cloud does not know the button.

The request is anonymous: there is no account and no key. [seen] The cloud answers in about 0.5 s and the button accepts
the keys.

In one run two pairing attempts in a row failed: the button was in public mode (hello flag `0x41`), the cloud reply was
fine, but the button never answered the `07 01` packet and the host gave up after 25 s. After the battery was taken out for
a few seconds, the next attempt paired at once. The cause is not known. A stuck state of the button after an earlier failed
attempt is the likely one. [seen once]

The cloud does not lock a button to an account: a button that was used with a Flic app account could still be paired. [seen]
The internet is only needed for this request. The Linux daemon allows 28 seconds for the whole exchange and 10 seconds
after the hello.

## 8. Battery

**Log.** Host and button exchange, with packets from section 4: [seen]

1. The host sends op `0x18` (empty). The button answers on handle 0x12 with `05 | N u16 | t u32 | M u32`. `N` is the
   number of log entries, and the button can send the last `M`.
2. The host sends op `0x19` with `pos u32`, starting at `pos = max(0, N - M)`.
3. The button sends frames `06 | 19 bytes`, 285 bytes per request. The host asks again at each 285 bytes until
   `N - pos` entries have arrived.

An entry is one byte. The value is `v = 1100 + 3 * entry`. A 0 entry means "no data" and separates two batteries: older
entries belong to a cell that was replaced. A log that ends with such a 0 has no value yet for the new cell.
The log gains an entry only about once an hour, so it lags after a battery change.

**Percentage.** This is the scale of the Flic app. `v` is the mean of the newest (up to 8) non-zero entries, and the scan
stops at a 0 entry:

| v | percent |
|---|---|
| 1705 or more | 100 |
| 1584 to 1705 | 28 + 72 * (v - 1584) / 121 |
| 1360 to 1584 | 4 + 24 * (v - 1360) / 224 |
| 1136 to 1360 | 4 * (v - 1136) / 224 |
| 1136 or less | 0 |

**Voltage.** The ends of the scale, 1136 and 1705, are 2.0 V and 3.0 V at 3.6 V / 2048 per count (a CR2032 cell is the
likely type). This is an estimate from that match, not a documented unit. A direct read (op `0x14`, answer op 20) uses
3.6 V / 1024 on a 10-bit value. [sw]

**Advertised value.** The `current battery` field of the manufacturer data (section 2) is live and uses the same scale
as the log: one button advertised 1586 while its log entry was 1556, written in the same minute. After a battery change
the advertised value was right at once and the log was not. So the integration uses the advertised value and keeps the
log as a fallback. [seen]

The Linux daemon reads the log 60 seconds after connecting and then every 3 hours. The Android app waits 30 seconds, then
reads every 3 hours, and warns below 15 %.

## 9. Connection parameters and modes

- A phone or the Linux daemon keeps a pending connection (`connectGatt` with `autoConnect`, or a controller white list),
  so the connection starts when the button advertises. An ESPHome proxy cannot do that. It connects directly after Home
  Assistant has heard an advertisement.
- **Auto-disconnect** is the number of seconds after the last press until the button may hang up. 511 means never. The
  Flic app uses 511 for a new button, and 70 seconds in "Passive mode". Older app versions used 10 seconds.
- **Parameters.** ESPHome proxies connect with an interval of about 10 ms and latency 0. The button would wake about 100
  times more often than with the Linux daemon. The integration therefore asks the proxy to change the parameters right
  after the verify. Values are (minimum interval, maximum interval in 1.25 ms, slave latency, supervision timeout in 10 ms):

  | mode | values | idle wake-up |
  |---|---|---|
  | Linux daemon Normal | 70, 80, 19, 501 | about 2.0 s |
  | Linux daemon Low | 13, 14, 79, 421 | about 1.4 s |
  | Linux daemon High | 200, 220, 7, 661 | about 2.2 s |
  | `stable` (ours, the default) | 70, 80, 19, 2000 | about 2.0 s |
  | `proxy` | none: the proxy keeps about 10 ms and latency 0 | |

  `stable` is the Normal mode with a link timeout of 20 s instead of 5 s. Every Flic mode has a timeout of only 2 or 3
  idle wake-ups. That is fine for a phone next to the button, but a proxy with a weak signal (-80 dBm) drops the link
  after two lost packets. Drops after 25 to 35 s and after 135 s were seen. With `stable` no idle drop was seen in the
  later tests. [seen]
  The Flic app also uses (100, 110, 29, 1000), (20, 24, 39, 300), (33, 34, 0, 400) and (224, 230, 13, 806). For every set
  the timeout is more than 2 * (1 + latency) * maximum interval, which is the Bluetooth rule.
- Setting the parameters from the proxy needs an ESPHome firmware with that feature (`set_connection_params` in
  bleak-esphome). It was accepted by the proxy that was tested. [seen]
- The Flic app and the iOS library cannot change the parameters as a central device. They put them in the config packet
  (op `0x02` or op `0x06`) and the button asks the central to switch. We do not do that.
- The button has no pending-connection mode for proxies, so after a lost link it only comes back when it advertises.
  It does, continuously, which costs battery.

## 10. Commands from the host (Flic app, 18 ops)

| op | meaning |
|---|---|
| 0x02 | set the connection parameters now (6 bytes) |
| 0x04 | config pack: 3 pairs of (4 bit, 12 bit) plus flags. App default `A0 00 E6 01 FC FF 01 02 5A 00 5F 00 01` |
| 0x05 | no payload, sent when handling a disconnect |
| 0x06 | config: 6 bytes of parameters, `u16` auto-disconnect (9 bits), flags `0x02`, `0x10`, `0x20`. The app sends 511 with these flags. We send zero parameters, like the Linux daemon |
| 0x07 | full verify steps (section 7) |
| 0x08 | firmware version request. Firmware 23 does not answer [seen] |
| 0x09 | quick verify |
| 0x0D | sent after a Bluetooth bond exists |
| 0x0F, 0x10, 0x11 | firmware update: start (length), data (14 bytes), end |
| 0x13 | one byte, sent once after the verify |
| 0x14 | read the battery voltage now (answer op 20) |
| 0x15 | store the name or appearance blob (3 times 11 bytes) |
| 0x18, 0x19 | battery log (section 8) |
| 0x1F | HID and bond mode flags |
| 0x24 | no payload, sent while handling state |
| 0x25 | event acknowledgement (section 6) |

Packets from the button that the app knows: op 14 info, 16 and 17 name, 20 battery level, 33 timing and press counters,
5, 6, 18 and 19 firmware update.

## 11. Firmware

- The firmware revision is the number in the 3 digits of the advertised name (`f023...` is revision 23). The button has
  no command to report it. Revision 23 is the newest known one and dates from 2017.
- The update image is encrypted (flat byte histogram, no repeated blocks). The key is in the bootloader of the button.
  So the firmware itself was not analyzed, and what is written here about the button comes from the host side only.

## 12. A button works only with the proxy it was paired through

[seen] A button in private mode refuses every connection that does not come from the Bluetooth address it was paired
with. The error is `ESP_GATT_ERROR` (133) about 0.3 s after the request. It happens even at -49 dBm, while the proxy
that was used for pairing connects the same button without problems.

- Two buttons were paired through proxy A. Proxy B always got error 133. After the buttons were removed and paired again
  through proxy B, proxy B connected at once (88 to 93 ms). Whether proxy A still worked after that was not tested.
- Flic confirmed the reason: a Flic 1 accepts its pairing key only from a client that has the same Bluetooth address as
  during pairing. A Flic 2 is different: its pairing key can be shared with another device, which then can connect too.
  Flic sees no way to change this in the Flic 1 firmware, and it does not plan to maintain the Flic 1.
- So a Flic 1 does not roam between proxies. To use another proxy the button has to be paired again next to it.
- A public-mode button (uppercase `F`, hello flag `0x41`) may accept other proxies, because there is no filter at that
  moment. Do not rely on it.
- [?] Whether a proxy could be given the address of another proxy (not tried).

## 13. Proxy behavior that matters

- **One connection at a time.** [seen] The ESPHome tracker moves one discovered client to "connecting" at a time and stops
  scanning until it is done. Bluedroid allows one outstanding create-connection request, and a disconnect request from
  the host does not cancel an attempt that is already running. So every useless connect (for example to an e-paper tag
  that is out of range) blocks the proxy for up to the stack timeout of about 20 s. Five such tags that retry every 20 s
  keep a proxy busy almost all the time.
- **Effect on the buttons.** While the proxy tries to connect to other devices it does not scan, so it does not hear the
  button. A press is not lost: the button queues it, flashes red, and the press arrives later. That is why presses older
  than 5 seconds are dropped by default. In one run a press was 98 seconds old. The worst moment is just after a Home
  Assistant restart. Quiet times gave 94 to 760 ms from the advertisement to the link.
- **Remedies**, in this order: disable the entries of other integrations that fail all the time; give the buttons their
  own proxy and pair them again next to it; later, maybe a dedicated ESPHome component on its own ESP32.
- **Weak signal.** Below -80 dBm the link drops easily. A single connect also failed once with "interference/range".
- **Retries.** A button that lost its link advertises until it is connected, so every advertisement started a new attempt
  (127 failed connects in 3 minutes). The integration now waits 2 s, doubling up to 30 s, and resets after a verified
  session.
- **Silent link loss.** A proxy with a hung Bluetooth stack may never report that the link is gone. A connectable
  advertisement 10 s after the ready frame now triggers a GATT read (8 s timeout, at most every 30 s). A dead link is dropped
  and built again.
- **Competing phone.** The Flic phone app keeps a pending connection for a button it knows and may win the race for the
  first advertisement. The log would look the same as a failed connect. Not proven; test with Bluetooth off on the phone.

## 14. Differences from the official clients

| point | official clients | this integration |
|---|---|---|
| config op 6 | Linux daemon: zero parameters. App and iOS: parameters and flags | Linux daemon form; the parameters are set from the proxy |
| connection | pending connection with automatic reconnect | connect after an advertisement, retry with back-off |
| battery | log 30 or 60 s after connecting, then every 3 h | log 30 s after connecting, then every 3 h; the advertised value is live |
| firmware check | app checks the Flic cloud every 6 h | none |
| bonding | only for flag `0x43` | not needed on firmware 23 |

## 15. Open questions

- How many Bluetooth addresses a private button remembers, and whether a Flic 1 can be paired to two proxies. (Flic
  said it is the address used during pairing, which suggests one.)
- The size of the event queue on the button, the advertising time after a press, and what the flags `0x02`, `0x10`, `0x20`,
  `0x40` of op `0x06` and the config pack change.
- Hello bytes 9 to 16 and the hash that gives the config pack id.
- Whether bonding is needed on firmware newer than 23 (none exists) or with the flag `0x43`.
- The battery life with the different link modes. There is no instrument for it yet.
