# PROJECT

**Version:** 1.2 | **Updated:** 2026-08-22

---

## Overview

Bridges a Lippert OneControl RV control system (proprietary "IDS-CAN" protocol, not RV-C) to a Victron Cerbo GX MK2's spare CAN interface via SocketCAN, publishing decoded telemetry and safe device commands to Venus OS's D-Bus.

---

## Documentation Structure

| File | Role | Visibility |
|------|------|------------|
| `PROJECT.md` | This file — doc structure and roles | Public |
| `README.md` | User-facing setup, hardware wiring, safety notes | Public |
| `CHANGELOG.md` | Append-only release history | Public |
| `TODO.md` | Active planned work | Public |
| `ARCHITECTURE.md` | Design decisions and rationale, plus the full protocol/technical reference (byte layouts, PID tables, platform constraints) | Public |
| `ONECONTROL_CAN_PROTOCOL_NOTES.txt` | Plain-text protocol writeup for sharing with other OneControl bridge builders | Public |
| `samples/` | Raw CAN captures and coach-specific reference data (e.g. physical wiring inventory) | Private, gitignored |

---

## Private Docs

`samples/` is gitignored. It contains raw CAN captures and coach-specific reference data (e.g. physical wiring inventories) specific to this RV's OneControl installation. Never commit this directory. No `docs-private/` currently exists -- nothing yet warrants it (personal process notes, AI session logs, prompt receipts, reflections); add it if that changes.
