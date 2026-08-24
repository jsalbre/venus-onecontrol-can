# TODO

**Version:** 3.6 | **Updated:** 2026-08-24

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

Two real goals: (1) assign a name/function to a currently-unused Unity module input/output, (2) fix a real problem -- one of the module's dimming-capable outputs behaved as a plain on/off latch instead of a dimmer. Both PID write mechanics and the goal-2 root cause (PID 161) are fully researched, confirmed, and fixed on real hardware; goal 1's disambiguation problem (picking a specific unconfigured port out of many that share an identical fallback stable key) is solved and physically confirmed for the target port via `device_instance`. See `CHANGELOG.md` and `ARCHITECTURE.md`'s `device_instance` section for full detail.

- [x] **Relay-blip physical confirmation done (2026-08-22):** `relay_blip.py` run for real against both DIMM/LATCH output 7 and output 8 -- both confirmed correct against the `device_instance`-based candidate mapping. Also caught and fixed a real bug in the tool itself (default hold matched the session timeout exactly, briefly leaving output 7 stuck on).
- [x] **`manage-system` built (2026-08-22):** interactive tool for module-level reconfiguration -- configure a port (identity + applicable known settings like PID 161), unconfigure a port, or back up every port's current settings. Distinct from `manage-devices` (D-Bus config only). Reused/extended `probe_common.py` with shared session-open/close, test-blip, and board-scan helpers (also refactored into `pid_write.py`/`relay_blip.py`/`list_unconfigured.py`, behavior-preserving). Full test suite green (312 tests).
- [x] **First real-hardware run (2026-08-23) failed, root cause found and fixed (2026-08-24), confirmed working end-to-end (2026-08-24):** renaming DIMM/LATCH output 7 (PID 4/5 writes) both failed against real hardware. Root cause: `build_pid_write_request()` sized the value at each PID's own declared `Formatter` width (2 bytes for PID 4, 1 for PID 5) instead of the real, universal 6-byte (`UInt48`) width every PID write actually requires -- confirmed by reading the decompiled LippertConnect `WritePidAsync`/`PAYLOAD.FromArgs` source directly, not guessed. Fixed and retried the same day: output 7 renamed to "Front Cap Light," both writes verified `PASS`, physically tested, added to `config.json`, and now live/controllable in the OneControl app. Closes out the original "assign a name to an unused port" goal. See `ARCHITECTURE.md`'s "PID Writes" section and `CHANGELOG.md`.
- [ ] **Open side-investigation, not blocking:** PID 238 (`ON_OFF_INPUT_PIN`) looks like it records which "Configurable Input" position is wired as a device's local switch -- well-supported by real evidence but not confirmed by documentation or a physical test. PID 146 (`INPUT_SWITCH_TYPE`)'s meaning is unknown -- no enum found in the decompiled source, no observed value variation yet. The "Configurable Inputs" bank itself (3 wired-but-uncommissioned positions) has no matching visible CAN device at all in the unconfigured pool -- genuinely unresolved.

## Performance — publisher.py CPU usage

- [ ] **Not yet deployed/measured on real hardware:** `ConfigManager`/`DiscoveryLog` caching, `tank_service.py`/`motor_status_service.py` D-Bus write batching, and `publisher.py`'s `StableKey`-keying cleanup (2026-08-24, see `CHANGELOG.md`) address a confirmed root cause (`publisher.py` observed at 11-12% CPU vs. 3-4% for the next-heaviest process). Deploy and confirm the CPU drop via `top`/`htop`, and re-exercise the live-reconfiguration workflow (add/expose a device via `manage-devices` while `publisher.py` keeps running) to confirm the caching fix didn't regress it.

## Not Planned (deliberate scope boundary)

- Motor control (awnings/slides/leveling jacks) — read-only status only. Requires separate explicit re-approval, not a followup TODO.
