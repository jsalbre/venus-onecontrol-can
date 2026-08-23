# TODO

**Version:** 3.0 | **Updated:** 2026-08-22

---

## Phase 0 — Wiring + Raw Capture (done, 2026-08-19)

Cerbo wired to the OneControl bus as the new physical bus end; real captures taken; 4 TEA session handshakes and 3 physical device actions correlated against log timestamps. See `CHANGELOG.md` for full detail.

## Phase 1 — Decoder Validation (done, 2026-08-19)

DEVICE_ID, relay/motor status, dimmable light status, and tank sensor decoders all validated against real captures from this coach. See `CHANGELOG.md` for full detail.

- [ ] PID battery voltage read — deferred, low priority. No PID_READ_WRITE traffic seen in the original capture at all; not needed, will opportunistically check any future natural capture instead.

## Phase 2 — D-Bus Publish, Read-Only (done and confirmed on real hardware, 2026-08-20)

`dbus_bridge/` implemented and unit tested; deployed to the Cerbo; a real device-instance collision found and fixed; `enable-device` CLI built and confirmed working end-to-end. See `CHANGELOG.md` for full detail.

## Phase 3 — Safe Commands (implemented 2026-08-20, confirmed on real hardware 2026-08-21)

Address claiming, command sequencer, layered command safety gate, and writeable switch/dimming paths implemented and confirmed end-to-end on real hardware (on/off, brightness slider, panel sort/group, self-healing CAN interface bring-up, and `commands_enabled: false` confirmed to transmit nothing). See `CHANGELOG.md` for full detail.

- [ ] **Last item before Phase 3 is fully trusted on the coach:** run a real OneControl power-loss test to confirm in-flight commands abort cleanly and a device needs a fresh post-outage DEVICE_ID before being command-eligible again (exercises the *second* `resolve_for_command()` check, not just the address table's own already-unit-tested outage behavior).

## Future — GitHub-Based Auto-Update (not started, deliberately deferred)

- Once this project is ready to be public: push to a public GitHub repo, add `gitHubInfo` (`user:branch`), and PackageManager's own `GitHubDownload`/`updateGitHubVersion` can check `raw.githubusercontent.com/<user>/<repo>/<branch>/version` and pull `github.com/<user>/<repo>/archive/<branch>.tar.gz` automatically -- no special GitHub Release/tag needed, confirmed by reading `PackageManager.py` directly. See `ARCHITECTURE.md`'s "Platform Constraints (Venus OS)" section for the mechanics.
- Until then: manual tarball sync + `setup install auto` (see README.md's Installation section) is the deployment path. Not a blocker for anything -- just slower than it'll eventually be.

## Future Phase — Device Reconfiguration via CAN

Two real goals: (1) assign a name/function to a currently-unused Unity module input/output, (2) fix a real problem -- one of the module's dimming-capable outputs behaved as a plain on/off latch instead of a dimmer. Both PID write mechanics and the goal-2 root cause (PID 161) are fully researched, confirmed, and fixed on real hardware; goal 1's disambiguation problem (picking a specific unconfigured port out of many that share an identical fallback stable key) is substantially solved via `device_instance`. See `CHANGELOG.md` and `ARCHITECTURE.md`'s `device_instance` section for full detail.

- [ ] **Deferred, not started:** production integration -- `manage-devices` UI for reconfiguration, PID 4/5 rename/reassign support (mechanism confirmed, not yet built), any automatic/production write path in `publisher.py`. `pid_write.py` remains a manual, one-shot diagnostic tool for now; this is a separate planning pass if/when a permanent feature is wanted.
- [ ] **Not yet done:** the relay-blip physical confirmation of the `device_instance`-based candidate mapping -- deferred by the user to a later session (needs to be at the rig with a multimeter). Until run, the mapping is "high confidence," not confirmed -- do not write PID 4/5 to any of these addresses before this step.
- [ ] **Open side-investigation, not blocking:** PID 238 (`ON_OFF_INPUT_PIN`) looks like it records which "Configurable Input" position is wired as a device's local switch -- well-supported by real evidence but not confirmed by documentation or a physical test. PID 146 (`INPUT_SWITCH_TYPE`)'s meaning is unknown -- no enum found in the decompiled source, no observed value variation yet. The "Configurable Inputs" bank itself (3 wired-but-uncommissioned positions) has no matching visible CAN device at all in the unconfigured pool -- genuinely unresolved.

## Not Planned (deliberate scope boundary)

- Motor control (awnings/slides/leveling jacks) — read-only status only. Requires separate explicit re-approval, not a followup TODO.
