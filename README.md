# venus-onecontrol-can

**Version:** 0.3.0 (Phase 2 implemented, not yet deployed) | **Updated:** 2026-08-19

---

## What This Is

Bridges a Lippert OneControl RV control system (Unity X270, proprietary "IDS-CAN" protocol — not RV-C) to a Victron Cerbo GX MK2's spare CAN interface, so tank levels and light/relay/pump/water-heater state appear natively in the Venus OS GUI and VRM, and (from Phase 3 on) those same devices can be controlled from there.

**Motor-driven devices (awnings, slides, leveling jacks) are read-only.** This project never sends a command that could move one — see `ARCHITECTURE.md` for why.

**A device is only ever published to D-Bus if it has an explicit `expose: true` entry in `config.json`.** Everything discovered on the bus but not configured is logged to `discovered_devices.json` for review, never exposed automatically.

---

## Status

- Phase 0 (wiring + capture) and Phase 1 (decoder validation) are done, confirmed against real traffic from this coach.
- Phase 2 (D-Bus publishing) is implemented and unit-tested where testable, but has **not yet been run on the actual Cerbo** — the D-Bus/GLib-dependent code (`dbus_bridge/{tank,switch,motor_status}_service.py`, `publisher.py`) can't run or even be imported off a Venus OS/Linux system with `dbus`/`gi` installed, so this is the next thing to test on real hardware.
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

Installed as a SetupHelper package, entirely via SSH — no reliance on the Classic GUI's PackageManager menu (this system may run GUIv2, where that menu isn't available). No public repo exists yet, so deploy by copying the project directly:

```bash
scp -r . root@<cerbo-host>:/data/venus-onecontrol-can
ssh root@<cerbo-host> "cp /data/venus-onecontrol-can/config.example.json /data/venus-onecontrol-can/config.json"
ssh root@<cerbo-host> "/data/venus-onecontrol-can/setup install auto"
```

Edit `/data/venus-onecontrol-can/config.json` on the Cerbo to enable specific devices (`expose: true`) before or after installing — the service reloads config on every restart.
