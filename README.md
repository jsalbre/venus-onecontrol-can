# venus-onecontrol-can

**Version:** 0.4.0 (Phase 2 deployed and working) | **Updated:** 2026-08-20

---

## What This Is

Bridges a Lippert OneControl RV control system (Unity X270, proprietary "IDS-CAN" protocol — not RV-C) to a Victron Cerbo GX MK2's spare CAN interface, so tank levels and light/relay/pump/water-heater state appear natively in the Venus OS GUI and VRM, and (from Phase 3 on) those same devices can be controlled from there.

**Motor-driven devices (awnings, slides, leveling jacks) are read-only.** This project never sends a command that could move one — see `ARCHITECTURE.md` for why.

**A device is only ever published to D-Bus if it has an explicit `expose: true` entry in `config.json`.** Everything discovered on the bus but not configured is logged to `discovered_devices.json` for review, never exposed automatically.

---

## Status

- Phase 0 (wiring + capture) and Phase 1 (decoder validation) are done, confirmed against real traffic from this coach.
- Phase 2 (D-Bus publishing) is deployed and confirmed working on the real Cerbo: tank and water pump switch services registered on D-Bus, values update live, and the read-only safety gate is confirmed live (a GUI write attempt was correctly rejected and logged, not silently accepted or actually applied).
- Phase 3 (commands) is not started.

See `TODO.md` for the detailed phase checklist.

---

## Hardware Setup

1. Wire the Cerbo GX MK2's spare/second CAN interface to the OneControl bus.
2. This installation wires the Cerbo in as the new physical end of the bus (not a mid-bus tap), so:
   - **Enable** CAN bus termination (120Ω) on the Cerbo side — it is now a true bus end.
   - **Remove** the terminator from whatever device was previously the bus's end, so there are still exactly two terminators total, one at each true end. Leaving the old one in place along with the new one at the Cerbo overloads the bus past what the transceivers can drive.
3. If no frames appear once wired, try swapping CANH/CANL — reversed polarity causes silence, not damage.
4. Bring the interface up at 250 kbit/s (confirmed interface name on this Cerbo: `vecan1`):
   ```bash
   ip link set vecan1 up type can bitrate 250000
   ```

---

## Development

Requires no third-party Python packages anywhere in `src/` — only the standard library (`socket.AF_CAN`/`CAN_RAW` for the bus, no `python-can`; `dbus`/`gi` are provided by Venus OS itself, not pip-installed).

```bash
# Run the protocol decoder + config/routing unit tests (no hardware, no dbus/gi needed)
python3 -m unittest discover -s tests

# Replay a captured candump-format log through the decoder
python3 src/tools/candump_replay.py samples/<capture>.log
```

`dbus_bridge/{tank,switch,motor_status}_service.py` and `publisher.py` require `dbus`/`gi`, which only exist on Venus OS — they're syntax-checked with `python3 -m py_compile` here, but can only be functionally tested on the Cerbo itself.

---

## Installation (Cerbo GX)

Installed as a SetupHelper package, entirely via SSH — no reliance on the Classic GUI's PackageManager menu (this system may run GUIv2, where that menu isn't available). No public GitHub repo exists yet (planned for later -- see TODO.md), so deploy by copying the project directly as a tarball, excluding `config.json` (your real on-device config -- never overwrite it with the repo's), `dev-notes/`, `samples/`, and `tests/`:

```bash
tar czf /tmp/venus-onecontrol-can.tar.gz --exclude='.git' --exclude='dev-notes' --exclude='samples' --exclude='tests' --exclude='__pycache__' --exclude='config.json' .
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

Edit `/data/venus-onecontrol-can/config.json` on the Cerbo to enable specific devices (`expose: true`) before or after installing — the service reloads config on every restart. Prefer `enable-device` (below) over hand-editing where possible.

---

## Enabling Devices

Once running, the service logs every device it sees on the bus but isn't configured to `discovered_devices.json`, next to `config.json`. Use `enable-device` to review that list and add one interactively:

```bash
/data/venus-onecontrol-can/enable-device
```

It shows a numbered menu of addable devices (already-configured devices and devices with no supported service type are filtered out automatically), lets you pick one, confirms a friendly name (defaulting to Lippert's own name for it) and whether to expose it, then offers to restart the service. `device_class` is never asked for — it's inferred automatically from what the device itself broadcasts (its DEVICE_TYPE and FUNCTION_NAME), the same way a human reviewing the discovery log would work it out.

Devices using the `(PRODUCT_ID, instance)` fallback key (unconfigured/unnamed inputs — see `ARCHITECTURE.md`'s stable-key design decision) are never offered, since multiple physical (non-)devices share that exact fallback identity and there's no single reliable device to enable there.
