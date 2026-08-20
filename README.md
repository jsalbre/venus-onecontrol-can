# venus-onecontrol-can

**Version:** 0.1.0 (unreleased) | **Updated:** 2026-08-18

---

## What This Is

Bridges a Lippert OneControl RV control system (Unity X270, proprietary "IDS-CAN" protocol — not RV-C) to a Victron Cerbo GX MK2's spare CAN interface, so tank levels, battery voltage, and light/relay/pump/water-heater state appear natively in the Venus OS GUI and VRM, and those same devices can be controlled from there.

**Motor-driven devices (awnings, slides, leveling jacks) are read-only.** This project never sends a command that could move one — see `ARCHITECTURE.md` for why.

---

## Status

Not yet wired to real hardware. See `TODO.md` for phase status. Nothing in this repo should be trusted to control a physical device until it has passed Phase 0–2 validation against this specific coach's traffic.

---

## Hardware Setup

1. Wire the Cerbo GX MK2's spare/second CAN interface to the OneControl bus.
2. This installation wires the Cerbo in as the new physical end of the bus (not a mid-bus tap), so:
   - **Enable** CAN bus termination (120Ω) on the Cerbo side — it is now a true bus end.
   - **Remove** the terminator from whatever device was previously the bus's end, so there are still exactly two terminators total, one at each true end. Leaving the old one in place along with the new one at the Cerbo overloads the bus past what the transceivers can drive.
3. If no frames appear once wired, try swapping CANH/CANL — reversed polarity causes silence, not damage.

---

## Development

Requires no third-party Python packages — everything in `src/can_link/` and `src/bus/` uses only the standard library (`socket.AF_CAN`/`CAN_RAW` for the bus, no `python-can`).

```bash
# Run the protocol decoder unit tests (no hardware needed)
python3 -m unittest discover -s tests

# Replay a captured candump-format log through the decoder
python3 src/tools/candump_replay.py samples/<capture>.log
```

## Installation (Cerbo GX)

Installed as a SetupHelper package, entirely via SSH — no reliance on the Classic GUI's PackageManager menu (this system may run GUIv2, where that menu isn't available):

```bash
wget -qO - https://github.com/<your-fork>/venus-onecontrol-can/archive/latest.tar.gz | tar -xzf - -C /data
mv /data/venus-onecontrol-can-latest /data/venus-onecontrol-can
/data/venus-onecontrol-can/setup install auto
```
