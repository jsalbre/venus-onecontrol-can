# venus-onecontrol-can

**Version:** 1.1.0 | **Updated:** 2026-08-26

---

## What This Is

Bridges a Lippert OneControl RV control system (Unity X270, proprietary "IDS-CAN" protocol — not RV-C) to a Victron Cerbo GX MK2's spare CAN interface, so tank levels and light/relay/pump/water-heater state appear natively in the Venus OS GUI and VRM, and those same devices can be controlled from there.

**Motor-driven devices (awnings, slides, leveling jacks) are read-only.** This project never sends a command that could move one — see `ARCHITECTURE.md` for why.

**A device is only ever published to D-Bus if it has an explicit `expose: true` entry in `config.json`, and only ever commandable if it separately has `commands_enabled: true`.** Everything discovered on the bus but not configured is logged to `discovered_devices.json` for review, never exposed automatically.

**A device briefly stops responding to commands right after a bus outage or a service restart** — its address isn't trusted again until it broadcasts fresh. This is expected, momentary behavior, not a bug.

---

## Features

- **Live status** — tank levels, and light/relay/pump/water-heater state, appear natively in the Venus OS GUI and VRM.
- **Two-way control** — turn lights/pump/water heater on/off and adjust dimmable lights' brightness directly from the Cerbo GUI or VRM; every write passes through a layered safety gate (see above) before a real command is attempted.
- **GUI panel grouping** — give two or more switches the same group name (via `manage-devices` or directly in the Cerbo GUI's own per-output settings page) to show them together in one panel instead of each getting its own.
- **Per-device visibility control** — show a switch everywhere, hide it entirely, or restrict it to local UIs (GX/MFD) or VRM's remote console only (`manage-devices`'s "Show controls" option) — the same Off/Always/Only Local/Only on VRM choice Node-RED's own virtual switches offer.
- **Automatic dimmer vs. on/off detection** — a dimming-capable output configured (via `manage-system`, below) to behave as a plain on/off switch is detected automatically and shown as a plain switch, not a non-functional dimmer.
- **Self-healing CAN interface** — recovers on its own if the interface goes down (e.g. after a Venus OS firmware update, or it isn't up yet at boot), no manual step needed in normal operation.

See `TODO.md` for currently open work and `CHANGELOG.md` for the full history.

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

`ext/velib_python` (Victron's own `VeDbusService` reference implementation, MIT-licensed) is vendored directly into this repo (not a git submodule -- see ARCHITECTURE.md's "Vendored, Not a Submodule" note for why: GitHub's `archive/<branch>.tar.gz` endpoint, which SetupHelper's GitHub-based auto-update relies on, never includes submodule content, so a submodule here would silently ship broken on every auto-update). `dbus_bridge/{tank,switch}_service.py` and `publisher.py` import directly from it (`sys.path` is extended at runtime to include `ext/velib_python`). A plain `git clone` is all that's needed -- nothing extra to check out.

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

Installed as a SetupHelper package, entirely via SSH — no reliance on the Classic GUI's PackageManager menu (this system may run GUIv2, where that menu isn't available). The GitHub repo (`github.com/jsalbre/venus-onecontrol-can`) is public and set up for PackageManager's own GitHub-based update checking (see ARCHITECTURE.md's "Platform Constraints (Venus OS)" section) — but that only covers *updates*, not the first install: SetupHelper needs the package present on the Cerbo before it can check anything, so the first install downloads the same public archive directly onto the Cerbo — no build step, nothing to do on a development machine first:

```bash
mkdir -p /tmp/venus-onecontrol-can-download /data/venus-onecontrol-can /data/setupOptions/venus-onecontrol-can
wget -qO /tmp/venus-onecontrol-can-download/archive.tar.gz https://github.com/jsalbre/venus-onecontrol-can/archive/main.tar.gz
tar xzf /tmp/venus-onecontrol-can-download/archive.tar.gz -C /tmp/venus-onecontrol-can-download
mv /tmp/venus-onecontrol-can-download/venus-onecontrol-can-*/* /data/venus-onecontrol-can/
cp /data/venus-onecontrol-can/config.example.json /data/setupOptions/venus-onecontrol-can/config.json
/data/venus-onecontrol-can/setup install auto
```

`config.json` and `discovered_devices.json` live under `/data/setupOptions/venus-onecontrol-can/`, not inside the package directory itself -- see ARCHITECTURE.md's "Config Lives Outside the Package Directory" note for why (in short: `/data/venus-onecontrol-can/` gets entirely replaced on every package update, `/data/setupOptions/<packageName>/` is SetupHelper's own guaranteed-persistent location for exactly this kind of file).

### Updating an existing install

Preferred: PackageManager's own GitHub-based update checking, once the GitHub user/branch fields are set on this package in PackageManager's edit screen (`jsalbre` / `main`) -- it downloads, installs, and restarts the service on its own, no SSH needed.

Manual fallback (e.g. troubleshooting, or no internet access on the Cerbo): re-run the same download commands as a first install, but skip the `config.json` step -- it already exists and must not be overwritten:

```bash
mkdir -p /tmp/venus-onecontrol-can-download
wget -qO /tmp/venus-onecontrol-can-download/archive.tar.gz https://github.com/jsalbre/venus-onecontrol-can/archive/main.tar.gz
tar xzf /tmp/venus-onecontrol-can-download/archive.tar.gz -C /tmp/venus-onecontrol-can-download
mv /tmp/venus-onecontrol-can-download/venus-onecontrol-can-*/* /data/venus-onecontrol-can/
/data/venus-onecontrol-can/setup install auto
```

Either way, do **not** manually stop/start the service around an update. `setup`'s `INSTALL_SERVICES` step (`installService` in SetupHelper's `HelperResources/ServiceResources`) already diffs the run file and, if the service is currently up, sends it a clean `svc -t` restart itself.

(Note: `setup`'s `INSTALL_FILES` step is a no-op for this package -- it only matters for packages that patch pre-existing Venus OS system files via a `fileList`, which this project doesn't have or need. File placement is handled by the extraction above, or by PackageManager's own GitHub-based download/extract, not by `setup` itself.)

Edit `/data/setupOptions/venus-onecontrol-can/config.json` on the Cerbo to enable specific devices (`expose: true`) before or after installing — the service reloads config on every restart. Prefer `manage-devices` (below) over hand-editing where possible.

---

## Enabling and Managing Devices

Once running, the service logs every device it sees on the bus but isn't configured to `discovered_devices.json` (next to `config.json`, under `/data/setupOptions/venus-onecontrol-can/`). Use `manage-devices` to review that list and add a device interactively, or to change/remove a device already configured:

```bash
/data/venus-onecontrol-can/manage-devices
```

Its first menu shows a numbered list of addable devices (already-configured devices and devices with no supported service type are filtered out automatically); pick one to add it, confirming a friendly name (defaulting to Lippert's own name for it) and whether to expose it, then offers to restart the service. `device_class` is never asked for — it's inferred automatically from what the device itself broadcasts (its DEVICE_TYPE and FUNCTION_NAME), the same way a human reviewing the discovery log would work it out. For a commandable device_class (lights, pump, water heater), it also asks — separately, defaulting to No — whether to enable commands now; a device can be exposed (visible, read-only) without ever being made commandable.

Devices using the `(PRODUCT_ID, instance)` fallback key (unconfigured/unnamed inputs — see `ARCHITECTURE.md`'s stable-key design decision) are never offered, since multiple physical (non-)devices share that exact fallback identity and there's no single reliable device to enable there.

Choose `M) Manage existing devices` from that same first menu to rename a device, toggle its `expose`/`commands_enabled` flags, or remove it entirely (`device_class` still isn't editable there, for the same reason it's never asked at add time). The tool offers to restart the service after any change, but only some of them actually need one: toggling `commands_enabled` takes effect immediately, and so does adding/exposing a new device (the running service creates its D-Bus object live, the next time that device's `DEVICE_ID` broadcast arrives). Toggling `expose` off or removing a device is the one case that genuinely needs the restart — there's no live teardown path, so it stays on D-Bus until the service restarts.

For switch-kind devices, both the add flow and "Manage existing devices" also offer to set a **GUI panel group** (devices sharing a group name appear together in one Cerbo GUI panel) and **Show controls** (`Off`/`Always`/`Only Local`/`Only on VRM` — the same visibility choice Node-RED's own virtual switches offer). Unlike `expose`/`commands_enabled`, both of these are read once at service creation, so they need the restart the tool offers — editing them takes effect on the *next* restart, not immediately. Both are also directly editable from the Cerbo GUI's own per-output settings page if you'd rather not use `manage-devices` for it.

---

## Identifying Unconfigured Physical Outputs

Not every device on your OneControl bus broadcasts a real name. An output or input that's never been assigned a function on the Unity module reports a generic placeholder identity, and multiple unconfigured ports on the same module usually share that *exact* placeholder — so they can't be told apart just by looking at `discovered_devices.json`.

To figure out which physical port an unconfigured device actually is:

1. Run `list_unconfigured.py` against a known, already-named device on the same physical module, to list every other unconfigured device sharing that module:
   ```bash
   /data/venus-onecontrol-can/src/tools/list_unconfigured.py --reference-stable-key "function_name=<N>,function_instance=<M>"
   ```
   Each result includes a `device_instance` value — a sequential counter, scoped per device type, across your whole module (see `ARCHITECTURE.md`'s "device_instance" section for the full mechanism and a worked example). Cross-referencing that sequence against your own wiring documentation (which connector bank comes first, how many positions each bank has) is usually enough to form a strong hypothesis — this reasoning applies to any Unity module, though the actual sequence numbers and wiring will differ on every installation.
2. Confirm a hypothesis physically before trusting it, watching a multimeter on the terminal you think it is:
   ```bash
   /data/venus-onecontrol-can/src/tools/relay_blip.py --address 0x11 --confirm
   ```
   Without `--confirm` it only prints what it *would* do — always try it without `--confirm` first. This sends a real ON, waits (`--hold-seconds`, default 2s, capped at 4s), then OFF — **make sure nothing except a multimeter (voltage mode) is connected to the terminal you're testing.**

Only once you're physically certain which port you're looking at should you use `manage-system` (below) to actually assign it a name.

---

## Reconfiguring Your OneControl/Unity Module (`manage-system`)

**`manage-system` reconfigures the OneControl/Unity module itself — not just this bridge's D-Bus config.** Use it to assign a name/function to a currently-unused port, or change a device's on-module behavior setting (currently: dimming vs. latching). This is fundamentally different from `manage-devices`, which never touches your OneControl hardware at all.

**This is real, unguarded hardware access.** There is no config-gated safety net here the way there is for D-Bus commands — writing to the wrong target, or the wrong setting, can affect a live device you depend on (a water pump, water heater, or a light). If your installation has no OneControl touchscreen, there's no official Lippert tooling to revert a bad write either. `manage-system` itself requires typing `i understand` before it does anything, and every write is read back and verified — but that only catches a *failed* write, not a *wrong* one. **Identify your target physically first** (see above) before ever using its "configure a port" flow, and let it run its own post-write test blip to confirm before adding a newly-named device to your config.

```bash
/data/venus-onecontrol-can/manage-system
```

See `ARCHITECTURE.md`'s "PID Reconfiguration" design decision for full technical detail (exact PIDs, session requirements, what each menu option does).

---

## Logs

- App-level log: `/data/setupOptions/venus-onecontrol-can/logs/onecontrol-can.log` (rotates at 1MB x 7, human-readable timestamps). Controlled by `log_level` in `config.json` (default `INFO`; set to `DEBUG` for verbose per-write/per-command detail).
- Raw service output — also catches a crash from before the app's own logging even starts: `tail -n 100 /var/log/onecontrol-can/current` (runit/multilog capture, TAI64N-format timestamps).

---

## License

MIT — see `LICENSE`.
