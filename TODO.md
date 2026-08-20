# TODO

**Version:** 1.4 | **Updated:** 2026-08-20

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

## Phase 3 — Safe Commands (blocked on Phase 2)

- Wire up `switch_service.py` write paths, session handshake, and command send with the safety gate active.
- Test with a single low-consequence device before enabling the rest.
- Run a real or simulated OneControl power-loss test to confirm the outage safety gate works.

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
