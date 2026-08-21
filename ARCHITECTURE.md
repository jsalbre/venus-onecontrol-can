# Architecture

**Version:** 1.8 | **Updated:** 2026-08-21

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

Enabling a discovered device (`manage-devices`) never prompts for `device_class`. It's fully determined by what the device itself already broadcasts — `device_mapping.infer_device_class()` maps DEVICE_TYPE to `tank`/`dimmable_light`/`motor_status` unambiguously, and for the remaining relay-family DEVICE_TYPEs (which cover lights, pumps, and water heaters identically at the protocol level), sub-classifies using the device's FUNCTION_NAME semantics (e.g. FUNCTION_NAME 5 = "Water Pump" → `relay_pump`; 3/4 = "Gas/Electric Water Heater" → `relay_water_heater`; anything else defaults to `relay_light`). This mirrors how a human would work it out by reading the discovery log, and removing the prompt was an explicit user request after noticing the initial config was populated this way without ever being asked.

The inference is deliberately narrow where precision matters for safety: motor DEVICE_TYPEs always resolve to `motor_status`, full stop — there's no FUNCTION_NAME-based path that could route a motor to a commandable class. Tank-heater-style FUNCTION_NAMEs (freeze-protection relays on a tank, e.g. "Tank Heater", "Fresh Tank Heater") are deliberately excluded from the water-heater bucket despite the name similarity, since they're a different kind of device from a domestic water heater; they fall back to `relay_light`, which is accurate (a plain on/off relay) even if the label undersells what it's for.

`manage-devices` also never offers a `(PRODUCT_ID, instance)` fallback-keyed device to add (`build_addable_list()` filters these out) — see the Stable-Key Device Discovery decision above for why those keys can't reliably identify a single physical device at all. The same tool's "manage existing devices" mode (rename, toggle `expose`/`commands_enabled`, remove) has the identical restriction on editing `device_class` — it's never offered there either, for the same reason.

### Switch Service Follows the Real Shelly Driver Pattern

Lights, the water pump, and the water heater are published via Venus OS's native `com.victronenergy.switch` service using the exact `/SwitchableOutput/<n>/...` path structure used by Victron's own `dbus-shelly` driver (State, Status, Name, Settings/Type, Settings/Function, Settings/ValidTypes, Settings/ValidFunctions, Settings/CustomName, and Dimming for dimmable lights) — confirmed by reading that driver's source rather than assuming. This was chosen over inventing a custom read-only service type after checking that `/State` being registered `writeable=False` isn't how any real Victron driver actually represents a not-yet-controllable output; Shelly always registers it writeable.

`/State` and (for dimmable lights) `/Dimming` are both writeable, but their `onchangecallback`s unconditionally return `False` regardless of outcome — the GUI reverts immediately, and the real state only ever changes via a confirmed `DEVICE_STATUS` broadcast reaching `update_relay()`/`update_dimmable()`. This was Phase 2's read-only UX (a rejected write, logged, nothing sent) and is unchanged in Phase 3 (a real write attempt, logged, sent through the command safety gate) — `SwitchService` itself never became decision-making code; it only reports a write upward via `on_command()` and lets `publisher.py`/`command_gate.py`/`command_mapping.py` decide what happens. `Settings/Type` and `Settings/Function` are `writeable=False` even though Shelly's driver makes them writable, because a Shelly's physical output type is actually reconfigurable firmware-side; a OneControl device's type is fixed by its own hardware regardless of what the Cerbo GUI is told, so letting a user "change" it here would be a no-op that looks like it did something.

**Confirmed on real hardware (2026-08-20):** tapping the water pump switch in the Cerbo GUI immediately reverted to its previous state — the write was rejected as designed, and the GUI honestly reflects that rather than showing a stale "on" state. The read path was confirmed the same session: toggling the pump from the physical OneControl panel updated the Venus GUI immediately.

Motor status is deliberately never exposed this way — see `motor_status_service.py`, which uses a plain non-standard service name and has no writable state path of any kind (not even a rejected one), so it can never be mistaken for something controllable.

### Address Claiming (Phase 3)

Sending a COMMAND requires this bridge to hold a CAN source address of its own — Phases 0-2 were purely passive and never needed one. Rather than hardcode a fixed address (the only prior-art approach among the community reference projects, and one its own author calls out as coach-specific), the real claim procedure was decoded directly from a captured OneControl power-cycle/reconnect (`samples/poweroutage_capture.log`, gitignored — see `dev-notes/ARCHITECTURE.md` for the exact byte layout): a claim frame at CAN ID `0x000` with an 8-byte `[candidate_address, identity_tail(7)]` payload, followed roughly 1.0s later (matching the decompiled firmware's `ADDRESS_CLAIM_TIMEOUT` exactly) by steady-state NETWORK broadcasts at the claimed address. 32 real devices claimed 32 distinct addresses with zero contention in that capture, which is why this project's own contention handling (`can_link/address_claim.py::AddressClaimer`) is deliberately simple — retry with a new candidate on contention, back off after repeated failures — rather than replicating the real firmware's full MAC-priority arbitration.

This bridge's identity uses `DeviceType.ONECONTROL_APPLICATION` (34) and `FUNCTION_NAME` 1 ("Diagnostic Tool") — both already vendor-defined for exactly this kind of node, not arbitrary picks — plus a self-assigned `PRODUCT_ID` (`0xA0FF`) and a synthetic 7-byte identity tail generated once via `os.urandom(7)` and persisted (`ConfigManager.get_or_create_bridge_identity_tail()`), since this project has no real hardware identity of its own to reuse. Address `0x00` is permanently excluded as a candidate: this bridge's own steady-state NETWORK broadcast from address `0x00` would encode to CAN ID `0x000`, identical to the claim-frame ID itself — no real device in the capture ever claimed it either, consistent with this being a real protocol-level reservation. A fresh claim happens on every process start (never persisted), matching `address_table.py`'s existing "never trust state across a gap we didn't observe" philosophy — and once claimed, holding the address only requires continuing to broadcast on it, since other devices' own contention-avoidance logic (matching this bridge's) then naturally excludes it, without this bridge needing any outage-triggered re-claim logic.

### Dimmable-Light Command Byte Layout: Corrected Against Real Hardware

The dimmable-light COMMAND payload was originally documented (community-sourced, `dev-notes/ARCHITECTURE.md`, citing esphome-onecontrol's `IDS-CAN.md`) as `[mode, brightness(1-100), auto_off_minutes, t1_hi, t1_lo, t2_hi, t2_lo, reserved]`. A real capture of a plain on/off tap (`samples/capture.log`) didn't fit this at all — `mode=0x7F` is outside the documented 0-3 enum, and `brightness=0` failed the builder's own validation in both directions — while a real relay command captured in the same session matched its own documented format exactly, making this look specific to dimmable lights rather than a project-wide documentation problem.

Rather than guess, a second real capture was taken specifically of a brightness-slider drag (`samples/dimming_capture.log`, 2026-08-20). It resolved the discrepancy completely: the byte *positions* were right all along, but brightness is a raw **0-255** scale (matching `DimmableLightStatus.current_brightness`'s own scale, not a 1-100 percentage), and a plain on/off tap uses a separate, simpler command — `mode=0x7F` to resume the light's own last remembered brightness, or all-zero bytes to turn off — distinct from the granular `mode=1, brightness=N` command a slider drag sends. Five distinct real brightness commands in that capture each produced an immediate, exact-match `DEVICE_STATUS.current_brightness`. `can_link/command.py`'s `build_dimmable_light_command()` validation was fixed to the confirmed 0-255 range; `build_dimmable_light_toggle_command()` was added for the separate plain on/off case. `auto_off_minutes`/`t1_ms`/`t2_ms` were never exercised in either capture (always 0) and remain unconfirmed, but aren't needed for Phase 3's scope (a plain brightness percentage, no auto-off timer or cycling).

### Config-Gated Commands (Phase 3)

Mirrors the config-gated-exposure design above, one layer deeper: a device must be both `expose: true` **and** `commands_enabled: true` before any command is attempted — two independent flags, so a device can be visible/read-only without ever being commandable (`commands_enabled` defaults to `False` everywhere it can be set, matching `expose`'s own default). `dbus_bridge/command_gate.py::evaluate_command_request()` is the single decision point (`NOT_EXPOSED` / `COMMANDS_NOT_ENABLED` / `UNSUPPORTED_DEVICE_CLASS` / `NOT_VERIFIED` / `OK`), pure and unit tested like `routing.py`. Its `NOT_VERIFIED` check (`address_table.resolve_for_command()`) is deliberately a *cheap early refusal*, not the sole safety mechanism — `can_link/command_sequencer.py::CommandAttempt` re-runs the identical check immediately before the COMMAND frame is actually built, since a bus outage can be detected during the handshake's ~1-3ms round trips, and that second check is what actually protects against a race. `manage-devices` offers a `commands_enabled` prompt (at add time, defaulting to No) and toggle (for an already-configured device) only for device classes `command_gate.py` will ever approve (`relay_light`, `relay_pump`, `relay_water_heater`, `dimmable_light` — never `tank`/`motor_status`).

### Stable D-Bus Identifiers Across Restarts

Service name suffixes and device instance numbers are derived from a stable key via `zlib.crc32`, not Python's builtin `hash()`. `hash()` on strings is randomized per process (`PYTHONHASHSEED`) as a security feature — using it here would silently assign a new D-Bus service name (and therefore lose any GUI customization tied to it, like renamed/repositioned devices) on every service restart. Caught during Phase 2 implementation before it shipped; regression-tested in `tests/test_device_mapping.py` by actually spawning subprocesses with `PYTHONHASHSEED=random` and confirming the id doesn't change.

**A related but distinct bug was caught on first real deployment (2026-08-20), not in testing:** `zlib.crc32(...) % 100` is evenly distributed but not collision-free, and two of the four tank services (Grey Tank 1, Black Tank) were assigned the identical device instance (86) on first boot. Fixed with `device_mapping.assign_device_instance()`: the hash still picks a starting candidate, but it's now checked against every other already-assigned instance of the same kind and linearly probed to a free slot on collision, then persisted to `config.json` so the resolved value never changes again (regardless of what the hash would compute on a later run). This is a genuine gap the "deterministic hash is good enough" assumption had — collision-freedom needs an actual check, not just a large-enough modulus, once real device counts are in play.

### This Project Brings Its Own CAN Interface Up

Originally a deliberate boundary: `bus/socketcan.py` assumed the CAN interface was already up and configured (250 kbit/s) before use, treating that as an external, system-level responsibility (a one-time manual `ip link set vecan1 up type can bitrate 250000`, documented in the Hardware Setup section below) — not something this project's code should own. That assumption broke on real hardware (2026-08-21): after a Venus OS firmware update, the interface still existed but came back administratively `DOWN`, and nothing else on the system noticed or fixed it, silently taking the whole bridge offline (the service itself stayed running the whole time — `svstat` showed it up — it just had nothing to read or publish).

`SocketCanBus` now calls `ensure_interface_up()` before every connection attempt, bringing the interface up itself if (and only if) it's currently down — checked via the kernel's own `IFF_UP` flag (`/sys/class/net/<if>/flags`), not by parsing `ip link show` output, and never touching an interface that's already up. This makes the bridge self-healing across whatever external event (firmware update, a cold boot before Venus OS's own CAN-bus profile assignment settles, etc.) might otherwise leave the interface down, without needing to distinguish *why* it's down.

That only covers the interface being down at *(re)connect* time, though -- a `SocketCanBus` that's already bound stays bound even if the interface later goes down mid-run (bind() doesn't require the interface to be up, and `recv()` never fires an error in that state either, since no traffic arrives to trigger the GLib read watch at all). `Publisher._send_frame()` -- already the single chokepoint every outbound frame goes through -- is what catches this instead: a write failure calls `ensure_interface_up()` again right there, edge-triggered logging (one WARNING when first noticed down, one INFO on recovery, DEBUG for repeated drops in between) rather than a WARNING per dropped frame, and further recovery attempts rate-limited to once per `INTERFACE_RECOVERY_RETRY_SEC` (15s, a plain fixed interval -- deliberately not exponential backoff, since bringing the interface up is cheap and idempotent, unlike the outer service-restart backoff where each retry is an expensive full teardown/recreate). A `CommandAttempt` that fails to actually transmit because of this needs no special handling -- it just sits waiting for a RESPONSE that'll never come, and the existing 2s timeout sweep (`command_sequencer.DEFAULT_STEP_TIMEOUT_SEC`) cleans it up the same as any other non-response.

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
- Dimmable-light command `auto_off_minutes`/`t1_ms`/`t2_ms` (auto-off timer, blink/swell cycle timing) are unconfirmed against real hardware — both real captures used to validate the command format only ever sent zero for these fields. Not needed for Phase 3's scope (a plain brightness percentage).
- Tank capacity (gallons) cannot be read from the bus reliably per prior community findings — must be configured manually if needed, not read live.
