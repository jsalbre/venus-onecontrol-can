# TODO

**Version:** 6.3 | **Updated:** 2026-08-26

---

## Open Items

- [ ] **Last item before Phase 3 (safe commands) is fully trusted on the coach:** run a real OneControl power-loss test to confirm in-flight commands abort cleanly and a device needs a fresh post-outage DEVICE_ID before being command-eligible again (exercises the *second* `resolve_for_command()` check, not just the address table's own already-unit-tested outage behavior).
- [ ] **Open side-investigation, not blocking:** PID 238 (`ON_OFF_INPUT_PIN`) looks like it records which "Configurable Input" position is wired as a device's local switch -- well-supported by real evidence but not confirmed by documentation or a physical test. PID 146 (`INPUT_SWITCH_TYPE`)'s meaning has a plausible hypothesis now (Lippert's own Configurator Guide documents a real "Toggle Switch"/"Momentary Switch" setting matching this PID's name/subject) but it's still unconfirmed -- the observed value (`3`) doesn't cleanly fit a plain 2-value enum, and there's no second device with a different value to test against yet. The "Configurable Inputs" bank itself (3 wired-but-uncommissioned positions) has no matching visible CAN device at all in the unconfigured pool -- genuinely unresolved.

