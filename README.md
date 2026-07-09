# Mech Mapper

A Windows desktop utility that maps any joystick, HOTAS, or gamepad input to a **virtual Xbox 360 controller** and/or **emulated keyboard presses**, with per-control axis/button modes, named profiles, and a built-in input tester. Built for games (like *MechWarrior 5*) that only recognize a standard Xbox controller or that poll the keyboard per-frame in ways naive key-emulation libraries miss.

![Python](https://img.shields.io/badge/python-3.x-blue) ![Platform](https://img.shields.io/badge/platform-Windows-lightgrey)

## Features

- **Multi-device input** — reads every connected joystick simultaneously; bind different controls from different physical devices in the same profile.
- **Virtual Xbox 360 output** — drives a ViGEm virtual pad (via `vgamepad`) so games see a real Xbox controller no matter what hardware you're actually using.
- **Axis or Button mode** — each stick and trigger can be driven by its native axis, or by a pair of buttons acting as +/- (useful for HOTAS switches or extra buttons standing in for stick movement).
- **Axis inversion** per control.
- **30 keyboard emulation slots** — bind any joystick button, axis, or hat direction to a keyboard key, injected as a hardware-level scancode (`SendInput`) so DirectInput games pick it up, or as a standard virtual-key if the game expects that instead.
- **Minimum key-hold enforcement** — guarantees a fast tap is held long enough that per-frame game polling can't miss it.
- **Bind conflict detection** — refuses to bind a physical input that's already in use elsewhere; clear the old binding first.
- **Named profiles** — save/load any number of configs as JSON; the app auto-loads whichever profile you used last.
- **Input tester window** — live view of axes, hats, and button states for the selected device, for verifying wiring before you bind anything.
- **Deadzone handling** on analog sticks.
- **Cockpit-styled UI** — dark theme with amber/green status indicators for bound and armed states.

## Requirements

- Windows (uses `ctypes`/`SendInput` for keyboard injection and ViGEm for the virtual pad — no other OS is supported)
- Python 3.8+
- [ViGEmBus driver](https://github.com/ViGEm/ViGEmBus) installed (required by `vgamepad` to create the virtual controller)
- Python packages:
  - `pygame`
  - `vgamepad`

`tkinter` ships with standard Python installs on Windows and needs no separate install.

## Installation

1. Install the [ViGEmBus driver](https://github.com/ViGEm/ViGEmBus/releases).
2. Install dependencies:

   ```bash
   pip install pygame vgamepad
   ```

3. Run the app:

   ```bash
   python mech_mapper.py
   ```

The app requests administrator privileges on launch (required for `SendInput` and the virtual controller driver) and will relaunch itself elevated if needed.

## Usage

1. **Select a device** — pick a joystick from the device list. All connected devices are polled for bindings regardless of which one is selected; the selection only affects the input tester.
2. **Bind controls** — click **Map** next to a control and move the stick/press the button you want bound. Sticks and triggers can be set to *Axis* or *Button* mode via the dropdown in that row; in Button mode, bind separate `+`/`-` inputs.
3. **Invert** an axis with the checkbox if it's backwards.
4. **Keyboard binds** — in the Keyboard Binds panel, pick a key for any of the 30 slots, then click **Map** to bind the joystick input that should trigger it. Use **T** to test-fire a slot's key after a 3-second countdown (useful for confirming the injection path independent of any joystick).
5. **Key Mode** — choose between Scancode (DirectInput-compatible, works with most games) and Virtual Key (standard Windows key events) depending on what the target game expects.
6. **Enable** arms the virtual Xbox 360 controller; **KB Enable** arms keyboard emulation. Either can run independently.
7. **Save/Load** — name a profile and save; profiles are stored as `<name>.json` next to the script (or the compiled `.exe`) and the last-used profile reloads automatically on startup.
8. **Test Joystick** opens a live tester window showing raw axis, hat, and button state for the selected device.

## Configuration Storage

Profiles are saved as JSON files in the same directory as the script/executable. A legacy single-file config (`~/.xbox360_mapper_pro.json`, from an earlier version) is migrated automatically into a `default` profile the first time the app runs, then renamed so it won't be re-imported on later launches.

## Notes

- Deadzone for analog sticks defaults to `0.08`.
- Minimum keyboard hold time is `65ms`, tuned to survive per-frame polling in typical game loops.
- Keyboard emulation is Windows-only; on other platforms the keyboard panel reports itself unavailable.

## License

Add a license of your choice (e.g. MIT) here.
