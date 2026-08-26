# TODO

**Version:** 6.2 | **Updated:** 2026-08-26

---

## Open Items

- [ ] **Last item before Phase 3 (safe commands) is fully trusted on the coach:** run a real OneControl power-loss test to confirm in-flight commands abort cleanly and a device needs a fresh post-outage DEVICE_ID before being command-eligible again (exercises the *second* `resolve_for_command()` check, not just the address table's own already-unit-tested outage behavior).
- [ ] **Open side-investigation, not blocking:** PID 238 (`ON_OFF_INPUT_PIN`) looks like it records which "Configurable Input" position is wired as a device's local switch -- well-supported by real evidence but not confirmed by documentation or a physical test. PID 146 (`INPUT_SWITCH_TYPE`)'s meaning is unknown -- no enum found in the decompiled source, no observed value variation yet. The "Configurable Inputs" bank itself (3 wired-but-uncommissioned positions) has no matching visible CAN device at all in the unconfigured pool -- genuinely unresolved.
- [ ] **`battery_voltage` device_class (CHASSIS_INFO battery voltage) is implemented, not yet confirmed on real hardware.** Keyed by `(DEVICE_TYPE, device_instance)` -- a new `StableKey` kind added specifically for this, since `CHASSIS_INFO`'s `FUNCTION_NAME=0` fallback key would otherwise collide with the ambiguous unconfigured pool (see `ARCHITECTURE.md`'s "device_instance" section). Periodically re-reads PID 43 (`BATTERY_VOLTAGE`) via `publisher.py`'s first-ever recurring PID poll (`pid_poll_interval_sec`, previously declared but unused), publishing to a new `com.victronenergy.battery` service with only `/Dc/0/Voltage` populated -- no `/Soc`/`/Dc/0/Current`, since this project has no data for either. Needs real-hardware verification: confirm it appears in `manage-devices`' addable list, confirm the periodic poll actually updates the value over time, and visually confirm how a battery service with no SOC renders in the real Cerbo GUI (unconfirmed from documentation alone -- if it renders badly, `ARCHITECTURE.md` notes a fallback to a custom, non-standard service name instead).

