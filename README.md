# venus-onecontrol-can

**Version:** 0.5.5 (Phase 3 on real hardware, self-healing CAN bring-up) | **Updated:** 2026-08-24

---

## What This Is

Bridges a Lippert OneControl RV control system (Unity X270, proprietary "IDS-CAN" protocol — not RV-C) to a Victron Cerbo GX MK2's spare CAN interface, so tank levels and light/relay/pump/water-heater state appear natively in the Venus OS GUI and VRM, and those same devices can be controlled from there.

**Motor-driven devices (awnings, slides, leveling jacks) are read-only.** This project never sends a command that could move one — see `ARCHITECTURE.md` for why.

**A device is only ever published to D-Bus if it has an explicit `expose: true` entry in `config.json`, and only ever commandable if it separately has `commands_enabled: true`.** Everything discovered on the bus but not configured is logged to `discovered_devices.json` for review, never exposed automatically.

---

## Status

- Phase 0 (wiring + capture) and Phase 1 (decoder validation) are done, confirmed against real traffic from this coach.
- Phase 2 (D-Bus publishing) is deployed and confirmed working on the real Cerbo: tank and water pump switch services registered on D-Bus, values update live, and the read-only safety gate is confirmed live (a GUI write attempt was correctly rejected and logged, not silently accepted or actually applied).
- Phase 3 (commands) is implemented, unit tested (312 tests passing), and **confirmed working on real hardware** (on/off, brightness slider, panel sort/group, self-healing CAN interface bring-up) — see `TODO.md`'s Phase 3 section for the one remaining rollout item (a real power-loss test).

See `TODO.md` for the detailed phase checklist.

---

## Hardware Setup

1. Wire the Cerbo GX MK2's spare/second CAN interface to the OneControl bus. On the Unity board's connector, CAN H goes to pin 7 and CAN L goes to pin 8 on the Cerbo's CAN interface connector.
2. This installation wires the Cerbo in as the new physical end of the bus (not a mid-bus tap), so:
   - **Add termination (120Ω)** on the Cerbo side by plugging a terminator into the Cerbo CAN interface's unused plug — it is now a true bus end. There is no software/config setting for this; it's a physical terminator connector.
   - **Remove** the terminator from whatever device was previously the bus's end, so there are still exactly two terminators total, one at each true end. Leaving the old one in place along with the new one at the Cerbo overloads the bus past what the transceivers can drive.
3. If no frames appear once wired, try swapping CANH/CANL — reversed polarity causes silence, not damage.
4. In Venus OS's own settings, set this CAN interface's profile to **disabled** — otherwise Venus OS's own CAN-bus service tries to manage the port itself. The interface still exists at the kernel level for this project's raw SocketCAN access either way.
5. The service brings the interface up itself (at 250 kbit/s, confirmed interface name on this Cerbo: `vecan1`) on every connection attempt if it isn't already up — including after a Venus OS firmware update, which can leave it administratively down. No manual step needed. If you ever want to bring it up by hand (e.g. to check traffic with `candump` before the service is installed):
   ```bash
   ip link set vecan1 up type can bitrate 250000
   ```

---

## Dependencies

`ext/velib_python` (Victron's own `VeDbusService` reference implementation, MIT-licensed) is a **git submodule**, not vendored source — clone this repo with `git clone --recurse-submodules <url>`, or if already cloned without it, run `git submodule update --init` before doing anything else. `dbus_bridge/{tank,switch}_service.py` and `publisher.py` import directly from it (`sys.path` is extended at runtime to include `ext/velib_python`). It must be present locally *before* building the deploy tarball below — the build step is a plain `tar` of whatever's on disk, so a repo cloned without submodules will silently produce a tarball that's missing it, and the service will fail to import on the Cerbo.

## Development

Requires no third-party Python packages anywhere in `src/` — only the standard library (`socket.AF_CAN`/`CAN_RAW` for the bus, no `python-can`; `dbus`/`gi` are provided by Venus OS itself, not pip-installed).

```bash
# Run the protocol decoder + config/routing unit tests (no hardware, no dbus/gi needed)
python3 -m unittest discover -s tests

# Replay a captured candump-format log through the decoder
python3 src/tools/candump_replay.py samples/<capture>.log
```

`dbus_bridge/{tank,switch}_service.py` and `publisher.py` require `dbus`/`gi`, which only exist on Venus OS — they're syntax-checked with `python3 -m py_compile` here, but can only be functionally tested on the Cerbo itself.

---

## Installation (Cerbo GX)

Installed as a SetupHelper package, entirely via SSH — no reliance on the Classic GUI's PackageManager menu (this system may run GUIv2, where that menu isn't available). The GitHub repo (`github.com/jsalbre/venus-onecontrol-can`) is currently private, so there's no GitHub-based auto-update yet -- see ARCHITECTURE.md's "Platform Constraints (Venus OS)" section. In the meantime, deploy by copying the project directly as a tarball, excluding `config.json` (your real on-device config -- never overwrite it with the repo's), `samples/`, and `tests/`:

```bash
tar czf /tmp/venus-onecontrol-can.tar.gz --exclude='.git' --exclude='samples' --exclude='tests' --exclude='__pycache__' --exclude='config.json' .
scp /tmp/venus-onecontrol-can.tar.gz root@<cerbo-host>:/tmp/
```

First install only, on the Cerbo:
```bash
mkdir -p /data/venus-onecontrol-can
tar xzf /tmp/venus-onecontrol-can.tar.gz -C /data/venus-onecontrol-can
cp /data/venus-onecontrol-can/config.example.json /data/venus-onecontrol-can/config.json
/data/venus-onecontrol-can/setup install auto
```

### Updating an existing install

Extract over the existing directory, then just re-run `setup install auto` -- do **not** manually stop/start the service around this. `setup`'s `INSTALL_SERVICES` step (`installService` in SetupHelper's `HelperResources/ServiceResources`) already diffs the run file and, if the service is currently up, sends it a clean `svc -t` restart itself. Manually stopping it first is unnecessary and was a mistake in earlier deployment notes for this project.

```bash
tar xzf /tmp/venus-onecontrol-can.tar.gz -C /data/venus-onecontrol-can
/data/venus-onecontrol-can/setup install auto
```

(Note: `setup`'s `INSTALL_FILES` step is a no-op for this package -- it only matters for packages that patch pre-existing Venus OS system files via a `fileList`, which this project doesn't have or need. File placement is handled by the `tar` extraction above, not by `setup` itself, until/unless this project moves to GitHub-based installs.)

Edit `/data/venus-onecontrol-can/config.json` on the Cerbo to enable specific devices (`expose: true`) before or after installing — the service reloads config on every restart. Prefer `manage-devices` (below) over hand-editing where possible.

---

## Enabling and Managing Devices

Once running, the service logs every device it sees on the bus but isn't configured to `discovered_devices.json`, next to `config.json`. Use `manage-devices` to review that list and add a device interactively, or to change/remove a device already configured:

```bash
/data/venus-onecontrol-can/manage-devices
```

Its first menu shows a numbered list of addable devices (already-configured devices and devices with no supported service type are filtered out automatically); pick one to add it, confirming a friendly name (defaulting to Lippert's own name for it) and whether to expose it, then offers to restart the service. `device_class` is never asked for — it's inferred automatically from what the device itself broadcasts (its DEVICE_TYPE and FUNCTION_NAME), the same way a human reviewing the discovery log would work it out. For a commandable device_class (lights, pump, water heater), it also asks — separately, defaulting to No — whether to enable commands now; a device can be exposed (visible, read-only) without ever being made commandable.

Devices using the `(PRODUCT_ID, instance)` fallback key (unconfigured/unnamed inputs — see `ARCHITECTURE.md`'s stable-key design decision) are never offered, since multiple physical (non-)devices share that exact fallback identity and there's no single reliable device to enable there.

Choose `M) Manage existing devices` from that same first menu to rename a device, toggle its `expose`/`commands_enabled` flags, or remove it entirely (`device_class` still isn't editable there, for the same reason it's never asked at add time). The tool offers to restart the service after any change, but only some of them actually need one: toggling `commands_enabled` takes effect immediately, and so does adding/exposing a new device (the running service creates its D-Bus object live, the next time that device's `DEVICE_ID` broadcast arrives). Toggling `expose` off or removing a device is the one case that genuinely needs the restart — there's no live teardown path, so it stays on D-Bus until the service restarts.

---

## License

MIT — see `LICENSE`.
