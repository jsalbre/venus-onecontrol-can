# TODO

**Version:** 6.1 | **Updated:** 2026-08-26

---

## Open Items

- [ ] **Last item before Phase 3 (safe commands) is fully trusted on the coach:** run a real OneControl power-loss test to confirm in-flight commands abort cleanly and a device needs a fresh post-outage DEVICE_ID before being command-eligible again (exercises the *second* `resolve_for_command()` check, not just the address table's own already-unit-tested outage behavior).
- [ ] **Open side-investigation, not blocking:** PID 238 (`ON_OFF_INPUT_PIN`) looks like it records which "Configurable Input" position is wired as a device's local switch -- well-supported by real evidence but not confirmed by documentation or a physical test. PID 146 (`INPUT_SWITCH_TYPE`)'s meaning is unknown -- no enum found in the decompiled source, no observed value variation yet. The "Configurable Inputs" bank itself (3 wired-but-uncommissioned positions) has no matching visible CAN device at all in the unconfigured pool -- genuinely unresolved.
- [ ] **Expose CHASSIS_INFO battery voltage (PID 43/44/144) as an enable/expose-able device, like tanks/switches.** Confirmed real and readable (see `ARCHITECTURE.md`'s "PID_READ_WRITE" and Known Limitations). Real blocker: `CHASSIS_INFO` broadcasts `FUNCTION_NAME=0`, so its stable key falls back to `(PRODUCT_ID, instance)` -- the same generic fallback key shared by 13 of 31 other unconfigured devices on this coach's module (see "device_instance" under Protocol Reference). It cannot go through the normal `manage-devices` discovery flow the way a tank or light does, since that flow already deliberately excludes fallback-keyed devices for exactly this ambiguity reason. Needs a design decision on how to identify/key a `CHASSIS_INFO`-type device instead (e.g. by `DEVICE_TYPE` directly, since that field never collides, possibly combined with `device_instance` if more than one ever turns up) before implementation -- not yet decided.

