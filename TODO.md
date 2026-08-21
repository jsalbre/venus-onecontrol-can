# TODO

**Version:** 1.8 | **Updated:** 2026-08-21

---

## Phase 0 — Wiring + Raw Capture (done, 2026-08-19)

- [x] Wired the Cerbo GX MK2's spare CAN interface (`vecan1`) to the OneControl bus as the new physical bus end.
- [x] Confirmed frames arrive for both ID widths (`samples/capture.log`, gitignored — 7541 frames, ~48s).
- [x] Captured 4 real TEA session handshakes (8 seed/key pairs) by operating lights from the OneControl app while logging.
- [x] Correlated physical device actions (3 lights on/off, water pump off/on) against log timestamps.

## Phase 1 — Decoder Validation (done, 2026-08-19 — only a deferred low-priority item remains)

- [x] DEVICE_ID structure validated: 1514 broadcasts, 31 devices, zero decode errors.
- [x] TEA cipher validated: all 8 real captured seed/key pairs match exactly (`tests/test_session.py::TeaTransformRealHardwareTests`).
- [x] Relay/motor status (`decode_relay_or_motor`) validated against a real water pump ON/OFF/ON cycle and a relay-driven light ON/OFF cycle.
- [x] Dimmable light status (`decode_dimmable_light`) validated against two real light ON/OFF cycles.
- [x] Tank sensor status (`decode_tank_sensor`) validated: fresh 66%, grey 66%/33%, black 33% all confirmed matching real known levels. Also caught and fixed a real bug in the process (`battery_level_pct=0xFF` "not supported" sentinel wasn't handled).
- [ ] PID battery voltage read — deferred, low priority (nice to have, not needed). None seen in the 48s 2026-08-19 capture (no request code 0x11 traffic at all), but that's not proof nothing on the coach ever polls it — some node could do so at a longer interval than this window covered. No active probe tool planned; will opportunistically check any future longer/natural capture for it instead.
- [x] ~~Investigate CIRCUIT_ID traffic~~ — checked and corrected. Earlier note overstated this: all 1525 CIRCUIT_ID frames in the capture (from all 31 devices) have payload `00000000` with zero variation. It's genuinely all-zero/unused, exactly as the source research describes — it's just broadcast by every node on this coach (~1Hz, alongside DEVICE_ID/DEVICE_STATUS) rather than by only a few. No further investigation needed.

## Phase 2 — D-Bus Publish, Read-Only (done and confirmed on real hardware, 2026-08-20)

- [x] `dbus_bridge/config_manager.py` -- the exposure safety gate (`is_exposed()`), device add/remove/rename, `DiscoveryLog` for unconfigured-device review. Unit tested.
- [x] `dbus_bridge/device_mapping.py` -- device_class <-> DeviceType/service-kind/OutputType/OutputFunction/FluidType mapping, `stable_id_for()` (stable across restarts, unlike builtin `hash()`). Unit tested.
- [x] `dbus_bridge/routing.py` -- pure decision logic (the two-layer safety gate: config-exposed + device-class-matches-live-broadcast) extracted so it's testable despite `publisher.py` requiring dbus/gi. Unit tested, including the specific "motor configured as a light" rejection case.
- [x] `dbus_bridge/backoff.py`, `tank_service.py`, `switch_service.py` (Shelly-pattern `com.victronenergy.switch`, read-only in Phase 2), `motor_status_service.py` (custom, no writable state), `publisher.py` (orchestrator).
- [x] SetupHelper packaging: `setup` script, `services/onecontrol-can/run`, `GUI_V1_NOT_REQUIRED`, `version`.
- [x] Deployed to the Cerbo (venus.local). 4 tank services + water pump switch service registered on D-Bus; the Phase 2 write-rejection safety gate confirmed working live (a real write attempt to `/SwitchableOutput/0/State` was correctly rejected and logged). `discovered_devices.json` populated as expected.
- [x] Found, fixed, and redeployed: a real device-instance collision (two tank services both got instance=86) -- see CHANGELOG.md and `ARCHITECTURE.md`'s "Stable D-Bus Identifiers" note. Confirmed on hardware: all four tank services now have distinct instances (76, 86, 58, 87).
- [x] Visually confirmed in the Cerbo GUI: water pump switch-pane entry correctly reverts on a rejected write (doesn't show a stale "on"); physical OneControl panel toggles reflect in the Venus GUI immediately.
- [x] `enable-device` CLI tool built, tested end-to-end locally, and confirmed working on the real Cerbo (2026-08-20): used to add Tank Heater, Kitchen Island Light, and Kitchen Pendants Light, all with correctly auto-inferred device_class (notably "Tank Heater" correctly landed on `relay_light`, not `relay_water_heater`) and zero device-instance collisions across 4 tank + 4 switch services.
- [x] Fixed a deployment-process bug found in the process: an initial partial-file copy (just `enable-device` itself) broke with `ImportError: cannot import name 'build_addable_list'` since it depends on modules not yet synced. Fixed by always doing a full tarball sync + `setup install auto` (which handles the service restart itself, no manual `svc -d`/`svc -u` needed) -- documented in README.md and `dev-notes/VENUS_OS_CONSTRAINTS.md`.
- [ ] Battery voltage service is not implemented in Phase 2 (deferred, matches the low-priority PID item above -- Phase 2 makes no bus transmissions at all, including reads).

## Phase 3 — Safe Commands (implemented 2026-08-20, not yet rolled out to real hardware)

- [x] Empirically determined the real CAN address-claim procedure via a live power-cycle capture (`samples/poweroutage_capture.log`) rather than hardcoding an address -- see ARCHITECTURE.md's "Address Claiming" design decision. `can_link/address_claim.py` (claim/announce frame codec, `AddressClaimer` state machine, `ActiveAddressTracker`) + `tests/test_address_claim.py` (23 tests, real-capture fixtures).
- [x] Found and corrected a real discrepancy: the community-documented dimmable-light COMMAND byte layout (`brightness 1-100`) didn't match real hardware. A follow-up real capture of an actual brightness-slider drag (`samples/dimming_capture.log`) resolved it -- brightness is a raw 0-255 scale, and a plain on/off tap uses a separate simplified toggle command (`mode=0x7F`/all-zero) distinct from the granular `mode=1,brightness=N` command. `can_link/command.py` fixed and re-tested against both captures.
- [x] `can_link/command_sequencer.py` (`CommandAttempt` -- async TEA-handshake-through-COMMAND state machine, re-verifies `address_table.resolve_for_command()` a second time immediately before building the COMMAND frame) + `tests/test_command_sequencer.py` (13 tests, using the real captured handshake trace as the happy-path fixture).
- [x] `dbus_bridge/command_mapping.py` (device_class -> CanFrame dispatch, including percent<->raw-byte brightness conversion) + `dbus_bridge/command_gate.py` (the layered command safety gate: exposed + commands_enabled + supported device_class + address_table verification) + tests for both.
- [x] `dbus_bridge/config_manager.py`: `commands_enabled_for()`/`set_commands_enabled()`, `add_device(..., commands_enabled=False)`, `get_or_create_bridge_identity_tail()`.
- [x] `switch_service.py`: `/SwitchableOutput/0/State` and (for dimmable lights) `/SwitchableOutput/0/Dimming` both writeable, both still always return `False` (GUI reverts immediately, matching Phase 2's UX) but now report the write upward via `on_command()`.
- [x] `publisher.py`: address claiming on startup + steady-state self-announcement, RESPONSE-frame routing to pending `CommandAttempt`s, a 500ms timeout sweep, and bus-outage-triggered immediate abort of every pending command.
- [x] `enable-device` (renamed `manage-devices` -- see below): optional default-No `commands_enabled` prompt, shown only for commandable device classes, folded into the existing add-device flow rather than kept separate.
- [x] `enable-device` renamed to `manage-devices` and extended with a "Manage existing devices" mode (2026-08-20): rename, toggle `expose`/`commands_enabled`, remove -- all built on `ConfigManager` methods that already existed and were already unit tested (`update_friendly_name`, `set_expose`, `set_commands_enabled`, `remove_device`) but had no CLI caller until now. `device_class` is still never editable, matching the add-flow's existing "inferred, never asked" design. Smoke-tested end-to-end locally (add, rename, toggle both flags, remove).
- [x] Full test suite green (275 tests) and `py_compile` clean across all dbus/gi-dependent modules.
- [x] Post-implementation resource audit (2026-08-20): found and fixed a GLib timer/IO-watch leak across crash-restarts (never removed, compounding forever -- includes real duplicate CAN traffic from the self-announce timer), unguarded `self.bus.send()` calls that could crash the whole main loop over one transient write error, and un-closed private D-Bus connections per service on `close()`. See CHANGELOG.md for details.
- [x] First real-hardware deployment (2026-08-21): address claim succeeded, commands confirmed working end-to-end on a dimmable light (both on/off and brightness-slider). Found and fixed two more real bugs from this test: rapid slider-drag writes were refused outright instead of coalesced (see CHANGELOG.md -- could have left the light on an earlier drag position, not just log noise); switch panels had no predictable sort order and couldn't be grouped, root-caused against `gui-v2`'s own source (missing root `/CustomName`, and `/SwitchableOutput/0/Settings/Group` was never registered) and fixed, plus `Settings/Group` support added to `manage-devices`. 281 tests passing.
- [x] Fixed a real outage after a Venus OS firmware update (2026-08-21): service stayed up (`svstat`) but published nothing, since `vecan1` came back administratively `DOWN` and nothing brought it back up -- interface bring-up had always been an external/manual responsibility (Phase 0's original design). `SocketCanBus` now brings its own interface up if it isn't already (idempotent, kernel `IFF_UP`-flag check), self-healing across firmware updates or any other cause. Also documented (previously unwritten) exactly how SetupHelper's boot-time package-reinstall mechanism is supposed to work, for diagnosing future "service didn't come back after a firmware update" reports that turn out not to be this. See CHANGELOG.md, `dev-notes/VENUS_OS_CONSTRAINTS.md`, `ARCHITECTURE.md`.
- [x] Follow-up (2026-08-21): that fix only covered the interface going down at (re)connect time. Confirmed on real hardware that a mid-run outage (interface goes down while already connected) instead flooded the log with one WARNING per dropped frame, since `recv()` never errors in that state. `Publisher._send_frame()` now catches write failures directly, retries `ensure_interface_up()` immediately then rate-limited to once per 15s (fixed interval, not exponential -- discussed with the user first), with edge-triggered logging instead of per-frame spam.
- [ ] **Not yet done -- next steps before this is trusted on the coach:**
  - [ ] Confirm a `commands_enabled: false` device really transmits nothing (parallel `candump`).
  - [ ] Run a real OneControl power-loss test to confirm in-flight commands abort cleanly and a device needs a fresh post-outage DEVICE_ID before being command-eligible again (this exercises the *second* `resolve_for_command()` check, not just the address table's own already-unit-tested outage behavior).
  - [ ] Confirm the panel sort-order and grouping fixes on real hardware after the next redeploy (implemented and unit tested, not yet observed live).
  - [ ] Only then, enable remaining devices one at a time, watching logs each time.

## Future — GitHub-Based Auto-Update (not started, deliberately deferred)

- Once this project is ready to be public: push to a public GitHub repo, add `gitHubInfo` (`user:branch`), and PackageManager's own `GitHubDownload`/`updateGitHubVersion` can check `raw.githubusercontent.com/<user>/<repo>/<branch>/version` and pull `github.com/<user>/<repo>/archive/<branch>.tar.gz` automatically -- no special GitHub Release/tag needed, confirmed by reading `PackageManager.py` directly (2026-08-20). See `dev-notes/VENUS_OS_CONSTRAINTS.md` for the mechanics.
- Until then: manual tarball sync + `setup install auto` (see README.md's Installation section) is the deployment path. Not a blocker for anything -- just slower than it'll eventually be.

## Future Phase — System Configuration via CAN (research only, not scoped yet)

- Investigate whether OneControl input/device configuration (renaming an input, changing its purpose/function assignment) can be done over CAN. Normally done via the Lippert touchscreen controller, which this installation doesn't have.
- **Mechanism now identified (2026-08-20), not yet implemented or tested:** decompiling the LippertConnect app confirmed a device's `(FUNCTION_NAME, FunctionInstance)` is stored in two dedicated, per-device PIDs — `PID 4 = IDS_CAN_FUNCTION_NAME` (UINT16) and `PID 5 = IDS_CAN_FUNCTION_INSTANCE` (UINT8) — and the app's own rename feature works by PID-writing these two values (almost certainly via the same session/TEA-cipher write path already implemented for Phase 3 commands, though this hasn't been confirmed against a real PID_WRITE capture). This turns "investigate whether it's possible" into "implement and test a PID write to PID 4/5" — a much narrower, better-scoped task than originally framed, whenever it's wanted.
- **On the FUNCTION_NAME=0 collision (13 of 31 devices in the 2026-08-19 capture sharing an identical fallback `(PRODUCT_ID=232, instance=42)` stable key):** per the user, these are understood to just be unused/unconfigured physical input connections on the Unity module itself (empty ports with nothing wired to them) — not mystery devices needing investigation. This isn't a gap to solve unless/until something new gets physically wired into one of those ports and needs a name assigned without the touchscreen, at which point the PID 4/5 write mechanism above is how that would be done.
- Not started. No design decisions made beyond the above. Revisit only when explicitly asked.

## Not Planned (deliberate scope boundary)

- Motor control (awnings/slides/leveling jacks) — read-only status only. Requires separate explicit re-approval, not a followup TODO.
