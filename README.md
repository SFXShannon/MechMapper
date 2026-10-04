# Mech Mapper

A Windows desktop utility that maps any joystick, HOTAS, or gamepad input to a **virtual Xbox 360 controller** and/or **emulated keyboard presses**, with per-control axis/button modes, named profiles, and a built-in input tester. Built for games (like *MechWarrior 5*) that only recognize a standard Xbox controller, or that poll the keyboard per-frame in ways naive key-emulation libraries miss.

![Python](https://img.shields.io/badge/python-3.8%2B-blue) ![Platform](https://img.shields.io/badge/platform-Windows-lightgrey)

## Features

- **Multi-device input**: reads every connected joystick at once; bind controls from different devices in the same profile. Two identical sticks (same model, same GUID) are told apart.
- **Hot-plug**: devices are picked up and dropped automatically as you plug and unplug them. Bindings come back on their own when a device reconnects.
- **Virtual Xbox 360 output**: drives a ViGEm virtual pad (via `vgamepad`), so games see a real Xbox controller whatever hardware you use. Mech Mapper ignores its own virtual pad, so it can't map or read its own output.
- **Axis or Button mode**: each stick and trigger can be driven by its native axis, or by a pair of buttons acting as +/- (useful for HOTAS switches standing in for stick movement).
- **Axis inversion** for sticks (in either mode) and for triggers driven by an axis.
- **30 keyboard emulation slots**: bind any button, hat direction, or axis direction to a key. Keys are injected as hardware scancodes (`SendInput`) so DirectInput games pick them up, or as standard virtual keys if the game expects that instead. Includes the numpad and right-hand modifier keys.
- **Directional axis binds**: an axis bound to a key remembers which way you pushed it, so one throttle or twist axis can drive two keys (for example forward = `W`, back = `S`).
- **Hat diagonals**: a hat bound to *Up* also fires on Up-Left and Up-Right.
- **Minimum key-hold enforcement**: a fast tap is held for at least 65 ms so per-frame game polling can't miss it.
- **Bind conflict detection**: refuses to bind a physical input that's already in use elsewhere.
- **Named profiles**: saved as JSON in a `profiles` folder; the last-used profile loads on startup, and you're asked before unsaved changes are thrown away.
- **Input tester window**: live view of axes, hats, and buttons for the selected device.
- **Built-in updater**: installs new versions from GitHub Releases.
- **Cockpit-styled UI**: dark theme with amber/green status indicators. A binding shows amber when its device isn't plugged in.

## Requirements

- Windows (uses `SendInput` for keyboard injection and ViGEm for the virtual pad)
- Python 3.8+
- The [ViGEmBus driver](https://github.com/nefarius/ViGEmBus/releases). The app offers to install the bundled copy (in `vendor/`) on first run if it's missing.
- Python packages from `requirements.txt` (`pygame`, `vgamepad`). `tkinter` ships with the standard Windows Python installer.

## Running from source

```bash
pip install -r requirements.txt
python MW5_MECHMAPPER.py
```

The app asks for administrator rights on launch and relaunches itself elevated. Admin rights are needed so key presses reach games that run as administrator (for example a game hooked by UEVR). If you decline the prompt you can still continue without them.

For development without the elevation prompt, set `MECHMAPPER_NO_ELEVATE=1`.

## Building a standalone .exe

Run `build.bat` (it installs PyInstaller if needed), or run the same command by hand:

```bash
pyinstaller --noconfirm --onefile --windowed --uac-admin ^
    --add-data "vendor;vendor" --collect-all vgamepad ^
    --icon mech_mapper.ico --add-data "mech_mapper.ico;." ^
    MW5_MECHMAPPER.py
```

- `--uac-admin` makes Windows ask for admin rights when the exe starts.
- `--collect-all vgamepad` bundles `ViGEmClient.dll`, which PyInstaller misses on its own (without it the exe crashes on launch).
- `vendor/` carries the ViGEmBus installer, so a missing driver is installed on first run (after one admin prompt). The app then carries on without a restart where possible.
- `mech_mapper.ico` (16-256 px) is the app and exe icon; `mech_mapper.png` is a 256 px copy for the repo or release page. `build.bat` skips the icon if the file is missing.

If you update the bundled driver, replace the file in `vendor/` and update `VIGEM_INSTALLER` near the top of `MW5_MECHMAPPER.py` to match.

## Updates and releases

Mech Mapper checks GitHub for a newer release a few seconds after it starts, and you can check any time by clicking the version text in the top-right of the window. When a newer version exists you can install it now (it downloads the new `.exe`, verifies it, swaps it in and restarts), skip that version, or be reminded next time. Running from source, it offers to open the release page instead.

To publish a release:

1. Set `APP_VERSION` near the top of `MW5_MECHMAPPER.py` (for example `2.1.0`) and commit.
2. Tag and push: `git tag v2.1.0` then `git push origin v2.1.0`.
3. The **Release** GitHub Action builds the exe and attaches it to a new release. It refuses to build if the tag and `APP_VERSION` don't match.

The updater uses GitHub's public API, so **the repository must be public** for other people's copies to see releases. For a private repo, set a GitHub token in the `MECHMAPPER_GITHUB_TOKEN` environment variable on the PCs that should update. If an update can't replace the exe, the reason is written to `update.log` next to the exe.

## Usage

1. **Devices**: every connected device is read for binds. Selecting one only chooses which device the input tester shows.
2. **Bind controls**: click **Map** next to a control, then move the axis or press the button you want. Press **Esc** (or the Cancel button) to stop waiting; mapping also gives up after 10 seconds. Sticks and triggers can be set to *Axis* or *Button* mode; in Button mode a stick gets separate `+`/`-` binds.
3. **Invert** an axis with the checkbox if it's backwards.
4. **Keyboard binds**: pick a key for a slot, then click **Map** and press a button, push a hat, or move an axis in the direction that should trigger it. Use **T** to test-fire the slot's key after a 3-second countdown, without involving the joystick.
5. **Key Mode**: *Scancode* (DirectInput, works with most games) or *Virtual Key* (standard Windows key events).
6. **Enable** arms the virtual Xbox 360 controller; **KB Enable** arms keyboard emulation. Either can run on its own.
7. **Profiles**: type a name and **Save**; pick one from the list to load it. **Folder** opens the profiles folder.
8. **Test Joystick** opens the live tester for the selected device.

## Configuration storage

Profiles are stored as `profiles/<name>.json` next to the script or exe, and `profiles/.last_profile` remembers which one to load at startup. On first run, profiles from older versions are **copied** into this folder (originals are left in place) from:

- `.json` profiles saved next to the script or exe by the previous version
- `%USERPROFILE%\.mech_mapper_configs\`
- the very old single-file `%USERPROFILE%\.xbox360_mapper_pro.json` (becomes the `default` profile)

## Notes

- Analog stick deadzone is `0.08`, rescaled so there's no jump at the edge of the deadzone.
- Two identical devices are numbered in the order Windows reports them. If they swap order after a reboot, swap their binds or rebind.
- Keyboard emulation is Windows-only.

## License

Add a license of your choice (e.g. MIT) here.
