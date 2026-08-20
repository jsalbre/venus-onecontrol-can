# Architecture

**Version:** 1.4 | **Updated:** 2026-08-20

---

## Overview

This project decodes Lippert's proprietary OneControl CAN protocol ("IDS-CAN") from a Victron Cerbo GX MK2's spare CAN interface and publishes it to Venus OS's D-Bus. It also sends safe, non-motor commands (lights, relays, water pump, water heater) back to OneControl devices. Motor control (awnings, slides, leveling jacks) is deliberately out of scope — see Safety Boundaries below.

The protocol is not documented by Lippert. This project's decode tables are derived from three independent community reverse-engineering projects (andrewcfitz/esphome-onecontrol, D-Jeffrey/UnityX-canbus, manos/OneControl-RV-C-Protocol) and must be validated against this specific coach's real traffic before being trusted for anything beyond passive logging.

---

## Design Decisions

### Stable-Key Device Discovery

The CAN protocol's 8-bit source address is not a fixed per-device identifier — it is dynamically pool-assigned by the OneControl network and can change across power cycles depending on device boot order (corroborated by decompiled Lippert firmware and an independent community tool's own design notes). Using it as a persistent config key would silently break after any breaker reset or battery disconnect.

Instead, devices are identified by `(FUNCTION_NAME, function_instance)` — falling back to `(PRODUCT_ID, instance)` when FUNCTION_NAME is unpopulated — taken from each device's periodic DEVICE_ID broadcast. A live address table maps this stable key to the device's *current* source address, continuously refreshed from DEVICE_ID broadcasts and expired after a short window. User-facing config maps stable key to friendly name and expose flag, never raw address to device.

**This design is independently confirmed correct by Lippert's own gateway code** (2026-08-20, decompiled from the LippertConnect Android app): `DeviceInstanceManager.GetAvailableDeviceInstanceClaim` re-associates a reconnecting device by matching its `FunctionKey` (FUNCTION_NAME + FunctionInstance) *before* falling back to anything address-based — i.e. Lippert's own software uses this exact strategy to survive CAN address churn, not a scheme this project invented independently. FUNCTION_NAME/FunctionInstance are backed by dedicated PIDs (4 and 5) that a device broadcasts from its own stored configuration, not renegotiated per boot.

One documented, deliberately-unhandled edge case: FUNCTION_NAME/instance are stable but not strictly immutable. A rename feature exists in the app that writes PIDs 4/5 to move a device to a different `(FUNCTION_NAME, instance)` pair. This is a deliberate, rare user action (via OneControl commissioning), not something that happens spontaneously on boot/reconnect — the project does not defend against it (e.g. by detecting a stable key's DeviceType suddenly changing), since the touchscreen/commissioning flow that would trigger it isn't part of this installation.

### Human-Readable Device Names Are Never On The Wire

Only the numeric FUNCTION_NAME code is broadcast — never a name string. The LippertConnect app shows names like "Kitchen Island Light" or "Scare Light" by resolving the numeric code against a **446-entry static lookup table compiled into the app itself** (confirmed by decompiling it: `FUNCTION_NAME(38, "Kitchen Island Light", ICON.LIGHT)`, etc., values 0–445, no gaps). There is no cloud lookup and no per-floorplan config file involved — every coach's app ships the same fixed table, and Lippert's own display logic is simply `f"{FUNCTION_NAME.Name} {FunctionInstance}"` when the instance is nonzero (e.g. two FUNCTION_NAME=105 "Awning" devices with instances 1/2 display as "Awning 1"/"Awning 2").

This project embeds that same table (`can_link/types.py::FUNCTION_NAMES`, `function_name_label()`), so friendly names shown to the user come from the same source Lippert's own app uses — not guesses. `config.example.json`'s device names are the exact vendor strings for this reason.

### Bus-Outage Safety Gate

Because addresses are pool-reassigned, a cached address-table entry can look fresh (not yet past its own expiry) while actually being wrong, if the whole OneControl bus lost power and came back with devices claiming addresses in a different order. Per-key expiry alone does not catch this.

The address table separately tracks bus-wide liveness (timestamp of the most recent frame from *any* device). A gap beyond a threshold declares an outage — covering both a real OneControl power loss and this service's own restart, since the two are indistinguishable from inside the process. On outage, every stable key is marked unverified; a key only becomes command-eligible again once a *new* DEVICE_ID broadcast for it is observed after the outage was declared. The table is in-memory only and never persisted across a service restart, so trust is never inherited across a gap the process didn't itself observe. This gate fails closed: no verified mapping, no command sent, and the refusal is logged rather than silent.

### Safety Boundary: No Motor Commands

Commanding a motor (awning/slide/jack) requires holding an open session with a heartbeat while it runs; losing the session (5s timeout) means losing the ability to send STOP. The community researcher who reverse-engineered this protocol declined to implement motor control for exactly this reason. This project reads motor status (passive DEVICE_STATUS decoding) but never sends a motor COMMAND frame. This is a deliberate boundary, not an oversight — changing it requires explicit re-approval, not just removing a TODO item.

### Two Non-Unifiable Command Builders

Relay commands (lights-via-relay, pump, water heater) carry their command in the CAN ID's message-data byte with a mandatory *empty* payload; dimmable light commands carry an 8-byte payload with the message-data byte left at zero. These are kept as two separate builder functions rather than one parameterized function, specifically so the two payload shapes cannot be accidentally conflated — sending a non-empty payload on a relay command causes silent, un-NAK'd discard by the device.

### Protocol Layer Isolation

`can_link/` (frame encode/decode, device decoders, session/command logic, address table) has no `socket` or `dbus` imports. It is pure `bytes in / structured data out`, so it can be unit tested and validated against captured CAN logs entirely offline, without a live bus or Venus OS — important given how slow on-device iteration is, and how safety-relevant the command encoding is.

### Config-Gated D-Bus Exposure (Phase 2)

A device present on the bus is never enough, by itself, to get a D-Bus service. Exposure requires an explicit `devices[]` entry in config with `expose: true` — this is checked by `ConfigManager.is_exposed()`, the single source of truth for whether a device is allowed to be published at all. Devices seen on the bus but not configured (or configured with `expose: false`) are recorded to a discovery log (`discovered_devices.json`, mirrors `discovered_sensors.json` from the govee-ble-venus-py reference project) purely for the user's review — being in that file never causes exposure.

A second, independent check runs alongside the first: `device_mapping.validate_device_class()` cross-checks the config's declared `device_class` (e.g. `"relay_light"`) against the DeviceType the device is *actually* broadcasting right now. A config entry that's stale, copy-pasted from another device, or simply wrong is refused rather than trusted — this is the same "config declaration + live cross-check" pattern already established for the command safety gate in `address_table.py`, applied here to service creation. Both checks are pulled into `dbus_bridge/routing.py`, a pure module with no `dbus`/`gi` imports, specifically so this decision logic — unlike the rest of `dbus_bridge/` — can be unit tested without D-Bus.

### Device Class Is Inferred, Never Asked

Enabling a discovered device (`enable-device`) never prompts for `device_class`. It's fully determined by what the device itself already broadcasts — `device_mapping.infer_device_class()` maps DEVICE_TYPE to `tank`/`dimmable_light`/`motor_status` unambiguously, and for the remaining relay-family DEVICE_TYPEs (which cover lights, pumps, and water heaters identically at the protocol level), sub-classifies using the device's FUNCTION_NAME semantics (e.g. FUNCTION_NAME 5 = "Water Pump" → `relay_pump`; 3/4 = "Gas/Electric Water Heater" → `relay_water_heater`; anything else defaults to `relay_light`). This mirrors how a human would work it out by reading the discovery log, and removing the prompt was an explicit user request after noticing the initial config was populated this way without ever being asked.

The inference is deliberately narrow where precision matters for safety: motor DEVICE_TYPEs always resolve to `motor_status`, full stop — there's no FUNCTION_NAME-based path that could route a motor to a commandable class. Tank-heater-style FUNCTION_NAMEs (freeze-protection relays on a tank, e.g. "Tank Heater", "Fresh Tank Heater") are deliberately excluded from the water-heater bucket despite the name similarity, since they're a different kind of device from a domestic water heater; they fall back to `relay_light`, which is accurate (a plain on/off relay) even if the label undersells what it's for.

`enable-device` also never offers a `(PRODUCT_ID, instance)` fallback-keyed device (`build_addable_list()` filters these out) — see the Stable-Key Device Discovery decision above for why those keys can't reliably identify a single physical device at all.

### Switch Service Follows the Real Shelly Driver Pattern

Lights, the water pump, and the water heater are published via Venus OS's native `com.victronenergy.switch` service using the exact `/SwitchableOutput/<n>/...` path structure used by Victron's own `dbus-shelly` driver (State, Status, Name, Settings/Type, Settings/Function, Settings/ValidTypes, Settings/ValidFunctions, Settings/CustomName) — confirmed by reading that driver's source rather than assuming. This was chosen over inventing a custom read-only service type after checking that `/State` being registered `writeable=False` isn't how any real Victron driver actually represents a not-yet-controllable output; Shelly always registers it writeable.

Phase 2 keeps `/State` writeable (so it renders as a normal switch in the Cerbo GUI, matching user expectation from Shelly integrations) but its `onchangecallback` unconditionally rejects the write and logs why — there is no command path wired up yet. Phase 3 replaces that rejecting callback with a real one. `Settings/Type` and `Settings/Function` are `writeable=False` even though Shelly's driver makes them writable, because a Shelly's physical output type is actually reconfigurable firmware-side; a OneControl device's type is fixed by its own hardware regardless of what the Cerbo GUI is told, so letting a user "change" it here would be a no-op that looks like it did something.

**Confirmed on real hardware (2026-08-20):** tapping the water pump switch in the Cerbo GUI immediately reverted to its previous state — the write was rejected as designed, and the GUI honestly reflects that rather than showing a stale "on" state. The read path was confirmed the same session: toggling the pump from the physical OneControl panel updated the Venus GUI immediately.

Motor status is deliberately never exposed this way — see `motor_status_service.py`, which uses a plain non-standard service name and has no writable state path of any kind (not even a rejected one), so it can never be mistaken for something controllable.

### Stable D-Bus Identifiers Across Restarts

Service name suffixes and device instance numbers are derived from a stable key via `zlib.crc32`, not Python's builtin `hash()`. `hash()` on strings is randomized per process (`PYTHONHASHSEED`) as a security feature — using it here would silently assign a new D-Bus service name (and therefore lose any GUI customization tied to it, like renamed/repositioned devices) on every service restart. Caught during Phase 2 implementation before it shipped; regression-tested in `tests/test_device_mapping.py` by actually spawning subprocesses with `PYTHONHASHSEED=random` and confirming the id doesn't change.

**A related but distinct bug was caught on first real deployment (2026-08-20), not in testing:** `zlib.crc32(...) % 100` is evenly distributed but not collision-free, and two of the four tank services (Grey Tank 1, Black Tank) were assigned the identical device instance (86) on first boot. Fixed with `device_mapping.assign_device_instance()`: the hash still picks a starting candidate, but it's now checked against every other already-assigned instance of the same kind and linearly probed to a free slot on collision, then persisted to `config.json` so the resolved value never changes again (regardless of what the hash would compute on a later run). This is a genuine gap the "deterministic hash is good enough" assumption had — collision-freedom needs an actual check, not just a large-enough modulus, once real device counts are in play.

---

## Protocol Reference

See `dev-notes/ARCHITECTURE.md` (private) for the full byte-level protocol reference (CAN ID formulas, DEVICE_STATUS payload layouts per device type, PID table, TEA cipher constants). Summary:

- 250 kbit/s, big-endian, single-frame only. Mixed 11-bit (broadcast) and 29-bit (point-to-point) CAN IDs on one bus.
- DEVICE_ID (11-bit, type 2) broadcasts device inventory ~1Hz; DEVICE_STATUS (type 3) broadcasts current state ~1Hz idle / ~333ms on change.
- PID_READ_WRITE (29-bit REQUEST) reads values not otherwise broadcast (e.g. battery voltage); no session needed for reads.
- Commands (29-bit COMMAND) require a TEA-cipher session handshake against SESSION_ID 4.

---

## Known Limitations

- Battery voltage is not published in Phase 2 — it's PID-based (request/response), not broadcast, and Phase 2 is passive-only (no bus transmission at all yet, not even a read request). Deferred, low priority per the user's own steer.
- PID battery voltage reads have not been validated against this coach's real traffic yet (deferred, low priority — no PID traffic appeared in the first capture at all). Everything else in v1's decode scope (DEVICE_ID structure, relay/motor status, dimmable light status, tank sensor status, the TEA session handshake) is now confirmed against a real 2026-08-19 capture from this coach — see dev-notes/ARCHITECTURE.md for specifics.
- The stable-key fallback (PRODUCT_ID, instance) is not always unique in practice: on this coach, 13 of 31 discovered devices report `FUNCTION_NAME=0` and also share an identical `(PRODUCT_ID, instance)`, so they cannot currently be distinguished from each other by any broadcast data. Per the user, these are simply unused/unconfigured physical input connections on the Unity module (empty ports, nothing wired to them) — not a gap needing investigation. If something new is ever wired into one of those ports, see the "Future Phase — System Configuration via CAN" item in TODO.md, which now has a concrete mechanism identified (writing PIDs 4/5). Devices with a real assigned FUNCTION_NAME (tanks, water pump, awning, slide, etc.) are unaffected.
- Generator, HVAC, and leveler status decoding are not implemented in v1 (undocumented byte layouts in the source research).
- Tank capacity (gallons) cannot be read from the bus reliably per prior community findings — must be configured manually if needed, not read live.
