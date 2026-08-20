# TODO

**Version:** 1.1 | **Updated:** 2026-08-19

---

## Phase 0 — Wiring + Raw Capture (done, 2026-08-19)

- [x] Wired the Cerbo GX MK2's spare CAN interface (`vecan1`) to the OneControl bus as the new physical bus end.
- [x] Confirmed frames arrive for both ID widths (`samples/capture.log`, gitignored — 7541 frames, ~48s).
- [x] Captured 4 real TEA session handshakes (8 seed/key pairs) by operating lights from the OneControl app while logging.
- [x] Correlated physical device actions (3 lights on/off, water pump off/on) against log timestamps.

## Phase 1 — Decoder Validation (partially done, 2026-08-19)

- [x] DEVICE_ID structure validated: 1514 broadcasts, 31 devices, zero decode errors.
- [x] TEA cipher validated: all 8 real captured seed/key pairs match exactly (`tests/test_session.py::TeaTransformRealHardwareTests`).
- [x] Relay/motor status (`decode_relay_or_motor`) validated against a real water pump ON/OFF/ON cycle and a relay-driven light ON/OFF cycle.
- [x] Dimmable light status (`decode_dimmable_light`) validated against two real light ON/OFF cycles.
- [ ] Tank sensor status (`decode_tank_sensor`) — not yet exercised by a physical action. Capture a log while checking/changing a tank level (or watch one fill/drain) and replay it.
- [ ] PID battery voltage read — not yet exercised. Needs a point-to-point REQUEST/RESPONSE with request code 0x11 in a capture; none seen yet, so either it wasn't triggered during the capture window or it's polled by a different node than expected.
- [ ] Investigate unmapped `MessageType.CIRCUIT_ID` (type 1) traffic — the source research described this as "all-zero, unused" on other coaches, but this coach shows non-trivial CIRCUIT_ID broadcasts from several addresses. Worth understanding before Phase 2, in case it's carrying something relevant.

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
- Not started. No design decisions made. Revisit only when explicitly asked.

## Not Planned (deliberate scope boundary)

- Motor control (awnings/slides/leveling jacks) — read-only status only. Requires separate explicit re-approval, not a followup TODO.
