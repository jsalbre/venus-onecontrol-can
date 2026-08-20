# Architecture

**Version:** 1.1 | **Updated:** 2026-08-19

---

## Overview

This project decodes Lippert's proprietary OneControl CAN protocol ("IDS-CAN") from a Victron Cerbo GX MK2's spare CAN interface and publishes it to Venus OS's D-Bus. It also sends safe, non-motor commands (lights, relays, water pump, water heater) back to OneControl devices. Motor control (awnings, slides, leveling jacks) is deliberately out of scope — see Safety Boundaries below.

The protocol is not documented by Lippert. This project's decode tables are derived from three independent community reverse-engineering projects (andrewcfitz/esphome-onecontrol, D-Jeffrey/UnityX-canbus, manos/OneControl-RV-C-Protocol) and must be validated against this specific coach's real traffic before being trusted for anything beyond passive logging.

---

## Design Decisions

### Stable-Key Device Discovery

The CAN protocol's 8-bit source address is not a fixed per-device identifier — it is dynamically pool-assigned by the OneControl network and can change across power cycles depending on device boot order (corroborated by decompiled Lippert firmware and an independent community tool's own design notes). Using it as a persistent config key would silently break after any breaker reset or battery disconnect.

Instead, devices are identified by `(FUNCTION_NAME, function_instance)` — falling back to `(PRODUCT_ID, instance)` when FUNCTION_NAME is unpopulated — taken from each device's periodic DEVICE_ID broadcast. A live address table maps this stable key to the device's *current* source address, continuously refreshed from DEVICE_ID broadcasts and expired after a short window. User-facing config maps stable key to friendly name and expose flag, never raw address to device.

### Bus-Outage Safety Gate

Because addresses are pool-reassigned, a cached address-table entry can look fresh (not yet past its own expiry) while actually being wrong, if the whole OneControl bus lost power and came back with devices claiming addresses in a different order. Per-key expiry alone does not catch this.

The address table separately tracks bus-wide liveness (timestamp of the most recent frame from *any* device). A gap beyond a threshold declares an outage — covering both a real OneControl power loss and this service's own restart, since the two are indistinguishable from inside the process. On outage, every stable key is marked unverified; a key only becomes command-eligible again once a *new* DEVICE_ID broadcast for it is observed after the outage was declared. The table is in-memory only and never persisted across a service restart, so trust is never inherited across a gap the process didn't itself observe. This gate fails closed: no verified mapping, no command sent, and the refusal is logged rather than silent.

### Safety Boundary: No Motor Commands

Commanding a motor (awning/slide/jack) requires holding an open session with a heartbeat while it runs; losing the session (5s timeout) means losing the ability to send STOP. The community researcher who reverse-engineered this protocol declined to implement motor control for exactly this reason. This project reads motor status (passive DEVICE_STATUS decoding) but never sends a motor COMMAND frame. This is a deliberate boundary, not an oversight — changing it requires explicit re-approval, not just removing a TODO item.

### Two Non-Unifiable Command Builders

Relay commands (lights-via-relay, pump, water heater) carry their command in the CAN ID's message-data byte with a mandatory *empty* payload; dimmable light commands carry an 8-byte payload with the message-data byte left at zero. These are kept as two separate builder functions rather than one parameterized function, specifically so the two payload shapes cannot be accidentally conflated — sending a non-empty payload on a relay command causes silent, un-NAK'd discard by the device.

### Protocol Layer Isolation

`can_link/` (frame encode/decode, device decoders, session/command logic, address table) has no `socket` or `dbus` imports. It is pure `bytes in / structured data out`, so it can be unit tested and validated against captured CAN logs entirely offline, without a live bus or Venus OS — important given how slow on-device iteration is, and how safety-relevant the command encoding is.

---

## Protocol Reference

See `dev-notes/ARCHITECTURE.md` (private) for the full byte-level protocol reference (CAN ID formulas, DEVICE_STATUS payload layouts per device type, PID table, TEA cipher constants). Summary:

- 250 kbit/s, big-endian, single-frame only. Mixed 11-bit (broadcast) and 29-bit (point-to-point) CAN IDs on one bus.
- DEVICE_ID (11-bit, type 2) broadcasts device inventory ~1Hz; DEVICE_STATUS (type 3) broadcasts current state ~1Hz idle / ~333ms on change.
- PID_READ_WRITE (29-bit REQUEST) reads values not otherwise broadcast (e.g. battery voltage); no session needed for reads.
- Commands (29-bit COMMAND) require a TEA-cipher session handshake against SESSION_ID 4.

---

## Known Limitations

- PID battery voltage reads have not been validated against this coach's real traffic yet (deferred, low priority — no PID traffic appeared in the first capture at all). Everything else in v1's decode scope (DEVICE_ID structure, relay/motor status, dimmable light status, tank sensor status, the TEA session handshake) is now confirmed against a real 2026-08-19 capture from this coach — see dev-notes/ARCHITECTURE.md for specifics.
- The stable-key fallback (PRODUCT_ID, instance) is not always unique in practice: on this coach, 13 of 31 discovered devices report `FUNCTION_NAME=0` and also share an identical `(PRODUCT_ID, instance)`, so they cannot currently be distinguished from each other by any broadcast data. These appear to be inputs never individually configured via the Lippert touchscreen (which this installation doesn't have) — see the "Future Phase — System Configuration via CAN" item in TODO.md. Devices with a real assigned FUNCTION_NAME (tanks, water pump, awning, slide, etc.) are unaffected.
- Generator, HVAC, and leveler status decoding are not implemented in v1 (undocumented byte layouts in the source research).
- Tank capacity (gallons) cannot be read from the bus reliably per prior community findings — must be configured manually if needed, not read live.
