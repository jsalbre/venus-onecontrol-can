# TODO

**Version:** 5.0 | **Updated:** 2026-08-24

---

## Open Items

- [ ] PID battery voltage read — deferred, low priority. No PID_READ_WRITE traffic seen in the original capture at all; not needed, will opportunistically check any future natural capture instead.
- [ ] **Last item before Phase 3 (safe commands) is fully trusted on the coach:** run a real OneControl power-loss test to confirm in-flight commands abort cleanly and a device needs a fresh post-outage DEVICE_ID before being command-eligible again (exercises the *second* `resolve_for_command()` check, not just the address table's own already-unit-tested outage behavior).
- [ ] **Open side-investigation, not blocking:** PID 238 (`ON_OFF_INPUT_PIN`) looks like it records which "Configurable Input" position is wired as a device's local switch -- well-supported by real evidence but not confirmed by documentation or a physical test. PID 146 (`INPUT_SWITCH_TYPE`)'s meaning is unknown -- no enum found in the decompiled source, no observed value variation yet. The "Configurable Inputs" bank itself (3 wired-but-uncommissioned positions) has no matching visible CAN device at all in the unconfigured pool -- genuinely unresolved.
- [ ] **Deferred, not blocking:** two smaller, known-tiny `publisher.py` CPU contributors were flagged but not fixed during the CPU investigation -- `routing.py`'s exposure-check-before-already-created ordering (one extra O(n) scan per frame for already-created devices) and `find_device()`'s unindexed linear scan. See `CHANGELOG.md` for the original investigation. If CPU usage ever needs to come down further, these are the known starting points, though real profiling would be needed first to confirm there isn't a different, larger remaining contributor.
- [ ] **PID 161 live-read not yet run on real hardware.** See `CHANGELOG.md`/`ARCHITECTURE.md`'s "PID 161 Live Read" section for the implementation. The async request/response machinery in `publisher.py` (delayed service creation, timeout fallback) has no unit test coverage (consistent with the rest of its dbus/gi-dependent code) and needs a real dimmable_light to confirm against, ideally one actually configured as latching (PID 161=1) to see the on/off-only presentation, not just one that's already a confirmed real dimmer.

## Future — GitHub-Based Auto-Update (not started, deliberately deferred)

- Repo is now pushed to GitHub (`github.com/jsalbre/venus-onecontrol-can`, currently private). Still open: flip it public when ready, then add `gitHubInfo` (`user:branch`) so PackageManager's own `GitHubDownload`/`updateGitHubVersion` can check `raw.githubusercontent.com/<user>/<repo>/<branch>/version` and pull `github.com/<user>/<repo>/archive/<branch>.tar.gz` automatically -- no special GitHub Release/tag needed, confirmed by reading `PackageManager.py` directly. See `ARCHITECTURE.md`'s "Platform Constraints (Venus OS)" section for the mechanics.
- Until then: manual tarball sync + `setup install auto` (see README.md's Installation section) is the deployment path. Not a blocker for anything -- just slower than it'll eventually be.

