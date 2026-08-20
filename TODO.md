# TODO

**Version:** 1.1 | **Updated:** 2026-08-19

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

## Phase 2 — D-Bus Publish, Read-Only (blocked on Phase 1)

- Deploy `dbus_bridge/` to the Cerbo via the SetupHelper `setup` script.
- Publish tanks/battery/motor-status/lights-relays-pump-water-heater as read-only D-Bus services.

## Phase 3 — Safe Commands (blocked on Phase 2)

- Wire up `switch_service.py` write paths, session handshake, and command send with the safety gate active.
- Test with a single low-consequence device before enabling the rest.
- Run a real or simulated OneControl power-loss test to confirm the outage safety gate works.

## Future Phase — System Configuration via CAN (research only, not scoped yet)

- Investigate whether OneControl input/device configuration (renaming an input, changing its purpose/function assignment, enabling additional unused inputs) can be done over CAN. Normally done via the Lippert touchscreen controller, which this installation doesn't have.
- This is a distinct problem from reading/commanding existing devices (v1's scope): it likely means writing to whatever config/provisioning mechanism assigns a physical input's DEVICE_ID identity (FUNCTION_NAME, instance) rather than just sending DEVICE_STATUS commands. None of the research so far (esphome-onecontrol, UnityX-canbus, manos/OneControl-RV-C-Protocol) documents this — it would need new reverse-engineering, likely by capturing traffic while using the LippertConnect app or a touchscreen controller (borrowed/simulated) to change a setting and diffing the bus traffic.
- **Confirmed real-world motivation (2026-08-19 capture):** 13 of the 31 devices discovered on this coach report `FUNCTION_NAME=0` (unconfigured) and, worse, ALSO share an identical fallback `(PRODUCT_ID=232, instance=42)` — meaning the stable-key discovery design (`ARCHITECTURE.md`) cannot currently distinguish these 13 devices from each other at all, by any broadcast data. They span tank sensors, relays, an H-bridge motor, the hour meter, chassis info, and the generator genie. This isn't a decoder bug — there's genuinely no unique identity being broadcast for an unconfigured input. Solving this future-phase item may be the only way to make those 13 devices usable.
- Not started. No design decisions made. Revisit only when explicitly asked.

## Not Planned (deliberate scope boundary)

- Motor control (awnings/slides/leveling jacks) — read-only status only. Requires separate explicit re-approval, not a followup TODO.
