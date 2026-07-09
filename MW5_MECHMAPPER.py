import tkinter as tk
from tkinter import ttk, messagebox
import pygame
import threading
import json
import os
import time
import vgamepad as vg
import sys
import ctypes



def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False


# 2. CALL the function second
if is_admin():
    print("Running with administrative privileges!")
else:
    ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, " ".join(sys.argv), None, 1)
    sys.exit()

os.environ['SDL_JOYSTICK_HIDAPI'] = '1'

OLD_CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".xbox360_mapper_pro.json")
# CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".mech_mapper_configs")
if getattr(sys, 'frozen', False):
    # Running as a compiled exe
    CONFIG_DIR = os.path.dirname(sys.executable)
else:
    # Running as a script in PyCharm
    CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
LAST_PROFILE_FILE = os.path.join(CONFIG_DIR, ".last_profile")
DEADZONE = 0.08
MIN_KEY_HOLD = 0.065  # seconds: minimum injected key hold so per-frame game polling can't miss it

# ---------------- Theme: mech cockpit ----------------
# Dark panels, amber HUD accents, radar-green for bound/active states.
C = {
    "bg": "#14171b",  # window background
    "panel": "#1d2127",  # section panels
    "panel2": "#232830",  # row stripe / input fields
    "border": "#343b45",
    "text": "#d6dbe1",
    "muted": "#7c8692",
    "amber": "#f2a33c",  # attention / interactive / headings
    "amber_d": "#c67f1f",  # amber pressed
    "green": "#5fbf6a",  # bound / active / ok
    "red": "#e0584d",  # errors
}
FONT_UI = ("Segoe UI", 10)
FONT_UI_B = ("Segoe UI", 10, "bold")
FONT_MONO = ("Consolas", 10)
FONT_MONO_B = ("Consolas", 10, "bold")
FONT_TITLE = ("Consolas", 16, "bold")

# Which controls map to a real XUSB button flag (everything except the two sticks and the triggers)
BUTTON_TARGETS = {
    "D_UP": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_UP,
    "D_DOWN": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_DOWN,
    "D_LEFT": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_LEFT,
    "D_RIGHT": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_RIGHT,
    "RS": vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_THUMB,
    "LS": vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_THUMB,
    "RB": vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_SHOULDER,
    "LB": vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_SHOULDER,
    "A": vg.XUSB_BUTTON.XUSB_GAMEPAD_A,
    "B": vg.XUSB_BUTTON.XUSB_GAMEPAD_B,
    "X": vg.XUSB_BUTTON.XUSB_GAMEPAD_X,
    "Y": vg.XUSB_BUTTON.XUSB_GAMEPAD_Y,
    "START": vg.XUSB_BUTTON.XUSB_GAMEPAD_START,
    "BACK": vg.XUSB_BUTTON.XUSB_GAMEPAD_BACK,
}

STICK_TARGETS = {"LX", "LY", "RX", "RY"}
TRIGGER_TARGETS = {"LT", "RT"}
NUM_KB_SLOTS = 30

# ---------------- Keyboard emulation (SendInput with scancodes) ----------------
# Scancodes are sent as hardware-level input so DirectX games (like MW5) see them;
# virtual-key based libs (pynput etc.) are often ignored by games.
# Format: name -> (set-1 scancode, is_extended)
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
    "MINUS": (0x0C, False), "EQUALS": (0x0D, False),
    "LBRACKET": (0x1A, False), "RBRACKET": (0x1B, False),
    "SEMICOLON": (0x27, False), "APOSTROPHE": (0x28, False), "GRAVE": (0x29, False),
    "BACKSLASH": (0x2B, False), "COMMA": (0x33, False), "PERIOD": (0x34, False),
    "SLASH": (0x35, False),
    "UP": (0x48, True), "DOWN": (0x50, True), "LEFT": (0x4B, True), "RIGHT": (0x4D, True),
    "HOME": (0x47, True), "END": (0x4F, True), "PGUP": (0x49, True), "PGDN": (0x51, True),
    "INSERT": (0x52, True), "DELETE": (0x53, True),
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
    "MINUS": 0xBD, "EQUALS": 0xBB,
    "LBRACKET": 0xDB, "RBRACKET": 0xDD,
    "SEMICOLON": 0xBA, "APOSTROPHE": 0xDE, "GRAVE": 0xC0,
    "BACKSLASH": 0xDC, "COMMA": 0xBC, "PERIOD": 0xBE, "SLASH": 0xBF,
    "UP": 0x26, "DOWN": 0x28, "LEFT": 0x25, "RIGHT": 0x27,
    "HOME": 0x24, "END": 0x23, "PGUP": 0x21, "PGDN": 0x22,
    "INSERT": 0x2D, "DELETE": 0x2E,
}

KEY_MODES = ["Scancode (DirectInput)", "Virtual Key (Standard)"]

try:
    import ctypes

    _KEYEVENTF_SCANCODE = 0x0008
    _KEYEVENTF_KEYUP = 0x0002
    _KEYEVENTF_EXTENDEDKEY = 0x0001
    _ULONG_PTR = ctypes.POINTER(ctypes.c_ulong)


    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                    ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                    ("dwExtraInfo", _ULONG_PTR)]


    class _INPUTUNION(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT), ("padding", ctypes.c_ubyte * 32)]


    class _INPUT(ctypes.Structure):
        _fields_ = [("type", ctypes.c_ulong), ("union", _INPUTUNION)]


    def send_key(key_name, down, use_vk=False):
        flags = 0
        if use_vk:
            vk = VKCODES.get(key_name, 0)
            sc = SCANCODES[key_name][0]
            extended = SCANCODES[key_name][1]
        else:
            sc, extended = SCANCODES[key_name]
            vk = 0
            flags |= _KEYEVENTF_SCANCODE
        if extended:
            flags |= _KEYEVENTF_EXTENDEDKEY
        if not down:
            flags |= _KEYEVENTF_KEYUP
        inp = _INPUT(type=1, union=_INPUTUNION(ki=_KEYBDINPUT(vk, sc, flags, 0, None)))
        ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))


    KEYBOARD_AVAILABLE = True
except (ImportError, AttributeError, OSError):
    def send_key(key_name, down, use_vk=False):
        pass


    KEYBOARD_AVAILABLE = False


class Xbox360Mapper:
    def __init__(self, root):
        self.root = root
        self.root.title("Mech Mapper")
        self.root.geometry("1700x1200")
        self.root.configure(bg=C["bg"])

        pygame.init()
        pygame.joystick.init()

        self.is_mapping = False
        self.mapping_target = None
        self.controller_active = False
        self.active_joysticks = []
        self.joy_by_guid = {}
        self.current_joystick = None
        # mapping_info[target] = {"type": "AXIS"/"BTN"/"HAT", ...}
        self.mapping_info = {}
        self.resting_values = {}
        self.resting_buttons = {}
        self.resting_hats = {}
        self.control_modes = {k: "Axis" for k in ["LX", "LY", "RX", "RY", "LT", "RT"]}
        self.vg_pad = None
        self._button_held = {}
        self._stop = False
        self.kb_active = False
        self.tester_window = None
        self._tester_running = False

        self.controls_config = {
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
            "START": ("Pause (Menu btn)", False), "BACK": ("Show BattleGrid (View btn)", False)
        }
        self.invert_vars = {}
        # keyboard binds: slot index -> selected key name ("" = none)
        self.kb_keys = {i: "" for i in range(NUM_KB_SLOTS)}
        self._kb_held = {}
        self._kb_press_time = {}

        self.setup_style()
        self.create_widgets()
        self.update_joystick_list()
        self.try_autoload()
        threading.Thread(target=self.background_loop, daemon=True).start()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        if getattr(sys, 'frozen', False):
            # If running as an .exe, look in the temporary folder
            icon_path = os.path.join(sys._MEIPASS, "mech_mapper.ico")
        else:
            # If running in PyCharm, look in the script folder
            icon_path = "mech_mapper.ico"

        if os.path.exists(icon_path):
            self.root.iconbitmap(icon_path)

    # ---------------- Style ----------------

    def setup_style(self):
        s = ttk.Style(self.root)
        s.theme_use("clam")

        s.configure(".", background=C["bg"], foreground=C["text"],
                    fieldbackground=C["panel2"], font=FONT_UI,
                    bordercolor=C["border"], lightcolor=C["panel"], darkcolor=C["bg"])

        s.configure("TFrame", background=C["bg"])
        s.configure("Panel.TFrame", background=C["panel"])
        s.configure("TLabel", background=C["bg"], foreground=C["text"])

        s.configure("TButton", background=C["panel2"], foreground=C["text"],
                    bordercolor=C["border"], focuscolor=C["amber"],
                    padding=(10, 4), relief="flat")
        s.map("TButton",
              background=[("pressed", C["border"]), ("active", "#2b313b")],
              foreground=[("disabled", C["muted"])])

        # Amber primary button for Map actions
        s.configure("Map.TButton", background=C["amber"], foreground="#14171b",
                    font=FONT_UI_B, padding=(10, 3))
        s.map("Map.TButton",
              background=[("pressed", C["amber_d"]), ("active", "#ffb650")],
              foreground=[("pressed", "#14171b"), ("active", "#14171b")])

        # Small quiet clear button; red on hover so it reads as destructive
        s.configure("Clear.TButton", background=C["panel2"], foreground=C["muted"],
                    font=("Consolas", 9, "bold"), padding=(4, 1))
        s.map("Clear.TButton",
              background=[("pressed", C["red"]), ("active", "#2b313b")],
              foreground=[("pressed", "#14171b"), ("active", C["red"])])

        # Green state for the Enable button while the virtual pad is active
        s.configure("Armed.TButton", background=C["green"], foreground="#14171b",
                    font=FONT_UI_B, padding=(10, 4))
        s.map("Armed.TButton",
              background=[("pressed", "#4a9e54"), ("active", "#72d47d")],
              foreground=[("pressed", "#14171b"), ("active", "#14171b")])

        s.configure("TCombobox", fieldbackground=C["panel2"], background=C["panel2"],
                    foreground=C["text"], arrowcolor=C["amber"],
                    selectbackground=C["panel2"], selectforeground=C["text"])
        s.map("TCombobox",
              fieldbackground=[("readonly", C["panel2"])],
              foreground=[("readonly", C["text"])])
        self.root.option_add("*TCombobox*Listbox.background", C["panel2"])
        self.root.option_add("*TCombobox*Listbox.foreground", C["text"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", C["amber"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#14171b")

        s.configure("TCheckbutton", background=C["panel"], foreground=C["text"],
                    indicatorcolor=C["panel2"], focuscolor=C["panel"])
        s.map("TCheckbutton",
              background=[("active", C["panel"])],
              indicatorcolor=[("selected", C["amber"])])

    def make_section(self, parent, title):
        """Panel with an amber-tagged header label, replaces LabelFrame."""
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

    # ---------------- UI ----------------

    def create_widgets(self):
        # Header bar
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

        # Joystick list
        outer, body = self.make_section(self.root, "Devices  (all are read for binds; select one for the tester)")
        outer.pack(fill=tk.X, padx=10, pady=5)
        self.joystick_listbox = tk.Listbox(
            body, height=5, bg=C["panel2"], fg=C["text"], font=FONT_MONO,
            selectbackground=C["amber"], selectforeground="#14171b",
            highlightthickness=0, relief=tk.FLAT, activestyle="none")
        self.joystick_listbox.pack(fill=tk.X)
        self.joystick_listbox.bind('<<ListboxSelect>>', self.on_joystick_selected)

        # Actions + profile
        action_frame = ttk.Frame(self.root)
        action_frame.pack(fill=tk.X, padx=10, pady=5)

        tk.Label(action_frame, text="CONFIG", bg=C["bg"], fg=C["muted"],
                 font=FONT_MONO_B).pack(side=tk.LEFT, padx=(4, 4))
        self.profile_var = tk.StringVar(value="default")
        self.profile_box = ttk.Combobox(action_frame, textvariable=self.profile_var,
                                        values=self.list_profiles(), width=18)
        self.profile_box.pack(side=tk.LEFT, padx=(0, 10))

        for label, cmd in (("Save", self.save_settings), ("Load", self.load_settings),
                           ("Clear All", self.clear_all),
                           ("Refresh Joysticks", self.update_joystick_list),
                           ("Test Joystick", self.open_tester)):
            ttk.Button(action_frame, text=label, command=cmd).pack(side=tk.LEFT, padx=4)
        self.enable_btn = ttk.Button(action_frame, text="Enable",
                                     command=self.toggle_controller)
        self.enable_btn.pack(side=tk.LEFT, padx=4)
        self.kb_enable_btn = ttk.Button(action_frame, text="KB Enable",
                                        command=self.toggle_keyboard)
        self.kb_enable_btn.pack(side=tk.LEFT, padx=4)
        tk.Label(action_frame, text="KEY MODE", bg=C["bg"], fg=C["muted"],
                 font=FONT_MONO_B).pack(side=tk.LEFT, padx=(10, 4))
        self.key_mode_var = tk.StringVar(value=KEY_MODES[0])
        km = ttk.Combobox(action_frame, textvariable=self.key_mode_var,
                          values=KEY_MODES, width=20, state="readonly")
        km.pack(side=tk.LEFT)
        km.bind("<<ComboboxSelected>>", lambda e: self._release_all_keys())

        # Content: binds on the left, keyboard binds on the right
        content = ttk.Frame(self.root)
        content.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        outer, self.btn_frame = self.make_section(content, "Binds")
        outer.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.render_bindings()

        outer, self.kb_frame = self.make_section(content, "Keyboard Binds")
        outer.pack(side=tk.LEFT, fill=tk.Y, padx=(10, 0))
        self.render_kb_bindings()

        # Status readout bar (cockpit-style)
        status_bar = tk.Frame(self.root, bg=C["panel2"], highlightthickness=1,
                              highlightbackground=C["border"])
        status_bar.pack(fill=tk.X, side=tk.BOTTOM, padx=10, pady=(5, 10))
        tk.Label(status_bar, text=" SYS ", bg=C["amber"], fg="#14171b",
                 font=FONT_MONO_B).pack(side=tk.LEFT)
        self.status_label = tk.Label(status_bar, text="Ready", bg=C["panel2"],
                                     fg=C["text"], font=FONT_MONO, anchor="w")
        self.status_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8, pady=3)

    def render_bindings(self):
        for w in self.btn_frame.winfo_children():
            w.destroy()

        table = tk.Frame(self.btn_frame, bg=C["panel"])
        table.pack(fill=tk.BOTH, expand=True)
        headers = ["CONTROL", "ACTION", "MODE", "INV", "MAP", "BOUND TO", ""]
        widths = [8, 24, 10, 4, 12, 30, 4]
        for c, (h, w) in enumerate(zip(headers, widths)):
            tk.Label(table, text=h, bg=C["panel"], fg=C["muted"],
                     font=("Consolas", 9, "bold")).grid(row=0, column=c, padx=6, pady=(0, 4), sticky="w")

        self.bound_labels = {}
        for i, (name, (desc, can_be_axis)) in enumerate(self.controls_config.items(), start=1):
            row_bg = C["panel2"] if i % 2 else C["panel"]
            # per-cell backgrounds give the stripe effect
            tk.Label(table, text=name, bg=row_bg, fg=C["amber"], font=FONT_MONO_B,
                     width=8, anchor="w").grid(row=i, column=0, padx=(6, 0), sticky="nsew")
            tk.Label(table, text=desc, bg=row_bg, fg=C["text"], font=FONT_UI,
                     width=24, anchor="w").grid(row=i, column=1, sticky="nsew")

            mode_cell = tk.Frame(table, bg=row_bg)
            mode_cell.grid(row=i, column=2, sticky="nsew")
            if can_be_axis:
                cb = ttk.Combobox(mode_cell, values=["Axis", "Button"], width=8, state="readonly")
                cb.set(self.control_modes.get(name, "Axis"))
                cb.bind("<<ComboboxSelected>>", lambda e, n=name, c=cb: self.update_mode(n, c.get()))
                cb.pack(padx=4, pady=3)
            else:
                tk.Label(mode_cell, text="Button", bg=row_bg, fg=C["muted"],
                         font=FONT_MONO).pack(padx=4, pady=3)

            inv_cell = tk.Frame(table, bg=row_bg)
            inv_cell.grid(row=i, column=3, sticky="nsew")
            var = self.invert_vars.get(name, tk.BooleanVar(value=False))
            self.invert_vars[name] = var
            if can_be_axis:
                chk = tk.Checkbutton(inv_cell, variable=var, bg=row_bg,
                                     activebackground=row_bg, selectcolor=C["panel2"],
                                     fg=C["amber"], highlightthickness=0, bd=0)
                chk.pack(pady=3)

            map_cell = tk.Frame(table, bg=row_bg)
            map_cell.grid(row=i, column=4, sticky="nsew")
            if can_be_axis and name in STICK_TARGETS and self.control_modes.get(name) == "Button":
                ttk.Button(map_cell, text="+", width=3, style="Map.TButton",
                           command=lambda n=name: self.start_mapping(f"{n}_POS")).pack(side=tk.LEFT, padx=(4, 1),
                                                                                       pady=3)
                ttk.Button(map_cell, text="-", width=3, style="Map.TButton",
                           command=lambda n=name: self.start_mapping(f"{n}_NEG")).pack(side=tk.LEFT, padx=1, pady=3)
            else:
                ttk.Button(map_cell, text="Map", width=6, style="Map.TButton",
                           command=lambda n=name: self.start_mapping(n)).pack(padx=4, pady=3)

            bound_text = self.describe_binding(name)
            is_bound = "unbound" not in bound_text
            lbl = tk.Label(table, text=bound_text, bg=row_bg,
                           fg=C["green"] if is_bound else C["muted"],
                           font=FONT_MONO, width=30, anchor="w")
            lbl.grid(row=i, column=5, padx=(6, 6), sticky="nsew")
            self.bound_labels[name] = lbl

            clr_cell = tk.Frame(table, bg=row_bg)
            clr_cell.grid(row=i, column=6, sticky="nsew")
            ttk.Button(clr_cell, text="X", width=2, style="Clear.TButton",
                       command=lambda n=name: self.clear_binding(n)).pack(padx=(0, 6), pady=4)

        # bound_labels was rebuilt above; restore the keyboard slots' labels too
        if hasattr(self, "kb_frame"):
            self.render_kb_bindings()

    def render_kb_bindings(self):
        for w in self.kb_frame.winfo_children():
            w.destroy()

        if not KEYBOARD_AVAILABLE:
            tk.Label(self.kb_frame, text="Keyboard emulation unavailable (Windows only)",
                     bg=C["panel"], fg=C["red"], font=FONT_MONO).pack(anchor="w")
            return

        table = tk.Frame(self.kb_frame, bg=C["panel"])
        table.pack(fill=tk.X)

        # Calculate split point (half of the slots)
        mid = NUM_KB_SLOTS // 2

        for slot in range(NUM_KB_SLOTS):
            # Column 0 for first half, Column 5 for second half (5 widgets wide each)
            col_offset = 5 if slot >= mid else 0
            r = slot % mid
            row_bg = C["panel2"] if r % 2 else C["panel"]

            # Label (KB01)
            tk.Label(table, text=f"KB{slot + 1:02d}", bg=row_bg, fg=C["amber"],
                     font=FONT_MONO_B, width=6, anchor="w").grid(
                row=r, column=0 + col_offset, padx=(6, 0), sticky="nsew")

            # Combobox
            key_cell = tk.Frame(table, bg=row_bg)
            key_cell.grid(row=r, column=1 + col_offset, sticky="nsew")
            cb = ttk.Combobox(key_cell, values=KEY_OPTIONS, width=11, state="readonly")
            cb.set(self.kb_keys.get(slot, ""))
            cb.bind("<<ComboboxSelected>>",
                    lambda e, s=slot, c=cb: self.kb_keys.__setitem__(s, c.get()))
            cb.pack(padx=4, pady=3)

            # Map Button
            map_cell = tk.Frame(table, bg=row_bg)
            map_cell.grid(row=r, column=2 + col_offset, sticky="nsew")
            ttk.Button(map_cell, text="Map", width=6, style="Map.TButton",
                       command=lambda s=slot: self.start_mapping(f"KB{s}")).pack(padx=4, pady=3)

            # Description
            key = f"KB{slot}"
            bound_text = self._describe_single(self.mapping_info.get(key))
            is_bound = "unbound" not in bound_text
            # Truncate text slightly if needed for the 2-column layout
            display_text = bound_text if len(bound_text) < 14 else bound_text[:11] + "..."
            lbl = tk.Label(table, text=display_text, bg=row_bg,
                           fg=C["green"] if is_bound else C["muted"],
                           font=FONT_MONO, width=14, anchor="w")
            lbl.grid(row=r, column=3 + col_offset, padx=(6, 6), sticky="nsew")
            self.bound_labels[key] = lbl

            # Clear/Test Buttons
            clr_cell = tk.Frame(table, bg=row_bg)
            clr_cell.grid(row=r, column=4 + col_offset, sticky="nsew")
            ttk.Button(clr_cell, text="X", width=2, style="Clear.TButton",
                       command=lambda k=key: self.clear_binding(k)).pack(side=tk.LEFT, padx=(0, 2), pady=4)
            ttk.Button(clr_cell, text="T", width=2, style="Clear.TButton",
                       command=lambda s=slot: self.test_fire_key(s)).pack(side=tk.LEFT, padx=(0, 6), pady=4)

    def _describe_single(self, info):
        if not info:
            return "unbound"
        dev = f" @{info['dev_name']}" if info.get("dev_name") else ""
        if info["type"] == "AXIS":
            return f"Axis {info['index']}{dev}"
        if info["type"] == "HAT":
            return f"Hat{info['hat']} ({info['x']},{info['y']}){dev}"
        return f"Button {info['index']}{dev}"

    def describe_binding(self, name):
        if name in STICK_TARGETS and self.control_modes.get(name) == "Button":
            pos = self._describe_single(self.mapping_info.get(f"{name}_POS"))
            neg = self._describe_single(self.mapping_info.get(f"{name}_NEG"))
            return f"+: {pos}  -: {neg}"
        return self._describe_single(self.mapping_info.get(name))

    def update_mode(self, name, mode):
        self.control_modes[name] = mode
        self.render_bindings()

    def clear_binding(self, name):
        """Remove the physical binding(s) for one control or KB slot."""
        for k in (name, f"{name}_POS", f"{name}_NEG"):
            self.mapping_info.pop(k, None)
        if name in self.bound_labels:
            text = self.describe_binding(name) if not name.startswith("KB") \
                else self._describe_single(None)
            self.bound_labels[name].config(text=text, fg=C["muted"])
        self.set_status(f"Cleared binding for {name}", "info")

    def update_invert(self, name):
        pass  # value read live from invert_vars

    # ---------------- Mapping ----------------

    def start_mapping(self, key):
        if not self.active_joysticks:
            self.set_status("ERROR: no joysticks detected", "error")
            return
        self.mapping_target = key
        # snapshot resting state of EVERY device: mapping listens to all of them,
        # and only a change from rest counts (protects against latched switches).
        self.resting_all = {}
        for joy in self.active_joysticks:
            try:
                self.resting_all[joy.get_guid()] = {
                    "axes": {i: joy.get_axis(i) for i in range(joy.get_numaxes())},
                    "buttons": {i: bool(joy.get_button(i)) for i in range(joy.get_numbuttons())},
                    "hats": {h: joy.get_hat(h) for h in range(joy.get_numhats())},
                }
            except pygame.error:
                continue
        self.is_mapping = True
        if key.endswith("_POS") or key.endswith("_NEG"):
            mode = "button/hat"
        else:
            mode = self.control_modes.get(key, "Button").lower()
        self.set_status(f"AWAITING INPUT [{mode}] for {key} (any device) ...", "warn")

    def background_loop(self):
        while not self._stop:
            try:
                pygame.event.pump()
            except pygame.error:
                time.sleep(0.5)
                continue

            if self.is_mapping and self.active_joysticks:
                self._poll_mapping()

            if self.controller_active and self.vg_pad and self.active_joysticks:
                try:
                    self._drive_output()
                except pygame.error:
                    self.root.after(0, lambda: self.set_status("ERROR: joystick disconnected", "error"))
                    self.controller_active = False
                    self.root.after(0, self._update_armed_lamp)

            # Keyboard binds run on their own toggle, independent of the Xbox pad
            if self.kb_active and KEYBOARD_AVAILABLE and self.active_joysticks and not self.is_mapping:
                try:
                    self._process_keyboard()
                except pygame.error:
                    self.kb_active = False
                    self.root.after(0, self._update_armed_lamp)

            time.sleep(0.01)

    def _find_conflict(self, new_info, exclude_key):
        """Return the key of an existing binding using the same physical input, if any."""
        for key, info in self.mapping_info.items():
            if key == exclude_key or not isinstance(info, dict):
                continue
            if info.get("dev") != new_info.get("dev"):
                continue
            if info.get("type") != new_info.get("type"):
                continue
            t = new_info["type"]
            if t == "BTN" and info.get("index") == new_info.get("index"):
                return key
            if t == "AXIS" and info.get("index") == new_info.get("index"):
                return key
            if t == "HAT" and (info.get("hat"), info.get("x"), info.get("y")) == \
                    (new_info.get("hat"), new_info.get("x"), new_info.get("y")):
                return key
        return None

    def _try_bind(self, key, new_info, success_msg):
        """Bind if the physical input isn't already in use; otherwise reject."""
        conflict = self._find_conflict(new_info, key)
        self.is_mapping = False
        if conflict:
            self.root.after(0, lambda: self.set_status(
                f"REJECTED: that input is already bound to {conflict} - clear it first", "error"))
            return
        self.mapping_info[key] = new_info
        self._finish_mapping(success_msg)

    def _poll_mapping(self):
        key = self.mapping_target
        is_split = key.endswith("_POS") or key.endswith("_NEG")
        mode = "Button" if is_split else self.control_modes.get(key, "Button")

        for joy in self.active_joysticks:
            try:
                guid = joy.get_guid()
                rest = self.resting_all.get(guid, {})
                dev_short = joy.get_name()[:18]

                if mode == "Button":
                    for i in range(joy.get_numbuttons()):
                        pressed = bool(joy.get_button(i))
                        if pressed and not rest.get("buttons", {}).get(i, False):
                            self._try_bind(key,
                                           {"type": "BTN", "index": i,
                                            "dev": guid, "dev_name": dev_short},
                                           f"Bound {key} -> Button {i} on {dev_short}")
                            return
                    for h in range(joy.get_numhats()):
                        cur = joy.get_hat(h)
                        h_rest = rest.get("hats", {}).get(h, (0, 0))
                        if cur != h_rest and cur != (0, 0):
                            hx, hy = cur
                            self._try_bind(key,
                                           {"type": "HAT", "hat": h, "x": hx, "y": hy,
                                            "dev": guid, "dev_name": dev_short},
                                           f"Bound {key} -> Hat {h} ({hx},{hy}) on {dev_short}")
                            return
                else:
                    for i in range(joy.get_numaxes()):
                        a_rest = rest.get("axes", {}).get(i, 0.0)
                        if abs(joy.get_axis(i) - a_rest) > 0.6:
                            self._try_bind(key,
                                           {"type": "AXIS", "index": i, "rest": a_rest,
                                            "dev": guid, "dev_name": dev_short},
                                           f"Bound {key} -> Axis {i} on {dev_short}")
                            return
            except pygame.error:
                continue

    def _finish_mapping(self, msg):
        def update():
            self.set_status(msg, "ok")
            key = self.mapping_target
            base = key[:-4] if key.endswith("_POS") or key.endswith("_NEG") else key
            if base in self.bound_labels:
                text = self.describe_binding(base)
                is_bound = "unbound" not in text
                self.bound_labels[base].config(text=text, fg=C["green"] if is_bound else C["muted"])

        self.root.after(0, update)

    # ---------------- Output ----------------

    def _apply_deadzone(self, val):
        return 0.0 if abs(val) < DEADZONE else val

    def _get_joy(self, info):
        """Resolve the physical device for a binding; falls back to selected joystick for old configs."""
        if info and "dev" in info:
            return self.joy_by_guid.get(info["dev"])
        return self.current_joystick

    def _is_input_pressed(self, key):
        """Shared digital-input check for a binding key (button, hat direction, or axis threshold)."""
        info = self.mapping_info.get(key)
        j = self._get_joy(info)
        if not j or not info:
            return False
        try:
            if info["type"] == "BTN":
                return bool(j.get_button(info["index"]))
            if info["type"] == "HAT":
                hx, hy = j.get_hat(info["hat"])
                return hx == info["x"] and hy == info["y"] and (info["x"] != 0 or info["y"] != 0)
            val = j.get_axis(info["index"])
            return abs(val - info.get("rest", 0.0)) > 0.5
        except pygame.error:
            return False

    def _process_keyboard(self):
        """Keyboard binds run independently of the virtual Xbox pad."""
        use_vk = self.key_mode_var.get() == KEY_MODES[1]
        now = time.time()
        for slot in range(NUM_KB_SLOTS):
            key_name = self.kb_keys.get(slot, "")
            if not key_name or key_name not in SCANCODES:
                continue
            pressed = self._is_input_pressed(f"KB{slot}")
            was_held = self._kb_held.get(slot, False)
            if pressed and not was_held:
                send_key(key_name, down=True, use_vk=use_vk)
                self._kb_press_time[slot] = now
                self._kb_held[slot] = True
            elif not pressed and was_held:
                # enforce a minimum hold so game frame-polling can't miss a fast tap
                if now - self._kb_press_time.get(slot, 0) >= MIN_KEY_HOLD:
                    send_key(key_name, down=False, use_vk=use_vk)
                    self._kb_held[slot] = False
                # else: keep holding; release on a later cycle

    def test_fire_key(self, slot):
        """Fire a slot's key after a 3s countdown: click into the game window,
        and the key injects with no joystick involved - isolates the injection path."""
        key_name = self.kb_keys.get(slot, "")
        if not key_name or key_name not in SCANCODES:
            self.set_status(f"KB{slot + 1:02d}: pick a key first", "error")
            return
        use_vk = self.key_mode_var.get() == KEY_MODES[1]

        def run():
            for n in (3, 2, 1):
                self.root.after(0, lambda n=n: self.set_status(
                    f"TEST: focus the game window... sending {key_name} in {n}", "warn"))
                time.sleep(1)
            send_key(key_name, down=True, use_vk=use_vk)
            time.sleep(0.15)
            send_key(key_name, down=False, use_vk=use_vk)
            self.root.after(0, lambda: self.set_status(
                f"TEST: sent {key_name} ({self.key_mode_var.get()})", "ok"))

        threading.Thread(target=run, daemon=True).start()

    def _release_all_keys(self):
        if KEYBOARD_AVAILABLE:
            use_vk = self.key_mode_var.get() == KEY_MODES[1]
            for slot, held in self._kb_held.items():
                key_name = self.kb_keys.get(slot, "")
                if held and key_name in SCANCODES:
                    send_key(key_name, down=False, use_vk=use_vk)
        self._kb_held = {}

    def _drive_output(self):
        def read_axis(target):
            info = self.mapping_info.get(target)
            if not info or info["type"] != "AXIS":
                return None
            j = self._get_joy(info)
            if not j:
                return None
            try:
                val = j.get_axis(info["index"])
            except pygame.error:
                return None
            if self.invert_vars.get(target) and self.invert_vars[target].get():
                val = -val
            return val

        is_pressed = self._is_input_pressed

        def stick_value(name):
            invert = bool(self.invert_vars.get(name) and self.invert_vars[name].get())
            if self.control_modes.get(name, "Axis") == "Axis":
                val = read_axis(name) or 0.0  # invert already applied inside read_axis
                return self._apply_deadzone(val)
            pos = is_pressed(f"{name}_POS")
            neg = is_pressed(f"{name}_NEG")
            val = 0.0
            if pos and not neg:
                val = 1.0
            elif neg and not pos:
                val = -1.0
            return -val if invert else val

        lx = stick_value("LX")
        ly = stick_value("LY")
        rx = stick_value("RX")
        ry = stick_value("RY")
        self.vg_pad.left_joystick_float(x_value_float=lx, y_value_float=-ly)
        self.vg_pad.right_joystick_float(x_value_float=rx, y_value_float=-ry)

        for target, setter in (("LT", self.vg_pad.left_trigger_float), ("RT", self.vg_pad.right_trigger_float)):
            info = self.mapping_info.get(target)
            if not info:
                setter(value_float=0.0)
                continue
            if self.control_modes.get(target) == "Button":
                setter(value_float=1.0 if is_pressed(target) else 0.0)
            else:
                tj = self._get_joy(info)
                if not tj:
                    setter(value_float=0.0)
                    continue
                try:
                    raw = tj.get_axis(info["index"])
                except pygame.error:
                    setter(value_float=0.0)
                    continue
                rest = info["rest"]
                span = max(abs(1.0 - rest), abs(-1.0 - rest), 0.01)
                norm = abs(raw - rest) / span
                setter(value_float=max(0.0, min(1.0, norm)))

        for target, xusb_btn in BUTTON_TARGETS.items():
            pressed = is_pressed(target)
            was_held = self._button_held.get(target, False)
            if pressed and not was_held:
                self.vg_pad.press_button(button=xusb_btn)
            elif not pressed and was_held:
                self.vg_pad.release_button(button=xusb_btn)
            self._button_held[target] = pressed

        self.vg_pad.update()

    # ---------------- Joystick management ----------------

    def update_joystick_list(self):
        pygame.joystick.quit()
        pygame.joystick.init()
        self.active_joysticks = [pygame.joystick.Joystick(i) for i in range(pygame.joystick.get_count())]
        self.joy_by_guid = {}
        for joy in self.active_joysticks:
            joy.init()
            self.joy_by_guid[joy.get_guid()] = joy
        self.joystick_listbox.delete(0, tk.END)
        for i, joy in enumerate(self.active_joysticks):
            self.joystick_listbox.insert(tk.END, f" {i + 1}  {joy.get_name()}")
        if not self.active_joysticks:
            self.set_status("No joysticks detected", "warn")

    def on_joystick_selected(self, event):
        sel = self.joystick_listbox.curselection()
        if sel:
            self.current_joystick = self.active_joysticks[sel[0]]
            self.current_joystick.init()
            self.set_status(f"Selected: {self.current_joystick.get_name()}", "ok")

    def _update_armed_lamp(self):
        if self.controller_active:
            self.enable_btn.config(text="ENABLED ●", style="Armed.TButton")
        else:
            self.enable_btn.config(text="Enable", style="TButton")
        if self.kb_active:
            self.kb_enable_btn.config(text="KB ON ●", style="Armed.TButton")
        else:
            self.kb_enable_btn.config(text="KB Enable", style="TButton")
        # header lamp is lit if either system is live
        if self.controller_active or self.kb_active:
            self.armed_lamp.config(text="  ARMED  ", bg=C["green"], fg="#14171b")
        else:
            self.armed_lamp.config(text="  SAFE  ", bg=C["panel2"], fg=C["muted"])

    def toggle_keyboard(self):
        if not KEYBOARD_AVAILABLE:
            self.set_status("Keyboard emulation unavailable (Windows only)", "error")
            return
        self.kb_active = not self.kb_active
        if not self.kb_active:
            self._release_all_keys()
        self._update_armed_lamp()
        self.set_status("Keyboard binds ACTIVE" if self.kb_active
                        else "Keyboard binds disabled",
                        "ok" if self.kb_active else "info")

    def toggle_controller(self):
        self.controller_active = not self.controller_active
        if self.controller_active:
            self.vg_pad = vg.VX360Gamepad()
        else:
            if self.vg_pad:
                self.vg_pad.reset()
                self.vg_pad.update()
            self.vg_pad = None
            self._button_held = {}
        self._update_armed_lamp()
        self.set_status("Virtual controller ARMED" if self.controller_active
                        else "Virtual controller safe (disabled)",
                        "ok" if self.controller_active else "info")

    # ---------------- Save / Load (named profiles) ----------------

    @staticmethod
    def _sanitize_profile_name(name):
        name = "".join(c for c in name.strip() if c.isalnum() or c in "-_ ").strip()
        return name or "default"

    @staticmethod
    def _profile_path(name):
        return os.path.join(CONFIG_DIR, f"{name}.json")

    def list_profiles(self):
        try:
            return sorted(f[:-5] for f in os.listdir(CONFIG_DIR) if f.endswith(".json"))
        except OSError:
            return []

    def _refresh_profile_list(self):
        self.profile_box["values"] = self.list_profiles()

    def save_settings(self):
        """Save the current bindings to the profile named in the profile box.

        NOTE: this writes the SAME schema that _apply_loaded_data()/try_autoload()
        read (mapping_info / control_modes / invert / kb_keys / key_mode), and it
        writes to '<profile>.json' - the file that actually gets auto-loaded.
        Previously this wrote a separate 'settings.json' with different keys that
        nothing ever read back, so Save appeared to silently do nothing.
        """
        if not os.path.exists(CONFIG_DIR):
            os.makedirs(CONFIG_DIR)

        name = self._sanitize_profile_name(self.profile_var.get())
        data = {
            "mapping_info": self.mapping_info,
            "control_modes": self.control_modes,
            "invert": {k: bool(v.get()) for k, v in self.invert_vars.items()},
            "kb_keys": self.kb_keys,
            "key_mode": self.key_mode_var.get(),
        }
        if self.current_joystick:
            try:
                data["joystick_guid"] = self.current_joystick.get_guid()
                data["joystick_name"] = self.current_joystick.get_name()
            except pygame.error:
                pass

        with open(self._profile_path(name), "w") as f:
            json.dump(data, f, indent=2)

        try:
            with open(LAST_PROFILE_FILE, "w") as f:
                f.write(name)
        except OSError:
            pass

        self.profile_var.set(name)
        self._refresh_profile_list()
        self.set_status(f"Saved profile '{name}'", "ok")

    def load_settings(self):
        name = self._sanitize_profile_name(self.profile_var.get())
        path = self._profile_path(name)
        if not os.path.exists(path):
            messagebox.showinfo("Not found", f"No saved config named '{name}'.")
            return
        try:
            with open(path, "r") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            messagebox.showerror("Load failed", str(e))
            return
        self._apply_loaded_data(data)
        try:
            with open(LAST_PROFILE_FILE, "w") as f:
                f.write(name)
        except OSError:
            pass
        self.set_status(f"Loaded config '{name}'", "ok")

    def try_autoload(self):
        """Load the last-used profile on startup.

        This is the ONLY try_autoload now - the file used to define this method
        twice, which silently discarded the first definition and made it dead
        code. The one-time migration below also now renames the legacy config
        after copying it, so it can't keep resurrecting deleted bindings on
        every subsequent launch.
        """
        os.makedirs(CONFIG_DIR, exist_ok=True)

        # migrate old single-file config to a 'default' profile - ONCE.
        # Renaming OLD_CONFIG_PATH after the copy prevents this from re-firing
        # (and re-populating a deleted default.json) on every future launch.
        if os.path.exists(OLD_CONFIG_PATH) and not os.path.exists(self._profile_path("default")):
            try:
                with open(OLD_CONFIG_PATH, "r") as f:
                    data = json.load(f)
                with open(self._profile_path("default"), "w") as f:
                    json.dump(data, f, indent=2)
                os.replace(OLD_CONFIG_PATH, OLD_CONFIG_PATH + ".migrated")
            except (OSError, json.JSONDecodeError):
                pass

        name = "default"
        try:
            with open(LAST_PROFILE_FILE, "r") as f:
                name = self._sanitize_profile_name(f.read())
        except OSError:
            pass

        path = self._profile_path(name)
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    self._apply_loaded_data(json.load(f))
                self.profile_var.set(name)
                self.set_status(f"Loaded config '{name}'", "ok")
            except (OSError, json.JSONDecodeError):
                pass
        self._refresh_profile_list()

    def _apply_loaded_data(self, data):
        self.mapping_info = data.get("mapping_info", {})
        self.control_modes = data.get("control_modes", self.control_modes)

        # older configs stored one joystick for the whole profile; stamp any binding
        # that lacks a device with that GUID so it keeps working in multi-device mode
        legacy_guid = data.get("joystick_guid")
        if legacy_guid:
            legacy_name = (data.get("joystick_name") or "")[:18]
            for info in self.mapping_info.values():
                if isinstance(info, dict) and "dev" not in info:
                    info["dev"] = legacy_guid
                    if legacy_name:
                        info["dev_name"] = legacy_name

        for k, v in data.get("invert", {}).items():
            self.invert_vars.setdefault(k, tk.BooleanVar()).set(v)
        for k, v in data.get("kb_keys", {}).items():
            try:
                self.kb_keys[int(k)] = v
            except (ValueError, TypeError):
                pass
        if data.get("key_mode") in KEY_MODES:
            self.key_mode_var.set(data["key_mode"])

        guid = data.get("joystick_guid")
        if guid:
            for idx, joy in enumerate(self.active_joysticks):
                if joy.get_guid() == guid:
                    self.joystick_listbox.selection_clear(0, tk.END)
                    self.joystick_listbox.selection_set(idx)
                    self.current_joystick = joy
                    joy.init()
                    break
        self.render_bindings()

        # flag any bindings whose device isn't currently connected
        missing = {info.get("dev_name", "unknown device")
                   for info in self.mapping_info.values()
                   if isinstance(info, dict) and info.get("dev")
                   and info["dev"] not in self.joy_by_guid}
        if missing:
            self.set_status("WARNING: not connected: " + ", ".join(sorted(missing)), "warn")

    def clear_all(self):
        """Reset everything in memory - bindings, modes, inverts, and keyboard slots.

        Previously this only cleared mapping_info, leaving control_modes/kb_keys/
        invert_vars stale, and it never touched disk - so a stale saved profile
        would immediately repopulate everything the next time the app loaded it.
        Use 'Save' afterward if you also want this blank state written to disk.
        """
        self.mapping_info = {}
        self.control_modes = {k: "Axis" for k in ["LX", "LY", "RX", "RY", "LT", "RT"]}
        self.kb_keys = {i: "" for i in range(NUM_KB_SLOTS)}
        for var in self.invert_vars.values():
            var.set(False)
        self.render_bindings()
        self.set_status("Cleared all bindings (not yet saved to disk)", "info")

    # ---------------- Input tester ----------------

    def open_tester(self):
        if not self.current_joystick:
            messagebox.showinfo("No joystick", "Select a joystick first.")
            return
        if getattr(self, "tester_window", None):
            try:
                if self.tester_window.winfo_exists():
                    self.tester_window.lift()
                    return
            except tk.TclError:
                pass

        j = self.current_joystick
        n_axes = j.get_numaxes()
        n_buttons = j.get_numbuttons()
        n_hats = j.get_numhats()

        win = tk.Toplevel(self.root)
        win.title(f"Input Tester - {j.get_name()}")
        win.geometry("500x720")
        win.configure(bg=C["bg"])
        self.tester_window = win

        outer, body = self.make_section(win, f"Axes ({n_axes})")
        outer.pack(fill=tk.X, padx=10, pady=5)
        self.tester_axis_canvases = []
        self.tester_axis_labels = []
        for i in range(n_axes):
            row = tk.Frame(body, bg=C["panel"])
            row.pack(fill=tk.X, pady=2)
            tk.Label(row, text=f"AX {i}", width=6, anchor="w", bg=C["panel"],
                     fg=C["muted"], font=FONT_MONO).pack(side=tk.LEFT)
            canvas = tk.Canvas(row, width=300, height=16, bg=C["panel2"],
                               highlightthickness=1, highlightbackground=C["border"])
            canvas.pack(side=tk.LEFT, padx=5)
            val_label = tk.Label(row, text="+0.00", width=6, bg=C["panel"],
                                 fg=C["text"], font=FONT_MONO)
            val_label.pack(side=tk.LEFT)
            self.tester_axis_canvases.append(canvas)
            self.tester_axis_labels.append(val_label)

        self.tester_hat_canvases = []
        if n_hats:
            outer, body = self.make_section(win, f"Hats / POV ({n_hats})")
            outer.pack(fill=tk.X, padx=10, pady=5)
            for h in range(n_hats):
                col = tk.Frame(body, bg=C["panel"])
                col.pack(side=tk.LEFT, padx=15, pady=5)
                tk.Label(col, text=f"HAT {h}", bg=C["panel"], fg=C["muted"],
                         font=FONT_MONO).pack()
                c = tk.Canvas(col, width=70, height=70, bg=C["panel2"],
                              highlightthickness=1, highlightbackground=C["border"])
                c.pack()
                self.tester_hat_canvases.append(c)

        outer, body = self.make_section(win, f"Buttons ({n_buttons})")
        outer.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        self.tester_button_widgets = []
        cols = 6
        for b in range(n_buttons):
            r, c = divmod(b, cols)
            lbl = tk.Label(body, text=f"{b:02d}", width=4, height=2, bg=C["panel2"],
                           fg=C["muted"], font=FONT_MONO,
                           highlightthickness=1, highlightbackground=C["border"])
            lbl.grid(row=r, column=c, padx=3, pady=3)
            self.tester_button_widgets.append(lbl)

        self._tester_running = True
        win.protocol("WM_DELETE_WINDOW", self._close_tester)
        self.update_tester()

    def _close_tester(self):
        self._tester_running = False
        if getattr(self, "tester_window", None):
            self.tester_window.destroy()
            self.tester_window = None

    def update_tester(self):
        if not getattr(self, "_tester_running", False) or not getattr(self, "tester_window", None):
            return
        try:
            if not self.tester_window.winfo_exists():
                return
        except tk.TclError:
            return

        j = self.current_joystick
        if j:
            try:
                for i, canvas in enumerate(self.tester_axis_canvases):
                    val = j.get_axis(i)
                    w, h = int(canvas["width"]), int(canvas["height"])
                    mid = w / 2
                    x = mid + (val * mid)
                    color = C["green"] if abs(val) > DEADZONE else C["border"]
                    canvas.delete("all")
                    canvas.create_line(mid, 0, mid, h, fill=C["muted"])
                    canvas.create_rectangle(min(mid, x), 1, max(mid, x), h - 1, fill=color, outline="")
                    self.tester_axis_labels[i].config(
                        text=f"{val:+.2f}",
                        fg=C["green"] if abs(val) > DEADZONE else C["muted"])

                for h_i, canvas in enumerate(self.tester_hat_canvases):
                    hx, hy = j.get_hat(h_i)
                    canvas.delete("all")
                    cx, cy = 35, 35
                    canvas.create_oval(5, 5, 65, 65, outline=C["muted"])
                    px, py = cx + hx * 25, cy - hy * 25
                    active = (hx, hy) != (0, 0)
                    canvas.create_oval(px - 8, py - 8, px + 8, py + 8,
                                       fill=C["green"] if active else C["border"], outline="")

                for b_i, lbl in enumerate(self.tester_button_widgets):
                    pressed = bool(j.get_button(b_i))
                    lbl.config(bg=C["green"] if pressed else C["panel2"],
                               fg="#14171b" if pressed else C["muted"])
            except (pygame.error, IndexError):
                pass

        self.tester_window.after(33, self.update_tester)

    def on_close(self):
        self._stop = True
        self._release_all_keys()
        self.root.after(50, self.root.destroy)


if __name__ == "__main__":
    root = tk.Tk()
    app = Xbox360Mapper(root)
    root.mainloop()
