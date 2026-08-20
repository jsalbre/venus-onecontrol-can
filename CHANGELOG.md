# Changelog

All notable changes to this project are documented here, newest first. This file is append-only — do not revise past entries.

---

## Unreleased

- 2026-08-19: First real capture from this coach's OneControl bus (Cerbo wired in as the new bus end, interface `vecan1`). 1514 DEVICE_ID broadcasts decoded cleanly across 31 discovered devices, zero decode errors. TEA cipher confirmed exactly against 8 real seed/key pairs captured during light-control sessions. Relay/motor status decoder confirmed against a real water pump ON/OFF/ON cycle; dimmable light status decoder confirmed against two real light ON/OFF cycles; a third light (relay-driven, non-dimmable) also confirmed. Tank sensor status and PID battery voltage reads remain unvalidated (not exercised in this capture).
- Project scaffolding and documentation structure established.
- Protocol decoder library (`can_link/`) implemented per the cross-validated IDS-CAN protocol research (esphome-onecontrol, UnityX-canbus, manos/OneControl-RV-C-Protocol), covering CAN ID encode/decode, DEVICE_ID/DEVICE_STATUS decoding, PID reads, TEA-cipher session handshake, command frame builders, and the stable-key discovery/address-resolution table with bus-outage safety gating.
- Phase 0 raw capture tool (`candump_logger.py`) and Phase 1 replay/validation harness (`candump_replay.py`) implemented.
- Not yet validated against real hardware — the OneControl CAN bus is not wired to the Cerbo yet. All decode tables are sourced from other coaches' reverse-engineering and are unverified for this Unity X270 until Phase 0/1 acceptance criteria are met.
