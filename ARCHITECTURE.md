# Architecture

**Version:** 3.4 | **Updated:** 2026-08-24

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

**This coach's addressing is not always unique via the fallback key alone:** 13 of 31 devices discovered on this coach share `FUNCTION_NAME=0` and an identical `(PRODUCT_ID, instance)` — see "device_instance -- Disambiguating Devices That Share the Fallback Stable Key" below for the real disambiguator found for this specific case.

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

Sending a COMMAND requires this bridge to hold a CAN source address of its own — Phases 0-2 were purely passive and never needed one. Rather than hardcode a fixed address (the only prior-art approach among the community reference projects, and one its own author calls out as coach-specific), the real claim procedure was decoded directly from a captured OneControl power-cycle/reconnect (`samples/poweroutage_capture.log`, gitignored — see "Address Claiming" under Protocol Reference below for the exact byte layout, decoded from this coach's own capture): a claim frame at CAN ID `0x000` with an 8-byte `[candidate_address, identity_tail(7)]` payload, followed roughly 1.0s later (matching the decompiled firmware's `ADDRESS_CLAIM_TIMEOUT` exactly) by steady-state NETWORK broadcasts at the claimed address. 32 real devices claimed 32 distinct addresses with zero contention in that capture, which is why this project's own contention handling (`can_link/address_claim.py::AddressClaimer`) is deliberately simple — retry with a new candidate on contention, back off after repeated failures — rather than replicating the real firmware's full MAC-priority arbitration.

This bridge's identity uses `DeviceType.ONECONTROL_APPLICATION` (34) and `FUNCTION_NAME` 1 ("Diagnostic Tool") — both already vendor-defined for exactly this kind of node, not arbitrary picks — plus a self-assigned `PRODUCT_ID` (`0xA0FF`) and a synthetic 7-byte identity tail generated once via `os.urandom(7)` and persisted (`ConfigManager.get_or_create_bridge_identity_tail()`), since this project has no real hardware identity of its own to reuse. Address `0x00` is permanently excluded as a candidate: this bridge's own steady-state NETWORK broadcast from address `0x00` would encode to CAN ID `0x000`, identical to the claim-frame ID itself — no real device in the capture ever claimed it either, consistent with this being a real protocol-level reservation. A fresh claim happens on every process start (never persisted), matching `address_table.py`'s existing "never trust state across a gap we didn't observe" philosophy — and once claimed, holding the address only requires continuing to broadcast on it, since other devices' own contention-avoidance logic (matching this bridge's) then naturally excludes it, without this bridge needing any outage-triggered re-claim logic.

### Dimmable-Light Command Byte Layout: Corrected Against Real Hardware

The dimmable-light COMMAND payload was originally documented (community-sourced, citing esphome-onecontrol's `IDS-CAN.md`) as `[mode, brightness(1-100), auto_off_minutes, t1_hi, t1_lo, t2_hi, t2_lo, reserved]`. A real capture of a plain on/off tap (`samples/capture.log`) didn't fit this at all — `mode=0x7F` is outside the documented 0-3 enum, and `brightness=0` failed the builder's own validation in both directions — while a real relay command captured in the same session matched its own documented format exactly, making this look specific to dimmable lights rather than a project-wide documentation problem.

Rather than guess, a second real capture was taken specifically of a brightness-slider drag (`samples/dimming_capture.log`, 2026-08-20). It resolved the discrepancy completely: the byte *positions* were right all along, but brightness is a raw **0-255** scale (matching `DimmableLightStatus.current_brightness`'s own scale, not a 1-100 percentage), and a plain on/off tap uses a separate, simpler command — `mode=0x7F` to resume the light's own last remembered brightness, or all-zero bytes to turn off — distinct from the granular `mode=1, brightness=N` command a slider drag sends. Five distinct real brightness commands in that capture each produced an immediate, exact-match `DEVICE_STATUS.current_brightness`. `can_link/command.py`'s `build_dimmable_light_command()` validation was fixed to the confirmed 0-255 range; `build_dimmable_light_toggle_command()` was added for the separate plain on/off case. `auto_off_minutes`/`t1_ms`/`t2_ms` were never exercised in either capture (always 0) and remain unconfirmed, but aren't needed for Phase 3's scope (a plain brightness percentage, no auto-off timer or cycling). See "Command Payload Shapes" under Protocol Reference below for the full worked example.

### Config-Gated Commands (Phase 3)

Mirrors the config-gated-exposure design above, one layer deeper: a device must be both `expose: true` **and** `commands_enabled: true` before any command is attempted — two independent flags, so a device can be visible/read-only without ever being commandable (`commands_enabled` defaults to `False` everywhere it can be set, matching `expose`'s own default). `dbus_bridge/command_gate.py::evaluate_command_request()` is the single decision point (`NOT_EXPOSED` / `COMMANDS_NOT_ENABLED` / `UNSUPPORTED_DEVICE_CLASS` / `NOT_VERIFIED` / `OK`), pure and unit tested like `routing.py`. Its `NOT_VERIFIED` check (`address_table.resolve_for_command()`) is deliberately a *cheap early refusal*, not the sole safety mechanism — `can_link/command_sequencer.py::CommandAttempt` re-runs the identical check immediately before the COMMAND frame is actually built, since a bus outage can be detected during the handshake's ~1-3ms round trips, and that second check is what actually protects against a race. `manage-devices` offers a `commands_enabled` prompt (at add time, defaulting to No) and toggle (for an already-configured device) only for device classes `command_gate.py` will ever approve (`relay_light`, `relay_pump`, `relay_water_heater`, `dimmable_light` — never `tank`/`motor_status`).

**Confirmed on real hardware (2026-08-21):** a `commands_enabled: false` device transmits nothing on a write attempt — verified with a parallel raw capture during a real write attempt; zero `COMMAND` frames and no bridge-originated `REQUEST` traffic. See CHANGELOG.md.

### Stable D-Bus Identifiers Across Restarts

Service name suffixes and device instance numbers are derived from a stable key via `zlib.crc32`, not Python's builtin `hash()`. `hash()` on strings is randomized per process (`PYTHONHASHSEED`) as a security feature — using it here would silently assign a new D-Bus service name (and therefore lose any GUI customization tied to it, like renamed/repositioned devices) on every service restart. Caught during Phase 2 implementation before it shipped; regression-tested in `tests/test_device_mapping.py` by actually spawning subprocesses with `PYTHONHASHSEED=random` and confirming the id doesn't change.

**A related but distinct bug was caught on first real deployment (2026-08-20), not in testing:** `zlib.crc32(...) % 100` is evenly distributed but not collision-free, and two of the four tank services (Grey Tank 1, Black Tank) were assigned the identical device instance (86) on first boot. Fixed with `device_mapping.assign_device_instance()`: the hash still picks a starting candidate, but it's now checked against every other already-assigned instance of the same kind and linearly probed to a free slot on collision, then persisted to `config.json` so the resolved value never changes again (regardless of what the hash would compute on a later run). This is a genuine gap the "deterministic hash is good enough" assumption had — collision-freedom needs an actual check, not just a large-enough modulus, once real device counts are in play.

### This Project Brings Its Own CAN Interface Up

Originally a deliberate boundary: `bus/socketcan.py` assumed the CAN interface was already up and configured (250 kbit/s) before use, treating that as an external, system-level responsibility (a one-time manual `ip link set vecan1 up type can bitrate 250000`) — not something this project's code should own. That assumption broke on real hardware (2026-08-21): after a Venus OS firmware update, the interface still existed but came back administratively `DOWN`, and nothing else on the system noticed or fixed it, silently taking the whole bridge offline (the service itself stayed running the whole time — `svstat` showed it up — it just had nothing to read or publish).

`SocketCanBus` now calls `ensure_interface_up()` before every connection attempt, bringing the interface up itself if (and only if) it's currently down — checked via the kernel's own `IFF_UP` flag (`/sys/class/net/<if>/flags`), not by parsing `ip link show` output, and never touching an interface that's already up. This makes the bridge self-healing across whatever external event (firmware update, a cold boot before Venus OS's own CAN-bus profile assignment settles, etc.) might otherwise leave the interface down, without needing to distinguish *why* it's down.

That only covers the interface being down at *(re)connect* time, though -- a `SocketCanBus` that's already bound stays bound even if the interface later goes down mid-run (bind() doesn't require the interface to be up, and `recv()` never fires an error in that state either, since no traffic arrives to trigger the GLib read watch at all). `Publisher._send_frame()` -- already the single chokepoint every outbound frame goes through -- is what catches this instead: a write failure calls `ensure_interface_up()` again right there, edge-triggered logging (one WARNING when first noticed down, one INFO on recovery, DEBUG for repeated drops in between) rather than a WARNING per dropped frame, and further recovery attempts rate-limited to once per `INTERFACE_RECOVERY_RETRY_SEC` (15s, a plain fixed interval -- deliberately not exponential backoff, since bringing the interface up is cheap and idempotent, unlike the outer service-restart backoff where each retry is an expensive full teardown/recreate). A `CommandAttempt` that fails to actually transmit because of this needs no special handling -- it just sits waiting for a RESPONSE that'll never come, and the existing 2s timeout sweep (`command_sequencer.DEFAULT_STEP_TIMEOUT_SEC`) cleans it up the same as any other non-response.

### PID Reconfiguration: Researched, Read Path and Real Writes Confirmed

Reconfiguring a device -- assigning a real name/function to a currently-unused Unity input/output, or (the specific real case that prompted this) figuring out why one of this coach's Unity X270D board's dimming-capable outputs behaved as a plain on/off latch instead of a dimmer, in both the OneControl app and this project -- requires *writing* PIDs, not just reading them. This project decoded how that works (2026-08-21, decompiled LippertConnect source, cross-checked against Lippert's own official `OneControl Configurator Guide`, CCD-0001830), and has since confirmed the full write path against real hardware for one specific PID (161, see "PID Writes" under Protocol Reference below) via a manual, `--confirm`-gated diagnostic tool. `manage-system` (project root, 2026-08-22) is the resulting interactive tool -- distinct from `manage-devices`, which only ever touches this bridge's own D-Bus config -- built once the dimming/latching setting (PID 161) and target disambiguation (`device_instance`) were proven real, with identity writes (PID 4/5) assumed to work the same way. That assumption was wrong in a specific, now-fixed way: its first real run (2026-08-23) tried to rename an unconfigured port and both PID 4 and PID 5 writes failed -- root-caused to the same universal value-width issue described in "PID Writes" below, not anything specific to identity fields. Not yet a fully automated/production-integrated capability (no `command_gate.py`/`publisher.py` involvement, deliberately -- see `TODO.md`):

- PID writes reuse request code `0x11` (the same one `pid_client.py` already uses for reads), distinguished purely by payload length -- confirmed, no new wire-level message type needed.
- Device rename/reassignment (PID 4 `FUNCTION_NAME` / PID 5 `FUNCTION_INSTANCE`) is confirmed real and documented -- the Configurator's `DEVICE NAME` section, shown in the guide being used on a device literally labeled `UNKNOWN`. This is exactly the "enable an unused port" case. Both PIDs require the **DIAGNOSTIC session (SESSION_ID=2)** -- correcting an earlier, never-implemented guess in this project's own notes that assumed `REMOTE_CONTROL` (SESSION_ID=4, the session commands already use). The correction is cross-validated, not just decompiled-and-trusted: the decompiled `REMOTE_CONTROL` cypher constant converts to exactly `0xB16B00B5`, the same value already proven against 8 real captured handshakes, giving real confidence in the sibling DIAGNOSTIC constant (`0xBABECAFE`) despite having zero real captures of a DIAGNOSTIC handshake -- since confirmed for real, see "PID Writes" below.
- The dimmable-output problem does **not** match a documented feature the way it was first framed -- the Configurator has no dimmable-to-relay conversion anywhere; its only dimmer-specific setting is `DEVICE SWITCH TYPE` (Toggle vs. Momentary), which is about a *physical wall switch's* behavior, not the output's own dimming capability. `LOAD_TYPE` (PID 451, also DIAGNOSTIC-gated per the decompiled PID catalog) looked like a real, specific candidate for what's clamping it -- **ruled out** by real hardware, see below.
- No real capture can validate any of this before implementation -- confirmed with the user: the phone app can't reconfigure at all, only the physical touchscreen can, and this installation doesn't have one.

`src/tools/pid_probe.py` (read-only, no session, cannot change anything) was built first, before any write path, specifically to query real values rather than guess. **Its first real run (2026-08-21) disproved the `DEVICE_TYPE`(183)/`LOAD_TYPE`(451) hypothesis directly**: probed against two dimmable lights (one misbehaving, one working) and, as a deliberate cross-check, a tank sensor with an independently-known, different real `DeviceType` -- all three real devices returned byte-identical replies for both PIDs. That ruled out "these are real per-device values" (a tank and a light would not read the same real `DEVICE_TYPE`); decoding the reply's trailing byte against the decompiled `RESPONSE` enum confirmed why -- it's `UNKNOWN_ID` (4) in every case. Neither PID is recognized by this hardware family at all; the decompiled catalog these numbers came from likely covers a different Lippert product line. Chasing more PID numbers from that same catalog isn't a productive next step, so `pid_probe.py` gained a better one instead: `--list-pids` (`PID_READ_LIST`, request code `0x10`, also read-only/no-session) enumerates every PID a specific real device actually supports, discovered directly rather than guessed.

**The dimming root cause is confirmed, and the fix is confirmed working end-to-end on real hardware (2026-08-21).** `--list-pids` against both real lights on this coach returned an identical 97-PID supported set (identical numbers, identical per-PID flags) -- so the difference between the working and misbehaving dimmer isn't which PIDs are supported, it's a value within that shared set. Cross-referencing all 97 numbers against the full named PID catalog in the decompiled source (`assembly_0080_Sharp.decompiled.cs`) surfaced **PID 161 = `SIMULATE_ON_OFF_STYLE_LIGHT`** (DIAGNOSTIC-gated, `flags=0x07` readable+writable on both devices) -- a name matching the user's own description almost exactly. Reading it confirmed the hypothesis directly: `1` (true) on Kitchen Island Light (the misbehaving one, this coach), `0` (false) on Kitchen Pendants Light (working, this coach). This also incidentally confirmed `parse_pid_reply()`'s previously-unvalidated success-reply shape is correct as-is (real reply `00 A1 01` decodes exactly as the existing code already assumed) -- see "PID_READ_WRITE" under Protocol Reference below.

**The write path was then built and validated live.** `can_link/session.py` was generalized to support an arbitrary SESSION_ID/cypher (previously hardcoded to REMOTE_CONTROL); `can_link/pid_client.py` gained `build_pid_write_request()`; a new, deliberately separate and `--confirm`-gated CLI (`src/tools/pid_write.py`) opens a DIAGNOSTIC session (SESSION_ID=2) and performs one write, verifying it via a plain read-back rather than trusting the write RESPONSE's own (previously unconfirmed) shape. First real attempt (`--value-bytes 1`, matching the width a *read* returns) was rejected with `RESPONSE.BAD_REQUEST` -- the DIAGNOSTIC handshake itself completed cleanly (first real validation of any session type besides REMOTE_CONTROL), so the rejection pointed at the write payload's shape, not authorization. Retrying with the PID's full declared `Formatter` width (`UINT48` = 6 bytes, per the decompiled catalog) succeeded: `RESPONSE.SUCCESS`, and a read-back confirmed PID 161 now reads `0`. **The user then confirmed the fix physically** -- Kitchen Island Light dims correctly now, and after restarting the OneControl phone app, it shows the device as a dimmer there too (the app itself reads this same PID to decide how to render the device). See "PID Writes" and "SESSION_ID Catalog" under Protocol Reference below for the full byte-level detail.

**Second goal (assigning a name to a currently-unused port) hit a real disambiguation problem, now solved and physically confirmed for the target port (2026-08-22).** This coach's stable-key fallback's `(PRODUCT_ID, product_instance)` turned out to be a shared generic default across the entire physical Unity module, not per-port -- useless for picking out one specific unconfigured port among many. `DEVICE_ID`'s `device_instance` field (decoded since day one, never previously used) turned out to be the real answer: a sequential, gapless counter scoped per DEVICE_TYPE across the whole module. Confirmed with two independent real-hardware cross-checks against this coach's own wiring inventory, one of which was independently corroborated a second way (a candidate device's real fuse-rating PID value matched a physically-distinct output's known amperage rating exactly). A new manual `--confirm`-gated tool (`src/tools/relay_blip.py`) was built to convert "high confidence" into "confirmed" by briefly energizing a candidate relay while the user watches a multimeter -- **run for real against both DIMM/LATCH output 7 and output 8, both confirmed correct**. Its first run also caught a real bug (the default hold matched the session timeout exactly, briefly leaving output 7 stuck on) -- fixed, see CHANGELOG.md. The remaining candidates in the mapping (Water Heater pair, Tank positions) are still position-in-sequence inference only, not individually blip-tested. Full detail, including what remains genuinely unresolved (a separate "Configurable Inputs" bank has no matching visible CAN device at all), in "device_instance -- Disambiguating Devices That Share the Fallback Stable Key" under Protocol Reference below.

---

## Source Files

| File | Description |
|------|-------------|
| `src/can_link/types.py` | Enums (MessageType, DeviceType, FunctionName) and StableKey dataclass |
| `src/can_link/frame.py` | 11-bit/29-bit CAN ID encode/decode |
| `src/can_link/device_id.py` | DEVICE_ID broadcast decoder + `stable_key()` helper |
| `src/can_link/device_status.py` | DEVICE_STATUS decoders, dispatched by DeviceType |
| `src/can_link/pid_client.py` | PID_READ_LIST (0x10), PID_READ_WRITE (0x11) request/reply (read + `build_pid_write_request()`), PID_GET_PROPERTIES (0x12) request builder + defensive reply parser. Write support confirmed real 2026-08-21 (see "PID Reconfiguration" design decision above). |
| `src/can_link/session.py` | TEA cipher + session handshake state machine, generalized (2026-08-21) to an arbitrary SESSION_ID/cypher (`SESSION_CYPHERS`) -- REMOTE_CONTROL (default) and DIAGNOSTIC are both real-hardware-confirmed. |
| `src/can_link/command.py` | COMMAND frame builders (relay, dimmable-light granular set, dimmable-light plain toggle) |
| `src/can_link/command_sequencer.py` | `CommandAttempt` -- async TEA-handshake-through-COMMAND state machine, re-verifies the command safety gate right before sending COMMAND. Pure. |
| `src/can_link/address_claim.py` | This bridge's own CAN address claiming + steady-state self-announcement. Pure. |
| `src/can_link/address_table.py` | Stable-key/live-address table, expiry, bus-outage safety gate |
| `src/bus/socketcan.py` | stdlib `socket.AF_CAN`/`CAN_RAW` wrapper |
| `src/tools/candump_logger.py` | Phase 0: raw frame capture to log |
| `src/tools/candump_replay.py` | Phase 1+: replay logs through the decoder for offline validation |
| `src/tools/probe_common.py` | Shared bus-setup/address-claim/request-response plumbing, plus session-open/close (`open_session`/`close_session`), a test-blip helper (`send_test_blip`), and board-scanning (`scan_board`) -- extracted as each became needed by 2+ real callers (`pid_probe.py`, `pid_write.py`, `relay_blip.py`, `list_unconfigured.py`, `manage-system`). |
| `src/tools/pid_probe.py` | Read-only diagnostic CLI: `--list-pids` enumerates every PID a real device supports (PID_READ_LIST), plus PID_READ_WRITE/PID_GET_PROPERTIES against arbitrary PIDs. Never opens a session, never writes a PID -- permanently read-only by design, unlike `pid_write.py`. |
| `src/tools/pid_write.py` | Manual, `--confirm`-gated PID write CLI (2026-08-21). Opens a DIAGNOSTIC session, writes one PID, verifies via read-back. Real hardware confirmed (PID 161 on Kitchen Island Light). Deliberately separate from `pid_probe.py`, never wired into `manage-devices`/`publisher.py`. |
| `src/tools/list_unconfigured.py` | Purely passive diagnostic (2026-08-22) -- doesn't claim a CAN address at all. Lists unconfigured (FUNCTION_NAME=0) devices sharing a reference device's board, plus `--compare-key` to resolve known devices' full `DeviceIdentity` fields (esp. `device_instance`) for comparison. See "device_instance" section below. |
| `src/tools/relay_blip.py` | Manual, `--confirm`-gated relay/dimmable ON-OFF test CLI (2026-08-22). Sends a real COMMAND directly to a raw address over REMOTE_CONTROL, bypassing `command_gate.py` (target has no `device_class` to gate on). Physically confirmed both DIMM/LATCH outputs 7 and 8 (2026-08-22). Caught and fixed a real bug: the original default hold matched the session's own 5s timeout exactly, silently leaving a relay stuck on. |
| `manage-system` (project root) | Interactive tool to reconfigure the Unity module itself (PID writes) -- distinct from `manage-devices`, which only touches this bridge's own D-Bus config. Configure a port (identity + any applicable known setting, e.g. PID 161's dimming/latching behavior), unconfigure a port, or back up every port's current settings to `samples/`. Opens with a mandatory typed-acknowledgment warning every run. See "PID Reconfiguration" design decision above. Not yet run on real hardware. |
| `src/dbus_bridge/config_manager.py` | `ConfigManager` (the exposure safety gate, `is_exposed()`) + `DiscoveryLog`. Pure, no dbus/gi. |
| `src/dbus_bridge/device_mapping.py` | Pure device_class <-> DeviceType/service-kind/OutputType/OutputFunction/FluidType mapping, device instance assignment (`assign_device_instance`), and device_class inference for the enable tool (`infer_device_class`, `build_addable_list`). No dbus/gi. |
| `src/dbus_bridge/routing.py` | Pure decision logic for what publisher.py should do with a decoded frame (the second half of the safety gate). No dbus/gi -- this is what makes the exposure logic testable despite publisher.py itself requiring D-Bus. |
| `src/dbus_bridge/backoff.py` | `RestartBackoff`, pure (explicit `now`, no real `time.sleep`). |
| `src/dbus_bridge/tank_service.py` | `com.victronenergy.tank` service. Requires dbus/gi. |
| `src/dbus_bridge/switch_service.py` | `com.victronenergy.switch` service (lights/pump/water-heater), Shelly-pattern paths, State + Dimming writeable (Phase 3). Requires dbus/gi. |
| `src/dbus_bridge/motor_status_service.py` | Read-only, non-standard service type for motor status. No writable state path of any kind. Requires dbus/gi. |
| `src/dbus_bridge/command_gate.py` | Pure command safety gate decision logic (exposed + commands_enabled + supported device_class + address_table verification). No dbus/gi. |
| `src/dbus_bridge/command_mapping.py` | Pure device_class -> CanFrame dispatch for a SwitchService write. No dbus/gi. |
| `src/dbus_bridge/publisher.py` | Orchestrator: SocketCAN read loop, GLib main loop, lazy service creation via routing.py, RestartBackoff, address claiming, command sequencing. Requires dbus/gi -- cannot run or be imported on a non-Linux dev machine. |
| `manage-devices` (project root, formerly `enable-device`) | Interactive CLI to add a discovered device to config.json, and to rename/toggle/remove one already configured. No dbus/gi -- runs fine off the Cerbo for review; only the final service-restart step needs the real device. |

**Testability note:** every file above except the four requiring dbus/gi (`tank_service.py`, `switch_service.py`, `motor_status_service.py`, `publisher.py`) has unit tests that run in this repo. The dbus/gi-dependent files are syntax-checked with `py_compile` only, and can only be functionally verified on the actual Cerbo.

---

## Protocol Reference (IDS-CAN)

**Sources (cross-validated):** andrewcfitz/esphome-onecontrol (`IDS-CAN.md`), D-Jeffrey/UnityX-canbus (`IDS-coding.md`), manos/OneControl-RV-C-Protocol (decompiled Lippert `IDS.Core.IDS_CAN` firmware). None of these target Victron/SocketCAN directly — this project is original integration work on top of their protocol documentation.

**Validation status against this coach's real traffic (2026-08-19 capture, samples/capture.log, gitignored):** 1514 DEVICE_ID broadcasts decoded cleanly across 31 discovered devices, zero decode errors. TEA cipher confirmed exactly against 8 real seed/key pairs (see tests/test_session.py). Relay/motor status (OutputState, current_draw_amps) confirmed against a real water pump ON->OFF->ON cycle and a relay-driven light ON->OFF cycle. Dimmable light status (mode, current_brightness) confirmed against two real light ON->OFF cycles. Tank sensor status confirmed against real known levels (fresh 66%, grey 66%/33%, black 33%) -- this also caught and fixed a real bug (`battery_level_pct=0xFF` "not supported" sentinel wasn't handled, was reading as a literal 255%). **Still not validated:** PID battery voltage reads (deferred, low priority -- no PID traffic seen in this capture at all), TIME/CIRCUIT_ID/PRODUCT_STATUS/NETWORK broadcast payloads (structure decodes without error but semantics unconfirmed -- CIRCUIT_ID in particular shows unexpected non-zero traffic on this coach, flagged in TODO.md), and generator/HVAC/leveler status (out of v1 scope, undecoded). CIRCUIT_ID was double-checked and is confirmed genuinely all-zero/unused on this coach too (1525 frames, all devices, zero payload variation) -- an earlier note in this file overstated it as "non-trivial" based only on sender count, not payload content; corrected 2026-08-19.

**Also found (2026-08-19), this coach specifically:** the `(PRODUCT_ID, instance)` stable-key fallback is not reliably unique -- 13 of the 31 discovered devices share FUNCTION_NAME=0 and an identical `(PRODUCT_ID=232, instance=42)`, so they're indistinguishable from each other by that fallback alone. Per the user (2026-08-20), these are known to be unused/unconfigured physical input connections on the Unity module itself -- empty ports, not mystery devices. `device_instance` later turned out to be a real disambiguator for exactly this -- see "device_instance -- Disambiguating Devices That Share the Fallback Stable Key" below. Devices with a real FUNCTION_NAME are unaffected.

### Physical/Link Layer

- 250 kbit/s, ISO 11898-1, big-endian multi-byte fields, single-frame only (no ISO-TP/multi-frame).
- Bus mixes standard 11-bit and extended 29-bit CAN IDs — must check `CAN_EFF_FLAG` per frame, not assume one width.
- Termination: exactly two 120Ω terminators at true bus ends. This installation wires the Cerbo in as the new physical end of the bus, so a terminator is plugged into the Cerbo CAN interface's unused plug (a physical connector, not a software/config setting), and the terminator must be removed from whatever device was previously the bus's end (not left in place alongside the new one — three terminators overloads the bus past what the transceivers can drive).
- CANH/CANL polarity: reversed wiring causes silence, not damage — safe to swap and retry. The Unity board's CAN H goes to pin 7 and CAN L to pin 8 on the Cerbo's CAN interface connector — the same across all Unity boards and Cerbo GX devices, not specific to this installation.
- Venus OS's own CAN-bus service must have this interface's profile set to **disabled** in Venus OS settings, or it tries to manage the port itself — the interface remains available at the kernel level for this project's raw SocketCAN access regardless. See README.md's Hardware Setup section for the full procedure.

### CAN ID Structure

Confirmed byte-exact against decompiled Lippert `IDS.Core.IDS_CAN.CAN_ID` struct:

**Standard 11-bit (broadcasts):**
```text
SourceAddress = id & 0xFF
MessageType   = (id >> 8) & 0x7
```
MessageType: 0=NETWORK, 1=CIRCUIT_ID, 2=DEVICE_ID, 3=DEVICE_STATUS, 6=PRODUCT_STATUS, 7=TIME.

**Extended 29-bit (point-to-point):**
```text
SourceAddress = (id >> 18) & 0xFF
TargetAddress = (id >> 8) & 0xFF
MessageData   = id & 0xFF
MessageType   = 0x80 | ((id >> 24) & 0x1C) | ((id >> 16) & 0x3)
```
MessageType (all ≥0x80): 128=REQUEST, 129=RESPONSE, 130=COMMAND, 131=EXT_STATUS, 132=TEXT_CONSOLE, 133=GROUP_ID. (155=DAQ, 157=IOT, 159=BULK_XFER exist in firmware but don't cleanly fit this formula — unconfirmed, out of scope for v1.)

### DEVICE_ID Broadcast (MessageType 2, ID = `0x200 | addr`, ~1Hz)

8-byte payload:

| Byte | Field |
|------|-------|
| 0-1 | PRODUCT_ID (uint16 BE) |
| 2 | Product instance |
| 3 | DEVICE_TYPE (enum) |
| 4-5 | FUNCTION_NAME (uint16 BE, enum) |
| 6 | High nibble = device instance, low nibble = function instance |
| 7 | Capabilities bitfield (not fully decoded by any source) |

Stable key = `(FUNCTION_NAME, function_instance_nibble)`, fallback `(PRODUCT_ID, instance)` if FUNCTION_NAME is 0/unset.

**FUNCTION_NAME source of truth (2026-08-20):** `can_link/types.py::FUNCTION_NAMES` is the full 446-entry table (values 0-445), extracted from the decompiled LippertConnect Android app's `FUNCTION_NAME` class (`tools/decompile/decompiled/IDS_Core_CAN/assembly_0080/assembly_0080_Sharp.decompiled.cs` in manos/OneControl-RV-C-Protocol, static constructor, lines ~1818-2263). Confirmed complete (0-445, no gaps) and spot-checked against the user's own LippertConnect app display. Two distinct codes (109, 142) both map to `"Leveler"` -- verified as a genuine vendor duplicate, not a transcription error. The app never transmits these strings over CAN; it resolves the numeric FUNCTION_NAME locally against this same table, exactly as this project now does. FUNCTION_NAME/FunctionInstance are backed by dedicated PIDs 4 (`IDS_CAN_FUNCTION_NAME`, UINT16) and 5 (`IDS_CAN_FUNCTION_INSTANCE`, UINT8) per `D-Jeffrey/UnityX-canbus`'s independently-derived PID table -- device-owned values, not gateway-negotiated per session.

### device_instance -- Disambiguating Devices That Share the Fallback Stable Key (2026-08-22)

DEVICE_ID byte 6's high nibble (`device_instance`) was decoded from day one but never used anywhere in this project -- the stable-key fallback only uses `(PRODUCT_ID, product_instance)` (byte 0-2). That fallback isn't unique: on this coach, `product_instance` turned out to be a shared generic default across the *entire* physical Unity module (every unconfigured/FUNCTION_NAME=0 device on the board reports the identical `PRODUCT_ID=232, product_instance=42`, regardless of which peripheral category -- DIMM/LATCH outputs, tank inputs, water heater outputs, generator I/O, etc.), so it doesn't distinguish individual unconfigured ports at all, only "this coach's one Unity module" as a whole.

**Real finding: `device_instance` is a sequential, gapless counter, scoped per DEVICE_TYPE across the whole physical module** -- not per connector bank, and not a fixed silkscreen/terminal number. Confirmed with two independent real-hardware cross-checks (2026-08-22), both against a real, user-supplied physical wiring inventory for this coach specifically (every connector on this coach's Unity X270D board, by bank and connected/not-connected status) used as ground truth:

1. **`LATCHING_RELAY_TYPE_2` (device_type 30) group**: querying every device of this type on the board (`list_unconfigured.py` plus known named devices) found a *complete, gapless* sequence `device_instance` 1 through 9 -- 1-2 = two already-named non-dimmable lights, 3-6 = four unconfigured, 7 = Water Pump (named), 8 = unconfigured, 9 = Tank Heater (named). Cross-referencing this ordering against this coach's own connector-bank listing (DIMM/LATCH OUT's 4 latching positions, then Water Heater's 2, then Pump's 3 = 4+2+3=9, matching exactly) predicts position 8 = the Fuel Pump. Independently confirmed: that exact device (instance 8) read `15.0A` on PID 164/165 (`SOFTWARE_FUSE_RATING_AMPS`/`MAX`, 16.16-fixed-point, `0x0F0000/65536`), matching the Fuel Pump's real 15A rating from this coach's wiring sheet -- and no other candidate in the group read anything but 5.0A. Two independent signals (position-in-sequence, and a real physical rating value) agreeing on the same device is real corroboration, not just one inference.
2. **`TANK_SENSOR` (device_type 10) group**: the 4 already-configured tanks occupy `device_instance` 1-4; the 4 unconfigured ones pick up at 5-8 with zero gap. A second, independent signal confirms a sub-split within the unconfigured four: `capabilities_raw` is `0x04` for all 4 configured tanks (all "Holding" type) *and* for two of the unconfigured ones, while the other two unconfigured ones read `0x05` -- matching this coach's wiring sheet's Holding (positions 5-6) vs. LP/Fuel (positions 7-8) split exactly.

**Candidate identification for this coach's board (2026-08-22, real addresses from this coach, position-in-sequence unless noted otherwise):**

| device_instance | Type | Address (at capture time) | Identity | Status |
|---|---|---|---|---|
| 3 | LATCHING_RELAY_TYPE_2 | `0x11` | DIMM/LATCH output 7 | **Confirmed** -- `relay_blip.py`, real relay energized, physically observed by the user (2026-08-22) |
| 4 | LATCHING_RELAY_TYPE_2 | `0xE8` | DIMM/LATCH output 8 | **Confirmed** -- same method (2026-08-22) |
| 5 | LATCHING_RELAY_TYPE_2 | `0x12` | Water Heater (Elec or Gas -- pair not individually resolved) | Candidate, position-in-sequence only |
| 6 | LATCHING_RELAY_TYPE_2 | `0xF3` | Water Heater (Gas or Elec) | Candidate, position-in-sequence only |
| 8 | LATCHING_RELAY_TYPE_2 | `0xD4` | Fuel Pump | Cross-validated (fuse-rating PID), not physically blip-tested |
| 5 | TANK_SENSOR | `0xE5` | Tank position 5 (Holding) | Candidate, position-in-sequence + capabilities-byte match |
| 6 | TANK_SENSOR | `0x16` | Tank position 6 (Holding) | Candidate, position-in-sequence + capabilities-byte match |
| 7 | TANK_SENSOR | `0xE7` | Tank position 7 (LP/Fuel) | Candidate, position-in-sequence + capabilities-byte match |
| 8 | TANK_SENSOR | `0x69` | Tank position 8 (LP/Fuel) | Candidate, position-in-sequence + capabilities-byte match |

Addresses are volatile (see "Address (In)Stability" below) -- valid only as of the capture they came from, must be re-resolved live before any write. The two confirmed rows validate the `device_instance`-sequencing method itself, not just those two specific devices -- real evidence the same reasoning is sound for the still-unconfirmed rows too, though each one individually remains "candidate" until checked the same way.

**Certain, no inference needed** (only one candidate of that type exists in the unconfigured pool, matching exactly one physical description in this coach's wiring sheet): `0x74` (`MOMENTARY_H_BRIDGE_TYPE_2`) = the one unconnected `30A Rev` output pair. `0xF0` (`GENERATOR_GENIE`) and `0x8A` (`HOUR_METER`) = the generator interface -- this coach's wiring sheet's 6 individual generator wires apparently aren't 6 separate CAN devices, just these two.

**Not a physical port at all:** `0x0E` reports `device_type_raw=39` = `CHASSIS_INFO` (confirmed against the `DeviceType` enum, `can_link/types.py`) -- a system-level status device, not something a user would ever assign a name to.

**Unresolved:** this coach's wiring sheet's "Configurable Inputs" bank (8 positions, 3 wired-but-uncommissioned) has no matching device type anywhere in the unconfigured pool -- no `SWITCH`(8)/`GENERIC`(1)/`TOUCHSCREEN_SWITCH`(9)-typed device appears at all across two separate 20s listen windows that reliably caught everything else. Working theory, **not confirmed**: input pins may not broadcast their own `DEVICE_ID` the way output relay circuits do (which exist as provisioned firmware objects regardless of wiring) -- but this has not been tested or sourced, it's just the least-bad explanation for the data collected so far.

**New tool: `src/tools/list_unconfigured.py`** (2026-08-22) -- purely passive, doesn't even claim a CAN address (stricter than `pid_probe.py`). Listens for DEVICE_ID broadcasts, resolves a reference device's `(PRODUCT_ID, product_instance)` to filter the unconfigured pool to "this board," and optionally resolves any number of `--compare-key` known devices, printing full `DeviceIdentity` fields (including `device_instance`) for both groups so they can be compared directly. This is what produced the tables above.

### Local Switch Input Discovery (PID 238, `ON_OFF_INPUT_PIN`) -- Real Evidence, Not Yet Fully Confirmed

Investigating whether a "Configurable Input" position could be identified via the output device it's wired to (the user's hypothesis, since no input-typed device broadcasts on its own): found `PID 238 = ON_OFF_INPUT_PIN`, `PID 146 = INPUT_SWITCH_TYPE`, `PID 241 = INPUT_PIN_COUNT` in the decompiled catalog. All three were already present in Kitchen Island Light's real `--list-pids` output from earlier in this investigation.

**Real data (2026-08-22), this coach:** read PID 238 against all 12 currently-configured devices (every device_class, including tanks -- a completely different DEVICE_TYPE from relay/dimmer outputs). 11 of 12 read exactly `0`. Water Pump alone read `1`. `PID 241` (`INPUT_PIN_COUNT`) read `8` identically on every device tested (both a tank and a light), consistent with it being a board-wide constant rather than per-device -- and it happens to numerically match this coach's wiring sheet's Configurable Inputs bank having exactly 8 positions.

**Interpretation (well-supported inference, not proven):** `PID 238` records which Configurable Input position (if any) is wired as a given device's local on/off switch, `0` meaning none assigned. Water Pump's value of `1` matches this coach's wiring sheet's own note that Configurable Input position 1 is "probably Water Pump switch." This is a real, consistent pattern across 12 data points all pointing the same direction (not a single lucky match), and it's supported across two different DEVICE_TYPEs (tanks and relays both return real `0` values rather than an error, meaning this PID isn't relay-specific). It is **not** independently confirmed by documentation or a physical test (e.g. watching the value change in response to a real rewiring) -- that's the one thing that would move this from "well-supported inference" to "confirmed."

**`PID 146` (`INPUT_SWITCH_TYPE`) -- meaning genuinely unknown.** Catalog declares it a plain `UINT8` with no enum reference (`new PID(146, "INPUT_SWITCH_TYPE", new Formatter(FORMAT.UINT8, "${0:X2}"), 2)`); a repo-wide search for a switch-type enum in the decompiled source found nothing relevant. Both devices tested so far (Water Pump, Kitchen Island Light) read the identical value (`3`), so there isn't even empirical contrast to reason from yet. Explicitly unresolved -- do not guess at what values mean without either documentation or observed variation.

**New tool: `src/tools/relay_blip.py`** (2026-08-22) -- manual, `--confirm`-gated, sends a real relay ON/OFF COMMAND (via `command.build_relay_command()`, the same real-hardware-proven builder every Phase 3 relay command uses) directly to a raw address over a REMOTE_CONTROL session, bypassing `command_gate.py` entirely (an unconfigured device has no `device_class`/`commands_enabled` to gate on). Built to physically confirm the `device_instance`-based candidate mapping above (energize the relay, watch a multimeter on the target terminal). **Run for real (2026-08-22)** against both DIMM/LATCH output 7 and output 8 -- both confirmed correct, see the table above. Its first run also surfaced a real bug: the original 5-second default hold matched the session's own 5s-of-silence auto-expiry exactly, so the OFF command arrived too late and the relay stayed on (output 7 briefly stuck on, until manually turned off with a short-hold re-run) -- fixed (`DEFAULT_HOLD_SECONDS` reduced to 2.0, hard-capped at 4.0), see CHANGELOG.md.

### DEVICE_STATUS Broadcast (MessageType 3, ID = `0x300 | addr`, ~1Hz idle / ~333ms on change)

Interpretation requires already knowing the device's DEVICE_TYPE from its DEVICE_ID broadcast.

**Tank sensor (DEVICE_TYPE 10):**

| Byte | Field |
|------|-------|
| 0 | FillLevel % (mask `0x7F`) |
| 1 | BatteryLevel % |
| 2 | MeasurementQuality % (255 = n/a) |
| 3 | X acceleration, signed, ÷1024.0 = G (-128 = unknown) |
| 4 | Y acceleration, same format |
| 5 | Alert: bit7 = active, bits0-6 = count |
| 6-7 | DTC (uint16 BE) |

**Relay/motor shared struct** (6 bytes — same layout for latching relays AND H-bridge motors; device class comes from DEVICE_TYPE, not this payload):

| Byte | Field |
|------|-------|
| 0 | bits0-3 = OutputState (OFF_STOP=0, ON=1, FORWARD/EXTEND=2, REVERSE/RETRACT=3); bit5 = fault/clear-required latch; bit6 = reverse-allowed (1=allowed, per decompiled firmware "bit6=0 means REVERSE_COMMAND_NOT_ALLOWED"); bit7 = forward/on-allowed (1=allowed, same source) |
| 1 | Position % (0xFF = not supported) |
| 2-3 | Current draw, uint16 BE, ÷256.0 = Amps (0xFFFF = not supported) |
| 4-5 | DTC (uint16 BE) |

**Dimmable light (DEVICE_TYPE 20, 8 bytes):**

| Byte | Field |
|------|-------|
| 0 | Mode (0=off, 1=on/dimming, 2=blink, 3=swell) |
| 1 | MaxBrightness (0-255) |
| 2 | AutoOffMinutes (0=disabled) |
| 3 | CurrentBrightness (0-255) |
| 4-5 | T1 cycle time ms (uint16 BE) |
| 6-7 | T2 cycle time ms (uint16 BE) |

### PID_READ_LIST (REQUEST type 128, request code `0x10`)

No session required. Enumerates every PID a specific device actually supports, rather than guessing numbers from a catalog that may cover a different Lippert product line -- added 2026-08-21 after exactly that happened (see below). Confirmed directly against the decompiled server handler (`Request10PidReadList`, `assembly_0079_Sharp.decompiled.cs`), not guessed:

- Request payload: `page` as uint16 BE. `page=0` is special; `page=1,2,3,...` are subsequent pages.
- Replies are **always exactly 8 bytes**, padded with `PID=0/Flags=0` placeholder entries when there isn't enough real data left to fill the frame -- callers must stop once they've collected `total_count` real entries (from page 0's header), not when entries run out, since a padding entry isn't distinguishable from a real `PID=0` by shape alone.
- Page 0 reply: `[echo(2)=0x0000][total_count(2)][reserved(1)=0x00][entry?]` -- 5 header bytes leave room for exactly 1 `[PID(2),Flags(1)]` entry.
- Page N>0 reply: `[echo(2)=page][entry][entry?]` -- 2 header bytes leave room for exactly 2 entries.
- `can_link/pid_client.py`: `build_pid_list_request()`, `parse_pid_list_reply()` (`PidListPage`/`PidListEntry`). `src/tools/pid_probe.py --list-pids` drives the full pagination loop against a real device and prints every `(PID, Flags)` pair it actually supports.

### PID_READ_WRITE (REQUEST type 128, request code `0x11`)

No session required for reads. Request payload: PID as uint16 BE.

**Error replies are confirmed** (2026-08-21, real hardware, not guessed): `[0x0000][echoed requested PID(2)][RESPONSE code(1)]`, 5 bytes total. The leading `0x0000` and the `RESPONSE` code byte were not previously known -- found by cross-checking a real reply's trailing byte (`0x04`) against the decompiled `enum RESPONSE : byte` (`assembly_0079_Sharp.decompiled.cs`, sequential from 0: `SUCCESS=0, REQUEST_NOT_SUPPORTED=1, BAD_REQUEST=2, VALUE_OUT_OF_RANGE=3, UNKNOWN_ID=4, ...`), which matches exactly, including every other named value found earlier (`READ_ONLY=7`, `SESSION_NOT_OPEN=14`, etc.) -- see `pid_client.RESPONSE_CODE_NAMES` for the full table.

**Success replies are now confirmed** (2026-08-21, `--read-pid 161` against real hardware -- see below): the originally-documented shape (PID echoed at offset 0 as uint16 BE, directly followed by the value, width from DLC) is correct as-is. Real reply for PID 161: `00 A1 01` -- `0x00A1` = 161 (echoed PID), `0x01` (value), matching `parse_pid_reply()`'s existing unmodified logic exactly (`echoed_pid=161, raw_value=0x1, value_byte_count=1`). No code change was needed; this closes out the "unconfirmed" caveat that stood since 2026-08-19 (no PID_READ_WRITE traffic had ever appeared in a capture before now).

**Confirmed real finding, root cause of the Kitchen Island Light dimming problem (2026-08-21), this coach:** cross-referencing the 97 PIDs discovered via `--list-pids` (see above) against the full PID catalog in `assembly_0080_Sharp.decompiled.cs` (`new PID(<num>, "<NAME>", ...)`, 449 named entries) identified **PID 161 = `SIMULATE_ON_OFF_STYLE_LIGHT`** (`Formatter.UINT48`, session requirement `2` = DIAGNOSTIC per the PID class's 4th constructor argument) -- a name that matches the user's own description ("configure a dimming capable output as latching") almost exactly. Reading it against both real lights confirmed the hypothesis directly:

| Device (this coach) | PID 161 raw value |
|---|---|
| Kitchen Island Light (misbehaving -- dimming always snaps to 100%) | `0x01` (true) |
| Kitchen Pendants Light (working dimmer) | `0x00` (false) |

Both devices report PID 161 with identical `flags=0x07` (readable + writable), and both are otherwise identical (same 97-PID supported set, same flags on every other PID) -- the only difference between the working and non-working dimmer in this entire supported-PID set is this one value. This is real, direct evidence, not a guess: flipping PID 161 to `0` on Kitchen Island Light was the concrete hypothesis for the write path, confirmed below. Two other DIAGNOSTIC-gated PIDs turned up nearby in the catalog that were considered and set aside as worse fits: `PID 387 MOMENTARY_HBRIDGE_CIRCUIT_ROLE` (H-bridge/awning context, not lighting) and `PID 430 MAINTAIN_STATE_THROUGH_POWER_CYCLE` (persistence-across-power-cycle behavior, unrelated to dimming vs. latching).

Note the formatter mismatch: the decompiled catalog declares PID 161 as `UINT48` (6 bytes), but the real reply is only a 1-byte value (3-byte total payload). `Formatter` in the decompiled source appears to govern only how the LippertConnect app *displays* a value, not the actual wire width -- consistent with `parse_pid_reply()` already being DLC-driven rather than assuming a fixed width per PID.

**How this was found (2026-08-21, worked example, this coach):** `pid_probe.py --read-pid 183 --read-pid 451` (`DEVICE_TYPE`/`LOAD_TYPE`, from the decompiled catalog) against three different real devices -- two dimmable lights and, as a deliberate cross-check, a tank sensor with an independently-known, different real `DeviceType` -- returned byte-identical replies across all three (`00 00 00 b7 04` for PID 183, `00 00 01 c3 04` for PID 451, regardless of device). Identical results across a tank and two lights ruled out "these are real per-device values, the parser's just misreading them" -- real `DEVICE_TYPE`/`LOAD_TYPE` data would differ between a tank and a light. The `0x04` trailing byte matching `RESPONSE.UNKNOWN_ID` exactly resolved it: neither PID is recognized by any of these three devices. `PID_READ_LIST` exists specifically so this doesn't need to happen again -- ask the device what it supports instead of guessing from a catalog that may not apply.

Key PIDs: 43 = BATTERY_VOLTAGE (UINT32, ×1/65536, volts), 144 = AUX_BATTERY_VOLTAGE (same format). Poll every ~30s against candidate node addresses (DEVICE_TYPE CHASSIS_INFO/GENERATOR_GENIE/LEVELER commonly expose battery voltage). Neither has been read from real hardware either -- same caveat applies, and given PID 183/451 turned out unsupported on this hardware family, these should be verified with `--list-pids` before assuming they apply here either.

Reconfiguration-relevant PIDs (2026-08-21, decompiled LippertConnect source -- see "PID Reconfiguration" design decision above for the full research): 4 = `IDS_CAN_FUNCTION_NAME` (UINT16), 5 = `IDS_CAN_FUNCTION_INSTANCE` (UINT8) -- **confirmed supported** on this hardware family via `--list-pids` (both present in the real 97-PID set for Kitchen Island Light and Kitchen Pendants Light); a write at each PID's own declared width **failed real-hardware testing 2026-08-23**, root-caused to the universal 6-byte write-width requirement (see "PID Writes" below) rather than anything wrong with these two PIDs specifically -- not yet retried at the corrected width. 161 = `SIMULATE_ON_OFF_STYLE_LIGHT` -- **confirmed supported, confirmed as the real root cause, and confirmed fixed**: writing `0` (6-byte `UINT48` width) resolved the Kitchen Island Light latching-instead-of-dimming problem end-to-end, verified both on the wire and physically (see "PID Writes" below). 183 = `DEVICE_TYPE`, 451 = `LOAD_TYPE` are **confirmed NOT supported** on this Unity X270 hardware (see above) -- kept as named constants in `pid_client.py` for citation/reference only.

### PID Writes -- Confirmed Real (2026-08-21)

Same request code as a read (`0x11`), distinguished purely by payload length -- confirmed by decompiled source, now also confirmed by a real successful write. Requires the PID's own required session to be open first (`can_link/session.py`, `SessionClient(session_id=...)`); `can_link/pid_client.py` itself has no session awareness, it only builds/parses payload bytes.

**Value width is always 6 bytes (`UInt48`) -- universal across every PID, not derived from the target PID's own declared `Formatter` type.** This was first suspected from PID 161 alone (see below) but that was ambiguous, since PID 161's own declared `Formatter` also happens to be `UINT48` -- indistinguishable from "match the PID's declared type" until PID 4/5 were tried with their own genuinely different declared widths (`UINT16`/`UINT8`) and both failed at those widths. **Confirmed directly (2026-08-24) by reading the real client code**, not inferred: the decompiled LippertConnect `PidClient.WritePidAsync(AsyncOperation, IDevice, PID, UInt48 value, ISessionClient)` (`assembly_0079_Sharp.decompiled.cs`) takes `value` typed as `UInt48` for every single call site, and builds the request as `PAYLOAD.FromArgs(PID.op_Implicit(pid), value)` -- `PAYLOAD.FromArgs` (`assembly_0078_Sharp.decompiled.cs`) dispatches purely on each argument's C# runtime type (`UInt48` always serializes to exactly 6 bytes), with no per-PID branching anywhere in the write path. The PID's declared `Formatter` (`UINT16` for PID 4, `UINT8` for PID 5, `UINT48` for PID 161, etc.) governs the app's own *display* rendering only, never the wire write width. The takeaway: reads may arrive DLC-truncated (leading zero bytes omitted, per `parse_pid_reply()`'s existing docstring) and vary in length PID-to-PID, but every write's value field is unconditionally 6 bytes -- `build_pid_write_request()`'s default changed to reflect this (2026-08-24; previously defaulted to 1, which was simply wrong for anything except a value that happened to fit and be accepted anyway).

**Two real, distinct write failure shapes were observed (2026-08-23) from using the wrong (declared-Formatter-derived) width, both on real hardware, both fixed the next day by switching to the universal 6-byte width:** attempting to write PID 4 (`FUNCTION_NAME`) with a 2-byte value (matching its declared `UINT16`) got a reply the device's own echoed-PID-at-offset-0 shape marks as `SUCCESS` -- but a read-back immediately after showed the value never actually changed, and a raw `candump` capture confirmed the "success" reply was byte-identical to a plain unwritten read of the same PID, not a real acknowledgment of anything. Attempting to write PID 5 (`FUNCTION_INSTANCE`) with a 1-byte value (matching its declared `UINT8`) instead got an explicit, distinct 1-byte `RESPONSE.BAD_REQUEST` (`02`) -- shorter than the 5-byte read-error shape below, and clearly not an echo of anything. Both are explained by the same root cause (wrong value width on the wire), manifesting two different ways depending on how the device's parser happened to fail on a too-short payload. Neither had anything to do with the value itself -- a nonzero `FUNCTION_INSTANCE` value got the identical `BAD_REQUEST` at the wrong width, ruling out "0 is a reserved/unwritable sentinel value" as an explanation.

**Write error-reply shape (`BAD_REQUEST` case) is shorter than a read's confirmed error shape:** a malformed-width write can get back a single raw byte (`02`, matching `RESPONSE.BAD_REQUEST`) -- not the 5-byte `[0x0000][echoed pid(2)][response(1)]` shape confirmed for read errors above. The decompiled `PidClient` response-matching predicate (same file as `WritePidAsync` above) also recognizes a third, previously-undocumented 7-byte error shape (`uINT==0, rx.Length==7`, response code still at offset 4) with 2 trailing bytes whose meaning is unconfirmed -- not yet seen on real hardware. Only the 1-byte and 5-byte shapes have been observed for real; other write error codes may have other shapes, unconfirmed.

**Write success-reply shape matches a normal read reply exactly**, confirmed directly: writing PID 161 = 0 (6 bytes) got back `00 a1 00` -- `parse_pid_reply()` decodes this as `echoed_pid=161, raw_value=0`, i.e. the same DLC-truncated echo-plus-value shape as any successful read. No new parser was needed; `pid_write.py` reuses `parse_pid_reply()` as-is for both the write RESPONSE and the follow-up read-back.

**End-to-end real-hardware confirmation (2026-08-21), this coach:** `src/tools/pid_write.py` (new, `--confirm`-gated, never wired into any production path) opened a DIAGNOSTIC session against Kitchen Island Light (`function_name=38,function_instance=0`), wrote PID 161 = 0 (6-byte `UINT48` width), got `RESPONSE.SUCCESS`, and a read-back confirmed the new value. The user then confirmed the fix physically: Kitchen Island Light dims correctly now, and the OneControl phone app itself renders it as a dimmer after an app restart (the app reads this same PID to decide how to display the device). This closes out the Kitchen Island Light investigation and is the first real confirmation that this project can successfully reconfigure a device, not just read one.

### PID_GET_PROPERTIES (REQUEST type 128, request code `0x12`)

No session required (read-only, same as PID_READ_WRITE reads). Request payload: PID as uint16 BE, identical shape to a PID_READ_WRITE request (`pid_client.build_pid_properties_request()` just delegates to `build_pid_read_request()`).

**Error replies are confirmed** (2026-08-21, same real-hardware run as PID_READ_WRITE above): `[echoed requested PID(2)][RESPONSE code(1)]`, 3 bytes total -- no leading `0x0000` field here, unlike PID_READ_WRITE's error shape. **Success replies remain unconfirmed**: the decompiled source confirms the reply is "PID + Flags + SESSION_ID" as a triple but not the exact byte widths for a real success case. `pid_client.parse_pid_properties_reply()` is deliberately defensive about this: it always exposes the raw trailing bytes untouched, and only best-effort-decodes `session_id` from the last 2 bytes (the one width actually confirmed -- `SESSION_ID` is a 16-bit value in the decompiled `SESSION_ID` class) with everything before that treated as `flags` of whatever width is left over -- this heuristic hasn't been exercised against a real success reply either. Used by `src/tools/pid_probe.py` to check whether a given PID is actually reported as writable, and which session it requires, before ever attempting a write.

### SESSION_ID Catalog (decompiled `SESSION_ID` class, 2026-08-21)

Every PID write requires a specific session, not necessarily `REMOTE_CONTROL` -- confirmed by the decompiled `PID` class carrying a per-PID `Write_SessionId` field. Five real sessions, each with its own TEA cypher constant (the single per-session parameter fed into the same `tea_transform()`-shaped algorithm this project already implements in `session.py` -- confirmed by numeric cross-check: the decompiled `REMOTE_CONTROL` cypher converts to exactly `0xB16B00B5`, matching `session.py`'s already real-hardware-validated `TEA_SESSION_CONSTANT`):

| SESSION_ID | Name | Cypher (hex) | Used for |
|---|---|---|---|
| 1 | MANUFACTURING | `0xB16BA115` | manufacturing features |
| 2 | DIAGNOSTIC | `0xBABECAFE` | diagnostic tool features -- gates PID 4/5/183/451 writes |
| 3 | REPROGRAMMING | `0xDEADBEEF` | reprogramming a device |
| 4 | REMOTE_CONTROL | `0xB16B00B5` | **confirmed real** -- already implemented in `session.py`, used for COMMAND frames |
| 5 | DAQ | `0x0B00B135` | DAQ features |

REMOTE_CONTROL (4) and DIAGNOSTIC (2) are both implemented (`session.py`'s `SessionClient` takes a `session_id` param, defaulting to REMOTE_CONTROL). DIAGNOSTIC is now real-hardware-confirmed too (2026-08-21, see "PID Writes" above) -- the `0xBABECAFE` cypher constant, decompiled-only until this point, produced a key the device accepted. MANUFACTURING (1), REPROGRAMMING (3), and DAQ (5) remain decompiled-only/unconfirmed and unused by this project.

### Session Handshake (SESSION_ID 4, "REMOTE_CONTROL")

Required before any COMMAND (130) frame is honored.

1. REQUEST 66 (0x42) SESSION_REQUEST_SEED, payload `[00 04]` → device replies RESPONSE 66 with 32-bit seed.
2. Transform seed via 32-round modified TEA: `k = 0xB16B00B5`, `d = 0x9E3779B9` (delta), magic constants `1131376761`, `1919510376`, `1948272964`, `1400073827`.
   ```python
   for i in range(32):
       v += (((k << 4) + 1131376761) ^ (k + d) ^ ((k >> 5) + 1919510376))
       k += (((v << 4) + 1948272964) ^ (v + d) ^ ((v >> 5) + 1400073827))
       d += 0x9E3779B9
   ```
   (all arithmetic mod 2^32) — resulting `v` is the key.
3. REQUEST 67 (0x43) SESSION_TRANSMIT_KEY, payload `[00 04 <key32>]` → device replies session-open.
4. Send COMMAND (130) frame(s).
5. REQUEST 69 (0x45) SESSION_END.

Session auto-expires after 5s of silence.

### Command Payload Shapes — Do Not Conflate

- **Relay commands** (lights-via-relay, pump, water heater): command mode (OFF=0, ON=1, CLEAR_LATCH=3) goes in the CAN ID's MessageData byte. **Payload MUST be empty (0 bytes)** — any payload bytes cause silent, un-NAK'd discard. Confirmed 2026-08-20 against two real relay commands (`samples/capture.log`, msg_data=0x00/0x01, empty payload).
- **Dimmable light commands, granular**: MessageData byte = 0; 8-byte payload = `[mode, brightness(0-255), auto_off_minutes, t1_hi, t1_lo, t2_hi, t2_lo, reserved]`. **Brightness is a raw 0-255 byte, not a 1-100 percentage** — this corrects the original community-sourced documentation (esphome-onecontrol's `IDS-CAN.md` said "brightness 1-100"), which didn't survive contact with real hardware (see below). `auto_off_minutes`/`t1_ms`/`t2_ms` are unconfirmed (always 0 in both real captures used to validate this).
- **Dimmable light commands, plain toggle**: same MessageData=0 / 8-byte-payload shape, but a distinct simplified command the app sends for a plain tap (not a slider drag): `7F 00 00 00 00 00 00 00` turns on at the light's own last remembered brightness (mode=0x7F is a "resume" sentinel, not part of the mode 0-3 enum); `00 00 00 00 00 00 00 00` turns off.

  **Real hardware validation (2026-08-20), two captures, this coach:**
  1. `samples/capture.log`: a plain on/off tap on two different lights (addresses `0x1D`, `0x85`) produced `7F00000000000000` / `0000000000000000` — inconsistent with the documented granular layout in two ways (mode=0x7F is outside 0-3; brightness=0 fails "1-100" either direction). A real relay command in the same capture matched its own documentation exactly, so this looked dimmable-light-specific, not a broader documentation problem.
  2. `samples/dimming_capture.log`: a follow-up capture of an actual brightness-slider drag (light `0xEA`, "Kitchen Pendants Light", FUNCTION_NAME=34) resolved it completely. Five distinct COMMAND payloads, each producing an immediate exact-match `DEVICE_STATUS.current_brightness`:

     | COMMAND payload | DEVICE_STATUS current_brightness |
     |---|---|
     | `7F00000000000000` | 136 (device's own last-remembered level) |
     | `013E000000000000` | 62 (0x3E) |
     | `01B5000000000000` | 181 (0xB5) |
     | `01FF000000000000` | 255 (0xFF) |
     | `0120000000000000` | 32 (0x20) |
     | `0000000000000000` | 0 (off) |

  This confirms: byte0=mode (0=off, 1=on-with-explicit-brightness, 0x7F=on-resume-last), byte1=brightness (raw 0-255, matching `DimmableLightStatus.current_brightness`'s own scale exactly), bytes 2-7 unexercised (always 0 in both captures).

### Address (In)Stability

CAN SourceAddress is a reclaimable pool assignment (confirmed via decompiled `AddressDetectManager`: addresses freed via `GetUnusedDeviceAddress()` after ~5s of silence), not fixed per physical device. Never use raw SourceAddress as a persistent config key — see "Stable-Key Device Discovery" above for the design this drove.

**FUNCTION_NAME, by contrast, is confirmed device-owned and stable** (2026-08-20, decompiled `DeviceInstanceManager.GetAvailableDeviceInstanceClaim`: Lippert's own gateway re-associates a reconnecting device by `FunctionKey` before falling back to address). Don't conflate the two -- SourceAddress churns across power cycles, FUNCTION_NAME does not (barring a deliberate rename, see "Stable-Key Device Discovery" above).

### Address Claiming

Not documented by any of the three community source repos -- decoded directly from a real captured OneControl power-cycle/reconnect (`samples/poweroutage_capture.log`, gitignored, 2026-08-20, this coach). 32 real devices claimed 32 distinct addresses across this capture with zero collisions.

**Claim frame:** standard (11-bit) CAN ID `0x000` (not the claimant's own address). 8-byte payload: `[candidate_address(1), identity_tail(7)]`. Example, this coach's awning motor's real claim:
```text
000#2A1E000000302B3E
```
`candidate_address=0x2A`, `identity_tail=1E 00 00 00 30 2B 3E`.

**Steady-state announcement:** ~1.0s after the claim frame (matches the decompiled firmware's `ADDRESS_CLAIM_TIMEOUT` exactly), the device begins normal broadcasting, including a NETWORK (message type 0) broadcast at ~1Hz whose payload is `[0x00, *identity_tail]` -- same tail, leading byte changed from the candidate address to `0x00`:
```text
02A#001E000000302B3E   (1.002939s after the claim above)
```

**identity_tail is not per-device-unique.** 29 of the 32 real claims in this coach's capture shared one identical tail (`1E000000302B3E`, presumably the common Unity relay/light board firmware); only 3 distinct tails appeared across every device model on the coach. This project has no real hardware identity to reuse, so it generates its own synthetic 7-byte tail via `os.urandom(7)`, persisted once (`ConfigManager.get_or_create_bridge_identity_tail()`) -- not an attempt to impersonate any real device model.

**This bridge's own identity:** `DeviceType.ONECONTROL_APPLICATION` (34) / `FUNCTION_NAME` 1 ("Diagnostic Tool") / self-assigned `PRODUCT_ID=0xA0FF`. Address `0x00` is never a valid candidate -- see also "Address Claiming (Phase 3)" under Design Decisions above for why (this bridge's own NETWORK broadcast from `0x00` would collide on-wire with the claim-frame CAN ID itself).

---

## Config Format

```json
{
  "can_interface": "vecan1",
  "pid_poll_interval_sec": 30,
  "address_expiry_sec": 8,
  "bus_outage_threshold_sec": 12,
  "stale_threshold_sec": 300,
  "restart_min_delay_sec": 30,
  "restart_max_delay_sec": 300,
  "log_level": "INFO",
  "devices": [
    {
      "stable_key": "function_name=32,function_instance=1",
      "friendly_name": "Kitchen Light",
      "expose": true,
      "device_class": "relay_light",
      "commands_enabled": false,
      "group": "Kitchen"
    }
  ],
  "bridge_identity_tail": "1e00000030abcd"
}
```

Valid `device_class` values (`dbus_bridge/config_manager.py::VALID_DEVICE_CLASSES`): `tank`, `relay_light`, `dimmable_light`, `relay_pump`, `relay_water_heater`, `motor_status`. `device_class` is user/operator-confirmed at config time, never auto-inferred — `routing.route_device_id()` cross-checks it live against the observed DeviceType before creating a service, and `command_gate.py`'s command safety gate does the same before sending anything.

`ConfigManager.add_device()` defaults `expose` and `commands_enabled` both to `False` — adding a device to config must never implicitly turn it on for display or for commands. `commands_enabled` (Phase 3) is a second, independent flag: a device can be exposed (visible, read-only) without ever being commandable. `discovered_devices.json` (gitignored, next to `config.json`) records stable keys seen on the bus but absent from config, purely for the user's review; being in that file never causes exposure.

`bridge_identity_tail` (Phase 3, top-level, not per-device) is this bridge's own synthetic 7-byte CAN identity, hex-encoded, generated once via `os.urandom(7)` and persisted (`ConfigManager.get_or_create_bridge_identity_tail()`) -- see "Address Claiming" above.

`group` (switch-kind devices only, added 2026-08-21) mirrors `Settings/Group` on the D-Bus side -- see the "GUI panel sort/group mechanics" note under Switch Paths below. Defaults to `""` (its own panel); `ConfigManager.get_device_group()` never returns `None`, since the value is written directly to a D-Bus path that expects a string. Editable from either end -- the Cerbo GUI's own per-output settings page, or `manage-devices` -- and kept in sync via `SwitchService.on_group_change`/`Publisher._save_group()`, the same pattern as `friendly_name`.

---

## D-Bus Service Layer (Phase 2-3)

### Service Naming and Device Instances

Service name suffixes are derived via `device_mapping.stable_id_for()` (`zlib.crc32` of the stable key's config string, NOT Python's builtin `hash()` -- see "Stable D-Bus Identifiers Across Restarts" under Design Decisions above), modulo 10000 -- collision-tolerant since a service *name* collision would just mean two D-Bus service names look similar, not actually collide (10000-value space, unlikely with realistic device counts).

Device instances use a smaller, per-kind range and DO need to be collision-free (Venus OS ties GUI customization -- position, name -- to instance number, and a real collision was hit in production with as few as 4 tank devices at modulo 100). See `device_mapping.assign_device_instance()`: a candidate is derived the same way (`stable_id_for(key, modulo=100)` + a per-kind base offset) but then checked against every other already-assigned instance of the same kind and linearly probed forward to a free slot if it collides. The result is persisted to `config.json` (`ConfigManager.set_device_instance()`/`get_device_instance()`) the first time it's assigned and never recomputed after -- an instance must never move once a device has one, so future runs always return the persisted value regardless of what `stable_id_for()` would compute fresh.

| Kind | Service name pattern | Instance range |
|------|----------------------|-----------------|
| tank | `com.victronenergy.tank.onecontrol_<id>` | 20-119 |
| switch | `com.victronenergy.switch.onecontrol_<id>` | 700-799 |
| motor_status | `com.victronenergy.genericstatus.onecontrol_motor_<id>` | 800-899 |

Self-assigned `ProductId` values (not official Victron IDs, since this isn't an official Victron product): tank=`0xA000`, switch=`0xA001`, motor_status=`0xA002`.

### Tank Paths (`com.victronenergy.tank`)

Confirmed against Victron's own dbus wiki (github.com/victronenergy/venus/wiki/dbus): `/Level` (0-100%), `/Status` (0=Ok, 1=Disconnected), `/FluidType` (enum: 0=Fuel, 1=Fresh water, 2=Waste water, 5=Black water/sewage, 11=Raw water, ...), `/CustomName` (writeable). `/Capacity`/`/Remaining` are intentionally not implemented -- tank capacity isn't derivable from this protocol (see Known Limitations) and wasn't requested. `FluidType` is derived from the stable key's FUNCTION_NAME (`device_mapping.fluid_type_for()`); unconfigured/unnamed tanks default to 11 (Raw water) rather than the misleading default of 0 (Fuel).

### Switch Paths (`com.victronenergy.switch`)

Confirmed against `victronenergy/dbus-shelly`'s actual driver source (`shelly_handlers.py`), not assumed. Single channel per service (`/SwitchableOutput/0/...`), since each OneControl device is already its own independently-addressable CAN node (unlike a physical Shelly with multiple relay channels on one unit):

- `/SwitchableOutput/0/State` -- writeable=True. `onchangecallback` always returns False (GUI reverts immediately) but reports the write to `on_command()` (Phase 3), which drives a real command attempt through the safety gate. Real state only ever changes via a confirmed `DEVICE_STATUS`.
- `/SwitchableOutput/0/Status` -- 0=off, 9=on (Shelly's own `STATUS_OFF`/`STATUS_ON` constants -- confirmed real values, not assumed 0/1).
- `/CustomName` (root, writeable) -- added 2026-08-21, was missing (real bug, see CHANGELOG.md): this is what `gui-v2`'s device sort order actually keys on (falls back to `/ProductName` when unset, which is identical across every OneControl switch device). Kept in sync with the two paths below regardless of which one is edited.
- `/SwitchableOutput/0/Name`, `/SwitchableOutput/0/Settings/CustomName` (writeable)
- `/SwitchableOutput/0/Settings/Group` (writeable, default `""`, added 2026-08-21) -- GUI panel grouping, persisted via `ConfigManager.get_device_group()`/`set_device_group()` and editable from `manage-devices` too. Does nothing on this project's own side beyond persisting the value; the grouping behavior itself is entirely `gui-v2`'s.
- `/SwitchableOutput/0/Settings/Type` -- `OutputType.TOGGLE`(1) or `OutputType.DIMMABLE`(2) depending on device_class. `writeable=False` (unlike Shelly) -- see "Switch Service Follows the Real Shelly Driver Pattern" under Design Decisions above for why.
- `/SwitchableOutput/0/Settings/Function` -- `OutputFunction.MANUAL`(2) by default, `OutputFunction.TANK_PUMP`(3) for `relay_pump`. `writeable=False`.
- `/SwitchableOutput/0/Settings/ValidTypes`, `/SwitchableOutput/0/Settings/ValidFunctions` -- bitmasks of just the one fixed type/function.
- `/SwitchableOutput/0/Dimming` -- only added for `device_class="dimmable_light"`, 0-100%, derived from the decoded `current_brightness` (0-255) via `round(current_brightness / 255 * 100)` on read; writeable=True (Phase 3), converted back to the raw 0-255 scale via `round(pct / 100 * 255)` in `command_mapping.py` on write. A write of 0 is treated as "turn off" (routed to the plain toggle-off command, not brightness=0 with mode=1); 1-100 is "turn on at exactly this percentage".

`OutputType`/`OutputFunction` enum values live in `device_mapping.py` and are the real Venus OS SwitchableOutput API values (cross-checked against `gui-v2`'s QML referencing the same enum, e.g. `VenusOS.SwitchableOutput_Type_Dimmable`) -- not Shelly-specific.

**GUI panel sort/group mechanics (2026-08-21, real-hardware observation prompted this investigation):** confirmed directly against `gui-v2`'s C++ source, not assumed. Sort: `SortedIOChannelGroupModel` (`src/iochannelgroupmodel.cpp`) sorts panels alphabetically by `IOChannelGroup::name()`, which for a single-channel device (no `Settings/Group` set) is the device's own `name()` (`src/device.cpp`) -- `/CustomName` if synchronized and non-empty, else `/ProductName`. Group: a channel only shares a panel with another service's channel if both have an identical, non-empty `Settings/Group` string (`IOChannelGroupModel::addChannelToItsGroup()`); with no `Settings/Group` set, each service gets its own panel keyed by service UID, regardless of name. `victronenergy/dbus-switch`'s `dbus-switch.py` (the real generic-switch reference driver, more directly applicable here than dbus-shelly) registers both a root `/CustomName` and a writeable `Settings/Group` (default `""`, persisted via its own local-settings mechanism) -- confirming both paths are meant to be driver-owned, not something the GUI synthesizes on its own. `Settings/Group` is a real, already-editable field in the Cerbo GUI's own per-output settings page (`PageSwitchableOutput.qml`'s `ListIOChannelGroupField`) -- this project doesn't register that path yet, so there's currently nothing there to edit for a OneControl switch device.

### Motor Status Paths (custom, non-standard service)

`/MotorState` (raw OutputState int), `/PositionPercent`, `/CurrentAmps`, `/FaultLatch`, `/Dtc`, `/CustomName` (writeable). No other writable path exists on this service -- intentional, see the "No Motor Commands" boundary under Design Decisions above.

### Command Flow (Phase 3)

1. A D-Bus write hits `SwitchService._handle_state_write()` or `_handle_dimming_write()` -- runs synchronously on the GLib main loop thread, must return fast. Always returns `False` (unchanged Phase 2 UX); also calls `on_command(stable_key, desired_on, desired_brightness_pct)`.
2. `Publisher._on_switch_command()` (the `on_command` callback): runs `command_gate.evaluate_command_request()` (the cheap early check -- exposed, commands_enabled, supported device_class, `address_table.resolve_for_command()`). Any failure is logged and the function returns -- no frame is ever sent for a refused request. On success, if no command is already in flight for this device, constructs a `CommandAttempt` via `Publisher._send_command()` (its `build_command_frame` closure calls `command_mapping.command_frame_for_switch_write()`), stores it in `self._pending_commands` keyed by stable_key config string, and sends the first frame (`attempt.start()`, a SESSION_REQUEST_SEED REQUEST).
3. Each subsequent RESPONSE frame addressed to this bridge is routed by `Publisher._handle_extended_frame()` to the matching pending `CommandAttempt` (matched by source address -> `_address_to_key` -> pending-commands lookup, with a target-address cross-check as defense in depth), which advances its internal state machine and returns the next frame(s) to send.
4. Immediately after the key-exchange RESPONSE, `CommandAttempt._verify_and_send_command()` re-runs `resolve_for_command()` a second time -- this is the check that actually matters, since it's the last one before a frame with physical effect goes out. A mismatch here sends only SESSION_END, never COMMAND.
5. `Publisher._check_pending_command_timeouts()` (a 500ms GLib timer) aborts any attempt that hasn't advanced within `command_sequencer.DEFAULT_STEP_TIMEOUT_SEC` (2s). A detected bus outage (`address_table.note_bus_activity()` returning `True`) aborts every pending attempt immediately, regardless of state.

**Rapid writes to the same device (coalesced, not queued or refused outright):** a real handshake takes longer than a GUI can generate writes while a brightness slider is being dragged (confirmed on real hardware 2026-08-20 -- refusing every overlapping write as originally implemented meant the light could settle on an early drag position instead of wherever the slider was actually released). If a write arrives for a device with an attempt already in flight, `_on_switch_command()` stores it in `self._queued_commands[key_str]` (device_class, desired_on, desired_brightness_pct), overwriting any previously-queued value for that device rather than accumulating a backlog. `_finalize_command_attempt()` -- reached whether the in-flight attempt succeeded, failed, or timed out -- checks for a queued follow-up and, if present, re-runs `evaluate_command_request()` fresh (not the stale decision from when it was queued) before calling `_send_command()` again. `_abort_all_pending_commands()` (outage handling) clears both dicts, so a real outage never resumes a stale queued write once the bus recovers.

### Reverse Address Lookup

`publisher.py` maintains its own local `source_address -> StableKey` dict (`_address_to_key`), populated from DEVICE_ID broadcasts, so a DEVICE_STATUS frame's source address can be resolved back to which service (if any) should be updated. This is separate from `address_table.py`'s forward `StableKey -> source_address` map, which doesn't expose the reverse direction. There's a small window where a just-reassigned address could momentarily route a DEVICE_STATUS frame to the wrong (stale) key if a new DEVICE_ID hasn't been seen yet for the new occupant -- acceptable for Phase 2 since this only affects passive display (never a command), and DEVICE_ID broadcasts at ~1Hz make the window brief. `address_table.py` itself was left untouched to avoid re-touching Phase 1's already-validated, safety-critical code for a non-safety-critical convenience.

---

## Platform Constraints (Venus OS)

**System:** Venus OS (Victron Energy). **Device:** Cerbo GX MK2. **Base:** Yocto Linux (BusyBox utilities).

### Python Environment

- Python 3.12.x per govee-ble-venus-py's on-device findings (a different project on the same platform, not independently re-checked here) -- this project has run stdlib-only Python extensively on the real Cerbo since Phase 0 with no version-related failures, but the exact version string has never been explicitly confirmed via `python3 --version` on this specific unit.
- No pip available, no external packages installable — stdlib only (per govee-ble-venus-py's on-device findings). This is why this project uses stdlib `socket.AF_CAN`/`CAN_RAW` instead of the third-party `python-can` library. `dbus-starlink` is a documented exception (`pip3 install grpcio protobuf` via its SetupHelper setup script) — tied to a heavier dependency that project needed; not evidence pip is broadly available or needed here.
- `statistics` module NOT available despite being stdlib (per govee-ble-venus-py) — not needed by this project, but avoid it if added later.

### Shell and Utilities

BusyBox applets, not GNU coreutils. Confirmed constraints (from govee-ble-venus-py):

```bash
timeout 60 command       # No timeout applet — use Python time-based loops instead
head -50 file             # Wrong — use: head -n50 file
ps -aux                   # No flags — use: ps | grep name
grep -P '\d+' file         # No Perl regex — use: grep -E '[0-9]+' file
sed -r 's/...//' file      # GNU-only — use: sed -E 's/...//' file
```

### SocketCAN

- **Interface name confirmed: `vecan1`** (not `can0`/`can1` as originally assumed — Victron's own VE.Can naming convention on this Cerbo GX MK2). Update any hardcoded examples/defaults accordingly.
- Traffic decodes cleanly (1514 clean DEVICE_ID frames, no corruption) consistent with the assumed 250 kbit/s bitrate. Bring-up command confirmed and now resolved -- see below.
- Whether `vcan` is available on-device for offline loopback testing — not yet checked.
- **The interface does not reliably survive a Venus OS firmware update** (confirmed on real hardware, 2026-08-21): after updating firmware, `vecan1` still existed (`ip -d link show vecan1`) but was administratively `DOWN` -- whatever had brought it up before (originally a one-time manual `ip link set vecan1 up type can bitrate 250000`, per Phase 0's setup) did not happen again on that boot, and nothing else noticed or fixed it. `bus/socketcan.py::SocketCanBus` now brings the interface up itself (idempotently -- checks the kernel `IFF_UP` flag via `/sys/class/net/<if>/flags` first, never touches an already-up interface) before every connection attempt, rather than assuming this system-level step already happened. This was a deliberate reversal of Phase 0's original design (which explicitly left this as an external, manual/system-level responsibility) -- see "This Project Brings Its Own CAN Interface Up" under Design Decisions above.

### Process Management

- runit (not systemd). Service directories in `/service/`.

```bash
svstat /service/onecontrol-can      # Check status
svc -t /service/onecontrol-can      # Restart
svc -d /service/onecontrol-can      # Stop
svc -u /service/onecontrol-can      # Start
```

- `/data/` persists across reboots and firmware updates; `/service/` does not — symlink recreation must be handled by `rc.local` or the SetupHelper service-install mechanism.

**SetupHelper Update Mechanics** (confirmed 2026-08-20 by reading PackageManager.py / HelperResources/ServiceResources directly):

- `setup install auto`, re-run on an already-installed package, safely handles the restart itself -- `installService()` diffs the run file against what's already at `/service/<name>/run` and, if the service is currently up, sends `svc -t` (clean restart); if down, `svc -u`. **Never manually `svc -d`/`svc -u` around a redeploy** -- extract the fresh files, then just re-run `setup install auto`.
- `INSTALL_FILES` (`installAllFiles()`) is a no-op for this package: it operates on a `fileList`/`fileListVersionIndependent`/`fileListPatched` file we don't have and don't need. That mechanism exists for packages that patch pre-existing Venus OS system files (e.g. GUI QML files) across firmware versions -- not applicable to a fully self-contained package like this one, whose files live entirely under its own `/data/venus-onecontrol-can/` with nothing shared with Venus OS itself. File placement is handled entirely by our own `tar` extraction, not by `setup`.
- Real GitHub-based auto-update (`PackageManager.py`'s `GitHubDownload`/`updateGitHubVersion`) needs: a **public** repo (plain `wget`, no auth), a `version` file starting with `v` (already the convention here), and a `gitHubInfo` file (`user:branch`). It checks `raw.githubusercontent.com/<user>/<repo>/<branch>/version` for the version string and downloads `github.com/<user>/<repo>/archive/<branch>.tar.gz` on mismatch -- ordinary GitHub archive URLs, no Releases/tags required, "latest" is just a branch-naming convention SetupHelper's own author uses, not a platform feature. Deliberately not set up yet -- this project isn't public. See TODO.md.

**Boot-Time / Firmware-Update Package Reinstall** (confirmed 2026-08-21 by reading `reinstallMods` / `PackageManager.py` directly). Package survival across a Venus OS firmware update (which wipes `/service/` but not `/data/`) is meant to be fully automatic:

1. `/data/rcS.local` (persists, called by Venus OS's own boot process) calls SetupHelper's `reinstallMods` on every boot.
2. `reinstallMods` reinstalls the `PackageManager` service itself if it's missing (`/service/PackageManager` gone after a firmware update, same as any other service), then touches `/etc/venus/REINSTALL_PACKAGES`.
3. `PackageManager`'s own service watches for that flag. Before acting on it, it calls `AddStoredPackages()`, which scans every directory directly under `/data/` and re-registers anything with both a `setup` file and a `version` file starting with `v` as a known package -- independent of GitHub, independent of how it was originally installed. This is why a manually-installed, non-public package like this one is still rediscovered correctly.
4. Only then does it call each known package's own `setup` script with `reinstall auto deferReboot deferGuiRestart`, which is what actually reinstalls the `/service/<name>` symlink and restarts it.

Useful when a package doesn't come back after a firmware update: `svstat /service/PackageManager`, `tail -n 150 /var/log/PackageManager/current` (standard runit log location, `PackageManager.py` logs via `logging.basicConfig` with no filename -- stdout/stderr, captured by runit), and `ls /etc/venus/REINSTALL_PACKAGES` (still present means PackageManager hasn't finished/started processing it yet).

### Network

```bash
scp file.py root@<cerbo-host>:/data/venus-onecontrol-can/
ssh root@<cerbo-host> "python3 /data/venus-onecontrol-can/src/tools/candump_logger.py"
```

### Reference Links

- Venus OS Documentation: https://github.com/victronenergy/venus/wiki
- SetupHelper (third-party, kwindrem): https://github.com/kwindrem/SetupHelper

---

## Known Limitations

- Battery voltage is not published in Phase 2 — it's PID-based (request/response), not broadcast, and Phase 2 is passive-only (no bus transmission at all yet, not even a read request). Deferred, low priority per the user's own steer.
- PID battery voltage reads have not been validated against this coach's real traffic yet (deferred, low priority — no PID traffic appeared in the first capture at all). Everything else in v1's decode scope (DEVICE_ID structure, relay/motor status, dimmable light status, tank sensor status, the TEA session handshake) is now confirmed against a real 2026-08-19 capture from this coach.
- The stable-key fallback (PRODUCT_ID, instance) is not always unique in practice: on this coach, 13 of 31 discovered devices report `FUNCTION_NAME=0` and also share an identical `(PRODUCT_ID, instance)`, so they cannot be distinguished from each other by that fallback alone. `device_instance` (a separate DEVICE_ID field, decoded from day one but never previously used) turned out to be a real disambiguator instead -- see the "PID Reconfiguration" design decision and the "device_instance" section under Protocol Reference above for the confirmed mechanism, and TODO.md's "Future Phase — Device Reconfiguration via CAN" for current status. Devices with a real assigned FUNCTION_NAME (tanks, water pump, awning, slide, etc.) are unaffected either way.
- Generator, HVAC, and leveler status decoding are not implemented in v1 (undocumented byte layouts in the source research).
- Dimmable-light command `auto_off_minutes`/`t1_ms`/`t2_ms` (auto-off timer, blink/swell cycle timing) are unconfirmed against real hardware — both real captures used to validate the command format only ever sent zero for these fields. Not needed for Phase 3's scope (a plain brightness percentage).
- Tank capacity (gallons) cannot be read from the bus reliably per prior community findings — must be configured manually if needed, not read live.
