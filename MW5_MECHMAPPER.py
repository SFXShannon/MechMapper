"""
Mech Mapper - map joystick / HOTAS input to a virtual Xbox 360 pad and to
keyboard presses, for games (like MechWarrior 5) that only understand an
Xbox controller or a keyboard.

Architecture
------------
* InputEngine runs on ONE background thread and owns everything pygame/SDL
  and the virtual pad. It reads every device once per tick into an immutable
  snapshot, then uses that snapshot for mapping capture, the virtual pad and
  the keyboard slots.
* The Tk UI runs on the main thread. It never touches pygame. It changes
  configuration through small lock-protected setters on the engine, and it
  receives news (status text, new bindings, device changes) through a queue
  it polls with root.after().
"""

import ctypes
import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

# SDL hints must be set before pygame is imported / initialised.
os.environ.setdefault("SDL_JOYSTICK_HIDAPI", "1")
# We never open an SDL window, so make sure SDL keeps delivering joystick
# input while another window (the game) has focus.
os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import pygame  # noqa: E402
import tkinter as tk  # noqa: E402
from tkinter import ttk, messagebox  # noqa: E402

APP_NAME = "Mech Mapper"
APP_VERSION = "2.0.0"      # must match the GitHub release tag (v2.0.0) - the release workflow checks
GITHUB_REPO = "SFXShannon/MechMapper"
IS_WINDOWS = os.name == "nt"

vg = None  # the vgamepad module, set in main() once the driver is confirmed

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def resource_path(relative_path):
    """Resolve a bundled resource: next to the script, or inside the PyInstaller bundle."""
    base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

PROFILE_DIR = os.path.join(APP_DIR, "profiles")
LAST_PROFILE_FILE = os.path.join(PROFILE_DIR, ".last_profile")
APP_SETTINGS_FILE = os.path.join(PROFILE_DIR, ".app_settings.json")
# Older locations we migrate profiles from (copied, never deleted)
LEGACY_PROFILE_DIRS = [APP_DIR, os.path.join(os.path.expanduser("~"), ".mech_mapper_configs")]
LEGACY_SINGLE_CONFIG = os.path.join(os.path.expanduser("~"), ".xbox360_mapper_pro.json")

VIGEM_INSTALLER = os.path.join("vendor", "ViGEmBus_1.22.0_x64_x86_arm64.exe")
ICON_FILE = "mech_mapper.ico"

# ---------------------------------------------------------------------------
# Tuning
# ---------------------------------------------------------------------------

DEADZONE = 0.08             # analog stick deadzone (rescaled, so no jump at the edge)
MIN_KEY_HOLD = 0.065        # seconds an injected key is held at minimum, so frame polling can't miss it
POLL_INTERVAL = 0.005       # engine tick (~200 Hz)
MAP_TIMEOUT = 10.0          # seconds to wait for input when mapping
MAP_AXIS_THRESHOLD = 0.6    # how far an axis must move from rest to be captured
AXIS_PRESS_THRESHOLD = 0.5  # how far an axis must move to count as "pressed" for digital binds
HAT_DIAGONAL_HOLD = 0.25    # a diagonal must be held this long to be captured (avoids grabbing a slip)
VIRTUAL_PAD_WINDOW = 4.0    # seconds after arming during which a new Xbox device is assumed to be ours

# ---------------------------------------------------------------------------
# Windows helpers: message boxes, elevation, ViGEmBus driver
# ---------------------------------------------------------------------------

MB_OK, MB_OKCANCEL, MB_YESNO = 0x0, 0x1, 0x4
MB_ICONERROR, MB_ICONWARNING, MB_ICONINFO = 0x10, 0x30, 0x40
IDOK, IDYES = 1, 6


def message_box(text, title, flags=MB_OK):
    if IS_WINDOWS:
        return ctypes.windll.user32.MessageBoxW(0, text, title, flags)
    print(f"[{title}] {text}", file=sys.stderr)
    return IDOK


def is_admin():
    if not IS_WINDOWS:
        return True
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def clean_child_environment():
    """Environment for starting a fresh copy of this program.

    A one-file PyInstaller exe sets private variables (and Tcl/Tk paths) that
    point into its own temporary folder. A new copy that inherits them looks
    for that folder instead of unpacking its own, and fails with
    "Failed to load Python DLL" once the old copy has exited.
    """
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("_PYI_") and k not in ("_MEIPASS2", "TCL_LIBRARY", "TK_LIBRARY", "TKPATH")}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def relaunch_elevated():
    """Start a new elevated copy of this program. Returns True if Windows accepted the request."""
    if getattr(sys, "frozen", False):
        exe, args = sys.executable, sys.argv[1:]
    else:
        exe, args = sys.executable, [os.path.abspath(sys.argv[0])] + sys.argv[1:]
    # list2cmdline quotes arguments properly, so paths with spaces survive
    params = subprocess.list2cmdline(args)
    if getattr(sys, "frozen", False):
        # ShellExecute passes our environment on; see clean_child_environment()
        os.environ.clear()
        os.environ.update(clean_child_environment())
    shell_execute = ctypes.windll.shell32.ShellExecuteW
    shell_execute.restype = ctypes.c_ssize_t
    rc = shell_execute(None, "runas", exe, params, os.getcwd(), 1)
    return rc > 32


def ensure_elevated():
    """Keyboard injection into an elevated game (e.g. one hooked by UEVR) needs admin rights."""
    if not IS_WINDOWS or os.environ.get("MECHMAPPER_NO_ELEVATE") or is_admin():
        return
    if relaunch_elevated():
        sys.exit(0)
    choice = message_box(
        "Mech Mapper couldn't get administrator rights (the Windows prompt was "
        "declined or failed).\n\nWithout them, keyboard binds can't reach games "
        "running as administrator - for example a game hooked by UEVR.\n\n"
        "Continue without administrator rights?",
        APP_NAME, MB_YESNO | MB_ICONWARNING)
    if choice != IDYES:
        sys.exit(1)


def _import_vgamepad():
    # Drop any half-imported copy so a retry after the driver install starts clean
    for name in [m for m in sys.modules if m == "vgamepad" or m.startswith("vgamepad.")]:
        del sys.modules[name]
    import vgamepad
    return vgamepad


def ensure_vigembus():
    """Import vgamepad, installing the bundled ViGEmBus driver first if it's missing."""
    try:
        return _import_vgamepad()
    except ModuleNotFoundError as e:
        if (e.name or "").split(".")[0] != "vgamepad":
            raise
        message_box("The 'vgamepad' Python package isn't installed.\n\n"
                    "Run:  pip install -r requirements.txt",
                    "Missing package", MB_ICONERROR)
        sys.exit(1)
    except Exception as e:
        if "VIGEM_ERROR_BUS_NOT_FOUND" not in str(e):
            raise

    installer_path = resource_path(VIGEM_INSTALLER)
    if not os.path.exists(installer_path):
        message_box("Mech Mapper needs the ViGEmBus driver to create a virtual "
                    "Xbox 360 controller, but the bundled installer is missing "
                    "from this build.\n\nDownload it manually from:\n"
                    "https://github.com/nefarius/ViGEmBus/releases",
                    "ViGEmBus driver missing", MB_ICONERROR)
        sys.exit(1)

    choice = message_box("Mech Mapper needs a one-time driver install (ViGEmBus) to "
                         "create the virtual Xbox 360 controller.\n\n"
                         "Windows may show an administrator approval prompt. Continue?",
                         "Driver install required", MB_OKCANCEL | MB_ICONINFO)
    if choice != IDOK:
        sys.exit(1)

    # The bundled installer is an Advanced Installer package: /exenoui hides the
    # bootstrapper UI and /qn /norestart go to Windows Installer. -PassThru plus
    # 'exit $p.ExitCode' makes PowerShell report the installer's real result
    # (plain Start-Process -Wait always exits 0).
    ps_cmd = ("$p = Start-Process -FilePath '{}' -ArgumentList '/exenoui','/qn','/norestart' "
              "-Verb RunAs -Wait -PassThru; exit $p.ExitCode").format(installer_path.replace("'", "''"))
    result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
                            capture_output=True,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    # 0 = ok, 3010/1641 = ok but reboot suggested, 1638 = a version is already installed
    if result.returncode not in (0, 3010, 1641, 1638):
        message_box("The driver install didn't complete (it may have been cancelled "
                    "at the Windows prompt, or failed). Mech Mapper can't create the "
                    "virtual controller without it.\n\n"
                    "Details: return code {}".format(result.returncode),
                    "Driver install failed", MB_ICONERROR)
        sys.exit(1)

    try:
        return _import_vgamepad()
    except Exception:
        message_box("The ViGEmBus driver was installed. Please restart Mech Mapper"
                    + (" (Windows asked for a reboot, so restart the PC if it still fails)."
                       if result.returncode in (3010, 1641) else "."),
                    "Driver installed", MB_ICONINFO)
        sys.exit(0)


# ---------------------------------------------------------------------------
# Controls
# ---------------------------------------------------------------------------

# name -> (description, can_be_axis)
CONTROLS = {
    "LX": ("Move/Throttle (Steer)", True), "LY": ("Move/Throttle (Fwd/Back)", True),
    "LS": ("Jump Jets (LS Click)", False),
    "RX": ("Look/Aim (Turn)", True), "RY": ("Look/Aim (Pitch)", True),
    "RS": ("Target Enemy (RS Click)", False),
    "LT": ("Fire Weapon Group 1", True), "RT": ("Fire Weapon Group 2", True),
    "LB": ("Fire Weapon Group 3", False), "RB": ("Fire Weapon Group 4", False),
    "Y": ("Fire Weapon Group 5", False),
    "A": ("Full Stop/Interact", False), "B": ("Cancel Menu", False),
    "X": ("Cycle Zoom", False),
    "D_UP": ("Action Panel Up", False), "D_DOWN": ("Action Panel Down", False),
    "D_LEFT": ("Action Panel Left", False), "D_RIGHT": ("Action Panel Right", False),
    "START": ("Pause (Menu btn)", False), "BACK": ("Show BattleGrid (View btn)", False),
}

# Digital pad controls -> vgamepad XUSB_BUTTON attribute names
BUTTON_TARGETS = {
    "D_UP": "XUSB_GAMEPAD_DPAD_UP", "D_DOWN": "XUSB_GAMEPAD_DPAD_DOWN",
    "D_LEFT": "XUSB_GAMEPAD_DPAD_LEFT", "D_RIGHT": "XUSB_GAMEPAD_DPAD_RIGHT",
    "RS": "XUSB_GAMEPAD_RIGHT_THUMB", "LS": "XUSB_GAMEPAD_LEFT_THUMB",
    "RB": "XUSB_GAMEPAD_RIGHT_SHOULDER", "LB": "XUSB_GAMEPAD_LEFT_SHOULDER",
    "A": "XUSB_GAMEPAD_A", "B": "XUSB_GAMEPAD_B", "X": "XUSB_GAMEPAD_X", "Y": "XUSB_GAMEPAD_Y",
    "START": "XUSB_GAMEPAD_START", "BACK": "XUSB_GAMEPAD_BACK",
}
STICK_TARGETS = ("LX", "LY", "RX", "RY")
TRIGGER_TARGETS = ("LT", "RT")
ANALOG_TARGETS = STICK_TARGETS + TRIGGER_TARGETS
NUM_KB_SLOTS = 30


def default_modes():
    return {k: "Axis" for k in ANALOG_TARGETS}


def split_base(key):
    """'LX_POS' -> 'LX'; anything else is returned unchanged."""
    if key.endswith("_POS") or key.endswith("_NEG"):
        return key[:-4]
    return key


def is_split(key):
    return key.endswith("_POS") or key.endswith("_NEG")


# ---------------------------------------------------------------------------
# Keyboard emulation (SendInput)
# ---------------------------------------------------------------------------

# name -> (set-1 scancode, is_extended). Scancodes are what DirectInput games read.
SCANCODES = {
    "A": (0x1E, False), "B": (0x30, False), "C": (0x2E, False), "D": (0x20, False),
    "E": (0x12, False), "F": (0x21, False), "G": (0x22, False), "H": (0x23, False),
    "I": (0x17, False), "J": (0x24, False), "K": (0x25, False), "L": (0x26, False),
    "M": (0x32, False), "N": (0x31, False), "O": (0x18, False), "P": (0x19, False),
    "Q": (0x10, False), "R": (0x13, False), "S": (0x1F, False), "T": (0x14, False),
    "U": (0x16, False), "V": (0x2F, False), "W": (0x11, False), "X": (0x2D, False),
    "Y": (0x15, False), "Z": (0x2C, False),
    "1": (0x02, False), "2": (0x03, False), "3": (0x04, False), "4": (0x05, False),
    "5": (0x06, False), "6": (0x07, False), "7": (0x08, False), "8": (0x09, False),
    "9": (0x0A, False), "0": (0x0B, False),
    "F1": (0x3B, False), "F2": (0x3C, False), "F3": (0x3D, False), "F4": (0x3E, False),
    "F5": (0x3F, False), "F6": (0x40, False), "F7": (0x41, False), "F8": (0x42, False),
    "F9": (0x43, False), "F10": (0x44, False), "F11": (0x57, False), "F12": (0x58, False),
    "SPACE": (0x39, False), "ENTER": (0x1C, False), "TAB": (0x0F, False),
    "ESC": (0x01, False), "BACKSPACE": (0x0E, False), "CAPSLOCK": (0x3A, False),
    "LSHIFT": (0x2A, False), "LCTRL": (0x1D, False), "LALT": (0x38, False),
    "RSHIFT": (0x36, False), "RCTRL": (0x1D, True), "RALT": (0x38, True),
    "MINUS": (0x0C, False), "EQUALS": (0x0D, False),
    "LBRACKET": (0x1A, False), "RBRACKET": (0x1B, False),
    "SEMICOLON": (0x27, False), "APOSTROPHE": (0x28, False), "GRAVE": (0x29, False),
    "BACKSLASH": (0x2B, False), "COMMA": (0x33, False), "PERIOD": (0x34, False),
    "SLASH": (0x35, False),
    "UP": (0x48, True), "DOWN": (0x50, True), "LEFT": (0x4B, True), "RIGHT": (0x4D, True),
    "HOME": (0x47, True), "END": (0x4F, True), "PGUP": (0x49, True), "PGDN": (0x51, True),
    "INSERT": (0x52, True), "DELETE": (0x53, True),
    "NUM0": (0x52, False), "NUM1": (0x4F, False), "NUM2": (0x50, False), "NUM3": (0x51, False),
    "NUM4": (0x4B, False), "NUM5": (0x4C, False), "NUM6": (0x4D, False), "NUM7": (0x47, False),
    "NUM8": (0x48, False), "NUM9": (0x49, False),
    "NUM_DECIMAL": (0x53, False), "NUM_PLUS": (0x4E, False), "NUM_MINUS": (0x4A, False),
    "NUM_MULTIPLY": (0x37, False), "NUM_DIVIDE": (0x35, True), "NUM_ENTER": (0x1C, True),
}
KEY_OPTIONS = [""] + list(SCANCODES.keys())

# Virtual-key codes for the same keys; some games read VK events instead of scancodes
VKCODES = {
    **{chr(c): c for c in range(0x41, 0x5B)},  # A-Z
    **{chr(c): c for c in range(0x30, 0x3A)},  # 0-9
    **{f"F{i}": 0x6F + i for i in range(1, 13)},  # F1-F12
    "SPACE": 0x20, "ENTER": 0x0D, "TAB": 0x09, "ESC": 0x1B,
    "BACKSPACE": 0x08, "CAPSLOCK": 0x14,
    "LSHIFT": 0xA0, "LCTRL": 0xA2, "LALT": 0xA4,
    "RSHIFT": 0xA1, "RCTRL": 0xA3, "RALT": 0xA5,
    "MINUS": 0xBD, "EQUALS": 0xBB,
    "LBRACKET": 0xDB, "RBRACKET": 0xDD,
    "SEMICOLON": 0xBA, "APOSTROPHE": 0xDE, "GRAVE": 0xC0,
    "BACKSLASH": 0xDC, "COMMA": 0xBC, "PERIOD": 0xBE, "SLASH": 0xBF,
    "UP": 0x26, "DOWN": 0x28, "LEFT": 0x25, "RIGHT": 0x27,
    "HOME": 0x24, "END": 0x23, "PGUP": 0x21, "PGDN": 0x22,
    "INSERT": 0x2D, "DELETE": 0x2E,
    **{f"NUM{i}": 0x60 + i for i in range(10)},
    "NUM_DECIMAL": 0x6E, "NUM_PLUS": 0x6B, "NUM_MINUS": 0x6D,
    "NUM_MULTIPLY": 0x6A, "NUM_DIVIDE": 0x6F, "NUM_ENTER": 0x0D,
}

KEY_MODES = ["Scancode (DirectInput)", "Virtual Key (Standard)"]

if IS_WINDOWS:
    from ctypes import wintypes

    _KEYEVENTF_EXTENDEDKEY = 0x0001
    _KEYEVENTF_KEYUP = 0x0002
    _KEYEVENTF_SCANCODE = 0x0008
    _INPUT_KEYBOARD = 1

    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.c_size_t)]

    class _MOUSEINPUT(ctypes.Structure):  # only here so the union has the right size
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                    ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT)]

    class _INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("union", _INPUTUNION)]

    _SendInput = ctypes.windll.user32.SendInput
    _SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int)
    _SendInput.restype = wintypes.UINT

    def send_key(key_name, down, use_vk=False):
        """Inject one key event. Returns False if Windows rejected it."""
        if key_name not in SCANCODES:
            return False
        sc, extended = SCANCODES[key_name]
        flags = 0
        if use_vk:
            vk = VKCODES.get(key_name, 0)
        else:
            vk = 0
            flags |= _KEYEVENTF_SCANCODE
        if extended:
            flags |= _KEYEVENTF_EXTENDEDKEY
        if not down:
            flags |= _KEYEVENTF_KEYUP
        inp = _INPUT(type=_INPUT_KEYBOARD,
                     union=_INPUTUNION(ki=_KEYBDINPUT(vk, sc, flags, 0, 0)))
        return _SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT)) == 1

    KEYBOARD_AVAILABLE = True
else:
    def send_key(key_name, down, use_vk=False):
        return False

    KEYBOARD_AVAILABLE = False

# ---------------------------------------------------------------------------
# Binding helpers (pure functions, shared by engine and UI)
# ---------------------------------------------------------------------------

HAT_NAMES = {
    (0, 1): "Up", (0, -1): "Down", (-1, 0): "Left", (1, 0): "Right",
    (1, 1): "Up-Right", (-1, 1): "Up-Left", (1, -1): "Down-Right", (-1, -1): "Down-Left",
}


def device_key(guid, n=1):
    """Key for the n-th connected device with this GUID (identical sticks share a GUID)."""
    return guid if n <= 1 else f"{guid}#{n}"


def binding_device_key(info):
    if not info or not info.get("dev"):
        return None
    return device_key(info["dev"], info.get("dev_n", 1))


def axis_value(info, snap):
    state = snap.get(binding_device_key(info))
    if state is None:
        return None
    axes = state["axes"]
    idx = info.get("index", -1)
    return axes[idx] if 0 <= idx < len(axes) else None


def input_active(info, snap):
    """Is a binding currently 'pressed'? Works for buttons, hat directions and axes."""
    if not info:
        return False
    state = snap.get(binding_device_key(info))
    if state is None:
        return False
    t = info.get("type")
    if t == "BTN":
        buttons = state["buttons"]
        i = info.get("index", -1)
        return 0 <= i < len(buttons) and buttons[i]
    if t == "HAT":
        hats = state["hats"]
        h = info.get("hat", -1)
        if not 0 <= h < len(hats):
            return False
        hx, hy = hats[h]
        if (hx, hy) == (0, 0):
            return False
        bx, by = info.get("x", 0), info.get("y", 0)
        if (bx, by) == (0, 0):
            return False
        # A cardinal bind (e.g. Up) also fires on its diagonals (Up-Left/Up-Right);
        # a diagonal bind needs both components.
        return (bx == 0 or hx == bx) and (by == 0 or hy == by)
    if t == "AXIS":
        v = axis_value(info, snap)
        if v is None:
            return False
        delta = v - info.get("rest", 0.0)
        d = info.get("dir")
        if d:
            return delta * d > AXIS_PRESS_THRESHOLD
        return abs(delta) > AXIS_PRESS_THRESHOLD  # bindings made before direction was recorded
    return False


def find_conflict(mapping, new_info, exclude_key):
    """Return the key of an existing binding that uses the same physical input, if any."""
    new_dev = (new_info.get("dev"), new_info.get("dev_n", 1))
    t = new_info.get("type")
    for key, info in mapping.items():
        if key == exclude_key or not isinstance(info, dict):
            continue
        if (info.get("dev"), info.get("dev_n", 1)) != new_dev or info.get("type") != t:
            continue
        if t == "BTN" and info.get("index") == new_info.get("index"):
            return key
        if t == "HAT" and (info.get("hat"), info.get("x"), info.get("y")) == \
                (new_info.get("hat"), new_info.get("x"), new_info.get("y")):
            return key
        if t == "AXIS" and info.get("index") == new_info.get("index"):
            d1, d2 = info.get("dir"), new_info.get("dir")
            # opposite directions of one axis may drive two different digital binds
            if not d1 or not d2 or d1 == d2:
                return key
    return None


def apply_deadzone(v, dz=DEADZONE):
    a = abs(v)
    if a < dz:
        return 0.0
    scaled = min(1.0, (a - dz) / (1.0 - dz))
    return scaled if v > 0 else -scaled


def trigger_value(info, raw, invert=False):
    """Map an axis to 0..1 measured from where it rested when it was bound."""
    rest = info.get("rest", 0.0)
    span = max(abs(1.0 - rest), abs(-1.0 - rest), 0.01)
    val = max(0.0, min(1.0, abs(raw - rest) / span))
    return 1.0 - val if invert else val


def looks_like_xbox_pad(name):
    n = (name or "").lower()
    return "xbox" in n or "x-box" in n or "xinput" in n


# ---------------------------------------------------------------------------
# Input engine (background thread; owns pygame and the virtual pad)
# ---------------------------------------------------------------------------


class InputEngine:
    def __init__(self, gamepad_module=None):
        self.vg = gamepad_module
        self.lock = threading.RLock()

        # ---- configuration: written by the UI under self.lock, copied each tick
        self.mapping = {}
        self.modes = default_modes()
        self.invert = {}
        self.kb_keys = {i: "" for i in range(NUM_KB_SLOTS)}
        self.use_vk = False
        self.kb_enabled = False

        # ---- published state (replaced wholesale, safe to read from the UI)
        self.snapshot = {}       # device key -> {"axes": (...), "buttons": (...), "hats": (...)}
        self.devices = []        # list of dicts: key, name, virtual, axes, buttons, hats
        self.pad_enabled = False
        self.mapping_target = None

        self.events = queue.Queue()  # engine -> UI
        self._cmds = queue.Queue()   # UI -> engine
        self._stop = threading.Event()
        self._thread = None

        # ---- engine-thread-only state
        self._joys = {}          # instance id -> pygame Joystick
        self._virtual_ids = set()
        self._dev_key_by_id = {}
        self._dev_name_by_key = {}
        self._pad = None
        self._pad_held = {}
        self._pad_last = None
        self._kb_held = {}       # slot -> (key_name, use_vk, time_down)
        self._expect_virtual_until = 0.0
        self._map_rest = {}
        self._map_deadline = 0.0
        self._map_pending_hat = None
        self._sendinput_warned = False
        self._error_reported = False
        self._started_at = 0.0

    # ---------------- lifecycle ----------------

    def start(self):
        self._thread = threading.Thread(target=self._run, name="MechMapperInput", daemon=True)
        self._thread.start()

    def stop(self, timeout=1.5):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)

    def _post(self, *event):
        self.events.put(event)

    def status(self, text, kind="info"):
        self._post("status", text, kind)

    # ---------------- UI-facing API (any thread) ----------------

    def request(self, *cmd):
        self._cmds.put(cmd)

    def set_mode(self, name, mode):
        with self.lock:
            self.modes[name] = mode

    def set_invert(self, name, value):
        with self.lock:
            self.invert[name] = bool(value)

    def set_kb_key(self, slot, key_name):
        with self.lock:
            self.kb_keys[slot] = key_name

    def set_use_vk(self, value):
        with self.lock:
            self.use_vk = bool(value)

    def set_kb_enabled(self, value):
        with self.lock:
            self.kb_enabled = bool(value) and KEYBOARD_AVAILABLE

    def get_binding(self, key):
        with self.lock:
            return self.mapping.get(key)

    def clear_binding(self, name):
        with self.lock:
            for k in (name, f"{name}_POS", f"{name}_NEG"):
                self.mapping.pop(k, None)

    def clear_all(self):
        with self.lock:
            self.mapping = {}
            self.modes = default_modes()
            self.invert = {}
            self.kb_keys = {i: "" for i in range(NUM_KB_SLOTS)}

    def export_config(self):
        with self.lock:
            return {
                "version": 2,
                "mapping_info": json.loads(json.dumps(self.mapping)),
                "control_modes": dict(self.modes),
                "invert": dict(self.invert),
                "kb_keys": {str(k): v for k, v in self.kb_keys.items()},
                "key_mode": KEY_MODES[1] if self.use_vk else KEY_MODES[0],
            }

    def import_config(self, data):
        """Replace the configuration from profile data (any version)."""
        mapping = {k: dict(v) for k, v in (data.get("mapping_info") or {}).items()
                   if isinstance(v, dict)}
        # Old profiles stored one joystick for the whole profile; stamp any
        # binding without a device so it keeps working with multiple devices.
        legacy_guid = data.get("joystick_guid")
        if legacy_guid:
            legacy_name = (data.get("joystick_name") or "")[:18]
            for info in mapping.values():
                if "dev" not in info:
                    info["dev"] = legacy_guid
                    if legacy_name:
                        info["dev_name"] = legacy_name
        modes = default_modes()
        for k, v in (data.get("control_modes") or {}).items():
            if k in modes and v in ("Axis", "Button"):
                modes[k] = v
        invert = {k: bool(v) for k, v in (data.get("invert") or {}).items()}
        kb = {i: "" for i in range(NUM_KB_SLOTS)}
        for k, v in (data.get("kb_keys") or {}).items():
            try:
                i = int(k)
            except (TypeError, ValueError):
                continue
            if 0 <= i < NUM_KB_SLOTS:
                kb[i] = v if v in SCANCODES else ""
        with self.lock:
            self.mapping = mapping
            self.modes = modes
            self.invert = invert
            self.kb_keys = kb
            self.use_vk = data.get("key_mode") == KEY_MODES[1]

    # ---------------- engine thread ----------------

    def _run(self):
        try:
            pygame.display.init()   # pygame's event queue needs the video subsystem
            pygame.joystick.init()
            self._started_at = time.perf_counter()
            self._enumerate_devices()
        except Exception as e:
            self.status(f"ERROR: couldn't start the input system: {e}", "error")
            return
        try:
            while not self._stop.is_set():
                try:
                    self._tick(time.perf_counter())
                    self._error_reported = False
                except Exception as e:  # never let one bad tick kill input handling
                    if not self._error_reported:
                        self._error_reported = True
                        self.status(f"ERROR in input loop: {type(e).__name__}: {e}", "error")
                time.sleep(POLL_INTERVAL)
        finally:
            self._shutdown_outputs()
            try:
                pygame.joystick.quit()
                pygame.display.quit()
            except Exception:
                pass

    def _tick(self, now):
        self._process_commands(now)
        self._handle_sdl_events(now)
        snap = self._read_snapshot()
        with self.lock:
            mapping = dict(self.mapping)
            modes = dict(self.modes)
            invert = dict(self.invert)
            kb_keys = dict(self.kb_keys)
            use_vk = self.use_vk
            kb_enabled = self.kb_enabled
        if self.mapping_target:
            self._poll_mapping(snap, modes, now)
        self._drive_pad(snap, mapping, modes, invert)
        self._drive_keyboard(snap, mapping, kb_keys, use_vk,
                             kb_enabled and not self.mapping_target, now)

    def _process_commands(self, now):
        while True:
            try:
                cmd = self._cmds.get_nowait()
            except queue.Empty:
                return
            name, args = cmd[0], cmd[1:]
            if name == "refresh":
                self._refresh_devices(now)
            elif name == "map":
                self._begin_mapping(args[0], now)
            elif name == "cancel_map":
                if self.mapping_target:
                    self._end_mapping()
                    self.status("Mapping cancelled", "info")
            elif name == "pad":
                self._set_pad(args[0], now)

    # ---- devices ----

    def _handle_sdl_events(self, now):
        changed = False
        for ev in pygame.event.get():
            if ev.type == pygame.JOYDEVICEADDED:
                try:
                    changed |= self._add_device(pygame.joystick.Joystick(ev.device_index), now)
                except pygame.error:
                    pass
            elif ev.type == pygame.JOYDEVICEREMOVED:
                changed |= self._remove_device(ev.instance_id)
        if changed:
            self._reindex()

    def _add_device(self, joy, now):
        joy.init()
        iid = joy.get_instance_id()
        if iid in self._joys:
            return False
        self._joys[iid] = joy
        name = joy.get_name()
        if self._pad is not None and now < self._expect_virtual_until and looks_like_xbox_pad(name):
            # This is (almost certainly) the virtual pad we just created. Never read it,
            # or mapping could capture our own output and create a feedback loop.
            self._virtual_ids.add(iid)
            self._expect_virtual_until = 0.0
        elif now - self._started_at > 1.5:
            self.status(f"Connected: {name}", "ok")
        return True

    def _remove_device(self, iid):
        joy = self._joys.pop(iid, None)
        if joy is None:
            return False
        was_virtual = iid in self._virtual_ids
        self._virtual_ids.discard(iid)
        if not was_virtual:
            try:
                name = joy.get_name()
            except pygame.error:
                name = "device"
            self.status(f"Disconnected: {name}", "warn")
        return True

    def _reindex(self):
        counts = {}
        key_by_id, name_by_key, devices = {}, {}, []
        for iid in sorted(self._joys):
            joy = self._joys[iid]
            try:
                name = joy.get_name()
                guid = joy.get_guid()
                shape = (joy.get_numaxes(), joy.get_numbuttons(), joy.get_numhats())
            except pygame.error:
                continue
            virtual = iid in self._virtual_ids
            key = None
            if not virtual:
                counts[guid] = counts.get(guid, 0) + 1
                key = device_key(guid, counts[guid])
                key_by_id[iid] = key
                name_by_key[key] = name
            devices.append({"key": key, "guid": guid, "n": counts.get(guid, 1), "name": name,
                            "virtual": virtual, "axes": shape[0], "buttons": shape[1],
                            "hats": shape[2]})
        self._dev_key_by_id = key_by_id
        self._dev_name_by_key = name_by_key
        self.devices = devices
        self._post("devices")

    def _refresh_devices(self, now):
        had_pad = self._pad is not None
        if had_pad:
            self._set_pad(False, now, quiet=True)
        pygame.joystick.quit()
        self._joys.clear()
        self._virtual_ids.clear()
        pygame.joystick.init()
        self._enumerate_devices()
        if had_pad:
            self._set_pad(True, time.perf_counter(), quiet=True)
        n = sum(1 for d in self.devices if not d["virtual"])
        self.status(f"Devices refreshed: {n} found" if n else "No joysticks detected",
                    "ok" if n else "warn")

    def _enumerate_devices(self):
        """Open every connected device. The JOYDEVICEADDED events SDL also sends for
        them are dropped, and _add_device ignores any it has already seen."""
        pygame.event.clear()
        for i in range(pygame.joystick.get_count()):
            try:
                self._add_device(pygame.joystick.Joystick(i), 0.0)
            except pygame.error:
                pass
        self._reindex()

    def _read_snapshot(self):
        snap = {}
        for iid, key in self._dev_key_by_id.items():
            joy = self._joys.get(iid)
            if joy is None:
                continue
            try:
                snap[key] = {
                    "axes": tuple(joy.get_axis(i) for i in range(joy.get_numaxes())),
                    "buttons": tuple(bool(joy.get_button(i)) for i in range(joy.get_numbuttons())),
                    "hats": tuple(joy.get_hat(i) for i in range(joy.get_numhats())),
                }
            except pygame.error:
                continue
        self.snapshot = snap
        return snap

    # ---- mapping ----

    def _begin_mapping(self, target, now):
        if not self._dev_key_by_id:
            self.status("ERROR: no joysticks detected", "error")
            self._post("mapping_done", target)
            return
        if self.mapping_target and self.mapping_target != target:
            self._post("mapping_done", self.mapping_target)
        self.mapping_target = target
        self._map_rest = dict(self.snapshot)
        self._map_deadline = now + MAP_TIMEOUT
        self._map_pending_hat = None
        self._post("mapping", target)

    def _end_mapping(self):
        target = self.mapping_target
        self.mapping_target = None
        self._map_pending_hat = None
        self._post("mapping_done", target)

    def _wants_analog(self, target, modes):
        return (not is_split(target) and target in ANALOG_TARGETS
                and modes.get(target, "Axis") == "Axis")

    def _poll_mapping(self, snap, modes, now):
        target = self.mapping_target
        analog = self._wants_analog(target, modes)
        for key, st in snap.items():
            rest = self._map_rest.get(key)
            if rest is None:  # device appeared mid-mapping: take its current state as rest
                self._map_rest[key] = st
                continue
            guid, _, n = key.partition("#")
            base = {"dev": guid, "dev_name": self._dev_name_by_key.get(key, "")[:18]}
            if n:
                base["dev_n"] = int(n)
            dev_short = base["dev_name"]

            if not analog:
                for i, pressed in enumerate(st["buttons"]):
                    was = rest["buttons"][i] if i < len(rest["buttons"]) else False
                    if pressed and not was:
                        self._try_bind(target, dict(base, type="BTN", index=i),
                                       f"Bound {target} -> Button {i} on {dev_short}")
                        return
                for h, cur in enumerate(st["hats"]):
                    h_rest = rest["hats"][h] if h < len(rest["hats"]) else (0, 0)
                    if cur == h_rest or cur == (0, 0):
                        continue
                    hx, hy = cur
                    if hx and hy:  # diagonal: only accept it if it's held deliberately
                        pend = (key, h, cur)
                        if self._map_pending_hat is None or self._map_pending_hat[0] != pend:
                            self._map_pending_hat = (pend, now)
                            continue
                        if now - self._map_pending_hat[1] < HAT_DIAGONAL_HOLD:
                            continue
                    self._try_bind(target, dict(base, type="HAT", hat=h, x=hx, y=hy),
                                   f"Bound {target} -> Hat {h} {HAT_NAMES[cur]} on {dev_short}")
                    return

            for i, v in enumerate(st["axes"]):
                a_rest = rest["axes"][i] if i < len(rest["axes"]) else 0.0
                delta = v - a_rest
                if abs(delta) > MAP_AXIS_THRESHOLD:
                    info = dict(base, type="AXIS", index=i, rest=a_rest)
                    if analog:
                        msg = f"Bound {target} -> Axis {i} on {dev_short}"
                    else:
                        info["dir"] = 1 if delta > 0 else -1
                        msg = f"Bound {target} -> Axis {i}{'+' if delta > 0 else '-'} on {dev_short}"
                    self._try_bind(target, info, msg)
                    return

        if now > self._map_deadline:
            self._end_mapping()
            self.status("Mapping timed out - no input detected", "warn")

    def _try_bind(self, target, info, msg):
        with self.lock:
            conflict = find_conflict(self.mapping, info, target)
            if not conflict:
                self.mapping[target] = info
        self._end_mapping()
        if conflict:
            self.status(f"REJECTED: that input is already bound to {conflict} - clear it first", "error")
        else:
            self._post("bound", target)
            self.status(msg, "ok")

    # ---- virtual pad ----

    def _set_pad(self, on, now, quiet=False):
        if on and self._pad is None:
            if self.vg is None:
                self.status("ERROR: virtual controller support isn't available", "error")
            else:
                try:
                    self._pad = self.vg.VX360Gamepad()
                    self._pad_held = {}
                    self._pad_last = None
                    self._expect_virtual_until = now + VIRTUAL_PAD_WINDOW
                    if not quiet:
                        self.status("Virtual controller ARMED", "ok")
                except Exception as e:
                    self._pad = None
                    self.status(f"ERROR: couldn't create the virtual controller: {e}", "error")
        elif not on and self._pad is not None:
            self._reset_pad()
            self._pad = None  # vgamepad unplugs the virtual device when the object is freed
            if not quiet:
                self.status("Virtual controller safe (disabled)", "info")
        self.pad_enabled = self._pad is not None
        self._post("armed")

    def _reset_pad(self):
        try:
            self._pad.reset()
            self._pad.update()
        except Exception:
            pass
        self._pad_held = {}
        self._pad_last = None

    def _drive_pad(self, snap, mapping, modes, invert):
        pad = self._pad
        if pad is None:
            return

        def stick(name):
            if modes.get(name, "Axis") == "Axis":
                info = mapping.get(name)
                if not info or info.get("type") != "AXIS":
                    return 0.0
                v = axis_value(info, snap)
                v = apply_deadzone(v) if v is not None else 0.0
            else:
                pos = input_active(mapping.get(f"{name}_POS"), snap)
                neg = input_active(mapping.get(f"{name}_NEG"), snap)
                v = 1.0 if pos and not neg else -1.0 if neg and not pos else 0.0
            return -v if invert.get(name) else v

        def trigger(name):
            info = mapping.get(name)
            if not info:
                return 0.0
            if modes.get(name, "Axis") == "Button" or info.get("type") != "AXIS":
                return 1.0 if input_active(info, snap) else 0.0
            raw = axis_value(info, snap)
            if raw is None:
                return 0.0
            return trigger_value(info, raw, invert.get(name, False))

        lx, ly, rx, ry = (stick(n) for n in STICK_TARGETS)
        lt, rt = trigger("LT"), trigger("RT")
        buttons = tuple(input_active(mapping.get(n), snap) for n in BUTTON_TARGETS)
        state = (lx, ly, rx, ry, lt, rt, buttons)
        if state == self._pad_last:
            return  # nothing changed: skip the driver round-trip

        # pygame reports +Y as down; an Xbox stick reports +Y as up
        pad.left_joystick_float(x_value_float=lx, y_value_float=-ly)
        pad.right_joystick_float(x_value_float=rx, y_value_float=-ry)
        pad.left_trigger_float(value_float=lt)
        pad.right_trigger_float(value_float=rt)
        for name, pressed in zip(BUTTON_TARGETS, buttons):
            if pressed != self._pad_held.get(name, False):
                btn = getattr(self.vg.XUSB_BUTTON, BUTTON_TARGETS[name])
                if pressed:
                    pad.press_button(button=btn)
                else:
                    pad.release_button(button=btn)
                self._pad_held[name] = pressed
        pad.update()
        self._pad_last = state

    # ---- keyboard ----

    def _drive_keyboard(self, snap, mapping, kb_keys, use_vk, enabled, now):
        if not KEYBOARD_AVAILABLE:
            return
        for slot in range(NUM_KB_SLOTS):
            want = kb_keys.get(slot, "")
            if want not in SCANCODES:
                want = ""
            held = self._kb_held.get(slot)
            # Key or key mode changed while held: let go of the old key straight away
            if held and (held[0] != want or held[1] != use_vk):
                send_key(held[0], down=False, use_vk=held[1])
                self._kb_held.pop(slot, None)
                held = None
            pressed = bool(want) and enabled and input_active(mapping.get(f"KB{slot}"), snap)
            if pressed and not held:
                if not send_key(want, down=True, use_vk=use_vk) and not self._sendinput_warned:
                    self._sendinput_warned = True
                    self.status("WARNING: Windows blocked a key press - the game may be running "
                                "as administrator; run Mech Mapper as administrator too", "warn")
                self._kb_held[slot] = (want, use_vk, now)
            elif not pressed and held and now - held[2] >= MIN_KEY_HOLD:
                # (a fast tap stays held until MIN_KEY_HOLD so frame polling can't miss it)
                send_key(held[0], down=False, use_vk=held[1])
                self._kb_held.pop(slot, None)

    def _release_all_keys(self):
        for key_name, use_vk, _ in self._kb_held.values():
            send_key(key_name, down=False, use_vk=use_vk)
        self._kb_held = {}

    def _shutdown_outputs(self):
        self._release_all_keys()
        if self._pad is not None:
            self._reset_pad()
            self._pad = None
        self.pad_enabled = False


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------


def sanitize_profile_name(name):
    name = "".join(c for c in (name or "").strip() if c.isalnum() or c in "-_ ").strip()
    return name or "default"


def profile_path(name):
    return os.path.join(PROFILE_DIR, f"{name}.json")


def _looks_like_profile(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return isinstance(data, dict) and "mapping_info" in data
    except (OSError, ValueError):
        return False


def migrate_profiles():
    """Copy profiles from older locations into PROFILE_DIR. Originals are left in place."""
    os.makedirs(PROFILE_DIR, exist_ok=True)
    for src_dir in LEGACY_PROFILE_DIRS:
        try:
            names = os.listdir(src_dir)
        except OSError:
            continue
        for fn in names:
            if not fn.endswith(".json"):
                continue
            src = os.path.join(src_dir, fn)
            dst = os.path.join(PROFILE_DIR, fn)
            if not os.path.exists(dst) and _looks_like_profile(src):
                try:
                    shutil.copy2(src, dst)
                except OSError:
                    pass
        old_last = os.path.join(src_dir, ".last_profile")
        if not os.path.exists(LAST_PROFILE_FILE) and os.path.exists(old_last):
            try:
                shutil.copy2(old_last, LAST_PROFILE_FILE)
            except OSError:
                pass

    # very old single-file config -> 'default' profile, once
    if os.path.exists(LEGACY_SINGLE_CONFIG) and not os.path.exists(profile_path("default")):
        try:
            with open(LEGACY_SINGLE_CONFIG, "r", encoding="utf-8") as f:
                data = json.load(f)
            write_json_atomic(profile_path("default"), data)
            os.replace(LEGACY_SINGLE_CONFIG, LEGACY_SINGLE_CONFIG + ".migrated")
        except (OSError, ValueError):
            pass


def list_profiles():
    try:
        return sorted(fn[:-5] for fn in os.listdir(PROFILE_DIR)
                      if fn.endswith(".json") and sanitize_profile_name(fn[:-5]) == fn[:-5])
    except OSError:
        return []


def write_json_atomic(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def read_last_profile():
    try:
        with open(LAST_PROFILE_FILE, "r", encoding="utf-8") as f:
            return sanitize_profile_name(f.read())
    except OSError:
        return "default"


def write_last_profile(name):
    try:
        with open(LAST_PROFILE_FILE, "w", encoding="utf-8") as f:
            f.write(name)
    except OSError:
        pass


def load_app_settings():
    settings = {"check_updates": True, "skip_version": ""}
    try:
        with open(APP_SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            settings.update(data)
    except (OSError, ValueError):
        pass
    return settings


def save_app_settings(settings):
    try:
        os.makedirs(PROFILE_DIR, exist_ok=True)
        write_json_atomic(APP_SETTINGS_FILE, settings)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Updater (GitHub Releases)
# ---------------------------------------------------------------------------


def parse_version(text):
    """'v2.1.0' -> (2, 1, 0). Anything after a '-' or '+' (pre-release/build tags) is ignored."""
    core = re.split(r"[-+]", (text or "").strip().lstrip("vV"), maxsplit=1)[0]
    nums = [int(n) for n in re.findall(r"\d+", core)][:3]
    return tuple(nums + [0] * (3 - len(nums)))


class _StripAuthOnRedirect(urllib.request.HTTPRedirectHandler):
    """GitHub redirects asset downloads to a storage host that rejects our API token."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.parse.urlparse(newurl).hostname != urllib.parse.urlparse(req.full_url).hostname:
            new.headers.pop("Authorization", None)
            new.unredirected_hdrs.pop("Authorization", None)
        return new


class UpdateError(Exception):
    pass


class Updater:
    """Finds, downloads and installs a newer release from GitHub.

    A release needs a tag like 'v2.1.0' and the built .exe attached as an asset.
    For a private repo, put a GitHub token in the MECHMAPPER_GITHUB_TOKEN
    environment variable (public repos need nothing).
    """

    # MECHMAPPER_UPDATE_URL lets you point the updater at a test server
    API_LATEST = os.environ.get("MECHMAPPER_UPDATE_URL",
                                f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest")
    RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases/latest"

    def __init__(self):
        self._opener = urllib.request.build_opener(_StripAuthOnRedirect())

    @staticmethod
    def can_self_update():
        return IS_WINDOWS and getattr(sys, "frozen", False)

    def _open(self, url, accept, timeout=15):
        headers = {"User-Agent": f"MechMapper/{APP_VERSION}", "Accept": accept}
        token = os.environ.get("MECHMAPPER_GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            return self._opener.open(urllib.request.Request(url, headers=headers), timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise UpdateError("No published release found (or the GitHub repo is private).")
            if e.code == 403:
                raise UpdateError("GitHub refused the request (rate limit?) - try again later.")
            raise UpdateError(f"GitHub returned HTTP {e.code}.")
        except (urllib.error.URLError, OSError) as e:
            raise UpdateError(f"Couldn't reach GitHub: {getattr(e, 'reason', e)}")

    def fetch_latest(self):
        with self._open(self.API_LATEST, "application/vnd.github+json") as resp:
            try:
                data = json.load(resp)
            except ValueError:
                data = None
        if not isinstance(data, dict) or "tag_name" not in data:
            raise UpdateError("Got an unexpected reply instead of GitHub release info.")
        tag = data.get("tag_name") or ""
        return {
            "tag": tag,
            "version": parse_version(tag),
            "name": data.get("name") or tag,
            "notes": (data.get("body") or "").strip(),
            "page": data.get("html_url") or self.RELEASES_PAGE,
            "asset": self._pick_asset(data.get("assets") or []),
        }

    @staticmethod
    def _pick_asset(assets):
        exes = [a for a in assets if a.get("name", "").lower().endswith(".exe")]
        if not exes:
            return None
        current = os.path.basename(sys.executable).lower() if getattr(sys, "frozen", False) else ""
        for a in exes:
            if a["name"].lower() == current:
                return a
        return exes[0]

    @staticmethod
    def is_newer(info):
        return info["version"] > parse_version(APP_VERSION)

    def download(self, asset, progress=None):
        """Download the asset next to the running exe; verify size and SHA-256. Returns the path."""
        dest_dir = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else tempfile.gettempdir()
        dest = os.path.join(dest_dir, os.path.basename(sys.executable) + ".new")
        expected_size = asset.get("size") or 0
        digest = (asset.get("digest") or "").lower()
        sha = hashlib.sha256()
        got = 0
        try:
            with self._open(asset["url"], "application/octet-stream", timeout=60) as resp, \
                    open(dest, "wb") as out:
                while True:
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    sha.update(chunk)
                    got += len(chunk)
                    if progress:
                        progress(got, expected_size)
        except OSError as e:
            raise UpdateError(f"Download failed: {e}")
        if expected_size and got != expected_size:
            os.remove(dest)
            raise UpdateError(f"Download was incomplete ({got} of {expected_size} bytes).")
        if digest.startswith("sha256:") and sha.hexdigest() != digest.split(":", 1)[1]:
            os.remove(dest)
            raise UpdateError("Downloaded file failed its checksum - not installing it.")
        return dest

    @staticmethod
    def launch_installer(new_exe):
        """Start a helper that waits for us to exit, swaps the exe in and relaunches it."""
        exe = sys.executable
        script = os.path.join(tempfile.gettempdir(), f"mechmapper_update_{os.getpid()}.ps1")
        log = os.path.join(os.path.dirname(exe), "update.log")

        def ps(s):
            return "'" + s.replace("'", "''") + "'"

        lines = [
            f"$exe = {ps(exe)}; $new = {ps(new_exe)}; $old = $exe + '.old'; $log = {ps(log)}",
            "function Log($m) { Add-Content -Path $log -Value ((Get-Date -Format s) + ' ' + $m) }",
            f"Wait-Process -Id {os.getpid()} -Timeout 30 -ErrorAction SilentlyContinue",
            # a one-file PyInstaller exe runs as a parent bootloader + child; wait for both
            f"$parent = Get-Process -Id {os.getppid()} -ErrorAction SilentlyContinue",
            "if ($parent -and $parent.Path -eq $exe) { Wait-Process -Id $parent.Id -Timeout 30 -ErrorAction SilentlyContinue }",
            "$moved = $false",
            "for ($i = 0; $i -lt 40 -and -not $moved; $i++) {",
            "  try { if (Test-Path $old) { Remove-Item $old -Force }; Move-Item $exe $old -Force; $moved = $true }",
            "  catch { Start-Sleep -Milliseconds 500 } }",
            "if ($moved) {",
            "  try { Move-Item $new $exe -Force; Log 'Updated.' }",
            "  catch { Log ('Install failed: ' + $_); Move-Item $old $exe -Force } }",
            "else { Log 'Could not replace the running exe (still locked).' }",
            "Start-Process -FilePath $exe",
            "Remove-Item $MyInvocation.MyCommand.Path -Force -ErrorAction SilentlyContinue",
        ]
        with open(script, "w", encoding="utf-8-sig") as f:
            f.write("\r\n".join(lines) + "\r\n")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                          "-WindowStyle", "Hidden", "-File", script],
                         creationflags=flags, close_fds=True, env=clean_child_environment())

    @staticmethod
    def cleanup_previous():
        """Remove the backup left by the last update, if any."""
        if getattr(sys, "frozen", False):
            for leftover in (sys.executable + ".old", sys.executable + ".new"):
                try:
                    if os.path.exists(leftover):
                        os.remove(leftover)
                except OSError:
                    pass


# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------

C = {
    "bg": "#14171b", "panel": "#1d2127", "panel2": "#232830", "border": "#343b45",
    "text": "#d6dbe1", "muted": "#7c8692",
    "amber": "#f2a33c", "amber_d": "#c67f1f",
    "green": "#5fbf6a", "red": "#e0584d",
}
FONT_UI = ("Segoe UI", 10)
FONT_UI_B = ("Segoe UI", 10, "bold")
FONT_MONO = ("Consolas", 10)
FONT_MONO_B = ("Consolas", 10, "bold")
FONT_TITLE = ("Consolas", 16, "bold")

# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------


class MechMapperApp:
    def __init__(self, root, engine):
        self.root = root
        self.engine = engine
        self.root.configure(bg=C["bg"])

        self.profile_name = "default"
        self.dirty = False
        self.selected_dev_key = None
        self.tester_window = None
        self._tester_dev_key = None
        self.bound_labels = {}
        self.invert_vars = {}
        self._mapping_key = None
        self.updater = Updater()
        self.update_info = None
        self._update_busy = False

        self.setup_style()
        self.create_widgets()
        self._set_icon()
        self._fit_window()

        migrate_profiles()
        self.app_settings = load_app_settings()
        Updater.cleanup_previous()
        self.try_autoload()
        self._update_title()
        if self.app_settings.get("check_updates", True):
            self.root.after(2500, lambda: self.check_for_updates(manual=False))

        self.root.bind("<Escape>", lambda e: self.cancel_mapping())
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.engine.start()
        self.root.after(30, self._pump_engine_events)

    def _set_icon(self):
        icon_path = resource_path(ICON_FILE)
        if IS_WINDOWS and os.path.exists(icon_path):
            try:
                # default= makes every window (including the tester) use it
                self.root.iconbitmap(default=icon_path)
            except tk.TclError:
                pass

    # ---------------- Style ----------------

    def setup_style(self):
        s = ttk.Style(self.root)
        s.theme_use("clam")
        s.configure(".", background=C["bg"], foreground=C["text"],
                    fieldbackground=C["panel2"], font=FONT_UI,
                    bordercolor=C["border"], lightcolor=C["panel"], darkcolor=C["bg"])
        s.configure("TFrame", background=C["bg"])
        s.configure("TLabel", background=C["bg"], foreground=C["text"])
        s.configure("TButton", background=C["panel2"], foreground=C["text"],
                    bordercolor=C["border"], focuscolor=C["amber"], padding=(10, 4), relief="flat")
        s.map("TButton", background=[("pressed", C["border"]), ("active", "#2b313b")],
              foreground=[("disabled", C["muted"])])
        s.configure("Map.TButton", background=C["amber"], foreground="#14171b",
                    font=FONT_UI_B, padding=(10, 3))
        s.map("Map.TButton", background=[("pressed", C["amber_d"]), ("active", "#ffb650")],
              foreground=[("pressed", "#14171b"), ("active", "#14171b")])
        s.configure("Clear.TButton", background=C["panel2"], foreground=C["muted"],
                    font=("Consolas", 9, "bold"), padding=(4, 1))
        s.map("Clear.TButton", background=[("pressed", C["red"]), ("active", "#2b313b")],
              foreground=[("pressed", "#14171b"), ("active", C["red"])])
        s.configure("Armed.TButton", background=C["green"], foreground="#14171b",
                    font=FONT_UI_B, padding=(10, 4))
        s.map("Armed.TButton", background=[("pressed", "#4a9e54"), ("active", "#72d47d")],
              foreground=[("pressed", "#14171b"), ("active", "#14171b")])
        s.configure("Vertical.TScrollbar", background=C["panel2"], troughcolor=C["bg"],
                    bordercolor=C["border"], arrowcolor=C["amber"], lightcolor=C["panel2"],
                    darkcolor=C["panel2"])
        s.map("Vertical.TScrollbar", background=[("active", C["border"])])
        s.configure("TCombobox", fieldbackground=C["panel2"], background=C["panel2"],
                    foreground=C["text"], arrowcolor=C["amber"],
                    selectbackground=C["panel2"], selectforeground=C["text"])
        s.map("TCombobox", fieldbackground=[("readonly", C["panel2"])],
              foreground=[("readonly", C["text"])])
        self.root.option_add("*TCombobox*Listbox.background", C["panel2"])
        self.root.option_add("*TCombobox*Listbox.foreground", C["text"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", C["amber"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#14171b")

    def make_section(self, parent, title):
        outer = tk.Frame(parent, bg=C["panel"], highlightthickness=1,
                         highlightbackground=C["border"])
        header = tk.Frame(outer, bg=C["panel"])
        header.pack(fill=tk.X, padx=10, pady=(8, 4))
        tk.Frame(header, bg=C["amber"], width=4, height=14).pack(side=tk.LEFT)
        tk.Label(header, text=title.upper(), bg=C["panel"], fg=C["amber"],
                 font=FONT_MONO_B).pack(side=tk.LEFT, padx=(8, 0))
        body = tk.Frame(outer, bg=C["panel"])
        body.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        return outer, body

    def set_status(self, text, kind="info"):
        colors = {"info": C["text"], "ok": C["green"], "warn": C["amber"], "error": C["red"]}
        self.status_label.config(text=text, fg=colors.get(kind, C["text"]))

    # ---------------- Layout ----------------

    def create_widgets(self):
        header = tk.Frame(self.root, bg=C["panel"], highlightthickness=1,
                          highlightbackground=C["border"])
        header.pack(fill=tk.X, padx=10, pady=(10, 5))
        tk.Label(header, text="MECH MAPPER", bg=C["panel"], fg=C["amber"],
                 font=FONT_TITLE).pack(side=tk.LEFT, padx=12, pady=8)
        tk.Label(header, text="Joystick Mapping Essentials", bg=C["panel"],
                 fg=C["muted"], font=FONT_MONO).pack(side=tk.LEFT, pady=8)
        self.armed_lamp = tk.Label(header, text="  SAFE  ", bg=C["panel2"], fg=C["muted"],
                                   font=FONT_MONO_B, padx=6, pady=2)
        self.armed_lamp.pack(side=tk.RIGHT, padx=12)
        self.update_btn = tk.Label(header, text=f"v{APP_VERSION}  \u21bb check for updates",
                                   bg=C["panel"], fg=C["muted"], font=FONT_MONO, cursor="hand2")
        self.update_btn.pack(side=tk.RIGHT, padx=8)
        self.update_btn.bind("<Button-1>", lambda e: self.on_update_clicked())

        outer, body = self.make_section(
            self.root, "Devices  (all are read for binds; select one for the tester)")
        outer.pack(fill=tk.X, padx=10, pady=5)
        self.joystick_listbox = tk.Listbox(
            body, height=5, bg=C["panel2"], fg=C["text"], font=FONT_MONO,
            selectbackground=C["amber"], selectforeground="#14171b",
            highlightthickness=0, relief=tk.FLAT, activestyle="none", exportselection=False)
        self.joystick_listbox.pack(fill=tk.X)
        self.joystick_listbox.bind("<<ListboxSelect>>", self.on_joystick_selected)
        self._listbox_keys = []

        action_frame = ttk.Frame(self.root)
        action_frame.pack(fill=tk.X, padx=10, pady=5)
        tk.Label(action_frame, text="PROFILE", bg=C["bg"], fg=C["muted"],
                 font=FONT_MONO_B).pack(side=tk.LEFT, padx=(4, 4))
        self.profile_var = tk.StringVar(value="default")
        self.profile_box = ttk.Combobox(action_frame, textvariable=self.profile_var,
                                        values=list_profiles(), width=18)
        self.profile_box.pack(side=tk.LEFT, padx=(0, 10))
        self.profile_box.bind("<<ComboboxSelected>>", lambda e: self.load_settings())

        for label, cmd in (("Save", self.save_settings), ("Load", self.load_settings),
                           ("Folder", self.open_profile_folder),
                           ("Clear All", self.clear_all),
                           ("Refresh Devices", lambda: self.engine.request("refresh")),
                           ("Test Joystick", self.open_tester)):
            ttk.Button(action_frame, text=label, command=cmd).pack(side=tk.LEFT, padx=4)
        self.enable_btn = ttk.Button(action_frame, text="Enable", command=self.toggle_controller)
        self.enable_btn.pack(side=tk.LEFT, padx=4)
        self.kb_enable_btn = ttk.Button(action_frame, text="KB Enable", command=self.toggle_keyboard)
        self.kb_enable_btn.pack(side=tk.LEFT, padx=4)
        tk.Label(action_frame, text="KEY MODE", bg=C["bg"], fg=C["muted"],
                 font=FONT_MONO_B).pack(side=tk.LEFT, padx=(10, 4))
        self.key_mode_var = tk.StringVar(value=KEY_MODES[0])
        km = ttk.Combobox(action_frame, textvariable=self.key_mode_var,
                          values=KEY_MODES, width=20, state="readonly")
        km.pack(side=tk.LEFT)
        km.bind("<<ComboboxSelected>>", self.on_key_mode_changed)

        status_bar = tk.Frame(self.root, bg=C["panel2"], highlightthickness=1,
                              highlightbackground=C["border"])
        status_bar.pack(fill=tk.X, side=tk.BOTTOM, padx=10, pady=(5, 10))
        tk.Label(status_bar, text=" SYS ", bg=C["amber"], fg="#14171b",
                 font=FONT_MONO_B).pack(side=tk.LEFT)
        self.status_label = tk.Label(status_bar, text="Ready", bg=C["panel2"],
                                     fg=C["text"], font=FONT_MONO, anchor="w")
        self.status_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8, pady=3)
        self.cancel_map_btn = ttk.Button(status_bar, text="Cancel (Esc)", style="Clear.TButton",
                                         command=self.cancel_mapping)

        # Scrollable area for the two bind panels, so nothing is cut off on
        # smaller screens or at high display scaling.
        scroll_host = tk.Frame(self.root, bg=C["bg"])
        scroll_host.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        self.scroll_canvas = tk.Canvas(scroll_host, bg=C["bg"], highlightthickness=0, bd=0)
        self.scroll_bar = ttk.Scrollbar(scroll_host, orient=tk.VERTICAL,
                                        command=self.scroll_canvas.yview)
        self.scroll_canvas.configure(yscrollcommand=self.scroll_bar.set)
        self.scroll_bar.pack(side=tk.RIGHT, fill=tk.Y)
        self.scroll_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        content = tk.Frame(self.scroll_canvas, bg=C["bg"])
        self.scroll_content = content
        self._scroll_window = self.scroll_canvas.create_window((0, 0), window=content, anchor="nw")
        content.bind("<Configure>", lambda e: self.scroll_canvas.configure(
            scrollregion=self.scroll_canvas.bbox("all")))
        self.scroll_canvas.bind("<Configure>", self._on_scroll_canvas_resize)
        self.root.bind_all("<MouseWheel>", self._on_mousewheel)

        outer, self.btn_frame = self.make_section(content, "Binds")
        outer.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        outer, self.kb_frame = self.make_section(content, "Keyboard Binds")
        outer.pack(side=tk.LEFT, fill=tk.Y, padx=(10, 0))
        self.render_all()

    def _on_scroll_canvas_resize(self, event):
        # stretch the content to the canvas width (but never squeeze it)
        width = max(event.width, self.scroll_content.winfo_reqwidth())
        self.scroll_canvas.itemconfigure(self._scroll_window, width=width)

    def _on_mousewheel(self, event):
        widget = event.widget
        try:
            if isinstance(widget, str) or widget.winfo_toplevel() is not self.root:
                return  # combobox drop-downs and the tester window scroll themselves
        except (KeyError, tk.TclError):
            return
        if self.scroll_content.winfo_height() > self.scroll_canvas.winfo_height():
            self.scroll_canvas.yview_scroll(int(-event.delta / 120) or (-1 if event.delta > 0 else 1),
                                            "units")

    def _fit_window(self):
        """Size the window to its content, limited to the screen."""
        self.root.update_idletasks()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        content_w = self.scroll_content.winfo_reqwidth() + self.scroll_bar.winfo_reqwidth() + 24
        width = max(self.root.winfo_reqwidth(), content_w)
        height = (self.root.winfo_reqheight() - self.scroll_canvas.winfo_reqheight()
                  + self.scroll_content.winfo_reqheight() + 4)
        width, height = min(width, sw - 40), min(height, sh - 80)
        self.root.geometry(f"{width}x{height}+{max(0, (sw - width) // 2)}+{max(0, (sh - height) // 3)}")

    def render_all(self):
        self.bound_labels = {}
        self.render_bindings()
        self.render_kb_bindings()
        self.refresh_bound_labels()

    def render_bindings(self):
        for w in self.btn_frame.winfo_children():
            w.destroy()
        with self.engine.lock:
            modes = dict(self.engine.modes)
            invert = dict(self.engine.invert)

        table = tk.Frame(self.btn_frame, bg=C["panel"])
        table.pack(fill=tk.BOTH, expand=True)
        for c, h in enumerate(["CONTROL", "ACTION", "MODE", "INV", "MAP", "BOUND TO", ""]):
            tk.Label(table, text=h, bg=C["panel"], fg=C["muted"],
                     font=("Consolas", 9, "bold")).grid(row=0, column=c, padx=6, pady=(0, 4), sticky="w")

        self.invert_vars = {}
        for i, (name, (desc, can_be_axis)) in enumerate(CONTROLS.items(), start=1):
            row_bg = C["panel2"] if i % 2 else C["panel"]
            mode = modes.get(name, "Axis") if can_be_axis else "Button"
            tk.Label(table, text=name, bg=row_bg, fg=C["amber"], font=FONT_MONO_B,
                     width=8, anchor="w").grid(row=i, column=0, padx=(6, 0), sticky="nsew")
            tk.Label(table, text=desc, bg=row_bg, fg=C["text"], font=FONT_UI,
                     width=24, anchor="w").grid(row=i, column=1, sticky="nsew")

            mode_cell = tk.Frame(table, bg=row_bg)
            mode_cell.grid(row=i, column=2, sticky="nsew")
            if can_be_axis:
                cb = ttk.Combobox(mode_cell, values=["Axis", "Button"], width=8, state="readonly")
                cb.set(mode)
                cb.bind("<<ComboboxSelected>>", lambda e, n=name, c=cb: self.update_mode(n, c.get()))
                cb.pack(padx=4, pady=3)
            else:
                tk.Label(mode_cell, text="Button", bg=row_bg, fg=C["muted"],
                         font=FONT_MONO).pack(padx=4, pady=3)

            # Invert: sticks in either mode, triggers only when driven by an axis
            inv_cell = tk.Frame(table, bg=row_bg)
            inv_cell.grid(row=i, column=3, sticky="nsew")
            if name in STICK_TARGETS or (name in TRIGGER_TARGETS and mode == "Axis"):
                var = tk.BooleanVar(value=invert.get(name, False))
                var.trace_add("write", lambda *_, n=name, v=var: self.on_invert_changed(n, v))
                self.invert_vars[name] = var
                tk.Checkbutton(inv_cell, variable=var, bg=row_bg, activebackground=row_bg,
                               selectcolor=C["panel2"], fg=C["amber"],
                               highlightthickness=0, bd=0).pack(pady=3)

            map_cell = tk.Frame(table, bg=row_bg)
            map_cell.grid(row=i, column=4, sticky="nsew")
            if name in STICK_TARGETS and mode == "Button":
                ttk.Button(map_cell, text="+", width=3, style="Map.TButton",
                           command=lambda n=name: self.start_mapping(f"{n}_POS")
                           ).pack(side=tk.LEFT, padx=(4, 1), pady=3)
                ttk.Button(map_cell, text="-", width=3, style="Map.TButton",
                           command=lambda n=name: self.start_mapping(f"{n}_NEG")
                           ).pack(side=tk.LEFT, padx=1, pady=3)
            else:
                ttk.Button(map_cell, text="Map", width=6, style="Map.TButton",
                           command=lambda n=name: self.start_mapping(n)).pack(padx=4, pady=3)

            lbl = tk.Label(table, text="", bg=row_bg, fg=C["muted"],
                           font=FONT_MONO, width=34, anchor="w")
            lbl.grid(row=i, column=5, padx=(6, 6), sticky="nsew")
            self.bound_labels[name] = lbl

            clr_cell = tk.Frame(table, bg=row_bg)
            clr_cell.grid(row=i, column=6, sticky="nsew")
            ttk.Button(clr_cell, text="X", width=2, style="Clear.TButton",
                       command=lambda n=name: self.clear_binding(n)).pack(padx=(0, 6), pady=4)

    def render_kb_bindings(self):
        for w in self.kb_frame.winfo_children():
            w.destroy()
        if not KEYBOARD_AVAILABLE:
            tk.Label(self.kb_frame, text="Keyboard emulation unavailable (Windows only)",
                     bg=C["panel"], fg=C["red"], font=FONT_MONO).pack(anchor="w")
            return
        with self.engine.lock:
            kb_keys = dict(self.engine.kb_keys)

        table = tk.Frame(self.kb_frame, bg=C["panel"])
        table.pack(fill=tk.X)
        mid = NUM_KB_SLOTS // 2
        for slot in range(NUM_KB_SLOTS):
            col = 5 if slot >= mid else 0
            r = slot % mid
            row_bg = C["panel2"] if r % 2 else C["panel"]
            key = f"KB{slot}"

            tk.Label(table, text=f"KB{slot + 1:02d}", bg=row_bg, fg=C["amber"],
                     font=FONT_MONO_B, width=6, anchor="w").grid(row=r, column=col, padx=(6, 0), sticky="nsew")

            key_cell = tk.Frame(table, bg=row_bg)
            key_cell.grid(row=r, column=col + 1, sticky="nsew")
            cb = ttk.Combobox(key_cell, values=KEY_OPTIONS, width=11, state="readonly")
            cb.set(kb_keys.get(slot, ""))
            cb.bind("<<ComboboxSelected>>", lambda e, s=slot, c=cb: self.on_kb_key_changed(s, c.get()))
            cb.pack(padx=4, pady=3)

            map_cell = tk.Frame(table, bg=row_bg)
            map_cell.grid(row=r, column=col + 2, sticky="nsew")
            ttk.Button(map_cell, text="Map", width=6, style="Map.TButton",
                       command=lambda k=key: self.start_mapping(k)).pack(padx=4, pady=3)

            lbl = tk.Label(table, text="", bg=row_bg, fg=C["muted"],
                           font=FONT_MONO, width=14, anchor="w")
            lbl.grid(row=r, column=col + 3, padx=(6, 6), sticky="nsew")
            self.bound_labels[key] = lbl

            clr_cell = tk.Frame(table, bg=row_bg)
            clr_cell.grid(row=r, column=col + 4, sticky="nsew")
            ttk.Button(clr_cell, text="X", width=2, style="Clear.TButton",
                       command=lambda k=key: self.clear_binding(k)).pack(side=tk.LEFT, padx=(0, 2), pady=4)
            ttk.Button(clr_cell, text="T", width=2, style="Clear.TButton",
                       command=lambda s=slot: self.test_fire_key(s)).pack(side=tk.LEFT, padx=(0, 6), pady=4)

    # ---------------- Binding labels ----------------

    def _online_keys(self):
        return {d["key"] for d in self.engine.devices if d["key"]}

    @staticmethod
    def describe_info(info):
        if not info:
            return "unbound"
        dev = f" @{info['dev_name']}" if info.get("dev_name") else ""
        t = info.get("type")
        if t == "AXIS":
            d = info.get("dir")
            return f"Axis {info.get('index')}{'+' if d == 1 else '-' if d == -1 else ''}{dev}"
        if t == "HAT":
            direction = HAT_NAMES.get((info.get("x"), info.get("y")), "?")
            return f"Hat{info.get('hat')} {direction}{dev}"
        return f"Button {info.get('index')}{dev}"

    def _label_state(self, name, online):
        """(text, colour) for a bound-to label."""
        with self.engine.lock:
            mode = self.engine.modes.get(name, "Axis")
            if name in STICK_TARGETS and mode == "Button":
                infos = [self.engine.mapping.get(f"{name}_POS"), self.engine.mapping.get(f"{name}_NEG")]
                text = (f"+: {self.describe_info(infos[0])}  "
                        f"-: {self.describe_info(infos[1])}")
            else:
                infos = [self.engine.mapping.get(name)]
                text = self.describe_info(infos[0])
        bound = [i for i in infos if i]
        if not bound:
            return text, C["muted"]
        if any(binding_device_key(i) not in online for i in bound):
            return text, C["amber"]  # bound to a device that isn't plugged in
        return text, C["green"]

    def refresh_bound_labels(self):
        online = self._online_keys()
        for name, lbl in self.bound_labels.items():
            if name == self._mapping_key or (self._mapping_key and split_base(self._mapping_key) == name):
                lbl.config(text="... waiting for input", fg=C["amber"])
                continue
            text, color = self._label_state(name, online)
            if name.startswith("KB") and len(text) > 14:
                text = text[:13] + "~"
            lbl.config(text=text, fg=color)

    # ---------------- Config changes from widgets ----------------

    def mark_dirty(self, dirty=True):
        if self.dirty != dirty:
            self.dirty = dirty
            self._update_title()

    def _update_title(self):
        self.root.title(f"{APP_NAME} {APP_VERSION} - {self.profile_name}{' *' if self.dirty else ''}")

    def update_mode(self, name, mode):
        self.engine.set_mode(name, mode)
        self.mark_dirty()
        self.render_all()

    def on_invert_changed(self, name, var):
        try:
            value = var.get()
        except tk.TclError:
            return
        self.engine.set_invert(name, value)
        self.mark_dirty()

    def on_kb_key_changed(self, slot, key_name):
        self.engine.set_kb_key(slot, key_name)
        self.mark_dirty()

    def on_key_mode_changed(self, _event=None):
        self.engine.set_use_vk(self.key_mode_var.get() == KEY_MODES[1])
        self.mark_dirty()

    def clear_binding(self, name):
        if self._mapping_key and split_base(self._mapping_key) == split_base(name):
            self.cancel_mapping()
        self.engine.clear_binding(name)
        self.mark_dirty()
        self.refresh_bound_labels()
        self.set_status(f"Cleared binding for {name}", "info")

    def clear_all(self):
        self.cancel_mapping()
        self.engine.clear_all()
        self.mark_dirty()
        self.render_all()
        self.set_status("Cleared all bindings (not saved yet - press Save to keep this)", "info")

    # ---------------- Mapping ----------------

    def start_mapping(self, key):
        self.engine.request("map", key)

    def cancel_mapping(self):
        if self._mapping_key:
            self.engine.request("cancel_map")

    # ---------------- Engine events ----------------

    def _pump_engine_events(self):
        try:
            while True:
                ev = self.engine.events.get_nowait()
                kind = ev[0]
                if kind == "status":
                    self.set_status(ev[1], ev[2])
                elif kind == "devices":
                    self._rebuild_device_list()
                    self.refresh_bound_labels()
                elif kind == "mapping":
                    self._mapping_key = ev[1]
                    self.cancel_map_btn.pack(side=tk.RIGHT, padx=4, pady=2, before=self.status_label)
                    kind_txt = ("axis" if not is_split(ev[1]) and ev[1] in ANALOG_TARGETS
                                and self.engine.modes.get(ev[1], "Axis") == "Axis"
                                else "button / hat / axis direction")
                    self.set_status(f"AWAITING INPUT [{kind_txt}] for {ev[1]} on any device "
                                    f"- Esc to cancel", "warn")
                    self.refresh_bound_labels()
                elif kind == "mapping_done":
                    if self._mapping_key == ev[1]:
                        self._mapping_key = None
                        self.cancel_map_btn.pack_forget()
                    self.refresh_bound_labels()
                elif kind == "bound":
                    self.mark_dirty()
                    self.refresh_bound_labels()
                elif kind == "armed":
                    self._update_armed_lamp()
                elif kind.startswith("update_"):
                    self._handle_update_event(kind, ev[1:])
        except queue.Empty:
            pass
        self.root.after(30, self._pump_engine_events)

    def _rebuild_device_list(self):
        self.joystick_listbox.delete(0, tk.END)
        self._listbox_keys = []
        real = 0
        for d in self.engine.devices:
            if d["virtual"]:
                text = f"  -  {d['name']}   [Mech Mapper virtual pad - ignored]"
            else:
                real += 1
                text = f" {real}  {d['name']}"
            self.joystick_listbox.insert(tk.END, text)
            if d["virtual"]:
                self.joystick_listbox.itemconfig(tk.END, fg=C["muted"])
            self._listbox_keys.append(d["key"])
        if self.selected_dev_key not in self._listbox_keys or self.selected_dev_key is None:
            first = next((k for k in self._listbox_keys if k), None)
            self.selected_dev_key = first
        if self.selected_dev_key:
            idx = self._listbox_keys.index(self.selected_dev_key)
            self.joystick_listbox.selection_set(idx)
        if not real:
            self.set_status("No joysticks detected - plug one in (it's picked up automatically)", "warn")

    def on_joystick_selected(self, _event=None):
        sel = self.joystick_listbox.curselection()
        if not sel:
            return
        key = self._listbox_keys[sel[0]]
        if key is None:
            self.set_status("That's Mech Mapper's own virtual pad; it isn't read for input", "info")
            if self.selected_dev_key in self._listbox_keys:
                self.joystick_listbox.selection_clear(0, tk.END)
                self.joystick_listbox.selection_set(self._listbox_keys.index(self.selected_dev_key))
            return
        self.selected_dev_key = key
        dev = next((d for d in self.engine.devices if d["key"] == key), None)
        if dev:
            self.set_status(f"Selected: {dev['name']}", "ok")

    # ---------------- Arming ----------------

    def _update_armed_lamp(self):
        pad = self.engine.pad_enabled
        kb = self.engine.kb_enabled
        self.enable_btn.config(text="ENABLED ●" if pad else "Enable",
                               style="Armed.TButton" if pad else "TButton")
        self.kb_enable_btn.config(text="KB ON ●" if kb else "KB Enable",
                                  style="Armed.TButton" if kb else "TButton")
        if pad or kb:
            self.armed_lamp.config(text="  ARMED  ", bg=C["green"], fg="#14171b")
        else:
            self.armed_lamp.config(text="  SAFE  ", bg=C["panel2"], fg=C["muted"])

    def toggle_controller(self):
        self.engine.request("pad", not self.engine.pad_enabled)

    def toggle_keyboard(self):
        if not KEYBOARD_AVAILABLE:
            self.set_status("Keyboard emulation unavailable (Windows only)", "error")
            return
        on = not self.engine.kb_enabled
        self.engine.set_kb_enabled(on)
        self._update_armed_lamp()
        self.set_status("Keyboard binds ACTIVE" if on else "Keyboard binds disabled",
                        "ok" if on else "info")

    def test_fire_key(self, slot):
        """Fire a slot's key after a 3 s countdown with no joystick involved,
        to check the injection path on its own."""
        with self.engine.lock:
            key_name = self.engine.kb_keys.get(slot, "")
            use_vk = self.engine.use_vk
        if key_name not in SCANCODES:
            self.set_status(f"KB{slot + 1:02d}: pick a key first", "error")
            return
        mode_text = self.key_mode_var.get()

        def run():
            for n in (3, 2, 1):
                self.engine.status(f"TEST: focus the game window... sending {key_name} in {n}", "warn")
                time.sleep(1)
            ok = send_key(key_name, down=True, use_vk=use_vk)
            time.sleep(0.15)
            send_key(key_name, down=False, use_vk=use_vk)
            if ok:
                self.engine.status(f"TEST: sent {key_name} ({mode_text})", "ok")
            else:
                self.engine.status(f"TEST: Windows blocked {key_name} - is the game running as "
                                   "administrator?", "error")

        threading.Thread(target=run, daemon=True).start()

    # ---------------- Profiles ----------------

    def _refresh_profile_list(self):
        self.profile_box["values"] = list_profiles()

    def open_profile_folder(self):
        os.makedirs(PROFILE_DIR, exist_ok=True)
        if IS_WINDOWS:
            os.startfile(PROFILE_DIR)
        else:
            self.set_status(f"Profiles are in {PROFILE_DIR}", "info")

    def save_settings(self):
        name = sanitize_profile_name(self.profile_var.get())
        data = self.engine.export_config()
        if self.selected_dev_key:
            data["selected_device"] = self.selected_dev_key
        try:
            os.makedirs(PROFILE_DIR, exist_ok=True)
            write_json_atomic(profile_path(name), data)
        except OSError as e:
            messagebox.showerror("Save failed", str(e))
            return False
        write_last_profile(name)
        self.profile_name = name
        self.profile_var.set(name)
        self._refresh_profile_list()
        self.mark_dirty(False)
        self._update_title()
        self.set_status(f"Saved profile '{name}'", "ok")
        return True

    def _confirm_discard(self):
        """True if it's OK to throw away unsaved changes (saving first if asked)."""
        if not self.dirty:
            return True
        answer = messagebox.askyesnocancel(
            APP_NAME, f"Profile '{self.profile_name}' has unsaved changes.\n\nSave them first?")
        if answer is None:
            return False
        if answer:
            saved_as = self.profile_var.get()
            self.profile_var.set(self.profile_name)
            ok = self.save_settings()
            self.profile_var.set(saved_as)
            return ok
        return True

    def load_settings(self):
        name = sanitize_profile_name(self.profile_var.get())
        path = profile_path(name)
        if not os.path.exists(path):
            messagebox.showinfo("Not found", f"No saved profile named '{name}'.")
            return
        if not self._confirm_discard():
            self.profile_var.set(self.profile_name)
            return
        if self._load_profile(name):
            self.set_status(f"Loaded profile '{name}'", "ok")
            self._warn_missing_devices()

    def _load_profile(self, name):
        try:
            with open(profile_path(name), "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            messagebox.showerror("Load failed", f"Couldn't read profile '{name}':\n{e}")
            return False
        self.cancel_mapping()
        self.engine.import_config(data)
        self.key_mode_var.set(KEY_MODES[1] if self.engine.use_vk else KEY_MODES[0])
        sel = data.get("selected_device") or data.get("joystick_guid")
        if sel:
            self.selected_dev_key = sel
        self.profile_name = name
        self.profile_var.set(name)
        write_last_profile(name)
        self.render_all()
        self.mark_dirty(False)
        self._update_title()
        return True

    def try_autoload(self):
        name = read_last_profile()
        if os.path.exists(profile_path(name)):
            if self._load_profile(name):
                self.set_status(f"Loaded profile '{name}'", "ok")
                # devices aren't enumerated yet; check for missing ones shortly
                self.root.after(1500, self._warn_missing_devices)
        else:
            self.profile_name = name
            self.profile_var.set(name)
        self._refresh_profile_list()

    def _warn_missing_devices(self):
        online = self._online_keys()
        with self.engine.lock:
            missing = {info.get("dev_name") or "unknown device"
                       for info in self.engine.mapping.values()
                       if binding_device_key(info) and binding_device_key(info) not in online}
        if missing:
            self.set_status("WARNING: not connected: " + ", ".join(sorted(missing)), "warn")

    # ---------------- Input tester ----------------

    def open_tester(self):
        key = self.selected_dev_key
        dev = next((d for d in self.engine.devices if d["key"] == key), None)
        if not dev:
            messagebox.showinfo("No joystick", "Select a joystick first.")
            return
        if self.tester_window is not None:
            try:
                if self.tester_window.winfo_exists():
                    if self._tester_dev_key == key:
                        self.tester_window.lift()
                        return
                    self._close_tester()
            except tk.TclError:
                pass

        self._tester_dev_key = key
        win = tk.Toplevel(self.root)
        win.title(f"Input Tester - {dev['name']}")
        win.configure(bg=C["bg"])
        self.tester_window = win

        self.tester_state_label = tk.Label(win, text="", bg=C["bg"], fg=C["red"], font=FONT_MONO_B)
        self.tester_state_label.pack(fill=tk.X, padx=10, pady=(6, 0))

        outer, body = self.make_section(win, f"Axes ({dev['axes']})")
        outer.pack(fill=tk.X, padx=10, pady=5)
        self.tester_axis_canvases, self.tester_axis_labels = [], []
        for i in range(dev["axes"]):
            row = tk.Frame(body, bg=C["panel"])
            row.pack(fill=tk.X, pady=2)
            tk.Label(row, text=f"AX {i}", width=6, anchor="w", bg=C["panel"],
                     fg=C["muted"], font=FONT_MONO).pack(side=tk.LEFT)
            canvas = tk.Canvas(row, width=300, height=16, bg=C["panel2"],
                               highlightthickness=1, highlightbackground=C["border"])
            canvas.pack(side=tk.LEFT, padx=5)
            val_label = tk.Label(row, text="+0.00", width=6, bg=C["panel"], fg=C["text"], font=FONT_MONO)
            val_label.pack(side=tk.LEFT)
            self.tester_axis_canvases.append(canvas)
            self.tester_axis_labels.append(val_label)

        self.tester_hat_canvases = []
        if dev["hats"]:
            outer, body = self.make_section(win, f"Hats / POV ({dev['hats']})")
            outer.pack(fill=tk.X, padx=10, pady=5)
            for h in range(dev["hats"]):
                col = tk.Frame(body, bg=C["panel"])
                col.pack(side=tk.LEFT, padx=15, pady=5)
                tk.Label(col, text=f"HAT {h}", bg=C["panel"], fg=C["muted"], font=FONT_MONO).pack()
                c = tk.Canvas(col, width=70, height=70, bg=C["panel2"],
                              highlightthickness=1, highlightbackground=C["border"])
                c.pack()
                self.tester_hat_canvases.append(c)

        outer, body = self.make_section(win, f"Buttons ({dev['buttons']})")
        outer.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        self.tester_button_widgets = []
        for b in range(dev["buttons"]):
            r, c = divmod(b, 8)
            lbl = tk.Label(body, text=f"{b:02d}", width=4, height=2, bg=C["panel2"],
                           fg=C["muted"], font=FONT_MONO,
                           highlightthickness=1, highlightbackground=C["border"])
            lbl.grid(row=r, column=c, padx=3, pady=3)
            self.tester_button_widgets.append(lbl)

        win.protocol("WM_DELETE_WINDOW", self._close_tester)
        win.update_idletasks()
        max_h = win.winfo_screenheight() - 80
        if win.winfo_reqheight() > max_h:
            win.geometry(f"{win.winfo_reqwidth()}x{max_h}")
        self.update_tester()

    def _close_tester(self):
        if self.tester_window is not None:
            try:
                self.tester_window.destroy()
            except tk.TclError:
                pass
        self.tester_window = None
        self._tester_dev_key = None

    def update_tester(self):
        win = self.tester_window
        if win is None:
            return
        try:
            if not win.winfo_exists():
                return
        except tk.TclError:
            return

        st = self.engine.snapshot.get(self._tester_dev_key)
        if st is None:
            self.tester_state_label.config(text="DISCONNECTED - waiting for the device to come back")
        else:
            self.tester_state_label.config(text="")
            for i, canvas in enumerate(self.tester_axis_canvases):
                if i >= len(st["axes"]):
                    break
                val = st["axes"][i]
                w, h = int(canvas["width"]), int(canvas["height"])
                mid = w / 2
                x = mid + val * mid
                color = C["green"] if abs(val) > DEADZONE else C["border"]
                canvas.delete("all")
                canvas.create_line(mid, 0, mid, h, fill=C["muted"])
                canvas.create_rectangle(min(mid, x), 1, max(mid, x), h - 1, fill=color, outline="")
                self.tester_axis_labels[i].config(
                    text=f"{val:+.2f}", fg=C["green"] if abs(val) > DEADZONE else C["muted"])
            for h_i, canvas in enumerate(self.tester_hat_canvases):
                if h_i >= len(st["hats"]):
                    break
                hx, hy = st["hats"][h_i]
                canvas.delete("all")
                canvas.create_oval(5, 5, 65, 65, outline=C["muted"])
                px, py = 35 + hx * 25, 35 - hy * 25
                active = (hx, hy) != (0, 0)
                canvas.create_oval(px - 8, py - 8, px + 8, py + 8,
                                   fill=C["green"] if active else C["border"], outline="")
            for b_i, lbl in enumerate(self.tester_button_widgets):
                if b_i >= len(st["buttons"]):
                    break
                pressed = st["buttons"][b_i]
                lbl.config(bg=C["green"] if pressed else C["panel2"],
                           fg="#14171b" if pressed else C["muted"])
        win.after(33, self.update_tester)

    # ---------------- Updates ----------------

    def check_for_updates(self, manual=True):
        if self._update_busy:
            return
        self._update_busy = True
        self.update_btn.config(text="checking for updates...", fg=C["muted"])

        def run():
            try:
                info = self.updater.fetch_latest()
            except UpdateError as e:
                self.engine.events.put(("update_error", str(e), manual))
                return
            except Exception as e:  # malformed reply etc.
                self.engine.events.put(("update_error", f"Update check failed: {e}", manual))
                return
            self.engine.events.put(("update_checked", info, manual))

        threading.Thread(target=run, daemon=True).start()

    def _handle_update_event(self, kind, args):
        if kind == "update_error":
            self._update_busy = False
            msg, manual = args
            self.update_btn.config(text=f"v{APP_VERSION}  \u21bb check for updates", fg=C["muted"])
            if manual:
                messagebox.showwarning("Update check", msg)
        elif kind == "update_checked":
            self._update_busy = False
            info, manual = args
            if Updater.is_newer(info):
                self.update_info = info
                self.update_btn.config(text=f"\u2b06 UPDATE {info['tag']} AVAILABLE", fg=C["amber"])
                if manual or self.app_settings.get("skip_version") != info["tag"]:
                    self.offer_update(info)
            else:
                self.update_info = None
                self.update_btn.config(text=f"v{APP_VERSION}  \u2714 up to date", fg=C["muted"])
                if manual:
                    messagebox.showinfo("Update check", f"You're on the latest version (v{APP_VERSION}).")
        elif kind == "update_progress":
            got, total = args
            pct = f" {got * 100 // total}%" if total else ""
            self.update_btn.config(text=f"downloading {self.update_info['tag']}...{pct}", fg=C["amber"])
        elif kind == "update_failed":
            self._update_busy = False
            self.update_btn.config(text=f"\u2b06 UPDATE {self.update_info['tag']} AVAILABLE", fg=C["amber"])
            messagebox.showerror("Update failed", args[0])
        elif kind == "update_ready":
            self._update_busy = False
            self.update_btn.config(text="update ready - restarting", fg=C["green"])
            self.install_update(args[0])

    def on_update_clicked(self):
        if self.update_info and not self._update_busy:
            self.offer_update(self.update_info)
        else:
            self.check_for_updates(manual=True)

    def offer_update(self, info):
        notes = info["notes"]
        if len(notes) > 700:
            notes = notes[:700].rstrip() + " ..."
        header = f"{APP_NAME} {info['tag']} is available (you have v{APP_VERSION})."
        if not Updater.can_self_update() or not info["asset"]:
            why = ("You're running from source - update with 'git pull'."
                   if not getattr(sys, "frozen", False) else
                   "This release has no .exe attached, so it can't be installed automatically.")
            if messagebox.askyesno("Update available",
                                   f"{header}\n\n{why}\n\nOpen the release page?"):
                webbrowser.open(info["page"])
            return
        answer = messagebox.askyesnocancel(
            "Update available",
            f"{header}\n\n{notes or '(no release notes)'}\n\n"
            "Yes - download and install now (Mech Mapper restarts)\n"
            "No - skip this version\n"
            "Cancel - remind me next time")
        if answer is None:
            return
        if answer is False:
            self.app_settings["skip_version"] = info["tag"]
            save_app_settings(self.app_settings)
            self.set_status(f"Skipping {info['tag']} - click the update link in the header to install it later",
                            "info")
            return
        self.download_update(info)

    def download_update(self, info):
        if self._update_busy:
            return
        self._update_busy = True
        asset = info["asset"]
        last = [0.0]

        def progress(got, total):
            now = time.monotonic()
            if now - last[0] > 0.2 or got == total:
                last[0] = now
                self.engine.events.put(("update_progress", got, total))

        def run():
            try:
                path = self.updater.download(asset, progress)
            except UpdateError as e:
                self.engine.events.put(("update_failed", str(e)))
                return
            except Exception as e:
                self.engine.events.put(("update_failed", f"Download failed: {e}"))
                return
            self.engine.events.put(("update_ready", path))

        threading.Thread(target=run, daemon=True).start()

    def install_update(self, new_exe):
        if not self._confirm_discard():
            self.set_status("Update downloaded - it will install the next time you choose Update", "info")
            self.update_btn.config(text=f"\u2b06 UPDATE {self.update_info['tag']} AVAILABLE", fg=C["amber"])
            return
        try:
            Updater.launch_installer(new_exe)
        except OSError as e:
            messagebox.showerror("Update failed", f"Couldn't start the installer: {e}")
            return
        self.dirty = False
        self.on_close()

    # ---------------- Shutdown ----------------

    def on_close(self):
        if not self._confirm_discard():
            return
        self.engine.stop()
        self.root.destroy()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    global vg
    ensure_elevated()
    vg = ensure_vigembus()
    root = tk.Tk()
    MechMapperApp(root, InputEngine(vg))
    root.mainloop()


if __name__ == "__main__":
    main()
