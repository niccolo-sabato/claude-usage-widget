"""
Claude AI Usage Widget for Windows 11

Floating always-on-top widget showing Claude.ai usage statistics.
W11 Material style, rounded corners, resizable, position/size saving.

Controls:
  - White dot (bottom-left): expand/collapse extra bars
  - Ochre dot (bottom-right): drag to resize, double-click for essential mode
  - Essential mode: hides title bar + section names, percentage shown inside bar
    Double-click ochre dot again to return to normal mode
  - Hamburger menu (≡) in title bar: settings, refresh, mode toggle

To get a new sessionKey when it expires:
  1. Go to claude.ai (logged in)
  2. F12 → Application → Cookies → https://claude.ai
  3. Find "sessionKey" row, copy the Value
  4. Paste via ≡ menu → Renew session

To start at Windows login:
  Win+R -> shell:startup -> create shortcut to the installed exe.
"""

import sys
import os
import ctypes


def _early_excepthook(exc_type, exc_value, exc_tb):
    """Report a failure that happens before the widget exists.

    Everything below this point can fail at import time, and the one that
    really does is `ssl`: right after an update the antivirus is still holding
    `_ssl.pyd`, the load fails, and the frozen build answers with PyInstaller's
    own red "Unhandled exception in script" window, which shows a traceback and
    says nothing about what to do. Suppressing that window with
    --disable-windowed-traceback on its own would trade an ugly message for no
    message at all, so the handler goes in FIRST, before the imports it has to
    survive: the traceback still reaches crash.log and the user gets a sentence
    they can act on. The full handler replaces this one once the module is up.

    Deliberately built out of sys, os and ctypes alone, which are already
    imported: a crash reporter that needs the thing that just failed reports
    nothing.
    """
    import traceback
    text = ''.join(traceback.format_exception(exc_type, exc_value, exc_tb))
    path = ''
    try:
        folder = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')),
                              'Claude Usage')
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, 'crash.log')
        with open(path, 'a', encoding='utf-8') as f:
            f.write('\n=== startup failure ===\n' + text)
    except OSError:
        pass
    if (os.environ.get('CLAUDE_USAGE_DEV') == '1'
            or (len(sys.argv) > 1 and sys.argv[1] == '--cc')):
        # A modal box waits for a click, and a test bench has nobody to give
        # it one: this handler hung a test run until the window was closed by
        # hand. Nor has the command line (--cc): the scripts that run it often
        # work with nobody at the PC, waiting for its exit code. The log above
        # is written either way.
        return
    try:
        ctypes.windll.user32.MessageBoxW(
            None,
            'Claude Usage could not start.\n\n'
            'This usually clears by itself: another program was still holding '
            'one of its files. It will try again in a few seconds.\n\n'
            + (f'Details: {path}' if path else text[-400:]),
            'Claude Usage', 0x30)  # MB_ICONWARNING
    except Exception:
        pass


sys.excepthook = _early_excepthook

import re
import json
import uuid
import colorsys
import math
import ssl
import time
import signal
import atexit
import tempfile
import threading
import shutil
import base64
import socket
import subprocess
import webbrowser
import winreg
import urllib.request
import urllib.error
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime, timedelta, timezone
from PIL import Image, ImageDraw, ImageTk

# The Claude Code profiles live in their own module: it draws nothing and
# imports no tkinter, which is what lets the bench exercise it against fake
# credentials in a temporary directory. Running from source, its folder is not
# necessarily on the path; frozen, PyInstaller has already bundled it.
_SRC_DIR = os.path.dirname(os.path.abspath(__file__))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)
import cc_accounts  # noqa: E402

# ─── DPI awareness ──────────────────────────────────
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

# NOTE: SetCurrentProcessExplicitAppUserModelID was tried here to
# improve the taskbar progress overlay rendering on Win11 22H2+, but
# it disassociated the running process from the installer-supplied
# icon (Windows treats a new AUMID as a fresh app entity without an
# attached icon) - the taskbar showed the default placeholder
# instead of the Claude logo. Reverted: we live with the default
# rendering rather than break the icon.

# ─── Paths ───────────────────────────────────────────
# EXE_DIR: where the exe/script lives (Program Files or Scripts)
# DATA_DIR: writable folder for config, logs (AppData\Local\Claude Usage)
# _RES: bundled resources when running as PyInstaller exe
EXE_DIR = os.path.dirname(os.path.abspath(sys.argv[0] if sys.argv and sys.argv[0] else __file__))


def _resolve_local_appdata():
    """Per-user local AppData for config/logs. Prefer %LOCALAPPDATA%; fall back
    to %USERPROFILE%\\AppData\\Local so we never end up writing into Program
    Files (EXE_DIR) when a stripped autostart environment omits LOCALAPPDATA -
    a non-elevated write there fails or is redirected to VirtualStore, which is
    how settings 'vanish' between runs."""
    local = os.environ.get('LOCALAPPDATA')
    if local and os.path.isdir(local):
        return local
    profile = os.environ.get('USERPROFILE')
    if profile:
        candidate = os.path.join(profile, 'AppData', 'Local')
        if os.path.isdir(candidate):
            return candidate
    return local or EXE_DIR


def _ensure_data_dir():
    """Pick a writable folder for config/logs, trying the per-user AppData
    first, then the temp dir, then the exe's own folder. Never raises at import
    time (which would kill the process before any window or log exists)."""
    for base in (_resolve_local_appdata(), tempfile.gettempdir(), EXE_DIR):
        path = os.path.join(base, 'Claude Usage')
        try:
            os.makedirs(path, exist_ok=True)
            return path
        except OSError:
            continue
    return EXE_DIR  # give up gracefully; writes may fail but import survives


DATA_DIR = _ensure_data_dir()
_RES = getattr(sys, '_MEIPASS', EXE_DIR)  # bundled resources (icons) or same as EXE_DIR
# Migrate config.json from old location if needed
_old_cfg = os.path.join(EXE_DIR, 'config.json')
_new_cfg = os.path.join(DATA_DIR, 'config.json')
if os.path.exists(_old_cfg) and not os.path.exists(_new_cfg):
    try:
        shutil.copy2(_old_cfg, _new_cfg)
    except OSError:
        pass
CFG = _new_cfg
# Dev mode (env CLAUDE_USAGE_DEV=1): use a separate config so test runs never
# touch the real one, and skip single-instance (below) so the source can run
# alongside an installed copy. Invisible to normal users (env var unset).
if os.environ.get('CLAUDE_USAGE_DEV') == '1':
    CFG = os.path.join(DATA_DIR, 'config-dev.json')
    # 🔴 Nor the real Claude Code login. Two benches opened the account
    # window without pointing the credential paths anywhere, and the login
    # check the window starts ran on the user's real credentials, over the
    # network, with the right to bring their saved logins in step (found on
    # 2026-09-26). As with the notifications, the rule lives here so that no
    # tool can forget it: every path a test run has not set points into an
    # empty home of its own, where there is no login at all.
    # CLAUDE_USAGE_REAL_CC=1 lets a deliberate test on the real login through.
    if os.environ.get('CLAUDE_USAGE_REAL_CC') != '1':
        _dev_home = None
        for _var, _leaf in (('CLAUDE_WIDGET_CC_STORE', 'claude-accounts'),
                            ('CLAUDE_WIDGET_CC_CRED', 'credentials.json'),
                            ('CLAUDE_WIDGET_CC_CACHE', 'claude.json')):
            if not os.environ.get(_var):
                if _dev_home is None:
                    _dev_home = tempfile.mkdtemp(prefix='claude-usage-dev-')
                    atexit.register(shutil.rmtree, _dev_home, True)
                os.environ[_var] = os.path.join(_dev_home, _leaf)
# Backup companion for corruption recovery (see load_cfg/save_cfg).
CFG_BAK = CFG + '.bak'

def _find_res(name):
    """Locate a bundled resource: try _RES root, then _RES/assets, then EXE_DIR/assets."""
    for base in (_RES, os.path.join(_RES, 'assets'), os.path.join(EXE_DIR, 'assets')):
        p = os.path.join(base, name)
        if os.path.exists(p):
            return p
    return os.path.join(_RES, name)  # fallback (may not exist)

ICO = _find_res('claude.ico')
ICO_BAR = _find_res('icon-bar.png')
ICO_GITHUB = _find_res('icon-github-16.png')
# Keep DIR as alias for DATA_DIR (used by log paths)
DIR = DATA_DIR

# ─── Theme ───────────────────────────────────────────
BG       = '#262624'
BG_TITLE = '#1e1e1c'
BAR_BG   = '#3a3a38'
FG       = '#e4e4e4'
DIM      = '#d0d0ce'
CLAUDE   = '#DA7756'
# The accent as a fill is the product's colour and never changes. As TEXT it
# has to hold against the surface behind it: on a light card #DA7756 reads at
# 2.4:1, so the light theme darkens it and the dark theme keeps it as it is.
CLAUDE_TEXT = '#DA7756'
RED      = '#E85858'
ORANGE   = '#E8A838'
BLUE     = '#5B9BD5'
PURPLE   = '#9B72CF'
HOVER_BG = '#3a3a38'
OCHRE    = '#C8962A'
DOT_W    = '#d0d0d0'
DOT_W_H  = '#ffffff'
DOT_W_D  = '#a0a09e'
DOT_GREEN   = '#6BC275'  # pre-refresh breathing dot, and the header's info text
MENU_BG  = '#2c2c2a'

# ─── Bar palette (fixed per bar) ─────────────────────
# Fill = used portion, track = unused portion, aligned to Claude.ai's usage UI.
# The track is a dark shade of the fill, shown only once the bar has usage; an
# empty bar keeps the neutral gray (BAR_BG). The optional dynamic palette
# (dynamic_fill) overrides these with a colour driven by the usage level.
BAR_FILL_SESSION  = '#fab219'   # amber - current session
BAR_TRACK_SESSION = '#311a00'
BAR_FILL_WEEKLY   = '#2a78d6'   # blue - weekly, all models
BAR_TRACK_WEEKLY  = '#032042'
BAR_FILL_HIGH     = '#d03b3b'   # red - high-usage tone, used for the third bar
BAR_TRACK_HIGH    = '#3c0e0e'
# Purple is our own accent (not in Claude's UI); track derived with the same
# darkening as the pairs above. One of the picker presets.
BAR_FILL_PURPLE   = '#9b72cf'
BAR_TRACK_PURPLE  = '#2a1d3a'

# Default fill per bar (overridable per bar via the colour picker) and the
# quick-pick presets the swatch offers.
BAR_DEFAULT_FILL = {'session': BAR_FILL_SESSION,
                    'weekly':  BAR_FILL_WEEKLY,
                    'sonnet':  BAR_FILL_HIGH}
BAR_PRESETS = [BAR_FILL_SESSION, BAR_FILL_WEEKLY, BAR_FILL_HIGH, BAR_FILL_PURPLE]

# ─── App ────────────────────────────────────────────
APP_VERSION = '2.11.1-test2'

# ─── Auto-update ────────────────────────────────────
UPDATE_REPO = 'niccolo-sabato/claude-usage-widget'
UPDATE_API_URL = f'https://api.github.com/repos/{UPDATE_REPO}/releases/latest'
UPDATE_RELEASES_URL = f'https://github.com/{UPDATE_REPO}/releases'
UPDATE_ASSET_NAME = 'ClaudeUsage-Setup.exe'
UPDATE_CHECK_INTERVAL_S = 24 * 3600       # default throttle between auto-checks
UPDATE_STARTUP_DELAY_MS = 10_000          # check 10s after widget ready
UPDATE_CHANGELOG_MAX_CHARS = 1400         # truncate release body shown in dialog

# ─── Layout ──────────────────────────────────────────
DEF_W    = 280
MIN_W    = 210
MIN_W_ESS = 150  # essential strip: no title bar to fit, so it may go smaller
MIN_H_E  = 46   # essential mode minimum height
MIN_H_N  = 90   # normal mode minimum height
PAD      = 12
BAR_H    = 16
TITLE_H  = 28
DOT_INSET = BAR_H // 2   # dot centre sits on the centre of the bar's right rounded
                         # cap (the ideal circle that completes the end semicircle)
DOT_DIAM  = 7     # pre-refresh dot diameter (matches the corner dots' footprint)
SUB_LABEL_PADX = 6   # the reset label's own left inset (Section.lbl_sub)
ESS_CONTROLS_INSET = 18  # gap the close/refresh/time block is placed at (place x=-18)
ESS_MENU_W = 62   # hamburger pill width; reserves >= the bottom-right controls'
                  # footprint so the per-bar reset text never collides with them
REFRESH  = 180_000  # 3 minutes
GEOMETRY_WATCH_MS = 2000  # self-heal poll interval: detect live monitor-layout
                          # changes and reposition the widget (home / rescue)
ICON_CELL_W = 34   # fixed icon-column width in menus so labels align across fonts
ICON_CELL_H = 26

# ─── Fonts ───────────────────────────────────────────
# Text fonts are Tk named fonts, built by init_fonts() once the root exists
# and bound to the FT_* names below. Named fonts can be retargeted at runtime:
# _set_language() swaps the family and every widget already using them
# repaints, so a language change needs no restart.
#
# The Japanese family is not a cosmetic choice. Segoe UI carries no CJK
# glyphs, so Windows substitutes a gothic face glyph by glyph and, when a bold
# weight is asked for, emboldens it synthetically: at UI sizes that smears kana
# into an unreadable blob. Yu Gothic UI is the system Japanese UI family and
# ships real regular and bold faces.
_FONT_SPECS = {
    'FT':           ('Segoe UI', 9),
    'FT_B':         ('Segoe UI', 9, 'bold'),
    'FT_S':         ('Segoe UI', 8),
    'FT_BTN':       ('Segoe UI', 11),
    'FT_BAR':       ('Segoe UI', 9, 'bold'),    # Bar percentage + reset text
    'FT_DLG_TITLE': ('Segoe UI Semibold', 10),  # Dialog title bars
    'FT_DLG_H':     ('Segoe UI', 11, 'bold'),   # Section headers inside dialogs
    'FT_DLG_BODY':  ('Segoe UI', 10),           # Body text, entries
    'FT_DLG_HINT':  ('Segoe UI', 9),            # Hints / status lines
    'FT_DLG_HINT_LINK': ('Segoe UI', 9, 'normal', 'underline'),  # A link in one, under the pointer
    'FT_DLG_BTN':   ('Segoe UI', 10),           # Secondary pill button text
    'FT_DLG_BTN_B': ('Segoe UI', 10, 'bold'),   # Primary pill button text
    'FT_MENU':      ('Segoe UI', 10),           # Menu row text
    'FT_MENU_B':    ('Segoe UI', 10, 'bold'),   # Menu row text, selected
}

# Latin family -> family holding the same role that has Japanese glyphs.
JP_FAMILY = {
    'Segoe UI':          'Yu Gothic UI',
    'Segoe UI Semibold': 'Yu Gothic UI Semibold',
}


def _family_for(family, lang):
    return JP_FAMILY.get(family, family) if lang == 'ja' else family


def init_fonts(root, lang):
    """Create the named fonts. Run after the root exists, before any widget."""
    for name, spec in _FONT_SPECS.items():
        globals()[name] = tkfont.Font(
            root=root, name='cu_' + name, exists=False,
            family=_family_for(spec[0], lang), size=spec[1],
            weight=spec[2] if len(spec) > 2 else 'normal',
            underline=int(len(spec) > 3 and spec[3] == 'underline'))


def apply_font_lang(lang):
    """Point the named fonts at the family that has glyphs for `lang`."""
    for name, spec in _FONT_SPECS.items():
        globals()[name].configure(family=_family_for(spec[0], lang))


# Icon fonts stay plain tuples: their families are picked for glyph coverage,
# so they never follow the UI language.
FT_EMOJI = ('Segoe UI Emoji', 10)
# Geometry glyphs: dots, radio/check markers, category arrows. These are UI
# furniture, not text, so they must not follow the language either. The
# Japanese families draw the same codepoints full-width, which visibly inflates
# the corner dots and widens the fixed-width marker cells.
FT_DOT  = ('Segoe UI', 10)   # corner expand / resize dots
FT_MARK = ('Segoe UI', 10)   # menu radio, check and category-arrow glyphs
# The language menu lists each language under its own name, so the Japanese
# row stays Japanese even while the UI is English or Italian. Segoe UI has no
# CJK glyphs: Windows substitutes a face for kana but drops kanji outright,
# which rendered that row blank. Pin it to a family that has them.
FT_MENU_JP = ('Yu Gothic UI', 10)

# ─── Dialog / menu design system ─────────────────────
FT_EMOJI_11  = ('Segoe UI Emoji', 11)      # Emoji icons in dialogs/menus
# Segoe MDL2 Assets ships with Windows 10/11 and exposes a curated set of
# monochrome icons at a uniform visual weight via private-use codepoints.
# We use it only for the refresh icon (title bar, menu row, essential mode)
# so the three occurrences are pixel-identical; the rest of the menu keeps
# the original emoji+arrow icon set.
FT_MDL2_TB   = ('Segoe MDL2 Assets', 9)    # tight title bar / essential-mode size
FT_MDL2_MENU = ('Segoe MDL2 Assets', 10)   # same point size as FT_EMOJI for the menu row

ICON_REFRESH = '\uE72C'   # Segoe MDL2 Refresh glyph
ICON_KEY    = '\uE192'    # Segoe MDL2 Permissions (key) - edit account key
ICON_EDIT   = '\uE70F'    # Segoe MDL2 Edit (pencil) - rename account
ICON_DELETE = '\uE74D'    # Segoe MDL2 Delete (trash) - remove account
ICON_ADD    = '\uE710'    # Segoe MDL2 Add (plus) - add account
ICON_RESTART = '\uE777'  # Segoe MDL2 UpdateRestore - restart the widget

# Surface / state colors used by dialogs and menus (complement the theme)
# The light theme. Only the surfaces, the text and the tracks change: the
# bar fills and the Claude accent are the product's identity and stay put. Tk
# keeps the colour a widget was created with, so this is applied once at
# start-up (apply_theme) and a change asks for a restart.
LIGHT_THEME = {
    'BG': '#f4f3f1', 'BG_TITLE': '#e8e6e3', 'BAR_BG': '#e2e0dd',
    'FG': '#20201e', 'DIM': '#6a6a67', 'HOVER_BG': '#eceae7',
    'MENU_BG': '#fbfaf9', 'SOFT_BG': '#eceae7', 'SOFT_BG_HV': '#e0ddd9',
    'CLOSE_HV': '#f6dcdc', 'OCHRE': '#B5811A',
    'RED': '#C43D3D', 'ORANGE': '#B8801A', 'BLUE': '#2A6FB0', 'PURPLE': '#7A50B0',
    'CLAUDE_TEXT': '#A8482A', 'DOT_GREEN': '#2F6F35',
    # Matched to the weight the dark dots carry (measured as contrast against
    # their own background), not to their hex values.
    'DOT_W': '#5f5f5c', 'DOT_W_H': '#2a2a28', 'DOT_W_D': '#7b7b78',
    'BAR_TRACK_SESSION': '#f7e7c4', 'BAR_TRACK_WEEKLY': '#d8e6f7',
    'BAR_TRACK_HIGH': '#f7dcdc', 'BAR_TRACK_PURPLE': '#e9e0f6',
}
_DARK_THEME = {}


THEME = 'dark'


def apply_theme(name):
    """Rebind the colour globals for `name` ('dark' or 'light').

    Must run before any widget is built. The dark values are captured on the
    first call, so switching back restores the originals rather than a copy of
    them that could drift.
    """
    g = globals()
    if not _DARK_THEME:
        _DARK_THEME.update({k: g[k] for k in LIGHT_THEME})
    light = name == 'light'
    g.update(LIGHT_THEME if light else _DARK_THEME)
    g['THEME'] = 'light' if light else 'dark'
    _INK_CACHE.clear()


SOFT_BG    = '#2e2e2c'   # secondary pill button / card surface
SOFT_BG_HV = '#363634'   # secondary pill hover
PRIMARY_HV = '#E08060'   # primary pill hover
FOCUS_RING = '#C8652E'   # darker Claude for focused entry outline
CLOSE_HV   = '#3a1818'   # title bar close button hover tint

# Pill button padding presets - every dialog/menu button uses these so sizes
# stay visually consistent across the app.
PILL_PAD_PRIMARY_X   = 22  # baseline (96 DPI); scaled at use-site by dpi_scale
PILL_PAD_PRIMARY_Y   = 8
PILL_PAD_SECONDARY_X = 18
PILL_PAD_SECONDARY_Y = 8

# Dialog chrome constants
DLG_TB_HEIGHT = 34
DLG_PAD_X     = 20
DLG_PAD_TOP   = 18
DLG_PAD_BTM   = 16
SCREEN_MARGIN = 8        # minimum gap from screen edge
TASKBAR_GAP   = 50       # keep dialogs clear of the taskbar

# ─── Logging ────────────────────────────────────────
LOG_FILE = os.path.join(DIR, 'widget.log')
CRASH_LOG_FILE = os.path.join(DIR, 'crash.log')
MAX_LOG_LINES = 200
MAX_CRASH_LOG_BYTES = 256 * 1024  # 256 KB cap; older entries are dropped.

_log_count = 0


def write_crash(tag, tb):
    """Append a traceback to crash.log, capped at MAX_CRASH_LOG_BYTES.

    Each entry starts with '--- timestamp tag ---' so older entries can be
    identified and truncated whole. The cap exists because the previous
    behaviour was unbounded append, which let the file grow without limit.
    """
    try:
        line = f'\n--- {datetime.now():%Y-%m-%d %H:%M:%S} {tag} ---\n{tb}'
        with open(CRASH_LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line)
        # Trim from the front if we exceeded the cap.
        size = os.path.getsize(CRASH_LOG_FILE)
        if size > MAX_CRASH_LOG_BYTES:
            with open(CRASH_LOG_FILE, 'rb') as f:
                f.seek(size - MAX_CRASH_LOG_BYTES)
                tail = f.read()
            # Drop the first incomplete entry so the file starts on a header.
            cut = tail.find(b'\n--- ')
            if cut > 0:
                tail = tail[cut:]
            with open(CRASH_LOG_FILE, 'wb') as f:
                f.write(tail)
    except Exception:
        pass

def wlog(msg):
    """Append a timestamped line to widget.log, truncating when over MAX_LOG_LINES."""
    global _log_count
    line = f'{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}\n'
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line)
        _log_count += 1
        # Truncate periodically (every 50 writes) to keep file manageable
        if _log_count >= 50:
            _log_count = 0
            with open(LOG_FILE, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            if len(lines) > MAX_LOG_LINES:
                with open(LOG_FILE, 'w', encoding='utf-8') as f:
                    f.writelines(lines[-MAX_LOG_LINES:])
    except Exception:
        pass

# ─── Win32 ITaskbarList3 (taskbar icon progress bar) ─


class TaskbarProgress:
    """Wrap ITaskbarList3 so the widget can paint a coloured progress
    bar underneath its taskbar icon (the same UI Edge / Explorer use
    during file copies and downloads).

    Only renders when the widget's window is visible in the taskbar:
    Windows ignores SetProgressValue calls on toolwindow-styled or
    hidden windows. The widget's taskbar visibility is itself a
    user-controlled toggle (see Widget._set_taskbar_visible).

    Bare ctypes COM, no pywin32. Vtable layout is fixed by the COM
    contract:
      0..2   IUnknown      (QueryInterface, AddRef, Release)
      3..7   ITaskbarList  (HrInit, AddTab, DeleteTab, ActivateTab, SetActiveAlt)
      8      ITaskbarList2 (MarkFullscreenWindow)
      9..    ITaskbarList3 (SetProgressValue, SetProgressState, ...)
    """
    _CLSID_TASKBARLIST = '{56FDF344-FD6D-11d0-958A-006097C9A090}'
    _IID_ITASKBARLIST3 = '{ea1afb91-9e28-4b86-90e9-9e9f8a5eefaf}'
    _CLSCTX_INPROC_SERVER = 0x1
    _COINIT_APARTMENTTHREADED = 0x2
    _S_OK = 0
    # TBPFLAG values
    NOPROGRESS    = 0
    INDETERMINATE = 1
    NORMAL        = 2  # green
    ERROR         = 4  # red
    PAUSED        = 8  # yellow

    class _GUID(ctypes.Structure):
        _fields_ = [('Data1', ctypes.c_ulong),
                    ('Data2', ctypes.c_ushort),
                    ('Data3', ctypes.c_ushort),
                    ('Data4', ctypes.c_ubyte * 8)]

    def __init__(self):
        self._ptr = None
        self._co_initialized = False
        try:
            ole = ctypes.windll.ole32
            # Apartment-threaded matches Tk's main thread model. If COM is
            # already initialized (e.g. by some extension) RPC_E_CHANGED_MODE
            # may be returned; that's fine, we just don't pair an Uninit.
            hr = ole.CoInitializeEx(None, self._COINIT_APARTMENTTHREADED)
            self._co_initialized = (hr == self._S_OK)

            clsid = self._GUID()
            iid = self._GUID()
            ole.CLSIDFromString(self._CLSID_TASKBARLIST,
                                ctypes.byref(clsid))
            ole.IIDFromString(self._IID_ITASKBARLIST3,
                              ctypes.byref(iid))

            ptr = ctypes.c_void_p()
            hr = ole.CoCreateInstance(
                ctypes.byref(clsid), None,
                self._CLSCTX_INPROC_SERVER,
                ctypes.byref(iid),
                ctypes.byref(ptr))
            if hr != self._S_OK or not ptr.value:
                raise RuntimeError(f'CoCreateInstance HRESULT {hr:#x}')
            self._ptr = ptr.value
            # ITaskbarList::HrInit must be called once before any other
            # method or SetProgressValue silently no-ops.
            self._invoke(3, ctypes.HRESULT)
            wlog('TASKBAR ITaskbarList3 ready')
        except Exception as e:
            wlog(f'TASKBAR init failed: {e}')
            self.close()

    def _invoke(self, vtable_index, restype, *args):
        """Call vtable[vtable_index] of self._ptr with the given args."""
        if not self._ptr:
            return None
        # Resolve method pointer: this -> *vtable -> vtable[index]
        vtable_addr = ctypes.cast(
            self._ptr, ctypes.POINTER(ctypes.c_void_p))[0]
        method_addr = ctypes.cast(
            vtable_addr, ctypes.POINTER(ctypes.c_void_p))[vtable_index]
        proto = ctypes.WINFUNCTYPE(
            restype, ctypes.c_void_p, *(type(a) for a in args))
        return proto(method_addr)(self._ptr, *args)

    def set_progress(self, hwnd, completed, total=100):
        """SetProgressValue (vtable index 9). 0-`total` -> 0-100% width."""
        try:
            self._invoke(
                9, ctypes.HRESULT,
                ctypes.c_void_p(hwnd),
                ctypes.c_ulonglong(int(completed)),
                ctypes.c_ulonglong(int(total)))
        except Exception as e:
            wlog(f'TASKBAR set_progress: {e}')

    def set_state(self, hwnd, state):
        """SetProgressState (vtable index 10). state is a TBPFLAG."""
        try:
            self._invoke(
                10, ctypes.HRESULT,
                ctypes.c_void_p(hwnd),
                ctypes.c_int(state))
        except Exception as e:
            wlog(f'TASKBAR set_state: {e}')

    def close(self):
        if self._ptr:
            try:
                # IUnknown::Release (vtable index 2)
                self._invoke(2, ctypes.c_ulong)
            except Exception:
                pass
            self._ptr = None
        if self._co_initialized:
            try:
                ctypes.windll.ole32.CoUninitialize()
            except Exception:
                pass
            self._co_initialized = False


# ─── Toast notifications ─────────────────────────────


def _xml_escape(s):
    return (str(s).replace('&', '&amp;')
            .replace('<', '&lt;').replace('>', '&gt;'))


# Stable AppUserModelID. Must match the key registered in HKCU below
# AND the value passed to CreateToastNotifier in the PowerShell snippet.
TOAST_AUMID = 'NiccoloSabato.ClaudeUsage'


def register_toast_aumid():
    """Register the widget's AUMID in HKCU so Windows surfaces our
    toasts as banner notifications (and lists them under Settings ->
    Notifications with the right name and icon). Without this, Windows
    accepts the toast call but routes it straight to Action Center
    silently - the user observed "PS exit=0 / TOAST delivered" in the
    log but no banner ever appeared.

    HKCU only, no admin required. Safe to re-run on every launch.
    """
    try:
        key_path = r'Software\Classes\AppUserModelId\\' + TOAST_AUMID
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as k:
            winreg.SetValueEx(k, 'DisplayName', 0, winreg.REG_SZ,
                              'Claude Usage')
            try:
                if os.path.isfile(ICO):
                    winreg.SetValueEx(k, 'IconUri', 0, winreg.REG_SZ, ICO)
            except Exception:
                pass
    except Exception as e:
        wlog(f'AUMID  register failed: {e}')


def show_toast(title, lines):
    """Fire a Windows toast notification with one heading + N body lines.

    `lines` is a list/tuple of strings; each one becomes a separate
    `<text>` element in the ToastGeneric template (Windows shows them
    stacked under the bold heading).

    The XML is base64-encoded and reconstituted inside PowerShell to
    bypass the quoting / encoding pitfalls of the previous Get-Content
    -Raw + LoadXml() approach (which silently fell back to "Nuova
    notifica" / "New notification" on this Windows 11 build because the
    string-vs-file LoadXml overload was getting confused). Both WinRT
    types (`Windows.UI.Notifications` and `Windows.Data.Xml.Dom`) are
    pre-loaded since PowerShell 5 doesn't auto-resolve them on a bare
    New-Object.

    Synchronous call with a 5 s timeout so failures are surfaced into
    widget.log instead of disappearing.
    """
    # 🔴 Never from a test run. Every bench, probe and photograph loads
    # this file with CLAUDE_USAGE_DEV=1, and on 2026-09-25 two of them fed the
    # widget a session at 40% and at 77% and sent the user real Windows
    # notifications, over and over, for sessions that did not exist. Muting
    # each tool one by one had already failed once the same evening, so the
    # rule lives here, where no tool can forget it. CLAUDE_USAGE_TOASTS=1 lets
    # a deliberate test of the notifications through.
    if (os.environ.get('CLAUDE_USAGE_DEV') == '1'
            and os.environ.get('CLAUDE_USAGE_TOASTS') != '1'):
        wlog(f'TOAST  (test run, not sent) {title}: {lines}')
        return
    try:
        if isinstance(lines, str):
            lines = [lines]
        body_xml = ''.join(f'<text>{_xml_escape(l)}</text>' for l in lines)
        xml = (
            '<toast><visual><binding template="ToastGeneric">'
            f'<text>{_xml_escape(title)}</text>{body_xml}'
            '</binding></visual></toast>'
        )
        xml_b64 = base64.b64encode(xml.encode('utf-8')).decode('ascii')
        ps = (
            "[Windows.UI.Notifications.ToastNotificationManager,"
            "Windows.UI.Notifications,ContentType=WindowsRuntime] | Out-Null;"
            "[Windows.Data.Xml.Dom.XmlDocument,"
            "Windows.Data.Xml.Dom.XmlDocument,"
            "ContentType=WindowsRuntime] | Out-Null;"
            f"$xml = [System.Text.Encoding]::UTF8.GetString("
            f"[Convert]::FromBase64String('{xml_b64}'));"
            "$d = New-Object Windows.Data.Xml.Dom.XmlDocument;"
            "$d.LoadXml($xml);"
            "$t = New-Object Windows.UI.Notifications.ToastNotification $d;"
            "[Windows.UI.Notifications.ToastNotificationManager]"
            f"::CreateToastNotifier('{TOAST_AUMID}').Show($t);"
        )
        result = subprocess.run(
            ['powershell', '-NoProfile', '-WindowStyle', 'Hidden',
             '-Command', ps],
            creationflags=subprocess.CREATE_NO_WINDOW,
            capture_output=True, text=True, timeout=5)
        if result.returncode != 0 or result.stderr.strip():
            wlog(f'TOAST  PS exit={result.returncode} '
                 f'stderr={result.stderr.strip()[:200]}')
        else:
            wlog(f'TOAST  delivered: {title!r}')
    except Exception as e:
        wlog(f'TOAST  show_toast failed: {e}')


# ─── API ─────────────────────────────────────────────
API_URL  = 'https://claude.ai/api/organizations/{}/usage'

# Claude Code keeps a subscription OAuth token on disk (cc_accounts.cred_file)
# and refreshes it every time the CLI runs. An account marked cc_linked reads
# that token instead of a pasted session key, so it never needs renewing by
# hand. The path is asked of cc_accounts every time, never copied here: a
# second definition is one a test can redirect and the other not.
CC_USAGE_URL = 'https://api.anthropic.com/api/oauth/usage'
# Who the token belongs to. Without this the login is blind: the widget
# would read somebody's numbers with no way to say whose they are, and
# would show them under whatever name the account happens to carry.
CC_PROFILE_URL = 'https://api.anthropic.com/api/oauth/profile'
# How an account chooses which credential to read with.
AUTH_AUTO, AUTH_KEY, AUTH_CC = 'auto', 'key', 'claude_code'

# ─── i18n ───────────────────────────────────────────
LANG = {
    'en': {
        'current_session': 'Current Session',
        'all_models': 'All models (7d)',
        'model_scoped': '{model} only (7d)',
        'not_available': 'not available',
        'not_used': 'not used',
        'soon': 'soon',
        'reset_prefix': 'reset',
        'tip_resize': 'Drag to resize\nDouble-click to switch mode',
        'tip_expand': 'Click to see every bar',
        'tip_collapse': 'Click to go back to the strip',
        'days': ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'],
        'unit_d': 'd', 'unit_h': 'h', 'unit_min': 'min',
        'setup_required': 'Session key required to connect to Claude.ai.',
        'session_expired': 'Session expired. Renew your session key to keep tracking usage.',
        'error': 'error',
        'empty_response': 'Empty response',
        'plan_inactive': 'This account has no active subscription, so Claude reports no limits for it.',
        'no_org': 'No organization found',
        'session_expired_short': 'Session expired',
        'action_setup_now': 'Configure now',
        'action_renew_now': 'Renew session',
        'cc_use_login': 'Use Claude Code login',
        'cc_linking': 'Linking this computer\u2019s Claude Code login.',
        'dlg_retry': 'Try again',
        'dlg_use_key_instead': 'Use a session key',
        'cc_no_creds': 'Claude Code login not found. Run `claude` once and sign in.',
        'cc_no_org': "This account's Claude Code login does not say which organization it belongs to. Link it again from Accounts.",
        'plan_inactive_next': 'Renew it on claude.ai, or remove this account.',
        'plan_inactive_add': 'Renew it on claude.ai, then add the account again.',
        'no_plan_short': 'no plan',
        'cc_elsewhere_switch': 'Claude Code is now signed in as {email}. To read this account, switch Claude Code back to it (Accounts › Switch), or add its session key.',
        'cc_elsewhere_signin': 'Claude Code is now signed in as {email}. To read this account, sign in to Claude Code with it again, or add its session key.',
        'cc_token_expired': 'Claude Code login expired. Run `claude` once to refresh it.',
        'cc_rate_limited': 'Claude asked to slow down. Try again in a few minutes.',
        'cc_rate_limited_until': 'Claude asked to slow down. Trying again at {when}.',
        'cc_account_name': 'Claude Code',
        # Toast notifications
        'toast_title': 'Claude Usage',
        'toast_line_pct': 'Session: {pct}% reached at {now}',
        'toast_line_reset': 'Resets at {reset} (in {countdown})',
        'toast_line_no_reset': 'Session limit reached',
        # Menu
        'menu_refresh': 'Refresh',
        'menu_cat_display': 'Display',
        'menu_cat_data': 'Data & alerts',
        'menu_cat_general': 'General',
        'menu_mode_normal': 'Normal mode',
        'menu_mode_essential': 'Essential mode',
        'menu_open_config': 'Open config.json',
        'menu_open_claude': 'Go to Claude Usage',
        'menu_accounts': 'Accounts\u2026',
        'dlg_accounts_title': 'Accounts',
        'dlg_add_account': 'Add account',
        'dlg_bar_color': 'Bar colour',
        'dlg_presets': 'Presets',
        'dlg_account_name': 'Account name',
        'dlg_rename': 'Rename',
        'dlg_remove': 'Remove',
        'dlg_remove_confirm': 'Remove this account? The widget keeps its other accounts.',
        'dlg_update_key': 'Update session key',
        'dlg_no_accounts': 'No accounts yet.',
        'dlg_active': 'Active',
        'dlg_save': 'Save',
        'acct_source': 'data source',
        'acct_cc_here': 'in use here',
        'acct_cc_saved': 'saved',
        'acct_cc_dead': 'sign in again',
        'acct_cc_none': 'not saved',
        'acct_cc_reads': 'The widget reads this account through this login.',
        'acct_cc_in_use': 'Claude Code on this computer is signed in as this account.',
        'acct_cc_elsewhere': 'Claude Code is signed in as another account right now.',
        'acct_cc_unknown': 'It is not clear yet which account Claude Code is signed in as.',
        'acct_cc_no_login': 'Claude Code has no login on this computer right now: switching puts this one in use.',
        'acct_cc_how': 'To be able to switch to it, sign in to Claude Code once with this account: the widget saves the login by itself.',
        'acct_cc_how_manual': 'To be able to switch to it, sign in to Claude Code once with this account, then open this page again and press "Save this login".',
        'acct_cc_dead_how': 'Sign in to Claude Code with this account: the widget brings the saved login up to date by itself.',
        'acct_switch_here': 'Switch to this account',
        'acct_save_login': 'Save this login',
        'acct_key_why': 'Lets the widget read this account while Claude Code is signed in as another one.',
        'acct_advanced': 'Advanced options',
        'acct_read_with': 'Read the data with',
        'acct_saved_login': 'Saved login valid until {when}',
        'acct_saved_copy': 'saved {when}',
        'acct_refresh_copy': 'Update the saved login',
        'menu_open_repo': 'Open GitHub repo',
        'menu_notifications_on': 'Notifications: ON',
        'menu_notifications_off': 'Notifications: OFF',
        'menu_taskbar_on': 'Taskbar icon: ON',
        'menu_taskbar_off': 'Taskbar icon: OFF',
        'menu_countdown': 'Countdown',
        'countdown_full': 'Numeric',
        'countdown_dot': 'Dot',
        'menu_essential_bars': 'Bars to show',
        'menu_sync_on': 'Sync time in bar: ON',
        'menu_colors_fixed': 'Bar colours: fixed',
        'menu_colors_dynamic': 'Bar colours: by usage',
        'menu_sync_off': 'Sync time in bar: OFF',
        'tip_countdown_dot': 'A pulsing dot signals when the next refresh is near.',
        'tip_countdown_full': 'Show the exact time left until the limit resets.',
        'tip_sync': 'Show the time of the last refresh next to each bar.',
        'menu_theme': 'Theme',
        'theme_dark': 'Dark',
        'theme_light': 'Light',
        'menu_restart': 'Restart the widget',
        'tip_restart': 'A new theme is built into the interface, so it appears '
                       'when the widget starts again.',
        'menu_reset_label': 'Under the bars',
        'menu_reset_time_on': 'Reset time: ON',
        'menu_reset_time_off': 'Reset time: OFF',
        'menu_reset_left_on': 'Time left: ON',
        'menu_reset_left_off': 'Time left: OFF',
        'tip_reset_time': 'Show the clock time each limit resets at.',
        'tip_reset_left': 'Show how long is left until each limit resets.\nTurn both off for the narrowest strip: hover a bar to read them.',
        'dlg_add_how': 'How do you want to connect it?',
        'dlg_add_cc_detail': 'Use the Claude Code login already on this computer. Nothing to paste, and it renews itself.',
        'dlg_add_key_detail': 'Paste a session key taken from the browser. It lasts about a month.',
        'info_plan': 'Plan',
        'info_org': 'Organisation',
        'info_extra': 'Extra usage',
        'info_on': 'on',
        'info_off': 'off',
        'info_session_reset': 'Session resets',
        'info_week_reset': 'Week resets',
        'pref_auto': 'Automatic',
        'dlg_remove_account': 'Remove account',
        'dlg_last_credential_title': 'Last credential',
        'dlg_last_credential': 'This is the only way this account can be read. Removing it keeps the account and its details, and you can add a credential again later.',
        'auth_claude_code': 'Claude Code login',
        'auth_key': 'Session key',
        'cc_here': 'Claude Code on this computer: {email}',
        'cc_here_checking': 'Checking the Claude Code login...',
        'cc_here_none': 'No Claude Code login on this computer',
        'cc_here_unknown': 'Claude Code on this computer: account not identified yet',
        'cc_here_unlisted': 'Claude Code on this computer: an account not in this list',
        'cc_here_renews': '(it renews its login at the next command)',
        'cc_link_failed': 'The account was not added.',
        'cc_other_account': 'That login belongs to {email}',
        'cc_state_absent': 'Not found on this computer',
        'cc_link': 'Link',
        'cc_unlink': 'Unlink',
        'key_add': 'Add a key',
        'key_remove': 'Remove',
        'key_state_present': 'Saved',
        'key_state_absent': 'Not set',
        'sub_active': 'Active',
        'sub_canceled': 'Cancelled',
        'sub_past_due': 'Payment overdue',
        'dlg_avatar_title': 'Account avatar',
        'avatar_kind_initials': 'Initials',
        'avatar_kind_text': 'Text',
        'avatar_kind_icon': 'Icon',
        'avatar_text_hint': 'Up to three characters.',
        'avatar_bg': 'Background',
        'avatar_fg': 'Symbol',
        'dlg_custom': 'Custom\u2026',
        'dlg_account_details': 'Account',
        'dlg_account_unknown': 'Identity not known yet',
        'dlg_account_no_method': 'No credential',
        'dlg_account_manage': 'Manage',
        'dlg_cc_here': 'Claude Code',
        'dlg_account_exists_title': 'Account already added',
        'dlg_update': 'Update',
        'dlg_login_other_account': 'Claude Code is signed in as another account, not this one. Sign in to Claude Code as this account first, then try again.',
        'dlg_account_exists': 'This is the same account as "{name}". Update it instead of adding a second one?',
        'tip_colors': 'Fixed: each bar keeps its own colour. By usage: every bar is coloured by its consumption (blue, amber, red).',
        'menu_split': 'Weekly split',
        'split_none': 'none',
        'split_parts': '{n} parts',
        'split_intro': 'Split the weekly bar into equal parts.',
        'split_remove': 'Remove split',
        'tip_split': 'Splits the weekly bar into equal parts.',
        'tip_notifications': 'Windows notification when session usage crosses a threshold.',
        'tip_taskbar': 'Show a taskbar button with a usage progress overlay.',
        'menu_refresh_interval': 'Refresh interval\u2026',
        'dlg_interval_title': 'Refresh interval',
        'dlg_interval_label': 'Interval in seconds (minimum 10):',
        'dlg_interval_invalid': 'Enter a number between 10 and 3600',
        'menu_quit': 'Quit',
        'menu_language': 'Language',
        'menu_check_updates': 'Check for updates\u2026',
        # Update flow
        'update_banner_available': 'Update available: v{version}',
        'update_banner_update': 'Update',
        'update_banner_later': 'Later',
        'update_banner_skip': 'Skip',
        'update_dlg_title': 'Update available',
        'update_dlg_subtitle': 'Version {version} is available. You\u2019re currently running {current}.',
        'update_dlg_changelog': "What\u2019s new",
        'update_dlg_install': 'Install now',
        'update_dlg_cancel': 'Cancel',
        'update_dlg_no_changelog': 'No release notes provided.',
        'update_dlg_downloading': 'Downloading {percent}%  ({done} / {total})',
        'update_dlg_launching': 'Updating\u2026',
        'update_dlg_failed': 'Update failed: {error}',
        'update_dlg_open_page': 'View on GitHub',
        'update_check_uptodate': 'You\u2019re already on the latest version (v{version}).',
        'update_check_failed': 'Could not reach GitHub. Try again later.',
        'update_check_no_asset': 'New version available but the installer is missing from the release.',
        # Dialog
        'dlg_renew_title': 'Renew session',
        'dlg_setup_title': 'Welcome',
        'dlg_welcome_hint': 'Connect the widget to your Claude.ai account.',
        'dlg_step_guide': 'Where do I find my session key?',
        'dlg_step_paste': 'Paste your session key below',
        'dlg_open_guide': 'Open guide in browser',
        'dlg_paste_empty': 'Paste the session key in the field above.',
        'dlg_invalid_prefix': 'The value must start with sk-ant-',
        'dlg_verifying': 'Verifying\u2026',
        'dlg_error_prefix': 'Error',
        'dlg_renew_gone': 'This account was removed while the window was open: the key was not saved.',
        'dlg_connect': 'Connect',
        'dlg_cancel': 'Cancel',
        'key_invalid_or_expired': 'Invalid or expired session key',
        'net_err_revocation': 'Your network is blocking the certificate revocation check. A VPN, firewall or security suite is the usual cause.',
        'net_err_untrusted': 'The certificate could not be trusted. A proxy or antivirus inspecting HTTPS traffic is the usual cause.',
        'net_err_unreachable': 'Could not reach claude.ai. Check your internet connection.',
        'net_err_timeout': 'The connection to claude.ai timed out.',
        'net_err_reset': 'The connection to claude.ai was cut. Security software or a proxy along the way is the usual cause.',
        'net_err_tls': 'The secure connection to claude.ai failed.',
        'net_err_generic': 'The request to claude.ai failed.',
        'menu_selftest': 'Connection self-test\u2026',
        'selftest_title': 'Connection self-test',
        'selftest_hint': 'Checks how this PC reaches claude.ai. If a line fails, copy the report into a bug report.',
        'selftest_running': 'Running the checks\u2026',
        'selftest_copy': 'Copy report',
        'selftest_copied': 'Report copied to the clipboard.',
        'selftest_close': 'Close',
        'selftest_rerun': 'Run again',
        'selftest_summary_ok': 'Everything works: the widget can reach claude.ai.',
        'selftest_summary_warn': 'The connection works, but something on this PC could interfere.',
        'selftest_summary_fail': 'A check failed. The first failing line is the one to act on.',
        'selftest_summary_partial': 'The connection works. No account is configured, so the usage endpoint was not checked.',
        'selftest_dns_failed': 'claude.ai could not be resolved. Either this PC is offline, or a DNS that filters (Pi-hole, a company resolver, parental controls) is intercepting the name.',
        'selftest_step_curl': 'curl and TLS backend',
        'selftest_step_dns': 'Name resolution (claude.ai)',
        'selftest_step_tls': 'TLS handshake',
        'selftest_step_revoke': 'TLS handshake without revocation check',
        'selftest_step_proxy': 'Proxy configuration',
        'selftest_step_curlrc': 'curl configuration file',
        'selftest_step_api': 'Usage API',
        'selftest_curl_missing': 'curl not found. It ships with Windows 10 1803 and later.',
        'selftest_curl_backend': 'This curl does not use the Windows certificate store, so it can reject certificates Windows trusts.',
        'selftest_tls_ok': 'Certificate accepted',
        'selftest_revoke_not_needed': 'Not needed: the certificate check already passed.',
        'selftest_revoke_worked': 'Works without the revocation check, so the certificate is valid and the network is blocking the revocation lookup. The widget skips that lookup on its own.',
        'selftest_revoke_inconsistent': 'The handshake succeeded on the second attempt, so the revocation check was not the problem. It looks like a one-off failure.',
        'selftest_proxy_none': 'No proxy configured',
        'selftest_proxy_found': 'Proxy configured',
        'selftest_curlrc_none': 'None found',
        'selftest_curlrc_found': 'Found',
        'selftest_api_no_account': 'No account configured yet',
        'selftest_api_key_rejected': 'Session key rejected: renew it from the Accounts dialog.',
        'selftest_api_bad_body': 'Unexpected response from claude.ai',
        'selftest_api_ok': 'Usage data received',
        'dlg_pick_org_title': 'Choose organization',
        'dlg_pick_org_hint': 'This login belongs to more than one Claude organization, and each one is an account of its own. Choose the one to track; to track another as well, add the account again and choose it.',
        'dlg_pick_org_use': 'Use this org',
        'dlg_pick_org_added': 'already in the list',
        'reset_title': 'Limit resets',
        'reset_checking': 'Checking for resets\u2026',
        'reset_none': 'None available now. Anthropic grants them now and then, and they show up here.',
        'reset_grant_left': '{left} of {total} left \u00b7 until {when}',
        'reset_full_what': 'A reset clears the 5-hour and weekly limits together.',
        'reset_use': 'Use reset',
        'reset_needs_limit': 'It can be used once you reach a limit.',
        'reset_unusable': 'It cannot be used right now.',
        'reset_available': 'available',
        'reset_session_ready': 'A reset of the 5-hour limit is available ({n} a week).',
        'reset_session_next': 'The 5-hour reset can be used again from {when}.',
        'reset_session_use': 'Reset 5-hour limit',
        'reset_confirm_title': 'Use the reset?',
        'reset_confirm_full': 'The 5-hour and weekly usage of {name} goes back to zero now. This cannot be undone, and the week still restarts at its usual time ({week}).',
        'reset_confirm_session': 'The 5-hour usage of {name} goes back to zero now. The weekly limit still applies.',
        'reset_using': 'Using the reset\u2026',
        'reset_done': 'Done: the limits are reset.',
        'reset_failed_check': 'Could not check for resets: {why}',
        'reset_r_already_used': 'That reset was already used: nothing changed.',
        'reset_r_not_limited': 'Nothing was used: this reset only works at a limit, and you are not at one.',
        'reset_r_cooldown': 'Not available yet: try again later.',
        'reset_r_ineligible': 'This account cannot use the reset.',
        'reset_r_unavailable': 'The reset is not available any more: nothing was used.',
        'reset_r_rate_limited': 'Too many requests: try again in a minute.',
        'reset_r_auth': 'The session key was refused: update it, then try again.',
        'reset_r_no_answer': 'No answer from claude.ai. Check Settings > Usage on claude.ai before trying again.',
        'reset_r_changed': 'Nothing was used: the account changed in the meantime.',
        # Kept for legacy references - consolidated into dlg_step_* above
    },
    'it': {
        'current_session': 'Sessione Corrente',
        'all_models': 'Tutti i modelli (7gg)',
        'model_scoped': 'Solo {model} (7gg)',
        'not_available': 'non disponibile',
        'not_used': 'non utilizzato',
        'soon': 'tra poco',
        'reset_prefix': 'reset',
        'tip_resize': 'Trascina per ridimensionare\nDoppio clic per cambiare modalit\u00e0',
        'tip_expand': 'Clic per vedere tutte le barre',
        'tip_collapse': 'Clic per tornare alla striscia',
        'days': ['lun', 'mar', 'mer', 'gio', 'ven', 'sab', 'dom'],
        'unit_d': 'gg', 'unit_h': 'h', 'unit_min': 'min',
        'setup_required': 'Chiave di sessione necessaria per connettersi a Claude.ai.',
        'session_expired': 'Sessione scaduta. Rinnova la chiave di sessione per continuare.',
        'error': 'errore',
        'empty_response': 'Risposta vuota',
        'plan_inactive': 'Questo account non ha un abbonamento attivo, quindi Claude non riporta limiti.',
        'no_org': 'Nessuna organizzazione trovata',
        'session_expired_short': 'Sessione scaduta',
        'action_setup_now': 'Configura ora',
        'action_renew_now': 'Rinnova sessione',
        'toast_title': 'Claude Usage',
        'toast_line_pct': 'Sessione: {pct}% raggiunto alle {now}',
        'toast_line_reset': 'Reset alle {reset} (tra {countdown})',
        'toast_line_no_reset': 'Limite sessione raggiunto',
        'menu_refresh': 'Aggiorna',
        'menu_cat_display': 'Visualizzazione',
        'menu_cat_data': 'Dati e avvisi',
        'menu_cat_general': 'Generale',
        'menu_mode_normal': 'Modalit\u00e0 normale',
        'menu_mode_essential': 'Modalit\u00e0 essential',
        'menu_open_config': 'Apri config.json',
        'menu_open_claude': 'Vai a Claude Usage',
        'menu_accounts': 'Account\u2026',
        'dlg_accounts_title': 'Account',
        'dlg_add_account': 'Aggiungi account',
        'dlg_bar_color': 'Colore barra',
        'dlg_presets': 'Preset',
        'dlg_account_name': 'Nome account',
        'dlg_rename': 'Rinomina',
        'dlg_remove': 'Rimuovi',
        'dlg_remove_confirm': 'Rimuovere questo account? Gli altri account restano.',
        'dlg_update_key': 'Aggiorna chiave di sessione',
        'dlg_no_accounts': 'Nessun account.',
        'dlg_active': 'Attivo',
        'dlg_save': 'Salva',
        'acct_source': 'fonte dei dati',
        'acct_cc_here': 'in uso qui',
        'acct_cc_saved': 'salvato',
        'acct_cc_dead': 'accedi di nuovo',
        'acct_cc_none': 'non salvato',
        'acct_cc_reads': 'Il widget legge questo account con questo login.',
        'acct_cc_in_use': 'Claude Code su questo computer è autenticato con questo account.',
        'acct_cc_elsewhere': 'Claude Code adesso è autenticato con un altro account.',
        'acct_cc_unknown': 'Non è ancora chiaro con quale account è autenticato Claude Code.',
        'acct_cc_no_login': 'Claude Code non ha un accesso su questo computer in questo momento: passando, usa questo.',
        'acct_cc_how': 'Per poterci passare, accedi una volta a Claude Code con questo account: il widget salva l’accesso da solo.',
        'acct_cc_how_manual': 'Per poterci passare, accedi una volta a Claude Code con questo account, poi riapri questa pagina e premi "Salva questo accesso".',
        'acct_cc_dead_how': 'Accedi a Claude Code con questo account: il widget rimette in pari da solo l’accesso salvato.',
        'acct_switch_here': 'Passa a questo account',
        'acct_save_login': 'Salva questo accesso',
        'acct_key_why': 'Permette di leggere questo account mentre Claude Code è autenticato con un altro.',
        'acct_advanced': 'Opzioni avanzate',
        'acct_read_with': 'Leggi i dati con',
        'acct_saved_login': 'Accesso salvato valido fino al {when}',
        'acct_saved_copy': 'salvato il {when}',
        'acct_refresh_copy': 'Aggiorna l’accesso salvato',
        'menu_open_repo': 'Apri repo GitHub',
        'menu_notifications_on': 'Notifiche: attive',
        'menu_notifications_off': 'Notifiche: disattive',
        'menu_taskbar_on': 'Icona taskbar: visibile',
        'menu_taskbar_off': 'Icona taskbar: nascosta',
        'menu_countdown': 'Conto alla rovescia',
        'countdown_full': 'Numerico',
        'countdown_dot': 'Puntino',
        'menu_essential_bars': 'Barre da mostrare',
        'menu_sync_on': 'Orario sync nella barra: attivo',
        'menu_colors_fixed': 'Colori barre: fissi',
        'menu_colors_dynamic': 'Colori barre: per consumo',
        'menu_sync_off': 'Orario sync nella barra: disattivo',
        'tip_countdown_dot': 'Un puntino che pulsa segnala quando manca poco al prossimo aggiornamento.',
        'tip_countdown_full': 'Mostra il tempo esatto che manca al reset del limite.',
        'tip_sync': 'Mostra accanto a ogni barra quando \u00e8 stato fatto l\u2019ultimo aggiornamento.',
        'menu_theme': 'Tema',
        'theme_dark': 'Scuro',
        'theme_light': 'Chiaro',
        'menu_restart': 'Riavvia il widget',
        'tip_restart': 'Il tema fa parte di come viene costruita l\u2019interfaccia, '
                       'quindi si vede al riavvio del widget.',
        'menu_reset_label': 'Sotto le barre',
        'menu_reset_time_on': 'Orario di reset: visibile',
        'menu_reset_time_off': 'Orario di reset: nascosto',
        'menu_reset_left_on': 'Tempo rimanente: visibile',
        'menu_reset_left_off': 'Tempo rimanente: nascosto',
        'tip_reset_time': 'Mostra a che ora si azzera ogni limite.',
        'tip_reset_left': 'Mostra quanto manca all\u2019azzeramento di ogni limite.\nSpegnili entrambi per la striscia pi\u00f9 stretta: passa il mouse su una barra per leggerli.',
        'dlg_add_how': 'Come vuoi collegarlo?',
        'dlg_add_cc_detail': 'Usa il login di Claude Code gi\u00e0 presente su questo computer. Niente da incollare, e si rinnova da solo.',
        'dlg_add_key_detail': 'Incolla una chiave di sessione presa dal browser. Dura circa un mese.',
        'info_plan': 'Piano',
        'info_org': 'Organizzazione',
        'info_extra': 'Crediti extra',
        'info_on': 'attivi',
        'info_off': 'non attivi',
        'info_session_reset': 'La sessione si azzera',
        'info_week_reset': 'La settimana si azzera',
        'pref_auto': 'Automatico',
        'dlg_remove_account': 'Rimuovi account',
        'dlg_last_credential_title': 'Ultima credenziale',
        'dlg_last_credential': '\u00c8 l\u2019unico modo in cui questo account pu\u00f2 essere letto. Rimuovendolo, l\u2019account e le sue informazioni restano, e potrai aggiungere una credenziale pi\u00f9 avanti.',
        'cc_account_name': 'Claude Code',
        'cc_token_expired': 'Login di Claude Code scaduto. Esegui `claude` una volta per rinnovarlo.',
        'cc_no_creds': 'Login di Claude Code non trovato. Esegui `claude` una volta e accedi.',
        'cc_no_org': 'Il login di Claude Code di questo account non dice a quale organizzazione appartiene. Collegalo di nuovo da Account.',
        'plan_inactive_next': 'Rinnovalo su claude.ai, oppure rimuovi questo account.',
        'plan_inactive_add': 'Rinnovalo su claude.ai, poi aggiungi di nuovo l’account.',
        'no_plan_short': 'nessun piano',
        'cc_elsewhere_switch': 'Claude Code adesso è autenticato come {email}. Per leggere questo account, fai tornare Claude Code qui (Account › Passa) oppure aggiungi la sua chiave di sessione.',
        'cc_elsewhere_signin': 'Claude Code adesso è autenticato come {email}. Per leggere questo account, accedi di nuovo a Claude Code con questo account oppure aggiungi la sua chiave di sessione.',
        'cc_rate_limited': 'Claude ha chiesto di rallentare. Riprova fra qualche minuto.',
        'cc_rate_limited_until': 'Claude ha chiesto di rallentare. Riprovo alle {when}.',
        'cc_use_login': 'Usa il login di Claude Code',
        'cc_linking': 'Collego il login di Claude Code di questo computer.',
        'dlg_retry': 'Riprova',
        'dlg_use_key_instead': 'Usa una chiave di sessione',
        'auth_claude_code': 'Login di Claude Code',
        'auth_key': 'Chiave di sessione',
        'cc_here': 'Claude Code su questo computer: {email}',
        'cc_here_checking': 'Controllo del login di Claude Code...',
        'cc_here_none': 'Nessun login di Claude Code su questo computer',
        'cc_here_unknown': 'Claude Code su questo computer: account non ancora identificato',
        'cc_here_unlisted': 'Claude Code su questo computer: un account che non è in questo elenco',
        'cc_here_renews': '(rinnova il login al prossimo comando)',
        'cc_link_failed': 'L’account non è stato aggiunto.',
        'cc_other_account': 'Quel login appartiene a {email}',
        'cc_state_absent': 'Non presente su questo computer',
        'cc_link': 'Collega',
        'cc_unlink': 'Scollega',
        'key_add': 'Aggiungi una chiave',
        'key_remove': 'Rimuovi',
        'key_state_present': 'Salvata',
        'key_state_absent': 'Non impostata',
        'sub_active': 'Attivo',
        'sub_canceled': 'Disdetto',
        'sub_past_due': 'Pagamento in ritardo',
        'dlg_avatar_title': 'Aspetto dell\u2019account',
        'avatar_kind_initials': 'Iniziali',
        'avatar_kind_text': 'Testo',
        'avatar_kind_icon': 'Icona',
        'avatar_text_hint': 'Fino a tre caratteri.',
        'avatar_bg': 'Sfondo',
        'avatar_fg': 'Simbolo',
        'dlg_custom': 'Personalizza\u2026',
        'dlg_account_details': 'Account',
        'dlg_account_unknown': 'Identit\u00e0 non ancora nota',
        'dlg_account_no_method': 'Nessuna credenziale',
        'dlg_account_manage': 'Gestisci',
        'dlg_cc_here': 'Claude Code',
        'dlg_account_exists_title': 'Account gi\u00e0 presente',
        'dlg_update': 'Aggiorna',
        'dlg_login_other_account': 'Claude Code è entrato con un altro account, non con questo. Prima fai entrare Claude Code con questo account, poi riprova.',
        'dlg_account_exists': '\u00c8 lo stesso account di "{name}". Vuoi aggiornarlo invece di aggiungerne un altro?',
        'tip_colors': 'Fissi: ogni barra tiene il suo colore. Per consumo: ogni barra \u00e8 colorata in base al consumo (blu, giallo, rosso).',
        'menu_split': 'Suddivisione settimanale',
        'split_none': 'nessuna',
        'split_parts': '{n} parti',
        'split_intro': 'Dividi la barra settimanale in parti uguali.',
        'split_remove': 'Rimuovi suddivisione',
        'tip_split': 'Divide la barra settimanale in parti uguali.',
        'tip_notifications': 'Notifica di Windows quando il consumo della sessione supera una soglia.',
        'tip_taskbar': 'Mostra un pulsante nella taskbar con la barra di avanzamento del consumo.',
        'menu_refresh_interval': 'Intervallo aggiornamento\u2026',
        'dlg_interval_title': 'Intervallo aggiornamento',
        'dlg_interval_label': 'Intervallo in secondi (minimo 10):',
        'dlg_interval_invalid': 'Inserisci un numero tra 10 e 3600',
        'menu_quit': 'Chiudi',
        'menu_language': 'Lingua',
        'menu_check_updates': 'Controlla aggiornamenti\u2026',
        'update_banner_available': 'Aggiornamento disponibile: v{version}',
        'update_banner_update': 'Aggiorna',
        'update_banner_later': 'Dopo',
        'update_banner_skip': 'Ignora',
        'update_dlg_title': 'Aggiornamento disponibile',
        'update_dlg_subtitle': '\u00c8 disponibile la versione {version}. Attualmente stai usando la {current}.',
        'update_dlg_changelog': 'Cosa cambia',
        'update_dlg_install': 'Installa ora',
        'update_dlg_cancel': 'Annulla',
        'update_dlg_no_changelog': 'Nessuna nota di rilascio disponibile.',
        'update_dlg_downloading': 'Download {percent}%  ({done} / {total})',
        'update_dlg_launching': 'Aggiornamento in corso\u2026',
        'update_dlg_failed': 'Aggiornamento fallito: {error}',
        'update_dlg_open_page': 'Vedi su GitHub',
        'update_check_uptodate': 'Stai gi\u00e0 usando la versione pi\u00f9 recente (v{version}).',
        'update_check_failed': 'Impossibile contattare GitHub. Riprova pi\u00f9 tardi.',
        'update_check_no_asset': 'Nuova versione disponibile ma l\u2019installer non \u00e8 stato trovato nella release.',
        'dlg_renew_title': 'Rinnova sessione',
        'dlg_setup_title': 'Benvenuto',
        'dlg_welcome_hint': 'Collega il widget al tuo account Claude.ai.',
        'dlg_step_guide': 'Dove trovo la chiave di sessione?',
        'dlg_step_paste': 'Incolla la chiave di sessione qui sotto',
        'dlg_open_guide': 'Apri guida nel browser',
        'dlg_paste_empty': 'Incolla la chiave di sessione nel campo sopra.',
        'dlg_invalid_prefix': 'Il valore deve iniziare con sk-ant-',
        'dlg_verifying': 'Verifica in corso\u2026',
        'dlg_error_prefix': 'Errore',
        'dlg_renew_gone': 'Questo account \u00e8 stato rimosso mentre la finestra era aperta: la chiave non \u00e8 stata salvata.',
        'dlg_connect': 'Connetti',
        'dlg_cancel': 'Annulla',
        'key_invalid_or_expired': 'Chiave di sessione non valida o scaduta',
        'net_err_revocation': 'La rete impedisce di verificare la revoca del certificato. Di solito la causa \u00e8 una VPN, un firewall o un antivirus.',
        'net_err_untrusted': 'Il certificato non risulta attendibile. Di solito la causa \u00e8 un proxy o un antivirus che ispeziona il traffico HTTPS.',
        'net_err_unreachable': 'Impossibile raggiungere claude.ai. Controlla la connessione.',
        'net_err_timeout': 'La connessione a claude.ai \u00e8 scaduta.',
        'net_err_reset': 'La connessione a claude.ai \u00e8 stata interrotta. Di solito la causa \u00e8 un antivirus o un proxy sul percorso.',
        'net_err_tls': 'La connessione sicura a claude.ai non \u00e8 riuscita.',
        'net_err_generic': 'La richiesta a claude.ai non \u00e8 riuscita.',
        'menu_selftest': 'Test di connessione\u2026',
        'selftest_title': 'Test di connessione',
        'selftest_hint': 'Verifica come questo PC raggiunge claude.ai. Se una riga fallisce, copia il report in una segnalazione.',
        'selftest_running': 'Verifiche in corso\u2026',
        'selftest_copy': 'Copia report',
        'selftest_copied': 'Report copiato negli appunti.',
        'selftest_close': 'Chiudi',
        'selftest_rerun': 'Ripeti',
        'selftest_summary_ok': 'Tutto funziona: il widget raggiunge claude.ai.',
        'selftest_summary_warn': 'La connessione funziona, ma qualcosa su questo PC potrebbe interferire.',
        'selftest_summary_fail': 'Una verifica non \u00e8 riuscita. La prima riga fallita \u00e8 quella su cui intervenire.',
        'selftest_summary_partial': 'La connessione funziona. Nessun account configurato, quindi l\u2019endpoint di utilizzo non \u00e8 stato verificato.',
        'selftest_dns_failed': 'claude.ai non \u00e8 stato risolto. O questo PC \u00e8 offline, o un DNS che filtra (Pi-hole, resolver aziendale, parental control) intercetta il nome.',
        'selftest_step_curl': 'curl e backend TLS',
        'selftest_step_dns': 'Risoluzione del nome (claude.ai)',
        'selftest_step_tls': 'Handshake TLS',
        'selftest_step_revoke': 'Handshake TLS senza controllo di revoca',
        'selftest_step_proxy': 'Configurazione proxy',
        'selftest_step_curlrc': 'File di configurazione di curl',
        'selftest_step_api': 'API di utilizzo',
        'selftest_curl_missing': 'curl non trovato. \u00c8 incluso in Windows 10 1803 e successivi.',
        'selftest_curl_backend': 'Questo curl non usa l\u2019archivio certificati di Windows, quindi pu\u00f2 rifiutare certificati che Windows considera validi.',
        'selftest_tls_ok': 'Certificato accettato',
        'selftest_revoke_not_needed': 'Non necessario: il controllo del certificato \u00e8 gi\u00e0 riuscito.',
        'selftest_revoke_worked': 'Funziona senza il controllo di revoca: il certificato \u00e8 valido ed \u00e8 la rete a bloccarne la verifica. Il widget salta quel controllo da solo.',
        'selftest_revoke_inconsistent': 'L\u2019handshake \u00e8 riuscito al secondo tentativo, quindi il controllo di revoca non era il problema. Sembra un errore isolato.',
        'selftest_proxy_none': 'Nessun proxy configurato',
        'selftest_proxy_found': 'Proxy configurato',
        'selftest_curlrc_none': 'Nessuno trovato',
        'selftest_curlrc_found': 'Trovato',
        'selftest_api_no_account': 'Nessun account configurato',
        'selftest_api_key_rejected': 'Chiave di sessione rifiutata: rinnovala dalla finestra Account.',
        'selftest_api_bad_body': 'Risposta inattesa da claude.ai',
        'selftest_api_ok': 'Dati di utilizzo ricevuti',
        'dlg_pick_org_title': 'Scegli organizzazione',
        'dlg_pick_org_hint': 'Questo login fa parte di pi\u00f9 organizzazioni Claude, e ognuna \u00e8 un account a s\u00e9. Scegli quella da monitorare; per seguirne anche un\u2019altra, aggiungi di nuovo l\u2019account e sceglila.',
        'dlg_pick_org_use': 'Usa questa',
        'dlg_pick_org_added': 'gi\u00e0 in elenco',
        'reset_title': 'Reset dei limiti',
        'reset_checking': 'Controllo dei reset\u2026',
        'reset_none': 'Nessuno disponibile adesso. Anthropic li concede di tanto in tanto, e compaiono qui.',
        'reset_grant_left': 'Disponibili: {left} su {total} \u00b7 fino al {when}',
        'reset_full_what': 'Un reset azzera insieme il limite di 5 ore e quello settimanale.',
        'reset_use': 'Usa il reset',
        'reset_needs_limit': 'Si pu\u00f2 usare quando raggiungi un limite.',
        'reset_unusable': 'Non si pu\u00f2 usare in questo momento.',
        'reset_available': 'disponibile',
        'reset_session_ready': '\u00c8 disponibile un reset del limite di 5 ore ({n} a settimana).',
        'reset_session_next': 'Il reset del limite di 5 ore torna disponibile dal {when}.',
        'reset_session_use': 'Azzera il limite di 5 ore',
        'reset_confirm_title': 'Usare il reset?',
        'reset_confirm_full': 'L\u2019uso di 5 ore e settimanale di {name} torna a zero adesso. Non si pu\u00f2 annullare, e la settimana riparte comunque alla solita ora ({week}).',
        'reset_confirm_session': 'L\u2019uso di 5 ore di {name} torna a zero adesso. Il limite settimanale resta.',
        'reset_using': 'Uso il reset\u2026',
        'reset_done': 'Fatto: i limiti sono azzerati.',
        'reset_failed_check': 'Non riesco a controllare i reset: {why}',
        'reset_r_already_used': 'Quel reset era gi\u00e0 stato usato: non \u00e8 cambiato niente.',
        'reset_r_not_limited': 'Non \u00e8 stato usato niente: questo reset funziona solo al limite, e non ci sei.',
        'reset_r_cooldown': 'Non ancora disponibile: riprova pi\u00f9 tardi.',
        'reset_r_ineligible': 'Questo account non pu\u00f2 usare il reset.',
        'reset_r_unavailable': 'Il reset non \u00e8 pi\u00f9 disponibile: non \u00e8 stato usato niente.',
        'reset_r_rate_limited': 'Troppe richieste: riprova fra un minuto.',
        'reset_r_auth': 'La chiave di sessione \u00e8 stata rifiutata: aggiornala e riprova.',
        'reset_r_no_answer': 'Nessuna risposta da claude.ai. Controlla Impostazioni > Utilizzo su claude.ai prima di riprovare.',
        'reset_r_changed': 'Non \u00e8 stato usato niente: nel frattempo l\u2019account \u00e8 cambiato.',
    },
    'ja': {
        'current_session': '\u73fe\u5728\u306e\u30bb\u30c3\u30b7\u30e7\u30f3',
        'all_models': '\u5168\u30e2\u30c7\u30eb (7\u65e5)',
        'model_scoped': '{model}\u306e\u307f (7\u65e5)',
        'not_available': '\u5229\u7528\u4e0d\u53ef',
        'not_used': '\u672a\u4f7f\u7528',
        'soon': '\u307e\u3082\u306a\u304f',
        'reset_prefix': '\u30ea\u30bb\u30c3\u30c8',
        'tip_resize': '\u30c9\u30e9\u30c3\u30b0\u3067\u30b5\u30a4\u30ba\u5909\u66f4\n\u30c0\u30d6\u30eb\u30af\u30ea\u30c3\u30af\u3067\u8868\u793a\u30e2\u30fc\u30c9\u5207\u66ff',
        'tip_expand': '\u30af\u30ea\u30c3\u30af\u3067\u5168\u30d0\u30fc\u3092\u8868\u793a',
        'tip_collapse': '\u30af\u30ea\u30c3\u30af\u3067\u5143\u306e\u8868\u793a\u306b\u623b\u3059',
        'days': ['\u6708', '\u706b', '\u6c34', '\u6728', '\u91d1', '\u571f', '\u65e5'],
        # Latin unit markers rather than the kanji forms: those are full-width
        # and push the reset label past the end of the bar, where it clips.
        'unit_d': 'd', 'unit_h': 'h', 'unit_min': 'm',
        'setup_required': 'Claude.ai \u306b\u63a5\u7d9a\u3059\u308b\u306b\u306f\u30bb\u30c3\u30b7\u30e7\u30f3\u30ad\u30fc\u304c\u5fc5\u8981\u3067\u3059\u3002',
        'session_expired': '\u30bb\u30c3\u30b7\u30e7\u30f3\u6709\u52b9\u671f\u5207\u308c\u3002\u30bb\u30c3\u30b7\u30e7\u30f3\u30ad\u30fc\u3092\u66f4\u65b0\u3057\u3066\u304f\u3060\u3055\u3044\u3002',
        'error': '\u30a8\u30e9\u30fc',
        'empty_response': '\u5fdc\u7b54\u304c\u7a7a\u3067\u3059',
        'plan_inactive': '\u3053\u306e\u30a2\u30ab\u30a6\u30f3\u30c8\u306b\u306f\u6709\u52b9\u306a\u30b5\u30d6\u30b9\u30af\u30ea\u30d7\u30b7\u30e7\u30f3\u304c\u306a\u3044\u305f\u3081\u3001\u4e0a\u9650\u306f\u8868\u793a\u3055\u308c\u307e\u305b\u3093\u3002',
        'no_org': '\u7d44\u7e54\u304c\u898b\u3064\u304b\u308a\u307e\u305b\u3093',
        'session_expired_short': '\u30bb\u30c3\u30b7\u30e7\u30f3\u6709\u52b9\u671f\u5207\u308c',
        'action_setup_now': '\u4eca\u3059\u3050\u8a2d\u5b9a',
        'action_renew_now': '\u30bb\u30c3\u30b7\u30e7\u30f3\u66f4\u65b0',
        'toast_title': 'Claude Usage',
        'toast_line_pct': '\u30bb\u30c3\u30b7\u30e7\u30f3: {now}\u306b{pct}%\u306b\u9054\u3057\u307e\u3057\u305f',
        'toast_line_reset': '\u30ea\u30bb\u30c3\u30c8 {reset} (\u3042\u3068 {countdown})',
        'toast_line_no_reset': '\u30bb\u30c3\u30b7\u30e7\u30f3\u4e0a\u9650\u306b\u5230\u9054',
        'menu_refresh': '\u66f4\u65b0',
        'menu_cat_display': '\u8868\u793a',
        'menu_cat_data': '\u30c7\u30fc\u30bf\u3068\u901a\u77e5',
        'menu_cat_general': '\u4e00\u822c',
        'menu_mode_normal': '\u901a\u5e38\u30e2\u30fc\u30c9',
        'menu_mode_essential': '\u30b7\u30f3\u30d7\u30eb\u30e2\u30fc\u30c9',
        'menu_open_config': 'config.json\u3092\u958b\u304f',
        'menu_open_claude': 'Claude Usage\u306b\u79fb\u52d5',
        'menu_accounts': '\u30a2\u30ab\u30a6\u30f3\u30c8\u2026',
        'dlg_accounts_title': '\u30a2\u30ab\u30a6\u30f3\u30c8',
        'dlg_add_account': '\u30a2\u30ab\u30a6\u30f3\u30c8\u3092\u8ffd\u52a0',
        'dlg_bar_color': '\u30d0\u30fc\u306e\u8272',
        'dlg_presets': '\u30d7\u30ea\u30bb\u30c3\u30c8',
        'dlg_account_name': '\u30a2\u30ab\u30a6\u30f3\u30c8\u540d',
        'dlg_rename': '\u540d\u524d\u3092\u5909\u66f4',
        'dlg_remove': '\u524a\u9664',
        'dlg_remove_confirm': '\u3053\u306e\u30a2\u30ab\u30a6\u30f3\u30c8\u3092\u524a\u9664\u3057\u307e\u3059\u304b\uff1f\u4ed6\u306e\u30a2\u30ab\u30a6\u30f3\u30c8\u306f\u6b8b\u308a\u307e\u3059\u3002',
        'dlg_update_key': '\u30bb\u30c3\u30b7\u30e7\u30f3\u30ad\u30fc\u3092\u66f4\u65b0',
        'dlg_no_accounts': '\u30a2\u30ab\u30a6\u30f3\u30c8\u304c\u3042\u308a\u307e\u305b\u3093\u3002',
        'dlg_active': '\u4f7f\u7528\u4e2d',
        'dlg_save': '\u4fdd\u5b58',
        'acct_source': 'データ元',
        'acct_cc_here': 'このPCで使用中',
        'acct_cc_saved': '保存済み',
        'acct_cc_dead': '再サインインが必要',
        'acct_cc_none': '未保存',
        'acct_cc_reads': 'ウィジェットはこのログインでこのアカウントを読み取ります。',
        'acct_cc_in_use': 'このコンピューターの Claude Code はこのアカウントでサインインしています。',
        'acct_cc_elsewhere': 'Claude Code は現在、別のアカウントでサインインしています。',
        'acct_cc_unknown': 'Claude Code がどのアカウントでサインインしているか、まだ確認できていません。',
        'acct_cc_no_login': 'このPCの Claude Code は今ログインしていません。切り替えるとこのログインが使われます。',
        'acct_cc_how': '切り替えられるようにするには、このアカウントで Claude Code に一度サインインしてください。ログインはウィジェットが自動で保存します。',
        'acct_cc_how_manual': '切り替えられるようにするには、このアカウントで Claude Code に一度サインインしてから、このページを開き直して「このログインを保存」を押してください。',
        'acct_cc_dead_how': 'このアカウントで Claude Code にサインインしてください。保存済みのログインはウィジェットが自動で更新します。',
        'acct_switch_here': 'このアカウントに切り替え',
        'acct_save_login': 'このログインを保存',
        'acct_key_why': 'Claude Code が別のアカウントでサインインしている間も、このアカウントを読み取れます。',
        'acct_advanced': '詳細設定',
        'acct_read_with': 'データの取得方法',
        'acct_saved_login': '保存済みログインの有効期限: {when}',
        'acct_saved_copy': '{when} に保存',
        'acct_refresh_copy': '保存済みのログインを更新',
        'menu_open_repo': 'GitHub\u30ea\u30dd\u30b8\u30c8\u30ea\u3092\u958b\u304f',
        'menu_notifications_on': '\u901a\u77e5: \u30aa\u30f3',
        'menu_notifications_off': '\u901a\u77e5: \u30aa\u30d5',
        'menu_taskbar_on': '\u30bf\u30b9\u30af\u30d0\u30fc\u30a2\u30a4\u30b3\u30f3: \u8868\u793a',
        'menu_taskbar_off': '\u30bf\u30b9\u30af\u30d0\u30fc\u30a2\u30a4\u30b3\u30f3: \u975e\u8868\u793a',
        'menu_countdown': '\u30ab\u30a6\u30f3\u30c8\u30c0\u30a6\u30f3',
        'countdown_full': '\u6570\u5024',
        'countdown_dot': '\u30c9\u30c3\u30c8',
        'menu_essential_bars': '\u8868\u793a\u3059\u308b\u30d0\u30fc',
        'menu_sync_on': '\u30d0\u30fc\u5185\u306e\u540c\u671f\u6642\u523b: \u30aa\u30f3',
        'menu_colors_fixed': '\u30d0\u30fc\u8272: \u56fa\u5b9a',
        'menu_colors_dynamic': '\u30d0\u30fc\u8272: \u6d88\u8cbb\u91cf',
        'menu_sync_off': '\u30d0\u30fc\u5185\u306e\u540c\u671f\u6642\u523b: \u30aa\u30d5',
        'tip_countdown_dot': '\u6b21\u306e\u66f4\u65b0\u304c\u8fd1\u3065\u304f\u3068\u70b9\u6ec5\u3059\u308b\u30c9\u30c3\u30c8\u3067\u77e5\u3089\u305b\u307e\u3059\u3002',
        'tip_countdown_full': '\u5236\u9650\u306e\u30ea\u30bb\u30c3\u30c8\u307e\u3067\u306e\u6b63\u78ba\u306a\u6b8b\u308a\u6642\u9593\u3092\u8868\u793a\u3057\u307e\u3059\u3002',
        'tip_sync': '\u5404\u30d0\u30fc\u306e\u6a2a\u306b\u6700\u5f8c\u306e\u66f4\u65b0\u6642\u523b\u3092\u8868\u793a\u3057\u307e\u3059\u3002',
        'menu_theme': '\u30c6\u30fc\u30de',
        'theme_dark': '\u30c0\u30fc\u30af',
        'theme_light': '\u30e9\u30a4\u30c8',
        'menu_restart': '\u30a6\u30a3\u30b8\u30a7\u30c3\u30c8\u3092\u518d\u8d77\u52d5',
        'tip_restart': '\u30c6\u30fc\u30de\u306f\u753b\u9762\u3092\u4f5c\u308b\u6bb5\u968e\u3067\u6c7a\u307e\u308b\u305f\u3081\u3001'
                       '\u518d\u8d77\u52d5\u5f8c\u306b\u53cd\u6620\u3055\u308c\u307e\u3059\u3002',
        'menu_reset_label': '\u30d0\u30fc\u306e\u4e0b\u306e\u8868\u793a',
        'menu_reset_time_on': '\u30ea\u30bb\u30c3\u30c8\u6642\u523b: \u30aa\u30f3',
        'menu_reset_time_off': '\u30ea\u30bb\u30c3\u30c8\u6642\u523b: \u30aa\u30d5',
        'menu_reset_left_on': '\u6b8b\u308a\u6642\u9593: \u30aa\u30f3',
        'menu_reset_left_off': '\u6b8b\u308a\u6642\u9593: \u30aa\u30d5',
        'tip_reset_time': '\u5404\u5236\u9650\u304c\u30ea\u30bb\u30c3\u30c8\u3055\u308c\u308b\u6642\u523b\u3092\u8868\u793a\u3057\u307e\u3059\u3002',
        'tip_reset_left': '\u30ea\u30bb\u30c3\u30c8\u307e\u3067\u306e\u6b8b\u308a\u6642\u9593\u3092\u8868\u793a\u3057\u307e\u3059\u3002\n\u4e21\u65b9\u30aa\u30d5\u3067\u6700\u3082\u7d30\u304f\u306a\u308a\u307e\u3059\u3002\u30d0\u30fc\u306b\u30ab\u30fc\u30bd\u30eb\u3092\u5408\u308f\u305b\u308b\u3068\u8aad\u3081\u307e\u3059\u3002',
        'dlg_add_how': '\u3069\u306e\u65b9\u6cd5\u3067\u63a5\u7d9a\u3057\u307e\u3059\u304b\uff1f',
        'dlg_add_cc_detail': '\u3053\u306ePC\u306b\u3042\u308bClaude Code\u306e\u30ed\u30b0\u30a4\u30f3\u3092\u4f7f\u3044\u307e\u3059\u3002\u8cbc\u308a\u4ed8\u3051\u306f\u4e0d\u8981\u3067\u3001\u81ea\u52d5\u7684\u306b\u66f4\u65b0\u3055\u308c\u307e\u3059\u3002',
        'dlg_add_key_detail': '\u30d6\u30e9\u30a6\u30b6\u304b\u3089\u53d6\u5f97\u3057\u305f\u30bb\u30c3\u30b7\u30e7\u30f3\u30ad\u30fc\u3092\u8cbc\u308a\u4ed8\u3051\u307e\u3059\u3002\u6709\u52b9\u671f\u9593\u306f\u7d041\u304b\u6708\u3067\u3059\u3002',
        'info_plan': '\u30d7\u30e9\u30f3',
        'info_org': '\u7d44\u7e54',
        'info_extra': '\u8ffd\u52a0\u30af\u30ec\u30b8\u30c3\u30c8',
        'info_on': '\u30aa\u30f3',
        'info_off': '\u30aa\u30d5',
        'info_session_reset': '\u30bb\u30c3\u30b7\u30e7\u30f3\u306e\u30ea\u30bb\u30c3\u30c8',
        'info_week_reset': '\u9031\u306e\u30ea\u30bb\u30c3\u30c8',
        'pref_auto': '\u81ea\u52d5',
        'dlg_remove_account': '\u30a2\u30ab\u30a6\u30f3\u30c8\u3092\u524a\u9664',
        'dlg_last_credential_title': '\u6700\u5f8c\u306e\u8a8d\u8a3c\u60c5\u5831',
        'dlg_last_credential': '\u3053\u306e\u30a2\u30ab\u30a6\u30f3\u30c8\u3092\u53d6\u5f97\u3059\u308b\u552f\u4e00\u306e\u65b9\u6cd5\u3067\u3059\u3002\u524a\u9664\u3057\u3066\u3082\u30a2\u30ab\u30a6\u30f3\u30c8\u3068\u60c5\u5831\u306f\u6b8b\u308a\u3001\u5f8c\u3067\u8a8d\u8a3c\u60c5\u5831\u3092\u8ffd\u52a0\u3067\u304d\u307e\u3059\u3002',
        'cc_account_name': 'Claude Code',
        'cc_token_expired': 'Claude Code \u306e\u30ed\u30b0\u30a4\u30f3\u306e\u6709\u52b9\u671f\u9650\u304c\u5207\u308c\u307e\u3057\u305f\u3002`claude` \u3092\u4e00\u5ea6\u5b9f\u884c\u3057\u3066\u66f4\u65b0\u3057\u3066\u304f\u3060\u3055\u3044\u3002',
        'cc_no_creds': 'Claude Code \u306e\u30ed\u30b0\u30a4\u30f3\u304c\u898b\u3064\u304b\u308a\u307e\u305b\u3093\u3002`claude` \u3092\u4e00\u5ea6\u5b9f\u884c\u3057\u3066\u30b5\u30a4\u30f3\u30a4\u30f3\u3057\u3066\u304f\u3060\u3055\u3044\u3002',
        'cc_no_org': 'このアカウントの Claude Code ログインには、所属する組織の情報がありません。アカウント画面から連携し直してください。',
        'plan_inactive_next': 'claude.ai で更新するか、このアカウントを削除してください。',
        'plan_inactive_add': 'claude.ai で更新してから、もう一度アカウントを追加してください。',
        'no_plan_short': 'プランなし',
        'cc_elsewhere_switch': 'Claude Code は現在 {email} でサインインしています。このアカウントを表示するには、Claude Code をこのアカウントに戻す (アカウント › 切り替え) か、セッションキーを追加してください。',
        'cc_elsewhere_signin': 'Claude Code は現在 {email} でサインインしています。このアカウントを表示するには、このアカウントで Claude Code にもう一度サインインするか、セッションキーを追加してください。',
        'cc_rate_limited': 'Claude \u304b\u3089\u901f\u5ea6\u3092\u843d\u3068\u3059\u3088\u3046\u6c42\u3081\u3089\u308c\u307e\u3057\u305f\u3002\u6570\u5206\u5f8c\u306b\u3082\u3046\u4e00\u5ea6\u304a\u8a66\u3057\u304f\u3060\u3055\u3044\u3002',
        'cc_rate_limited_until': 'Claude \u304b\u3089\u901f\u5ea6\u3092\u843d\u3068\u3059\u3088\u3046\u6c42\u3081\u3089\u308c\u307e\u3057\u305f\u3002{when} \u306b\u518d\u8a66\u884c\u3057\u307e\u3059\u3002',
        'cc_use_login': 'Claude Code \u306e\u30ed\u30b0\u30a4\u30f3\u3092\u4f7f\u3046',
        'cc_linking': '\u3053\u306e\u30b3\u30f3\u30d4\u30e5\u30fc\u30bf\u30fc\u306e Claude Code \u30ed\u30b0\u30a4\u30f3\u3092\u63a5\u7d9a\u3057\u3066\u3044\u307e\u3059\u3002',
        'dlg_retry': '\u518d\u8a66\u884c',
        'dlg_use_key_instead': '\u30bb\u30c3\u30b7\u30e7\u30f3\u30ad\u30fc\u3092\u4f7f\u3046',
        'auth_claude_code': 'Claude Code \u30ed\u30b0\u30a4\u30f3',
        'auth_key': '\u30bb\u30c3\u30b7\u30e7\u30f3\u30ad\u30fc',
        'cc_here': '\u3053\u306ePC\u306eClaude Code: {email}',
        'cc_here_checking': 'Claude Code\u306e\u30ed\u30b0\u30a4\u30f3\u3092\u78ba\u8a8d\u3057\u3066\u3044\u307e\u3059...',
        'cc_here_none': '\u3053\u306ePC\u306bClaude Code\u306e\u30ed\u30b0\u30a4\u30f3\u306f\u3042\u308a\u307e\u305b\u3093',
        'cc_here_unknown': 'このPCのClaude Code: アカウントはまだ特定できていません',
        'cc_here_unlisted': 'このPCのClaude Code: この一覧にないアカウント',
        'cc_here_renews': '(次のコマンドでログインを自動更新します)',
        'cc_link_failed': 'アカウントは追加されていません。',
        'cc_other_account': '\u305d\u306e\u30ed\u30b0\u30a4\u30f3\u306f {email} \u306e\u3082\u306e\u3067\u3059',
        'cc_state_absent': '\u3053\u306ePC\u306b\u306f\u3042\u308a\u307e\u305b\u3093',
        'cc_link': '\u9023\u643a',
        'cc_unlink': '\u89e3\u9664',
        'key_add': '\u30ad\u30fc\u3092\u8ffd\u52a0',
        'key_remove': '\u524a\u9664',
        'key_state_present': '\u4fdd\u5b58\u6e08\u307f',
        'key_state_absent': '\u672a\u8a2d\u5b9a',
        'sub_active': '\u6709\u52b9',
        'sub_canceled': '\u89e3\u7d04\u6e08\u307f',
        'sub_past_due': '\u652f\u6255\u3044\u672a\u5b8c\u4e86',
        'dlg_avatar_title': '\u30a2\u30ab\u30a6\u30f3\u30c8\u306e\u8868\u793a',
        'avatar_kind_initials': '\u982d\u6587\u5b57',
        'avatar_kind_text': '\u30c6\u30ad\u30b9\u30c8',
        'avatar_kind_icon': '\u30a2\u30a4\u30b3\u30f3',
        'avatar_text_hint': '3\u6587\u5b57\u307e\u3067\u3067\u3059\u3002',
        'avatar_bg': '\u80cc\u666f',
        'avatar_fg': '\u8a18\u53f7',
        'dlg_custom': '\u30ab\u30b9\u30bf\u30e0\u2026',
        'dlg_account_details': '\u30a2\u30ab\u30a6\u30f3\u30c8',
        'dlg_account_unknown': '\u60c5\u5831\u306f\u672a\u53d6\u5f97\u3067\u3059',
        'dlg_account_no_method': '\u8a8d\u8a3c\u60c5\u5831\u306a\u3057',
        'dlg_account_manage': '\u7ba1\u7406',
        'dlg_cc_here': 'Claude Code',
        'dlg_account_exists_title': '\u65e2\u306b\u8ffd\u52a0\u6e08\u307f',
        'dlg_update': '\u66f4\u65b0',
        'dlg_login_other_account': 'Claude Code はこのアカウントではなく、別のアカウントでサインインしています。先にこのアカウントで Claude Code にサインインしてから、もう一度お試しください。',
        'dlg_account_exists': '\u300c{name}\u300d\u3068\u540c\u3058\u30a2\u30ab\u30a6\u30f3\u30c8\u3067\u3059\u3002\u65b0\u3057\u304f\u8ffd\u52a0\u305b\u305a\u306b\u66f4\u65b0\u3057\u307e\u3059\u304b\uff1f',
        'tip_colors': '\u56fa\u5b9a: \u5404\u30d0\u30fc\u304c\u72ec\u81ea\u306e\u8272\u3092\u4fdd\u3061\u307e\u3059\u3002\u6d88\u8cbb\u91cf: \u3059\u3079\u3066\u306e\u30d0\u30fc\u304c\u6d88\u8cbb\u91cf\u306b\u5fdc\u3058\u3066\u8272\u5206\u3051\u3055\u308c\u307e\u3059 (\u9752\u3001\u9ec4\u3001\u8d64)\u3002',
        'menu_split': '\u9031\u9593\u30d0\u30fc\u306e\u5206\u5272',
        'split_none': '\u306a\u3057',
        'split_parts': '{n} \u5206\u5272',
        'split_intro': '\u9031\u9593\u30d0\u30fc\u3092\u7b49\u5206\u3057\u307e\u3059\u3002',
        'split_remove': '\u5206\u5272\u3092\u89e3\u9664',
        'tip_split': '\u9031\u9593\u30d0\u30fc\u3092\u7b49\u5206\u3057\u307e\u3059\u3002',
        'tip_notifications': '\u30bb\u30c3\u30b7\u30e7\u30f3\u4f7f\u7528\u91cf\u304c\u3057\u304d\u3044\u5024\u3092\u8d85\u3048\u308b\u3068Windows\u901a\u77e5\u3092\u8868\u793a\u3057\u307e\u3059\u3002',
        'tip_taskbar': '\u30bf\u30b9\u30af\u30d0\u30fc\u306b\u4f7f\u7528\u72b6\u6cc1\u3092\u91cd\u306d\u305f\u30dc\u30bf\u30f3\u3092\u8868\u793a\u3057\u307e\u3059\u3002',
        'menu_refresh_interval': '\u66f4\u65b0\u9593\u9694\u2026',
        'dlg_interval_title': '\u66f4\u65b0\u9593\u9694',
        'dlg_interval_label': '\u79d2\u5358\u4f4d\u306e\u9593\u9694 (\u6700\u4f4e10):',
        'dlg_interval_invalid': '10\u304b\u30893600\u306e\u6570\u5024\u3092\u5165\u529b',
        'menu_quit': '\u7d42\u4e86',
        'menu_language': '\u8a00\u8a9e',
        'menu_check_updates': '\u66f4\u65b0\u3092\u78ba\u8a8d\u2026',
        'update_banner_available': '\u65b0\u3057\u3044\u30d0\u30fc\u30b8\u30e7\u30f3: v{version}',
        'update_banner_update': '\u66f4\u65b0',
        'update_banner_later': '\u3042\u3068\u3067',
        'update_banner_skip': '\u30b9\u30ad\u30c3\u30d7',
        'update_dlg_title': '\u65b0\u3057\u3044\u30d0\u30fc\u30b8\u30e7\u30f3\u304c\u3042\u308a\u307e\u3059',
        'update_dlg_subtitle': '\u30d0\u30fc\u30b8\u30e7\u30f3 {version} \u304c\u5229\u7528\u53ef\u80fd\u3067\u3059\u3002\u73fe\u5728\u306e\u30d0\u30fc\u30b8\u30e7\u30f3: {current}',
        'update_dlg_changelog': '\u5909\u66f4\u70b9',
        'update_dlg_install': '\u4eca\u3059\u3050\u66f4\u65b0',
        'update_dlg_cancel': '\u30ad\u30e3\u30f3\u30bb\u30eb',
        'update_dlg_no_changelog': '\u30ea\u30ea\u30fc\u30b9\u30ce\u30fc\u30c8\u306f\u3042\u308a\u307e\u305b\u3093\u3002',
        'update_dlg_downloading': '\u30c0\u30a6\u30f3\u30ed\u30fc\u30c9\u4e2d {percent}%  ({done} / {total})',
        'update_dlg_launching': '\u66f4\u65b0\u4e2d\u2026',
        'update_dlg_failed': '\u66f4\u65b0\u306b\u5931\u6557\u3057\u307e\u3057\u305f: {error}',
        'update_dlg_open_page': 'GitHub \u3067\u8868\u793a',
        'update_check_uptodate': '\u3059\u3067\u306b\u6700\u65b0\u30d0\u30fc\u30b8\u30e7\u30f3\u3067\u3059\uff08v{version}\uff09\u3002',
        'update_check_failed': 'GitHub \u306b\u63a5\u7d9a\u3067\u304d\u307e\u305b\u3093\u3002\u5f8c\u3067\u518d\u8a66\u884c\u3057\u3066\u304f\u3060\u3055\u3044\u3002',
        'update_check_no_asset': '\u65b0\u3057\u3044\u30d0\u30fc\u30b8\u30e7\u30f3\u306f\u3042\u308a\u307e\u3059\u304c\u3001\u30ea\u30ea\u30fc\u30b9\u306b\u30a4\u30f3\u30b9\u30c8\u30fc\u30e9\u30fc\u304c\u542b\u307e\u308c\u3066\u3044\u307e\u305b\u3093\u3002',
        'dlg_renew_title': '\u30bb\u30c3\u30b7\u30e7\u30f3\u66f4\u65b0',
        'dlg_setup_title': '\u3088\u3046\u3053\u305d',
        'dlg_welcome_hint': 'Claude.ai \u30a2\u30ab\u30a6\u30f3\u30c8\u3068\u30a6\u30a3\u30b8\u30a7\u30c3\u30c8\u3092\u63a5\u7d9a\u3057\u307e\u3059\u3002',
        'dlg_step_guide': '\u30bb\u30c3\u30b7\u30e7\u30f3\u30ad\u30fc\u306e\u53d6\u5f97\u65b9\u6cd5',
        'dlg_step_paste': '\u30bb\u30c3\u30b7\u30e7\u30f3\u30ad\u30fc\u3092\u4e0b\u306b\u8cbc\u308a\u4ed8\u3051\u307e\u3059',
        'dlg_open_guide': '\u30d6\u30e9\u30a6\u30b6\u3067\u30ac\u30a4\u30c9\u3092\u958b\u304f',
        'dlg_paste_empty': '\u4e0a\u306e\u30d5\u30a3\u30fc\u30eb\u30c9\u306b\u30bb\u30c3\u30b7\u30e7\u30f3\u30ad\u30fc\u3092\u8cbc\u308a\u4ed8\u3051\u3066\u304f\u3060\u3055\u3044\u3002',
        'dlg_invalid_prefix': '\u5024\u306f sk-ant- \u3067\u59cb\u307e\u308b\u5fc5\u8981\u304c\u3042\u308a\u307e\u3059',
        'dlg_verifying': '\u78ba\u8a8d\u4e2d\u2026',
        'dlg_error_prefix': '\u30a8\u30e9\u30fc',
        'dlg_renew_gone': '\u3053\u306e\u30a6\u30a3\u30f3\u30c9\u30a6\u3092\u958b\u3044\u3066\u3044\u308b\u9593\u306b\u30a2\u30ab\u30a6\u30f3\u30c8\u304c\u524a\u9664\u3055\u308c\u305f\u305f\u3081\u3001\u30ad\u30fc\u306f\u4fdd\u5b58\u3055\u308c\u3066\u3044\u307e\u305b\u3093\u3002',
        'dlg_connect': '\u63a5\u7d9a',
        'dlg_cancel': '\u30ad\u30e3\u30f3\u30bb\u30eb',
        'key_invalid_or_expired': '\u30bb\u30c3\u30b7\u30e7\u30f3\u30ad\u30fc\u304c\u7121\u52b9\u307e\u305f\u306f\u671f\u9650\u5207\u308c\u3067\u3059',
        'net_err_revocation': '\u30cd\u30c3\u30c8\u30ef\u30fc\u30af\u304c\u8a3c\u660e\u66f8\u306e\u5931\u52b9\u78ba\u8a8d\u3092\u30d6\u30ed\u30c3\u30af\u3057\u3066\u3044\u307e\u3059\u3002VPN\u30fb\u30d5\u30a1\u30a4\u30a2\u30a6\u30a9\u30fc\u30eb\u30fb\u30bb\u30ad\u30e5\u30ea\u30c6\u30a3\u30bd\u30d5\u30c8\u304c\u539f\u56e0\u306e\u5834\u5408\u304c\u3042\u308a\u307e\u3059\u3002',
        'net_err_untrusted': '\u8a3c\u660e\u66f8\u3092\u4fe1\u983c\u3067\u304d\u307e\u305b\u3093\u3002HTTPS\u901a\u4fe1\u3092\u691c\u67fb\u3059\u308b\u30d7\u30ed\u30ad\u30b7\u3084\u30a6\u30a4\u30eb\u30b9\u5bfe\u7b56\u30bd\u30d5\u30c8\u304c\u539f\u56e0\u306e\u5834\u5408\u304c\u3042\u308a\u307e\u3059\u3002',
        'net_err_unreachable': 'claude.ai \u306b\u63a5\u7d9a\u3067\u304d\u307e\u305b\u3093\u3002\u30a4\u30f3\u30bf\u30fc\u30cd\u30c3\u30c8\u63a5\u7d9a\u3092\u78ba\u8a8d\u3057\u3066\u304f\u3060\u3055\u3044\u3002',
        'net_err_timeout': 'claude.ai \u3078\u306e\u63a5\u7d9a\u304c\u30bf\u30a4\u30e0\u30a2\u30a6\u30c8\u3057\u307e\u3057\u305f\u3002',
        'net_err_reset': 'claude.ai \u3078\u306e\u63a5\u7d9a\u304c\u5207\u65ad\u3055\u308c\u307e\u3057\u305f\u3002\u30bb\u30ad\u30e5\u30ea\u30c6\u30a3\u30bd\u30d5\u30c8\u3084\u7d4c\u8def\u4e0a\u306e\u30d7\u30ed\u30ad\u30b7\u304c\u539f\u56e0\u306e\u5834\u5408\u304c\u3042\u308a\u307e\u3059\u3002',
        'net_err_tls': 'claude.ai \u3078\u306e\u5b89\u5168\u306a\u63a5\u7d9a\u306b\u5931\u6557\u3057\u307e\u3057\u305f\u3002',
        'net_err_generic': 'claude.ai \u3078\u306e\u30ea\u30af\u30a8\u30b9\u30c8\u306b\u5931\u6557\u3057\u307e\u3057\u305f\u3002',
        'menu_selftest': '\u63a5\u7d9a\u30c6\u30b9\u30c8\u2026',
        'selftest_title': '\u63a5\u7d9a\u30c6\u30b9\u30c8',
        'selftest_hint': '\u3053\u306ePC\u304cclaude.ai\u306b\u63a5\u7d9a\u3067\u304d\u308b\u304b\u3092\u78ba\u8a8d\u3057\u307e\u3059\u3002\u5931\u6557\u3057\u305f\u9805\u76ee\u304c\u3042\u308c\u3070\u3001\u30ec\u30dd\u30fc\u30c8\u3092\u30b3\u30d4\u30fc\u3057\u3066\u5831\u544a\u306b\u6dfb\u4ed8\u3057\u3066\u304f\u3060\u3055\u3044\u3002',
        'selftest_running': '\u78ba\u8a8d\u4e2d\u2026',
        'selftest_copy': '\u30ec\u30dd\u30fc\u30c8\u3092\u30b3\u30d4\u30fc',
        'selftest_copied': '\u30ec\u30dd\u30fc\u30c8\u3092\u30af\u30ea\u30c3\u30d7\u30dc\u30fc\u30c9\u306b\u30b3\u30d4\u30fc\u3057\u307e\u3057\u305f\u3002',
        'selftest_close': '\u9589\u3058\u308b',
        'selftest_rerun': '\u518d\u5b9f\u884c',
        'selftest_summary_ok': '\u3059\u3079\u3066\u6b63\u5e38\u3067\u3059\u3002claude.ai\u306b\u63a5\u7d9a\u3067\u304d\u3066\u3044\u307e\u3059\u3002',
        'selftest_summary_warn': '\u63a5\u7d9a\u306f\u3067\u304d\u3066\u3044\u307e\u3059\u304c\u3001\u5e72\u6e09\u3059\u308b\u53ef\u80fd\u6027\u306e\u3042\u308b\u8a2d\u5b9a\u304c\u3042\u308a\u307e\u3059\u3002',
        'selftest_summary_fail': '\u5931\u6557\u3057\u305f\u9805\u76ee\u304c\u3042\u308a\u307e\u3059\u3002\u6700\u521d\u306b\u5931\u6557\u3057\u305f\u884c\u304c\u539f\u56e0\u3067\u3059\u3002',
        'selftest_summary_partial': '\u63a5\u7d9a\u306f\u6b63\u5e38\u3067\u3059\u3002\u30a2\u30ab\u30a6\u30f3\u30c8\u304c\u672a\u8a2d\u5b9a\u306e\u305f\u3081\u3001\u4f7f\u7528\u91cf\u30a8\u30f3\u30c9\u30dd\u30a4\u30f3\u30c8\u306f\u78ba\u8a8d\u3057\u3066\u3044\u307e\u305b\u3093\u3002',
        'selftest_dns_failed': 'claude.ai \u306e\u540d\u524d\u89e3\u6c7a\u304c\u3067\u304d\u307e\u305b\u3093\u3002\u3053\u306ePC\u304c\u30aa\u30d5\u30e9\u30a4\u30f3\u3067\u3042\u308b\u304b\u3001\u30d5\u30a3\u30eb\u30bf\u30ea\u30f3\u30b0\u3092\u884c\u3046DNS (Pi-hole\u3001\u793e\u5185DNS\u3001\u30da\u30a2\u30ec\u30f3\u30bf\u30eb\u30b3\u30f3\u30c8\u30ed\u30fc\u30eb) \u304c\u540d\u524d\u3092\u906e\u65ad\u3057\u3066\u3044\u307e\u3059\u3002',
        'selftest_step_curl': 'curl\u3068TLS\u30d0\u30c3\u30af\u30a8\u30f3\u30c9',
        'selftest_step_dns': '\u540d\u524d\u89e3\u6c7a (claude.ai)',
        'selftest_step_tls': 'TLS\u30cf\u30f3\u30c9\u30b7\u30a7\u30a4\u30af',
        'selftest_step_revoke': '\u5931\u52b9\u78ba\u8a8d\u306a\u3057\u306eTLS\u30cf\u30f3\u30c9\u30b7\u30a7\u30a4\u30af',
        'selftest_step_proxy': '\u30d7\u30ed\u30ad\u30b7\u8a2d\u5b9a',
        'selftest_step_curlrc': 'curl\u306e\u8a2d\u5b9a\u30d5\u30a1\u30a4\u30eb',
        'selftest_step_api': '\u4f7f\u7528\u91cfAPI',
        'selftest_curl_missing': 'curl\u304c\u898b\u3064\u304b\u308a\u307e\u305b\u3093\u3002Windows 10 1803\u4ee5\u964d\u306b\u6a19\u6e96\u3067\u542b\u307e\u308c\u3066\u3044\u307e\u3059\u3002',
        'selftest_curl_backend': '\u3053\u306ecurl\u306fWindows\u306e\u8a3c\u660e\u66f8\u30b9\u30c8\u30a2\u3092\u4f7f\u7528\u3057\u3066\u3044\u306a\u3044\u305f\u3081\u3001Windows\u304c\u4fe1\u983c\u3059\u308b\u8a3c\u660e\u66f8\u3092\u62d2\u5426\u3059\u308b\u5834\u5408\u304c\u3042\u308a\u307e\u3059\u3002',
        'selftest_tls_ok': '\u8a3c\u660e\u66f8\u3092\u53d7\u3051\u5165\u308c\u307e\u3057\u305f',
        'selftest_revoke_not_needed': '\u4e0d\u8981\u3067\u3059\u3002\u8a3c\u660e\u66f8\u306e\u78ba\u8a8d\u306f\u3059\u3067\u306b\u6210\u529f\u3057\u3066\u3044\u307e\u3059\u3002',
        'selftest_revoke_worked': '\u5931\u52b9\u78ba\u8a8d\u306a\u3057\u3067\u306f\u6210\u529f\u3057\u307e\u3059\u3002\u8a3c\u660e\u66f8\u306f\u6b63\u5e38\u3067\u3001\u30cd\u30c3\u30c8\u30ef\u30fc\u30af\u304c\u5931\u52b9\u78ba\u8a8d\u3092\u30d6\u30ed\u30c3\u30af\u3057\u3066\u3044\u307e\u3059\u3002\u30a6\u30a3\u30b8\u30a7\u30c3\u30c8\u306f\u3053\u306e\u78ba\u8a8d\u3092\u81ea\u52d5\u7684\u306b\u30b9\u30ad\u30c3\u30d7\u3057\u307e\u3059\u3002',
        'selftest_revoke_inconsistent': '2\u56de\u76ee\u306e\u8a66\u884c\u3067\u306f\u6210\u529f\u3057\u305f\u305f\u3081\u3001\u5931\u52b9\u78ba\u8a8d\u304c\u539f\u56e0\u3067\u306f\u3042\u308a\u307e\u305b\u3093\u3002\u4e00\u6642\u7684\u306a\u5931\u6557\u3068\u8003\u3048\u3089\u308c\u307e\u3059\u3002',
        'selftest_proxy_none': '\u30d7\u30ed\u30ad\u30b7\u306f\u8a2d\u5b9a\u3055\u308c\u3066\u3044\u307e\u305b\u3093',
        'selftest_proxy_found': '\u30d7\u30ed\u30ad\u30b7\u304c\u8a2d\u5b9a\u3055\u308c\u3066\u3044\u307e\u3059',
        'selftest_curlrc_none': '\u898b\u3064\u304b\u308a\u307e\u305b\u3093',
        'selftest_curlrc_found': '\u691c\u51fa',
        'selftest_api_no_account': '\u30a2\u30ab\u30a6\u30f3\u30c8\u304c\u672a\u8a2d\u5b9a\u3067\u3059',
        'selftest_api_key_rejected': '\u30bb\u30c3\u30b7\u30e7\u30f3\u30ad\u30fc\u304c\u62d2\u5426\u3055\u308c\u307e\u3057\u305f\u3002\u30a2\u30ab\u30a6\u30f3\u30c8\u753b\u9762\u3067\u66f4\u65b0\u3057\u3066\u304f\u3060\u3055\u3044\u3002',
        'selftest_api_bad_body': 'claude.ai\u304b\u3089\u4e88\u671f\u3057\u306a\u3044\u5fdc\u7b54\u304c\u3042\u308a\u307e\u3057\u305f',
        'selftest_api_ok': '\u4f7f\u7528\u91cf\u30c7\u30fc\u30bf\u3092\u53d6\u5f97\u3057\u307e\u3057\u305f',
        'dlg_pick_org_title': '\u7d44\u7e54\u3092\u9078\u629e',
        'dlg_pick_org_hint': '\u3053\u306e\u30ed\u30b0\u30a4\u30f3\u306f\u8907\u6570\u306e Claude \u7d44\u7e54\u306b\u6240\u5c5e\u3057\u3066\u304a\u308a\u3001\u7d44\u7e54\u3054\u3068\u306b\u5225\u306e\u30a2\u30ab\u30a6\u30f3\u30c8\u3067\u3059\u3002\u8ffd\u8de1\u3059\u308b\u7d44\u7e54\u3092\u9078\u3093\u3067\u304f\u3060\u3055\u3044\u3002\u5225\u306e\u7d44\u7e54\u3082\u8ffd\u8de1\u3059\u308b\u306b\u306f\u3001\u30a2\u30ab\u30a6\u30f3\u30c8\u3092\u3082\u3046\u4e00\u5ea6\u8ffd\u52a0\u3057\u3066\u305d\u306e\u7d44\u7e54\u3092\u9078\u3073\u307e\u3059\u3002',
        'dlg_pick_org_use': '\u3053\u306e\u7d44\u7e54\u3092\u4f7f\u7528',
        'dlg_pick_org_added': '\u8ffd\u52a0\u6e08\u307f',
        'reset_title': '\u5236\u9650\u306e\u30ea\u30bb\u30c3\u30c8',
        'reset_checking': '\u30ea\u30bb\u30c3\u30c8\u3092\u78ba\u8a8d\u3057\u3066\u3044\u307e\u3059\u2026',
        'reset_none': '\u4eca\u4f7f\u3048\u308b\u30ea\u30bb\u30c3\u30c8\u306f\u3042\u308a\u307e\u305b\u3093\u3002Anthropic \u304b\u3089\u968f\u6642\u4ed8\u4e0e\u3055\u308c\u3001\u3053\u3053\u306b\u8868\u793a\u3055\u308c\u307e\u3059\u3002',
        'reset_grant_left': '\u6b8b\u308a {left}/{total} \u56de \u00b7 {when} \u307e\u3067',
        'reset_full_what': '\u30ea\u30bb\u30c3\u30c8\u3059\u308b\u3068\u30015 \u6642\u9593\u3068\u9031\u9593\u306e\u4f7f\u7528\u91cf\u304c\u307e\u3068\u3081\u3066\u30bc\u30ed\u306b\u623b\u308a\u307e\u3059\u3002',
        'reset_use': '\u30ea\u30bb\u30c3\u30c8\u3092\u4f7f\u3046',
        'reset_needs_limit': '\u5236\u9650\u306b\u9054\u3059\u308b\u3068\u4f7f\u3048\u307e\u3059\u3002',
        'reset_unusable': '\u4eca\u306f\u4f7f\u3048\u307e\u305b\u3093\u3002',
        'reset_available': '\u5229\u7528\u53ef\u80fd',
        'reset_session_ready': '5 \u6642\u9593\u306e\u5236\u9650\u306e\u30ea\u30bb\u30c3\u30c8\u304c\u4f7f\u3048\u307e\u3059\uff08\u9031 {n} \u56de\uff09\u3002',
        'reset_session_next': '5 \u6642\u9593\u306e\u5236\u9650\u306e\u30ea\u30bb\u30c3\u30c8\u306f {when} \u304b\u3089\u518d\u3073\u4f7f\u3048\u307e\u3059\u3002',
        'reset_session_use': '5 \u6642\u9593\u306e\u5236\u9650\u3092\u30ea\u30bb\u30c3\u30c8',
        'reset_confirm_title': '\u30ea\u30bb\u30c3\u30c8\u3092\u4f7f\u3044\u307e\u3059\u304b\uff1f',
        'reset_confirm_full': '{name} \u306e 5 \u6642\u9593\u3068\u9031\u9593\u306e\u4f7f\u7528\u91cf\u304c\u4eca\u3059\u3050\u30bc\u30ed\u306b\u623b\u308a\u307e\u3059\u3002\u53d6\u308a\u6d88\u3057\u306f\u3067\u304d\u307e\u305b\u3093\u3002\u9031\u306e\u30ea\u30bb\u30c3\u30c8\u306f\u3044\u3064\u3082\u306e\u6642\u523b\uff08{week}\uff09\u306e\u307e\u307e\u3067\u3059\u3002',
        'reset_confirm_session': '{name} \u306e 5 \u6642\u9593\u306e\u4f7f\u7528\u91cf\u304c\u4eca\u3059\u3050\u30bc\u30ed\u306b\u623b\u308a\u307e\u3059\u3002\u9031\u9593\u306e\u5236\u9650\u306f\u305d\u306e\u307e\u307e\u9069\u7528\u3055\u308c\u307e\u3059\u3002',
        'reset_using': '\u30ea\u30bb\u30c3\u30c8\u3092\u4f7f\u3063\u3066\u3044\u307e\u3059\u2026',
        'reset_done': '\u5b8c\u4e86: \u5236\u9650\u304c\u30ea\u30bb\u30c3\u30c8\u3055\u308c\u307e\u3057\u305f\u3002',
        'reset_failed_check': '\u30ea\u30bb\u30c3\u30c8\u3092\u78ba\u8a8d\u3067\u304d\u307e\u305b\u3093\u3067\u3057\u305f: {why}',
        'reset_r_already_used': '\u3053\u306e\u30ea\u30bb\u30c3\u30c8\u306f\u3059\u3067\u306b\u4f7f\u308f\u308c\u3066\u3044\u307e\u3059\u3002\u4f55\u3082\u5909\u308f\u3063\u3066\u3044\u307e\u305b\u3093\u3002',
        'reset_r_not_limited': '\u4f55\u3082\u4f7f\u308f\u308c\u3066\u3044\u307e\u305b\u3093\u3002\u3053\u306e\u30ea\u30bb\u30c3\u30c8\u306f\u5236\u9650\u306b\u9054\u3057\u305f\u3068\u304d\u3060\u3051\u4f7f\u3048\u307e\u3059\u3002',
        'reset_r_cooldown': '\u307e\u3060\u4f7f\u3048\u307e\u305b\u3093\u3002\u3057\u3070\u3089\u304f\u3057\u3066\u304b\u3089\u3082\u3046\u4e00\u5ea6\u304a\u8a66\u3057\u304f\u3060\u3055\u3044\u3002',
        'reset_r_ineligible': '\u3053\u306e\u30a2\u30ab\u30a6\u30f3\u30c8\u3067\u306f\u30ea\u30bb\u30c3\u30c8\u3092\u4f7f\u3048\u307e\u305b\u3093\u3002',
        'reset_r_unavailable': '\u3053\u306e\u30ea\u30bb\u30c3\u30c8\u306f\u3082\u3046\u4f7f\u3048\u307e\u305b\u3093\u3002\u4f55\u3082\u4f7f\u308f\u308c\u3066\u3044\u307e\u305b\u3093\u3002',
        'reset_r_rate_limited': '\u30ea\u30af\u30a8\u30b9\u30c8\u304c\u591a\u3059\u304e\u307e\u3059\u30021 \u5206\u307b\u3069\u5f85\u3063\u3066\u304b\u3089\u3082\u3046\u4e00\u5ea6\u304a\u8a66\u3057\u304f\u3060\u3055\u3044\u3002',
        'reset_r_auth': '\u30bb\u30c3\u30b7\u30e7\u30f3\u30ad\u30fc\u304c\u62d2\u5426\u3055\u308c\u307e\u3057\u305f\u3002\u66f4\u65b0\u3057\u3066\u304b\u3089\u3082\u3046\u4e00\u5ea6\u304a\u8a66\u3057\u304f\u3060\u3055\u3044\u3002',
        'reset_r_no_answer': 'claude.ai \u304b\u3089\u5fdc\u7b54\u304c\u3042\u308a\u307e\u305b\u3093\u3002\u3082\u3046\u4e00\u5ea6\u8a66\u3059\u524d\u306b\u3001claude.ai \u306e\u8a2d\u5b9a > \u4f7f\u7528\u72b6\u6cc1\u3092\u78ba\u8a8d\u3057\u3066\u304f\u3060\u3055\u3044\u3002',
        'reset_r_changed': '\u4f7f\u7528\u3055\u308c\u3066\u3044\u307e\u305b\u3093: \u305d\u306e\u9593\u306b\u30a2\u30ab\u30a6\u30f3\u30c8\u304c\u5909\u308f\u308a\u307e\u3057\u305f\u3002',
    },
}

# ═══════════════════════════════════════════════════════
# Claude Code accounts: what the judgements say out loud
# ═══════════════════════════════════════════════════════
#
# cc_accounts.py produces KEYS and the values that fill them, never prose: it
# knows nothing about languages, and a module that answered in Italian would
# have made two of the three impossible. The sentences live here, in their own
# table so that they can be checked against the keys the module actually emits
# (tests/test_cc_i18n.py does exactly that, in all three languages).
#
# A judgement is a statement about what is; a remedy is the thing to do about
# it. Every judgement that is not "good" carries one, because telling somebody
# they have a problem without telling them what to do about it is the surest
# way of having them do nothing.
CC_LANG = {
    'en': {
        # what is
        'cc_j_already_active': 'Claude Code is already signed in as {name}.',
        'cc_j_answered': 'Claude answered.',
        'cc_j_200_not_data': 'Claude answered, but with a page instead of data.',
        'cc_j_403_page': 'Claude answered 403 with a page rather than data: that is the filter in front of the site, and it says nothing about these credentials.',
        'cc_j_refused_401': 'Claude refused these credentials (401).',
        'cc_j_refused_403': 'Claude refused these credentials (403, from the API itself).',
        'cc_j_archive_failed': 'Could not archive it: {why}',
        'cc_j_archived': '{name} is archived.',
        'cc_j_bad_name': '"{name}" is not a name a folder can take.',
        'cc_j_not_checked_stale': 'Not checked against Claude just now, which is normal for an account you are not signed in as. What the switch uses is the renewal of this saved login, valid until {renewal}.',
        'cc_j_checked_alive': 'Checked: the credentials work, and they are {email}.',
        'cc_j_file_corrupt': 'This file is not valid JSON: {path}',
        'cc_j_file_missing': 'This file is not there: {path}',
        'cc_j_file_unreadable': 'I cannot read {path}: {why}',
        'cc_j_live_no_email': 'The email is not in the identity cache yet.',
        'cc_j_live_no_org': 'The live credentials carry no organization, which is the normal state right after a sign-in.',
        'cc_j_live_no_token': 'The live credentials carry no access token.',
        'cc_j_live_ok': 'The live credentials are readable and complete.',
        'cc_j_live_unidentified': 'I cannot tell which account Claude Code is signed in as right now, so I changed nothing: a switch could have written an older login over the one just made.',
        'cc_j_live_moving': 'Claude Code was rewriting its login while I was switching, so I changed nothing.',
        'cc_r_identify_live': 'Try again in a few minutes. If Claude Code has been idle for hours, run one command in it first, so that it renews its login.',
        'cc_j_name_taken': 'A saved login called "{name}" already exists.',
        'cc_j_no_access': 'No access token.',
        'cc_j_no_answer': 'No answer from Claude.',
        'cc_j_no_block': 'This saved login holds no credentials.',
        'cc_j_no_org': 'No organization: this saved login is in the older format.',
        'cc_j_no_refresh': 'No refresh token, so it cannot renew itself.',
        'cc_j_no_refresh_expiry_access_alive': 'The renewal date is not recorded, but the access token is good until {when}.',
        'cc_j_no_refresh_expiry_access_dead': 'The renewal date is not recorded and the access token expired on {when}. The refresh token is there and may still work.',
        'cc_j_no_such_profile': 'There is no saved login called "{name}". Saved: {known}.',
        'cc_j_other_account': 'Those credentials work, but they belong to {email}.',
        'cc_j_outgoing_not_saved': 'I could not save the account you are leaving: {why}',
        'cc_j_profile_outdated': 'This saved login is behind: on {when} the account already carried a different refresh token.',
        'cc_j_rate_limited': 'Claude asked to slow down.',
        'cc_j_refresh_expired': 'The renewal expired on {when}.',
        'cc_j_refresh_soon': 'The renewal expires on {when}, in less than three days.',
        'cc_j_rename_failed': 'I could not rename it: {why}',
        'cc_j_renamed': '"{old}" is now called "{new}".',
        'cc_j_renamed_not_inside': 'The folder was renamed, but the name inside the file is still the old one.',
        'cc_j_server_error': 'Claude has a problem of its own (HTTP {code}).',
        'cc_j_snapshot_behind': 'This is the account in use and it works. Its saved login, from {saved}, is behind.',
        'cc_j_switched': 'Claude Code is now signed in as {name}.',
        'cc_j_unexpected_code': 'Unexpected answer from Claude (HTTP {code}).',
        'cc_j_unrecognised': 'No saved login matches the one on this computer. I tried: {tried}.',
        'cc_j_usable': 'Usable.',
        'cc_j_write_unverified': 'I could not verify what was written. The previous file is in {backups}.',
        # what to do about it
        'cc_r_applies_at_once': 'This takes effect at once and in every chat already open. Nothing to close or reopen.',
        'cc_r_archive_is_reversible': 'Nothing was destroyed: it is in {where}, and moving the folder back puts it in the list again.',
        'cc_r_check_connection': 'Check the connection.',
        'cc_r_check_permissions': 'Check who is allowed to read that file.',
        'cc_r_close_what_uses_it': 'Close whatever is using that folder.',
        'cc_r_email_is_a_label': 'The email is only a label: an account is recognised by its organization.',
        'cc_r_from_backup': 'Recover it from the backups folder.',
        'cc_r_nothing_to_do': 'Nothing to do.',
        'cc_r_pick_another_name': 'Pick another name.',
        'cc_r_pick_existing': 'Pick one of the saved logins.',
        'cc_r_resave_while_active': 'Save this login again while that account is the one signed in.',
        'cc_r_restore_from_backup': 'Put the previous file back from the backups folder.',
        'cc_r_save_current': 'Save the login of the account signed in now, so that it has one of its own.',
        'cc_r_save_now': 'Save it now: that brings the saved login back in step.',
        'cc_r_save_repairs_org': 'Saving the login again puts the organization back where it belongs.',
        'cc_r_sign_in': 'Run `claude` once and sign in.',
        'cc_r_sign_in_then_save': 'Sign in with that account, then save its login again.',
        'cc_r_switch_and_save': 'Switch to that account and save it again.',
        'cc_r_try_again': 'Try again.',
        'cc_r_try_again_soon': 'Try again in a moment.',
        'cc_r_try_later': 'Try again later: this one is not about your credentials.',
        'cc_r_wait_a_few_minutes': 'Wait a few minutes before asking again.',
        # how sure we are that this profile is the live account
        'cc_why_same_refresh': 'same refresh token as the live credentials',
        'cc_why_same_org_live': 'same organization as the live credentials',
        'cc_why_same_org_cache': 'same organization as the identity cache',
        # what was tried, when none of them worked
        'cc_try_refresh_print': 'the refresh token fingerprint',
        'cc_try_no_refresh_print': 'the refresh token fingerprint, which is missing',
        'cc_try_org_live': 'the organization in the live credentials',
        'cc_try_no_org_live': 'the organization in the live credentials, which is missing',
        'cc_try_org_cache': 'the organization in the identity cache',
        'cc_try_no_org_cache': 'the organization in the identity cache, which is missing',
        'cc_try_live_unreadable': 'the live credentials, which could not be read',
        'cc_try_org_server': 'the organization the server named for this login',
        # refusals
        'cc_e_bad_name': 'Letters, digits, space, dot, hyphen and underscore, up to 41 characters.',
        'cc_e_no_live_token': 'The live credentials carry no access token.',
        'cc_e_save_other_account': 'Not saved: the account signed in now is not the one this saved login belongs to, and saving would overwrite it with another account.',
        'cc_e_save_unproven': 'Not saved: I could not confirm which account is signed in now, and a wrong guess would overwrite this saved login with another account. Try again in a few minutes; if Claude Code has been idle for hours, run one command in it first.',
        'cc_e_save_older': 'Not saved: the login in use is an earlier state of this saved login, and saving would put it back over the later one.',
        # the window
        'cc_ui_switch': 'Switch',
        'cc_ui_close': 'Close',
        'cc_ui_snapshot_saved': 'Login saved on {when}. It is now in step with the one in use.',
        'cc_ui_switch_to': 'Switch to {name}',
        'cc_j_no_subscription': 'The subscription on {email} is not active any more, so Claude will not renew this login. Switching to it would leave Claude Code asking you to sign in at your next command.',
        'cc_r_renew_or_pick_other': 'Renew the subscription, or switch to another account.',
        'cc_ui_checking': 'Checking the credentials…',
        'cc_ui_saving_out': 'Saving the account you are leaving…',
        'cc_ui_switching': 'Switching…',
        'cc_ui_proceed': 'Switch anyway',
        'cc_ui_now_widget': 'Widget now showing: {name}',
        'cc_ui_verdict_good': 'Usable',
        'cc_ui_verdict_bad': 'Not usable',
        'cc_ui_verdict_unknown': 'Cannot tell',
        'cc_cli_uses': 'For this account the widget uses the saved login "{name}".',
        'cc_cli_saved_into': 'This account already has a saved login, "{name}": it was saved there.',
        'cc_cli_force': 'To switch anyway: claude-account.ps1 switch "{name}" -Force',
        'cc_cli_incoherent': 'The credentials file names the organization of one account while its tokens belong to another.',
        'cc_cli_synced': 'The saved login "{name}" is now in step with the one in use.',
        'cc_cli_usage': 'Usage: --cc list | save NAME | switch NAME [--force] | verify NAME | resolve NAME | sync | doctor, with --quiet for one line and --out FILE for the output.',
        'cc_cli_failed': 'The command failed; the reason is in widget.log.',
        'cc_cli_in_step': 'The saved login "{name}" is already in step with the one in use.',
        'cc_cli_unlisted': 'The account in use has no saved login yet: save it once, choosing its name.',
        'cc_cli_not_synced': 'The saved login "{name}" was left as it is: the login in use is one it had already moved past.',
        'cc_cli_not_written': 'The saved login "{name}" could not be brought in step. Run the same command again.',
        'cc_cli_sync_unverified': 'The saved login "{name}" was written, but it does not read back as the one in use. Run the same command again.',
        # the renewal and the check of a saved login (2026-09-30)
        'cc_j_renewed': 'The saved login was renewed: it works, and it is good until {until}.',
        'cc_j_renewed_no_date': 'The saved login was renewed: it works.',
        'cc_j_renew_unidentified': 'I cannot tell which account Claude Code is signed in to right now, so I did not renew the saved login of {name}: it could be an earlier copy of the login in use.',
        'cc_j_renew_refused': 'The server no longer accepts the saved login of {name}: switching to it would sign Claude Code out.',
        'cc_j_renew_refused_before': 'The server refused this saved login on {when}.',
        'cc_j_renew_in_use': 'This is the login Claude Code is using: it is not renewed from here.',
        'cc_j_renew_account_in_use': 'Claude Code is signed in to this account: its saved login is not renewed from here, because its token may be one Claude Code has already replaced.',
        'cc_j_renew_moved': 'The saved login of {name} changed while I was renewing it, so I changed nothing.',
        'cc_j_renew_busy': 'Another switch or check of a saved login was already under way on this computer, so I changed nothing.',
        'cc_j_renew_live_refused': 'The server refuses the login Claude Code is using right now, so I cannot tell whose it is, and I did not renew the saved login of {name}.',
        'cc_j_renew_same_signin': 'The saved login of {name} holds an earlier token of the login Claude Code is using now: it would sign Claude Code out, so it is not used.',
        'cc_j_renew_signin_unknown': 'The saved login of {name} has no sign-in date and the server has never confirmed its account: it could be an earlier token of the login Claude Code is using, so it is not used.',
        'cc_j_renew_live_undated': 'The login Claude Code is using has no sign-in date, and the server has never confirmed the account of the saved login of {name}: I cannot tell whether it is an earlier token of that login, so it is not used.',
        'cc_j_renew_installing': 'An update of the widget is being installed, so I changed nothing: try again once it is done.',
        'cc_j_label_contradicted': 'This saved login holds the same tokens as the saved login {other}, whose account the server has confirmed: its own label is wrong, so it is not used.',
        'cc_j_renew_not_kept': 'The server renewed the saved login of {name}, but I could not make sure its new tokens were saved, and the old ones no longer work.',
        'cc_j_renew_other': 'The server refused the request ({error}, HTTP {code}), not the login.',
        'cc_j_in_use_renews': 'In use by Claude Code, which renews it at its next command: there is nothing to check it with right now.',
        'cc_j_in_use_refused': 'The server refused the login Claude Code is using.',
        'cc_j_verified_in_use': 'In use by Claude Code, and it works.',
        'cc_r_login_again': 'Sign in to that account with /login in Claude Code: the widget saves the new login by itself.',
        'cc_ui_verify': 'Verify',
        'cc_ui_verify_tip': 'Renews the saved login now, to be sure the server still accepts it. The login Claude Code is using is checked without renewing it.',
        'cc_ui_verifying': 'Checking…',
        'cc_ui_renewing': 'Renewing the saved login, to be sure it works…',
        'cc_ui_unverified_risk': 'Switching without the check writes the login as it was saved: if the server no longer accepts it, Claude Code will ask you to sign in.',
        'cc_ui_st_in_use': 'In use by Claude Code on this computer',
        'cc_ui_st_in_use_ok': 'In use by Claude Code on this computer · checked {when}',
        'cc_ui_st_saved': 'Saved login · good until {until}',
        'cc_ui_st_saved_no_date': 'Saved login',
        'cc_ui_st_verified': 'Verified {when} · good until {until}',
        'cc_ui_st_verified_no_date': 'Verified {when}',
        'cc_ui_st_refused': 'Refused by the server {when}: sign in again with /login',
        'cc_ui_st_bad': 'Not usable: {why}',
        'cc_ui_st_unverified': 'Not verified {when}: {why}',
        'cc_ui_when_today': 'at {time}',
        'cc_ui_when_day': 'on {day} at {time}',
        'cc_ui_day': '{d}/{m}',
    },
    'it': {
        'cc_j_already_active': 'Claude Code è già autenticato come {name}.',
        'cc_j_answered': 'Claude ha risposto.',
        'cc_j_200_not_data': 'Claude ha risposto, ma con una pagina invece che con i dati.',
        'cc_j_403_page': 'Claude ha risposto 403 con una pagina invece che con i dati: è il filtro davanti al sito, e di queste credenziali non dice niente.',
        'cc_j_refused_401': 'Claude ha rifiutato queste credenziali (401).',
        'cc_j_refused_403': 'Claude ha rifiutato queste credenziali (403, risposta dell’API).',
        'cc_j_archive_failed': 'Non sono riuscito ad archiviarlo: {why}',
        'cc_j_archived': '{name} è archiviato.',
        'cc_j_bad_name': '"{name}" non è un nome che una cartella possa avere.',
        'cc_j_not_checked_stale': 'Non verificato su Claude adesso, ed è normale per un account con cui non sei autenticato. Il cambio usa il rinnovo di questo accesso salvato, valido fino al {renewal}.',
        'cc_j_checked_alive': 'Verificato: le credenziali funzionano, e sono di {email}.',
        'cc_j_file_corrupt': 'Questo file non è un JSON valido: {path}',
        'cc_j_file_missing': 'Questo file non c’è: {path}',
        'cc_j_file_unreadable': 'Non riesco a leggere {path}: {why}',
        'cc_j_live_no_email': 'L’email non è ancora nella cache di identità.',
        'cc_j_live_no_org': 'Le credenziali in uso non indicano l’organizzazione: è lo stato normale subito dopo un accesso.',
        'cc_j_live_no_token': 'Le credenziali in uso non hanno un token di accesso.',
        'cc_j_live_ok': 'Le credenziali in uso sono leggibili e complete.',
        'cc_j_live_unidentified': 'Non riesco a capire con quale account è entrato Claude Code in questo momento, quindi non ho cambiato niente: il cambio avrebbe potuto scrivere un accesso più vecchio sopra quello appena fatto.',
        'cc_j_live_moving': 'Claude Code stava riscrivendo il suo accesso mentre cambiavo account, quindi non ho cambiato niente.',
        'cc_r_identify_live': 'Riprova fra qualche minuto. Se Claude Code è fermo da ore, lancia prima un comando, così rinnova il suo accesso.',
        'cc_j_name_taken': 'Esiste già un accesso salvato che si chiama "{name}".',
        'cc_j_no_access': 'Nessun token di accesso.',
        'cc_j_no_answer': 'Nessuna risposta da Claude.',
        'cc_j_no_block': 'Questo accesso salvato non contiene credenziali.',
        'cc_j_no_org': 'Nessuna organizzazione: l’accesso salvato è nel formato vecchio.',
        'cc_j_no_refresh': 'Nessun token di rinnovo: non potrà rinnovarsi da solo.',
        'cc_j_no_refresh_expiry_access_alive': 'La data di rinnovo non è registrata, ma il token di accesso vale fino al {when}.',
        'cc_j_no_refresh_expiry_access_dead': 'La data di rinnovo non è registrata e il token di accesso è scaduto il {when}. Il token di rinnovo c’è e potrebbe funzionare ancora.',
        'cc_j_no_such_profile': 'Non esiste un accesso salvato che si chiama "{name}". Salvati: {known}.',
        'cc_j_other_account': 'Quelle credenziali funzionano, ma sono di {email}.',
        'cc_j_outgoing_not_saved': 'Non sono riuscito a salvare l’account che stai lasciando: {why}',
        'cc_j_profile_outdated': 'Questo accesso salvato è indietro: il {when} l’account portava già un altro token di rinnovo.',
        'cc_j_rate_limited': 'Claude ha chiesto di rallentare.',
        'cc_j_refresh_expired': 'Il rinnovo è scaduto il {when}.',
        'cc_j_refresh_soon': 'Il rinnovo scade il {when}, fra meno di tre giorni.',
        'cc_j_rename_failed': 'Non sono riuscito a rinominarlo: {why}',
        'cc_j_renamed': '"{old}" adesso si chiama "{new}".',
        'cc_j_renamed_not_inside': 'La cartella è stata rinominata, ma il nome dentro il file è rimasto quello vecchio.',
        'cc_j_server_error': 'Claude ha un problema suo (HTTP {code}).',
        'cc_j_snapshot_behind': 'È l’account che stai usando, e funziona. Il suo accesso salvato, del {saved}, è indietro.',
        'cc_j_switched': 'Claude Code adesso è autenticato come {name}.',
        'cc_j_unexpected_code': 'Risposta inattesa da Claude (HTTP {code}).',
        'cc_j_unrecognised': 'Nessun accesso salvato corrisponde al login su questo computer. Ho provato: {tried}.',
        'cc_j_usable': 'Utilizzabile.',
        'cc_j_write_unverified': 'Non ho potuto verificare ciò che è stato scritto. Il file precedente è in {backups}.',
        'cc_r_applies_at_once': 'Vale subito e per tutte le chat già aperte. Non serve chiudere né riaprire niente.',
        'cc_r_archive_is_reversible': 'Non è stato distrutto niente: sta in {where}, e rispostando la cartella torna nell’elenco.',
        'cc_r_check_connection': 'Controlla la connessione.',
        'cc_r_check_permissions': 'Controlla chi può leggere quel file.',
        'cc_r_close_what_uses_it': 'Chiudi ciò che sta usando quella cartella.',
        'cc_r_email_is_a_label': 'L’email è solo un’etichetta: un account si riconosce dalla sua organizzazione.',
        'cc_r_from_backup': 'Recuperalo dalla cartella delle copie.',
        'cc_r_nothing_to_do': 'Non c’è niente da fare.',
        'cc_r_pick_another_name': 'Scegli un altro nome.',
        'cc_r_pick_existing': 'Scegli uno degli accessi salvati.',
        'cc_r_resave_while_active': 'Salva di nuovo questo accesso mentre quell’account è quello autenticato.',
        'cc_r_restore_from_backup': 'Rimetti il file precedente dalla cartella delle copie.',
        'cc_r_save_current': 'Salva l’accesso dell’account autenticato adesso, così ne avrà uno suo.',
        'cc_r_save_now': 'Salvalo adesso: rimette in pari l’accesso salvato.',
        'cc_r_save_repairs_org': 'Salvare di nuovo l’accesso rimette a posto l’organizzazione.',
        'cc_r_sign_in': 'Esegui `claude` una volta e accedi.',
        'cc_r_sign_in_then_save': 'Accedi con quell’account, poi salva di nuovo il suo accesso.',
        'cc_r_switch_and_save': 'Passa a quell’account e salvalo di nuovo.',
        'cc_r_try_again': 'Riprova.',
        'cc_r_try_again_soon': 'Riprova fra poco.',
        'cc_r_try_later': 'Riprova più tardi: questo non dipende dalle tue credenziali.',
        'cc_r_wait_a_few_minutes': 'Aspetta qualche minuto prima di richiedere.',
        'cc_why_same_refresh': 'stesso token di rinnovo delle credenziali in uso',
        'cc_why_same_org_live': 'stessa organizzazione delle credenziali in uso',
        'cc_why_same_org_cache': 'stessa organizzazione della cache di identità',
        'cc_try_refresh_print': 'l’impronta del token di rinnovo',
        'cc_try_no_refresh_print': 'l’impronta del token di rinnovo, che manca',
        'cc_try_org_live': 'l’organizzazione nelle credenziali in uso',
        'cc_try_no_org_live': 'l’organizzazione nelle credenziali in uso, che manca',
        'cc_try_org_cache': 'l’organizzazione nella cache di identità',
        'cc_try_no_org_cache': 'l’organizzazione nella cache di identità, che manca',
        'cc_try_live_unreadable': 'le credenziali in uso, che non si leggono',
        'cc_try_org_server': 'l’organizzazione che il server ha indicato per questo login',
        'cc_e_bad_name': 'Lettere, numeri, spazio, punto, trattino e trattino basso, fino a 41 caratteri.',
        'cc_e_no_live_token': 'Le credenziali in uso non hanno un token di accesso.',
        'cc_e_save_other_account': 'Non salvato: l’account autenticato adesso non è quello a cui appartiene questo accesso salvato, e salvare lo sovrascriverebbe con un altro account.',
        'cc_e_save_unproven': 'Non salvato: non ho potuto confermare quale account è autenticato adesso, e sbagliando sovrascriverei questo accesso salvato con un altro account. Riprova fra qualche minuto; se Claude Code è fermo da ore, lancia prima un comando.',
        'cc_e_save_older': 'Non salvato: il login in uso è uno stato precedente di questo accesso salvato, e salvando lo si rimetterebbe sopra quello più recente.',
        'cc_ui_switch': 'Passa',
        'cc_ui_close': 'Chiudi',
        'cc_ui_snapshot_saved': 'Accesso salvato il {when}. Adesso è in pari con quello in uso.',
        'cc_ui_switch_to': 'Passa a {name}',
        'cc_j_no_subscription': 'L’abbonamento di {email} non è più attivo, quindi Claude non rinnoverà questo accesso. Passando a questo account, Claude Code ti chiederebbe di accedere al comando successivo.',
        'cc_r_renew_or_pick_other': 'Rinnova l’abbonamento, oppure passa a un altro account.',
        'cc_ui_checking': 'Verifico le credenziali…',
        'cc_ui_saving_out': 'Salvo l’account che stai lasciando…',
        'cc_ui_switching': 'Cambio account in corso…',
        'cc_ui_proceed': 'Passa lo stesso',
        'cc_ui_now_widget': 'Il widget ora mostra: {name}',
        'cc_ui_verdict_good': 'Utilizzabile',
        'cc_ui_verdict_bad': 'Non utilizzabile',
        'cc_ui_verdict_unknown': 'Non verificabile',
        'cc_cli_uses': 'Per questo account il widget usa l’accesso salvato "{name}".',
        'cc_cli_saved_into': 'Questo account ha già un accesso salvato, "{name}": è stato salvato lì.',
        'cc_cli_force': 'Per passare lo stesso: claude-account.ps1 switch "{name}" -Force',
        'cc_cli_incoherent': 'Il file delle credenziali indica l’organizzazione di un account, ma i suoi token sono di un altro.',
        'cc_cli_synced': 'L’accesso salvato "{name}" adesso è in pari con quello in uso.',
        'cc_cli_usage': 'Uso: --cc list | save NOME | switch NOME [--force] | verify NOME | resolve NOME | sync | doctor, con --quiet per una riga sola e --out FILE per l’uscita.',
        'cc_cli_failed': 'Il comando non è riuscito; il motivo è in widget.log.',
        'cc_cli_in_step': 'L’accesso salvato "{name}" è già in pari con quello in uso.',
        'cc_cli_unlisted': 'L’account in uso non ha ancora un accesso salvato: salvalo una volta, scegliendo tu il nome.',
        'cc_cli_not_synced': 'L’accesso salvato "{name}" è rimasto com’era: il login in uso è uno stato che aveva già superato.',
        'cc_cli_not_written': 'Non è stato possibile rimettere in pari l’accesso salvato "{name}". Rilancia lo stesso comando.',
        'cc_cli_sync_unverified': 'L’accesso salvato "{name}" è stato scritto, ma non si rilegge uguale a quello in uso. Rilancia lo stesso comando.',
        # il rinnovo e la verifica di un accesso salvato (30/9/2026)
        'cc_j_renewed': 'Accesso salvato rinnovato: funziona, ed è valido fino al {until}.',
        'cc_j_renewed_no_date': 'Accesso salvato rinnovato: funziona.',
        'cc_j_renew_unidentified': 'Non riesco a capire con quale account è entrato Claude Code in questo momento, quindi non ho rinnovato l’accesso salvato di {name}: potrebbe essere una copia precedente del login in uso.',
        'cc_j_renew_refused': 'Il server non accetta più l’accesso salvato di {name}: passarci farebbe uscire Claude Code.',
        'cc_j_renew_refused_before': 'Il server ha rifiutato questo accesso salvato il {when}.',
        'cc_j_renew_in_use': 'È il login che Claude Code sta usando: da qui non si rinnova.',
        'cc_j_renew_account_in_use': 'Claude Code è entrato con questo account: il suo accesso salvato non si rinnova da qui, perché il suo token potrebbe essere uno che Claude Code ha già sostituito.',
        'cc_j_renew_moved': 'L’accesso salvato di {name} è cambiato mentre lo rinnovavo, quindi non ho cambiato niente.',
        'cc_j_renew_busy': 'Su questo computer era già in corso un altro passaggio o una verifica di un accesso salvato, quindi non ho cambiato niente.',
        'cc_j_renew_live_refused': 'Il server rifiuta il login che Claude Code sta usando in questo momento, quindi non posso capire di chi sia, e non ho rinnovato l’accesso salvato di {name}.',
        'cc_j_renew_same_signin': 'L’accesso salvato di {name} contiene un token precedente del login che Claude Code sta usando: farebbe uscire Claude Code, quindi non lo uso.',
        'cc_j_renew_signin_unknown': 'L’accesso salvato di {name} non ha la data di accesso e il server non ne ha mai confermato l’account: potrebbe essere un token precedente del login che Claude Code sta usando, quindi non lo uso.',
        'cc_j_renew_live_undated': 'Il login che Claude Code sta usando non ha la data di accesso, e il server non ha mai confermato l’account dell’accesso salvato di {name}: non posso escludere che sia un token precedente di quel login, quindi non lo uso.',
        'cc_j_renew_installing': 'Si sta installando un aggiornamento del widget, quindi non ho cambiato niente: riprova quando ha finito.',
        'cc_j_label_contradicted': 'Questo accesso salvato contiene gli stessi token dell’accesso salvato {other}, il cui account è stato confermato dal server: la sua etichetta è sbagliata, quindi non lo uso.',
        'cc_j_renew_not_kept': 'Il server ha rinnovato l’accesso salvato di {name}, ma non ho potuto assicurarmi che i token nuovi fossero salvati, e quelli vecchi non funzionano più.',
        'cc_j_renew_other': 'Il server ha rifiutato la richiesta ({error}, HTTP {code}), non l’accesso.',
        'cc_j_in_use_renews': 'In uso da Claude Code, che lo rinnova al prossimo comando: adesso non c’è niente con cui verificarlo.',
        'cc_j_in_use_refused': 'Il server ha rifiutato il login che Claude Code sta usando.',
        'cc_j_verified_in_use': 'In uso da Claude Code, e funziona.',
        'cc_r_login_again': 'Entra in quell’account con /login in Claude Code: il widget salva da solo il nuovo accesso.',
        'cc_ui_verify': 'Verifica',
        'cc_ui_verify_tip': 'Rinnova adesso l’accesso salvato, per essere sicuri che il server lo accetti ancora. Il login che Claude Code sta usando viene controllato senza rinnovarlo.',
        'cc_ui_verifying': 'Verifico…',
        'cc_ui_renewing': 'Rinnovo l’accesso salvato, per essere sicuro che funzioni…',
        'cc_ui_unverified_risk': 'Passando senza la verifica scrivo l’accesso così com’è salvato: se il server non lo accetta più, Claude Code ti chiederà di rientrare.',
        'cc_ui_st_in_use': 'In uso da Claude Code su questo computer',
        'cc_ui_st_in_use_ok': 'In uso da Claude Code su questo computer · verificato {when}',
        'cc_ui_st_saved': 'Accesso salvato · valido fino al {until}',
        'cc_ui_st_saved_no_date': 'Accesso salvato',
        'cc_ui_st_verified': 'Verificato {when} · valido fino al {until}',
        'cc_ui_st_verified_no_date': 'Verificato {when}',
        'cc_ui_st_refused': 'Rifiutato dal server {when}: entra di nuovo con /login',
        'cc_ui_st_bad': 'Non utilizzabile: {why}',
        'cc_ui_st_unverified': 'Non verificato {when}: {why}',
        'cc_ui_when_today': 'alle {time}',
        'cc_ui_when_day': 'il {day} alle {time}',
        'cc_ui_day': '{d}/{m}',
    },
    'ja': {
        'cc_j_already_active': 'Claude Code はすでに {name} としてサインインしています。',
        'cc_j_answered': 'Claude が応答しました。',
        'cc_j_200_not_data': 'Claude は応答しましたが、データではなくページを返しました。',
        'cc_j_403_page': 'Claude は 403 をデータではなくページで返しました。サイトの手前のフィルターによるもので、この認証情報については何も示していません。',
        'cc_j_refused_401': 'Claude がこの認証情報を拒否しました (401)。',
        'cc_j_refused_403': 'Claude がこの認証情報を拒否しました (403、API 自身からの応答)。',
        'cc_j_archive_failed': 'アーカイブできませんでした: {why}',
        'cc_j_archived': '{name} をアーカイブしました。',
        'cc_j_bad_name': '「{name}」はフォルダー名として使えません。',
        'cc_j_not_checked_stale': '今は Claude に確認していません。サインインしていないアカウントでは普通のことです。切り替えに使うのはこの保存済みログインの更新有効期限で、{renewal} まで有効です。',
        'cc_j_checked_alive': '確認しました。認証情報は有効で、{email} のものです。',
        'cc_j_file_corrupt': 'このファイルは正しい JSON ではありません: {path}',
        'cc_j_file_missing': 'このファイルがありません: {path}',
        'cc_j_file_unreadable': '{path} を読み取れません: {why}',
        'cc_j_live_no_email': 'メールアドレスはまだ識別キャッシュにありません。',
        'cc_j_live_no_org': '現在の認証情報に組織がありません。サインイン直後の通常の状態です。',
        'cc_j_live_no_token': '現在の認証情報にアクセストークンがありません。',
        'cc_j_live_ok': '現在の認証情報は読み取れて、内容もそろっています。',
        'cc_j_live_unidentified': 'Claude Code が今どのアカウントでサインインしているか確認できないため、何も変更していません。切り替えると、今行ったばかりのログインの上に古いログインを書き込むおそれがありました。',
        'cc_j_live_moving': '切り替えの途中で Claude Code がログイン情報を書き換えていたため、何も変更していません。',
        'cc_r_identify_live': '数分後にもう一度お試しください。Claude Code を何時間も使っていない場合は、先に何かコマンドを実行して、ログインを更新させてください。',
        'cc_j_name_taken': '「{name}」という名前の保存済みログインはすでにあります。',
        'cc_j_no_access': 'アクセストークンがありません。',
        'cc_j_no_answer': 'Claude から応答がありません。',
        'cc_j_no_block': 'この保存済みログインに認証情報が入っていません。',
        'cc_j_no_org': '組織がありません。古い形式の保存済みログインです。',
        'cc_j_no_refresh': '更新トークンがないため、自動で更新できません。',
        'cc_j_no_refresh_expiry_access_alive': '更新期限は記録されていませんが、アクセストークンは {when} まで有効です。',
        'cc_j_no_refresh_expiry_access_dead': '更新期限が記録されておらず、アクセストークンも {when} に期限切れです。更新トークンは残っており、まだ使える可能性があります。',
        'cc_j_no_such_profile': '「{name}」という保存済みログインはありません。保存済み: {known}。',
        'cc_j_other_account': 'その認証情報は有効ですが、{email} のものです。',
        'cc_j_outgoing_not_saved': '切り替え元のアカウントを保存できませんでした: {why}',
        'cc_j_profile_outdated': 'この保存済みログインは古くなっています。{when} の時点でアカウントはすでに別の更新トークンを持っていました。',
        'cc_j_rate_limited': 'Claude から速度を落とすよう求められました。',
        'cc_j_refresh_expired': '更新期限が {when} に切れました。',
        'cc_j_refresh_soon': '更新期限は {when}、3 日を切っています。',
        'cc_j_rename_failed': '名前を変更できませんでした: {why}',
        'cc_j_renamed': '「{old}」は「{new}」になりました。',
        'cc_j_renamed_not_inside': 'フォルダー名は変わりましたが、ファイルの中の名前は古いままです。',
        'cc_j_server_error': 'Claude 側の問題です (HTTP {code})。',
        'cc_j_snapshot_behind': '今使っているアカウントで、動作しています。{saved} に保存したログインが古いだけです。',
        'cc_j_switched': 'Claude Code は {name} としてサインインしました。',
        'cc_j_unexpected_code': 'Claude から予期しない応答です (HTTP {code})。',
        'cc_j_unrecognised': 'このコンピューターのログインに一致する保存済みログインがありません。試したもの: {tried}。',
        'cc_j_usable': '使用できます。',
        'cc_j_write_unverified': '書き込み結果を確認できませんでした。直前のファイルは {backups} にあります。',
        'cc_r_applies_at_once': 'すぐに、開いているすべてのチャットに適用されます。閉じたり開き直したりする必要はありません。',
        'cc_r_archive_is_reversible': '何も削除していません。{where} にあり、フォルダーを戻せば一覧に戻ります。',
        'cc_r_check_connection': '接続を確認してください。',
        'cc_r_check_permissions': 'そのファイルの読み取り権限を確認してください。',
        'cc_r_close_what_uses_it': 'そのフォルダーを使っているものを閉じてください。',
        'cc_r_email_is_a_label': 'メールアドレスはラベルにすぎません。アカウントは組織で識別されます。',
        'cc_r_from_backup': 'バックアップのフォルダーから復元してください。',
        'cc_r_nothing_to_do': '何もする必要はありません。',
        'cc_r_pick_another_name': '別の名前にしてください。',
        'cc_r_pick_existing': '保存済みのログインから選んでください。',
        'cc_r_resave_while_active': 'そのアカウントでサインインしている間に、このログインを保存し直してください。',
        'cc_r_restore_from_backup': 'バックアップのフォルダーから直前のファイルを戻してください。',
        'cc_r_save_current': '今サインインしているアカウントのログインを保存してください。',
        'cc_r_save_now': '今すぐ保存してください。保存済みのログインが最新になります。',
        'cc_r_save_repairs_org': 'ログインを保存し直すと組織が元に戻ります。',
        'cc_r_sign_in': '`claude` を一度実行してサインインしてください。',
        'cc_r_sign_in_then_save': 'そのアカウントでサインインしてから、ログインを保存し直してください。',
        'cc_r_switch_and_save': 'そのアカウントに切り替えて、保存し直してください。',
        'cc_r_try_again': 'もう一度試してください。',
        'cc_r_try_again_soon': '少し待ってから試してください。',
        'cc_r_try_later': '後で試してください。認証情報の問題ではありません。',
        'cc_r_wait_a_few_minutes': '数分待ってから試してください。',
        'cc_why_same_refresh': '現在の認証情報と同じ更新トークン',
        'cc_why_same_org_live': '現在の認証情報と同じ組織',
        'cc_why_same_org_cache': '識別キャッシュと同じ組織',
        'cc_try_refresh_print': '更新トークンの指紋',
        'cc_try_no_refresh_print': '更新トークンの指紋 (ありません)',
        'cc_try_org_live': '現在の認証情報の組織',
        'cc_try_no_org_live': '現在の認証情報の組織 (ありません)',
        'cc_try_org_cache': '識別キャッシュの組織',
        'cc_try_no_org_cache': '識別キャッシュの組織 (ありません)',
        'cc_try_live_unreadable': '現在の認証情報 (読み取れませんでした)',
        'cc_try_org_server': 'サーバーがこのログインについて示した組織',
        'cc_e_bad_name': '英数字、スペース、ピリオド、ハイフン、アンダースコアで 41 文字までです。',
        'cc_e_no_live_token': '現在の認証情報にアクセストークンがありません。',
        'cc_e_save_other_account': '保存していません。今サインインしているアカウントはこの保存済みログインのアカウントではなく、保存すると別のアカウントで上書きしてしまいます。',
        'cc_e_save_unproven': '保存していません。今どのアカウントでサインインしているか確認できず、誤るとこの保存済みログインを別のアカウントで上書きしてしまいます。数分後にもう一度お試しください。Claude Code を何時間も使っていない場合は、先に何かコマンドを実行してください。',
        'cc_e_save_older': '保存していません。使用中のログインはこの保存済みログインの以前の状態で、保存すると新しい状態の上に古い状態を戻してしまいます。',
        'cc_ui_switch': '切り替え',
        'cc_ui_close': '閉じる',
        'cc_ui_snapshot_saved': '{when} にログインを保存しました。現在のログインと一致しています。',
        'cc_ui_switch_to': '{name} に切り替え',
        'cc_j_no_subscription': '{email} \u306e\u30b5\u30d6\u30b9\u30af\u30ea\u30d7\u30b7\u30e7\u30f3\u304c\u6709\u52b9\u3067\u306f\u306a\u3044\u305f\u3081\u3001\u3053\u306e\u30ed\u30b0\u30a4\u30f3\u306f\u66f4\u65b0\u3055\u308c\u307e\u305b\u3093\u3002\u5207\u308a\u66ff\u3048\u308b\u3068\u3001\u6b21\u306e\u30b3\u30de\u30f3\u30c9\u3067 Claude Code \u304c\u30b5\u30a4\u30f3\u30a4\u30f3\u3092\u6c42\u3081\u307e\u3059\u3002',
        'cc_r_renew_or_pick_other': '\u30b5\u30d6\u30b9\u30af\u30ea\u30d7\u30b7\u30e7\u30f3\u3092\u66f4\u65b0\u3059\u308b\u304b\u3001\u5225\u306e\u30a2\u30ab\u30a6\u30f3\u30c8\u306b\u5207\u308a\u66ff\u3048\u3066\u304f\u3060\u3055\u3044\u3002',
        'cc_ui_checking': '認証情報を確認しています…',
        'cc_ui_saving_out': '切り替え元のアカウントを保存しています…',
        'cc_ui_switching': '切り替えています…',
        'cc_ui_proceed': 'このまま切り替える',
        'cc_ui_now_widget': 'ウィジェットの表示: {name}',
        'cc_ui_verdict_good': '使用できます',
        'cc_ui_verdict_bad': '使用できません',
        'cc_ui_verdict_unknown': '判断できません',
        'cc_cli_uses': 'このアカウントには、ウィジェットは保存済みのログイン「{name}」を使います。',
        'cc_cli_saved_into': 'このアカウントには保存済みのログイン「{name}」があるため、そこに保存しました。',
        'cc_cli_force': 'それでも切り替える場合: claude-account.ps1 switch "{name}" -Force',
        'cc_cli_incoherent': '認証情報ファイルの組織と、トークンの持ち主のアカウントが一致しません。',
        'cc_cli_synced': '保存済みのログイン「{name}」を使用中のログインと同じ状態にしました。',
        'cc_cli_usage': '使い方: --cc list | save 名前 | switch 名前 [--force] | verify 名前 | resolve 名前 | sync | doctor。--quiet で1行、--out FILE で出力先を指定します。',
        'cc_cli_failed': 'コマンドが失敗しました。理由は widget.log に記録されています。',
        'cc_cli_in_step': '保存済みのログイン「{name}」は、すでに使用中のログインと同じ状態です。',
        'cc_cli_unlisted': '使用中のアカウントには、まだ保存済みのログインがありません。名前を決めて一度保存してください。',
        'cc_cli_not_synced': '使用中のログインは保存済みのログイン「{name}」が以前に持っていた状態のため、そのままにしました。',
        'cc_cli_not_written': '保存済みのログイン「{name}」を使用中のログインと同じ状態にできませんでした。同じコマンドをもう一度実行してください。',
        'cc_cli_sync_unverified': '保存済みのログイン「{name}」を書き込みましたが、読み直すと使用中のログインと一致しません。同じコマンドをもう一度実行してください。',
        # 保存済みログインの更新と確認（2026-09-30）
        'cc_j_renewed': '保存済みログインを更新しました。有効で、{until} まで使えます。',
        'cc_j_renewed_no_date': '保存済みログインを更新しました。有効です。',
        'cc_j_renew_unidentified': 'Claude Code が今どのアカウントでサインインしているか確認できないため、{name} の保存済みログインは更新していません。使用中のログインの以前のコピーかもしれません。',
        'cc_j_renew_refused': 'サーバーは {name} の保存済みログインをもう受け付けません。切り替えると Claude Code がサインアウトされます。',
        'cc_j_renew_refused_before': 'サーバーは {when} にこの保存済みログインを拒否しました。',
        'cc_j_renew_in_use': 'Claude Code が使用中のログインのため、ここからは更新しません。',
        'cc_j_renew_account_in_use': 'Claude Code はこのアカウントでサインイン中です。保存済みログインのトークンは Claude Code がすでに置き換えたものかもしれないため、ここからは更新しません。',
        'cc_j_renew_moved': '{name} の保存済みログインが更新中に変わったため、何も変更していません。',
        'cc_j_renew_busy': 'このPCで別の切り替えか保存済みログインの確認がすでに進行中だったため、何も変更していません。',
        'cc_j_renew_live_refused': 'サーバーは Claude Code が今使用中のログインを拒否しているため、どのアカウントのものか確認できず、{name} の保存済みログインは更新していません。',
        'cc_j_renew_same_signin': '{name} の保存済みログインには、Claude Code が今使っているログインの以前のトークンが入っています。使うと Claude Code がサインアウトされるため、使いません。',
        'cc_j_renew_signin_unknown': '{name} の保存済みログインにはサインイン日がなく、アカウントもサーバーで確認されていません。Claude Code が今使っているログインの以前のトークンかもしれないため、使用しません。',
        'cc_j_renew_live_undated': 'Claude Code が今使っているログインにはサインイン日がなく、{name} の保存済みログインのアカウントもサーバーで確認されていません。そのログインの以前のトークンでないとは言えないため、使用しません。',
        'cc_j_renew_installing': 'ウィジェットの更新をインストールしているため、何も変更していません。完了してからもう一度お試しください。',
        'cc_j_label_contradicted': 'この保存済みログインには、サーバーでアカウントが確認された保存済みログイン {other} と同じトークンが入っています。ラベルが間違っているため、使用しません。',
        'cc_j_renew_not_kept': 'サーバーは {name} の保存済みログインを更新しましたが、新しいトークンが保存されたか確認できませんでした。古いトークンはもう使えません。',
        'cc_j_renew_other': 'サーバーが拒否したのはリクエスト（{error}、HTTP {code}）で、ログインではありません。',
        'cc_j_in_use_renews': 'Claude Code が使用中で、次のコマンドで更新されます。今は確認に使えるものがありません。',
        'cc_j_in_use_refused': 'サーバーは Claude Code が使用中のログインを拒否しました。',
        'cc_j_verified_in_use': 'Claude Code が使用中で、有効です。',
        'cc_r_login_again': 'Claude Code で /login を実行してそのアカウントにサインインしてください。新しいログインはウィジェットが自動で保存します。',
        'cc_ui_verify': '確認',
        'cc_ui_verify_tip': '保存済みログインを今すぐ更新して、サーバーがまだ受け付けるか確かめます。Claude Code が使用中のログインは更新せずに確認します。',
        'cc_ui_verifying': '確認しています…',
        'cc_ui_renewing': '保存済みログインを更新して、使えるか確かめています…',
        'cc_ui_unverified_risk': '確認せずに切り替えると、保存されたままのログインを書き込みます。サーバーがもう受け付けない場合、Claude Code からサインインを求められます。',
        'cc_ui_st_in_use': 'このPCの Claude Code が使用中',
        'cc_ui_st_in_use_ok': 'このPCの Claude Code が使用中 · {when} に確認済み',
        'cc_ui_st_saved': '保存済みログイン · {until} まで有効',
        'cc_ui_st_saved_no_date': '保存済みログイン',
        'cc_ui_st_verified': '{when} に確認済み · {until} まで有効',
        'cc_ui_st_verified_no_date': '{when} に確認済み',
        'cc_ui_st_refused': '{when} にサーバーが拒否しました。/login でサインインし直してください',
        'cc_ui_st_bad': '使用できません: {why}',
        'cc_ui_st_unverified': '{when} は確認できませんでした: {why}',
        'cc_ui_when_today': '{time}',
        'cc_ui_when_day': '{day} {time}',
        'cc_ui_day': '{m}/{d}',
    },
}

for _code, _msgs in CC_LANG.items():
    LANG[_code].update(_msgs)

_current_lang = 'en'

def t(key):
    """Translate a key using the current language (fallback to English)."""
    return LANG.get(_current_lang, LANG['en']).get(key, LANG['en'].get(key, key))

def set_lang(code):
    global _current_lang
    if code in LANG:
        _current_lang = code


def cc_fill(data):
    """The values of a judgement, ready to drop into a sentence.

    Dates arrive as datetimes and lists of message keys arrive as keys: both
    have to become text in the reader's language before the sentence is built,
    and neither can be done inside cc_accounts, which has no language.
    """
    out = {}
    for key, value in (data or {}).items():
        if isinstance(value, datetime):
            out[key] = f'{value:%d/%m/%Y %H:%M}'
        elif isinstance(value, (list, tuple)):
            # A list of message keys (what was tried) or of plain names.
            out[key] = ', '.join(t(str(v)) if str(v).startswith('cc_') else str(v)
                                 for v in value)
        elif isinstance(value, str) and value.startswith('cc_') and value in LANG['en']:
            # A refusal passed on as its key ("cc_e_save_unproven"): the window
            # printed it raw inside the sentence (review of 2026-09-26).
            out[key] = t(value)
        else:
            out[key] = value
    return out


def cc_when(moment):
    """When a saved login was checked: 'alle 09:12' today, 'il 29/9 alle
    09:12' before. '' for anything that is not a time."""
    if not isinstance(moment, datetime):
        return ''
    clock = f'{moment:%H:%M}'
    if moment.date() == datetime.now().date():
        return t('cc_ui_when_today').format(time=clock)
    return t('cc_ui_when_day').format(time=clock, day=cc_day(moment))


def cc_day(moment):
    """A date as day and month ('26/10'; '10/26' in Japanese). A saved login
    lasts weeks, so its year says nothing, and with it the status line of a
    row was cut short at 100%."""
    if not isinstance(moment, datetime):
        return '?'
    return t('cc_ui_day').format(d=moment.day, m=moment.month)


def cc_say(judgement, part='key'):
    """One judgement as a sentence. `part` is 'key' or 'remedy'.

    A missing value must never lose the sentence: a half-filled message still
    tells the user more than a bare identifier does.
    """
    name = (judgement or {}).get(part)
    if not name:
        return ''
    text = t(name)
    try:
        return text.format(**cc_fill(judgement.get('data')))
    except (KeyError, IndexError, ValueError):
        return text


# What the sub-label under each bar carries: the clock the window resets at,
# the time left until it does, or both. Module state rather than a parameter
# threaded through every caller, for the same reason the language is: the
# formatter is called from measurement code that has no view of the config.
_reset_parts = {'time': True, 'left': True}

def set_reset_parts(show_time, show_left):
    _reset_parts['time'] = bool(show_time)
    _reset_parts['left'] = bool(show_left)


# ═══════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════

# Config writes are atomic (temp file + os.replace) and serialised by this
# lock so a save triggered on the refresh worker thread can never interleave
# with one from the Tk main thread. CFG_BAK is defined in the paths section.
_cfg_lock = threading.RLock()


# ─── Session keys ───────────────────────────────────
# A claude.ai session key is a long opaque string with a stable prefix. That
# shape is checked wherever a key is about to be stored, because the same
# cookie is also how the server ENDS a session: `sessionKey=""` with
# Max-Age=0, or a deletion marker. Taking one of those literally once
# overwrote a valid key with two quote characters, after which every refresh
# got a 401 and the only good copy had already rotated out of the backup.
SESSION_KEY_PREFIX = 'sk-ant-'
SESSION_KEY_MIN_LEN = 40


def plausible_key(value):
    """True if `value` looks like a session key rather than a cleared cookie.

    Deliberately strict. Refusing a genuine key costs a missed rotation, and
    the current key keeps working until it expires; accepting a bogus one
    destroys the stored credential and locks the user out.
    """
    return (isinstance(value, str)
            and value.startswith(SESSION_KEY_PREFIX)
            and len(value) >= SESSION_KEY_MIN_LEN)


def _read_config(path):
    """The config at `path`: a dict, {} when missing or unusable, or None when
    the file is there but cannot be read.

    Unknown is not the same as empty. A caller about to discard a backup must
    not act on a guess, and an antivirus holding the file open for a moment is
    enough to produce one.
    """
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}          # no file, so nothing in it to lose
    except OSError:
        return None        # there but unreadable: unknown, not empty
    except ValueError:
        return {}          # unparseable: whatever is in there is unusable
    return data if isinstance(data, dict) else {}


def _account_keys(data):
    """{account id: session key} for every account in `data` that has one."""
    accounts = data.get('accounts')
    if not isinstance(accounts, list):
        return {}
    return {a.get('id'): a['session_key'] for a in accounts
            if isinstance(a, dict) and plausible_key(a.get('session_key'))}


def _account_ids(data):
    """The ids of every account in `data`, with or without a usable key."""
    accounts = data.get('accounts')
    if not isinstance(accounts, list):
        return set()
    return {a.get('id') for a in accounts if isinstance(a, dict)}


def _restore_keys_from_backup(cfg):
    """Put back session keys that a bad write clobbered. Returns True if any.

    Accounts are matched by id and an existing key is only ever replaced when
    it no longer looks like a key, so an account the user deleted cannot come
    back and a genuine rotation is never rolled back. Best effort: if the
    backup has been overwritten too there is nothing to recover, and the user
    is asked to paste the key again as before.
    """
    accounts = cfg.get('accounts')
    if not isinstance(accounts, list):
        return False
    broken = [a for a in accounts
              if isinstance(a, dict) and not plausible_key(a.get('session_key'))]
    if not broken or not CFG_BAK:
        return False
    backup = _read_config(CFG_BAK)
    if not backup:
        return False
    saved = _account_keys(backup)
    legacy = backup.get('session_key') if plausible_key(backup.get('session_key')) else None
    healed = 0
    for account in broken:
        key = saved.get(account.get('id'))
        if (key is None and legacy and len(broken) == 1 and len(accounts) == 1
                and not saved):
            # A config from before multi-account support keeps its key at the
            # top level, so the account this run just migrated has an id the
            # backup cannot know. Only when the backup holds nothing else: a
            # top-level key next to accounts is a mirror of one of them, and
            # taking it would hand this account somebody else's key.
            key = legacy
        if plausible_key(key):
            account['session_key'] = key
            healed += 1
    if healed:
        mirror_active(cfg)
        wlog(f'CFG    restored {healed} session key(s) from the backup')
    return bool(healed)


def sync_backup():
    """Make the backup match the current config, dropping the older generation.

    Called when the user removes an account: without it the backup would keep
    that account's key, since save_cfg deliberately refuses to overwrite a
    backup that still holds keys with one that does not.
    """
    try:
        if CFG_BAK and os.path.exists(CFG):
            shutil.copy2(CFG, CFG_BAK)
    except OSError:
        pass


def load_cfg():
    """Load config, tolerating a corrupt/truncated primary by recovering from
    the .bak written on the last successful save. Never raises: a hard failure
    returns {} (fresh start) instead of crashing the widget before it appears.

    A killed or force-closed write (e.g. the installer's CloseApplications
    during an auto-update) used to leave a half-written or empty config.json,
    which then failed to parse and either crashed startup or was silently
    replaced by defaults. Atomic writes plus this backup fallback remove that
    whole failure mode."""
    cfg = None
    quarantined = False  # primary was corrupt and moved aside -> rewrite it
    for path in (CFG, CFG_BAK):
        if not path or not os.path.exists(path):
            continue
        try:
            with open(path, encoding='utf-8') as f:
                loaded = json.load(f)
        except (json.JSONDecodeError, ValueError) as e:
            # Real corruption (truncated/garbled). Quarantine the bad primary
            # so a reboot does not keep tripping on it, then try the backup.
            wlog(f'CFG    corrupt {os.path.basename(path)}: {e}')
            if path == CFG:
                try:
                    os.replace(CFG, CFG + '.corrupt')
                    quarantined = True
                except OSError:
                    pass
            continue
        except OSError as e:
            # Transient (a sharing/lock violation, e.g. AV scanning the file):
            # the primary may be perfectly good, just unreadable right now. Fall
            # back to the backup for this run, but do NOT quarantine it or later
            # overwrite it with possibly-staler backup data.
            wlog(f'CFG    unreadable {os.path.basename(path)}: {e}')
            continue
        if isinstance(loaded, dict):
            cfg = loaded
            if path != CFG:
                wlog('CFG    recovered from backup')
            break
    if cfg is None:
        return {}
    # Rewrite the primary only when we quarantined it (so the on-disk file is
    # restored) or a migration changed the data. A backup used because of a
    # transient lock is intentionally NOT written back.
    changed = quarantined
    if cfg.get('refresh_ms') == 300_000:  # old 5-min refresh -> 3-min
        cfg['refresh_ms'] = REFRESH
        changed = True
    if 'accounts' not in cfg:  # wrap a legacy single key into the accounts list
        account_migrate(cfg)
        changed = True
    if _restore_keys_from_backup(cfg):
        changed = True
    if changed:
        # Never let a heal-write failure turn a recovered-in-memory config into
        # a hard no-start: this function must not raise (see docstring).
        try:
            save_cfg(cfg)
        except Exception as e:
            wlog(f'CFG    heal-write failed: {e}')
    return cfg


def save_cfg(data):
    """Persist config atomically. Writes to a temp file in the same directory,
    flushes it to disk, then atomically replaces the target, so a crash or
    force-kill mid-write can never leave a truncated or empty config. The
    previous good file is copied to CFG_BAK first for recovery on next load."""
    with _cfg_lock:
        d = os.path.dirname(CFG) or '.'
        # Snapshot the current good file as the backup before overwriting it,
        # but not when that would drop the last copy of a key. An account that
        # is still listed and has lost its key was clobbered, and the backup is
        # all that is left of it; an account that is gone from the list was
        # removed on purpose, and its key is meant to go with it. Judging per
        # account, on that distinction, is what keeps the rule from freezing
        # the backup forever after a removal.
        try:
            if CFG_BAK and os.path.exists(CFG) and os.path.getsize(CFG) > 0:
                current, backup = _read_config(CFG), _read_config(CFG_BAK)
                if current is not None and backup is not None:
                    kept, listed = _account_keys(current), _account_ids(current)
                    if all(acct not in listed or acct in kept
                           for acct in _account_keys(backup)):
                        shutil.copy2(CFG, CFG_BAK)
        except OSError:
            pass
        fd, tmp = tempfile.mkstemp(dir=d, prefix='.cfg-', suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, CFG)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


# ─── Accounts ───────────────────────────────────────
# Multiple Claude.ai logins live under cfg['accounts']; cfg['active_account']
# holds the id of the selected one. The active account's key/org are mirrored
# back onto the top-level cfg['session_key']/cfg['org_id'] so fetch_usage and
# the refresh loop keep reading a single, stable location.

def _new_id():
    return uuid.uuid4().hex


def account_migrate(cfg):
    """Ensure cfg carries an accounts list. A pre-multi-account config has a
    top-level session_key: wrap it as the first (active) account."""
    if cfg.get('accounts'):
        return
    accounts = []
    if cfg.get('session_key'):
        accounts.append({
            'id': _new_id(),
            'name': cfg.get('account_name') or 'Account 1',
            'session_key': cfg['session_key'],
            'org_id': cfg.get('org_id', ''),
            'email': cfg.get('email', ''),
            'plan': cfg.get('plan', ''),
        })
    cfg['accounts'] = accounts
    cfg['active_account'] = accounts[0]['id'] if accounts else None


def active_account(cfg):
    """The selected account dict, or None when no account is configured."""
    aid = cfg.get('active_account')
    for a in cfg.get('accounts', []):
        if a.get('id') == aid:
            return a
    return None


def mirror_active(cfg):
    """Copy the active account's key/org onto the top-level mirror."""
    a = active_account(cfg)
    if a:
        cfg['session_key'] = a.get('session_key', '')
        cfg['org_id'] = a.get('org_id', '')
        cfg['cc_linked'] = bool(a.get('cc_linked'))


def pretty_date(iso, with_time=False):
    """An ISO timestamp as a date somebody can read, or '' if it is not one."""
    if not iso:
        return ''
    try:
        d = datetime.fromisoformat(str(iso).replace('Z', '+00:00')).astimezone()
    except (ValueError, TypeError):
        return ''
    return f'{d:%d/%m/%Y %H:%M}' if with_time else f'{d:%d/%m/%Y}'


def token_expiry():
    """When the Claude Code token on disk stops being accepted, or ''.

    Worth showing: these last hours rather than weeks, which is the reason the
    widget keeps a session key as a fall-back instead of trusting the login on
    its own.
    """
    try:
        with open(cc_accounts.cred_file(), encoding='utf-8') as f:
            exp = (json.load(f).get('claudeAiOauth') or {}).get('expiresAt')
    except (OSError, ValueError):
        return ''
    if not exp:
        return ''
    try:
        return f'{datetime.fromtimestamp(exp / 1000):%d/%m/%Y %H:%M}'
    except (ValueError, OSError, OverflowError):
        return ''


# In how many equal parts the weekly bar can be split. Two is not offered: a
# week's budget cannot be spent in two days when every five hours has its own
# cap. Three was dropped once and brought back at the user's request
# (2026-09-26).
SPLIT_CHOICES = (3, 4, 5, 6, 7)


def elide(text, font, limit):
    """`text` cut with an ellipsis so that it measures no more than `limit`.

    🔴 A Tk label does not wrap and does not shrink: it asks for the
    width its text needs, the frame around it asks for the same, and the row
    ends up wider than the window, which is then clipped at its own edge. From
    outside that looks like the text sliding underneath the buttons on the
    right, which is what the account list did in Italian as soon as a second
    button appeared on the row.
    """
    if not text or limit <= 0:
        return text
    if font.measure(text) <= limit:
        return text
    dots = '\u2026'
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if font.measure(text[:mid] + dots) <= limit:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + dots if lo else dots


def account_methods(acc):
    """Which ways in this account has, best first.

    On auto the Claude Code login comes first, because it renews itself for as
    long as the CLI is used, while a session key has to be pasted again by hand
    every few weeks. An explicit preference moves its own method to the front.
    """
    out = []
    if acc.get('cc_linked'):
        out.append(AUTH_CC)
    if str(acc.get('session_key') or '').startswith(SESSION_KEY_PREFIX):
        out.append(AUTH_KEY)
    pref = acc.get('auth_pref', AUTH_AUTO)
    if pref in out:
        out.remove(pref)
        out.insert(0, pref)
    return tuple(out)


def find_account_by_identity(cfg, org_id, email, skip_id=None):
    """The account that already is this account, credentials or not.

    The organization decides, because it is what the usage is counted against.
    The email is the safety net for an account whose organization was never
    resolved. An account keeps both after its credentials are removed, which is
    what lets a later re-add find it instead of making a copy.
    """
    hit = find_account_by_org(cfg, org_id, skip_id)
    if hit is not None:
        return hit
    if not email:
        return None
    for a in cfg.get('accounts', []):
        if a.get('email') and a['email'].lower() == email.lower() \
                and a.get('id') != skip_id:
            return a
    return None


def find_account_by_org(cfg, org_id, skip_id=None):
    """The account already tracking this organization, if any.

    The organization is the identity: two credentials that resolve to it are
    two doors into the same usage counter, so the second one belongs on the
    account that is already there rather than on a copy of it.
    """
    if not org_id:
        return None
    for a in cfg.get('accounts', []):
        if a.get('org_id') == org_id and a.get('id') != skip_id:
            return a
    return None


def migrate_cc_accounts(cfg):
    """Carry the first shape of the Claude Code login forward.

    It marked an account with auth='claude_code', which said how to read but
    not who the account was. The flag becomes cc_linked; the identity is filled
    in on the next successful read.
    """
    for a in cfg.get('accounts', []):
        if a.pop('auth', None) == AUTH_CC:
            a['cc_linked'] = True


def has_credentials(cfg):
    """True when the active account has any way of reading its usage."""
    acc = active_account(cfg)
    if acc is not None:
        return bool(account_methods(acc))
    return bool(cfg.get('session_key') and cfg.get('org_id'))



def set_active_key(cfg, key, org_id=None):
    """Write a (possibly rotated) key onto the active account and the mirror."""
    a = active_account(cfg)
    if a:
        a['session_key'] = key
        if org_id is not None:
            a['org_id'] = org_id
    cfg['session_key'] = key
    if org_id is not None:
        cfg['org_id'] = org_id


# Bubble tints for account avatars, keyed by a stable hash of the account id.
_BUBBLE_COLORS = ['#DA7756', '#5B9BD5', '#9B72CF', '#4CA98A', '#C9803B', '#B5687F']


def account_initials(name):
    """One or two uppercase initials from an account name for its avatar."""
    parts = [p for p in re.split(r'\s+', (name or '').strip()) if p]
    if not parts:
        return '?'
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[1][0]).upper()


# Glyphs offered as an account avatar. Segoe MDL2 Assets is on every
# Windows 10/11 and draws them all at one visual weight, so the set stays
# coherent and there is nothing to ship. Grouped by sense, six per row in the
# picker: who, what work, tools, marks, messages and time, then places.
AVATAR_ICONS = [
    '\uE77B', '\uE80F', '\uE821', '\uE731', '\uE825', '\uE7BE',
    '\uE943', '\uE99A', '\uE950', '\uE90F', '\uE713', '\uE945',
    '\uE82F', '\uE735', '\uE8D7', '\uE83D', '\uE8EC', '\uE909',
    '\uE715', '\uE90A', '\uE787', '\uE823', '\uE916', '\uE908',
    '\uE82D', '\uE7BF', '\uE8BE', '\uE7EC', '\uE709', '\uE76E',
]
# Symbol colours. White and near-black carry most cases; the four tints are
# there for a dark bubble that wants a softer mark than pure white.
AVATAR_FG_PRESETS = ['#ffffff', '#1e1e1c', '#FFE0B2', '#BBDEFB', '#C8E6C9',
                     '#F8BBD0']


def account_avatar(acc):
    """What the bubble shows: (mark, kind, colour).

    The initials of the name, unless the account was given a short text or one
    of the glyphs. `kind` is 'icon' or 'text' and decides the font.
    """
    av = acc.get('avatar') or {}
    fg = av.get('fg') or '#ffffff'
    if av.get('kind') == 'icon' and av.get('icon'):
        return av['icon'], 'icon', fg
    if av.get('kind') == 'text' and (av.get('text') or '').strip():
        return av['text'].strip()[:3], 'text', fg
    return account_initials(acc.get('name')), 'text', fg


def account_color(acc):
    """The account's avatar tint: the one it was given, else the derived one."""
    return acc.get('color') or bubble_color(acc.get('id'))


def bubble_color(acc_id):
    """Deterministic avatar tint so an account keeps its colour across runs."""
    try:
        n = int((acc_id or '0')[:8], 16)
    except ValueError:
        n = 0
    return _BUBBLE_COLORS[n % len(_BUBBLE_COLORS)]


def _hex_to_rgb(h):
    h = h.lstrip('#')
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _rgb_to_hex(rgb):
    return '#%02x%02x%02x' % tuple(max(0, min(255, round(c))) for c in rgb)


def derive_track(fill_hex):
    """The 'unused' track colour for a bar fill, in the current theme.

    Dark theme: Claude's official fill/track pairs, keeping the hue, pushing
    saturation up a touch (x1.123, clamped) and dropping value to a fixed low
    0.229. Light theme: the same hue washed out instead of darkened, which is
    what the four hand-picked light tracks do, so a colour picked from the
    wheel or the usage-driven palette lands in the same family as them."""
    r, g, b = [c / 255 for c in _hex_to_rgb(fill_hex)]
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    if THEME == 'light':
        r, g, b = colorsys.hsv_to_rgb(h, min(1.0, s * 0.22), 0.97)
    else:
        r, g, b = colorsys.hsv_to_rgb(h, min(1.0, s * 1.123), 0.229)
    return _rgb_to_hex((r * 255, g * 255, b * 255))


# Dynamic (percentage-driven) palette: every bar shares the same scale, so the
# colour reads the consumption level rather than which bar it is.
DYN_LOW  = '#2a78d6'   # blue  - low usage
DYN_MID  = '#fab219'   # amber - mid usage
DYN_HIGH = '#d03b3b'   # red   - high usage


# How much of the reset label there is room for. Ordered widest first, so a
# caller can ask for "at least this narrow" with a comparison.
RESET_FULL = 0        # reset Sat 11:00 (2d 5h)
RESET_NO_PREFIX = 1   # Sat 11:00 (2d 5h)
RESET_COMPACT = 2     # 11:00 (2d 5h)


INK_LIGHT, INK_DARK = '#ffffff', '#20201e'
# Fills that keep white ink whatever the measurement says: see ink_on.
INK_KEEP_WHITE = (BAR_FILL_SESSION.lower(),)
_INK_CACHE = {}


def _relative_luminance(hex_color):
    """WCAG relative luminance, 0 (black) to 1 (white)."""
    out = []
    for c in (c / 255 for c in _hex_to_rgb(hex_color)):
        out.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * out[0] + 0.7152 * out[1] + 0.0722 * out[2]


def contrast_ratio(a, b):
    """How far apart two colours read, 1 (identical) to 21 (black on white)."""
    la, lb = _relative_luminance(a), _relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def ink_on(color):
    """The text colour to use on `color`: white unless white would not read.

    Bar colours are chosen by the user, so neither ink is right everywhere:
    white measures 1.2:1 on the pale tracks of the light theme, where it truly
    disappears, while dark ink disappears on the deep tracks of the dark theme.
    White is kept wherever it clears 3:1 and dark ink takes over below that.

    The amber session fill is the one deliberate exception. It measures 1.8:1
    against white, so the rule would turn the percentage dark on it; the
    widget's owner looked at both and kept white, because at this size the
    number still reads and a single dark figure among white ones breaks a look
    the widget has had from the start. Recorded here rather than by loosening
    the threshold, which would have taken pale custom fills with it.
    """
    if color.lower() in INK_KEEP_WHITE:
        return INK_LIGHT
    hit = _INK_CACHE.get(color)
    if hit is None:
        hit = INK_LIGHT if contrast_ratio(color, INK_LIGHT) >= 3.0 else INK_DARK
        _INK_CACHE[color] = hit
    return hit


def dynamic_fill(pct):
    """Fill colour for the dynamic palette, stepped by usage percentage."""
    if pct >= 85:
        return DYN_HIGH
    if pct >= 50:
        return DYN_MID
    return DYN_LOW


def format_reset(iso_str, level=RESET_FULL, parts=None):
    """Format a reset time at one of three widths.

    RESET_FULL       'reset Sat 11:00 (2d 5h)'  the standard form
    RESET_NO_PREFIX  'Sat 11:00 (2d 5h)'        the word goes first: the clock
                                                and the countdown already say
                                                what it is
    RESET_COMPACT    '11:00 (2d 5h)'            side-by-side bars, where the
                                                weekday goes too

    The word is the widest part of the label that carries no information, so
    dropping it is what lets the window shrink below the full label (issue
    #10).

    Those three are the widths of the FULL label. The user also chooses which
    halves exist at all (set_reset_parts): with only the clock it reads
    'reset Sat 11:00', with only the time left 'reset 2d 5h', and the
    parentheses go with the clock, since they are there to set the countdown
    apart from a time of day. With neither, there is nothing to draw.
    """
    show_time, show_left = (parts if parts is not None
                            else (_reset_parts['time'], _reset_parts['left']))
    if not (show_time or show_left):
        return None
    if not iso_str:
        return None
    try:
        target = datetime.fromisoformat(iso_str)
    except (ValueError, TypeError):
        return None
    local = target.astimezone()
    now_local = datetime.now().astimezone()
    secs = (target - datetime.now(timezone.utc)).total_seconds()
    if secs <= 0:
        return t('soon')
    total_h = int(secs) // 3600
    total_m = (int(secs) % 3600) // 60
    ud, uh, umin = t('unit_d'), t('unit_h'), t('unit_min')
    if total_h >= 48:
        cd = f'{total_h // 24}{ud} {total_h % 24}{uh}'
    elif total_h > 0:
        cd = f'{total_h}{uh} {total_m:02d}{umin}'
    else:
        cd = f'{total_m}{umin}'
    if not show_time:
        return (cd if level >= RESET_NO_PREFIX
                else f'{t("reset_prefix")} {cd}')
    time_str = f'{local:%H:%M}'
    if level >= RESET_COMPACT or local.date() == now_local.date():
        when = time_str
    else:
        when = f'{t("days")[local.weekday()]} {time_str}'
    if not show_left:
        return (when if level >= RESET_NO_PREFIX
                else f'{t("reset_prefix")} {when}')
    if level >= RESET_NO_PREFIX:
        return f'{when} ({cd})'
    return f'{t("reset_prefix")} {when} ({cd})'


def pill(cv, x, y, w, h, color):
    """Draw a pill-shaped bar - ovals + rect, outline=fill to seal seams."""
    r = h / 2
    cv.create_oval(x, y, x + h, y + h, fill=color, outline=color, width=1)
    cv.create_oval(x + w - h, y, x + w, y + h, fill=color, outline=color, width=1)
    if w > h:
        cv.create_rectangle(x + r, y, x + w - r, y + h, fill=color, outline=color, width=0)


_MD_INLINE_RE = re.compile(r'(\*\*[^*\n]+?\*\*|`[^`\n]+?`|~~[^~\n]+?~~)')
_MD_HEADER_RE = re.compile(r'^\s*(#{1,6})\s+(.+?)\s*$')
_MD_BULLET_RE = re.compile(r'^\s*[-*]\s+(.+?)\s*$')

# Headers that mark boilerplate sections not useful inside the in-app update
# dialog (the user is already triggering the install). Case-insensitive.
_MD_SKIP_HEADERS = (
    'install', 'installation', 'installazione',
    'download',
    '\u30a4\u30f3\u30b9\u30c8\u30fc\u30eb',  # インストール
    '\u30c0\u30a6\u30f3\u30ed\u30fc\u30c9',  # ダウンロード
)


def strip_boilerplate_sections(markdown_str):
    """Drop the install/download section (and everything after it).

    GitHub release bodies typically end with an install section that's
    relevant when reading the release page on GitHub but redundant in the
    in-app update dialog - the user is already acting on the update via the
    Install button. We cut the text at the first header whose title matches
    one of the known install/download keywords.
    """
    out_lines = []
    for line in markdown_str.splitlines():
        h = _MD_HEADER_RE.match(line)
        if h:
            title = h.group(2).strip().lower()
            if any(k in title for k in _MD_SKIP_HEADERS):
                break
        out_lines.append(line)
    # Trim trailing blank lines so the rendered block doesn't end with gap.
    while out_lines and not out_lines[-1].strip():
        out_lines.pop()
    return '\n'.join(out_lines)


def _md_insert_inline(text_widget, line, base_tag=None):
    """Insert a line splitting on **bold**, `code`, and ~~strike~~ tokens."""
    parts = _MD_INLINE_RE.split(line)
    for part in parts:
        if not part:
            continue
        if part.startswith('**') and part.endswith('**'):
            tags = ('md_bold',) if not base_tag else (base_tag, 'md_bold')
            text_widget.insert('end', part[2:-2], tags)
        elif part.startswith('`') and part.endswith('`'):
            tags = ('md_code',) if not base_tag else (base_tag, 'md_code')
            text_widget.insert('end', part[1:-1], tags)
        elif part.startswith('~~') and part.endswith('~~'):
            tags = ('md_strike',) if not base_tag else (base_tag, 'md_strike')
            text_widget.insert('end', part[2:-2], tags)
        else:
            tags = (base_tag,) if base_tag else ()
            text_widget.insert('end', part, tags)


def render_markdown_into(text_widget, markdown_str, *, base_font, fg, header_fg):
    """Render a subset of Markdown (headers, bullets, bold, code, strike)
    into a pre-configured tk.Text widget using styled tags.

    Not a full parser - covers the patterns that appear in GitHub release
    notes we write (## Title, ### Install, `ClaudeUsage-Setup.exe`, **bold**,
    `- list items`). Everything else renders as plain text.
    """
    fam, size = base_font.cget('family'), base_font.cget('size')

    # Tag styles - tight spacing so a typical release note fits without
    # scrolling in the update dialog.
    text_widget.tag_configure('md_h',
                              font=(fam, size + 1, 'bold'),
                              foreground=header_fg,
                              spacing1=6, spacing3=1)
    text_widget.tag_configure('md_bold',
                              font=(fam, size, 'bold'),
                              foreground=header_fg)
    # Code spans: monospace + subtle accent color - no background tint.
    # The darker highlight read as random noise next to regular sentences.
    text_widget.tag_configure('md_code',
                              font=('Consolas', max(size - 1, 8)),
                              foreground=header_fg)
    text_widget.tag_configure('md_strike',
                              overstrike=1, foreground=fg)
    text_widget.tag_configure('md_bullet',
                              lmargin1=10, lmargin2=26, spacing1=1)
    text_widget.tag_configure('md_para', spacing1=1)

    text_widget.config(state='normal')
    text_widget.delete('1.0', 'end')

    for raw in markdown_str.splitlines():
        line = raw.rstrip()
        if not line:
            # Blank line becomes a small vertical gap.
            text_widget.insert('end', '\n')
            continue
        h = _MD_HEADER_RE.match(line)
        if h:
            _md_insert_inline(text_widget, h.group(2), 'md_h')
            text_widget.insert('end', '\n')
            continue
        b = _MD_BULLET_RE.match(line)
        if b:
            text_widget.insert('end', '\u2022  ', 'md_bullet')
            _md_insert_inline(text_widget, b.group(1), 'md_bullet')
            text_widget.insert('end', '\n')
            continue
        _md_insert_inline(text_widget, line, 'md_para')
        text_widget.insert('end', '\n')

    text_widget.config(state='disabled')


_PILL_IMAGE_CACHE = {}


def _lerp_hex(c1, c2, frac):
    """Linear-interpolate two '#rrggbb' colors; frac in [0, 1]."""
    r1, g1, b1 = _hex_to_rgb(c1)
    r2, g2, b2 = _hex_to_rgb(c2)
    r = int(r1 + (r2 - r1) * frac)
    g = int(g1 + (g2 - g1) * frac)
    b = int(b1 + (b2 - b1) * frac)
    return f'#{r:02x}{g:02x}{b:02x}'


_DOT_IMG_CACHE = {}


def _dot_image(diameter, color):
    """A smooth, anti-aliased filled circle as a PhotoImage, transparent
    outside the disc. Rendered at 4x then LANCZOS-downscaled so the edges are
    clean (a font glyph's bounding box is asymmetric, which made the dot look
    off-centre; a rendered disc gives exact size and centring). Cached by
    (diameter, color) since the breathing cycle reuses a small set of colors.
    """
    key = (diameter, color)
    img = _DOT_IMG_CACHE.get(key)
    if img is None:
        ss = 4
        d = diameter * ss
        im = Image.new('RGBA', (d, d), (0, 0, 0, 0))
        ImageDraw.Draw(im).ellipse((0, 0, d - 1, d - 1), fill=color)
        im = im.resize((diameter, diameter), Image.LANCZOS)
        img = ImageTk.PhotoImage(im)
        _DOT_IMG_CACHE[key] = img
    return img


def _render_pill_image(w, h, color, radius=None, outline=None, outline_w=1):
    """Render an anti-aliased pill as a PhotoImage.

    Supersamples at 4x then downscales with LANCZOS so the curves are smooth
    instead of the aliased staircase that tkinter's native oval produces.
    `outline` draws a border in that colour, which is how a button that leaves
    the app is marked without giving it the weight of a filled one. Cached by
    every parameter because buttons rarely change dimensions.
    """
    if radius is None:
        radius = h // 2
    key = (w, h, color, radius, outline, outline_w)
    cached = _PILL_IMAGE_CACHE.get(key)
    if cached is not None:
        return cached
    scale = 4
    img = Image.new('RGBA', (w * scale, h * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(
        (0, 0, w * scale - 1, h * scale - 1),
        radius=radius * scale,
        fill=_hex_to_rgb(color) + (255,),
        outline=(_hex_to_rgb(outline) + (255,)) if outline else None,
        width=outline_w * scale if outline else 0,
    )
    img = img.resize((w, h), Image.LANCZOS)
    photo = ImageTk.PhotoImage(img)
    _PILL_IMAGE_CACHE[key] = photo
    return photo


def make_pill_button(parent, *, text, font, fg, bg, hover_bg, cmd,
                     icon=None, icon_font=None, padx=16, pady=8, parent_bg=None,
                     outline=None, outline_w=1):
    """Pill-shaped button with smooth (anti-aliased) curves.

    The pill background is a PIL image rendered at 4x and downscaled with
    LANCZOS so the rounded edges are clean on any display. Text and optional
    emoji icon are drawn on the Canvas above the image. `parent_bg` overrides
    the Canvas background so the pill blends with non-standard surfaces
    (e.g. the orange update banner).
    """
    parent.update_idletasks()
    m = tk.Label(parent, text=text, font=font)
    m.update_idletasks()
    text_w, text_h = m.winfo_reqwidth(), m.winfo_reqheight()
    m.destroy()

    icon_w = 0
    icon_h = 0
    if icon:
        mi = tk.Label(parent, text=icon, font=icon_font or font)
        mi.update_idletasks()
        icon_w, icon_h = mi.winfo_reqwidth(), mi.winfo_reqheight()
        mi.destroy()

    gap = 8 if icon else 0
    content_h = max(text_h, icon_h)
    btn_w = text_w + icon_w + gap + padx * 2
    btn_h = content_h + pady * 2

    canvas_bg = parent_bg if parent_bg is not None else parent.cget('bg')
    cv = tk.Canvas(parent, width=btn_w, height=btn_h,
                   bg=canvas_bg, highlightthickness=0, bd=0, cursor='hand2')

    img_normal = _render_pill_image(btn_w, btn_h, bg, outline=outline,
                                    outline_w=outline_w)
    img_hover  = _render_pill_image(btn_w, btn_h, hover_bg, outline=outline,
                                    outline_w=outline_w)
    # Keep refs on the widget so Python GC doesn't reap the PhotoImages.
    cv._pill_normal = img_normal
    cv._pill_hover  = img_hover
    cv._pill_outline = outline
    bg_item = cv.create_image(0, 0, image=img_normal, anchor='nw')

    cy = btn_h / 2
    if icon:
        # Center the icon+text pair as a group so measurement padding on the
        # text Label doesn't pull everything visually to one side.
        content_w = icon_w + gap + text_w
        start_x = (btn_w - content_w) / 2
        cv.create_text(start_x, cy - 1, text=icon, fill=fg,
                       font=icon_font or font, anchor='w')
        cv.create_text(start_x + icon_w + gap, cy, text=text,
                       fill=fg, font=font, anchor='w')
    else:
        # Anchor the Canvas text to the pill's geometric center.
        cv.create_text(btn_w / 2, cy, text=text, fill=fg,
                       font=font, anchor='center')

    cv.bind('<Enter>', lambda e: cv.itemconfigure(bg_item, image=img_hover))
    cv.bind('<Leave>', lambda e: cv.itemconfigure(bg_item, image=img_normal))
    cv.bind('<Button-1>', lambda e: cmd())
    return cv


def dwm_round(win, shadow=True):
    """Apply W11 rounded corners via DWM (no-op on W10)."""
    try:
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        if not hwnd:
            hwnd = win.winfo_id()
        val = ctypes.c_int(2)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 33, ctypes.byref(val), ctypes.sizeof(val))
        if not shadow:
            GCL_STYLE = -26
            style = ctypes.windll.user32.GetClassLongPtrW(hwnd, GCL_STYLE)
            ctypes.windll.user32.SetClassLongPtrW(
                hwnd, GCL_STYLE, style & ~0x00020000)
            class MARGINS(ctypes.Structure):
                _fields_ = [("l", ctypes.c_int), ("r", ctypes.c_int),
                            ("t", ctypes.c_int), ("b", ctypes.c_int)]
            m = MARGINS(0, 0, 0, 0)
            ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(
                hwnd, ctypes.byref(m))
    except Exception:
        pass


# ═══════════════════════════════════════════════════════
# Per-monitor geometry (Win32)
# ═══════════════════════════════════════════════════════
# Tk's winfo_vroot* only expose the bounding box of all monitors, which stays
# rectangular even when the physical layout is L-shaped. A saved position can
# then sit in an empty gap between monitors: inside the bounding box yet off
# every real screen, so the window is invisible. These helpers query
# the actual monitors so the check matches what Windows can really display.

class _RECT(ctypes.Structure):
    _fields_ = [('left', ctypes.c_long), ('top', ctypes.c_long),
                ('right', ctypes.c_long), ('bottom', ctypes.c_long)]


class _POINT(ctypes.Structure):
    _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]


class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [('cbSize', ctypes.c_ulong), ('rcMonitor', _RECT),
                ('rcWork', _RECT), ('dwFlags', ctypes.c_ulong),
                ('szDevice', ctypes.c_wchar * 32)]


_MONITOR_DEFAULTTONULL = 0
_MONITOR_DEFAULTTOPRIMARY = 1
_MONITOR_DEFAULTTONEAREST = 2
_MONITORENUMPROC = ctypes.WINFUNCTYPE(
    ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
try:
    ctypes.windll.user32.MonitorFromRect.restype = ctypes.c_void_p
    ctypes.windll.user32.MonitorFromRect.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    ctypes.windll.user32.GetMonitorInfoW.restype = ctypes.c_int
    ctypes.windll.user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
except Exception:
    pass


def _monitor_of(x, y, w, h, flag):
    """(device, rcMonitor, rcWork) of the monitor the given rect sits on, with
    the two rects as (l, t, r, b) tuples. With _MONITOR_DEFAULTTONULL returns
    None when the rect intersects no connected monitor; _MONITOR_DEFAULTTONEAREST
    and _MONITOR_DEFAULTTOPRIMARY always return a monitor."""
    try:
        r = _RECT(int(x), int(y), int(x + w), int(y + h))
        hmon = ctypes.windll.user32.MonitorFromRect(ctypes.byref(r), flag)
        if not hmon:
            return None
        mi = _MONITORINFOEXW()
        mi.cbSize = ctypes.sizeof(_MONITORINFOEXW)
        if not ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            return None
        m, wk = mi.rcMonitor, mi.rcWork
        return (mi.szDevice,
                (m.left, m.top, m.right, m.bottom),
                (wk.left, wk.top, wk.right, wk.bottom))
    except Exception:
        return None


def _enum_monitors():
    """List every connected monitor as (device, (l, t, r, b) bounds)."""
    mons = []

    def _cb(hmon, hdc, lprc, data):
        try:
            mi = _MONITORINFOEXW()
            mi.cbSize = ctypes.sizeof(_MONITORINFOEXW)
            if ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
                m = mi.rcMonitor
                mons.append((mi.szDevice, (m.left, m.top, m.right, m.bottom)))
        except Exception:
            pass
        return 1

    try:
        ctypes.windll.user32.EnumDisplayMonitors(
            None, None, _MONITORENUMPROC(_cb), 0)
    except Exception:
        pass
    return mons


def _monitor_signature():
    """A hashable snapshot of the connected monitors (device + bounds) so a
    live layout or resolution change can be detected between polls."""
    return tuple(sorted(_enum_monitors()))


def _place_on_screen(x, y, w, h, min_visible=20):
    """Keep a saved window position visible after a monitor-layout change.

    Returns (x, y, moved). If at least `min_visible` px of the rect show on a
    connected monitor (full bounds, taskbar area included, so a widget parked
    on the taskbar is left where it is), the position is returned unchanged.
    Otherwise it is clamped into the work area of the monitor it overlaps, or
    the primary monitor if it overlaps none (moved=True), so it lands fully on
    screen; the user can drag it back.
    """
    on = _monitor_of(x, y, w, h, _MONITOR_DEFAULTTONULL)
    if on:
        _dev, (ml, mt, mr, mb), _wk = on
        if (min(x + w, mr) - max(x, ml) >= min_visible and
                min(y + h, mb) - max(y, mt) >= min_visible):
            return x, y, False
    fallback = _monitor_of(x, y, w, h, _MONITOR_DEFAULTTOPRIMARY)
    if not fallback:
        return x, y, False
    _dev, _m, (wl, wt, wr, wb) = fallback
    nx = max(wl, min(int(x), wr - w))
    ny = max(wt, min(int(y), wb - h))
    return nx, ny, True


# ═══════════════════════════════════════════════════════
# Showing a window whole
# ═══════════════════════════════════════════════════════
# 🔴 Measured on 2026-09-27, after the user found the menu and the account
# windows opening "broken, built in bands, with black rows through them": a
# Tk window parked off every screen is not painted at all (captured with
# PrintWindow: 48000 black pixels out of 48000). Every menu and dialog was
# built out there so that the building would not show, then moved onto the
# screen still empty and painted there piece by piece, in front of the user,
# and the dialogs were then measured and moved two or three times more, each
# move shifting everything in them. Now a new window is cloaked by DWM from
# its first map (laid out, painted and composed, but not shown), placed and
# painted where it belongs, and uncloaked in one frame once Tk has finished
# with it (Widget._present). A window on show that has to change is covered
# by a still picture of itself while the real one changes out of sight
# (Widget._hold). The same window cloaked at its real place was found whole
# after one turn of the event loop. The widget's own window changes the same
# way when the expand dot or the mode switch reshapes it (Widget._reshape).
#
# Private handles on the system libraries, so that the prototypes set here do
# not change the ones the rest of the file relies on through ctypes.windll.

_PARK = -20000              # where a new window is first mapped: off every screen
_DWMWA_CLOAK = 13
_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_SRCCOPY = 0x00CC0020
_HWND_TOPMOST = -1
_SWP_NOSIZE, _SWP_NOMOVE, _SWP_NOACTIVATE, _SWP_SHOWWINDOW = 0x1, 0x2, 0x10, 0x40
_STM_SETIMAGE = 0x0172
_GWL_EXSTYLE = -20
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_APPWINDOW = 0x00040000
_WS_EX_LAYERED = 0x00080000
_STILL_STYLE = (0x80000000       # a popup
                | 0x0000000E     # SS_BITMAP: the system paints the picture
                | 0x00000100)    # SS_NOTIFY: a click stops on it, never below it
_STILL_EX = (0x00000080          # tool window: no taskbar button
             | 0x00000008        # topmost, like every window of the widget
             | 0x08000000)       # never activated, never takes the focus


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [('biSize', ctypes.c_uint32), ('biWidth', ctypes.c_int32),
                ('biHeight', ctypes.c_int32), ('biPlanes', ctypes.c_uint16),
                ('biBitCount', ctypes.c_uint16), ('biCompression', ctypes.c_uint32),
                ('biSizeImage', ctypes.c_uint32), ('biXPelsPerMeter', ctypes.c_int32),
                ('biYPelsPerMeter', ctypes.c_int32), ('biClrUsed', ctypes.c_uint32),
                ('biClrImportant', ctypes.c_uint32)]


def _show_api():
    """The Win32 and DWM calls below, with their prototypes, or None when they
    cannot be had: every window then shows as it is placed, as before."""
    try:
        from ctypes import wintypes as wt
        u32 = ctypes.WinDLL('user32', use_last_error=True)
        g32 = ctypes.WinDLL('gdi32')
        k32 = ctypes.WinDLL('kernel32')
        dwm = ctypes.WinDLL('dwmapi')
        H = ctypes.c_void_p
        protos = (
            (u32.GetParent, H, [H]),
            (u32.GetWindowRect, wt.BOOL, [H, ctypes.POINTER(wt.RECT)]),
            (u32.GetUpdateRect, wt.BOOL, [H, ctypes.c_void_p, wt.BOOL]),
            (u32.GetWindowDC, H, [H]),
            (u32.ReleaseDC, ctypes.c_int, [H, H]),
            (u32.MonitorFromRect, H, [ctypes.POINTER(wt.RECT), wt.DWORD]),
            (u32.CreateWindowExW, H, [wt.DWORD, ctypes.c_wchar_p, ctypes.c_wchar_p,
                                      wt.DWORD, ctypes.c_int, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_int, H, H, H, H]),
            (u32.SendMessageW, H, [H, ctypes.c_uint, H, H]),
            (u32.UpdateWindow, wt.BOOL, [H]),
            (u32.RedrawWindow, wt.BOOL, [H, ctypes.c_void_p, H, ctypes.c_uint]),
            (u32.DestroyWindow, wt.BOOL, [H]),
            (u32.SetWindowPos, wt.BOOL, [H, H, ctypes.c_int, ctypes.c_int,
                                         ctypes.c_int, ctypes.c_int, ctypes.c_uint]),
            (u32.GetWindowLongW, ctypes.c_long, [H, ctypes.c_int]),
            (u32.GetLayeredWindowAttributes, wt.BOOL,
             [H, ctypes.POINTER(wt.DWORD), ctypes.POINTER(ctypes.c_ubyte),
              ctypes.POINTER(wt.DWORD)]),
            (u32.SetLayeredWindowAttributes, wt.BOOL,
             [H, wt.DWORD, ctypes.c_ubyte, wt.DWORD]),
            (g32.CreateCompatibleDC, H, [H]),
            (g32.CreateDIBSection, H, [H, ctypes.POINTER(_BITMAPINFOHEADER), ctypes.c_uint,
                                       ctypes.POINTER(ctypes.c_void_p), H, wt.DWORD]),
            (g32.SelectObject, H, [H, H]),
            (g32.BitBlt, wt.BOOL, [H, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, H, ctypes.c_int, ctypes.c_int,
                                   wt.DWORD]),
            (g32.DeleteObject, wt.BOOL, [H]),
            (g32.DeleteDC, wt.BOOL, [H]),
            (g32.GdiFlush, wt.BOOL, []),
            (k32.GetModuleHandleW, H, [ctypes.c_wchar_p]),
            (dwm.DwmSetWindowAttribute, ctypes.c_long,
             [H, wt.DWORD, ctypes.c_void_p, wt.DWORD]),
        )
        for fn, res, args in protos:
            fn.restype, fn.argtypes = res, args
        return {'u32': u32, 'g32': g32, 'k32': k32, 'dwm': dwm, 'wt': wt}
    except Exception:
        return None


_SHOW = _show_api()


def _frame_of(win):
    """The window Windows knows for a Tk toplevel: Tk hands back a child of it."""
    child = win.winfo_id()
    if _SHOW is None:
        return child
    return _SHOW['u32'].GetParent(child) or child


def _set_cloak(hwnd, on):
    """Take a window off the screen (on) or put it back, through DWM, without
    unmapping it: meanwhile it goes on being laid out and painted. True when
    DWM did it."""
    if _SHOW is None or not hwnd:
        return False
    try:
        v = ctypes.c_int(1 if on else 0)
        return _SHOW['dwm'].DwmSetWindowAttribute(
            hwnd, _DWMWA_CLOAK, ctypes.byref(v), ctypes.sizeof(v)) == 0
    except Exception:
        return False


def _on_taskbar(hwnd):
    """True when the window has a button on the taskbar: the widget's own
    does when the user turned it on (show_in_taskbar). Unknown counts as yes."""
    if _SHOW is None or not hwnd:
        return True
    try:
        ex = _SHOW['u32'].GetWindowLongW(hwnd, _GWL_EXSTYLE)
    except Exception:
        return True
    return bool(ex & _WS_EX_APPWINDOW) or not (ex & _WS_EX_TOOLWINDOW)


def _unpainted(win):
    """True while some part of the toplevel still waits for the paint message
    Windows sends before Tk draws it. Toplevels it hosts (a tooltip) are not
    part of it."""
    if _SHOW is None:
        return False
    try:
        hwnds = [_frame_of(win)]
        todo = [win]
        while todo:
            w = todo.pop()
            hwnds.append(w.winfo_id())
            todo.extend(c for c in w.winfo_children() if not isinstance(c, tk.Toplevel))
    except tk.TclError:
        return False
    return any(_SHOW['u32'].GetUpdateRect(h, None, False) for h in hwnds)


def _paint_now(win):
    """Deliver at once every paint message the toplevel is waiting for.

    Windows hands them out one per turn of the event loop, and a menu is some
    fifty windows: measured, the menu waited close to 200 ms for its last
    one. Tk turns each into an Expose event, drawn at the next idle."""
    if _SHOW is not None:
        try:
            # RDW_UPDATENOW | RDW_ALLCHILDREN: paints what is invalid, nothing more.
            _SHOW['u32'].RedrawWindow(_frame_of(win), None, None, 0x0100 | 0x0080)
        except (tk.TclError, OSError):
            pass


def _gdi_flush():
    if _SHOW is not None:
        try:
            _SHOW['g32'].GdiFlush()
        except Exception:
            pass


class _Still:
    """A still picture of a window on show, in its place.

    The window's own pixels, read from its surface, shown by a plain system
    STATIC popup set directly above it in the z-order and given the same
    corners and the same translucency (the widget's own window is 0.94
    opaque: a solid picture of it stood out while it was up). Taking the
    real window away behind it changes no pixel on screen. From its surface and not from the screen: a screen grab of the
    account list took in the account page lying over it, and would have left
    the page's ghost behind when the page was redrawn smaller. Built cloaked,
    painted at once (UpdateWindow), and uncloaked whole. It is never
    activated and answers nothing: a click on it is lost, as a click on a
    window being rebuilt always was.
    """

    def __init__(self, hwnd, rounded=True):
        api = _SHOW
        if api is None:
            raise OSError('no window API')
        u32, g32, wt = api['u32'], api['g32'], api['wt']
        self.hwnd = self._bmp = None
        r = wt.RECT()
        if not u32.GetWindowRect(hwnd, ctypes.byref(r)):
            raise OSError('the window has no rectangle')
        if not u32.MonitorFromRect(ctypes.byref(r), 0):   # MONITOR_DEFAULTTONULL
            raise OSError('the window is on no screen: nothing to keep still')
        x, y, w, h = r.left, r.top, r.right - r.left, r.bottom - r.top
        if w <= 0 or h <= 0:
            raise OSError('the window has no size')
        self.rect = (x, y, w, h)
        # 24 bits: no alpha channel for the system to blend the picture with.
        bi = _BITMAPINFOHEADER(ctypes.sizeof(_BITMAPINFOHEADER), w, -h, 1, 24, 0, 0, 0, 0, 0, 0)
        key, alpha, flags = wt.DWORD(), ctypes.c_ubyte(), wt.DWORD()
        layered = bool(u32.GetLayeredWindowAttributes(hwnd, ctypes.byref(key),
                                                      ctypes.byref(alpha), ctypes.byref(flags)))
        bits = ctypes.c_void_p()
        src = u32.GetWindowDC(hwnd)
        mem = g32.CreateCompatibleDC(src)
        self._bmp = g32.CreateDIBSection(mem, ctypes.byref(bi), 0, ctypes.byref(bits), None, 0)
        try:
            if not self._bmp:
                raise OSError('no memory for the picture')
            old = g32.SelectObject(mem, self._bmp)
            ok = g32.BitBlt(mem, 0, 0, w, h, src, 0, 0, _SRCCOPY)
            g32.SelectObject(mem, old)
            if not ok:
                raise OSError('the window could not be read')
            self.hwnd = u32.CreateWindowExW(_STILL_EX | (_WS_EX_LAYERED if layered else 0),
                                            'Static', None, _STILL_STYLE,
                                            x, y, w, h, None, None,
                                            api['k32'].GetModuleHandleW(None), None)
            if not self.hwnd:
                raise OSError('the still window could not be created')
            if layered and not u32.SetLayeredWindowAttributes(self.hwnd, key.value,
                                                              alpha.value, flags.value):
                raise OSError('the still window could not take the translucency')
            if not _set_cloak(self.hwnd, True):
                raise OSError('the still window could not be cloaked')
            if rounded:
                pref = ctypes.c_int(2)                     # DWMWCP_ROUND, as dwm_round
                api['dwm'].DwmSetWindowAttribute(self.hwnd, _DWMWA_WINDOW_CORNER_PREFERENCE,
                                                 ctypes.byref(pref), ctypes.sizeof(pref))
            u32.SendMessageW(self.hwnd, _STM_SETIMAGE, None, self._bmp)   # IMAGE_BITMAP
            if not self.seat(hwnd, show=True):
                raise OSError('the still window could not be shown')
            u32.UpdateWindow(self.hwnd)
            _gdi_flush()
            _set_cloak(self.hwnd, False)
        except Exception:
            self.close()
            raise
        finally:
            g32.DeleteDC(mem)
            u32.ReleaseDC(hwnd, src)

    def seat(self, hwnd, show=False):
        """Directly above the window `hwnd`, below whatever is above it: put
        just under it, and the window then put under the picture. Only our
        own windows are named to Windows. The picture used to be put after
        the window above, and when that one was the system's
        (ForegroundStaging, which Windows keeps over the topmost windows, the
        widget's included) Windows refused, access denied (measured
        2026-09-28): the picture never showed, and the window it stood for
        went blank while it changed. The windows a window owns (the widget's
        popups) stay above it, and so above its picture."""
        u32 = _SHOW['u32']
        flags = _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOACTIVATE
        if not self.hwnd or not u32.SetWindowPos(self.hwnd, hwnd, 0, 0, 0, 0,
                                                 flags | (_SWP_SHOWWINDOW if show else 0)):
            return False
        u32.SetWindowPos(hwnd, self.hwnd, 0, 0, 0, 0, flags)
        return True

    def lift(self):
        """Back to the top of the topmost band, just after its window was."""
        if self.hwnd:
            _SHOW['u32'].SetWindowPos(self.hwnd, ctypes.c_void_p(_HWND_TOPMOST), 0, 0, 0, 0,
                                      _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOACTIVATE)

    def close(self):
        hwnd, bmp, self.hwnd, self._bmp = self.hwnd, self._bmp, None, None
        u32, g32 = _SHOW['u32'], _SHOW['g32']
        if hwnd:
            try:
                # The control may have kept a copy of its own: it is handed back
                # here, and freed with ours.
                held = u32.SendMessageW(hwnd, _STM_SETIMAGE, None, None)
                if held and held != bmp:
                    g32.DeleteObject(held)
                u32.DestroyWindow(hwnd)
            except Exception:
                pass
        if bmp:
            g32.DeleteObject(bmp)


# ═══════════════════════════════════════════════════════
# API
# ═══════════════════════════════════════════════════════

_BROWSER_UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36'
)


# Schannel status codes reported when the revocation check could not be
# completed (CRYPT_E_NO_REVOCATION_CHECK / CRYPT_E_REVOCATION_OFFLINE). They
# mean "could not verify", not "the certificate is revoked": a VPN, filtering
# DNS or security suite is blocking the CA's OCSP/CRL endpoint.
_REVOCATION_HINTS = ('0x80092012', '0x80092013', 'revocation')

# curl exit codes worth explaining in the user's own words.
CURL_COULDNT_RESOLVE_HOST = 6
CURL_COULDNT_CONNECT = 7
CURL_OPERATION_TIMEDOUT = 28
CURL_SSL_CONNECT_ERROR = 35
CURL_SEND_ERROR = 55
CURL_RECV_ERROR = 56
CURL_PEER_FAILED_VERIFICATION = 60

# Failures that are usually gone a moment later: a laptop resuming from
# standby, a VPN reconnecting, a connection dropped mid-flight. Worth one
# retry before telling the user anything is wrong.
_TRANSIENT_EXITS = (CURL_COULDNT_RESOLVE_HOST, CURL_COULDNT_CONNECT,
                    CURL_OPERATION_TIMEDOUT, CURL_SEND_ERROR, CURL_RECV_ERROR)
TRANSIENT_RETRY_DELAY_S = 1.5


# Anything account-identifying that must never reach a log, an error message
# or the self-test report, which users are asked to paste into public issues.
_SECRET_PATTERNS = (
    (re.compile(r'sk-ant-[A-Za-z0-9_\-]+'), 'sk-ant-***'),
    # Credentials in a URL, before the email rule can nibble at them. Matches a
    # bare intranet host too, which is the common shape of a corporate proxy.
    (re.compile(r'//[^/\s@]+@'), '//<redacted>@'),
    (re.compile(r'\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b'), '<org-id>'),
    (re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+'), '<redacted>'),
    # Up to the next path separator, not the next space: a Windows account
    # name can contain one ("C:\\Users\\Mario Rossi\\...").
    # With one backslash or two, or forward slashes: str(OSError) quotes
    # its path with repr(), which doubles every backslash, and the rule
    # never matched the lines that needed it (review of 2026-09-26).
    (re.compile(r'(?i)([A-Z]:(?:\\{1,2}|/)Users(?:\\{1,2}|/))[^\\/\r\n"\']+'),
     r'\1<user>'),
)


def _redact(text):
    """Strip account-identifying values from text that will be shown or shared.

    Applied at the points where such text enters the program rather than
    trusted to each caller, so a new caller cannot leak by omission. Covers
    session keys, organization ids, email addresses, credentials embedded in a
    proxy URL, and the Windows account name in a file path.
    """
    out = str(text)
    for pattern, replacement in _SECRET_PATTERNS:
        out = pattern.sub(replacement, out)
    return out


def _decode_console(raw):
    """Decode output curl wrote to the console.

    curl uses the Windows ANSI code page for its messages, not UTF-8, so a
    strict UTF-8 decode fails on any localized text and 'replace' would turn
    accented characters into replacement marks.
    """
    try:
        return raw.decode('utf-8').strip()
    except UnicodeDecodeError:
        try:
            return raw.decode('mbcs', 'replace').strip()
        except (LookupError, ValueError):
            return raw.decode('utf-8', 'replace').strip()


def _first_line(text):
    """Keep only the first line of a console message.

    curl follows a certificate error with a fixed five-line advisory pointing
    at its documentation. The first line is the error; the rest would flood
    the widget's error panel, the log and the self-test report.
    """
    return text.split('\n', 1)[0].strip()


def _explain_curl_error(code, message):
    """Turn a curl failure into something the user can act on.

    The raw text is kept after the explanation: it is what a bug report needs,
    while the leading sentence is what tells the user whether to look at their
    network, their proxy or their key.
    """
    lower = message.lower()
    if any(h in lower for h in _REVOCATION_HINTS):
        hint = t('net_err_revocation')
    elif (code == CURL_PEER_FAILED_VERIFICATION
            or 'untrusted' in lower or '0x800b0109' in lower or '0x800b010a' in lower
            or 'local issuer' in lower or 'self-signed' in lower
            or 'self signed' in lower):
        # Exit 60 and the issuer wording come from an OpenSSL-backed curl
        # (Git, MSYS, conda) that validates against its own CA bundle rather
        # than the Windows store, so a root Windows trusts is rejected.
        hint = t('net_err_untrusted')
    elif code in (CURL_COULDNT_RESOLVE_HOST, CURL_COULDNT_CONNECT):
        hint = t('net_err_unreachable')
    elif code == CURL_OPERATION_TIMEDOUT:
        hint = t('net_err_timeout')
    elif code in (CURL_SEND_ERROR, CURL_RECV_ERROR):
        # A reset connection is not a slow one: something cut it deliberately.
        hint = t('net_err_reset')
    elif code == CURL_SSL_CONNECT_ERROR:
        hint = t('net_err_tls')
    else:
        hint = t('net_err_generic')
    return f'{hint} ({message})' if message else hint


def _curl_args(url, cookie, max_time, headers=()):
    """Argument list every claude.ai request shares.

    `-D -` dumps the response headers to stdout ahead of the body so the
    caller can read the status line: without it, a 401 error payload parses as
    valid JSON and gets mis-read as a missing organization.

    `--max-time` makes curl give up before Python's own timeout does. curl's
    default connect timeout is 300s, so against a firewall that silently drops
    the connection Python would be the one to time out, and its exception
    carries the whole command line - session key included.
    """
    args = ['-D', '-', '--max-time', str(max_time),
            '-H', f'User-Agent: {_BROWSER_UA}',
            '-H', 'anthropic-client-platform: web_claude_ai']
    if cookie:
        args += ['-H', f'Cookie: {cookie}']
    for h in headers:
        args += ['-H', h]
    return args + [url]


def _curl_attempt(url, cookie, extra=(), timeout=20, headers=()):
    """Run curl once. Returns (exit code, stdout bytes, decoded stderr).

    NOTE: claude.ai sits behind Cloudflare which fingerprints the TLS
    handshake (JA3) to detect non-browser clients. Python's urllib uses
    OpenSSL and gets a 403 challenge regardless of how browser-shaped
    the headers are. curl on Windows uses schannel - the same TLS stack
    Edge/Chrome use - so the JA3 matches a real browser. Schannel also
    uses the system CA store, so cert validation matches the browser.

    `-sS` hides the progress meter while keeping error messages: with `-s`
    alone a failure produced an empty stderr, which surfaced as a bare
    "curl:" with nothing after it and left users unable to tell a blocked
    certificate check from an expired key.

    stdout stays bytes: the API responses are UTF-8, but Python's text mode
    would decode with the Windows locale (cp1252) and raise on any non-latin1
    byte (accented names, Japanese org names, emoji), silently failing the
    whole call.

    stderr is redacted here, at the single point where it enters the program:
    everything downstream (the log, the message shown to the user, the
    self-test report) is then safe to share by construction.

    A Python-side timeout is reported as curl's own timeout exit code. Letting
    subprocess.TimeoutExpired escape would both bypass this pipeline and print
    the command line - session key included - into the log and the UI.
    """
    try:
        result = subprocess.run(
            ['curl', '-sS'] + list(extra) + _curl_args(url, cookie, timeout, headers),
            capture_output=True, timeout=timeout + 5,
            creationflags=subprocess.CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        return CURL_OPERATION_TIMEDOUT, b'', f'curl did not return within {timeout}s'
    return (result.returncode, result.stdout,
            _redact(_first_line(_decode_console(result.stderr))))


def _curl_call(url, cookie, timeout=20, retry_transient=True, headers=(), data=None):
    """Fetch a claude.ai URL; return (headers, body) or raise. With `data`,
    a POST of that body (which must hold nothing secret: it is an argument).

    Shared by every call so the transport behaves identically everywhere.
    Two recoveries are built in, each attempted at most once:

    - the revocation lookup is dropped only when that lookup is what failed.
      Certificate chain, hostname and expiry checks stay enforced, and a
      working network never reaches this path.
    - a transient failure is simply repeated after a short pause: waking from
      standby or a reconnecting VPN routinely costs one request.

    The connection self-test passes retry_transient=False, since it reports
    what a single attempt does and a retry would mask the symptom. So does a
    request that changes something (a reset used): the answer of a lost
    attempt may still have spent it.
    """
    body_args = ['--data-raw', data] if data is not None else []
    extra = []
    tried_no_revoke = tried_transient = False
    while True:
        code, out, err = _curl_attempt(url, cookie, extra + body_args, timeout, headers)
        if code == 0:
            break
        if not tried_no_revoke and any(h in err.lower() for h in _REVOCATION_HINTS):
            tried_no_revoke = True
            extra = ['--ssl-no-revoke']
            wlog('NET    revocation check unavailable, retrying without it')
            continue
        if retry_transient and not tried_transient and code in _TRANSIENT_EXITS:
            tried_transient = True
            wlog(f'NET    transient failure (curl exit {code}), retrying once: {err}')
            time.sleep(TRANSIENT_RETRY_DELAY_S)
            continue
        wlog(f'NET    curl exit {code}: {err}')
        raise RuntimeError(_explain_curl_error(code, err))
    if tried_no_revoke:
        wlog('NET    request succeeded with the revocation check skipped')

    stdout = out.decode('utf-8', 'replace')
    # Split on the LAST header/body boundary and read the LAST status line, so
    # an interim (1xx) header block emitted before the final response does not
    # get mistaken for the body or the status.
    sep = '\r\n\r\n' if '\r\n\r\n' in stdout else '\n\n'
    headers, found, body = stdout.rpartition(sep)
    if not found:
        headers, body = '', stdout
    return headers or stdout, body.strip()


def _http_status(headers):
    """Last HTTP status code in a header dump, or None."""
    codes = re.findall(r'HTTP/[\d.]+ (\d+)', headers)
    return int(codes[-1]) if codes else None


def _curl_get(url, session_key):
    """Authenticated GET returning the response body, or raising."""
    headers, body = _curl_call(url, f'sessionKey={session_key}')
    code = _http_status(headers)
    if code is not None:
        if code in (401, 403):
            raise RuntimeError(t('key_invalid_or_expired'))
        if code >= 400:
            raise RuntimeError(f'HTTP {code}')
    if not body:
        raise RuntimeError(t('empty_response'))
    return body


# ── Limit resets (#14) ─────────────────────────────────────────────────────
# Anthropic grants resets now and then (a model launch, a promotion): a full
# one clears the 5-hour and weekly limits together, and some plans also get a
# reset of the 5-hour limit once a week, offered at that limit. claude.ai
# shows them under Settings > Usage; the usage answer carries them when asked
# with the query below, as `cedar_ember` (the grants of full resets) and
# `juniper_tide` (the 5-hour one), and both are null without it. Read from
# Claude Code 2.1.300, which uses the same blocks for /limit-reset, and
# measured on 2026-10-15. Only claude.ai may redeem one: the Claude Code token
# is answered "surface", so this takes the account's session key.
RESET_STATUS_URL = API_URL + '?at_wall=1&skip_spend=1'
RESET_CLAIM_URL = 'https://claude.ai/api/organizations/{}/reset_rate_limits'
RESET_PROGRAM_FULL = 'cedar_ember'
RESET_PROGRAM_SESSION = 'juniper_tide'
RESET_RESULTS = ('reset', 'already_used', 'not_limited', 'cooldown', 'ineligible',
                 'unavailable')
RESET_STATUS_REUSE_S = 60
# How long the outcome of a reset used stays on its card, through redraws and
# a page opened again.
RESET_NOTE_S = 300
_GRANT_ID = re.compile(r'^[a-z0-9_-]{1,40}$')      # what the server takes
# A reset that was not used, and the sentence that says why.
RESET_OUTCOME_KEYS = {
    'already_used': 'reset_r_already_used', 'not_limited': 'reset_r_not_limited',
    'cooldown': 'reset_r_cooldown', 'ineligible': 'reset_r_ineligible',
    'unavailable': 'reset_r_unavailable', 'rate_limited': 'reset_r_rate_limited',
    'auth': 'reset_r_auth', 'no_answer': 'reset_r_no_answer'}


def _iso_local(iso):
    """An ISO time from the server as a local datetime, or None."""
    try:
        return datetime.fromisoformat(str(iso).replace('Z', '+00:00')).astimezone()
    except (TypeError, ValueError):
        return None


def reset_when(iso):
    """A grant's end or a reset's time, as the account lines say a date:
    '23/10 01:00' ('10/23 01:00' in Japanese). '' for no time."""
    moment = _iso_local(iso)
    return f'{cc_day(moment)} {moment:%H:%M}' if moment else ''


def parse_resets(data):
    """The resets in a usage answer asked with RESET_STATUS_URL.

    {'grants': [...], 'next_grant': id or None, 'session': {...} or None,
    'weekly_resets_at': iso or None}. A grant is {'id', 'label', 'left',
    'total', 'ends_at', 'usable', 'needs_limit', 'clears'}, kept only with a
    reset left and an id the server would take back. `session` is the 5-hour
    reset, only for an account the server offers it to: {'available',
    'next_at', 'per_week'}."""
    out = {'grants': [], 'next_grant': None, 'session': None, 'weekly_resets_at': None}
    if not isinstance(data, dict):
        return out
    full = data.get('cedar_ember')
    if isinstance(full, dict):
        out['weekly_resets_at'] = full.get('weekly_resets_at')
        if full.get('eligible'):
            for g in full.get('grants') or []:
                if not isinstance(g, dict):
                    continue
                gid, left = g.get('id'), g.get('resets_left')
                if not (isinstance(gid, str) and _GRANT_ID.match(gid)):
                    continue
                if not isinstance(left, int) or isinstance(left, bool) or left <= 0:
                    continue
                total = g.get('resets_total')
                out['grants'].append({
                    'id': gid, 'label': str(g.get('label') or ''), 'left': left,
                    'total': total if isinstance(total, int) and total >= left else left,
                    'ends_at': g.get('ends_at'),
                    'usable': bool(g.get('usable_now')) and not g.get('paused'),
                    'needs_limit': bool(g.get('use_requires_limit')),
                    'clears': [c for c in g.get('clears') or [] if isinstance(c, str)]})
            nxt = full.get('next_grant_id')
            if any(g['id'] == nxt for g in out['grants']):
                out['next_grant'] = nxt
    session = data.get('juniper_tide')
    if isinstance(session, dict) and session.get('eligible'):
        per_week = session.get('resets_per_week')
        out['session'] = {'available': bool(session.get('available')),
                          'next_at': session.get('next_available_at'),
                          'per_week': per_week if isinstance(per_week, int) and per_week > 0 else 1}
    return out


def fetch_resets(session_key, org_id):
    """The resets claude.ai grants this account (parse_resets), or raising."""
    headers, body = _curl_call(RESET_STATUS_URL.format(org_id),
                               f'sessionKey={session_key}; lastActiveOrg={org_id}')
    code = _http_status(headers)
    if code in (401, 403):
        raise RuntimeError(t('key_invalid_or_expired'))
    if code is not None and code >= 400:
        raise RuntimeError(f'HTTP {code}')
    try:
        return parse_resets(json.loads(body))
    except ValueError:
        raise RuntimeError(t('empty_response'))


def claim_reset(session_key, org_id, program, grant_id=None):
    """Use a reset. Returns {'result', 'reason', 'left', 'cleared',
    'weekly_resets_at', 'next_at'}: `result` is one of RESET_RESULTS,
    'rate_limited' / 'auth' when claude.ai said so by its status, or
    'no_answer' when the outcome cannot be known (a server error, a result
    nobody knows). Raises when nothing came back: nobody knows either.

    One attempt only (see _curl_call). A full reset names its grant and
    carries a request id of its own, which is how the server tells a repeat
    from a second use (Claude Code sends the same three fields)."""
    body = {'program': program}
    if program == RESET_PROGRAM_FULL:
        if not (isinstance(grant_id, str) and _GRANT_ID.match(grant_id)):
            raise ValueError('a full reset needs the id of its grant')
        body['grant_id'] = grant_id
        body['request_id'] = uuid.uuid4().hex
    elif program != RESET_PROGRAM_SESSION:
        raise ValueError('unknown reset program')
    headers, text = _curl_call(RESET_CLAIM_URL.format(org_id),
                               f'sessionKey={session_key}; lastActiveOrg={org_id}',
                               retry_transient=False,
                               headers=('Content-Type: application/json',),
                               data=json.dumps(body))
    code = _http_status(headers)
    if code == 429:
        return {'result': 'rate_limited'}
    if code in (401, 403):
        return {'result': 'auth'}
    if code is not None and code >= 400:
        # A server error on a request that changes something says nothing
        # about whether it was done (a gateway timeout after the backend
        # committed): nobody knows. Its JSON error body was read as "not
        # available, nothing was used", which invited a second use (review of
        # 2.11, first iteration).
        return {'result': 'no_answer'}
    try:
        answer = json.loads(text)
    except ValueError:
        answer = None
    if not isinstance(answer, dict):
        raise RuntimeError(t('empty_response'))
    result = answer.get('result')
    left = answer.get('resets_left')
    # An outcome nobody knows (a new value, or none) is not "nothing was used".
    return {'result': result if result in RESET_RESULTS else 'no_answer',
            'reason': answer.get('reason'),
            'left': left if isinstance(left, int) and not isinstance(left, bool) else None,
            'cleared': [c for c in answer.get('cleared') or [] if isinstance(c, str)],
            'weekly_resets_at': answer.get('weekly_resets_at'),
            'next_at': answer.get('next_available_at')}


def plan_label(tier):
    """Human label for an org's rate_limit_tier ('default_claude_max_5x' ->
    'Max 5x'). Falls back to a title-cased form of any unknown tier."""
    if not tier:
        return ''
    s = tier.lower()
    if 'max_20x' in s:
        return 'Max 20x'
    if 'max_5x' in s:
        return 'Max 5x'
    if 'max' in s:
        return 'Max'
    if 'team' in s:
        return 'Team'
    if 'enterprise' in s:
        return 'Enterprise'
    if 'pro' in s:
        return 'Pro'
    if 'free' in s or s == 'default':
        return 'Free'
    return s.replace('default_', '').replace('claude_', '').replace('_', ' ').title()


def _org_uuid(o):
    return o.get('uuid') or o.get('id')


def _org_rank(o):
    """Rank an org for auto-selection: chat capability first, then a paid tier,
    then breadth of capabilities. Higher tuple sorts first. Used only as a
    tiebreaker when the browser's last-active org is unknown."""
    caps = set(o.get('capabilities') or [])
    tier = (o.get('rate_limit_tier') or '').lower()
    paid = any(k in tier for k in ('max', 'pro', 'team', 'enterprise'))
    return (1 if 'chat' in caps else 0, 1 if paid else 0, len(caps))


def fetch_account_info(session_key):
    """Resolve org_id plus the account identity (email, name, plan) from a
    session key.

    Returns {'org_id', 'email', 'name', 'plan', 'org_choices'}. Whenever the
    login can read more than one org, `org_choices` lists them all
    [{'id', 'name', 'tier'}], the default `org_id` first, for the caller to
    offer: a personal and a team org behind one login are two accounts
    (#16). `org_id` is None only when the browser's last-active org is
    unknown and none wins the ranking outright.

    Org selection strategy:
      1. List the user's orgs via /api/organizations.
      2. Keep only the orgs the widget can read usage for; fall back to the
         full list if none qualify.
      3. If exactly one remains, use it.
      4. Otherwise prefer the org /api/bootstrap reports as last active in the
         browser (account.lastActiveOrgId), matching what Claude.ai shows.
      5. If bootstrap gives nothing, rank by capability and auto-pick a clear
         winner; only on a genuine tie return org_id=None for the picker.

    The bootstrap call also carries the account's email and name, and the
    chosen org carries the rate-limit tier we turn into a plan label.
    """
    body = _curl_get('https://claude.ai/api/organizations', session_key)
    try:
        orgs = json.loads(body)
    except json.JSONDecodeError as e:
        raise RuntimeError(f'invalid response: {e}')
    # An error payload comes back as a dict ({"error": {...}} / {"detail": ...})
    # rather than a list: surface its message instead of the misleading "no
    # organization found".
    if isinstance(orgs, dict):
        err = orgs.get('error')
        msg = (err.get('message') if isinstance(err, dict) else err) or orgs.get('detail')
        raise RuntimeError(msg or t('key_invalid_or_expired'))
    if not isinstance(orgs, list) or not orgs:
        raise RuntimeError(t('no_org'))

    email = name = ''
    active_id = None
    try:
        boot = json.loads(_curl_get('https://claude.ai/api/bootstrap', session_key))
        acct = boot.get('account') or {}
        email = acct.get('email_address') or ''
        name = acct.get('full_name') or acct.get('display_name') or ''
        active_id = acct.get('lastActiveOrgId')
    except Exception as e:
        wlog(f'BOOT   bootstrap fallback failed: {e}')

    # Only orgs exposing 'chat' have usage the widget can read; a pure
    # Console-API org would leave every bar at 0. Keep the full list as a
    # fallback so a missing capabilities field never yields nothing.
    trackable = [o for o in orgs if 'chat' in (o.get('capabilities') or [])] or orgs
    ranked = sorted(trackable, key=_org_rank, reverse=True)

    def choices(first=None):
        """Every org the login can read, `first` at the top (stable sort)."""
        ordered = sorted(ranked, key=lambda o: _org_uuid(o) != first)
        return [{'id': _org_uuid(o), 'name': o.get('name') or _org_uuid(o),
                 'tier': o.get('rate_limit_tier') or ''} for o in ordered]

    def result(org, org_id=None):
        # One login can belong to a personal and a team organization (#16):
        # the default below is only a default, and the caller offers the
        # others, since each is an account of its own.
        oid = org_id or _org_uuid(org or {})
        return {'org_id': oid,
                'email': email, 'name': name,
                'plan': plan_label((org or {}).get('rate_limit_tier')),
                'org_choices': choices(oid) if len(trackable) > 1 else None}

    if len(trackable) == 1:
        return result(trackable[0])

    # Prefer the org Claude.ai itself currently routes to (authoritative even
    # if it is not in the listed orgs).
    if active_id:
        for o in orgs:
            if _org_uuid(o) == active_id:
                return result(o)
        return result(None, org_id=active_id)

    # No active org from bootstrap: rank the trackable orgs.
    if _org_rank(ranked[0]) == _org_rank(ranked[1]):
        wlog(f'ORG    ambiguous ({len(trackable)} orgs, no active) -> user picks')
        return {'org_id': None, 'email': email, 'name': name,
                'plan': '', 'org_choices': choices()}
    wlog(f'ORG    auto-picked by capability, 1 of {len(ranked)}')
    return result(ranked[0])


def org_account_name(info):
    """The name for a new account of a login that reads several orgs (#16):
    a team org's own name. A personal org is named after its owner ("x@y's
    Organization"), which says nothing the person's name does not, so it
    gives way to that."""
    org_name = (info or {}).get('org_name') or ''
    if org_name and not org_name.endswith("'s Organization"):
        return org_name
    return ''


def fetch_org_id(session_key):
    """Back-compat shim: just the org_id. Never returns None - resolves an
    ambiguous multi-org choice to the best-ranked candidate (no UI)."""
    info = fetch_account_info(session_key)
    if info['org_id']:
        return info['org_id']
    choices = info.get('org_choices') or []
    return choices[0]['id'] if choices else None


def scoped_model(d):
    """Third-bar data: the weekly per-model limit.

    Claude.ai moved this from d['seven_day_sonnet'] into the d['limits'] list,
    whose per-model entry carries scope.model.display_name (currently 'Fable',
    previously 'Sonnet'). Read that entry when present so the bar follows
    whatever model the weekly limit is scoped to, and fall back to the legacy
    sonnet bucket otherwise.

    Returns (percent, resets_at, model_name). model_name is None on the legacy
    path so the caller keeps its own localized label.
    """
    for lim in (d.get('limits') or []):
        model = (lim.get('scope') or {}).get('model') or {}
        name = model.get('display_name')
        if name:
            return lim.get('percent'), lim.get('resets_at'), name
    ss = d.get('seven_day_sonnet')
    if ss:
        return ss.get('utilization'), ss.get('resets_at'), None
    return None, None, None


def _rotated_key(headers, current):
    """The session key claude.ai issued in this response, or None.

    Two things this must not do, both of which cost the user their stored
    credential (see plausible_key):

    - read the value from anything other than a real Set-Cookie line. The
      substring turns up in diagnostic headers as well, and an unanchored
      search happily took `sessionKey=none` out of one of them.
    - accept a value that is not a key. The same cookie carries the server's
      way of ENDING a session, and `sessionKey=""` parsed as a two-character
      key that then replaced a working one.
    """
    for line in headers.splitlines():
        if not line.lower().startswith('set-cookie:'):
            continue
        match = re.search(r'sessionKey=("?)([^;\s"]*)\1', line)
        if match:
            new_key = match.group(2)
            if plausible_key(new_key):
                return new_key if new_key != current else None
            # Logged because this is the one place the strict check can be
            # wrong: if claude.ai ever changes the cookie's shape, every
            # rotation would be discarded and the only symptom, weeks later,
            # would be a key that expired. Length and prefix only.
            wlog(f'NET    ignored a session cookie that is not a key '
                 f'({len(new_key)} chars, prefix '
                 f'{"ok" if new_key.startswith(SESSION_KEY_PREFIX) else "no"})')
    return None


def read_claude_code_token():
    """(access token, plan) from Claude Code's credential file, or raise
    PermissionError with a message that says what to do."""
    try:
        with open(cc_accounts.cred_file(), encoding='utf-8') as f:
            oauth = json.load(f).get('claudeAiOauth') or {}
    except (OSError, ValueError):
        raise CCNoLogin()
    token = oauth.get('accessToken')
    if not token:
        raise CCNoLogin()
    return token, oauth.get('subscriptionType', '')


_CC_PROFILE_CACHE = {}


def fetch_claude_code_profile(force=False):
    """Who the Claude Code login on this machine belongs to.

    Cached against the token itself: the CLI renews the token often, while the
    identity behind it changes only when somebody else signs in, and that
    changes the token too. One login exists at a time, so a new token empties
    the cache rather than adding to it.
    """
    token, plan = read_claude_code_token()
    tail = token[-12:]
    if not force and tail in _CC_PROFILE_CACHE:
        return _CC_PROFILE_CACHE[tail]
    headers, body = _curl_call(
        CC_PROFILE_URL, None,
        headers=(f'Authorization: Bearer {token}',
                 'anthropic-beta: oauth-2025-04-20'))
    code = _http_status(headers)
    if code in (401, 403):
        raise CCLoginExpired()
    if code == 429:
        # Named here too, so that the caller can tell "the login does not
        # work" from "wait a moment": they are two different things.
        raise RateLimited(t('cc_rate_limited'))
    if code and code >= 400:
        raise RuntimeError(f'HTTP {code}')
    try:
        d = json.loads(body or '{}')
    except json.JSONDecodeError as e:
        raise RuntimeError(f'invalid response: {e}')
    acct, org = d.get('account') or {}, d.get('organization') or {}
    # Everything the details window can honestly show. The payload carries no
    # renewal or expiry date, and the dates it does carry (when the account was
    # opened, when the subscription started) say nothing a user needs, so they
    # are left out rather than shown for the sake of filling space.
    prof = {'org_id': org.get('uuid') or '',
            'email': acct.get('email_address') or acct.get('email') or '',
            'name': acct.get('display_name') or acct.get('full_name') or '',
            'org_name': org.get('name') or '',
            'org_type': org.get('organization_type') or '',
            'billing': org.get('billing_type') or '',
            'sub_status': org.get('subscription_status') or '',
            'extra_usage': bool(org.get('has_extra_usage_enabled')),
            'plan': plan_label(org.get('rate_limit_tier')) or plan}
    _CC_PROFILE_CACHE.clear()
    _CC_PROFILE_CACHE[tail] = prof
    return prof


def cc_http(url, headers):
    """The transport cc_accounts checks a profile with.

    Handed in rather than built there, so that module needs no network of its
    own and a bench can answer whatever it wants to try. It is the same curl
    path everything else uses: schannel, so Cloudflare sees a browser's TLS
    fingerprint, with the same redaction of anything curl prints.
    """
    try:
        headers_out, body = _curl_call(url, None, timeout=15,
                                       retry_transient=False,
                                       headers=tuple(headers))
    except Exception as e:
        wlog(f'CC     check could not be made: {_redact(str(e))}')
        return None, ''
    return _http_status(headers_out), body


# How the widget introduces itself to the token endpoint: as what it is. The
# endpoint answered curl's own agent with 429, and a named one normally
# (measured on 2026-09-30 with an invented token).
CC_AGENT = f'ClaudeUsageWidget/{APP_VERSION} (+https://github.com/niccolo-sabato/claude-usage-widget)'
# How long a quit, or an update, waits for a renewal, a switch or a check
# under way (cc_accounts.renewal_in_flight). Only a last resort: every step is
# bounded by its own timeouts, and the longest chain, a switch, adds up to
# about five minutes at worst (its identity reads, 2 x 20 s each; the
# subscription read; icacls, 15 s twice; the renewal's proof; cc_post, 25 s a
# run, twice, and again after invalid_scope; the outgoing identity). 30 s,
# then 180 s, were shorter than that (review of 2026-09-30, second and third
# iterations).
QUIT_RENEWAL_WAIT_S = 600


def cc_post(url, body, headers=()):
    """The transport cc_accounts renews a saved login with: (status or None,
    text).

    🔴 The body carries the renewal token, so curl reads it from its standard
    input and never finds it on its command line, where any process of the
    user could read it. The answer carries the new tokens: it is handed back
    and never logged.

    One retry only, without the revocation check and only when that check is
    what failed: the request has not left then. Any other failure is not
    tried again. The server may have renewed the token and the answer been
    lost, and asking again with the old token would turn "no answer" into a
    refusal of a login that the server had just renewed.
    """
    if cc_accounts.on_bench():
        # The credentials are fakes in a temporary folder (a bench): nothing
        # of theirs goes to the real server, whatever the bench forgot to
        # stand in for.
        wlog('CC     renewal not asked: these credentials belong to a bench')
        return None, ''
    args = ['curl', '-sS', '-X', 'POST', '--max-time', '20',
            '-H', 'Content-Type: application/json', '-H', f'User-Agent: {CC_AGENT}',
            '-o', '-', '-w', '\n%{http_code}']
    for h in headers:
        args += ['-H', h]
    args += ['--data-binary', '@-', url]
    tried_no_revoke = False
    while True:
        try:
            r = subprocess.run(args, input=body.encode('utf-8'), capture_output=True,
                               timeout=25, creationflags=subprocess.CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired:
            wlog('CC     renewal: no answer within 25 s')
            return None, ''
        except OSError as e:
            wlog(f'CC     renewal could not be asked: {_redact(str(e))}')
            return None, ''
        if r.returncode == 0:
            break
        err = _redact(_first_line(_decode_console(r.stderr)))
        if not tried_no_revoke and any(h in err.lower() for h in _REVOCATION_HINTS):
            tried_no_revoke = True
            args = args[:2] + ['--ssl-no-revoke'] + args[2:]
            continue
        wlog(f'CC     renewal: curl exit {r.returncode}: {err}')
        return None, ''
    text, _, code = r.stdout.decode('utf-8', 'replace').rpartition('\n')
    try:
        return int(code), text
    except ValueError:
        return None, ''


# 🔴 Claude Code's OAuth endpoint does not bear a widget that refreshes
# every 30 seconds: on 25 September it answered 429, and from then on every
# refresh tried again and earned another one. The numbers change slowly, so
# the answer is reused for this long instead of being asked for again.
CC_MIN_GAP_S = 120
_CC_USAGE_CACHE = {'key': None, 'at': 0.0, 'data': None}


class CCLoginElsewhere(PermissionError):
    """Claude Code on this computer is signed in as another account.

    Not an expired credential: nothing is wrong with the account on show, it
    simply is not the one the single Claude Code login belongs to right now.
    Named so the message can say that, instead of "session expired, renew the
    session key" on an account that has no session key at all (2026-09-25).
    """

    def __init__(self, email):
        self.email = email or '?'
        super().__init__(t('cc_other_account').format(email=self.email))


class CCLoginExpired(PermissionError):
    """The Claude Code login on this computer has expired.

    Named so the message can say so. As a plain PermissionError it was taken
    for an expired session key: "Session expired, renew your session key", on
    an account that has none, with a button that asked for one (review of
    2026-09-26). Claude Code renews its own login at the next command it runs.
    """

    def __init__(self):
        super().__init__(t('cc_token_expired'))


class CCNoLogin(PermissionError):
    """There is no Claude Code login on this computer: no file, or no token.

    Named, as CCLoginExpired is: as a plain PermissionError it was read as an
    expired session key, "Session expired" with a button to renew a key the
    account may not even have (review of 2026-09-26, second iteration).
    """

    def __init__(self):
        super().__init__(t('cc_no_creds'))


class CCNoOrg(RuntimeError):
    """The account reads through Claude Code but does not know its own
    organization, so no number read that way could be shown as its own."""

    def __init__(self):
        super().__init__(t('cc_no_org'))


class RateLimited(RuntimeError):
    """The endpoint asked us to slow down. It says nothing about the
    credential, so it must never be reported as an expired login."""


# How long the Claude Code reading is left alone after a 429. Measured on
# 2026-09-13: the OAuth usage endpoint answered 429 to every refresh for a
# whole session while the profile endpoint answered 200 for the same token, so
# the credential was fine and only the polling was too eager. Asking again a
# minute later keeps the limit tripped and the reading never comes back.
CC_RATE_LIMIT_PAUSE_S = 5 * 60
# organization -> until when its Claude Code reading is left alone. Per
# account: a rate limit on one login says nothing about another's, and one
# timestamp for everybody paused the account on show for the other's refusal
# (review of 2026-09-26).
_cc_paused = {}


def _cc_pause_until(acc):
    return _cc_paused.get((acc or {}).get('org_id') or '', 0.0)


class NoActivePlan(RuntimeError):
    """The account answered, and it has no plan to report limits for.

    `rotation` carries a session key claude.ai rotated on that same answer:
    raised before the return, the new key was thrown away, and a month later
    the account asked for a key to be pasted again (review of 2026-09-26).
    """

    def __init__(self, msg='', rotation=None):
        super().__init__(msg)
        self.rotation = rotation


def no_active_plan(data):
    """True when the answer is the one a lapsed subscription gives.

    🔴 Measured on 2026-09-21 against a real account whose subscription
    had lapsed: HTTP 200, every window null, `limits` an empty list. Shown as
    "not available", which is what the widget says about a value the server did
    not report, it read as a fault of the widget; and nothing anywhere said the
    plain fact, which is that the account has no plan any more.

    Only that exact shape counts. A missing key answers 401 and a fault answers
    nothing, and neither must ever be dressed up as "your subscription ended".
    """
    if not isinstance(data, dict):
        return False
    return (data.get('limits') == []
            and data.get('five_hour') is None
            and data.get('seven_day') is None)


def _cc_cache_key(token, expect_org):
    # The tail of the token, never the token: it tells the sessions apart
    # without carrying the secret around. The organization is part of the key
    # because an answer must NEVER be reusable for another account.
    return (token[-12:], expect_org or '')


def _live_org_hint():
    """The organization the live credentials file names, or None. A hint for
    choosing between "expired" and "another account" when the token cannot be
    asked about: the wrong choice only tries this account's saved login."""
    try:
        return cc_accounts.active().get('org')
    except Exception:
        return None


def _org_email(org):
    """The e-mail of an organization's saved login, for a message, or None."""
    try:
        return (cc_accounts.find_profile_for_org(org) or {}).get('email')
    except Exception:
        return None


def claude_code_token_expired():
    """True when the live Claude Code login's short token is known to have
    expired. Unknown counts as not expired: the server then says."""
    try:
        with open(cc_accounts.cred_file(), encoding='utf-8') as f:
            expires = (json.load(f).get('claudeAiOauth') or {}).get('expiresAt')
        return bool(expires) and int(expires) / 1000 <= time.time()
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def _windows_passed(data):
    """True when a usage answer describes a window that has already reset.

    Such an answer must not be served again from the reuse: its reset time is
    in the past, and every tick would take it for a reset just reached and
    refresh at once, for as long as the reuse lasts (review of 2026-09-26).
    """
    if not isinstance(data, dict):
        return False
    now = datetime.now(timezone.utc)
    stamps = [(data.get(k) or {}).get('resets_at') for k in ('five_hour', 'seven_day')]
    stamps += [x.get('resets_at') for x in (data.get('limits') or [])
               if isinstance(x, dict)]
    for stamp in stamps:
        if not stamp:
            continue
        try:
            when = datetime.fromisoformat(stamp)
        except (ValueError, TypeError):
            continue
        if when.tzinfo is not None and when <= now:
            return True
    return False


def fetch_usage_claude_code(retry_transient=True, expect_org=None, token=None):
    """Usage via the OAuth endpoint Claude Code itself uses. Same five_hour /
    seven_day shape as the claude.ai endpoint, so _on_data needs no change.

    `token` reads with a token the caller already vouches for (the saved copy
    of an account's login, see saved_login_token) instead of the live one.

    An expired login is CCLoginExpired, never a plain PermissionError: that
    one means "renew the session key", which is not what is wrong. A login
    known to have expired is not even sent: the server would refuse it on
    every refresh, and Claude Code renews it at its next command.
    """
    if token is None:
        token, _ = read_claude_code_token()
        if expect_org:
            # The token follows whoever signed in last. Reading it for an
            # account it does not belong to would put one account's numbers
            # under another account's name (issue #11). Asked from what is
            # already known first, so an expired login of ANOTHER account is
            # still reported as elsewhere, not as expired.
            known = _CC_PROFILE_CACHE.get(token[-12:])
            if known and known.get('org_id') and known['org_id'] != expect_org:
                raise CCLoginElsewhere(known.get('email'))
        if expect_org and claude_code_token_expired():
            # An expired token cannot be asked about, but the live file's own
            # key, written by the switch that put the login there, can say
            # whose it is. Another account's expired login is "elsewhere", so
            # this account's saved login is tried: reported as this account's
            # "login expired", an account with a good saved login looked broken
            # for hours after a switch (review of 2026-09-26).
            other = _live_org_hint()
            if other and other != expect_org:
                raise CCLoginElsewhere(_org_email(other))
        if claude_code_token_expired():
            raise CCLoginExpired()
        if expect_org:
            prof = fetch_claude_code_profile()
            if prof.get('org_id') and prof['org_id'] != expect_org:
                raise CCLoginElsewhere(prof.get('email'))
    cache_key = _cc_cache_key(token, expect_org)
    cached = _CC_USAGE_CACHE['data']
    if (_CC_USAGE_CACHE['key'] == cache_key
            and time.time() - _CC_USAGE_CACHE['at'] < CC_MIN_GAP_S
            and not _windows_passed(cached)):
        if no_active_plan(cached):
            raise NoActivePlan(t('plan_inactive'))
        return cached
    headers, body = _curl_call(
        CC_USAGE_URL, None, retry_transient=retry_transient,
        headers=(f'Authorization: Bearer {token}', 'anthropic-beta: oauth-2025-04-20'))
    code = _http_status(headers)
    if code in (401, 403):
        raise CCLoginExpired()
    if code == 429:
        raise RateLimited(t('cc_rate_limited'))
    if code and code >= 400:
        raise RuntimeError(f'HTTP {code}')
    if not body:
        raise RuntimeError(t('empty_response'))
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise RuntimeError(f'invalid response: {e}')
    # Kept before the lapsed check: a lapsed account asked at every refresh
    # was exactly the storm the reuse exists to stop.
    _CC_USAGE_CACHE.update(key=cache_key, at=time.time(), data=data)
    if no_active_plan(data):
        raise NoActivePlan(t('plan_inactive'))
    return data


def saved_login_token(org):
    """The access token of this account's saved login, while it is still good.

    🔴 There is one Claude Code login per computer. When it moves to
    another account, an account that has only that login cannot be read the
    usual way - which is what the user met on 2026-09-25, as "session expired"
    and an empty bar. But the switch saved this account's login, and its short
    token stays valid for hours (only the account in use has it renewed). A
    read with it rotates nothing and spends nothing. None once it has expired,
    or when there is no saved login for the organization.

    Light, because it runs on the refresh thread every time Claude Code is
    signed in as another account: it reads the saved profiles and nothing else
    (it used to walk the live file, the identity cache and every historic copy
    on every tick). And before its numbers go under this account's name, the
    token is confirmed once to be this organization's: a profile labelled from
    the lagging cache, by an older build or by another tool, would otherwise
    show one account's usage as the other's. The answer is kept per token.
    When the server cannot be asked, the label is trusted rather than the bar
    emptied on a guess, and the question is asked again ten minutes later.
    """
    if not org:
        return None
    try:
        token, name = cc_accounts.usable_access_token(org)
    except Exception as e:
        wlog(f'FETCH  saved login unreadable: {_redact(str(e))}')
        return None
    if not token:
        return None
    key = cc_accounts.fingerprint(token)
    said = _SAVED_TOKEN_ORG.get(key)
    if said is None or (said[0] is None and time.time() - said[1] > 600):
        try:
            said = (cc_accounts.ask_server_org(token, cc_http), time.time())
        except Exception:
            said = (None, time.time())
        _SAVED_TOKEN_ORG[key] = said
    if said[0] and said[0] != org:
        wlog(f'FETCH  the saved login "{name}" belongs to another organisation, '
             f'not using it')
        return None
    return token


# token fingerprint -> (organisation the server named, or None; when asked)
_SAVED_TOKEN_ORG = {}


def _fetch_usage_key(src):
    """The session-key read. `src` is the active account, or the config itself
    for a layout old enough not to have one.

    The key is read once, before the request: `src` is the account's own
    dict, and a key saved by hand while the request was out was read after
    it as the key the request had used, so a rotation of the old key reached
    the new one and the accounts holding it (review of 2.11, second
    iteration)."""
    key, org, acct_id = src['session_key'], src['org_id'], src.get('id')
    url = API_URL.format(org)
    cookie = f"sessionKey={key}; lastActiveOrg={org}"
    headers, body = _curl_call(url, cookie)
    if not body:
        raise RuntimeError(t('empty_response'))
    code = _http_status(headers)
    if code is not None:
        if code in (401, 403):
            raise PermissionError(t('session_expired_short'))
        if code >= 400:
            raise RuntimeError(f'HTTP {code}')
    rotation = None
    new_key = _rotated_key(headers, key)
    if new_key:
        # With the key the request carried: only accounts still holding it
        # take the new one (_apply_rotated_key).
        rotation = (acct_id, new_key, key)
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise RuntimeError(f'invalid response: {e}')
    if no_active_plan(data):
        raise NoActivePlan(t('plan_inactive'), rotation=rotation)
    return data, rotation


def fetch_usage(cfg):
    """Fetch usage data for the active account. See fetch_org_id for why curl.

    Returns (data, rotation): rotation is None, or (account_id, new_key,
    used_key) when
    Claude.ai rotated the session key. The persist is deliberately NOT done
    here: this runs on the refresh worker thread, and writing cfg off-thread
    could (a) serialise the dict while the UI thread mutates it and (b) land
    the new key on the wrong account if the user switched accounts mid-fetch.
    The caller applies the rotation on the main thread, targeting the exact
    account the key was issued for (see Widget._apply_rotated_key).

    An account may hold both credentials. They are tried in preference order,
    and falling back is silent on purpose: a token that expired while a good
    key is on file is not something the user has to act on, and saying so would
    train them to ignore the message that does matter. Only when nothing is
    left does the error of the preferred method surface.
    """
    acc = active_account(cfg)
    methods = account_methods(acc) if acc is not None else ()
    if not methods:
        # Deliberately weaker than plausible_key, which gates what may be
        # WRITTEN: here a wrong call would lock the user out of a widget that
        # works, so only a value that cannot be a credential at all counts.
        if acc is None and str(cfg.get('session_key') or '').startswith(SESSION_KEY_PREFIX):
            return _fetch_usage_key(cfg)
        wlog('FETCH  no usable credential stored, asking for one')
        raise PermissionError(t('session_expired_short'))
    errors = {}
    for method in methods:
        # 🔴 The pause ALWAYS holds, even when it is the only way to read.
        # It used to be respected only when there was another credential:
        # with the Claude Code login alone the widget called the endpoint
        # already limited at every refresh, got another 429, and showed the
        # error over and over (25 Sep 2026, once every 20 seconds). Better
        # not to call, and to say when it will try again.
        if method == AUTH_CC and time.time() < _cc_pause_until(acc):
            continue
        try:
            if method == AUTH_CC:
                org = acc.get('org_id')
                if not org:
                    # Without an organization there is no knowing WHOSE the
                    # numbers coming back would be, and showing them under
                    # the wrong name is worse than not showing them.
                    wlog('FETCH  claude_code has no organisation on this '
                         'account, not using it')
                    # With a reason of its own: skipped in silence, it left
                    # no error behind and the widget said "rate limited".
                    errors[method] = CCNoOrg()
                    continue
                try:
                    return fetch_usage_claude_code(expect_org=org), None
                except CCLoginElsewhere as elsewhere:
                    # Claude Code has moved to another account: read this one
                    # with the copy of its login saved at the switch, while
                    # that copy lasts. When it does not, the reason stays the
                    # true one - never "session expired".
                    saved = saved_login_token(org)
                    if not saved:
                        raise
                    try:
                        return fetch_usage_claude_code(expect_org=org,
                                                       token=saved), None
                    except PermissionError:
                        raise elsewhere
            return _fetch_usage_key(acc)
        except RateLimited as e:
            if method == AUTH_CC:
                org_key = acc.get('org_id') or ''
                # A pause already running is not stretched: every attempt
                # used to push it forward, and the widget never came out of it.
                if time.time() >= _cc_paused.get(org_key, 0.0):
                    _cc_paused[org_key] = time.time() + CC_RATE_LIMIT_PAUSE_S
                    wlog(f'FETCH  {method} is rate limited, leaving it alone for '
                         f'{CC_RATE_LIMIT_PAUSE_S // 60} minutes')
                # With the time in it from the very first refusal: the generic
                # sentence went on screen once per start before the timed one
                # took over, and it was the one the user read.
                e = RateLimited(t('cc_rate_limited_until').format(
                    when=datetime.fromtimestamp(_cc_paused[org_key]).strftime('%H:%M')))
            errors.setdefault(method, e)
        except Exception as e:
            # The reason is logged, not just the fact: a reading that quietly
            # falls back every minute looks like a working widget, and without
            # the reason nobody can tell a rate limit from an expired token.
            if len(methods) > 1:
                wlog(f'FETCH  {method} did not answer ({_redact(str(e))}), '
                     f'trying the other way')
            errors.setdefault(method, e)
    key_error = errors.get(AUTH_KEY)
    if isinstance(key_error, (PermissionError, NoActivePlan)):
        # An expired session key is the one thing the user can act on at once,
        # so it is said even when Claude Code has a reason of its own: behind
        # "signed in as another account" it went unnoticed, and the key was
        # the only way left to read this account (review of 2026-09-26).
        # A lapsed plan the key read is said first too: it is the account's
        # state, whatever Claude Code says, and it carries the session key
        # claude.ai rotated on that answer, lost when another error was
        # raised in its place (third iteration).
        raise key_error
    for method in methods:
        if method in errors:
            raise errors[method]
    # Every method was skipped, which can only be the paused one. The
    # message carries the time: "soon" says nothing to someone reading it
    # for the tenth time in two minutes; and only when it is a real time,
    # still to come.
    until = _cc_pause_until(acc)
    if until > time.time():
        raise RateLimited(t('cc_rate_limited_until').format(
            when=datetime.fromtimestamp(until).strftime('%H:%M')))
    raise RateLimited(t('cc_rate_limited'))


# ═══════════════════════════════════════════════════════
# Connection self-test
# ═══════════════════════════════════════════════════════
#
# One place to look when the widget cannot reach claude.ai. The checks follow
# the path a request actually takes - is curl there, does the name resolve,
# does the handshake complete, is anything intercepting it, is the key still
# good - so the first failing line is the cause rather than a symptom.
#
# The report is written to be pasted into a bug report, which is exactly why
# it carries no credentials: see _redact.

CLAUDE_ORIGIN = 'https://claude.ai/'
SELFTEST_DETAIL_MAX = 300


def _selftest_curl():
    """Is curl there, and does it use the Windows certificate store?

    A curl earlier in PATH (MSYS2, conda, or a tool shipping its own) may
    validate against its own CA bundle instead of Schannel, so certificates
    Windows trusts - a corporate root, an antivirus root - are rejected on a
    machine where the browser works fine.
    """
    try:
        result = subprocess.run(['curl', '-V'], capture_output=True, timeout=10,
                                creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        # A missing curl surfaces as WinError 2, which says nothing useful.
        return 'fail', t('selftest_curl_missing')
    line = (_decode_console(result.stdout).splitlines() or [''])[0]
    if result.returncode != 0 or not line:
        return 'fail', t('selftest_curl_missing')
    if 'schannel' in line.lower():
        return 'ok', line
    return 'warn', f"{t('selftest_curl_backend')} ({line})"


def _selftest_dns():
    """Does claude.ai resolve? Filtering DNS fails here, before any TLS."""
    try:
        infos = socket.getaddrinfo('claude.ai', 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        # Not "check your connection": the rest of the internet works fine for
        # someone whose resolver is the thing filtering claude.ai.
        return 'fail', f"{t('selftest_dns_failed')} ({e})"
    addresses = sorted({i[4][0] for i in infos})
    shown = ', '.join(addresses[:3])
    if len(addresses) > 3:
        shown += ', \u2026'
    return 'ok', shown


def _selftest_tls(skip_revocation=False):
    """Complete a handshake with claude.ai, with no credentials attached.

    Run twice when the first attempt fails: if dropping the revocation lookup
    is what makes it work, the certificate is fine and the network is blocking
    the CA's OCSP/CRL endpoint - a different problem, with a different fix,
    than a certificate that cannot be trusted at all.
    """
    extra = ['-o', os.devnull]  # keep the header dump, discard the page body
    if skip_revocation:
        extra.append('--ssl-no-revoke')
    code, out, err = _curl_attempt(CLAUDE_ORIGIN, '', extra)
    if code != 0:
        # Same explanation the widget shows anywhere else, so the diagnosis
        # reads in the user's language and not only in curl's.
        return 'fail', _explain_curl_error(code, err)
    status = _http_status(out.decode('utf-8', 'replace'))
    return 'ok', f"{t('selftest_tls_ok')} (HTTP {status})" if status else t('selftest_tls_ok')


# A proxy value may carry a 'scheme://' or a Windows 'protocol=' prefix in
# front of its credentials. Anchored, so it can only match an actual prefix.
_PROXY_PREFIX = re.compile(r'^(?:[A-Za-z][\w+.-]*(?:://|=))?')


def _mask_userinfo(value):
    """Drop any user:password@ from a proxy value.

    Done by shape rather than left to _redact, whose patterns cannot recognise
    credentials in front of a dotless intranet host name. Windows can list one
    proxy per protocol (`http=host;https=host`), so each part is masked while
    its `scheme://` or `protocol=` prefix is kept.
    """
    def mask(part):
        head, sep, host = part.rpartition('@')   # last @: a password may hold one
        if not sep:
            return part
        # Only a prefix counts as a prefix. Searching the whole head for '='
        # would find the padding of a base64 password and keep it.
        prefix = _PROXY_PREFIX.match(head).group(0)
        return f'{prefix}<redacted>@{host}'

    value = str(value)
    parts = value.split(';')
    if len(parts) > 1 and not all(_PROXY_PREFIX.match(p).group(0) for p in parts):
        parts = [value]     # not the per-protocol form: a ';' inside one value
    return ';'.join(mask(p) for p in parts)


def _selftest_proxy():
    """Report any proxy in the way. Not an error - just the usual suspect
    behind an untrusted certificate, since a proxy re-signs what it inspects."""
    found = []
    for var in ('HTTPS_PROXY', 'HTTP_PROXY', 'ALL_PROXY'):
        value = os.environ.get(var) or os.environ.get(var.lower())
        if value:
            found.append(f'{var}={_mask_userinfo(value)}')
    try:
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r'Software\Microsoft\Windows\CurrentVersion\Internet Settings') as key:
            if winreg.QueryValueEx(key, 'ProxyEnable')[0]:
                system = _mask_userinfo(winreg.QueryValueEx(key, 'ProxyServer')[0])
                found.append(f'system={system}')
    except OSError:
        pass
    if not found:
        return 'ok', t('selftest_proxy_none')
    return 'warn', f"{t('selftest_proxy_found')}: {'; '.join(found)}"


def _selftest_curlrc():
    """Report curl config files, which apply to every curl run on the machine.

    Only the presence and the directives that change TLS behaviour are
    reported: the file itself can hold credentials, so it is never quoted.
    """
    found = []
    directives = []
    for token, base in (('%CURL_HOME%', os.environ.get('CURL_HOME')),
                        ('%APPDATA%', os.environ.get('APPDATA')),
                        ('%USERPROFILE%', os.environ.get('USERPROFILE'))):
        if not base:
            continue
        for name in ('_curlrc', '.curlrc'):
            path = os.path.join(base, name)
            label = f'{token}\\{name}'
            if not os.path.isfile(path) or label in found:
                continue
            found.append(label)
            try:
                with open(path, encoding='utf-8', errors='replace') as f:
                    content = f.read().lower()
            except OSError:
                continue
            for directive in ('insecure', 'ssl-no-revoke', 'proxy', 'cacert'):
                if directive in content and directive not in directives:
                    directives.append(directive)
    if not found:
        return 'ok', t('selftest_curlrc_none')
    detail = f"{t('selftest_curlrc_found')}: {', '.join(found)}"
    if directives:
        detail += f" [{', '.join(directives)}]"
    return 'warn', detail


def _selftest_api(cfg):
    """The end-to-end check: the usage endpoint, with the configured key."""
    account = active_account(cfg) or {}
    if AUTH_CC in account_methods(account):
        try:
            fetch_usage_claude_code(retry_transient=False,
                                    expect_org=account.get('org_id'))
        except CCLoginElsewhere as e:
            # Claude Code is on another account: the widget reads this one
            # with its saved login while that lasts, and so does the check.
            # It used to report "session key rejected" (review of 2026-09-26).
            saved = saved_login_token(account.get('org_id'))
            if not saved:
                return 'fail', _redact(str(e))
            try:
                fetch_usage_claude_code(retry_transient=False,
                                        expect_org=account.get('org_id'), token=saved)
            except Exception as e2:
                return 'fail', _redact(str(e2))
        except (CCLoginExpired, CCNoLogin) as e:
            return 'fail', str(e)
        except PermissionError:
            return 'fail', t('selftest_api_key_rejected')
        except Exception as e:
            return 'fail', str(e)
        return 'ok', t('selftest_api_ok')
    key = account.get('session_key') or cfg.get('session_key')
    org_id = account.get('org_id') or cfg.get('org_id')
    if not key or not org_id:
        return 'skip', t('selftest_api_no_account')
    headers, body = _curl_call(API_URL.format(org_id),
                               f'sessionKey={key}; lastActiveOrg={org_id}',
                               retry_transient=False)
    status = _http_status(headers)
    if status in (401, 403):
        return 'fail', t('selftest_api_key_rejected')
    if status and status >= 400:
        return 'fail', f'HTTP {status}'
    try:
        json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return 'fail', t('selftest_api_bad_body')
    return 'ok', t('selftest_api_ok')


def run_connection_selftest(cfg, on_step=None):
    """Run every check in request order and return the results.

    Each result is {'key', 'label', 'status', 'detail'} with status in ok /
    warn / fail / skip. on_step(step) fires as each check finishes so the
    dialog can fill in progressively instead of freezing until the last one
    returns.
    """
    steps = []

    def add(key, check):
        label = t('selftest_step_' + key)
        try:
            status, detail = check()
        except Exception as e:
            status, detail = 'fail', str(e)
        detail = _redact(detail)
        if len(detail) > SELFTEST_DETAIL_MAX:
            detail = detail[:SELFTEST_DETAIL_MAX].rstrip() + '\u2026'
        step = {'key': key, 'label': label, 'status': status, 'detail': detail}
        steps.append(step)
        wlog(f'SELFTEST {status:4} {label}: {detail}')
        if on_step:
            on_step(step)
        return step

    add('curl', _selftest_curl)
    add('dns', _selftest_dns)
    handshake = add('tls', _selftest_tls)

    def without_revocation():
        if handshake['status'] == 'ok':
            return 'skip', t('selftest_revoke_not_needed')
        status, detail = _selftest_tls(skip_revocation=True)
        if status != 'ok':
            return status, detail
        # Succeeding here is the diagnosis, not a clean result - but only if
        # the revocation lookup is what failed first. Otherwise the first
        # attempt simply hit a blip, and saying otherwise would send the user
        # after a problem they do not have.
        if any(h in handshake['detail'].lower() for h in _REVOCATION_HINTS):
            return 'warn', t('selftest_revoke_worked')
        return 'warn', t('selftest_revoke_inconsistent')

    add('revoke', without_revocation)
    add('proxy', _selftest_proxy)
    add('curlrc', _selftest_curlrc)
    add('api', lambda: _selftest_api(cfg))
    return steps


def selftest_verdict(steps):
    """Overall result: ok / warn / fail / partial.

    The end-to-end check against the usage endpoint decides the headline:

    - it succeeded, so whatever failed upstream was recovered from (the
      handshake that only works once the revocation lookup is dropped): a
      warning, not a broken connection;
    - it never ran because no account is configured: 'partial', since a green
      "everything works" would be a claim nothing verified.
    """
    api = next((s['status'] for s in steps if s.get('key') == 'api'), None)
    worst = ('fail' if not steps or any(s['status'] == 'fail' for s in steps)
             else 'warn' if any(s['status'] == 'warn' for s in steps)
             else 'ok')
    if worst == 'fail' and api == 'ok':
        return 'warn'
    if worst == 'ok' and api != 'ok':
        return 'partial'
    return worst


def selftest_report(steps):
    """Plain-text report for the clipboard. Credential-free by construction."""
    win = sys.getwindowsversion()
    # Windows 11 still reports itself as 10.0; the build number is what tells
    # them apart, and a report that says "Windows 10" on a Windows 11 machine
    # sends every bug report off on the wrong foot.
    name = '11' if win.build >= 22000 else '10'
    lines = [f'Claude Usage Widget {APP_VERSION} - connection self-test',
             f'Windows {name} ({win.major}.{win.minor}.{win.build})'
             f' | language: {_current_lang}',
             '']
    # English labels whatever the UI language: this text is written to be
    # pasted into an issue tracker where reports have to be comparable. The
    # header still records the language the details were produced in.
    lines += [f"[{s['status'].upper():4}] "
              f"{LANG['en'].get('selftest_step_' + s.get('key', ''), s['label'])}: "
              f"{s['detail']}" for s in steps]
    return _redact('\n'.join(lines))


# ═══════════════════════════════════════════════════════
# Auto-update
# ═══════════════════════════════════════════════════════

def _version_tuple(v):
    """Parse 'v2.8.0' / '2.8.0' into (2, 8, 0); returns (0,) on failure."""
    if not v:
        return (0,)
    v = v.strip().lstrip('vV')
    parts = []
    for chunk in v.split('.'):
        m = re.match(r'\d+', chunk)
        if not m:
            break
        parts.append(int(m.group(0)))
    return tuple(parts) if parts else (0,)


def _http_get(url, timeout=15, accept=None):
    """Simple HTTPS GET using urllib. Returns (status, bytes). Raises URLError on network failure."""
    req = urllib.request.Request(
        url,
        headers={
            'User-Agent': f'ClaudeUsageWidget/{APP_VERSION} (+{UPDATE_RELEASES_URL})',
            **({'Accept': accept} if accept else {}),
        },
    )
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.status, resp.read()


def check_latest_release():
    """Query GitHub for the latest release metadata.

    Returns a dict {'version', 'tag', 'body', 'asset_url', 'asset_size', 'html_url'}
    or None if the API call fails (network error, rate limit, etc.).
    """
    try:
        status, raw = _http_get(UPDATE_API_URL, accept='application/vnd.github+json')
        if status != 200:
            wlog(f'UPDATE check HTTP {status}')
            return None
        data = json.loads(raw.decode('utf-8'))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, OSError) as e:
        wlog(f'UPDATE check failed: {e}')
        return None
    tag = data.get('tag_name') or ''
    if data.get('draft') or data.get('prerelease'):
        return None
    asset_url = None
    asset_size = 0
    for a in data.get('assets') or []:
        if a.get('name') == UPDATE_ASSET_NAME:
            asset_url = a.get('browser_download_url')
            asset_size = a.get('size') or 0
            break
    return {
        'version': tag.lstrip('vV'),
        'tag': tag,
        'body': (data.get('body') or '').strip(),
        'asset_url': asset_url,
        'asset_size': asset_size,
        'html_url': data.get('html_url') or UPDATE_RELEASES_URL,
    }


def is_newer_version(latest, current=APP_VERSION):
    """True if `latest` is a semver-style version strictly newer than `current`."""
    return _version_tuple(latest) > _version_tuple(current)


def download_installer(url, dest_path, on_progress=None, chunk_size=65536):
    """Download the installer to `dest_path`, calling on_progress(downloaded, total).

    Returns the final path on success. Writes to a temp file and renames atomically
    so a partial download cannot be mistaken for a complete one. Raises on failure.
    """
    if not url:
        raise ValueError('no asset URL')
    tmp_path = dest_path + '.part'
    req = urllib.request.Request(
        url,
        headers={'User-Agent': f'ClaudeUsageWidget/{APP_VERSION}'},
    )
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
        total = int(resp.headers.get('Content-Length') or 0)
        downloaded = 0
        with open(tmp_path, 'wb') as f:
            while True:
                buf = resp.read(chunk_size)
                if not buf:
                    break
                f.write(buf)
                downloaded += len(buf)
                if on_progress:
                    try:
                        on_progress(downloaded, total)
                    except Exception:
                        pass
    # Reject a truncated download before it is renamed and executed elevated:
    # a dropped connection ends the read loop cleanly, leaving a short file.
    if downloaded == 0 or (total and downloaded != total):
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise IOError(f'incomplete download: {downloaded}/{total or "?"} bytes')
    if os.path.exists(dest_path):
        try:
            os.remove(dest_path)
        except OSError:
            pass
    os.replace(tmp_path, dest_path)
    return dest_path


# ═══════════════════════════════════════════════════════
# Usage Section
# ═══════════════════════════════════════════════════════

class Section:
    """One usage bar: header (label + pct) + bar canvas + sub-label."""

    def __init__(self, parent, label, fill, track):
        # fill = used portion; track = unused portion (shown only when pct > 0,
        # otherwise the bar keeps the neutral gray).
        self._fill = fill
        self._track = track
        self._dynamic = False        # percentage-driven palette (set by widget)
        self._pct = 0
        self._color = fill
        self._trackc = track         # the track colour actually drawn
        self._compact = False
        self._cd_txt = ''  # countdown text appended to pct
        self._err = False  # a failed refresh, said under the bar not inside it
        self._dot_phase = 'off'      # 'off' | 'on' (pre-refresh breathing dot)
        self._dot_level = 1.0        # breathing brightness 0..1 (0 = invisible)
        self._dot_bg = BAR_BG        # colour behind the dot, for the fade-out
        self._dot_img = None         # keeps the current dot PhotoImage from GC
        self._reset_level = RESET_FULL   # width of the reset sub-label
        self._resets_at = None           # last reset time, to re-render at another width
        self._state = 'empty'            # 'empty' | 'missing' | 'ok' (see _render_reset)
        self._split = 0                  # 0 = whole; else the equal parts shown
        self._no_number = False          # a preview with no reading: no number drawn

        self.frame = tk.Frame(parent, bg=BG)
        self.frame.pack(fill='x', padx=PAD, pady=(3, 0))

        self.hdr = tk.Frame(self.frame, bg=BG)
        self.hdr.pack(fill='x')
        self.lbl = tk.Label(self.hdr, text=label, font=FT, fg=FG, bg=BG)
        self.lbl.pack(side='left')
        self.lbl_info = tk.Label(self.hdr, text='', font=FT_S, fg=DOT_GREEN, bg=BG)
        self.lbl_info.pack(side='right')

        self.cv = tk.Canvas(self.frame, height=BAR_H, bg=BG,
                            highlightthickness=0, bd=0)
        self.cv.pack(fill='x', pady=(1, 0))
        self.cv.bind('<Configure>', lambda e: self._draw(e.width))

        self.lbl_sub = tk.Label(self.frame, text='', font=FT_S, fg=DIM, bg=BG,
                                anchor='w', padx=6, pady=0, bd=0,
                                highlightthickness=0)
        self.lbl_sub.pack(fill='x', pady=(2, 0))

    def set_compact(self, compact):
        self._compact = compact
        if compact:
            self.hdr.pack_forget()
        else:
            self.cv.pack_forget()
            self.lbl_sub.pack_forget()
            self.hdr.pack(fill='x')
            self.cv.pack(fill='x', pady=(1, 0))
            self.lbl_sub.pack(fill='x', pady=(2, 0))
        self._draw(self.cv.winfo_width())

    def set_countdown(self, txt):
        """Set countdown text shown after pct (e.g. '19:24 (270s)')."""
        self._cd_txt = txt
        self.lbl_info.config(text=txt)
        self._draw(self.cv.winfo_width())

    def set_dot_phase(self, phase):
        """Show/hide the pre-refresh breathing dot ('off' | 'on').

        The breathing brightness itself is driven by the Widget via
        set_dot_level; this only flips visibility and redraws.
        """
        if phase == self._dot_phase:
            return
        self._dot_phase = phase
        self._draw(self.cv.winfo_width())

    def set_dot_level(self, level):
        """Set breathing brightness 0..1 (0 = faded into the bar background,
        i.e. invisible) and recolour the dot in place without a full redraw."""
        self._dot_level = level
        if self._dot_phase == 'on':
            q = round(level * 12) / 12  # quantise so the image cache stays small
            color = _lerp_hex(self._dot_bg, DOT_GREEN, q)
            self._dot_img = _dot_image(DOT_DIAM, color)
            try:
                self.cv.itemconfigure('refresh_dot', image=self._dot_img)
            except Exception:
                pass

    def set_reset_level(self, level):
        """Redraw the reset label at a different width, from the value already
        held, so a window resize does not have to wait for the next fetch."""
        if level == self._reset_level:
            return
        self._reset_level = level
        self._render_reset()

    def clear(self):
        """Back to no value at all, as before the first reading.

        🔴 For a change of account. The bars used to keep the numbers of
        the account shown before until the new one answered - and when it did
        not, they kept them for good, with "error" underneath: one account's
        usage under another account's name (2026-09-25).
        """
        self._pct = 0
        self._resets_at = None
        self._state = 'empty'
        self._err = False
        self._color = BAR_BG
        self.lbl_sub.config(text='')
        self._draw(self.cv.winfo_width())

    def set_error(self, on, word=None):
        """Say (or stop saying) that the last refresh failed.

        It goes on the line under the bar, in place of the reset, because that
        is where the bar's words live and because the bar's interior is what
        every strip has to reserve room for. `word` replaces "error" when that
        is not what happened (a subscription that has lapsed).
        """
        word = word if on else None
        if on == self._err and word == getattr(self, '_err_word', None):
            return
        self._err = on
        self._err_word = word
        self._render_reset()

    def redraw_reset(self):
        """Re-render the sub-label after a change in what it should carry. The
        level governs how much room the text may take; this governs which parts
        of it exist at all."""
        self._render_reset()

    def reset_text(self, level=None):
        """The reset label as it would read at `level` (default: the current
        one). Used to measure a form before deciding to switch to it."""
        return format_reset(self._resets_at,
                            self._reset_level if level is None else level)

    def reset_display(self, level=None):  # noqa: D401 - see below
        """Exactly what _render_reset would write at `level`.

        The floor is measured from this and not from reset_text, because two of
        the three states draw a word instead of a time and neither of them goes
        through format_reset. Measuring the time alone let the strip shrink
        until those words ran under the controls.
        """
        if self._err:
            # Ahead of everything else, including the user's choice of what
            # this line carries: a refresh that failed is not a reset time.
            return getattr(self, '_err_word', None) or t('error')
        if self._state == 'empty':
            return self.lbl_sub.cget('text')
        if self._state == 'missing':
            # Said whatever the user chose to see: the setting governs which
            # parts of a RESET TIME to show, and this is not one, it is the
            # widget admitting it has no value for this limit. Silencing it
            # made an unreported limit look pixel-identical to a real 0%, in
            # the bar and in the hover tip alike.
            return t('not_available')
        if not (_reset_parts['time'] or _reset_parts['left']):
            # Asked for nothing under the bar. Distinct from the state above:
            # here there IS a value, the user just does not want the reset
            # spelled out under it.
            return ''
        cd = self.reset_text(level)
        if cd:
            return cd
        return t('not_used') if self._pct == 0 else ''

    def _render_reset(self):
        """Write the sub-label for the current data and width.

        Three states, and they must not be confused: nothing fetched yet says
        nothing at all, a value claude.ai did not report says 'not available',
        and a real zero says 'not used'. This runs again on every width change,
        so the state has to be remembered rather than inferred from the
        percentage: 'not available' and a genuine 0% both leave _pct at 0.
        """
        if self._state == 'empty' and not self._err:
            return
        self.lbl_sub.config(text=self.reset_display())

    def update(self, pct, resets_at):
        self._resets_at = resets_at
        self._state = 'ok' if pct is not None else 'missing'
        if pct is None:
            self._pct = 0
            self._color = BAR_BG
            self._render_reset()
            self._draw(self.cv.winfo_width())
            return
        self._pct = max(0, min(100, pct))
        if self._dynamic:
            self._color = dynamic_fill(self._pct)
            self._trackc = derive_track(self._color)
        else:
            self._color = self._fill
            self._trackc = self._track
        self._render_reset()
        self._draw(self.cv.winfo_width())

    def set_split(self, parts):
        """Show the bar in `parts` equal parts, 0 to show it whole.

        🔴 Only the division, on the user's word (2026-09-25). The
        7-day window starts wherever Anthropic starts it - one of the user's
        accounts on a Tuesday at 02:59, the other on a Wednesday at 06:00 - so
        the parts cannot be tied to days of the week, and the mark that used
        to say where the usage "should" be by today rested on exactly that
        guess: on screen it looked placed at random, because it was.
        """
        parts = int(parts or 0)
        parts = parts if parts in SPLIT_CHOICES else 0
        if parts == self._split:
            return
        self._split = parts
        self._draw(self.cv.winfo_width())

    def _draw_split(self, w, fw):
        """The divisions: one short line each, centred on the bar's height.

        🔴 On the user's word, after trying both (2026-09-25): a single
        line in the middle reads better than a notch at each edge, which
        looked like two marks where there is one. The line is faint and the
        percentage is drawn after it, so where the two meet the number stays
        on top.
        """
        def under(x):
            if self._pct <= 0:
                return BAR_BG
            return self._color if x < fw else self._trackc

        for k in range(1, self._split):
            x = round(w * k / self._split)
            col = _lerp_hex(under(x), ink_on(under(x)), 0.35)
            self.cv.create_line(x, 4, x, BAR_H - 4, fill=col, tags='split')

    def set_dynamic(self, on):
        """Switch this bar between the percentage-driven palette and its own
        fixed colour, recomputing the drawn colours for the current value."""
        if on == self._dynamic:
            return
        self._dynamic = on
        if self._dynamic:
            self._color = dynamic_fill(self._pct)
            self._trackc = derive_track(self._color)
        else:
            self._color = self._fill
            self._trackc = self._track
        self._draw(self.cv.winfo_width())

    def set_colors(self, fill, track):
        """Change this bar's fixed fill/track (from the colour picker) and
        repaint. No effect on what is drawn while dynamic mode is active, but
        the values are kept for when it is turned off."""
        self._fill = fill
        self._track = track
        if not self._dynamic:
            self._color = fill
            self._trackc = track
            self._draw(self.cv.winfo_width())

    def _draw(self, w):
        if w < 2:
            return
        self.cv.delete('all')
        # Track: neutral gray while the bar is empty, its dark colour once used.
        track = self._trackc if self._pct > 0 else BAR_BG
        pill(self.cv, 0, 0, w, BAR_H, track)
        if self._pct > 0:
            fw = max(BAR_H, w * self._pct / 100)
            pill(self.cv, 0, 0, fw, BAR_H, self._color)
        pct_str = f'{self._pct:.0f}%' if self._pct > 0 else '0%'
        if self._compact and self._cd_txt:
            txt = f'{pct_str}  {self._cd_txt}'
        else:
            txt = pct_str
        if self._split and self._state == 'ok':
            self._draw_split(w, fw if self._pct > 0 else 0)
        # The text is centred, so it sits on the fill once the bar is past
        # half and on the track before that. Ask the one it is actually on.
        behind = self._color if (self._pct > 0 and fw >= w / 2) else track
        if not self._no_number:
            self.cv.create_text(w / 2, BAR_H / 2 - 1, text=txt,
                                fill=ink_on(behind), font=FT_BAR, anchor='center')
        # Pre-refresh breathing dot, centred on the bar's right rounded cap
        # (the centre of the ideal circle that completes the end semicircle).
        # Same glyph + size as the corner dots, at the pct text's vertical
        # level. It fades between the background behind it (invisible) and
        # solid green for a real appear/disappear breathing pulse.
        if self._dot_phase == 'on':
            cx = w - DOT_INSET
            cy = BAR_H / 2 - 1  # measured centre of the bar pill for this image
            fill_w = max(BAR_H, w * self._pct / 100) if self._pct > 0 else 0
            self._dot_bg = self._color if (self._pct > 0 and fill_w >= cx) else BAR_BG
            q = round(self._dot_level * 12) / 12
            color = _lerp_hex(self._dot_bg, DOT_GREEN, q)
            self._dot_img = _dot_image(DOT_DIAM, color)
            self.cv.create_image(cx, cy, image=self._dot_img,
                                 anchor='center', tags='refresh_dot')


# ═══════════════════════════════════════════════════════
# Widget
# ═══════════════════════════════════════════════════════

class Widget:

    def __init__(self):
        self.cfg = load_cfg()
        # An account written by the first version of the Claude Code login
        # carries a flag that said how to read but not who the account was.
        migrate_cc_accounts(self.cfg)
        # Keep the top-level key/org in sync with the active account (config
        # could have been hand-edited, or an account removed).
        mirror_active(self.cfg)
        # Label for the weekly per-model bar. Claude.ai names the model in the
        # usage payload (see scoped_model); persist the last-seen name so the
        # bar reads correctly before the first fetch of a new session.
        self._model_label = self.cfg.get('model_label', 'Sonnet')
        # The theme decides the colour of every widget about to be created,
        # so it goes first: Tk has no way to repaint one afterwards.
        apply_theme(self.cfg.get('theme', 'dark'))
        # Load language from config, default English
        set_lang(self.cfg.get('language', 'en'))
        # And what the line under the bars carries, for the same reason: the
        # first measurement happens long before the menu can be opened.
        set_reset_parts(self.cfg.get('show_reset_time', True),
                        self.cfg.get('show_reset_left', True))
        self.root = tk.Tk()
        init_fonts(self.root, _current_lang)
        # Surface callback-level exceptions in the log. Tkinter normally
        # prints these to stderr, which is invisible for a pythonw/exe app,
        # so a typo inside an event handler (e.g. a bad screen distance)
        # would silently break the widget that was supposed to open.
        def _tk_cb_exc(exc, val, tb):
            import traceback
            wlog('TKERR  ' + ''.join(
                traceback.format_exception(exc, val, tb)).strip())
        self.root.report_callback_exception = _tk_cb_exc

        # DPI handling. Tk on Windows reads the system DPI to set its default
        # scaling factor (1 point = 1.333 px at 96 DPI, 2.0 px at 144 DPI,
        # etc). With SetProcessDpiAwareness(2) we render at the real per-
        # monitor DPI. dpi_scale is "how big is one logical pixel relative
        # to a 96-DPI baseline", used for paddings/margins that need to
        # grow with the user's text size.
        #
        # Optional debug override: set "debug_tk_scaling" in config.json
        # (e.g. 2.0 = simulate 150% Windows DPI without changing system
        # settings) to validate dialog layouts at higher DPI locally.
        override = self.cfg.get('debug_tk_scaling')
        if isinstance(override, (int, float)) and override > 0:
            try:
                self.root.tk.call('tk', 'scaling', float(override))
                wlog(f'INIT   tk scaling override = {override}')
            except Exception as e:
                wlog(f'INIT   tk scaling override failed: {e}')
        self.dpi_scale = self.root.winfo_fpixels('1i') / 96.0
        wlog(f'INIT   dpi_scale={self.dpi_scale:.3f}')

        self._job = None
        self._countdown_job = None
        self._tooltip_hides = []     # close every hover tooltip when a gesture starts
        self._shown_tips = set()     # tooltip windows on show (see _raise_covered_dialogs)
        self._has_usage = False       # a fetch has landed: the floor may act
        self._last_width = 0          # last width seen, to skip idle <Configure>
        self._fetch_busy = False      # a fetch is in flight; do not start another
        self._fetch_pending = False   # a refresh asked for while one was running
        self._fetch_lock = threading.Lock()   # guards the two flags above
        self._topmost_job = None
        self._pulse_job = None
        self._pulse_phase = 0.0
        self._countdown_secs = 0
        self._last_time = ''
        self._resets_at = []  # ISO reset times - trigger refresh when reached
        self._last_data = None  # last fetched usage dict (for ess-bar re-render)
        # Which account the fetches are for: bumped at every change of the
        # account on show, so an answer still on its way is recognised as
        # describing the previous one and dropped (see _set_active_account).
        self._fetch_gen = 0
        self._soft_note = None  # a wait, not a fault: said in the bars' hover text
        self._panel_down = None  # the error panel grew downward (see _fit_panel_height)
        self._dx = self._dy = 0
        self._expanded = False
        self._essential = False
        self._reshaping = False     # the expand dot or the mode switch at work
        self._rs_x = self._rs_y = self._rs_w = self._rs_h = 0
        self._rs_off = False        # the resize dot's gesture turned into a mode switch
        self._menu_win = None
        self._menu_click_job = None   # outside-click watcher while a menu is open
        self._menu_btn_down = False
        self._flyout_win = None       # category side-flyout Toplevel
        self._dialogs = {}            # key -> the live dialog of that kind
        self._open_dialogs = []       # every dialog, in the order it opened
        self._flyout_cat = None
        self._flyout_anchor = None

        self.root.title('Claude Usage')
        self.root.overrideredirect(True)
        self.root.attributes('-topmost', True)
        self.root.attributes('-alpha', 0.94)
        self.root.configure(bg=BG)

        try:
            self.root.iconbitmap(ICO)
        except Exception:
            pass

        # Read + validate the saved geometry BEFORE the first
        # update_idletasks(). winfo_vroot* talk to Windows directly so
        # they work without an idletasks pump. By calling geometry('+x+y')
        # here, the very first map (triggered by the idletasks below)
        # places the window at the saved position instead of at the
        # default Tk spawn spot - no startup flash.
        #
        # update_idletasks() must still run BEFORE _make_wintab_visible():
        # the helper reads winfo_id() to set WS_EX_TOOLWINDOW (hides from
        # taskbar + keeps topmost rock-solid against taskbar clicks).
        # Without the idletasks pump the HWND isn't ready, the EXSTYLE
        # write silently no-ops, and the widget regresses to acting like
        # a normal app window (flash on taskbar interaction).
        w = self.cfg.get('width', DEF_W)
        h = self.cfg.get('height', 41)
        # Prefer the monitor-anchored position (survives resolution / layout
        # changes); fall back to the raw saved coords. Either way _place_on_screen
        # is the final safety net: Tk's vroot bounding box can't see the
        # L-shaped gaps between monitors that leave a saved spot off every real
        # screen and invisible, so we validate against the actual
        # monitors instead.
        anchored = self._resolve_anchor(w, h)
        if anchored:
            x, y = anchored
            src = 'anchor'
        else:
            x, y = self.cfg.get('x', 100), self.cfg.get('y', 100)
            src = 'saved'
        ox, oy = x, y
        x, y, moved = _place_on_screen(x, y, w, h)
        # Keep cfg canonical/on-screen so later appliers (e.g. _restore_essential)
        # never re-apply a stale off-screen position.
        self.cfg['x'], self.cfg['y'] = x, y
        if moved:
            wlog(f'INIT   {src} ({ox},{oy}) off all monitors -> rescued to ({x},{y})')
        else:
            wlog(f'INIT   {src}=({x},{y}) {w}x{h} on screen')
        self.root.geometry(f'+{x}+{y}')

        self.root.update_idletasks()
        self._make_wintab_visible()

        # ITaskbarList3 wrapper for the Win11 progress overlay on the
        # taskbar icon. Initialised even when show_in_taskbar is off:
        # constructing the COM object eagerly prevents a stutter the
        # first time the user toggles the icon on. Calls into it are
        # safe no-ops while the icon isn't visible.
        self._taskbar = TaskbarProgress()
        self._last_session_pct = None
        # Make Windows recognise our AUMID so toast banners actually pop
        # (an unregistered AUMID causes Show() to succeed but Windows to
        # silently route the toast to Action Center without a banner).
        register_toast_aumid()
        # The taskbar entry can take a couple of seconds to register
        # after WS_EX_APPWINDOW is applied. Re-push the colour on a
        # short ramp so the bar settles into the correct state even if
        # the very first set_state was issued before Windows had
        # registered the icon.
        for delay in (1500, 3000, 6000):
            self.root.after(delay, self._push_taskbar_state)

        self._bar_icon = None
        try:
            self._bar_icon = tk.PhotoImage(file=ICO_BAR)
        except Exception:
            pass

        # GitHub Octocat used in the menu's "Open GitHub repo" row. Pre-
        # rendered to PNG (assets/icon-github-24.png) at build time so we
        # don't have to ship an SVG renderer; recoloured to match the
        # menu's FG so it lines up with the other icons.
        # The asset is a white silhouette, which is invisible on a light
        # menu, so it is tinted to the menu's own text colour at load.
        self._gh_icon = None
        try:
            if os.path.isfile(ICO_GITHUB):
                shape = Image.open(ICO_GITHUB).convert('RGBA')
                tinted = Image.new('RGBA', shape.size, _hex_to_rgb(FG) + (0,))
                tinted.putalpha(shape.getchannel('A'))
                self._gh_icon = ImageTk.PhotoImage(tinted)
        except Exception:
            pass

        self._build()

        # Re-apply geometry now that the UI is built so the height matches
        # the actual layout.
        self.root.update_idletasks()
        rh = self.root.winfo_reqheight()
        self.root.geometry(f'{w}x{rh}+{x}+{y}')
        self.root.minsize(MIN_W, 0)

        self.root.after(50, lambda: dwm_round(self.root, shadow=False))
        # Topmost recovery, three layers, all running on the Tk thread:
        #  1. WS_EX_NOACTIVATE on the window itself - eliminates the
        #     worst-case taskbar flash (focus transfer from widget to
        #     taskbar). Set in _make_wintab_visible.
        #  2. <Visibility> binding - Tk fires this when the window is
        #     covered/uncovered, instant SetWindowPos recovery.
        #  3. 10ms keep_topmost timer - safety net for cases Visibility
        #     misses (Tk's Visibility on Win32 isn't always reliable).
        # FocusOut is intentionally NOT bound: NOACTIVATE keeps the
        # widget out of the focus rotation, so the event would never fire.
        self._keep_topmost()
        self.root.bind('<Visibility>', lambda e: self._force_topmost())

        # Restore essential mode if it was active when last closed; otherwise
        # lay out the selected bars stacked for normal mode.
        if self.cfg.get('essential', False):
            self.root.after(100, self._restore_essential)
        else:
            self._pack_stacked()
            self._update_expand_visibility()
            self._auto_height()

        if has_credentials(self.cfg):
            self.refresh()
            self._schedule()
            # Fill a migrated account's identity (name/email/plan) once.
            self.root.after(1500, self._backfill_identity)
        else:
            self._error(t('setup_required'),
                        action_label=t('action_setup_now'),
                        action_cmd=self._setup_dialog)
            self.root.after(300, self._setup_dialog)

        self._update_banner = None
        self._schedule_update_check()

        # Pre-warm the slow paths that the first menu open would trigger on
        # a cold start: a Toplevel creation + Segoe UI Emoji font lookup.
        # Without this, the first right-click takes 3-5s while Tk and
        # Windows lazily initialize both; subsequent opens are instant.
        self.root.after(200, self._prewarm_menu)

        # Self-heal on live monitor changes: bring the widget home when its
        # monitor is re-added, or rescue it onto the primary if it vanished.
        self._mon_sig = _monitor_signature()
        self.root.after(GEOMETRY_WATCH_MS, self._geometry_watchdog)

        self.root.protocol('WM_DELETE_WINDOW', self._quit)

        # Protect against external termination (PowerToys, Task Manager, etc.)
        atexit.register(lambda: wlog('ATEXIT process shutting down'))
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGBREAK):
            try:
                signal.signal(sig, self._signal_quit)
            except (OSError, ValueError):
                pass

        wlog('START  widget started')
        self.root.mainloop()
        wlog('EXIT   mainloop ended')

    # ── Build UI ─────────────────────────────────────

    def _build(self):
        self.main = tk.Frame(self.root, bg=BG)
        self.main.pack(fill='both', expand=True)

        # ── Title bar ──
        self.tb = tk.Frame(self.main, bg=BG_TITLE, height=TITLE_H)
        self.tb.pack(fill='x')
        self.tb.pack_propagate(False)
        self.tb.bind('<Button-1>', self._drag_start)
        self.tb.bind('<B1-Motion>', self._drag_move)
        self.tb.bind('<ButtonRelease-1>', self._save_geometry_user)

        if self._bar_icon:
            ico = tk.Label(self.tb, image=self._bar_icon, bg=BG_TITLE, padx=4)
        else:
            ico = tk.Label(self.tb, text=' \u2731', font=('Segoe UI', 11),
                           fg=CLAUDE_TEXT, bg=BG_TITLE)
        ico.pack(side='left', padx=(6, 0))
        ico.bind('<Button-1>', self._drag_start)
        ico.bind('<B1-Motion>', self._drag_move)
        ico.bind('<ButtonRelease-1>', self._save_geometry_user)

        title = tk.Label(self.tb, text='Claude Usage', font=FT_B, fg=FG, bg=BG_TITLE)
        title.pack(side='left', padx=(2, 0))
        title.bind('<Button-1>', self._drag_start)
        title.bind('<B1-Motion>', self._drag_move)
        title.bind('<ButtonRelease-1>', self._save_geometry_user)

        # Close (rightmost)
        self.btn_x = tk.Label(self.tb, text=' \u2715 ', font=FT_EMOJI,
                              fg=DIM, bg=BG_TITLE, cursor='hand2')
        self.btn_x.pack(side='right', padx=(0, 2))
        self.btn_x.bind('<Button-1>', lambda e: self._quit())
        self.btn_x.bind('<Enter>', lambda e: self.btn_x.config(fg=RED, bg=HOVER_BG))
        self.btn_x.bind('<Leave>', lambda e: self.btn_x.config(fg=DIM, bg=BG_TITLE))

        # Hamburger menu
        self.btn_menu = tk.Label(self.tb, text=' \u2261 ', font=('Segoe UI', 12),
                                 fg=DIM, bg=BG_TITLE, cursor='hand2')
        self.btn_menu.pack(side='right')
        self.btn_menu.bind('<Button-1>', self._show_menu)
        self.btn_menu.bind('<Enter>', lambda e: self.btn_menu.config(fg=FG))
        self.btn_menu.bind('<Leave>', lambda e: self.btn_menu.config(fg=DIM))

        # Right-click anywhere inside the widget opens the menu. Binding lives
        # on the root because every child's bindtag chain passes through it,
        # so this catches clicks on the canvas bar and on header labels too -
        # places per-widget bindings used to miss in essential mode.
        self.root.bind('<Button-3>', self._show_menu)

        # The reset labels drop the word 'reset' when the window is dragged
        # narrower than the full form, so the form has to be re-picked as the
        # width changes. set_reset_level is a no-op when nothing changed, which
        # keeps this cheap during a drag.
        self.root.bind('<Configure>', self._on_root_configure)

        # Refresh button - Segoe MDL2 refresh glyph at a compact size so it
        # matches the original ↻ footprint without the thin-arrow look.
        self.btn_r = tk.Label(self.tb, text=f' {ICON_REFRESH} ', font=FT_MDL2_TB,
                              fg=DIM, bg=BG_TITLE, cursor='hand2')
        self.btn_r.pack(side='right')
        self.btn_r.bind('<Button-1>', lambda e: self.refresh())
        self.btn_r.bind('<Enter>', lambda e: self.btn_r.config(fg=BLUE))
        self.btn_r.bind('<Leave>', lambda e: self.btn_r.config(fg=DIM))

        # Last update time
        self.lbl_time = tk.Label(self.tb, text='', font=FT_S, fg=FG, bg=BG_TITLE)
        self.lbl_time.pack(side='right', padx=(0, 2))
        self.lbl_time.bind('<Button-1>', self._drag_start)
        self.lbl_time.bind('<B1-Motion>', self._drag_move)
        self.lbl_time.bind('<ButtonRelease-1>', self._save_geometry_user)

        # Separator
        self.sep = tk.Frame(self.main, bg=BAR_BG, height=1)
        self.sep.pack(fill='x')

        # ── Content ──
        self.content = tk.Frame(self.main, bg=BG)
        self.content.pack(fill='both', expand=True)

        self.s_session = Section(self.content, t('current_session'),
                                 *self._bar_ft('session'))

        # Expandable sections
        self.extra_frame = tk.Frame(self.content, bg=BG)
        self.s_weekly = Section(self.extra_frame, t('all_models'),
                                *self._bar_ft('weekly'))
        self.s_sonnet = Section(self.extra_frame, self._sonnet_label(),
                                *self._bar_ft('sonnet'))

        # Essential-collapsed multi-bar strip. Tk can't re-parent the originals,
        # so this row owns its own compact Section instances, shown side-by-side
        # only when essential AND collapsed. Fed the same data every refresh.
        self.ess_row = tk.Frame(self.content, bg=BG)
        self.ess_bars = {
            'session': Section(self.ess_row, t('current_session'),
                               *self._bar_ft('session')),
            'weekly':  Section(self.ess_row, t('all_models'),
                               *self._bar_ft('weekly')),
            'sonnet':  Section(self.ess_row, self._sonnet_label(),
                               *self._bar_ft('sonnet')),
        }
        for sec in self.ess_bars.values():
            # A bare tk.Canvas reports a large default requested width. Packed
            # side-by-side that forces the row far wider than the window, so the
            # extra bars get clipped off the right until you widen a lot. Pin
            # the requested width to 1 - the real width comes from fill='x' +
            # expand, which then splits the window evenly (halves for 2, etc.).
            sec.cv.config(width=1)
            # Also pin the reset sub-label's requested width to 1: otherwise a
            # longer reset string ('reset 1h 38min' vs 'reset 2gg 9h') makes one
            # bar's frame wider, so the bars don't split evenly. It still fills
            # via fill='x' and clips if the bar is narrower than the text.
            sec.lbl_sub.config(width=1)
            sec.set_compact(True)
            sec.frame.pack_forget()     # hidden until essential-collapsed
        # The reset-label form is chosen dynamically: see _sync_ess_reset_mode.

        # Whose numbers these are, on hover over the line under a bar. Zero
        # pixels, and it answers the question a user hit when every bar read
        # 0% because the widget was watching an account they had stopped using
        # (issue #11): the bars were right, the account was the surprise.
        for sec in self._all_sections():
            tip = (lambda s=sec: self._bar_tip(s))
            self._tooltip(sec.lbl_sub, tip, delay=700, persistent=True)
            # The bar itself too, not just the line under it: with both halves
            # of that line turned off it is blank, and the setting's own help
            # text tells the user to hover the bar to read the reset.
            self._tooltip(sec.cv, tip, delay=700, persistent=True)
            sec.cv.bind('<Button-1>', lambda e, s=sec: self._bar_clicked(s), add='+')

        # Apply the saved palette mode to every bar now that they all exist.
        if self.cfg.get('bar_dynamic', False):
            for sec in self._all_sections():
                sec._dynamic = True
        self._apply_split()

        # Hamburger menu pill - shown on the right of the side-by-side strip
        # whenever essential mode is collapsed. It pushes the bars left so the
        # per-bar reset text clears the bottom-right controls, and opens the
        # menu on click.
        self._ess_menu_hover = False
        self.ess_menu = tk.Canvas(self.ess_row, width=ESS_MENU_W, height=BAR_H,
                                  bg=BG, highlightthickness=0, bd=0, cursor='hand2')
        self.ess_menu.bind('<Configure>', lambda e: self._draw_ess_menu(e.width))
        self.ess_menu.bind('<Button-1>', self._show_menu)
        self.ess_menu.bind('<Enter>', lambda e: (
            setattr(self, '_ess_menu_hover', True), self._draw_ess_menu()))
        self.ess_menu.bind('<Leave>', lambda e: (
            setattr(self, '_ess_menu_hover', False), self._draw_ess_menu()))
        self.ess_menu.pack_forget()

        # Bottom spacer
        self.bottom_pad = tk.Frame(self.content, bg=BG, height=6)
        self.bottom_pad.pack(fill='x')

        # ── Overlay elements (place() on main - always at window corners) ──

        # Expand dot - bottom-left
        self.btn_expand = tk.Label(self.main, text='\u25cf', font=FT_DOT,
                                   fg=DOT_W_D, bg=BG, cursor='hand2',
                                   bd=0, highlightthickness=0, padx=0, pady=0)
        self.btn_expand.place(x=6, rely=1.0, y=-4, anchor='sw')
        self.btn_expand.bind('<Button-1>', lambda e: self._toggle_expand())
        self.btn_expand.bind('<Enter>', lambda e: self.btn_expand.config(fg=DOT_W_H))
        self.btn_expand.bind('<Leave>', lambda e: self.btn_expand.config(
            fg=DOT_W if self._expanded else DOT_W_D))
        # Nothing on screen said this dot opens the full view either, and it is
        # a toggle, so the text has to follow the state: a fixed 'click to
        # expand' would be wrong half of the time.
        self._tooltip(self.btn_expand,
                      lambda: t('tip_collapse' if self._expanded else 'tip_expand'),
                      delay=500, persistent=True)

        # Resize dot - bottom-right (ALWAYS stays here)
        self.btn_resize = tk.Label(self.main, text='\u25cf', font=FT_DOT,
                                   fg=OCHRE, bg=BG, cursor='hand2',
                                   bd=0, highlightthickness=0, padx=0, pady=0)
        self.btn_resize.place(relx=1.0, x=-6, rely=1.0, y=-4, anchor='se')
        self.btn_resize.bind('<Button-1>', self._resize_start)
        self.btn_resize.bind('<B1-Motion>', self._resize_move)
        self.btn_resize.bind('<ButtonRelease-1>', self._save_geometry_resize)
        self.btn_resize.bind('<Double-Button-1>', self._resize_double)
        self.btn_resize.bind('<Enter>', lambda e: self.btn_resize.config(fg='#E06030'))
        self.btn_resize.bind('<Leave>', lambda e: self.btn_resize.config(fg=OCHRE))
        # Nothing on screen says this dot resizes the widget, and a user asked
        # for a feature that has been there all along (issue #10). The tooltip
        # hides on Button-1, so it never sits in the way of the drag it
        # describes.
        self._tooltip(self.btn_resize, lambda: t('tip_resize'), delay=500,
                      persistent=True)

        # Essential mode controls - dynamic stack, right-aligned
        # Visual order left to right: ✕ ↻ HH:MM [resize dot]
        self.ess_bar = tk.Frame(self.main, bg=BG)
        self.ess_close = tk.Label(self.ess_bar, text='\u2715', font=FT_EMOJI,
                                  fg=DIM, bg=BG, cursor='hand2',
                                  bd=0, highlightthickness=0, padx=4, pady=0)
        self.ess_close.pack(side='left')
        self.ess_close.bind('<Button-1>', lambda e: self._quit())
        self.ess_close.bind('<Enter>', lambda e: self.ess_close.config(fg=RED))
        self.ess_close.bind('<Leave>', lambda e: self.ess_close.config(fg=DIM))
        self.ess_refresh = tk.Label(self.ess_bar, text=ICON_REFRESH, font=FT_MDL2_TB,
                                    fg=DIM, bg=BG, cursor='hand2',
                                    bd=0, highlightthickness=0, padx=2, pady=0)
        self.ess_refresh.pack(side='left')
        self.ess_refresh.bind('<Button-1>', lambda e: self.refresh())
        self.ess_refresh.bind('<Enter>', lambda e: self.ess_refresh.config(fg=BLUE))
        self.ess_refresh.bind('<Leave>', lambda e: self.ess_refresh.config(fg=DIM))
        self.ess_time = tk.Label(self.ess_bar, text='', font=FT_S, fg=FG, bg=BG,
                                 bd=0, highlightthickness=0, padx=4, pady=0)
        self.ess_time.pack(side='left')

        # Error panel: message label + optional action button (e.g. "Configure now")
        self.err_frame = tk.Frame(self.content, bg=BG)
        self.lbl_err = tk.Label(self.err_frame, text='', font=FT_S, fg=RED, bg=BG,
                                wraplength=DEF_W - 30, justify='left', anchor='w')
        self.lbl_err.pack(fill='x', padx=PAD, pady=(0, 4))
        self.err_btn = tk.Label(self.err_frame, text='', font=FT_B, fg=BG,
                                bg=CLAUDE, cursor='hand2', padx=10, pady=3)
        self.err_btn.bind('<Enter>', lambda e: self.err_btn.config(bg='#E08060'))
        self.err_btn.bind('<Leave>', lambda e: self.err_btn.config(bg=CLAUDE))
        self._err_action = None

    # ── Toggle expand/collapse ─────────────────────

    def _reshape(self, change):
        """Change the widget's own layout and size, and show only the result.

        The expand dot and the mode switch used to cover the widget with a
        plain panel and grow it in ten steps 8 ms apart: every step uncovered
        a band that Windows showed black until Tk painted it, the bars came
        back all at once at the end, and the translucency was dropped for the
        ride and put back after it. The user asked for these to change the way
        the windows now open (2026-09-28). A still picture of the widget stays
        on screen while the real one, cloaked, is laid out, sized and painted
        (_hold), and the finished widget takes the picture's place in one
        frame (_present). A click meanwhile lands on the picture and is lost,
        as one during the animation was.

        🔴 Not while the widget has a button on the taskbar (show_in_taskbar):
        a window taken off the screen may take its button off the taskbar,
        and the progress drawn on it (not measured: the taskbar cannot be
        watched without showing it to the user). The change then shows as it
        is made, in one step, as it does when DWM refuses the cloak."""
        root = self.root
        self._reshaping = True
        # What _hold and _present ask of a window: on show (the user just
        # clicked it), rounded (dwm_round at start-up), and its geometry.
        root._revealed = True
        root._rounded = True
        root._place = lambda: (root.winfo_width(), root.winfo_height(),
                               root.winfo_x(), root.winfo_y())
        if not _on_taskbar(_frame_of(root)):
            self._hold(root)

        def done():
            self._reshaping = False
            # The update banner follows the widget: in the frame the widget
            # comes out in (_on_banner_follow waits for it).
            self._on_banner_follow()

        try:
            change()
        finally:
            # Whatever happened, the widget comes back: _present uncloaks it.
            self._present(root, root._place, then=done, gone=done)

    def _toggle_expand(self):
        if self._reshaping:
            return
        # Expand is an essential-mode affordance only; normal mode already
        # shows the selected bars stacked (the dot is hidden there).
        if not self._essential:
            return
        self._reshape(self._flip_expand)

    def _flip_expand(self):
        """Open the strip to every bar stacked, or fold it back (_reshape)."""
        self._expanded = not self._expanded
        if self._expanded:
            # Expanding is 'see everything': show all bars stacked, with a bit
            # more bottom room for the essential-mode overlay controls.
            self.bottom_pad.config(height=24)
            self._pack_stacked(all_bars=True)
            self.btn_expand.config(fg=DOT_W)
            for sec in (self.s_session, self.s_weekly, self.s_sonnet):
                self._bind_drag_section(sec)
        else:
            self.btn_expand.config(fg=DOT_W_D)
            self.bottom_pad.config(height=6)
            # back to the collapsed side-by-side strip
            self._enter_ess_collapsed()
        self.root.update_idletasks()
        # Re-floor for the layout now showing, both ways round. Collapsing
        # into the strip may need a wider minimum than the last compute (bars
        # added while expanded); expanding raises the floor to the standard-mode
        # minimum, which the strip is allowed to sit below. Only the collapse
        # side used to do this, so after an expand the stale lower floor stayed
        # in place until the next refresh, which then widened the window on its
        # own, at an arbitrary moment and with no user action behind it.
        self._update_minsize(keep_height=True)
        if self._essential and not self._expanded:
            # The expanded state floors at the standard-mode minimum, so a
            # refresh landing while expanded can have widened the window past
            # what the strip needs. Without this the strip would come back
            # wider and the next periodic save would store that as the user's
            # width for this bar count.
            self._restore_ess_width(keep_height=True)
            # Let it land: the height below reads the width back, and a stale
            # read would re-apply the wider one.
            self.root.update_idletasks()
        # Size to the freshly-laid-out content in BOTH directions, keeping the
        # bottom edge anchored. A saved collapsed height was wrong when the mode
        # changed between expand and collapse (e.g. expand in essential, switch
        # to normal, then collapse): it restored essential's small height
        # instead of normal's. winfo_reqheight() always reflects current mode.
        self._resize_bottom_anchored()
        self.root.update_idletasks()

    # ── Essential mode ─────────────────────────────

    def _toggle_essential(self):
        if self._reshaping:
            return
        wlog(f'MODE   toggle essential: {self._essential} -> {not self._essential}')
        self._reshape(self._flip_essential)

    def _flip_essential(self):
        """Switch between the essential strip and standard mode (_reshape)."""
        self._essential = not self._essential
        if self._essential:
            self.tb.pack_forget()
            self.sep.pack_forget()
            if self._expanded:
                self._expanded = False
                self.btn_expand.config(fg=DOT_W_D)
            self.bottom_pad.config(height=6)
            self.ess_bar.place(relx=1.0, x=-18, rely=1.0, y=-1, anchor='se')
            self._enter_ess_collapsed()
            # Standard mode has a higher floor than the strip, so the trip
            # through it widens the window; without this the strip would come
            # back at the standard width and the next periodic save would
            # store that as the user's preference for this bar count.
            restore_width = True
        else:
            restore_width = False
            self._expanded = False
            self.ess_bar.place_forget()
            self.content.pack_forget()
            self.tb.pack(fill='x')
            self.sep.pack(fill='x')
            self.content.pack(fill='both', expand=True)
            self.bottom_pad.config(height=6)
            # Normal mode shows the selected bars stacked (same selection as
            # essential), and drags from the title bar only.
            self._pack_stacked()
            self._unbind_drag(self.content)
            for sec in (self.s_session, self.s_weekly, self.s_sonnet):
                self._unbind_drag_section(sec)
        self._update_expand_visibility()
        self.root.update_idletasks()
        self._update_minsize(keep_height=True)
        if restore_width:
            self._restore_ess_width(keep_height=True)
        # Let a widen from _update_minsize land before reading the geometry
        # back: the strip may sit below the standard-mode floor, and the stale
        # x would then be re-applied with the wider width, moving the widget
        # right instead of growing it leftward as intended.
        self.root.update_idletasks()
        # The height the new layout asks for, the bottom edge where it was.
        self._resize_bottom_anchored()
        self.root.update_idletasks()

    def _restore_essential(self):
        """Restore essential mode on startup - no animation, direct layout."""
        wlog(f'MODE   toggle essential: {self._essential} -> {not self._essential}')
        self._essential = True
        self.tb.pack_forget()
        self.sep.pack_forget()
        self.ess_bar.place(relx=1.0, x=-18, rely=1.0, y=-1, anchor='se')
        self._enter_ess_collapsed()
        self._update_expand_visibility()
        # Apply saved position directly
        w = self.cfg.get('width', DEF_W)
        x = self.cfg.get('x', 100)
        y = self.cfg.get('y', 100)
        self.root.update_idletasks()
        rh = self.root.winfo_reqheight()
        self.root.geometry(f'{w}x{rh}+{x}+{y}')
        self.root.attributes('-alpha', 0.94)
        self._update_minsize()
        self._restore_ess_width(on_launch=True)

    # ── Essential-collapsed multi-bar strip ──────────

    def _bind_drag_section(self, sec):
        """Bind drag on a Section's frame + its non-Canvas children."""
        self._bind_drag(sec.frame)
        for child in sec.frame.winfo_children():
            if not isinstance(child, tk.Canvas):
                self._bind_drag(child)

    def _unbind_drag_section(self, sec):
        """Remove drag handlers from a Section's frame + non-Canvas children."""
        self._unbind_drag(sec.frame)
        for child in sec.frame.winfo_children():
            if not isinstance(child, tk.Canvas):
                self._unbind_drag(child)

    def _draw_ess_menu(self, w=None):
        """Draw the hamburger pill: bar-style rounded background + three lines."""
        cv = self.ess_menu
        w = w or cv.winfo_width()
        if w < 2:
            return
        cv.delete('all')
        bg = HOVER_BG if self._ess_menu_hover else BAR_BG
        pill(cv, 0, 0, w, BAR_H, bg)
        cx, cy = w / 2, BAR_H / 2 - 1
        half = 5
        for dy in (-3, 0, 3):
            cv.create_line(cx - half, cy + dy, cx + half, cy + dy,
                           fill=FG, width=1)

    def _active_account_tip(self):
        """Which account the bars are showing, for the tooltip under them.

        States a fact and never a diagnosis: an account with genuinely no
        usage looks exactly like the wrong account, so naming the one being
        watched is all the widget can honestly say."""
        acc = active_account(self.cfg)
        if not acc:
            return ''
        lines = [acc.get('name') or t('dlg_accounts_title')]
        if acc.get('email'):
            lines.append(acc['email'])
        return '\n'.join(lines)

    def _bar_clicked(self, sec):
        """A click on a bar that says "error" does what the error panel's
        button does. In the strip the panel has no room, and its button - the
        way to Accounts, to renew a key, to claude.ai - was out of reach
        (review of 2026-09-26)."""
        if sec._err and self._err_action is not None:
            self._hide_tooltips()
            self._err_action()

    def _bar_tip(self, sec):
        """Hover text for the line under a bar: whose numbers these are, and
        the reset in full.

        The full form is added whenever the label is not already showing it,
        which is what makes hiding either half (or both) a fair trade rather
        than a loss: the strip gets narrower and the reading moves here.
        """
        lines = []
        acc = self._active_account_tip()
        if acc:
            lines.append(acc)
        full = format_reset(sec._resets_at, RESET_FULL, parts=(True, True))
        shown = sec.reset_display()
        # Only when it carries something the label does not. The word for
        # 'reset' is not something: the label drops it precisely because it
        # says nothing, and repeating the same line one word longer, two pixels
        # above where it already is, is noise.
        bare = full.removeprefix(t('reset_prefix') + ' ') if full else ''
        if full and shown not in (full, bare):
            lines.append(full)
        if sec._err and getattr(self, '_last_error', None):
            lines.append(self._last_error)
        elif getattr(self, '_soft_note', None):
            lines.append(self._soft_note)
        return '\n'.join(lines)

    def _on_root_configure(self, e):
        """Re-pick the reset-label form when the window's width changes.

        Only for the root's own event, and only on a real width change: this
        fires for every child too, and several times per pixel during a drag.
        """
        if e.widget is not self.root or e.width == self._last_width:
            return
        self._last_width = e.width
        try:
            self._sync_ess_reset_mode()
        except Exception as err:
            wlog(f'LAYOUT reset label: {err}')

    def _sync_ess_reset_mode(self):
        """Pick how much of the reset label there is room for.

        Side-by-side bars always take the compact form. A single bar keeps the
        full one while the window is wide enough and drops the word when it is
        not, which is what lets the window be dragged narrower than the full
        label (issue #10). The minimum width is computed from the narrow form,
        so this decision only changes what is shown, never what fits: it cannot
        feed back into itself.
        """
        width = self.root.winfo_width()
        bars = list(self.ess_bars.values())
        # Only the bars actually on screen decide, the same set the floor is
        # computed over: a hidden bar's longer label would otherwise drop the
        # word on a strip that had room for it.
        shown = [self.ess_bars[b] for b in self._essential_bar_ids()
                 if b in self.ess_bars]
        if len(shown) > 1:
            ess_level = RESET_COMPACT
        else:
            ess_level = self._reset_level_for(shown, width - self._strip_reserved())
        for sec in bars:
            sec.set_reset_level(ess_level)
        stacked = [s for s in self._all_sections() if s not in bars]
        for sec in stacked:
            sec.set_reset_level(self._reset_level_for(stacked, width - 2 * PAD))

    def _strip_reserved(self):
        """Pixels on the essential strip that the reset label cannot use.

        The label starts after the bar frame's padding and its own inset, and
        the close / refresh / sync-time block is PLACED over the right-hand
        side rather than packed beside it, so its footprint has to be reserved
        by hand. Measured, because that block grows with the display scaling
        while the paddings do not.

        Both the floor and the choice of label form go through this: when they
        disagreed, the full label came back before there was room for it and
        ran under the controls (worse the higher the DPI, where the mismatch
        outgrew the whole gain).
        """
        controls = self.ess_bar.winfo_reqwidth()
        if not self.ess_time.cget('text'):
            # The clock only appears once the first countdown tick writes it.
            # Reserve it up front, or the floor (and the label form measured
            # against it) would change under the user a moment later.
            controls += FT_S.measure('00:00')   # the label's own padx is already there
        return PAD + SUB_LABEL_PADX + controls + ESS_CONTROLS_INSET

    def _ess_row_reserve(self, n):
        """Horizontal space the collapsed strip spends around its `n` bars.

        Exactly what _enter_ess_collapsed packs: PAD outside the first bar, 3px
        between bars, 3px after the last, then the hamburger with its own
        (3, PAD) padding. Written once here because the floor has to reserve
        the same pixels the layout gives away, and the two used to disagree.
        """
        return PAD + (n - 1) * 3 + 3 + 3 + ESS_MENU_W + PAD

    def _bar_content_w(self):
        """Width the bar interior needs: the percentage plus whatever the
        countdown mode draws next to it, and room for the refresh dot.

        Measured on a worst-case sample rather than on the live text, whose
        width changes every second as the countdown ticks and would make the
        floor wobble with it. The sample is spaced exactly as Section._draw
        spaces it, two blanks and not one, or the reservation comes out a few
        pixels short of what is drawn.

        The text is centred and the dot sits on the right cap, so what has to
        be kept clear on each side is the dot's own half plus its inset: the
        margin below is that clearance, not decoration.

        A failed refresh used to write the word for 'error' here too, which
        every bar then had to reserve room for whether or not anything had
        failed. It is said under the bar now (Section.set_error), where the
        line is already sized for a longer text, so nothing reserves it twice.
        """
        sample = '100%'
        if self.cfg.get('show_sync_time', True):
            sample += '  00:00'
        if self._countdown_mode() == 'full':
            sample += '  (59min 59s)'
        return FT_BAR.measure(sample) + 2 * (DOT_INSET + DOT_DIAM // 2 + 1)

    @staticmethod
    def _reset_width(sections, level):
        """Width in pixels of the widest sub-label these sections would draw at
        `level`: the reset time when there is one, and the word drawn in its
        place when there is not."""
        widths = [FT_S.measure(sec.reset_display(level)) for sec in sections]
        return max(widths) if widths else 0

    def _reset_samples(self, level):
        """The reset label at `level` for the widest countdowns the formatter
        can produce: minutes only, hours and minutes, and each weekday of the
        days-and-hours form."""
        now = datetime.now(timezone.utc)
        offsets = [timedelta(minutes=59), timedelta(hours=47, minutes=59)]
        offsets += [timedelta(days=d, hours=23) for d in range(2, 9)]
        out = [format_reset((now + off).isoformat(), level) for off in offsets]
        return [s for s in out if s]

    def _reset_floor_width(self, sections, level):
        """Width the floor has to reserve for these sub-labels at `level`.

        Measured on the worst case the formatter can produce, not on the text
        showing right now: the live width changes as the countdown runs, and a
        floor that follows it drifted 24px across two bars and 36px across
        three, widening the window with no user action behind it. Same
        reasoning as _bar_content_w.

        Used for the side-by-side strip only, where the bar count multiplies
        the drift and the worst case costs nothing (it is the width the live
        measure already peaked at). A single bar keeps the live measure: there
        the worst case would reserve room for the longest countdown even when
        the reset is minutes away, and the floor would land above the one this
        release set out to lower.
        """
        widths = [0]
        for sec in sections:
            if not sec.reset_display(level):
                continue          # nothing under this bar: nothing to reserve
            if sec.reset_text(level) is None:
                # A word rather than a time ('not available' / 'not used'):
                # already stable, and the samples below would not cover it.
                widths.append(FT_S.measure(sec.reset_display(level)))
            else:
                widths.extend(FT_S.measure(s) for s in self._reset_samples(level))
        return max(widths)

    def _reset_level_for(self, sections, available):
        """The widest form that fits in `available` pixels.

        Measured on the live text: this is what the label reads at this moment,
        and the question is whether it fits. The floor asks a different one and
        measures the worst case instead (_reset_floor_width).
        """
        return (RESET_FULL if self._reset_width(sections, RESET_FULL) <= available
                else RESET_NO_PREFIX)

    def _sonnet_label(self):
        """Localized label for the weekly per-model bar, with the current
        model name (from the usage payload, persisted between sessions)."""
        return t('model_scoped').format(model=self._model_label)

    def _apply_model_label(self, name):
        """Update the per-model bar label to `name` (when the payload gives one)
        and repaint both the normal and essential-row labels."""
        if name and name != self._model_label:
            self._model_label = name
            self.cfg['model_label'] = name
            save_cfg(self.cfg)
        lbl = self._sonnet_label()
        self.s_sonnet.lbl.config(text=lbl)
        self.ess_bars['sonnet'].lbl.config(text=lbl)

    def _update_ess_bars(self, d):
        """Push fetched usage onto the essential-row bars with the right
        reset-label form for the current bar count."""
        if not d:
            return
        fh = d.get('five_hour')
        sd = d.get('seven_day')
        sp, sr, _ = scoped_model(d)
        self.ess_bars['session'].update(fh['utilization'] if fh else None,
                                        fh.get('resets_at') if fh else None)
        self.ess_bars['weekly'].update(sd['utilization'] if sd else None,
                                        sd.get('resets_at') if sd else None)
        self.ess_bars['sonnet'].update(sp, sr)
        # After the data, not before: the form is chosen by measuring the text,
        # and on the first fetch there was none to measure.
        self._sync_ess_reset_mode()

    def _enter_ess_collapsed(self):
        """Lay out the selected bars side-by-side for collapsed essential mode.

        The stacked originals (s_session + extra_frame) are hidden and the
        ess_row strip is shown instead, each selected bar sharing the width
        left of the hamburger equally (halved for 2, thirded for 3). Drives the
        per-bar refresh dot via the normal countdown tick; the parenthetical
        countdown is off here.
        """
        bars = self._essential_bar_ids()
        self.s_session.frame.pack_forget()
        self.extra_frame.pack_forget()
        self.bottom_pad.pack_forget()
        self.ess_menu.pack_forget()
        for sec in self.ess_bars.values():
            sec.frame.pack_forget()
        n = len(bars)
        top_pad = 3  # same top spacing for single- and multi-bar (was cramped at 1)
        # Reserve the hamburger on the right first so the bars fill the
        # remaining width on the left (and the reset text clears the
        # bottom-right controls).
        self.ess_menu.pack(side='right', anchor='n', padx=(3, PAD),
                           pady=(top_pad, 0))
        self._draw_ess_menu(ESS_MENU_W)
        for i, b in enumerate(bars):
            sec = self.ess_bars[b]
            sec.set_compact(True)
            left = PAD if i == 0 else 3
            right = 3 if i == n - 1 else 0  # small gap before the hamburger
            sec.frame.pack(side='left', expand=True, fill='both',
                           padx=(left, right), pady=(top_pad, 0))
        self.ess_row.pack(fill='x')
        self.bottom_pad.pack(fill='x')
        self._reassert_error_order()
        # Re-render the bars with the reset-label form for this bar count.
        self._update_ess_bars(self._last_data)
        # Whole strip is draggable (Canvas skipped; right-click menu still
        # reaches the bars through the root <Button-3> binding).
        self._bind_drag(self.content)
        self._bind_drag(self.ess_row)
        for b in bars:
            self._bind_drag_section(self.ess_bars[b])

    def _reassert_error_order(self):
        """Keep the error panel below the bars after a content re-pack.

        _error() packs err_frame last; the layout swaps here forget+repack
        bottom_pad, which would otherwise leave a visible error message
        sitting ABOVE the bar. Re-pin it to the bottom when it is showing.
        """
        if self.err_frame.winfo_ismapped():
            self.err_frame.pack_forget()
            self.err_frame.pack(fill='x', pady=(4, 0))

    def _pack_stacked(self, all_bars=False):
        """Lay out bars stacked. Normal mode shows the selected 'bars to show';
        essential-expanded (all_bars=True) shows every bar, since expanding is
        the 'see everything' action. The caller owns drag binding and the
        bottom_pad height."""
        sel = ['session', 'weekly', 'sonnet'] if all_bars else self._essential_bar_ids()
        self._set_pulse(False)
        self._apply_dot_phase('off')
        self.ess_menu.pack_forget()
        self.ess_row.pack_forget()
        self.bottom_pad.pack_forget()
        self.s_session.frame.pack_forget()
        self.extra_frame.pack_forget()
        self.s_weekly.frame.pack_forget()
        self.s_sonnet.frame.pack_forget()
        if 'session' in sel:
            self.s_session.set_compact(False)
            self.s_session.frame.pack(fill='x', padx=PAD, pady=(3, 0))
        show_extra = False
        for code, sec in (('weekly', self.s_weekly), ('sonnet', self.s_sonnet)):
            if code in sel:
                sec.set_compact(False)
                sec.frame.pack(fill='x', padx=PAD, pady=(3, 0))
                show_extra = True
        if show_extra:
            self.extra_frame.pack(fill='x')
        self.bottom_pad.pack(fill='x')
        self._reassert_error_order()

    def _resize_bottom_anchored(self):
        """Fit the height to the content, keeping the bottom edge fixed so the
        widget grows upward (safe when parked on the taskbar)."""
        self.root.update_idletasks()
        w = self.root.winfo_width()
        bottom = self.root.winfo_y() + self.root.winfo_height()
        new_h = self.root.winfo_reqheight()
        self.root.geometry(f'{w}x{new_h}+{self.root.winfo_x()}+{bottom - new_h}')

    def _update_expand_visibility(self):
        """The expand dot applies only to essential mode: normal mode always
        shows the selected bars stacked, so there is nothing to expand there."""
        if self._essential:
            self.btn_expand.place(x=6, rely=1.0, y=-4, anchor='sw')
        else:
            self.btn_expand.place_forget()

    def _bind_drag(self, w):
        # Drag handlers only. The menu binding lives on root (_build) so
        # right-click opens it no matter where in the widget the user clicks
        # - including over the Canvas bar, where per-widget Button-3 bindings
        # previously didn't fire because Canvas is skipped in the drag bind.
        w.bind('<Button-1>', self._drag_start)
        w.bind('<B1-Motion>', self._drag_move)
        w.bind('<ButtonRelease-1>', self._save_geometry_user)

    def _unbind_drag(self, w):
        w.unbind('<Button-1>')
        w.unbind('<B1-Motion>')
        w.unbind('<ButtonRelease-1>')

    def _update_minsize(self, keep_height=False):
        """Single minimum width for both modes, tuned to essential-mode content.

        Both essential and normal mode use the same floor (whichever is wider
        between the essential content and the normal title bar). This keeps
        the essential-mode controls (close / refresh / time on the right)
        from clipping when the user switches modes, and the standard-mode
        title bar from clipping either.
        """
        self.root.update_idletasks()
        # Measured on the narrow form over the bars actually on screen, not on
        # whatever the label happens to be showing: the floor is how small the
        # user may drag the window, and below the full label the widget
        # switches forms rather than clipping (see _sync_ess_reset_mode).
        # Falls back to the rendered width before the first fetch, when there
        # is no reset time to measure yet.
        collapsed = self._essential and not self._expanded
        shown = ([self.ess_bars[b] for b in self._essential_bar_ids()
                  if b in self.ess_bars] if collapsed else [self.s_session])
        text_w = self._reset_width(shown, RESET_NO_PREFIX)
        tb_w = self.tb.winfo_reqwidth() + 8
        # The essential strip is allowed below the standard-mode floor: it has
        # no title bar to fit, and going small is the whole point of it. On the
        # way back to standard mode the block below widens the window again.
        # _strip_reserved is the same measure the label form is chosen with, so
        # the two cannot disagree about how much room the text really has.
        needed = max(MIN_W_ESS if collapsed else MIN_W,
                     text_w + self._strip_reserved(), tb_w)
        # Collapsed essential mode with multiple side-by-side bars needs more
        # width: current min already fits 2 bars (each halved); 3 bars need
        # +50% (one extra third) to stay readable.
        if self._essential and not self._expanded:
            n = len(self._essential_bar_ids())
            if n > 1:
                # Each bar must fit its own compact reset text, plus the
                # reserved hamburger column on the right. Multi-bar always
                # shows the dot (see _countdown_mode), so the bar itself must
                # also fit '13% 19:11' and the dot without overlap; the sync
                # time adds width when shown.
                bar_text_w = self._reset_floor_width(shown, RESET_COMPACT)
                # What the bar itself draws, measured. This used to be a pair
                # of constants (104 with the sync time, 92 without) that ran
                # wider than the real content, and since it is multiplied by
                # the bar count it decided the floor on its own: hiding the
                # reset text under the bars bought nothing at two or three
                # bars until this followed the content like everything else.
                inside = self._bar_content_w()
                # Each label carries its own inset on both sides, and the bars
                # are separated by a small gap.
                per_bar = max(bar_text_w + 2 * SUB_LABEL_PADX + 3, inside)
                # The strip reserves the hamburger column AND, on the row
                # below, the placed control block: whichever is wider decides,
                # and the block is measured so it follows the display scaling
                # while ESS_MENU_W does not.
                side = max(self._ess_row_reserve(n), self._strip_reserved())
                needed = max(needed, n * per_bar + side)
                if n >= 3:
                    needed = max(needed, int(round(MIN_W * 1.5)))
            else:
                # The hamburger takes the same reserved column here, and the
                # bar shrinks to make room for it, so the text needs that
                # width back. The bar interior has to be reserved as well, or
                # the shorter reset label would let the window shrink until the
                # percentage and countdown drawn inside the bar are the ones
                # that clip.
                needed = max(needed, text_w + self._strip_reserved(),
                             self._bar_content_w() + self._ess_row_reserve(1))
        self.root.minsize(needed, 0)
        # Widen to the minimum only when the current width is below it (never
        # more than needed, and never shrink the user's width). Anchor the
        # RIGHT edge so the extra width grows to the LEFT, keeping the menu /
        # hamburger on the right in place.
        cur_w = self.root.winfo_width()
        if cur_w > 1 and cur_w < needed:
            right = self.root.winfo_x() + cur_w
            y = self.root.winfo_y()
            # keep_height: the caller sets the height itself once the width has
            # landed (_flip_expand, _flip_essential), from the bottom edge the
            # widget had: a height written here would move that edge first.
            h = self.root.winfo_height() if keep_height else self.root.winfo_reqheight()
            self.root.geometry(f'{needed}x{h}+{right - needed}+{y}')
            # Apply it now. Tk would defer the move to an idle handler, and the
            # 10ms topmost re-assert can land first and drop it: the window
            # would take the new width but keep the old x, growing rightward
            # into whatever the user parked it next to.
            self.root.update_idletasks()

    def _auto_height(self):
        self.root.update_idletasks()
        x, y = self.root.winfo_x(), self.root.winfo_y()
        w = self.root.winfo_width()
        new_h = self.root.winfo_reqheight()
        self.root.geometry(f'{w}x{new_h}+{x}+{y}')

    def _restore_ess_width(self, on_launch=False, keep_height=False):
        """Essential-collapsed: set the width to the user's saved width for the
        current bar count (if wider than the minimum), else the minimum. Grows
        or shrinks, anchored to the right edge, and updates the height too."""
        if not (self._essential and not self._expanded):
            return
        self.root.update_idletasks()
        minw = self.root.minsize()[0]
        n = str(len(self._essential_bar_ids()))
        saved = self.cfg.get('ess_width', {}).get(n)
        if saved:
            target = max(minw, saved)
        elif on_launch and not self._has_usage:
            # No preference to apply, and before the first fetch the minimum is
            # only an estimate: the labels it is measured from do not exist
            # yet. Shrinking to it would narrow the strip on launch and snap it
            # back wide a second later, when the real ones arrive. Only launch
            # declines: a mode switch or a bar-count change is a deliberate
            # action and must still bring the strip back down, including on a
            # widget that is offline and has never had a fetch land.
            return
        else:
            target = minw
        cur = self.root.winfo_width()
        if cur > 1 and cur != target:
            right = self.root.winfo_x() + cur
            y = self.root.winfo_y()
            # See _update_minsize: the caller sets the height itself.
            h = self.root.winfo_height() if keep_height else self.root.winfo_reqheight()
            self.root.geometry(f'{target}x{h}+{right - target}+{y}')
            # Apply it now, for the reason spelled out in _update_minsize: Tk
            # defers the move to an idle handler, the 10ms topmost re-assert
            # can land first and drop it, and the window would then take the
            # new width while keeping the old x. Two of the four callers add
            # this flush for their own reasons; the other two used to lose the
            # move, which walked the strip's right edge sideways.
            self.root.update_idletasks()

    # ── Resize via dot drag ────────────────────────

    def _resize_start(self, e):
        self._hide_tooltips()
        self._rs_off = False
        self._rs_x = e.x_root
        self._rs_y = e.y_root
        self._rs_w = self.root.winfo_width()
        self._rs_h = self.root.winfo_height()
        # Drop the translucency for the drag: a layered (alpha < 1) window is
        # recomposited in full every frame while resizing, which is what makes
        # the edge and the placed controls lag. Restored on release.
        self.root.attributes('-alpha', 1.0)

    def _resize_move(self, e):
        # Width-only resize: keep the current height (no relayout) so each drag
        # event is cheap and the window edge tracks the cursor without lagging.
        # Honors the dynamic minimum from _update_minsize so the content never
        # clips when dragging narrow.
        if self._rs_off:
            return
        min_w = self.root.wm_minsize()[0] or MIN_W
        w = max(min_w, self._rs_w + (e.x_root - self._rs_x))
        x, y = self.root.winfo_x(), self.root.winfo_y()
        self.root.geometry(f'{w}x{self._rs_h}+{x}+{y}')

    def _resize_double(self, e):
        """A double click on the resize dot: the mode switch. Its second
        press is still the gesture the first one began, and a move before
        the release gave the new mode the old one's height from that first
        press (_resize_move), saved on the release. The animation used to
        write over it; the widget now changes in one step, and nothing did
        (review of 2026-09-28). The rest of this gesture resizes nothing."""
        self._rs_off = True
        self._toggle_essential()

    # ── Data ─────────────────────────────────────────

    def refresh(self):
        # One fetch at a time. A slow network can now cost a minute per fetch
        # (a retried request, twice over) and the refresh interval goes down to
        # 10s, so without this the worker threads would pile up. Queued rather
        # than dropped: refresh() is also how an account switch asks for its
        # data, and that request must not be lost.
        with self._fetch_lock:
            if self._fetch_busy:
                self._fetch_pending = True
                wlog('FETCH  queued: a fetch is already running')
                return
            self._fetch_busy = True
            self._fetch_pending = False
        if self._countdown_job:
            self.root.after_cancel(self._countdown_job)
            self._countdown_job = None
        self._set_pulse(False)
        self._apply_dot_phase('off')
        self.btn_r.config(fg=BLUE)
        for tgt in self._countdown_targets():
            tgt.set_countdown('\u2022\u2022\u2022')
        try:
            threading.Thread(target=self._fetch, daemon=True).start()
        except Exception as e:
            # The flag is released by the thread, so a thread that never
            # started would leave the widget unable to refresh again.
            with self._fetch_lock:
                self._fetch_busy = False
            wlog(f'FETCH  could not start the worker: {e}')

    def _fetch(self):
        wlog('FETCH  thread started')
        try:
            self._fetch_once()
        finally:
            # Always released, or the widget would never refresh again. Read
            # and cleared under the lock: a refresh queued at this exact
            # moment must not fall between the two.
            with self._fetch_lock:
                self._fetch_busy = False
                pending, self._fetch_pending = self._fetch_pending, False
            if pending:
                try:
                    self.root.after(0, self.refresh)
                except (tk.TclError, RuntimeError):
                    pass

    def _fetch_once(self):
        # The account this read is for. An answer that lands after the account
        # on show has changed describes the previous one and is dropped: it
        # used to be drawn under the new account's name (review of 2026-09-26).
        gen = self._fetch_gen
        try:
            data, rotation = fetch_usage(self.cfg)
            wlog('FETCH  data received, dispatching to main thread')
            self.root.after(0, self._on_data, data, gen)
            if rotation:
                # By account id, so it lands on the account it was issued for
                # whatever is on show by then.
                self.root.after(0, self._apply_rotated_key, *rotation)
        except CCLoginElsewhere as e:
            wlog('FETCH  claude code is signed in as another account')
            self._after_fetch(gen, lambda em=e.email: self._error(
                self._elsewhere_text(em), t('menu_accounts'), self._accounts_dialog))
        except CCLoginExpired as e:
            # Not the session key: an account read only through Claude Code
            # has none, and the button to renew one led nowhere.
            wlog('FETCH  the claude code login has expired')
            self._after_fetch(gen, lambda msg=str(e): self._error(msg))
        except CCNoLogin as e:
            # No login at all, which is not a session key to renew: the
            # account may not have one (review of 2026-09-26).
            wlog('FETCH  no claude code login on this computer')
            self._after_fetch(gen, lambda msg=str(e): self._error(msg))
        except PermissionError:
            wlog('FETCH  session expired (401/403)')
            self._after_fetch(gen, lambda: self._error(
                t('session_expired'), t('action_renew_now'), self._renew_session))
        except NoActivePlan as e:
            wlog('FETCH  the account has no active plan')
            self._after_fetch(gen, lambda msg=str(e): self._no_plan(msg))
            if e.rotation:
                self.root.after(0, self._apply_rotated_key, *e.rotation)
        except CCNoOrg as e:
            wlog('FETCH  the linked login does not name its organisation')
            self._after_fetch(gen, lambda msg=str(e): self._error(
                msg, t('menu_accounts'), self._accounts_dialog))
        except RateLimited as e:
            # 🔴 Not a fault: a wait (see _on_rate_limited).
            msg = _redact(e)
            wlog(f'FETCH  {msg}')
            self._after_fetch(gen, lambda: self._on_rate_limited(msg))
        except Exception as e:
            # Redacted: an exception raised below the transport can still carry
            # the request that produced it, and this text is both logged and
            # shown on screen.
            msg = _redact(e)
            wlog(f'FETCH  exception: {msg}')
            self._after_fetch(gen, lambda: self._error(msg))

    def _after_fetch(self, gen, show):
        """Hand a fetch's outcome to the main thread, unless the account on
        show has changed since the fetch started."""
        def deliver():
            if gen != self._fetch_gen:
                wlog('FETCH  dropped an answer about the account shown before')
                return
            show()
        try:
            self.root.after(0, deliver)
        except (tk.TclError, RuntimeError) as e:
            wlog(f'FETCH  could not report back: {_redact(str(e))}')

    def _elsewhere_text(self, email):
        """What to do when Claude Code is signed in as another account, which
        depends on what exists: a saved login of this account can be switched
        back to; without one, the way back is to sign in again. The same
        sentence in every language (the Japanese used to give other advice)."""
        acc = active_account(self.cfg) or {}
        try:
            saved = cc_accounts.has_profile_for_org(acc.get('org_id'))
        except Exception:
            saved = False
        return t('cc_elsewhere_switch' if saved else 'cc_elsewhere_signin').format(
            email=email or '?')

    def _no_plan(self, msg):
        """A lapsed subscription is a state of the account, not a fault.

        The bars empty (their numbers belonged to a plan that is over), the
        word under them says why, and the panel says what to do. It used to
        read as "error" with the old numbers frozen (review of 2026-09-26).
        """
        for bar in self._all_sections():
            bar.clear()
        self._has_usage = False
        self._last_data = None
        self._last_session_pct = None
        self._push_taskbar_state()
        self._error(msg + ' ' + t('plan_inactive_next'),
                    t('menu_open_claude'), self._open_claude_usage,
                    word=t('no_plan_short'))

    def _on_rate_limited(self, msg):
        """A rate limit is a wait, not a fault.

        With numbers on screen they stay, the widget goes back to rest instead
        of showing "refreshing" for the whole pause, and the reason is a soft
        note in the bars' hover text rather than a red panel (review of
        2026-09-26: busy for five minutes, stale numbers, no reason anywhere).
        With nothing on screen yet there is nothing to keep, and it is said.
        """
        if not self._has_usage:
            self._error(msg)
            return
        if self._last_error or any(sec._err for sec in self._all_sections()):
            # An error from before the pause is no longer true, and it kept
            # its panel, its hover text and its click for the whole five
            # minutes, while the retry time appeared nowhere (review of
            # 2026-09-26).
            self._clear_error()
        self._soft_note = msg
        self.btn_r.config(fg=DIM)
        self._start_countdown()

    def _apply_rotated_key(self, acct_id, new_key, used_key=None):
        """Persist a key Claude.ai rotated during a fetch. Runs on the main
        thread. Writes the key onto the exact account it was issued for (by id),
        never onto 'whatever account is active now' - the user may have switched
        mid-fetch. The top-level mirror is touched only if that account is still
        the active one.

        The value is checked once more here, at the point that actually
        writes: this is the only path that replaces a working key without the
        user asking, so it is the one place where a wrong value is silent and
        permanent."""
        if not plausible_key(new_key):
            wlog('FETCH  refused a rotated key that does not look like one')
            return
        try:
            accounts = self.cfg.get('accounts', [])
            if acct_id is None:          # a config of before the accounts: the mirror
                old = self.cfg.get('session_key')
            else:
                old = next((a.get('session_key') for a in accounts
                            if a.get('id') == acct_id), None)
            if used_key is not None and old != used_key:
                # The account's key changed while the request was out (a key
                # updated by hand): the rotation is about a key it no longer
                # holds, and spread from the new one it reached accounts of
                # another login (review of 2.11, first iteration).
                wlog('FETCH  rotated key dropped: the account holds another key now')
                return
            shared = 0
            for a in accounts:
                if a.get('id') == acct_id:
                    a['session_key'] = new_key
                elif old and a.get('session_key') == old:
                    # The key is the login's, not the org's: an account of
                    # the same login in another org (a personal and a team
                    # one, #16) held the old key too, and would be left with
                    # one claude.ai has retired.
                    a['session_key'] = new_key
                    shared += 1
            if (acct_id is None or self.cfg.get('active_account') == acct_id
                    or (old and self.cfg.get('session_key') == old)):
                self.cfg['session_key'] = new_key
            if old:
                # The same login's key: what was read with the old one stays
                # its own (the resets on an open page, _reset_card), or a
                # rotation while the page was open turned the reset's button
                # into "the account changed" (review of 2.11, second iteration).
                self.__dict__.setdefault('_rotated_prints', {})[
                    cc_accounts.fingerprint(old)] = cc_accounts.fingerprint(new_key)
            save_cfg(self.cfg)
            wlog(f'FETCH  rotated session key persisted ({len(new_key)} chars'
                 + (f', {shared} more account(s) of the same login' if shared else '') + ')')
        except Exception as e:
            # A persist failure must not surface as a fetch error: the usage
            # data already arrived fine and the in-memory key stays valid.
            wlog(f'FETCH  rotated key persist failed: {e}')

    def _on_data(self, d, gen=None):
        if gen is not None and gen != self._fetch_gen:
            wlog('FETCH  dropped an answer about the account shown before')
            return
        # _restore_ess_width declines to size the strip on launch, because
        # before any data the floor is measured from labels that do not exist
        # yet. That is a deferral, not a skip: this is where it is honoured,
        # once, now that the real labels are on screen. Without it a width the
        # floor had pushed the window to was carried into every later session,
        # since nothing else ever narrows the strip.
        first_fetch = not self._has_usage
        self._has_usage = True
        self._soft_note = None
        self._clear_error()
        fh = d.get('five_hour')
        self.s_session.update(fh['utilization'] if fh else None,
                              fh.get('resets_at') if fh else None)
        sd = d.get('seven_day')
        self.s_weekly.update(sd['utilization'] if sd else None,
                             sd.get('resets_at') if sd else None)
        sp, sr, sname = scoped_model(d)
        self.s_sonnet.update(sp, sr)
        self._apply_model_label(sname)
        # Mirror the same data onto the essential-row bars (shown only in
        # collapsed essential mode, but kept in sync so the switch is instant).
        self._last_data = d
        self._update_ess_bars(d)
        # Collect reset times for instant refresh when they arrive. Only times
        # still to come: one already past would fire a refresh at once, and at
        # every tick after it for as long as a reused answer carried it.
        self._resets_at = []
        now_utc = datetime.now(timezone.utc)
        for rs in (fh.get('resets_at') if fh else None,
                   sd.get('resets_at') if sd else None, sr):
            if rs:
                try:
                    when = datetime.fromisoformat(rs)
                except (ValueError, TypeError):
                    continue
                if when.tzinfo is not None and when > now_utc:
                    self._resets_at.append(when)
        now = f'{datetime.now():%H:%M}'
        self._last_time = now
        self.btn_r.config(fg=DIM)
        wlog(f'FETCH  ok: session={fh["utilization"] if fh else "?"} weekly={sd["utilization"] if sd else "?"} {self._model_label}={sp if sp is not None else "?"}')
        # Threshold notifications on session usage (5-hour window).
        if fh and fh.get('utilization') is not None:
            self._check_thresholds(int(fh['utilization']),
                                   fh.get('resets_at'))
            self._last_session_pct = fh['utilization']
            self._push_taskbar_state()
        self._start_countdown()
        self._update_minsize()
        # Again, now that the countdown has written the clock into the strip's
        # control block: that block is what the label form is measured against,
        # and on the very first fetch it was still missing its widest part.
        self._sync_ess_reset_mode()
        if first_fetch:
            self._restore_ess_width()
        # Auto-save on each refresh (protection against kill), and only after
        # the floor above: _save_geometry stores a width above the minimum as
        # the user's preference for this bar count, and the pre-fetch floor is
        # systematically lower than the real one, so saving first made the very
        # first refresh invent a preference the user never set.
        self._save_geometry()

    TOAST_THRESHOLDS = (25, 50, 75, 90, 95, 100)

    @staticmethod
    def _stable_reset_key(resets_at):
        """Strip microseconds from claude.ai's resets_at ISO string.

        The API returns values like '2026-04-30T18:19:59.881978+00:00'
        and the microsecond portion drifts every fetch even though the
        underlying 5-hour session window is the same. Comparing the raw
        strings was making _check_thresholds think a new session had
        started on every refresh and re-fired the 25/50 toasts on every
        tick.
        """
        if not resets_at:
            return None
        # Keep YYYY-MM-DDTHH:MM:SS plus tz suffix (anything after '.' is
        # microseconds; cut everything between the dot and the next '+'
        # or '-' that introduces the tz offset).
        s = str(resets_at)
        if '.' in s:
            dot = s.index('.')
            tz_pos = -1
            for i, ch in enumerate(s[dot:], start=dot):
                if ch in ('+', '-', 'Z'):
                    tz_pos = i
                    break
            if tz_pos > 0:
                s = s[:dot] + s[tz_pos:]
            else:
                s = s[:dot]
        return s

    def _check_thresholds(self, percentage, resets_at):
        """Fire a Windows toast when the session usage crosses one of
        the configured thresholds (25 / 50 / 75 / 90 / 95 / 100 %).

        State (last threshold notified, current session reset time) lives
        in config so it survives widget restarts within the same 5-hour
        window. Counter resets automatically when a new session starts
        (resets_at changes - compared as a microsecond-stripped key so
        natural API-side jitter doesn't false-trigger the reset).

        An earlier version of this widget also pulsed the taskbar
        progress bar with TBPF_INDETERMINATE for ~1.5 s at each
        crossing. The animated stripes looked like a "loading"
        indicator with no obvious meaning, so the pulse was removed and
        the toast notification is the only cue now.
        """
        # Sync the saved session-reset key. On a mismatch (genuine new
        # five-hour session, OR the widget was off when the user
        # crossed one or more thresholds) we align the counter with
        # the highest threshold the current percentage has already
        # crossed - silencing thresholds that aren't fresh anymore.
        # If the user opens the widget at 60 % they've already passed
        # 25 / 50 hours / minutes ago, the toasts for those would feel
        # spurious; only 75 / 90 / 95 / 100 are still meaningful for
        # the rest of this session.
        # A fresh session at 0 % keeps `last = 0`, so 25 / 50 / ...
        # fire as it climbs.
        # Microsecond drift on resets_at is already filtered out by
        # _stable_reset_key, so this branch only runs on a real
        # session boundary or a cold widget startup.
        key = self._stable_reset_key(resets_at)
        if key and self.cfg.get('toast_session_reset_at') != key:
            new_last = 0
            for th in self.TOAST_THRESHOLDS:
                if percentage >= th:
                    new_last = th
            self.cfg['toast_session_reset_at'] = key
            self.cfg['toast_last_threshold'] = new_last
            save_cfg(self.cfg)
        if not self.cfg.get('notifications_enabled', True):
            return
        last_t = self.cfg.get('toast_last_threshold', 0)
        for threshold in self.TOAST_THRESHOLDS:
            if percentage >= threshold and last_t < threshold:
                wlog(f'TOAST  session crossed {threshold}% '
                     f'(now {percentage}%)')
                self._fire_threshold_toast(percentage, resets_at)
                last_t = threshold
        if last_t != self.cfg.get('toast_last_threshold', 0):
            self.cfg['toast_last_threshold'] = last_t
            save_cfg(self.cfg)

    def _fire_threshold_toast(self, percentage, resets_at):
        """Build the rich toast body (% + now + reset time + countdown)
        and ship it to Windows.

        Lines:
          [bold] Claude Usage
          Session: 75% reached at 14:30
          Resets at 16:45 (in 2h 15m)

        If we don't have a usable resets_at the second body line falls
        back to a simple "Session limit reached" string so the toast
        still surfaces the milestone.
        """
        now = datetime.now().strftime('%H:%M')
        reset_label = ''
        countdown_label = ''
        if resets_at:
            try:
                reset_dt = datetime.fromisoformat(resets_at)
                reset_label = reset_dt.astimezone().strftime('%H:%M')
                delta = reset_dt - datetime.now(timezone.utc)
                total_min = max(0, int(delta.total_seconds() // 60))
                hours, mins = divmod(total_min, 60)
                if hours > 0:
                    countdown_label = f'{hours}h {mins:02d}m'
                else:
                    countdown_label = f'{mins}m'
            except (ValueError, TypeError):
                pass
        line1 = t('toast_line_pct').format(pct=int(percentage), now=now)
        if reset_label and countdown_label:
            line2 = t('toast_line_reset').format(
                reset=reset_label, countdown=countdown_label)
        else:
            line2 = t('toast_line_no_reset')
        show_toast(t('toast_title'), [line1, line2])

    def _update_clock(self):
        """Update the current time in title bar and essential controls."""
        now = f'{datetime.now():%H:%M}'
        self.lbl_time.config(text=now)
        self.ess_time.config(text=now)

    def _start_countdown(self):
        """Start countdown timer to next refresh."""
        if self._countdown_job:
            self.root.after_cancel(self._countdown_job)
        self._countdown_secs = self.cfg.get('refresh_ms', REFRESH) // 1000
        self._tick_countdown()

    # ── Countdown display helpers ───────────────────

    def _essential_bar_ids(self):
        """Selected bars in fixed display order. Any bar (including session)
        may be hidden, but at least one is always shown, so an empty selection
        falls back to the session bar."""
        ids = self.cfg.get('essential_bars', ['session'])
        out = [b for b in ('session', 'weekly', 'sonnet') if b in ids]
        return out or ['session']

    def _countdown_mode(self):
        """Effective countdown mode. Multi-bar essential always uses the dot
        ('Essential'): the numeric countdown is too wide for narrow side-by-side
        bars, so the menu setting only applies to single-bar / normal mode."""
        mode = self.cfg.get('countdown_display', 'dot')
        if (self._essential and not self._expanded
                and len(self._essential_bar_ids()) > 1):
            return 'dot'
        return mode

    def _set_theme(self, name):
        """Remember the theme. It takes effect on the next start, and the menu
        offers that restart right below."""
        if self.cfg.get('theme', 'dark') == name:
            return
        self.cfg['theme'] = name
        save_cfg(self.cfg)
        wlog(f'THEME  theme -> {name}')

    def _restart_widget(self):
        """Relaunch and quit, so a new theme is built into the interface.

        Order matters. The geometry is written first, because the new process
        reads the config while this one is still alive. Then the
        single-instance lock is let go: the new process checks it on start,
        and with this one still holding it the child would raise this window
        and exit, leaving nothing running a moment later. If the lock cannot
        be released, nothing is restarted and the widget stays up.
        """
        self._save_geometry()
        mutex = globals().get('_mutex')
        if mutex:
            try:
                k32 = ctypes.WinDLL('kernel32', use_last_error=True)
                k32.ReleaseMutex(mutex)
                k32.CloseHandle(mutex)
                globals()['_mutex'] = None
            except Exception as e:
                wlog(f'RESTART single-instance lock not released: {e}')
                return
        try:
            args = ([sys.executable] if getattr(sys, 'frozen', False)
                    else [sys.executable, os.path.abspath(__file__)])
            subprocess.Popen(args, creationflags=subprocess.DETACHED_PROCESS
                             | subprocess.CREATE_NEW_PROCESS_GROUP, close_fds=True)
        except Exception as e:
            wlog(f'RESTART failed: {e}')
            return
        wlog('RESTART relaunched, quitting')
        self._quit()

    def _toggle_reset_part(self, part, close=True):
        """Show or hide one half of the line under the bars: the clock the
        window resets at, or the time left until it does.

        Both halves off is allowed. The strip then narrows to what the bars
        themselves need, which is the point of the setting on a multi-bar
        strip, and the reading is still one hover away (see _bar_tip). The
        floor is measured from this text, so everything that depends on it is
        recomputed here.
        """
        key = 'show_reset_time' if part == 'time' else 'show_reset_left'
        new = not self.cfg.get(key, True)
        self.cfg[key] = new
        save_cfg(self.cfg)
        set_reset_parts(self.cfg.get('show_reset_time', True),
                        self.cfg.get('show_reset_left', True))
        wlog(f'RESET  {key} -> {new}')
        if close:
            self._close_menu()
        for sec in self._all_sections():
            sec.redraw_reset()
        if self._essential and not self._expanded:
            self._enter_ess_collapsed()
            self._update_minsize()
            # Come down to the narrower floor the change just allowed, unless
            # the user has a width of their own for this bar count.
            self._restore_ess_width()
            self._auto_height()
        else:
            self._update_minsize()
        self._sync_ess_reset_mode()

    def _all_sections(self):
        """Every Section instance (originals + the essential-row bars)."""
        secs = [self.s_session, self.s_weekly, self.s_sonnet]
        ess = getattr(self, 'ess_bars', None)
        if ess:
            secs.extend(ess.values())
        return secs

    def _countdown_targets(self):
        """Sections that currently display the live refresh countdown / dot."""
        ess = getattr(self, 'ess_bars', None)
        if ess and self._essential and not self._expanded:
            return [ess[b] for b in self._essential_bar_ids()]
        return [self.s_session]

    def _apply_dot_phase(self, phase):
        """Set the dot phase on the active targets; clear it everywhere else
        so a stale dot never lingers after a mode/layout switch."""
        targets = self._countdown_targets()
        for sec in self._all_sections():
            sec.set_dot_phase(phase if sec in targets else 'off')

    def _set_pulse(self, active):
        """Start/stop the breathing animation of the refresh dot(s)."""
        if active:
            if self._pulse_job is None:
                self._pulse_phase = 0.0  # start faded-out, breathe in
                self._pulse_tick()
        elif self._pulse_job is not None:
            self.root.after_cancel(self._pulse_job)
            self._pulse_job = None

    def _pulse_tick(self):
        """Breathing fade for the pre-refresh dot on all targets. Slow (~3s
        cycle) while more than 10s remain, faster (~1s) in the final 10s.
        Cosine fade between fully invisible (0) and solid green (1) so it
        reads as a real appear/disappear pulse."""
        period = 1.0 if self._countdown_secs <= 10 else 3.0
        self._pulse_phase = (self._pulse_phase + 0.05 / period) % 1.0
        level = (1 - math.cos(2 * math.pi * self._pulse_phase)) / 2
        for tgt in self._countdown_targets():
            tgt.set_dot_level(level)
        self._pulse_job = self.root.after(50, self._pulse_tick)

    def _tick_countdown(self):
        """Update countdown with adaptive cadence:
          s >  60: tick every 30s
          30 < s <= 60: tick every 10s (so display updates at 60, 50, 40, 30)
          s <= 30: tick every 1s
        """
        now_utc = datetime.now(timezone.utc)
        for rt in self._resets_at:
            if rt <= now_utc:
                wlog('RESET  reset time reached, refreshing now')
                self._resets_at = []
                self._countdown_job = None
                self._set_pulse(False)
                self._apply_dot_phase('off')
                self.refresh()
                return
        s = self._countdown_secs
        self._update_clock()
        mode = self._countdown_mode()
        if s > 0:
            # Compose the countdown text shown on the bar / header per mode.
            # 'dot' and 'hidden' both keep the last-update time visible; 'dot'
            # adds the breathing dot instead of the numeric parenthetical.
            show_time = self.cfg.get('show_sync_time', True)
            tpref = f'{self._last_time} ' if show_time else ''
            if mode in ('dot', 'hidden'):
                cd_txt = self._last_time if show_time else ''
            elif s >= 60:
                m, sec = divmod(s, 60)
                cd_txt = f'{tpref}({m}min {sec:02d}s)'
            else:
                cd_txt = f'{tpref}({s}s)'
            for tgt in self._countdown_targets():
                tgt.set_countdown(cd_txt)
            # Pre-refresh breathing dot (dot mode): appears at <=30s and
            # breathes; _pulse_tick speeds it up under <=10s from the live
            # remaining seconds.
            if mode == 'dot' and s <= 30:
                self._apply_dot_phase('on')
                self._set_pulse(True)
            else:
                self._apply_dot_phase('off')
                self._set_pulse(False)
            # Schedule the next tick with adaptive cadence.
            if s > 60:
                # Snap to the next 30s tick boundary on the way down to 60.
                skip = min(30, s - 60)
                self._countdown_secs -= skip
                self._countdown_job = self.root.after(skip * 1000, self._tick_countdown)
            elif s > 30:
                # 60 -> 50 -> 40 -> 30 - one tick every 10s.
                skip = min(10, s - 30)
                self._countdown_secs -= skip
                self._countdown_job = self.root.after(skip * 1000, self._tick_countdown)
            else:
                self._countdown_secs -= 1
                self._countdown_job = self.root.after(1000, self._tick_countdown)
        else:
            for tgt in self._countdown_targets():
                tgt.set_countdown('')
            self._apply_dot_phase('off')
            self._set_pulse(False)
            self._countdown_job = None

    def _clear_error(self):
        """Hide the error panel and drop any pending action binding."""
        had_panel = bool(self.err_frame.winfo_manager())
        self._last_error = None
        self.err_btn.pack_forget()
        self.err_frame.pack_forget()
        if had_panel:
            self._fit_panel_height()
        # Also when the strip skipped the fit: the next panel chooses afresh.
        self._panel_down = None
        for sec in self._all_sections():
            sec.set_error(False)
        if self._err_action is not None:
            try:
                self.err_btn.unbind('<Button-1>')
            except Exception:
                pass
            self._err_action = None

    def _error_panel_height(self):
        """What the error panel adds to the window now: 0 when it is not
        shown, and in the collapsed strip, which keeps its size."""
        try:
            if self.err_frame.winfo_manager() and not (self._essential
                                                       and not self._expanded):
                return self.err_frame.winfo_height() + 4    # its pady above
        except (tk.TclError, AttributeError):
            pass
        return 0

    def _fit_panel_height(self):
        """Give the window the height its content now asks for, growing
        upward so a widget parked on the taskbar stays where it is. The error
        panel used to be packed into a window of fixed height and drawn below
        its bottom edge, the explanation and its button with it (review of
        2026-09-26). The collapsed strip has no room for the panel and keeps
        its size: there the bars say it, and a click on one does it.

        Upward only while there is room: at the top of its monitor the widget
        grows downward instead, or its title bar, the one handle to drag it
        by, went off the screen with the error (third iteration). The way it
        grew when the panel appeared holds until the panel goes, so it
        shrinks back the same way, and _save_geometry takes the panel off the
        right edge."""
        if self._essential and not self._expanded:
            return
        try:
            self.root.update_idletasks()
            w, h = self.root.winfo_width(), self.root.winfo_height()
            x, y = self.root.winfo_x(), self.root.winfo_y()
            new_h = self.root.winfo_reqheight()
            showing = bool(self.err_frame.winfo_manager())
            down = self._panel_down
            if showing and down is None:
                down = (y + h - new_h) < self._widget_monitor_area()[1]
                self._panel_down = down
            new_y = y if down else y + h - new_h
            self.root.geometry(f'{w}x{new_h}+{x}+{new_y}')
            if not showing:
                self._panel_down = None
        except tk.TclError:
            pass

    def _error(self, msg, action_label=None, action_cmd=None, word=None):
        """Show an error panel. If action_label/action_cmd are given, render a
        button below. `word` is what the bars say under them, when "error" is
        not the right word (a lapsed plan is not a fault)."""
        # The message can carry an e-mail (whose login Claude Code is on):
        # the screen shows it, the log does not keep it.
        wlog(f'ERROR  {_redact(msg)}')
        # Kept for the bars' hover text: in the strip the panel is not on
        # show, and the bars said "error" with no way to learn why.
        self._last_error = msg
        self.lbl_err.config(text=msg)
        self.err_btn.pack_forget()
        if self._err_action is not None:
            try:
                self.err_btn.unbind('<Button-1>')
            except Exception:
                pass
            self._err_action = None
        if action_label and action_cmd:
            self._err_action = action_cmd
            self.err_btn.config(text=f' {action_label} ')
            self.err_btn.bind('<Button-1>', lambda e: action_cmd())
            self.err_btn.pack(anchor='w', padx=PAD, pady=(0, 4))
        self.err_frame.pack(fill='x', pady=(4, 0))
        self._fit_panel_height()
        self._set_pulse(False)
        self._apply_dot_phase('off')
        # Under every bar, and the countdown inside them goes quiet: the word
        # belongs on the line that carries the bar's words, not in the pill.
        for sec in self._all_sections():
            sec.set_error(True, word)
        for tgt in self._countdown_targets():
            tgt.set_countdown('')
        self.btn_r.config(fg=DIM)
        self._update_minsize()

    def _schedule(self):
        # One chain only: setup after the last account was removed added a
        # second one beside the start-up chain, and every tick ran twice for
        # the rest of the session (review of 2026-09-26).
        if getattr(self, '_job', None):
            try:
                self.root.after_cancel(self._job)
            except (tk.TclError, ValueError):
                pass
        ms = self.cfg.get('refresh_ms', REFRESH)
        self._job = self.root.after(ms, self._schedule_tick)

    def _schedule_tick(self):
        wlog('SCHED  tick -> scheduled refresh')
        try:
            self.refresh()
        except Exception as e:
            wlog(f'SCHED  refresh error: {e}')
        try:
            # A sign-in made while the widget was running is caught here; one
            # made while it was closed is caught by the first tick.
            self._cc_watch_login()
        except Exception as e:
            wlog(f'CC     login watch error: {_redact(str(e))}')
        self._schedule()

    # ── Drag ─────────────────────────────────────────

    def _hide_tooltips(self):
        """Close any hover tooltip. Called when a gesture starts, so a tip
        cannot sit over the widget while it is being moved or resized."""
        for hide in self._tooltip_hides:
            try:
                hide()
            except Exception:
                pass

    def _drag_start(self, e):
        self._hide_tooltips()
        self._dx, self._dy = e.x, e.y

    def _drag_move(self, e):
        x = self.root.winfo_x() + e.x - self._dx
        y = self.root.winfo_y() + e.y - self._dy
        self.root.geometry(f'+{x}+{y}')

    def _on_anchor_monitor(self):
        """True if the widget currently sits on its anchor's monitor, or has no
        anchor yet. False when it is displaced onto a different monitor (a
        temporary off-screen rescue), so the home anchor is kept and the widget
        can return home once its monitor is back."""
        a = self.cfg.get('anchor')
        if not isinstance(a, dict):
            return True
        cur = _monitor_of(self.cfg['x'], self.cfg['y'],
                          self.cfg['width'], self.cfg['height'],
                          _MONITOR_DEFAULTTONULL)
        return cur is not None and cur[0] == a.get('device')

    def _save_geometry(self, e=None, update_anchor=None, user=False):
        """Save current position, size and mode. The 'home' anchor is refreshed
        only when the widget is on its own anchor monitor (or has none yet):
        a deliberate drag/resize passes update_anchor=True; a save taken while
        the widget sits rescued on another monitor leaves the anchor untouched
        so it can return home later."""
        # Resting state is translucent; restore it after a resize drag (which
        # turns the window opaque for smoothness) ends on this release.
        try:
            self.root.attributes('-alpha', 0.94)
        except Exception:
            pass
        try:
            self.cfg['x'] = self.root.winfo_x()
            self.cfg['y'] = self.root.winfo_y()
            self.cfg['width'] = self.root.winfo_width()
            self.cfg['height'] = self.root.winfo_height()
            # The error panel is transient: the window grew upward to show it
            # (_fit_panel_height), and saved as it was it reopened that much
            # higher, climbing a panel at every quit made while an error was
            # up (review of 2026-09-26). The home is the window without it.
            panel = self._error_panel_height()
            if panel:
                if not self._panel_down:
                    self.cfg['y'] += panel
                self.cfg['height'] = max(1, self.cfg['height'] - panel)
            self.cfg['expanded'] = self._expanded
            self.cfg['essential'] = self._essential
            # Per-bar-count width memory (essential-collapsed only): remember a
            # width the user set WIDER than the minimum, keyed by how many bars
            # are shown, so it is restored when that bar count is shown again.
            # A width at the minimum stores nothing (means "no preference").
            # Only a save the user caused may touch it. The floor follows the
            # reset text, so it rises on its own as a countdown grows and the
            # window widens with it; an automatic save wrote that width down as
            # a deliberate choice, and _restore_ess_width then handed it back at
            # every mode change and every launch. The user's own width always
            # comes through here: the resize drag ends on _save_geometry_user.
            if user and self._essential and not self._expanded:
                n = str(len(self._essential_bar_ids()))
                minw = self.root.minsize()[0]
                store = self.cfg.setdefault('ess_width', {})
                if self.cfg['width'] > minw + 2:
                    store[n] = self.cfg['width']
                elif n in store:
                    del store[n]
            if update_anchor is None:
                update_anchor = self._on_anchor_monitor()
            if update_anchor:
                anchor = self._compute_anchor(
                    self.cfg['x'], self.cfg['y'],
                    self.cfg['width'], self.cfg['height'])
                if anchor:
                    self.cfg['anchor'] = anchor
            save_cfg(self.cfg)
        except Exception as ex:
            wlog(f'SAVE   save_geometry error: {ex}')

    def _save_geometry_user(self, e=None):
        """Move-drag release: persist geometry AND refresh the home anchor (the
        user deliberately moved the widget).

        Not user=True: that flag governs the per-bar-count WIDTH memory, and a
        move says nothing about width. The floor follows the reset text and can
        push the window wider on its own; without this distinction the next time
        the user nudged the widget across the desktop, that width would be
        written down as a deliberate choice and handed back at every launch.
        """
        self._save_geometry(e, update_anchor=True)

    def _save_geometry_resize(self, e=None):
        """Resize-dot release: the one gesture that states a width, so the one
        that may write (or clear) the saved width for this bar count."""
        self._save_geometry(e, update_anchor=True, user=True)

    def _geometry_watchdog(self):
        """Keep the widget where the user put it across live monitor changes.

        Repositions only when the set of connected monitors changes, so it never
        fights manual dragging. On a layout change: if the home monitor is back,
        return the widget to its anchored spot; if the home spot is now off every
        screen, rescue it onto the primary. Between layout changes only an
        all-off-screen safety net can act (which normal use never triggers).
        Either way the home anchor is preserved (save with update_anchor=False),
        so a temporary rescue never erases where the user placed it."""
        try:
            x, y = self.root.winfo_x(), self.root.winfo_y()
            w, h = self.root.winfo_width(), self.root.winfo_height()
            sig = _monitor_signature()
            if sig != self._mon_sig:
                self._mon_sig = sig
                target = self._resolve_anchor(w, h) or (x, y)
                nx, ny, _moved = _place_on_screen(target[0], target[1], w, h)
                if (nx, ny) != (x, y):
                    wlog(f'WATCH  layout changed -> move ({x},{y}) to ({nx},{ny})')
                    self.cfg['x'], self.cfg['y'] = nx, ny
                    self.root.geometry(f'+{nx}+{ny}')
                    self.root.update_idletasks()
                    self._save_geometry()
            else:
                nx, ny, moved = _place_on_screen(x, y, w, h)
                if moved:
                    wlog(f'WATCH  off all monitors ({x},{y}) -> rescued to ({nx},{ny})')
                    self.cfg['x'], self.cfg['y'] = nx, ny
                    self.root.geometry(f'+{nx}+{ny}')
                    self.root.update_idletasks()
                    self._save_geometry()
        except Exception as ex:
            wlog(f'WATCH  geometry_watchdog error: {ex}')
        finally:
            self.root.after(GEOMETRY_WATCH_MS, self._geometry_watchdog)

    # ── Monitor-anchored position (survives layout / resolution changes) ──

    def _compute_anchor(self, x, y, w, h):
        """Describe the position relative to the nearest corner of the monitor
        it sits on, so it can be reproduced after a resolution or layout change.
        Uses monitor bounds (taskbar area included) so a widget parked on the
        taskbar keeps its edge placement."""
        info = _monitor_of(x, y, w, h, _MONITOR_DEFAULTTONEAREST)
        if not info:
            return None
        device, (ml, mt, mr, mb), _wk = info
        left = (x + w / 2) < (ml + mr) / 2
        top = (y + h / 2) < (mt + mb) / 2
        dx = int(x - ml) if left else int(mr - (x + w))
        dy = int(y - mt) if top else int(mb - (y + h))
        return {'device': device, 'mon': [ml, mt, mr, mb],
                'corner': ('t' if top else 'b') + ('l' if left else 'r'),
                'dx': dx, 'dy': dy}

    def _find_anchor_monitor(self, a):
        """Locate the saved anchor monitor among the connected ones: by device
        name first, then by identical bounds. Returns (l, t, r, b) or None."""
        mons = _enum_monitors()
        dev = a.get('device')
        if dev:
            for device, mon in mons:
                if device == dev:
                    return mon
        bounds = tuple(a.get('mon') or ())
        if bounds:
            for _device, mon in mons:
                if tuple(mon) == bounds:
                    return mon
        return None

    def _resolve_anchor(self, w, h):
        """Turn a saved anchor into an (x, y) on a currently-connected monitor,
        or None when there is no anchor or the anchor monitor is gone (then the
        raw saved coords + _place_on_screen take over)."""
        a = self.cfg.get('anchor')
        if not isinstance(a, dict):
            return None
        mon = self._find_anchor_monitor(a)
        if not mon:
            return None
        ml, mt, mr, mb = mon
        corner = a.get('corner', 'bl')
        dx = int(a.get('dx', 0))
        dy = int(a.get('dy', 0))
        top = corner[:1] == 't'
        left = corner[1:2] == 'l'
        x = (ml + dx) if left else (mr - dx - w)
        y = (mt + dy) if top else (mb - dy - h)
        return int(x), int(y)

    # ── Shared dialog / popup helpers ───────────────

    def _virtual_bounds(self):
        """Return (x, y, w, h) of the full virtual desktop (all monitors).

        winfo_screenwidth() only reports the primary monitor; a widget on a
        secondary display would otherwise be clamped back to primary. vroot*
        gives the full multi-monitor bounding box so popups stay with the
        widget.
        """
        return (
            self.root.winfo_vrootx(),
            self.root.winfo_vrooty(),
            self.root.winfo_vrootwidth(),
            self.root.winfo_vrootheight(),
        )

    def _widget_monitor_area(self):
        """Full bounds (l, t, r, b) of the monitor the widget currently sits on,
        falling back to the virtual desktop when it cannot be resolved.

        Full bounds, not the work area: the widget is meant to be parked on the
        taskbar, which sits outside the work area. Measuring against the work
        area would treat the widget as below the screen and push every popup up
        by the taskbar's height, leaving a large fixed gap above the widget.
        (_place_on_screen uses full bounds for the same reason.)
        """
        info = _monitor_of(self.root.winfo_x(), self.root.winfo_y(),
                           max(1, self.root.winfo_width()),
                           max(1, self.root.winfo_height()),
                           _MONITOR_DEFAULTTONEAREST)
        if info:
            return info[1]      # rcMonitor: taskbar area included
        vx, vy, vw, vh = self._virtual_bounds()
        return (vx, vy, vx + vw, vy + vh)

    def _place_popup(self, dw, dh, prefer='above'):
        """Position a popup next to the widget, preferring above it.

        Clamped to the work area of the monitor the widget is on, not to the
        virtual desktop: that bounding box spans every screen, so clamping to
        it let a popup straddle two monitors instead of staying whole on one.
        """
        ml, mt, mr, mb = self._widget_monitor_area()
        wx = self.root.winfo_x() + (self.root.winfo_width() - dw) // 2
        widget_top = self.root.winfo_y()
        widget_bottom = widget_top + self.root.winfo_height()
        if prefer == 'above':
            wy = widget_top - dh - SCREEN_MARGIN
            if wy < mt + SCREEN_MARGIN:
                wy = widget_bottom + SCREEN_MARGIN
        else:
            wy = widget_bottom + SCREEN_MARGIN
            if wy + dh > mb - TASKBAR_GAP:
                wy = widget_top - dh - SCREEN_MARGIN
        wx = max(ml + SCREEN_MARGIN, min(wx, mr - dw - SCREEN_MARGIN))
        wy = max(mt + SCREEN_MARGIN, min(wy, mb - dh - TASKBAR_GAP))
        return wx, wy

    def _place_submenu(self, dw, dh):
        """Right-aligned submenu anchored to the widget, virtual-desktop safe."""
        vx, vy, vw, vh = self._virtual_bounds()
        wx = self.root.winfo_rootx() + self.root.winfo_width() - dw
        widget_bottom = self.root.winfo_rooty() + self.root.winfo_height()
        if widget_bottom + dh > vy + vh - TASKBAR_GAP:
            # Not enough room below: open upward, flush above the widget top.
            wy = self.root.winfo_rooty() - dh - 2
        else:
            # Open downward, flush to the widget's actual bottom edge. (Was
            # widget_top + TITLE_H, which assumed a title bar and so misaligned
            # in essential mode, where there is none.)
            wy = widget_bottom + 2
        wx = max(vx + SCREEN_MARGIN, min(wx, vx + vw - dw - SCREEN_MARGIN))
        wy = max(vy + SCREEN_MARGIN, min(wy, vy + vh - dh - TASKBAR_GAP))
        return wx, wy

    def _build_titlebar(self, dlg, title):
        """Standard draggable title bar with close button. Same look everywhere."""
        tb = tk.Frame(dlg, bg=BG_TITLE, height=DLG_TB_HEIGHT)
        tb.pack(fill='x')
        tb.pack_propagate(False)
        title_lbl = tk.Label(tb, text=title, font=FT_DLG_TITLE, fg=FG,
                             bg=BG_TITLE, padx=12)
        title_lbl.pack(side='left')
        close_btn = tk.Label(tb, text='\u2715', font=('Segoe UI', 10),
                             fg=DIM, bg=BG_TITLE, cursor='hand2', padx=12, pady=4)
        close_btn.pack(side='right')
        close_btn.bind('<Button-1>', lambda e: self._close_dialog(dlg))
        close_btn.bind('<Enter>', lambda e: close_btn.config(fg=FG, bg=CLOSE_HV))
        close_btn.bind('<Leave>', lambda e: close_btn.config(fg=DIM, bg=BG_TITLE))

        def drag_s(e): dlg._dx, dlg._dy = e.x, e.y

        def drag_m(e): dlg.geometry(
            f'+{dlg.winfo_x()+e.x-dlg._dx}+{dlg.winfo_y()+e.y-dlg._dy}')
        for w in (tb, title_lbl):
            w.bind('<Button-1>', drag_s)
            w.bind('<B1-Motion>', drag_m)
        return tb

    def dp(self, x):
        """Scale a 96-DPI baseline pixel value to the current display DPI."""
        return int(round(x * self.dpi_scale))

    def _dlg_size(self, w, h):
        """Scale a 96-DPI dialog size to the display DPI and clamp it to the
        widget's monitor, so a dialog never renders wider or taller than the
        screen. Fonts (points) and pill paddings (dp) already grow with DPI;
        the frame must grow with them or its content clips at 125%+ scaling."""
        ml, mt, mr, mb = self._widget_monitor_area()
        return (min(self.dp(w), (mr - ml) - 2 * SCREEN_MARGIN),
                min(self.dp(h), (mb - mt) - 2 * SCREEN_MARGIN))

    def _primary_pill(self, parent, text, cmd, enabled=True):
        """Primary pill button (Claude orange). Padding scales with DPI so
        the pill keeps proportional whitespace around the (point-scaled)
        text at any Windows DPI setting."""
        px = self.dp(PILL_PAD_PRIMARY_X)
        py = self.dp(PILL_PAD_PRIMARY_Y)
        if enabled:
            return make_pill_button(
                parent, text=text, font=FT_DLG_BTN_B,
                fg='#FFFFFF', bg=CLAUDE, hover_bg=PRIMARY_HV, cmd=cmd,
                padx=px, pady=py)
        return make_pill_button(
            parent, text=text, font=FT_DLG_BTN_B,
            fg=DIM, bg=BAR_BG, hover_bg=BAR_BG, cmd=lambda: None,
            padx=px, pady=py)

    def _chosen_pill(self, parent, text, cmd):
        """The selected option among small pills: same size, Claude fill."""
        return make_pill_button(
            parent, text=text, font=FT_DLG_HINT, fg='#ffffff', bg=CLAUDE,
            hover_bg=CLAUDE, cmd=cmd,
            padx=self.dp(12), pady=self.dp(5))

    def _pill_flow(self, parent, items, width):
        """Lay small pills left to right, wrapping to a new line when the next
        one would not fit. Label lengths differ by half between languages, so a
        single row is a guess that only holds in the language it was tried in.
        """
        rows = [tk.Frame(parent, bg=parent.cget('bg'))]
        rows[0].pack(fill='x')
        used = 0
        for label, cmd, chosen in items:
            maker = self._chosen_pill if chosen else self._small_pill
            pill = maker(rows[-1], label, cmd)
            pill.update_idletasks()
            need = pill.winfo_reqwidth() + self.dp(6)
            if used and used + need > width:
                pill.destroy()
                rows.append(tk.Frame(parent, bg=parent.cget('bg')))
                rows[-1].pack(fill='x', pady=(6, 0))
                used = 0
                pill = maker(rows[-1], label, cmd)
                pill.update_idletasks()
                need = pill.winfo_reqwidth() + self.dp(6)
            pill.pack(side='left', padx=(0, self.dp(6)))
            used += need
        return rows

    def _small_pill(self, parent, text, cmd):
        """Compact secondary pill, for rows that hold several actions."""
        return make_pill_button(
            parent, text=text, font=FT_DLG_HINT, fg=FG, bg=SOFT_BG,
            hover_bg=SOFT_BG_HV, cmd=cmd,
            padx=self.dp(12), pady=self.dp(5))

    def _outline_pill(self, parent, text, cmd, icon=None):
        """Secondary pill drawn as an outline in Claude orange.

        Used for the one action in a dialog that leaves the widget: it reads
        as a link rather than a command, without competing with the filled
        buttons next to it."""
        # The icon is an arrow glyph, so it takes the geometry font: the emoji
        # family draws it boxed, and the Japanese family draws it full-width.
        # The border scales with the display like the padding does, or it would
        # thin out to a hairline at 200% and above.
        return make_pill_button(
            parent, text=text, font=FT_DLG_BTN, fg=FG, bg=BG, hover_bg=SOFT_BG,
            cmd=cmd, outline=CLAUDE, outline_w=self.dp(1),
            icon=icon, icon_font=FT_DOT if icon else None,
            padx=self.dp(PILL_PAD_SECONDARY_X),
            pady=self.dp(PILL_PAD_SECONDARY_Y))

    def _secondary_pill(self, parent, text, cmd, icon=None):
        """Secondary pill button (soft surface). Padding scales with DPI."""
        return make_pill_button(
            parent, text=text, font=FT_DLG_BTN,
            fg=FG, bg=SOFT_BG, hover_bg=SOFT_BG_HV, cmd=cmd,
            icon=icon, icon_font=FT_EMOJI_11 if icon else None,
            padx=self.dp(PILL_PAD_SECONDARY_X),
            pady=self.dp(PILL_PAD_SECONDARY_Y))

    def _close_dialog(self, dlg):
        """Close a dialog from its X or from Escape, unless it asks to stay.

        A dialog sets `hold_open` to a callable while it is writing something
        that must not be abandoned halfway (a switch of the Claude Code
        account): closing it then would lose the result the user is waiting
        for, while the write went on regardless.
        """
        hold = getattr(dlg, 'hold_open', None)
        try:
            if hold is not None and hold():
                return
            dlg.destroy()
        except tk.TclError:
            pass

    # ── Showing windows whole (see "Showing a window whole" at the top) ──

    def _prepare(self, win, rounded=True):
        """Map a new toplevel off every screen and cloak it, its corners
        already rounded: from here on it is built, placed and painted where
        it belongs with nothing of it showing, until _present. Call it once
        overrideredirect is set. A window DWM will not cloak shows as it is
        placed, as every window used to."""
        win._revealed = False       # the user has seen it at least once
        win._rounded = rounded
        win._cloaked = False
        try:
            win.geometry(f'+{_PARK}+{_PARK}')
            win.update_idletasks()      # mapped out there: its frame exists now
            if rounded:
                dwm_round(win)
            win._cloaked = _set_cloak(_frame_of(win), True)
        except tk.TclError:
            return
        if not win._cloaked and not getattr(Widget, '_cloak_warned', False):
            Widget._cloak_warned = True
            wlog('SHOW   windows cannot be cloaked here: they show as they are placed')

    def _present(self, win, place, then=None, gone=None):
        """Show a prepared window, or one held still, once it is whole.

        `place` puts the window where it belongs and returns its geometry. It
        is called again until it no longer changes what it did last time,
        whoever called it then: a <Configure> handler can still change what
        the window needs (a row cutting its text to its column), and the
        height has to be the last one. After each placement the paint
        messages it caused are delivered at once (_paint_now), so that Tk
        draws the window once for the move and the paint together rather
        than once for each: measured, the account page was drawn three times
        on the way out. When nothing moves and nothing waits to be painted,
        the window is uncloaked in one go. `then` runs right after it shows;
        `gone` if it is destroyed before. A window still settling after half
        a second is shown as it is, and the log says so.

        The steps run on the main window's clock, not the popup's: a popup
        destroyed with a step pending would leave Tk a callback to nowhere.
        """
        raw = getattr(place, '_raw', place)

        def placed():
            geo = raw()
            win._geo = geo
            return geo

        placed._raw = raw
        win._place = placed
        run = getattr(win, '_presenting', None)
        if run is not None:
            # A run is under way: it takes this change in, and its callbacks.
            if then:
                run['then'].append(then)
            if gone:
                run['gone'].append(gone)
            return
        run = {'then': [then] if then else [], 'gone': [gone] if gone else [],
               'rounds': 0, 'until': time.monotonic() + 0.5}
        win._presenting = run

        def alive():
            try:
                return bool(win.winfo_exists())
            except tk.TclError:
                return False

        def late():
            return time.monotonic() > run['until']

        def finish(callbacks):
            win._presenting = None
            for fn in callbacks:
                try:
                    fn()
                except Exception as e:
                    wlog(f'SHOW   after showing a window: {type(e).__name__}: '
                         f'{_redact(str(e))}')

        def settle():
            if not alive():
                return finish(run['gone'])
            try:
                before = getattr(win, '_geo', None)
                geo = win._place()
                win.update_idletasks()
            except tk.TclError:
                return finish(run['gone'])
            _paint_now(win)
            if geo != before and run['rounds'] < 8 and not late():
                run['rounds'] += 1
                self.root.after(0, settle)
                return
            # A timer, queued behind the Expose events those paint messages
            # became: they are handled first, and show() draws what they asked
            # for before anything is uncloaked. Not an idle callback: every
            # update_idletasks runs those, and a change made meanwhile (a pill
            # button measures itself with one) ran show() in its middle and
            # uncloaked a list rebuilt halfway (review of 2026-09-27).
            self.root.after(0, show)

        def show():
            if not alive():
                return finish(run['gone'])
            try:
                win.update_idletasks()
                before = getattr(win, '_geo', None)
                geo = win._place()
                win.update_idletasks()
            except tk.TclError:
                return finish(run['gone'])
            if not late():
                if geo != before:
                    self.root.after(0, settle)
                    return
                if _unpainted(win):
                    _paint_now(win)
                    self.root.after(0, show)
                    return
            else:
                wlog('SHOW   a window was still settling after 500 ms: shown as it was')
            win._revealed = True
            if getattr(win, '_cloaked', False):
                try:
                    if getattr(win, '_rounded', True):
                        # Again, cheap: Windows has been seen to drop it on a move.
                        dwm_round(win)
                    _gdi_flush()
                    _set_cloak(_frame_of(win), False)
                except tk.TclError:
                    pass
                win._cloaked = False
            finish(run['then'])

        self.root.after(0, settle)

    def _hold(self, win):
        """Keep what the user sees of a window on show still while it changes.

        A still picture of it (_Still) takes its place and the real one is
        cloaked behind it; _present brings it back, and takes the picture
        away, once it is whole again. Call it BEFORE the change: what Tk has
        painted, the user has seen. Nothing to do for a window not on show
        yet, or one already held; without a picture (the window is on no
        screen, or Windows refused) the change shows as it always did."""
        if (not getattr(win, '_revealed', False) or getattr(win, '_still', None) is not None
                or getattr(win, '_presenting', None) is not None):
            return
        try:
            hwnd = _frame_of(win)
            still = _Still(hwnd, getattr(win, '_rounded', True))
        except Exception:
            return
        if not _set_cloak(hwnd, True):
            still.close()
            return
        win._cloaked = True
        win._still = still
        if not getattr(win, '_still_watch', False):
            # Closed while held (Save, Cancel, a window of its kind taking its
            # place): the picture goes with it at once, not at the next step.
            win._still_watch = True
            win.bind('<Destroy>',
                     lambda e: self._drop_still(win) if e.widget is win else None,
                     add='+')
        self.root.after_idle(lambda: self._present(
            win, win._place, then=lambda: self._drop_still(win),
            gone=lambda: self._drop_still(win)))

    @staticmethod
    def _drop_still(win):
        still = getattr(win, '_still', None)
        win._still = None
        if still is not None:
            still.close()

    def _take_still(self, win):
        """A still picture of a window on show, handed to whoever will stand
        in its place: its own, if it is held, or a new one. None when there
        is nothing on show to picture."""
        own = getattr(win, '_still', None)
        if own is not None:
            win._still = None
            return own
        if not getattr(win, '_revealed', False) or getattr(win, '_cloaked', False):
            return None
        try:
            return _Still(_frame_of(win), getattr(win, '_rounded', True))
        except Exception:
            return None

    def _build_dialog_frame(self, title, dw, dh, key=None):
        """Create a Toplevel with the standard chrome. Returns (dlg, body).

        `key` names the kind of window. Opening one while another of the same
        kind is still up closes the old one first: the same menu entry clicked
        twice used to stack two Accounts windows, and nothing is gained by
        having two of anything here. The new one is built from scratch rather
        than the old one raised, so it shows the current state. The old one
        still goes at once, as it always did, but a still picture of it stays
        on screen until the new one shows, and the new one opens where the old
        one was: a page drawn again after a gesture no longer blinks out and
        back.

        Same title bar, padding, rounded corners and screen-clamped placement
        for every dialog. `dh` is a minimum: the dialog is cloaked from its
        first map (_prepare), and once the caller has populated `body` it is
        measured and placed until its height stops changing, then shown whole
        (_present). Measured once, it came out short: rows that cut their text
        to their column, among others, settle only after the first layout.

        Layout contract for callers: pack the bottom controls (the button row,
        and anything else that must never disappear) BEFORE the content that
        fills the middle. Tk allocates space in packing order, so whenever a
        dialog is shorter than its content, the widgets packed last are the
        ones squeezed out.
        """
        stand_in, anchor = None, None
        if key is not None:
            old = self._dialogs.get(key)
            try:
                live = old is not None and bool(old.winfo_exists())
            except tk.TclError:
                live = False
            if live:
                if getattr(old, '_revealed', False):
                    stand_in = self._take_still(old)
                    anchor = (old.winfo_x(), old.winfo_y(),
                              old.winfo_y() + old.winfo_height())
                else:
                    # Still being built out of sight: whatever stood in for the
                    # window before it stands in for this one.
                    stand_in, old._stand_in = getattr(old, '_stand_in', None), None
                    anchor = getattr(old, '_anchor', None)
                try:
                    old.destroy()
                except tk.TclError:
                    pass
        dlg = tk.Toplevel(self.root)
        if key is not None:
            self._dialogs[key] = dlg
        self._open_dialogs.append(dlg)
        dlg.title(title)
        dlg.configure(bg=BG)
        dlg.overrideredirect(True)
        dlg.attributes('-topmost', True)
        dlg.resizable(False, False)
        dlg.geometry(f'{dw}x{dh}')
        self._prepare(dlg)
        dlg._stand_in = stand_in
        dlg._anchor = anchor
        dlg._place_args = (dw, dh)
        dlg._place = lambda: self._place_dialog(dlg, *dlg._place_args)

        self._build_titlebar(dlg, title)

        body = tk.Frame(dlg, bg=BG)
        body.pack(fill='both', expand=True,
                  padx=DLG_PAD_X, pady=(DLG_PAD_TOP, DLG_PAD_BTM))
        dlg.bind('<Escape>', lambda e: self._close_dialog(dlg))

        def drop_stand_in():
            still, dlg._stand_in = getattr(dlg, '_stand_in', None), None
            if still is not None:
                still.close()

        # Once the caller has built it. The picture of the window it replaces
        # goes the moment it shows, or when it is closed before.
        self.root.after_idle(lambda: self._present(
            dlg, dlg._place, then=drop_stand_in, gone=drop_stand_in))
        return dlg, body

    def _keep_clear(self, x, top, w, h, lo, hi):
        """Where a dialog on show goes when it changes (_place_dialog).

        Where it is, while it is on its screen (its top between lo and hi) and
        clear of the widget. Otherwise the nearest place that is both: just
        above the widget, just below it, or back on the screen, SCREEN_MARGIN
        away from the widget; when no side has room for the margin, as far
        from the widget as the screen allows, touching it at worst, as
        _place_popup put a dialog below it (fourth review of 2026-09-27: held
        to the margin alone, a dialog with room below for itself but not for
        the margin stayed over the widget). Where it is, back on its screen,
        when no place is: its buttons stay reachable, over the widget if they
        must, as they always did.

        🔴 No memory of which side of the widget a dialog was on, and no exact
        comparison of edges: the second and third reviews of 2026-09-27 found
        a remembered side going stale in six ways (the widget dragged, grown by
        one pixel less during its animation, the dialog nudged, the side with
        no room left while the other had). What counts is where the two are.
        Touching the widget, or closer to it than SCREEN_MARGIN, is still clear:
        only a dialog that has to move is given the margin."""
        rx, ry, rw, rh = self._widget_rect()
        hi = max(lo, hi)

        def clear(t, gap=0):
            return not (x < rx + rw and rx < x + w and t < ry + rh + gap
                        and ry - gap < t + h)

        if lo <= top <= hi and clear(top):
            return top
        here = min(max(top, lo), hi)
        above, below = ry - SCREEN_MARGIN - h, ry + rh + SCREEN_MARGIN
        places = [t for t in (here, above, below)
                  if lo <= t <= hi and clear(t, SCREEN_MARGIN)]
        if not places:
            places = [t for t in (here, min(max(above, lo), hi),
                                  min(max(below, lo), hi))
                      if clear(t)]
        if places:
            return min(places, key=lambda t: abs(t - top))
        return here

    def _dialog_seat(self, x, top, w, h):
        """How a dialog at (x, top, w, h) sits against the widget now: 'above'
        or 'below' it and clear of it, or None (beside it, or over it)."""
        rx, ry, rw, rh = self._widget_rect()
        if not (x < rx + rw and rx < x + w):
            return None
        if top + h <= ry:
            return 'above'
        if top >= ry + rh:
            return 'below'
        return None

    def _widget_rect(self):
        return (self.root.winfo_x(), self.root.winfo_y(),
                self.root.winfo_width(), self.root.winfo_height())

    def _place_dialog(self, dlg, dw, dh_floor=0):
        """Give a dialog the height its content asks for, and its place.

        Until it first shows, the place comes from that height, as it always
        did: above the widget and clear of it, below when there is no room. A
        dialog with a part that can open later (dlg._reserve: the account
        page's advanced options, the key form behind the login way) is placed
        where its whole height fits clear of the widget, above or below, so
        that the part unfolds downward; when no side has room for all of it,
        it is placed for its own height and lifted by what the screen allows.
        A dialog opened in the place of one of its kind opens where that one
        was (dlg._anchor).

        🔴 Once it has shown, its top edge stays where the user saw it or
        dragged it: the dialog grows and shrinks downward, and moves only when
        it would leave its screen or cover the widget, to the nearest place
        that does neither (_keep_clear). It used to be placed afresh above the
        widget on every change, so every change moved its top edge and
        everything in it. A dialog on show is held still while it changes
        (_hold).

        dh_floor sets a minimum height (for dialogs that look better with some
        breathing room than shrink-wrapped); the content still wins when
        larger, and the screen caps both. Returns the geometry applied.

        A dialog whose middle scrolls (dlg._scrolls, _scroll_area) is held to
        its screen's work area instead, the taskbar left out: the whole of it
        is then always in reach, and the middle scrolls."""
        shown = getattr(dlg, '_revealed', False)
        if shown:
            self._hold(dlg)
        dlg._place_args = (dw, dh_floor)
        dlg.update_idletasks()
        anchor = getattr(dlg, '_anchor', None)
        scrolls = getattr(dlg, '_scrolls', False)
        work = None
        if shown or anchor:
            if shown:
                x, top = dlg.winfo_x(), dlg.winfo_y()
                seen_bottom = top + dlg.winfo_height()
            else:
                x, top, seen_bottom = anchor
            info = _monitor_of(x, top, dw, max(1, seen_bottom - top),
                               _MONITOR_DEFAULTTONEAREST)
            ml, mt, mr, mb = info[1] if info else self._widget_monitor_area()
            if scrolls and info:
                work = info[2]
            room = ((work[3] - work[1]) - SCREEN_MARGIN if work
                    else (mb - mt) - 2 * SCREEN_MARGIN)
            need = min(max(dh_floor, dlg.winfo_reqheight()), room)
            x = max(ml + SCREEN_MARGIN, min(x, mr - dw - SCREEN_MARGIN))
            if work:
                # Down to the taskbar's edge, not under it, and clear of
                # the widget: a margin of its own above the edge moved the
                # top of a page growing downward (test_showing 3e), and a
                # clamp after the search for a clear place could put the
                # page back over the widget (review of the fixes after 2.11).
                top = self._keep_clear(x, top, dw, need, work[1] + SCREEN_MARGIN,
                                       work[3] - need)
            else:
                top = self._keep_clear(x, top, dw, need, mt + SCREEN_MARGIN,
                                       mb - TASKBAR_GAP - need)
        else:
            ml, mt, mr, mb = self._widget_monitor_area()
            if scrolls:
                info = _monitor_of(self.root.winfo_x(), self.root.winfo_y(),
                                   max(1, self.root.winfo_width()),
                                   max(1, self.root.winfo_height()),
                                   _MONITOR_DEFAULTTONEAREST)
                work = info[2] if info else None
            room = ((work[3] - work[1]) - SCREEN_MARGIN if work
                    else (mb - mt) - 2 * SCREEN_MARGIN)
            need = min(max(dh_floor, dlg.winfo_reqheight()), room)
            reserve = getattr(dlg, '_reserve', 0)
            whole = min(room, need + reserve)
            x, top = self._place_popup(dw, whole)
            if not (reserve and self._dialog_seat(x, top, dw, whole)):
                # No side of the widget has room for the whole of it (review
                # of 2026-09-27: placed for the two heights anyway, the page
                # was clamped onto the widget). Its own height, as before,
                # and above the widget as much of the rest as the screen has.
                x, top = self._place_popup(dw, need)
                if reserve and self._dialog_seat(x, top, dw, need) == 'above':
                    top = max(mt + SCREEN_MARGIN, top - reserve)
            if work:
                # The place found, held to the work area and still clear
                # of the widget (_keep_clear), as in the branch above.
                top = self._keep_clear(x, top, dw, need, work[1] + SCREEN_MARGIN,
                                       work[3] - need)
        dlg.geometry(f'{dw}x{need}+{x}+{top}')
        # What _present compares the next placement with, whoever asked
        # for this one.
        dlg._geo = (dw, need, x, top)
        return dlg._geo

    def _scroll_area(self, dlg, parent, start=0.0):
        """The middle of a dialog, in a frame that scrolls when the dialog is
        held to its screen (dlg._scrolls, _place_dialog). Returns the frame to
        build the middle in; what was packed in `parent` before (the bottom
        row, first by the layout contract of _build_dialog_frame) stays where
        it is, always in view. `start`: where it was scrolled to, for a page
        drawn again in the place of one that scrolled (dlg._scroll_canvas).

        The account page with a limit reset, an outcome above it and its
        advanced options open ran past a 1080p screen at 150% (measured on
        2026-10-18: 1064 px for 1032), its buttons under the taskbar. The bar
        sits in the dialog's right margin, so the content keeps its width
        whether it shows or not; the wheel scrolls from anywhere on the page,
        and the bar can be dragged."""
        dlg._scrolls = True
        area = tk.Frame(parent, bg=BG)
        area.pack(fill='both', expand=True)
        # Height 1, not Tk's default of about 270: the dialog is measured from
        # what the canvas asks for, which is the content's height.
        canvas = tk.Canvas(area, bg=BG, highlightthickness=0, bd=0, height=1,
                           yscrollincrement=self.dp(20))
        canvas.pack(fill='both', expand=True)
        inner = tk.Frame(canvas, bg=BG)
        item = canvas.create_window(0, 0, window=inner, anchor='nw')
        dlg._scroll_canvas = canvas
        bar = tk.Canvas(dlg, bg=BG, highlightthickness=0, bd=0, width=self.dp(6))
        state = {'shown': False, 'drag': None, 'start': start or None, 'drawing': False}

        def draw_bar(*_view):
            # Also the canvas's yscrollcommand: every change of its view comes
            # here, one made by code too, not only the wheel and the bar. Not
            # again from inside itself (the yview_moveto calls below).
            if state['drawing']:
                return
            state['drawing'] = True
            try:
                if state['start'] is not None and getattr(dlg, '_revealed', False):
                    state['start'] = None       # on show: the user scrolls now
                total = max(1, inner.winfo_reqheight())
                seen = canvas.winfo_height()
                if seen <= 1 or total <= seen + 1:
                    if state['shown']:
                        bar.place_forget()
                        state['shown'] = False
                    canvas.yview_moveto(0)
                    return
                if state['start'] is not None:
                    # Drawn again (reopen): the line just used stays in view,
                    # where it was, not at the top; held until the page shows,
                    # as it is built, measured and placed: dropped the first
                    # time the half-built content fitted, it came back at the
                    # top (review of the fixes after 2.11).
                    canvas.yview_moveto(state['start'])
                if not state['shown']:
                    bar.place(in_=canvas, relx=1.0, x=(DLG_PAD_X - self.dp(6)) // 2,
                              rely=0, relheight=1.0, width=self.dp(6))
                    state['shown'] = True
                top, bottom = canvas.yview()
                # The canvas's height, which the bar always has (relheight=1.0):
                # the bar's own was the one before the placer had resized it, and
                # the first thumb of a page came out a third of its length.
                h = max(1, seen)
                y0 = int(top * h)
                y1 = max(y0 + self.dp(24), int(bottom * h))
                r = self.dp(3)
                bar.delete('all')
                bar.create_rectangle(0, y0 + r, self.dp(6), y1 - r, fill=DIM, outline='')
                bar.create_oval(0, y0, self.dp(6), y0 + 2 * r, fill=DIM, outline='')
                bar.create_oval(0, y1 - 2 * r, self.dp(6), y1, fill=DIM, outline='')
            finally:
                state['drawing'] = False

        def on_inner(_e=None):
            # The canvas asks for the whole content: the dialog takes it when
            # the screen has room, and the rest scrolls when it does not.
            canvas.configure(scrollregion=(0, 0, inner.winfo_reqwidth(),
                                           inner.winfo_reqheight()),
                             height=inner.winfo_reqheight())
            draw_bar()

        def on_canvas(e):
            canvas.itemconfigure(item, width=e.width)
            draw_bar()

        def on_wheel(e):
            # By its sign, whatever its size: a precision touchpad sends
            # deltas well under a notch, and dividing them by one rounded
            # every scroll away, as the diagnostics window already knew.
            if state['shown'] and e.delta:
                canvas.yview_scroll(-3 if e.delta > 0 else 3, 'units')
                draw_bar()

        def drag_start(e):
            state['drag'] = (e.y, canvas.yview()[0])

        def drag_move(e):
            if state['drag'] is None:
                return
            y, top = state['drag']
            canvas.yview_moveto(top + (e.y - y) / max(1, bar.winfo_height()))
            draw_bar()

        canvas.configure(yscrollcommand=draw_bar)
        inner.bind('<Configure>', on_inner)
        canvas.bind('<Configure>', on_canvas)
        dlg.bind('<MouseWheel>', on_wheel, add='+')
        bar.bind('<Button-1>', drag_start)
        bar.bind('<B1-Motion>', drag_move)
        bar.bind('<ButtonRelease-1>', lambda e: state.update(drag=None))
        return inner

    # ── W11 Styled Menu ─────────────────────────────

    def _prewarm_menu(self):
        """Create and discard a dummy Toplevel + emoji Label off-screen.

        Tk Toplevel creation is slow the first time (OS-level window class
        init) and Segoe UI Emoji glyph loading is similarly lazy. Doing both
        once at startup means the real first menu open is snappy instead
        of stalling for several seconds.
        """
        try:
            dummy = tk.Toplevel(self.root)
            dummy.overrideredirect(True)
            dummy.geometry('1x1+-20000+-20000')
            # Touch the emoji font so Windows caches the glyph metrics.
            lbl = tk.Label(dummy, text='\U0001F30D', font=FT_EMOJI)
            lbl.pack()
            dummy.update_idletasks()
            dummy.destroy()
        except Exception as e:
            wlog(f'PREWARM  failed: {e}')

    def _show_menu(self, e=None):
        wlog('MENU   _show_menu called')
        self._hide_tooltips()   # the menu opens over the same surface
        if self._menu_win and self._menu_win.winfo_exists():
            wlog('MENU   toggle: closing existing menu')
            self._close_menu()
            return 'break'

        # Dismiss the update banner so it can't compete with the menu for
        # Z-order / focus. User can re-trigger via Check for updates.
        self._dismiss_update_banner()

        m = tk.Toplevel(self.root)
        self._menu_win = m
        m.overrideredirect(True)
        m.attributes('-topmost', True)
        m.configure(bg=MENU_BG)
        # Cloaked from its first map (_prepare) and shown whole (_present).
        # Built off-screen, as it was, it came onto the screen before Windows
        # had painted any of it, and was painted there under the user's eyes.
        self._prepare(m)
        wlog('MENU   toplevel created')

        mode_label = t('menu_mode_normal') if self._essential else t('menu_mode_essential')

        # Quick actions at the top, then one row per category. Each category
        # opens a side flyout with its settings (Adobe-style cascading menu),
        # keeping this main column short and scannable.
        self._menu_row(m, t('menu_refresh'), lambda: self._menu_do(self.refresh),
                       icon=ICON_REFRESH, icon_ft=FT_MDL2_MENU)
        self._menu_row(m, mode_label, lambda: self._menu_do(self._toggle_essential),
                       icon='\u21F5\uFE0E', icon_ft=FT_EMOJI_11)
        self._menu_sep(m)
        def category(key, icon, icon_ft, label):
            row = self._menu_row(m, label, None, icon=icon, icon_ft=icon_ft,
                                 trailing='\u203A')
            self._bind_subtree(row, '<Button-1>',
                               lambda e, k=key, rr=row: self._open_flyout(k, rr))

        category('display', '\uECA5', FT_MDL2_MENU, t('menu_cat_display'))
        category('data', '\U0001F514\uFE0E', FT_EMOJI, t('menu_cat_data'))
        # Accounts is a destination, not a category: its flyout held the
        # accounts dialog and a single link, and the link now sits inside that
        # dialog. One row, one click, one window.
        self._menu_row(m, t('menu_accounts'),
                       lambda: self._menu_do(self._accounts_dialog),
                       icon=ICON_KEY, icon_ft=FT_MDL2_MENU)
        category('general', '\u2699\uFE0E', FT_EMOJI, t('menu_cat_general'))
        self._menu_sep(m)
        self._menu_row(m, t('menu_quit'), lambda: self._menu_do(self._quit),
                       icon='\u2715', icon_ft=FT_EMOJI)
        tk.Label(m, text=f'v{APP_VERSION}', font=FT_DLG_HINT, fg=DIM, bg=MENU_BG,
                 anchor='w', padx=14, pady=4).pack(fill='x')

        def place():
            m.update_idletasks()
            mw = max(m.winfo_reqwidth(), 220)
            mh = m.winfo_reqheight() + 4  # small bottom breathing room
            bx, by = self._place_submenu(mw, mh)
            m.geometry(f'{mw}x{mh}+{bx}+{by}')
            return (mw, mh, bx, by)

        mw, mh, bx, by = place()
        wlog(f'MENU   size={mw}x{mh} pos=({bx},{by})')
        self._present(m, place, then=lambda: self._lift_menu(m))
        m.bind('<Escape>', lambda e: self._close_menu())
        self._bind_menu_autoclose(m)
        m.focus_set()
        # Stop the click from propagating to ancestor widgets that also bind
        # <Button-1> or <Button-3> to _show_menu (in essential mode several
        # children share the same binding, which would fire _show_menu twice
        # and toggle the menu shut).
        return 'break'

    # ── Menu building blocks ─────────────────────────

    @staticmethod
    def _bind_subtree(w, seq, fn):
        w.bind(seq, fn)
        for c in w.winfo_children():
            Widget._bind_subtree(c, seq, fn)

    def _menu_sep(self, parent):
        tk.Frame(parent, bg=BAR_BG, height=1).pack(fill='x', padx=12, pady=4)

    def _menu_section(self, parent, text):
        """A quiet heading. It takes the surface it sits on: the same label is
        used inside dialogs, where the menu's own shade drew a visible band."""
        try:
            surface = parent.cget('bg')
        except tk.TclError:
            surface = MENU_BG
        tk.Label(parent, text=text, font=FT_DLG_HINT, fg=DIM, bg=surface,
                 anchor='w', padx=14, pady=2).pack(fill='x', pady=(6, 0))

    def _menu_do(self, fn):
        """Terminal menu action: close the whole menu, then run the command."""
        self._close_menu()
        fn()

    def _menu_row(self, parent, text, command=None, *, icon=None, icon_ft=None,
                  marker=None, marker_fg=None, trailing=None, text_ft=None,
                  tip=None):
        """One menu / flyout row. `icon` is a glyph (with icon_ft) or a
        PhotoImage; `marker` is a leading radio/check glyph; `trailing` is a
        right-aligned glyph (category arrow / state); `text_ft` overrides the
        row font for labels the menu font cannot render; `tip` shows an
        explanatory tooltip on hover. Binds hover, and click to `command`."""
        row = tk.Frame(parent, bg=MENU_BG, cursor='hand2' if command else 'arrow')
        row.pack(fill='x')
        cells = [row]
        if marker is not None:
            mk = tk.Label(row, text=marker, font=FT_MARK, width=2, padx=2, pady=5,
                          fg=(marker_fg or CLAUDE), bg=MENU_BG)
            mk.pack(side='left')
            cells.append(mk)
        if icon is not None:
            cell = tk.Frame(row, bg=MENU_BG, width=ICON_CELL_W, height=ICON_CELL_H)
            cell.pack(side='left', pady=2)
            cell.pack_propagate(False)
            # Anything that is not a glyph is an image. Testing for
            # tk.PhotoImage missed the tinted one, which is an
            # ImageTk.PhotoImage and not a subclass of it: the row then drew
            # the image's internal name as text.
            if icon_ft is None and not isinstance(icon, str):
                il = tk.Label(cell, image=icon, bg=MENU_BG, bd=0, highlightthickness=0)
            else:
                il = tk.Label(cell, text=icon, font=icon_ft, fg=FG, bg=MENU_BG)
            il.pack(expand=True)
            cells += [cell, il]
        lead = 0 if (marker is not None or icon is not None) else 14
        tl = tk.Label(row, text=text, font=(text_ft or FT_MENU), fg=FG, bg=MENU_BG,
                      anchor='w', pady=5)
        tl.pack(side='left', fill='x', expand=True, padx=(lead, 12))
        cells.append(tl)
        if trailing is not None:
            tr = tk.Label(row, text=trailing, font=FT_MARK, fg=DIM, bg=MENU_BG, pady=5)
            tr.pack(side='right', padx=(0, 12))
            cells.append(tr)

        def paint(bg):
            for c in cells:
                try:
                    c.config(bg=bg)
                except tk.TclError:
                    pass
        for c in cells:
            c.bind('<Enter>', lambda e: paint(HOVER_BG))
            c.bind('<Leave>', lambda e: paint(MENU_BG))
            if command:
                c.bind('<Button-1>', lambda e, cm=command: cm())
            if tip:
                self._tooltip(c, tip, delay=500)
        return row

    # ── Category side flyouts ────────────────────────

    def _open_flyout(self, cat, anchor):
        """Open (or toggle) the side flyout for a category next to its row."""
        if (self._flyout_cat == cat and self._flyout_win
                and self._flyout_win.winfo_exists()):
            self._close_flyout()
            return
        self._close_flyout()
        self._flyout_cat = cat
        self._flyout_anchor = anchor
        m = tk.Toplevel(self.root)
        self._flyout_win = m
        m.overrideredirect(True)
        m.attributes('-topmost', True)
        m.configure(bg=MENU_BG)
        # Cloaked from its first map and shown whole, as the menu (_prepare).
        self._prepare(m)
        self._populate_flyout(cat, m)

        def place():
            # Level with the row it belongs to, whatever it holds now: a
            # rebuild keeps its top edge.
            m.update_idletasks()
            mw = max(m.winfo_reqwidth(), 200)
            mh = m.winfo_reqheight() + 6
            fx, fy = self._place_flyout(mw, mh, anchor)
            m.geometry(f'{mw}x{mh}+{fx}+{fy}')
            return (mw, mh, fx, fy)

        place()
        self._present(m, place, then=lambda: self._lift_menu(m))
        # Autoclose on the flyout too: a widget click must dismiss the menu even
        # when focus currently sits in the flyout (e.g. after a bar toggle).
        self._bind_focus_autoclose(m)

    def _close_flyout(self):
        m = self._flyout_win
        if m and m.winfo_exists():
            m.destroy()
        self._flyout_win = None
        self._flyout_cat = None

    def _rebuild_flyout(self):
        """Repopulate the open flyout in place after a stateful toggle.

        Held still meanwhile (_hold): destroyed and filled again in view, its
        rows used to come back one by one."""
        m = self._flyout_win
        cat = self._flyout_cat
        if not m or not m.winfo_exists() or not cat:
            return
        self._hold(m)
        for c in m.winfo_children():
            c.destroy()
        self._populate_flyout(cat, m)
        getattr(m, '_place', lambda: None)()
        self._lift_menu(m)

    def _flyout_set(self, fn):
        """Apply a stateful change from a flyout, then refresh it so the new
        state (radio dot / ON-OFF label) shows without closing the flyout.

        A toggle can resize the widget, which briefly bounces focus and would
        otherwise trip the autoclose. Reset the grace period on the menu and
        flyout first so that focus bounce is ignored; a later widget click
        (past the grace window) still closes the menu normally."""
        now = time.monotonic()
        for win in (self._menu_win, self._flyout_win):
            if win is not None:
                win._opened_at = now
        fn()
        self._rebuild_flyout()

    def _place_flyout(self, fw, fh, anchor):
        """Place a flyout beside the MAIN menu, aligned to its right edge with a
        small gap (or its left edge if there is no room on the right), and
        vertically level with the clicked category row. Clamped into the work
        area."""
        GAP = 2
        menu = self._menu_win
        try:
            ay = anchor.winfo_rooty()
        except Exception:
            ay = 100
        try:
            mlx = menu.winfo_rootx()
            mrx = mlx + menu.winfo_width()
        except Exception:
            mlx, mrx = 100, 300
        info = _monitor_of(mlx, ay, max(1, mrx - mlx), fh, _MONITOR_DEFAULTTONEAREST)
        wl, wt, wr, wb = info[2] if info else (0, 0, 1920, 1080)
        x = mrx + GAP                    # sit just to the right of the menu
        if x + fw > wr:                  # no room on the right -> left of the menu
            x = mlx - fw - GAP
        x = max(wl, min(x, wr - fw))
        y = max(wt, min(ay - 4, wb - fh))
        return int(x), int(y)

    def _populate_flyout(self, cat, m):
        if cat == 'display':
            cd = self.cfg.get('countdown_display', 'dot')
            self._menu_section(m, t('menu_countdown'))
            self._menu_row(m, t('countdown_dot'),
                           lambda: self._flyout_set(lambda: self._set_countdown_mode('dot', close=False)),
                           marker=('●' if cd == 'dot' else '○'), tip=t('tip_countdown_dot'))
            self._menu_row(m, t('countdown_full'),
                           lambda: self._flyout_set(lambda: self._set_countdown_mode('full', close=False)),
                           marker=('●' if cd == 'full' else '○'), tip=t('tip_countdown_full'))
            self._menu_sep(m)
            sync_on = self.cfg.get('show_sync_time', True)
            self._menu_row(m, (t('menu_sync_on') if sync_on else t('menu_sync_off')),
                           lambda: self._flyout_set(lambda: self._toggle_sync_time(close=False)),
                           icon='\U0001F552︎', icon_ft=FT_EMOJI, tip=t('tip_sync'))
            dyn = self.cfg.get('bar_dynamic', False)
            self._menu_row(m, (t('menu_colors_dynamic') if dyn else t('menu_colors_fixed')),
                           lambda: self._flyout_set(lambda: self._toggle_bar_dynamic(close=False)),
                           icon='\U0001F3A8︎', icon_ft=FT_EMOJI, tip=t('tip_colors'))
            # How many equal parts the weekly bar is shown in.
            #
            # 🔴 One row that opens a window, not one row per number:
            # measured on 2026-09-21, eight more rows took this flyout to
            # 1092px at 150% scaling against 1020px of usable height on a
            # 1080p screen, and a menu does not scroll.
            split = int(self.cfg.get('weekly_split', 0) or 0)
            self._menu_row(m, t('menu_split') + ': ' + (
                t('split_parts').format(n=split) if split in SPLIT_CHOICES
                else t('split_none')),
                lambda: self._menu_do(self._split_dialog),
                icon='\U0001F4C5\ufe0e', icon_ft=FT_EMOJI, tip=t('tip_split'))
            # Whether the widget has a taskbar button is a question about how it
            # is shown, so it belongs here rather than beside the alerts.
            tb = self.cfg.get('show_in_taskbar', False)
            self._menu_row(m, (t('menu_taskbar_on') if tb else t('menu_taskbar_off')),
                           lambda: self._flyout_set(self._toggle_taskbar),
                           icon='\U0001F4CC︎', icon_ft=FT_EMOJI, tip=t('tip_taskbar'))
            self._menu_sep(m)
            self._menu_section(m, t('menu_theme'))
            theme = self.cfg.get('theme', 'dark')
            for value, label in (('dark', t('theme_dark')), ('light', t('theme_light'))):
                self._menu_row(m, label,
                               lambda v=value: self._flyout_set(lambda: self._set_theme(v)),
                               marker=('\u25cf' if theme == value else '\u25cb'))
            self._menu_row(m, t('menu_restart'),
                           lambda: self._menu_do(self._restart_widget),
                           icon=ICON_RESTART, icon_ft=FT_MDL2_MENU,
                           tip=t('tip_restart'))
            self._menu_sep(m)
            self._menu_section(m, t('menu_reset_label'))
            r_time = self.cfg.get('show_reset_time', True)
            self._menu_row(m, (t('menu_reset_time_on') if r_time else t('menu_reset_time_off')),
                           lambda: self._flyout_set(lambda: self._toggle_reset_part('time', close=False)),
                           icon='\U0001F553︎', icon_ft=FT_EMOJI, tip=t('tip_reset_time'))
            r_left = self.cfg.get('show_reset_left', True)
            self._menu_row(m, (t('menu_reset_left_on') if r_left else t('menu_reset_left_off')),
                           lambda: self._flyout_set(lambda: self._toggle_reset_part('left', close=False)),
                           icon='\u23F3︎', icon_ft=FT_EMOJI, tip=t('tip_reset_left'))
            self._menu_sep(m)
            self._menu_section(m, t('menu_essential_bars'))
            for code, name in (('session', t('current_session')),
                               ('weekly', t('all_models')),
                               ('sonnet', self._sonnet_label())):
                self._ess_bar_row(m, code, name, locked=False)
        elif cat == 'data':
            cur = self.cfg.get('refresh_ms', REFRESH) // 1000
            self._menu_row(m, f"{t('menu_refresh_interval')} ({cur}s)",
                           lambda: self._menu_do(self._show_interval_dialog),
                           icon='⏳︎', icon_ft=FT_EMOJI)
            self._menu_sep(m)
            notif = self.cfg.get('notifications_enabled', True)
            self._menu_row(m, (t('menu_notifications_on') if notif else t('menu_notifications_off')),
                           lambda: self._flyout_set(self._toggle_notifications),
                           icon='\U0001F514︎', icon_ft=FT_EMOJI, tip=t('tip_notifications'))
        elif cat == 'general':
            self._menu_section(m, t('menu_language'))
            for code, name, ft in (('en', 'English', None), ('it', 'Italiano', None),
                                   ('ja', '日本語', FT_MENU_JP)):
                self._menu_row(m, name,
                               lambda c=code: self._menu_do(lambda: self._set_language(c)),
                               marker=('●' if code == _current_lang else '○'),
                               text_ft=ft)
            self._menu_sep(m)
            self._menu_row(m, t('menu_check_updates'),
                           lambda: self._menu_do(self._check_updates_manual),
                           icon='⬆︎', icon_ft=FT_EMOJI)
            gh_icon, gh_font = ((self._gh_icon, None) if self._gh_icon
                                else ('\U0001F4BB︎', FT_EMOJI))
            self._menu_row(m, t('menu_open_repo'),
                           lambda: self._menu_do(self._open_repo),
                           icon=gh_icon, icon_ft=gh_font)
            self._menu_row(m, t('menu_open_config'),
                           lambda: self._menu_do(self._open_config),
                           icon='{ }', icon_ft=FT_EMOJI)
            self._menu_row(m, t('menu_selftest'),
                           lambda: self._menu_do(self._selftest_dialog),
                           icon='\U0001F50D︎', icon_ft=FT_EMOJI,
                           tip=t('selftest_hint'))

    def _bind_focus_autoclose(self, win):
        """Bind FocusOut on a menu/flyout window so losing focus schedules a
        close. Both the main menu AND the side flyout get this, so a click on
        the widget closes the menu even when focus currently sits in the
        flyout. A short grace period after `_opened_at` ignores transient focus
        bounces during setup and during a settings toggle that resizes the
        widget (which briefly steals focus but must not close the menu)."""
        win._opened_at = time.monotonic()

        def on_focus_out(_e=None):
            if time.monotonic() - win._opened_at < 0.2:
                return
            self.root.after(150, self._close_if_unfocused)

        win.bind('<FocusOut>', on_focus_out)

    def _bind_menu_autoclose(self, m):
        """Close the menu when it loses focus (click-outside, Alt-tab, etc.).

        _show_menu dismisses the update banner before creating the menu, so
        we no longer have a competing topmost window stealing focus - the
        FocusOut pattern that worked up to v2.7.5 is safe again.
        """
        self._bind_focus_autoclose(m)

        # NOACTIVATE means Tk never sees focus move to another application, so
        # FocusOut only fires for clicks on our own window. Poll the global
        # mouse state to also dismiss the menu when the user clicks a different
        # app (the reported bug: the menu stayed open on outside-app clicks).
        if self._menu_click_job is None:
            self._menu_btn_down = self._any_mouse_down()
            self._menu_click_job = self.root.after(40, self._watch_menu_click)

    @staticmethod
    def _any_mouse_down():
        u = ctypes.windll.user32
        return bool(u.GetAsyncKeyState(0x01) & 0x8000 or
                    u.GetAsyncKeyState(0x02) & 0x8000)

    def _watch_menu_click(self):
        """While a menu is open, close it on a mouse click outside BOTH the menu
        and the widget (i.e. on another application). Clicks on the widget or
        inside the menu are left to Tk's own handlers, so the hamburger / right
        click toggle never races with this watcher."""
        m = self._menu_win
        if not m or not m.winfo_exists():
            self._menu_click_job = None
            self._menu_btn_down = False
            return
        try:
            down = self._any_mouse_down()
            if down and not self._menu_btn_down:
                pt = _POINT()
                ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
                if not (self._point_in_widget(m, pt.x, pt.y) or
                        self._point_in_widget(self._flyout_win, pt.x, pt.y) or
                        self._point_in_widget(self.root, pt.x, pt.y)):
                    self._menu_btn_down = down
                    self._close_menu()
                    return
            self._menu_btn_down = down
        except Exception:
            pass
        self._menu_click_job = self.root.after(40, self._watch_menu_click)

    @staticmethod
    def _point_in_widget(w, x, y):
        try:
            wx, wy = w.winfo_rootx(), w.winfo_rooty()
            return (wx <= x < wx + w.winfo_width() and
                    wy <= y < wy + w.winfo_height())
        except Exception:
            return False

    def _close_if_unfocused(self):
        """Close the menu only if focus hasn't returned to it in the meantime.
        Focus inside the side flyout counts as inside the menu, so toggling a
        setting there (which can resize the widget and bounce focus) keeps the
        menu open."""
        m = self._menu_win
        if not m or not m.winfo_exists():
            return
        try:
            focused = m.focus_displayof()
            if focused is None:
                self._close_menu()
                return
            # focus_displayof may return a child of the menu or flyout - walk
            # up to see if the focused widget is inside either Toplevel.
            keep = (m, self._flyout_win)
            t = focused
            while t is not None:
                if t in keep:
                    return  # still inside the menu/flyout - keep open
                t = t.master
            self._close_menu()
        except Exception:
            self._close_menu()

    def _lift_menu(self, m):
        """Force menu Toplevel above everything including the main widget."""
        try:
            hwnd = ctypes.windll.user32.GetParent(m.winfo_id())
            if not hwnd:
                hwnd = m.winfo_id()
            # HWND_TOPMOST=-1, SWP_NOMOVE=2, SWP_NOSIZE=1, SWP_NOACTIVATE=0x10
            ctypes.windll.user32.SetWindowPos(
                hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
        except Exception:
            pass

    def _close_menu(self):
        self._close_flyout()
        m = self._menu_win
        if m and m.winfo_exists():
            m.destroy()
            wlog('MENU   closed')
        self._menu_win = None
        if self._menu_click_job is not None:
            try:
                self.root.after_cancel(self._menu_click_job)
            except Exception:
                pass
            self._menu_click_job = None

    def _set_language(self, code):
        """Apply new language, save to config, retranslate visible UI."""
        set_lang(code)
        # Retarget the named fonts before the labels below get their new text,
        # so a switch to Japanese repaints once instead of flashing the old
        # family under the new strings.
        apply_font_lang(code)
        self.cfg['language'] = code
        save_cfg(self.cfg)
        self._close_menu()
        # Retranslate section labels
        self.s_session.lbl.config(text=t('current_session'))
        self.s_weekly.lbl.config(text=t('all_models'))
        self.s_sonnet.lbl.config(text=self._sonnet_label())
        self.ess_bars['session'].lbl.config(text=t('current_session'))
        self.ess_bars['weekly'].lbl.config(text=t('all_models'))
        self.ess_bars['sonnet'].lbl.config(text=self._sonnet_label())
        # And the line under each bar, which carries a weekday and the word for
        # 'reset'. Waiting for the refresh below was enough only when it
        # succeeds: on an expired key or offline it never lands, and the old
        # language stayed under a fully translated UI.
        for sec in self._all_sections():
            sec.redraw_reset()
        # A translated label is a different width, so the room it needs has to
        # be recomputed with it. Leaving that to the refresh below meant the new
        # text sat under the essential controls for the length of the request,
        # and for good when there is no key to refresh with.
        self._update_minsize()
        self._sync_ess_reset_mode()
        # Refresh to update reset text + any visible messages
        if has_credentials(self.cfg):
            self.refresh()

    def _set_countdown_mode(self, mode, close=True):
        """Apply a countdown display mode, persist it, re-render immediately."""
        self.cfg['countdown_display'] = mode
        save_cfg(self.cfg)
        wlog(f'CDOWN  countdown_display -> {mode}')
        # The numeric form is wider than the dot, and the strip's minimum
        # width accounts for whichever is in use (see _bar_content_w), so the
        # floor has to be recomputed here as it is for the sync-time toggle.
        self._update_minsize()
        if close:
            self._close_menu()
        # Re-render the countdown / dot right away under the new mode without
        # waiting for the next tick. Cancel the pending tick first so we don't
        # double-schedule; _tick_countdown reuses the current remaining secs.
        self._set_pulse(False)
        self._apply_dot_phase('off')
        if self._countdown_job:
            self.root.after_cancel(self._countdown_job)
            self._countdown_job = None
        self._tick_countdown()

    def _toggle_sync_time(self, close=True):
        """Show/hide the last-sync time on the bars. The strip's minimum width
        depends on this (the time needs room beside the dot), so re-layout."""
        new = not self.cfg.get('show_sync_time', True)
        self.cfg['show_sync_time'] = new
        save_cfg(self.cfg)
        wlog(f'SYNC   show_sync_time -> {new}')
        if close:
            self._close_menu()
        if self._essential and not self._expanded:
            self._enter_ess_collapsed()
            self._update_minsize()
            self._auto_height()
        self._set_pulse(False)
        self._apply_dot_phase('off')
        if self._countdown_job:
            self.root.after_cancel(self._countdown_job)
            self._countdown_job = None
        self._tick_countdown()

    def _bar_ft(self, code):
        """(fill, track) for a bar: the user's saved colour or the default,
        with the track derived from the fill."""
        fill = self.cfg.get('bar_colors', {}).get(code) or BAR_DEFAULT_FILL[code]
        return fill, derive_track(fill)

    def _set_bar_color(self, code, fill_hex):
        """Persist a per-bar fill colour and apply it live to that bar's normal
        and essential-strip Sections (track derived)."""
        self.cfg.setdefault('bar_colors', {})[code] = fill_hex
        save_cfg(self.cfg)
        fill, track = self._bar_ft(code)
        normal = {'session': self.s_session, 'weekly': self.s_weekly,
                  'sonnet': self.s_sonnet}[code]
        normal.set_colors(fill, track)
        self.ess_bars[code].set_colors(fill, track)

    def _open_bar_color_picker(self, code):
        self._close_menu()
        self._color_picker_dialog(t('dlg_bar_color'), self._bar_ft(code)[0],
                                  lambda hexv: self._set_bar_color(code, hexv))

    def _avatar_dialog(self, acc, state, on_done):
        """Pick what the account's bubble shows, and in which two colours.

        Works on a draft: the account is written by the details window when it
        is saved, so leaving here with Cancel has to change nothing.
        """
        dw, dh = self._dlg_size(330, 300)
        dlg, body = self._build_dialog_frame(t('dlg_avatar_title'), dw, dh, key='avatar')
        draft = {'color': state['color'],
                 'avatar': dict(state.get('avatar') or {})}
        draft['avatar'].setdefault('kind', 'initials')
        draft['avatar'].setdefault('fg', '#ffffff')
        text_var = tk.StringVar(value=(draft['avatar'].get('text') or ''))

        # Bottom-up first: Tk gives space in packing order, so the controls
        # that must never disappear are packed before the content that grows.
        btns = tk.Frame(body, bg=BG)
        btns.pack(fill='x', side='bottom', pady=(14, 0))
        colors = tk.Frame(body, bg=BG)
        colors.pack(fill='x', side='bottom', pady=(12, 0))

        def confirm():
            av = dict(draft['avatar'])
            av['text'] = (text_var.get() or '').strip()[:3]
            # An empty custom text would show an empty bubble: fall back to
            # the initials rather than to nothing.
            if av['kind'] == 'text' and not av['text']:
                av['kind'] = 'initials'
            if av['kind'] == 'icon' and not av.get('icon'):
                av['kind'] = 'initials'
            on_done(draft['color'], av)
            dlg.destroy()

        self._primary_pill(btns, t('dlg_save'), confirm).pack(side='right')
        self._secondary_pill(btns, t('dlg_cancel'), dlg.destroy).pack(
            side='right', padx=(0, 8))

        # ── what it will look like ──
        preview_host = tk.Frame(body, bg=BG)
        preview_host.pack(pady=(0, 12))

        def preview_acc():
            av = dict(draft['avatar'])
            av['text'] = (text_var.get() or '').strip()[:3]
            return dict(acc, color=draft['color'], avatar=av)

        def draw_preview():
            # Drawn again on the same canvas (_paint_bubble): made anew, the
            # preview blinked with every letter typed.
            shown = preview_host.winfo_children()
            if shown:
                self._paint_bubble(shown[0], preview_acc(), 64)
            else:
                self._account_bubble(preview_host, preview_acc(), size=64).pack()

        # ── initials, a text of your own, or a glyph ──
        kinds = tk.Frame(body, bg=BG)
        kinds.pack(fill='x', pady=(0, 10))
        area = tk.Frame(body, bg=BG)
        area.pack(fill='x')

        def set_kind(kind):
            self._hold(dlg)
            draft['avatar']['kind'] = kind
            render_kinds()
            render_area()
            draw_preview()

        def render_kinds():
            for w in kinds.winfo_children():
                w.destroy()
            cur = draft['avatar'].get('kind')
            self._pill_flow(kinds, [
                (t('avatar_kind_initials'), lambda: set_kind('initials'),
                 cur == 'initials'),
                (t('avatar_kind_text'), lambda: set_kind('text'), cur == 'text'),
                (t('avatar_kind_icon'), lambda: set_kind('icon'), cur == 'icon'),
            ], dw - self.dp(44))

        def pick_icon(glyph):
            self._hold(dlg)
            draft['avatar']['icon'] = glyph
            render_area()
            draw_preview()

        def render_area():
            for w in area.winfo_children():
                w.destroy()
            kind = draft['avatar'].get('kind')
            if kind == 'text':
                wrap = tk.Frame(area, bg=BAR_BG)
                wrap.pack(fill='x')
                ent = tk.Entry(wrap, textvariable=text_var, font=FT_DLG_BODY,
                               bg=BAR_BG, fg=FG, insertbackground=FG, bd=0,
                               relief='flat', highlightthickness=0,
                               justify='center')
                ent.pack(fill='x', ipady=7, ipadx=10)
                ent.bind('<FocusIn>', lambda e: wrap.configure(bg=FOCUS_RING))
                ent.bind('<FocusOut>', lambda e: wrap.configure(bg=BAR_BG))
                ent.bind('<KeyRelease>', lambda e: draw_preview())
                tk.Label(area, text=t('avatar_text_hint'), font=FT_DLG_HINT,
                         fg=DIM, bg=BG, anchor='w').pack(fill='x', pady=(6, 0))
                ent.focus_set()
            elif kind == 'icon':
                grid = tk.Frame(area, bg=BG)
                grid.pack()
                chosen = draft['avatar'].get('icon')
                cw, ch = self.dp(36), self.dp(30)
                for i, glyph in enumerate(AVATAR_ICONS):
                    on = glyph == chosen
                    cell = tk.Canvas(grid, width=cw, height=ch,
                                     bg=CLAUDE if on else SOFT_BG,
                                     highlightthickness=0, bd=0, cursor='hand2')
                    cell.create_text(cw / 2, ch / 2, text=glyph,
                                     fill='#ffffff' if on else FG,
                                     font=('Segoe MDL2 Assets', 13))
                    cell.grid(row=i // 6, column=i % 6, padx=2, pady=2)
                    cell.bind('<Button-1>', lambda e, g=glyph: pick_icon(g))
                    if not on:
                        cell.bind('<Enter>',
                                  lambda e, c=cell: c.configure(bg=SOFT_BG_HV))
                        cell.bind('<Leave>',
                                  lambda e, c=cell: c.configure(bg=SOFT_BG))
            else:
                tk.Label(area, text=account_initials(acc.get('name')),
                         font=FT_DLG_HINT, fg=DIM, bg=BG, anchor='w').pack(
                             fill='x')
            # The window was measured for the section it opened on; the glyph
            # grid is three times taller than a text field, so it is measured
            # again every time the section changes.
            self._place_dialog(dlg, dw, dh_floor=dh)

        # ── the two colours ──
        def swatch_row(label, presets, current, on_set):
            tk.Label(colors, text=label, font=FT_DLG_HINT, fg=DIM, bg=BG,
                     anchor='w').pack(fill='x', pady=(0, 4))
            row = tk.Frame(colors, bg=BG)
            row.pack(fill='x', pady=(0, 8))
            for hexv in presets:
                c = tk.Canvas(row, width=26, height=26, bg=BG,
                              highlightthickness=0, bd=0, cursor='hand2')
                if hexv.lower() == (current or '').lower():
                    c._ring = _dot_image(26, FG)
                    c.create_image(13, 13, image=c._ring, anchor='center')
                c._img = _dot_image(18, hexv)
                c.create_image(13, 13, image=c._img, anchor='center')
                c.pack(side='left', padx=(0, 6))
                c.bind('<Button-1>', lambda e, hv=hexv: on_set(hv))
            self._small_pill(row, t('dlg_custom'),
                             lambda: self._color_picker_dialog(
                                 label, current, on_set)).pack(side='left')

        def set_bg(hexv):
            self._hold(dlg)
            draft['color'] = hexv
            render_colors()
            draw_preview()

        def set_fg(hexv):
            self._hold(dlg)
            draft['avatar']['fg'] = hexv
            render_colors()
            draw_preview()

        def render_colors():
            for w in colors.winfo_children():
                w.destroy()
            swatch_row(t('avatar_bg'), _BUBBLE_COLORS, draft['color'], set_bg)
            swatch_row(t('avatar_fg'), AVATAR_FG_PRESETS,
                       draft['avatar'].get('fg'), set_fg)

        render_kinds()
        render_colors()
        render_area()
        draw_preview()
        self._place_dialog(dlg, dw, dh_floor=dh)

    def _color_picker_dialog(self, title, initial, on_pick):
        """In-tool HSV colour picker: preset swatches + a saturation/value
        square, a hue strip and a hex field, all kept in sync."""
        dw, dh = self._dlg_size(300, 320)
        dlg, body = self._build_dialog_frame(title, dw, dh, key='color_picker')
        SVW, SVH, HUEH = dw - 40, self.dp(140), self.dp(14)
        st = {'h': 0.0, 's': 1.0, 'v': 1.0, 'sv': None, 'hue': None}
        r, g, b = [c / 255 for c in _hex_to_rgb(initial)]
        st['h'], st['s'], st['v'] = colorsys.rgb_to_hsv(r, g, b)

        def cur_hex():
            rr, gg, bb = colorsys.hsv_to_rgb(st['h'], st['s'], st['v'])
            return _rgb_to_hex((rr * 255, gg * 255, bb * 255))

        prow = tk.Frame(body, bg=BG)
        prow.pack(fill='x', pady=(0, 10))
        tk.Label(prow, text=t('dlg_presets'), font=FT_DLG_HINT, fg=DIM,
                 bg=BG).pack(side='left', padx=(0, 8))
        for hexv in BAR_PRESETS:
            c = tk.Canvas(prow, width=22, height=22, bg=BG, highlightthickness=0,
                          bd=0, cursor='hand2')
            c._img = _dot_image(18, hexv)
            c.create_image(11, 11, image=c._img, anchor='center')
            c.pack(side='left', padx=(0, 6))
            c.bind('<Button-1>', lambda e, hv=hexv: set_hex(hv))

        sv = tk.Canvas(body, width=SVW, height=SVH, highlightthickness=0,
                       bd=0, cursor='crosshair')
        sv.pack()
        hue = tk.Canvas(body, width=SVW, height=HUEH, highlightthickness=0,
                        bd=0, cursor='crosshair')
        hue.pack(pady=(8, 0))

        prow2 = tk.Frame(body, bg=BG)
        prow2.pack(fill='x', pady=(10, 0))
        preview = tk.Canvas(prow2, width=26, height=26, bg=BG,
                            highlightthickness=0, bd=0)
        preview.pack(side='left')
        hexvar = tk.StringVar()
        hexwrap = tk.Frame(prow2, bg=BAR_BG, padx=1, pady=1)
        hexwrap.pack(side='left', padx=(10, 0))
        hexentry = tk.Entry(hexwrap, textvariable=hexvar, font=FT_DLG_BODY,
                            bg=BAR_BG, fg=FG, insertbackground=FG, bd=0,
                            highlightthickness=0, relief='flat', width=9)
        hexentry.pack(ipady=5, ipadx=8)

        def render_sv():
            img = Image.new('RGB', (SVW, SVH))
            px = img.load()
            h = st['h']
            for yy in range(SVH):
                vv = 1 - yy / (SVH - 1)
                for xx in range(SVW):
                    rr, gg, bb = colorsys.hsv_to_rgb(h, xx / (SVW - 1), vv)
                    px[xx, yy] = (int(rr * 255), int(gg * 255), int(bb * 255))
            st['sv'] = ImageTk.PhotoImage(img)
            sv.delete('all')
            sv.create_image(0, 0, image=st['sv'], anchor='nw')
            mx, my = st['s'] * (SVW - 1), (1 - st['v']) * (SVH - 1)
            ring = '#000000' if st['v'] > 0.55 else '#ffffff'
            sv.create_oval(mx - 5, my - 5, mx + 5, my + 5, outline=ring, width=2)

        def render_hue():
            img = Image.new('RGB', (SVW, HUEH))
            px = img.load()
            for xx in range(SVW):
                rr, gg, bb = colorsys.hsv_to_rgb(xx / (SVW - 1), 1, 1)
                col = (int(rr * 255), int(gg * 255), int(bb * 255))
                for yy in range(HUEH):
                    px[xx, yy] = col
            st['hue'] = ImageTk.PhotoImage(img)
            hue.delete('all')
            hue.create_image(0, 0, image=st['hue'], anchor='nw')
            hx = st['h'] * (SVW - 1)
            hue.create_rectangle(hx - 2, 0, hx + 2, HUEH, outline='#ffffff', width=2)

        def render_preview():
            preview.delete('all')
            preview._img = _dot_image(24, cur_hex())
            preview.create_image(13, 13, image=preview._img, anchor='center')
            hexvar.set(cur_hex())

        def set_hex(hv):
            try:
                rr, gg, bb = [c / 255 for c in _hex_to_rgb(hv)]
            except Exception:
                return
            st['h'], st['s'], st['v'] = colorsys.rgb_to_hsv(rr, gg, bb)
            render_sv()
            render_hue()
            render_preview()

        def on_sv(e):
            st['s'] = min(1.0, max(0.0, e.x / (SVW - 1)))
            st['v'] = min(1.0, max(0.0, 1 - e.y / (SVH - 1)))
            render_sv()
            render_preview()

        def on_hue(e):
            st['h'] = min(1.0, max(0.0, e.x / (SVW - 1)))
            render_sv()
            render_hue()
            render_preview()

        sv.bind('<Button-1>', on_sv)
        sv.bind('<B1-Motion>', on_sv)
        hue.bind('<Button-1>', on_hue)
        hue.bind('<B1-Motion>', on_hue)

        def on_hex(e=None):
            v = hexvar.get().strip()
            if not v.startswith('#'):
                v = '#' + v
            if re.fullmatch(r'#[0-9a-fA-F]{6}', v):
                set_hex(v)
        hexentry.bind('<Return>', on_hex)
        hexentry.bind('<FocusOut>', on_hex)

        btns = tk.Frame(body, bg=BG)
        btns.pack(fill='x', side='bottom', pady=(14, 0))

        def confirm():
            on_pick(cur_hex())
            dlg.destroy()
        self._primary_pill(btns, t('dlg_save'), confirm).pack(side='right')
        self._secondary_pill(btns, t('dlg_cancel'), dlg.destroy).pack(
            side='right', padx=(0, 8))

        render_sv()
        render_hue()
        render_preview()
        self._place_dialog(dlg, dw)

    def _apply_split(self, parts=None):
        """Put the split on both copies of the weekly bar."""
        if parts is None:
            parts = self.cfg.get('weekly_split', 0)
        for bar in (self.s_weekly, self.ess_bars['weekly']):
            bar.set_split(parts)

    def _set_split(self, parts, close=True):
        """Choose in how many equal parts the weekly bar is shown."""
        self.cfg['weekly_split'] = int(parts)
        save_cfg(self.cfg)
        wlog(f'SPLIT  weekly_split -> {parts}')
        self._apply_split(parts)
        if close:
            self._close_menu()

    def _split_dialog(self):
        """Choose in how many equal parts the weekly bar is shown.

        🔴 Rebuilt on 2026-09-25 from what the user found in the first
        version: a row of pills that ran past the window and cut the "7" in
        half, pills of different sizes (the chosen one was a primary pill,
        bigger by construction), an "Off" nobody could read as "remove the
        split", and a paragraph that explained nothing. One control of equal
        segments, the bar itself shown as it will look, and the way back
        named for what it does.
        """
        dw, dh = self._dlg_size(420, 220)
        dlg, body = self._build_dialog_frame(t('menu_split'), dw, dh, key='split')
        # The body's own width: its padding is not scaled, so a scaled guess
        # left the selector short of the bar under it, by 8 px at 100% and by
        # 56 at 200% (review of 2026-09-26).
        inner_w = dw - 2 * DLG_PAD_X

        # Bottom controls first (see the layout contract in _build_dialog_frame).
        bottom = tk.Frame(body, bg=BG)
        bottom.pack(fill='x', side='bottom')
        tk.Frame(bottom, bg=BAR_BG, height=1).pack(fill='x', pady=(14, 12))
        buttons = tk.Frame(bottom, bg=BG)
        buttons.pack(fill='x')
        self._secondary_pill(buttons, t('cc_ui_close'), dlg.destroy).pack(side='right')
        remove_host = tk.Frame(buttons, bg=BG)
        remove_host.pack(side='left')

        tk.Label(body, text=t('split_intro'), font=FT_DLG_HINT, fg=DIM, bg=BG,
                 anchor='w', justify='left',
                 wraplength=inner_w).pack(fill='x', pady=(0, 12))
        seg_host = tk.Frame(body, bg=BG)
        seg_host.pack(fill='x')

        # The weekly bar as it will look, with the user's own number when there
        # is one. The bar on the widget may not even be on show - the strip can
        # hold the session bar alone - and a choice whose effect cannot be seen
        # is a choice made blind.
        src = self.s_weekly
        prev = Section(body, src.lbl.cget('text'), src._fill, src._track)
        prev.frame.pack_configure(padx=0, pady=(16, 0))
        prev._dynamic = src._dynamic
        if src._state == 'ok':
            prev.update(src._pct, src._resets_at)
        else:
            # No reading yet: the divisions on an empty bar and no number. It
            # used to show 30%, and any number here reads as the user's own
            # (review of 2026-09-26).
            prev._no_number = True
            prev.update(0, None)
            prev.lbl_sub.config(text='')

        def chosen():
            v = int(self.cfg.get('weekly_split', 0) or 0)
            return v if v in SPLIT_CHOICES else 0

        def render():
            for w_ in seg_host.winfo_children():
                w_.destroy()
            for w_ in remove_host.winfo_children():
                w_.destroy()
            now = chosen()
            self._segmented(seg_host, [(v, str(v)) for v in SPLIT_CHOICES],
                            now, pick, inner_w).pack(anchor='w')
            if now:
                self._secondary_pill(remove_host, t('split_remove'),
                                     lambda: pick(0)).pack(side='left')
            prev.set_split(now)

        def pick(value):
            self._set_split(value, close=False)
            if seg_host.winfo_exists():
                self._hold(dlg)
                render()

        render()
        self._place_dialog(dlg, dw, dh_floor=dh)

    def _segmented(self, parent, options, selected, on_pick, width):
        """A row of equal segments, one of them chosen.

        One control rather than a row of pills: a pill sizes itself to its
        text and to its style, so no row of them comes out even, and a row
        that does not fit simply runs off the edge of the window.
        """
        n = len(options)
        width, h = int(width), self.dp(32)
        seg = width / n
        cv = tk.Canvas(parent, width=width, height=h, bg=BG,
                       highlightthickness=0, bd=0, cursor='hand2')
        state = {'hover': None}

        def paint():
            # 🔴 Images, not Tk ovals: the ovals are drawn without
            # antialiasing, so at this size the curves came out as steps and
            # the chosen pill had a one-pixel bump under each rounded end,
            # where the oval reached lower than the rectangle between them.
            # The same 4x-rendered pills the buttons use.
            cv.delete('all')
            cv.create_image(0, 0, image=_render_pill_image(width, h, SOFT_BG),
                            anchor='nw')
            for i, (value, label) in enumerate(options):
                x0, x1 = round(i * seg), round((i + 1) * seg)
                on = value == selected
                if on or state['hover'] == i:
                    colour = CLAUDE if on else SOFT_BG_HV
                    # The inset scales with the rest, or at 150-200% the
                    # chosen pill all but filled its track.
                    inset = self.dp(3)
                    cv.create_image(x0 + inset, inset, anchor='nw',
                                    image=_render_pill_image(x1 - x0 - 2 * inset,
                                                             h - 2 * inset, colour))
                cv.create_text((x0 + x1) / 2, h / 2, text=label,
                               font=FT_DLG_BTN_B if on else FT_DLG_BTN,
                               fill='#FFFFFF' if on else FG)

        def at(x):
            return max(0, min(n - 1, int(x // seg)))

        def hover(e):
            state['hover'] = at(e.x)
            paint()

        def leave(_e):
            state['hover'] = None
            paint()

        cv.bind('<Button-1>', lambda e: on_pick(options[at(e.x)][0]))
        cv.bind('<Motion>', hover)
        cv.bind('<Leave>', leave)
        paint()
        return cv

    def _toggle_bar_dynamic(self, close=True):
        """Switch the whole widget between the fixed per-bar palette and the
        percentage-driven one (all bars then share the usage-level colours)."""
        new = not self.cfg.get('bar_dynamic', False)
        self.cfg['bar_dynamic'] = new
        save_cfg(self.cfg)
        wlog(f'PALETTE bar_dynamic -> {new}')
        for sec in self._all_sections():
            sec.set_dynamic(new)
        if close:
            self._close_menu()

    def _ess_bar_row(self, m, code, name, locked):
        selected = code in self._essential_bar_ids()
        row = tk.Frame(m, bg=MENU_BG, cursor='arrow' if locked else 'hand2')
        row.pack(fill='x')
        marker = tk.Label(row, text=('✓' if selected else ''),
                          font=FT_MARK, fg=(DIM if locked else CLAUDE), bg=MENU_BG,
                          width=2, padx=4, pady=6)
        marker.pack(side='left')
        txt = tk.Label(row, text=name,
                       font=FT_MENU_B if selected else FT_MENU,
                       fg=(DIM if locked else FG), bg=MENU_BG, anchor='w', padx=2, pady=6)
        txt.pack(side='left', fill='x', expand=True)
        # Colour swatch on the right: opens the picker for this bar. Works even
        # for the locked session bar (its colour is still user-choosable).
        sw = tk.Canvas(row, width=18, height=18, bg=MENU_BG,
                       highlightthickness=0, bd=0, cursor='hand2')
        sw._img = _dot_image(16, self._bar_ft(code)[0])
        sw.create_image(9, 9, image=sw._img, anchor='center')
        sw.pack(side='right', padx=(0, 10))
        sw.bind('<Button-1>', lambda e, c=code: (self._open_bar_color_picker(c), 'break')[1])
        self._tooltip(sw, t('dlg_bar_color'), delay=500)
        for w in (row, marker, txt, sw):
            w.bind('<Enter>', lambda e, r=row, mk=marker, tx=txt, s=sw: (
                r.config(bg=HOVER_BG), mk.config(bg=HOVER_BG),
                tx.config(bg=HOVER_BG), s.config(bg=HOVER_BG)))
            w.bind('<Leave>', lambda e, r=row, mk=marker, tx=txt, s=sw: (
                r.config(bg=MENU_BG), mk.config(bg=MENU_BG),
                tx.config(bg=MENU_BG), s.config(bg=MENU_BG)))
            if not locked and w is not sw:
                w.bind('<Button-1>', lambda e, c=code: self._flyout_set(
                    lambda: self._toggle_essential_bar(c)))

    def _toggle_essential_bar(self, code):
        """Toggle a bar in/out of the shown set; re-apply layout live. Any bar
        can be hidden, but the last remaining one cannot (keep at least one).
        Called via _flyout_set, which rebuilds the flyout so its checkmarks
        refresh and it stays open (the layout resize below would otherwise
        drop the menu's focus and close it)."""
        ids = self._essential_bar_ids()
        if code in ids:
            if len(ids) == 1:
                return  # can't hide the only visible bar
            ids = [b for b in ids if b != code]
        else:
            ids.append(code)
        ids = [b for b in ('session', 'weekly', 'sonnet') if b in ids]
        self.cfg['essential_bars'] = ids
        save_cfg(self.cfg)
        wlog(f'ESSBARS essential_bars -> {ids}')
        # Live re-apply to whichever view is showing the bars.
        if self._essential and not self._expanded:
            self._enter_ess_collapsed()
            self._update_minsize()
            # Restore the saved width for this bar count, or shrink back to the
            # minimum when the user has no saved preference for it.
            self._restore_ess_width()
        else:
            # Stacked view: essential-expanded shows all bars, normal mode
            # shows the selected ones.
            self._pack_stacked(all_bars=(self._essential and self._expanded))
            if self._essential:
                for sec in (self.s_session, self.s_weekly, self.s_sonnet):
                    self._bind_drag_section(sec)
            self._update_minsize()
            self._resize_bottom_anchored()
        # Re-tick so countdown text + dot phase recompute for the new bar set
        # immediately (otherwise an added bar stays dot-less for up to one
        # cadence step while the others already show it).
        self._set_pulse(False)
        self._apply_dot_phase('off')
        if self._countdown_job:
            self.root.after_cancel(self._countdown_job)
            self._countdown_job = None
        self._tick_countdown()

    # ── Refresh interval dialog ──────────────────────

    def _show_interval_dialog(self):
        """Defer to run after close_menu completes."""
        self.root.after(10, self._show_interval_dialog_now)

    def _show_interval_dialog_now(self):
        dw, dh = self._dlg_size(460, 260)
        dlg, body = self._build_dialog_frame(t('dlg_interval_title'), dw, dh, key='interval')

        # Bottom controls first (see the layout contract in _build_dialog_frame).
        btn_frame = tk.Frame(body, bg=BG)
        btn_frame.pack(fill='x', side='bottom', pady=(12, 0))

        tk.Label(body, text=t('dlg_interval_label'), font=FT_DLG_H, fg=FG,
                 bg=BG, anchor='w').pack(fill='x')

        entry_wrap = tk.Frame(body, bg=BAR_BG, padx=1, pady=1)
        entry_wrap.pack(fill='x', pady=(10, 0))
        entry = tk.Entry(entry_wrap, font=FT_DLG_BODY, bg=BAR_BG, fg=FG,
                         insertbackground=FG, bd=0,
                         highlightthickness=0, relief='flat')
        entry.pack(fill='x', ipady=7, ipadx=10)
        entry.bind('<FocusIn>', lambda e: entry_wrap.configure(bg=FOCUS_RING))
        entry.bind('<FocusOut>', lambda e: entry_wrap.configure(bg=BAR_BG))

        current_secs = self.cfg.get('refresh_ms', REFRESH) // 1000
        entry.insert(0, str(current_secs))
        entry.select_range(0, 'end')
        entry.focus_set()

        status_lbl = tk.Label(body, text='', font=FT_DLG_HINT, fg=RED, bg=BG,
                              anchor='w', wraplength=dw - 40)
        status_lbl.pack(fill='x', pady=(8, 0))

        def save_interval():
            try:
                secs = int(entry.get().strip())
            except ValueError:
                status_lbl.config(text=t('dlg_interval_invalid'))
                return
            if secs < 10 or secs > 3600:
                status_lbl.config(text=t('dlg_interval_invalid'))
                return
            self.cfg['refresh_ms'] = secs * 1000
            save_cfg(self.cfg)
            if self._countdown_job:
                self.root.after_cancel(self._countdown_job)
            self._countdown_secs = secs
            self._tick_countdown()
            if self._job:
                self.root.after_cancel(self._job)
            self._schedule()
            dlg.destroy()

        self._primary_pill(btn_frame, t('dlg_save'), save_interval).pack(side='right')
        self._secondary_pill(btn_frame, t('dlg_cancel'), dlg.destroy).pack(
            side='right', padx=(0, 8))
        entry.bind('<Return>', lambda e: save_interval())

    # ── Auto-update ──────────────────────────────────

    def _schedule_update_check(self):
        """Schedule the update check shortly after startup.

        Normally throttled to once every `UPDATE_CHECK_INTERVAL_S` seconds
        (24h) to be polite to the GitHub API. Can be overridden by setting
        `always_check_updates: true` in config.json - useful during development
        on the maintainer's machine where every launch should re-check.
        """
        if not self.cfg.get('update_check_enabled', True):
            return
        if not self.cfg.get('always_check_updates', False):
            last = self.cfg.get('last_update_check', 0)
            now_ts = int(datetime.now().timestamp())
            if now_ts - last < UPDATE_CHECK_INTERVAL_S:
                return
        self.root.after(UPDATE_STARTUP_DELAY_MS, self._auto_check_updates)

    def _auto_check_updates(self):
        """Run a non-blocking update check; show banner only if newer + not skipped."""
        # Only persist the timestamp when we're respecting the throttle, so the
        # dev override doesn't clutter the config with a stale marker.
        if not self.cfg.get('always_check_updates', False):
            self.cfg['last_update_check'] = int(datetime.now().timestamp())
            save_cfg(self.cfg)
        threading.Thread(target=self._do_check_auto, daemon=True).start()

    def _do_check_auto(self):
        info = check_latest_release()
        if not info:
            return
        if not is_newer_version(info['version']):
            return
        if self.cfg.get('skip_version') == info['version']:
            wlog(f"UPDATE  v{info['version']} skipped by user preference")
            return
        self.root.after(0, self._show_update_banner, info)

    def _check_updates_manual(self):
        """Menu entry: always runs a fresh check and shows a result either way."""
        threading.Thread(target=self._do_check_manual, daemon=True).start()

    def _do_check_manual(self):
        info = check_latest_release()
        if info is None:
            self.root.after(0, self._show_info_toast, t('update_check_failed'))
            return
        if not is_newer_version(info['version']):
            self.root.after(
                0,
                self._show_info_toast,
                t('update_check_uptodate').format(version=APP_VERSION),
            )
            return
        if not info.get('asset_url'):
            self.root.after(0, self._show_info_toast, t('update_check_no_asset'))
            return
        self.root.after(0, self._show_update_dialog, info)

    def _show_update_banner(self, info):
        """Two-row notification floating above the widget.

        Row 1: icon + message centered.  Row 2: action pills centered.
        The banner has a fixed width so both rows truly sit in the middle -
        without it, pack(side='left') clusters everything to the left and
        the layout looks lopsided.
        """
        self._dismiss_update_banner()
        bar = tk.Toplevel(self.root)
        self._update_banner = bar
        bar.overrideredirect(True)
        bar.attributes('-topmost', True)
        bar.configure(bg=ORANGE)
        self._prepare(bar)      # cloaked until it is whole (_present)

        # Tight wrap - just enough padding to keep text off the rounded edges.
        wrap = tk.Frame(bar, bg=ORANGE, padx=10, pady=8)
        wrap.pack()

        # Row 1 - icon + message.
        top = tk.Frame(wrap, bg=ORANGE)
        top.pack()
        tk.Label(top, text='\u2B06', font=FT_EMOJI_11,
                 fg='#1e1e1c', bg=ORANGE).pack(side='left', padx=(0, 8))
        msg = t('update_banner_available').format(version=info['version'])
        tk.Label(top, text=msg, font=FT_DLG_H,
                 fg='#1e1e1c', bg=ORANGE).pack(side='left')

        # Row 2 - compact pill actions.
        actions = tk.Frame(wrap, bg=ORANGE)
        actions.pack(pady=(8, 0))

        def banner_pill(parent, text, cmd, primary=False):
            if primary:
                return make_pill_button(
                    parent, text=text, font=FT_DLG_BTN_B,
                    fg='#FFFFFF', bg='#2c2c2a', hover_bg='#3c3c3a',
                    cmd=cmd, padx=12, pady=4, parent_bg=ORANGE)
            return make_pill_button(
                parent, text=text, font=FT_DLG_BTN,
                fg='#1e1e1c', bg='#D89018', hover_bg='#C88008',
                cmd=cmd, padx=10, pady=4, parent_bg=ORANGE)

        banner_pill(actions, t('update_banner_update'),
                    lambda: self._show_update_dialog(info),
                    primary=True).pack(side='left', padx=3)
        banner_pill(actions, t('update_banner_later'),
                    self._dismiss_update_banner).pack(side='left', padx=3)
        banner_pill(actions, t('update_banner_skip'),
                    lambda: self._skip_update(info)).pack(side='left', padx=3)

        # Let the banner auto-size to its natural content; no forced minimum.
        bar.update_idletasks()
        bw = bar.winfo_reqwidth()
        bh = bar.winfo_reqheight()
        self._banner_size = (bw, bh)
        self._reposition_banner(bw, bh)
        self._present(bar, lambda: self._reposition_banner(bw, bh))
        bar.bind('<Escape>', lambda e: self._dismiss_update_banner())

        self._banner_follow_id = self.root.bind(
            '<Configure>',
            lambda e: self.root.after_idle(self._on_banner_follow),
            add='+')

    def _reposition_banner(self, bw, bh):
        """Recompute banner position, clamped to the virtual desktop so it
        stays on the same monitor as the widget."""
        bar = getattr(self, '_update_banner', None)
        if not bar or not bar.winfo_exists():
            return
        vx, vy, vw, vh = self._virtual_bounds()
        wx = self.root.winfo_x() + (self.root.winfo_width() - bw) // 2
        wy = self.root.winfo_y() - bh - 8
        if wy < vy + SCREEN_MARGIN:
            wy = self.root.winfo_y() + self.root.winfo_height() + 8
        wx = max(vx + SCREEN_MARGIN, min(wx, vx + vw - bw - SCREEN_MARGIN))
        wy = max(vy + SCREEN_MARGIN, min(wy, vy + vh - bh - TASKBAR_GAP))
        bar.geometry(f'{bw}x{bh}+{wx}+{wy}')
        return (bw, bh, wx, wy)

    def _on_banner_follow(self):
        # Not while the widget changes behind its picture (_reshape, the widget
        # cloaked): the banner went to the widget's new place 50 to 110 ms
        # before the widget showed there. It follows when the widget comes out
        # (done). A widget reshaped in view (a taskbar button, no picture) is
        # followed at once, as before (second review of 2026-09-28).
        if self._reshaping and getattr(self.root, '_cloaked', False):
            return
        bw, bh = getattr(self, '_banner_size', (0, 0))
        if bw and bh:
            self._reposition_banner(bw, bh)

    def _dismiss_update_banner(self):
        bar = getattr(self, '_update_banner', None)
        if bar:
            try:
                bar.destroy()
            except Exception:
                pass
            self._update_banner = None
        follow_id = getattr(self, '_banner_follow_id', None)
        if follow_id:
            try:
                self.root.unbind('<Configure>', follow_id)
            except Exception:
                pass
            self._banner_follow_id = None

    def _skip_update(self, info):
        self.cfg['skip_version'] = info['version']
        save_cfg(self.cfg)
        wlog(f"UPDATE  v{info['version']} marked as skipped")
        self._dismiss_update_banner()

    def _show_info_toast(self, message):
        """Transient feedback toast aligned to the widget - used for manual checks."""
        dlg = tk.Toplevel(self.root)
        dlg.overrideredirect(True)
        dlg.attributes('-topmost', True)
        dlg.configure(bg=MENU_BG)
        tk.Label(dlg, text=message, font=FT_DLG_BODY, fg=FG, bg=MENU_BG,
                 padx=16, pady=10, wraplength=self.dp(340), justify='left').pack()
        self._prepare(dlg)      # cloaked until it is whole (_present)

        def place():
            dlg.update_idletasks()
            dw, dh = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
            wx, wy = self._place_popup(dw, dh, prefer='below')
            dlg.geometry(f'{dw}x{dh}+{wx}+{wy}')
            return (dw, dh, wx, wy)

        place()
        self._present(dlg, place)
        dlg.after(3500, dlg.destroy)

    def _show_update_dialog(self, info):
        """Full update dialog: shows changelog + download button + progress."""
        self._dismiss_update_banner()
        # Scale + clamp to the monitor: at a fixed 520x440 the content outgrew
        # the box on scaled displays and the bottom row could not fit.
        dw, dh = self._dlg_size(520, 440)
        # Raised, not rebuilt: this window may be downloading the installer
        # on a worker thread that reports back to it, and closing it then
        # would leave the installer downloaded and never started.
        live = self._dialogs.get('update')
        try:
            if live is not None and live.winfo_exists():
                live.lift()
                live.focus_force()
                return
        except tk.TclError:
            pass
        dlg, body = self._build_dialog_frame(t('update_dlg_title'), dw, dh, key='update')

        subtitle = t('update_dlg_subtitle').format(
            version=info['version'], current=APP_VERSION)
        tk.Label(body, text=subtitle, font=FT_DLG_H, fg=CLAUDE_TEXT, bg=BG,
                 anchor='w').pack(fill='x')

        tk.Label(body, text=t('update_dlg_changelog'), font=FT_DLG_BODY, fg=FG,
                 bg=BG, anchor='w').pack(fill='x', pady=(10, 4))

        raw_changelog = info.get('body') or ''
        raw_changelog = strip_boilerplate_sections(raw_changelog)
        changelog = raw_changelog or t('update_dlg_no_changelog')
        if len(changelog) > UPDATE_CHANGELOG_MAX_CHARS:
            changelog = changelog[:UPDATE_CHANGELOG_MAX_CHARS].rstrip() + '\u2026'

        # Bottom controls first (see the layout contract in _build_dialog_frame):
        # any height shortage compresses the changelog, never the buttons.
        btn_frame = tk.Frame(body, bg=BG)
        btn_frame.pack(fill='x', side='bottom', pady=(12, 0))

        progress_cv = tk.Canvas(body, height=6, bg=BG, bd=0, highlightthickness=0)
        progress_cv.pack(fill='x', pady=(4, 0), side='bottom')
        progress_cv.pack_forget()

        status_lbl = tk.Label(body, text='', font=FT_DLG_HINT, fg=DIM, bg=BG,
                              anchor='w', wraplength=dw - 40)
        status_lbl.pack(fill='x', pady=(10, 0), side='bottom')

        txt_frame = tk.Frame(body, bg=BAR_BG, bd=0, highlightthickness=0)
        txt_frame.pack(fill='both', expand=True)
        txt = tk.Text(txt_frame, font=FT_DLG_BODY, fg=DIM, bg=BAR_BG, bd=0,
                      highlightthickness=0, wrap='word',
                      padx=12, pady=10, height=9, relief='flat',
                      cursor='arrow', spacing1=2, spacing3=2)
        render_markdown_into(txt, changelog,
                             base_font=FT_DLG_BODY, fg=DIM, header_fg=FG)
        txt.pack(fill='both', expand=True)

        install_state = {'btn': None, 'enabled': True}

        def fmt_size(n):
            for unit in ('B', 'KB', 'MB'):
                if n < 1024 or unit == 'MB':
                    return f'{n:.1f} {unit}' if unit != 'B' else f'{n} {unit}'
                n /= 1024
            return f'{n:.1f} GB'

        def draw_progress(pct):
            w = progress_cv.winfo_width()
            progress_cv.delete('all')
            pill(progress_cv, 0, 0, w, 6, BAR_BG)
            if pct > 0:
                fw = max(6, w * pct / 100)
                pill(progress_cv, 0, 0, fw, 6, CLAUDE)

        def on_progress(done, total):
            pct = int(done * 100 / total) if total else 0
            status_lbl.config(
                text=t('update_dlg_downloading').format(
                    percent=pct, done=fmt_size(done),
                    total=fmt_size(total) if total else '?'),
                fg=DIM)
            draw_progress(pct)

        def start_download():
            if not info.get('asset_url'):
                webbrowser.open(info['html_url'])
                dlg.destroy()
                return
            self._hold(dlg)
            build_install_btn(enabled=False)
            # Re-insert right after btn_frame in the packing order so the
            # bottom stack stays: buttons, progress above them, status above.
            progress_cv.pack(fill='x', pady=(4, 0), side='bottom',
                             after=btn_frame)
            status_lbl.config(text=t('update_dlg_downloading').format(
                percent=0, done='0',
                total=fmt_size(info.get('asset_size') or 0)), fg=DIM)
            dest = os.path.join(tempfile.gettempdir(),
                                f'ClaudeUsage-Setup-{info["version"]}.exe')

            def worker():
                try:
                    download_installer(
                        info['asset_url'], dest,
                        on_progress=lambda d, tot: dlg.after(0, on_progress, d, tot))
                    dlg.after(0, lambda: status_lbl.config(
                        text=t('update_dlg_launching'), fg=BLUE))
                    dlg.after(300, lambda: self._launch_installer(dest))
                except Exception as e:
                    wlog(f'UPDATE  download failed: {e}')
                    # Bind the message as a default arg (see detect() above):
                    # a bare `e` in this deferred callback would NameError.
                    dlg.after(0, lambda msg=str(e): status_lbl.config(
                        text=t('update_dlg_failed').format(error=msg), fg=RED))
                    dlg.after(0, lambda: build_install_btn(enabled=True))

            threading.Thread(target=worker, daemon=True).start()

        def build_install_btn(enabled=True):
            self._hold(dlg)
            if install_state['btn'] is not None:
                install_state['btn'].destroy()
            b = self._primary_pill(btn_frame, t('update_dlg_install'),
                                   start_download if enabled else (lambda: None),
                                   enabled=enabled)
            b.pack(side='right')
            install_state['btn'] = b

        # Secondary actions (left-aligned on the left, right-aligned next to primary)
        self._secondary_pill(btn_frame, t('update_dlg_open_page'),
                             lambda: webbrowser.open(info['html_url'])).pack(
            side='left')
        self._secondary_pill(btn_frame, t('update_dlg_cancel'),
                             dlg.destroy).pack(side='right', padx=(0, 8))
        build_install_btn(enabled=True)
        # Double-pass measurement, same as every other dialog: the single
        # idle-time pass in _build_dialog_frame under-reports the height on
        # scaled displays (the button row is not yet accounted for), which
        # fixed the dialog too short.
        self._place_dialog(dlg, dw, dh_floor=dh)

    def _launch_installer(self, path):
        """Start the installer once nothing holds the renewal lock.

        🔴 The installer closes this process by force (CloseApplications=force:
        the Restart Manager ends it within about 30 s), whatever the quit that
        follows would have waited for. Started while a renewal was out, it cut
        the renewal between the server's answer and the write of it, and the
        saved login was lost (review of 2026-09-30, third iteration). So the
        gate closes first (nothing new starts; a Verify or a Switch pressed
        now answers "busy"), and the installer starts when what is under way
        has finished. The widget stays on screen meanwhile: the elevation
        prompt wants a process with the focus.

        The command line the scripts run is closed by the installer too: the
        lock is tried in every process, and a mark in the store keeps any
        process from starting one (cc_accounts.mark_install). One install at a
        time: a second download while one waits only changes the file it
        runs; two waits ran two installers over each other (review of
        2026-09-30, fourth iteration).
        """
        self._install_path = path
        if getattr(self, '_install_waiting', None) is not None:
            return          # a wait is under way: it runs this file
        self._install_waiting = time.monotonic()
        cc_accounts.close_renewals()
        cc_accounts.mark_install()
        self._install_when_free()

    def _install_when_free(self):
        """_launch_installer's wait: the installer once no process holds the
        renewal lock, or QUIT_RENEWAL_WAIT_S at the latest."""
        busy = cc_accounts.renewal_busy()
        if busy and time.monotonic() - self._install_waiting < QUIT_RENEWAL_WAIT_S:
            if not getattr(self, '_install_logged', False):
                self._install_logged = True
                wlog('UPDATE  waiting for a renewal to be kept before installing')
            self.root.after(200, self._install_when_free)
            return
        if busy:
            wlog('UPDATE  a renewal is still at work: installing anyway')
        # Reset either way: a retry after a failed start waits again.
        self._install_waiting = None
        self._install_logged = False
        self._launch_installer_now(self._install_path)

    def _launch_installer_now(self, path):
        """Run the downloaded installer silently and exit so it can replace files.

        /VERYSILENT hides the wizard entirely (no language picker, no Next/Finish)
        and /NORESTART prevents the rare reboot request. The ISS [Run] section
        auto-relaunches the widget after install, so the whole cycle is: click
        Install -> brief pause while files are swapped -> new version is up.

        The silent run can fail, and its failure used to be invisible AND
        destructive: [InstallDelete] has already removed the old _internal by
        the time a file turns out to be locked, /SUPPRESSMSGBOXES answers the
        "file in use" box with its default of Abort, Setup rolls back, and the
        user is left with a widget that cannot start and nothing to read
        (measured on 2026-08-17: a foreign process had one of our DLLs loaded).
        Dropping /SUPPRESSMSGBOXES is not the answer either: under /VERYSILENT
        there is no window to show that box, so it would wait behind whatever
        the user is looking at, forever, holding a half-written folder.

        So the silent run keeps its suppressed boxes, and a run that failed
        PART WAY THROUGH is followed by the SAME installer with its wizard
        visible: the second run can say what is wrong and offer Retry, and its
        file-in-use prompt has a window to belong to. cmd is what does the
        chaining, since we must exit immediately for the files to be
        replaceable at all. The Inno log goes next to ours so the next failure
        can be read rather than guessed.

        The retry is gated on exit code 3 or above, which is Inno for "this got
        as far as preparing or installing and then went wrong" (3 and 4 fatal
        errors, 5 an aborted or cancelled install: the destructive case), and on
        codes below zero, which is how a process killed by the system reads:
        0xC0000005 and its family. That case matters most of all, because
        nothing rolls back when Setup dies outright, so the folder is left
        exactly as [InstallDelete] and the failed copy left it. Codes 1 and 2
        are deliberately excluded. 2 in particular is what Setup returns when
        the user declines the elevation prompt, and re-launching would ask for
        the same elevation again, for an update they just refused, with the
        widget already gone from the screen and nothing to explain the second
        prompt. A refusal is a decision, and a second run cannot improve it.
        """
        log = os.path.join(DIR, 'install.log')
        # A second file, because /LOG overwrites: sharing one name would mean a
        # successful retry erased the log of the failure that called it.
        run = f'"{path}" /NORESTART "/LOG={os.path.join(DIR, "install-retry.log")}"'
        # cmd needs the whole chain wrapped in one more pair of quotes when it
        # starts with a quoted path, and the exe is quoted separately because
        # both %ProgramFiles% and our own folder have spaces in them.
        # `if errorlevel N` means "N or above" and compares SIGNED, which is why
        # the negative case is tested first and separately: `not errorlevel 0`
        # is true only for a code below zero, i.e. an abnormal termination.
        # The two branches are exclusive, so the installer runs at most twice.
        chain = (f'""{path}" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART "/LOG={log}"'
                 f' & if not errorlevel 0 ({run}) else if errorlevel 3 ({run})"')
        try:
            proc = subprocess.Popen(
                f'cmd /c {chain}',
                creationflags=(subprocess.DETACHED_PROCESS
                               | subprocess.CREATE_NEW_PROCESS_GROUP
                               | subprocess.CREATE_NO_WINDOW),
                close_fds=True)
        except Exception as e:
            wlog(f'UPDATE  launch failed: {e}')
            # The widget stays: renewals may start again (_launch_installer).
            cc_accounts.clear_install_mark()
            cc_accounts.reopen_renewals()
            return
        # The mark now lasts as long as the installer's chain runs, however
        # long its elevation prompt waits: counted from the click it lapsed
        # under a prompt left waiting (review of 2026-09-30, fifth
        # iteration). Outside the try: the installer has started either way.
        cc_accounts.mark_install(pid=getattr(proc, 'pid', None))
        wlog('UPDATE  installer launched (/VERYSILENT), exiting widget')
        self._save_geometry()
        # Give the OS a beat to start the installer before we vanish - the
        # UAC prompt needs to appear while our process still has focus.
        self.root.after(400, self._quit)

    # ── Connection self-test ─────────────────────────

    def _selftest_dialog(self):
        """Run the connection checks and show each result as it lands.

        The checks run on a worker thread: they make real network calls and
        would otherwise freeze the widget for several seconds.
        """
        dw, dh = self._dlg_size(520, 460)
        dlg, body = self._build_dialog_frame(t('selftest_title'), dw, dh, key='selftest')
        state = {'steps': [], 'running': False}
        actions = {'copy': None, 'rerun': None}

        # Bottom controls first (see the layout contract in _build_dialog_frame).
        btn_frame = tk.Frame(body, bg=BG)
        btn_frame.pack(fill='x', side='bottom', pady=(12, 0))
        status_lbl = tk.Label(body, text=t('selftest_running'), font=FT_DLG_HINT,
                              fg=DIM, bg=BG, anchor='w', justify='left',
                              wraplength=dw - 2 * DLG_PAD_X)
        status_lbl.pack(fill='x', pady=(10, 0), side='bottom')

        tk.Label(body, text=t('selftest_hint'), font=FT_DLG_BODY, fg=DIM, bg=BG,
                 anchor='w', justify='left',
                 wraplength=dw - 2 * DLG_PAD_X).pack(fill='x', pady=(0, 10))

        txt_frame = tk.Frame(body, bg=BAR_BG)
        txt_frame.pack(fill='both', expand=True)
        txt = tk.Text(txt_frame, font=FT_DLG_BODY, fg=DIM, bg=BAR_BG, bd=0,
                      highlightthickness=0, wrap='word', padx=12, pady=10,
                      height=12, relief='flat', cursor='arrow',
                      spacing3=4, state='disabled')
        # The status marks are geometry, not text: pinned to FT_DOT so the
        # Japanese family does not redraw them full-width.
        for status, colour in (('ok', DOT_GREEN), ('warn', ORANGE),
                               ('fail', RED), ('skip', DIM)):
            txt.tag_configure(status, foreground=colour, font=FT_DOT)
        txt.tag_configure('label', foreground=FG)
        txt.tag_configure('detail', foreground=DIM, lmargin1=18, lmargin2=18)
        # The list can outgrow the box on a short screen, and a read-only Text
        # has no wheel binding of its own. One line per event whatever the
        # delta: a precision touchpad sends deltas well under a notch, and
        # dividing them would round away every scroll in one direction.
        txt.bind('<MouseWheel>',
                 lambda e: (txt.yview_scroll(-1 if e.delta > 0 else 1, 'units'), 'break')[1])
        txt.pack(fill='both', expand=True)

        marks = {'ok': '✓', 'warn': '!', 'fail': '✕', 'skip': '–'}
        colours = {'ok': DOT_GREEN, 'warn': ORANGE, 'fail': RED, 'partial': BLUE}

        def render(step):
            txt.config(state='normal')
            txt.insert('end', marks.get(step['status'], '?') + '  ', step['status'])
            txt.insert('end', step['label'] + '\n', 'label')
            if step['detail']:
                txt.insert('end', step['detail'] + '\n', 'detail')
            txt.see('end')
            txt.config(state='disabled')

        def copy_report():
            self.root.clipboard_clear()
            self.root.clipboard_append(selftest_report(state['steps']))
            status_lbl.config(text=t('selftest_copied'), fg=DOT_GREEN)

        def build_actions():
            """Rebuild the button row. The pills are images, so enabling or
            disabling one means recreating it. While the checks run there is
            nothing to copy and nothing to re-run, and a pill that looks
            clickable but ignores clicks is worse than no pill at all."""
            self._hold(dlg)
            ready = not state['running']
            for key in actions:
                if actions[key] is not None:
                    actions[key].destroy()
                actions[key] = None
            actions['copy'] = self._primary_pill(
                btn_frame, t('selftest_copy'),
                copy_report if ready else (lambda: None), enabled=ready)
            actions['copy'].pack(side='right')
            if ready:
                actions['rerun'] = self._secondary_pill(
                    btn_frame, t('selftest_rerun'), run)
                actions['rerun'].pack(side='right', padx=(0, 8))

        def finish(steps):
            self._hold(dlg)
            state['steps'] = steps
            state['running'] = False
            # Back to the top: the list follows along while the checks run, but
            # what matters once they are done is the first failing line.
            txt.yview_moveto(0)
            worst = selftest_verdict(steps)
            status_lbl.config(text=t('selftest_summary_' + worst), fg=colours[worst])
            build_actions()

        def post(fn, *args):
            """Hand a result back to the UI thread.

            The checks keep running for a few seconds after the dialog is
            closed, so both ends are guarded: scheduling raises once the
            interpreter is gone, and the callback itself must not touch
            widgets that no longer exist.
            """
            def apply():
                try:
                    if dlg.winfo_exists():
                        fn(*args)
                except tk.TclError:
                    pass
            try:
                dlg.after(0, apply)
            except (tk.TclError, RuntimeError):
                pass

        def worker():
            steps = run_connection_selftest(self.cfg,
                                            on_step=lambda s: post(render, s))
            post(finish, steps)

        def run():
            if state['running']:
                return
            self._hold(dlg)
            state['running'] = True
            state['steps'] = []
            txt.config(state='normal')
            txt.delete('1.0', 'end')
            txt.config(state='disabled')
            status_lbl.config(text=t('selftest_running'), fg=DIM)
            build_actions()
            threading.Thread(target=worker, daemon=True).start()

        self._secondary_pill(btn_frame, t('selftest_close'), dlg.destroy).pack(side='left')
        run()
        self._place_dialog(dlg, dw, dh_floor=dh)

    # ── Open config ──────────────────────────────────

    def _open_config(self):
        try:
            subprocess.Popen(['notepad.exe', CFG])
        except Exception:
            try:
                os.startfile(CFG)
            except Exception:
                pass

    # ── Open Claude Usage page ────────────────────────

    def _open_claude_usage(self):
        """Open the Claude.ai usage settings page in the default browser."""
        webbrowser.open('https://claude.ai/settings/usage')

    def _open_repo(self):
        """Open the project's GitHub repo in the default browser."""
        webbrowser.open(f'https://github.com/{UPDATE_REPO}')

    def _toggle_notifications(self):
        """Toggle Windows toast notifications for session-usage thresholds."""
        new_value = not self.cfg.get('notifications_enabled', True)
        self.cfg['notifications_enabled'] = new_value
        save_cfg(self.cfg)
        wlog(f'TOAST  notifications_enabled -> {new_value}')

    def _toggle_taskbar(self):
        """Toggle whether the widget shows in the Windows taskbar.

        Off (default) keeps the widget as a pure floating tool window
        (WS_EX_TOOLWINDOW). On flips it to WS_EX_APPWINDOW so Windows
        gives it a real taskbar icon - which is also a prerequisite for
        the ITaskbarList3 progress overlay (set in _push_taskbar_state).
        """
        new_value = not self.cfg.get('show_in_taskbar', False)
        self.cfg['show_in_taskbar'] = new_value
        save_cfg(self.cfg)
        wlog(f'TASKBAR show_in_taskbar -> {new_value}')
        self._apply_taskbar_visibility()
        # Push the latest cached usage onto the new taskbar icon (if any),
        # otherwise the bar appears empty until the next refresh tick.
        self._push_taskbar_state()

    # ── Session renewal ──────────────────────────────

    def _renew_session(self):
        # The account and its org are fixed as the dialog opens: the dialog
        # stays open while a key is copied, and the account on show can
        # change meanwhile (review of 2.11, first iteration).
        acc = active_account(self.cfg) or {}
        self._session_key_dialog(t('dlg_renew_title'), keep_org=acc.get('org_id'),
                                 target_id=acc.get('id'))

    # ── Open session key guide ────────────────────────

    def _open_guide(self):
        """Open session key guide HTML in default browser with current language."""
        guide = os.path.join(EXE_DIR, 'guide', 'session-key-guide.html')
        if not os.path.exists(guide):
            guide = os.path.join(os.path.dirname(EXE_DIR), 'guide', 'session-key-guide.html')
        if os.path.exists(guide):
            # Pass current widget language via URL hash
            webbrowser.open(f'file:///{guide}#lang={_current_lang}')
        else:
            webbrowser.open('https://claude.ai')

    # ── Session key dialog (shared by setup + renew) ──

    def _session_key_dialog(self, title, is_setup=False, on_success=None,
                            prefill=None, show_name=False, name_prefill='',
                            on_done=None, offer_cc=True, start_cc=False,
                            keep_org=None, target_id=None):
        """Key entry dialog, shared by setup / renew / add / edit-key.

        on_success(key, info, name): when given, called with the verified key,
        its account info (org_id, email, name, plan) and the account name from
        the optional name field, instead of the default behaviour of writing
        the active account. prefill seeds the key entry; without it the dialog
        shows the active key (legacy renew flow). show_name adds an account
        name field (add / edit), so the key and name are set together.
        keep_org: the org of the account whose key this is (renew, edit-key);
        a login that reads several orgs keeps that one instead of asking.
        target_id: the account the default behaviour writes (renew), fixed
        when the dialog opens; without it, the active account.
        """
        # The Claude Code shortcut is offered on setup / renew (writes the
        # active account) and on add (on_done rebuilds the list); not on
        # edit-key, which targets a specific account this dialog cannot see.
        show_cc = (offer_cc and os.path.exists(cc_accounts.cred_file())
                   and (on_success is None or bool(on_done)))
        dh = 392 if show_name else 320
        if show_cc:
            dh += 56  # one more pill row above the key entry
        dw, dh = self._dlg_size(460, dh)
        # The height for the whole key form, kept for when the login way gives
        # up and the form is asked for; the login way alone needs a short one.
        dh_full = dh
        if start_cc:
            dh = self._dlg_size(460, 190)[1]
        dlg, body = self._build_dialog_frame(title, dw, dh, key='session_key')
        if start_cc:
            # Room for the whole key form, should the login fail and the
            # form be asked for: it unfolds downward (_place_dialog).
            dlg._reserve = max(0, dh_full - dh)

        # Bottom controls first (see the layout contract in _build_dialog_frame).
        btn_frame = tk.Frame(body, bg=BG)
        btn_frame.pack(fill='x', side='bottom', pady=(12, 0))
        connect_state = {'btn': None}
        # 'login': the user chose the Claude Code login on the screen before,
        # so only that way is on show until it fails and they ask for a key.
        mode = {'login': bool(start_cc), 'hidden': [], 'extra': [], 'head': None,
                'busy': False}

        if is_setup:
            tk.Label(body, text=t('dlg_welcome_hint'), font=FT_DLG_BODY, fg=DIM,
                     bg=BG, anchor='w', justify='left',
                     wraplength=dw - 40).pack(fill='x', pady=(0, 14))

        # Step 1 - guide
        tk.Label(body, text=t('dlg_step_guide'), font=FT_DLG_H, fg=FG, bg=BG,
                 anchor='w').pack(fill='x')
        self._secondary_pill(body, t('dlg_open_guide'), self._open_guide,
                             icon='\U0001F4D6').pack(anchor='w', pady=(8, 16))

        # Shortcut past the key entirely when Claude Code is signed in on this
        # machine: its token refreshes itself, so there is nothing to renew.
        if show_cc:
            self._secondary_pill(body, t('cc_use_login'),
                                 lambda: use_claude_code(),
                                 icon='\u26A1').pack(anchor='w', pady=(0, 16))

        # Step 2 - paste
        tk.Label(body, text=t('dlg_step_paste'), font=FT_DLG_H, fg=FG, bg=BG,
                 anchor='w').pack(fill='x')

        entry_wrap = tk.Frame(body, bg=BAR_BG, padx=1, pady=1)
        entry_wrap.pack(fill='x', pady=(8, 0))
        entry = tk.Entry(entry_wrap, font=FT_DLG_BODY, bg=BAR_BG, fg=FG,
                         insertbackground=FG, bd=0,
                         highlightthickness=0, relief='flat')
        entry.pack(fill='x', ipady=7, ipadx=10)
        def on_focus_in(e):
            entry_wrap.configure(bg=FOCUS_RING)
            # Select the whole key so pasting a new one replaces it outright.
            # after_idle is required: the click that grants focus is processed
            # after this handler and would drop the selection to place the
            # caret. Once focused, a further click positions the caret as usual.
            entry.after_idle(lambda: (entry.select_range(0, 'end'),
                                      entry.icursor('end')))

        entry.bind('<FocusIn>',  on_focus_in)
        entry.bind('<FocusOut>', lambda e: entry_wrap.configure(bg=BAR_BG))

        if prefill is not None:
            entry.insert(0, prefill)
        elif on_success is None and self.cfg.get('session_key'):
            entry.insert(0, self.cfg['session_key'])
        entry.focus_set()

        # Optional account name field (add / edit): set the key and the name in
        # one place instead of renaming as a separate step afterwards.
        name_entry = None
        if show_name:
            tk.Label(body, text=t('dlg_account_name'), font=FT_DLG_H, fg=FG,
                     bg=BG, anchor='w').pack(fill='x', pady=(14, 0))
            name_wrap = tk.Frame(body, bg=BAR_BG, padx=1, pady=1)
            name_wrap.pack(fill='x', pady=(8, 0))
            name_entry = tk.Entry(name_wrap, font=FT_DLG_BODY, bg=BAR_BG, fg=FG,
                                  insertbackground=FG, bd=0,
                                  highlightthickness=0, relief='flat')
            name_entry.pack(fill='x', ipady=7, ipadx=10)
            name_entry.insert(0, name_prefill or '')
            name_entry.bind('<FocusIn>', lambda e: name_wrap.configure(bg=FOCUS_RING))
            name_entry.bind('<FocusOut>', lambda e: name_wrap.configure(bg=BAR_BG))

        status_lbl = tk.Label(body, text='', font=FT_DLG_HINT, fg=DIM, bg=BG,
                              anchor='w', justify='left', wraplength=dw - 40)
        status_lbl.pack(fill='x', pady=(8, 0))

        cancel_pill = self._secondary_pill(btn_frame, t('dlg_cancel'), dlg.destroy)

        def place_main(btn, keep_cancel=True):
            # The main action on the right edge and Cancel beside it, as in
            # every other window: Cancel used to be packed first, so it took
            # the edge and the main action landed on its left, touching it.
            cancel_pill.pack_forget()
            btn.pack(side='right')
            if keep_cancel:
                cancel_pill.pack(side='right', padx=(0, 8))

        def build_connect(enabled=True):
            self._hold(dlg)   # the pill is an image: changed, it is built again
            if connect_state['btn'] is not None:
                connect_state['btn'].destroy()
            b = self._primary_pill(btn_frame, t('dlg_connect'),
                                   save_key if enabled else (lambda: None),
                                   enabled=enabled)
            place_main(b)
            connect_state['btn'] = b

        cancel_pill.pack(side='right', padx=(0, 8))

        def pinned():
            """The account the default behaviour writes, by the key or by the
            Claude Code login: the one the renewal was opened for (target_id),
            or the one on show when there is none; None once removed."""
            if target_id is not None:
                return next((x for x in self.cfg.get('accounts', [])
                             if x.get('id') == target_id), None)
            return active_account(self.cfg)

        def renewal_gone():
            """Whether the account the renewal was opened for was removed while
            the dialog was open. Its login is not a new account: written as
            one, the removed account came back as the account on show, by the
            key (review of 2.11, second iteration) and by the Claude Code login
            (after the third)."""
            return on_success is None and target_id is not None and pinned() is None

        def save_key():
            self._hold(dlg)
            key = entry.get().strip().strip('"').strip("'")
            if not key:
                status_lbl.config(text=t('dlg_paste_empty'), fg=RED)
                return
            if not key.startswith('sk-ant-'):
                status_lbl.config(text=t('dlg_invalid_prefix'), fg=RED)
                return
            build_connect(enabled=False)
            status_lbl.config(text=t('dlg_verifying'), fg=BLUE)
            dlg.update_idletasks()
            def detect():
                try:
                    info = fetch_account_info(key)
                except Exception as e:
                    # Bind the message as a default arg: Python clears the
                    # except variable when the block exits, so a bare `e`
                    # inside a deferred callback raises NameError instead of
                    # showing the error (leaving Connect stuck disabled).
                    dlg.after(0, lambda msg=str(e): on_err(msg))
                    return
                dlg.after(0, lambda: resolve(info))
            def resolve(info):
                # A login that reads several orgs (a personal and a team one,
                # #16): an account that already tracks one of them keeps it,
                # since a new key is not a new account; otherwise the user
                # picks, the browser's last-active org preselected. Taking
                # that default silently gave a team account the personal
                # org's numbers on a key renewal, and never let the second
                # org be added at all.
                choices = info.get('org_choices') or []
                if len(choices) > 1:
                    kept = next((c for c in choices if keep_org and c['id'] == keep_org), None)
                    nm = name_entry.get().strip() if name_entry else ''
                    def chosen(org_id, tier):
                        info['org_id'] = org_id
                        info['plan'] = plan_label(tier)
                        info['org_name'] = next(
                            (c['name'] for c in choices if c['id'] == org_id), '')
                        commit(info, nm)
                    if kept is not None:
                        chosen(kept['id'], kept['tier'])
                        return
                    def pick_cancel():
                        self._hold(dlg)
                        build_connect(enabled=True)
                        status_lbl.config(text='')
                    tracked = {a.get('org_id') for a in self.cfg.get('accounts', [])
                               if a.get('org_id')}
                    self._org_picker_dialog(choices, chosen, on_cancel=pick_cancel,
                                            preselect=info.get('org_id'), tracked=tracked)
                    return
                commit(info, name_entry.get().strip() if name_entry else '')
            def commit(info, name):
                if renewal_gone():
                    # Past the one door of a switch, too: written as a new
                    # account, it became the account on show directly.
                    wlog('RENEW  the account was removed meanwhile: nothing written')
                    try:
                        self._hold(dlg)
                        status_lbl.config(text=t('dlg_renew_gone'), fg=RED)
                        build_connect(enabled=False)
                    except tk.TclError:
                        pass
                    return
                # Close the dialog first (guarded: the org-picker path may have
                # let it go already) so the account write below always runs.
                try:
                    dlg.destroy()
                except tk.TclError:
                    pass
                if on_success is not None:
                    on_success(key, info, name)
                    return
                # Default: write the account the renewal was opened for, not
                # the one on show now (keep_org is that account's org), or
                # the active one; the first account on initial setup.
                a = pinned()
                org_before = (a or {}).get('org_id')
                if a is None:
                    a = {'id': _new_id(),
                         'name': org_account_name(info) or info.get('name') or 'Account 1',
                         'session_key': key, 'org_id': info['org_id'],
                         'email': info.get('email', ''), 'plan': info.get('plan', '')}
                    self.cfg.setdefault('accounts', []).append(a)
                    self.cfg['active_account'] = a['id']
                else:
                    a['session_key'] = key
                    a['org_id'] = info['org_id']
                    # A key puts the account back on the session-key path:
                    # leaving auth set would keep every fetch on the Claude
                    # Code token and quietly ignore the key just entered.
                    a.pop('auth', None)
                    if info.get('email'):
                        a['email'] = info['email']
                    if info.get('plan'):
                        a['plan'] = info['plan']
                on_show = a.get('id') == self.cfg.get('active_account')
                if org_before and org_before != a.get('org_id') and on_show:
                    # Another organization behind the account on show: its
                    # numbers are another account's, so they go.
                    self._set_active_account(a['id'], force=True)
                else:
                    # A renewal whose account is no longer the one on show
                    # (another was chosen while the dialog was open) only
                    # writes its key: it switched back to it, and cleared the
                    # error of the account on show (review of 2.11, second
                    # iteration).
                    mirror_active(self.cfg)
                    save_cfg(self.cfg)
                    if on_show:
                        self._clear_error()
                        self.refresh()
                if is_setup:
                    self._schedule()
            def on_err(msg):
                self._hold(dlg)
                status_lbl.config(text=f"{t('dlg_error_prefix')}: {msg}", fg=RED)
                build_connect(enabled=True)
            threading.Thread(target=detect, daemon=True).start()

        def use_claude_code():
            if mode['busy']:
                # A check is already running: a second click started a second
                # one, and each of them added the account (review of 2026-09-26).
                return
            self._hold(dlg)
            mode['busy'] = True
            if mode['login']:
                clear_extra()
                if mode['head'] is not None:
                    mode['head'].config(text=t('cc_linking'))
            else:
                build_connect(enabled=False)
            status_lbl.config(text=t('dlg_verifying'), fg=BLUE)
            # Read before anything can close the dialog: an Entry outlives its
            # window as a Python object, and reading it afterwards raises.
            nm = name_entry.get().strip() if name_entry else ''

            def fail(msg, kind='retry'):
                mode['busy'] = False
                if not dlg.winfo_exists():
                    wlog('CC     the login check ended after its window was closed')
                    return
                self._hold(dlg)
                status_lbl.config(text=f"{t('dlg_error_prefix')}: {msg}", fg=RED)
                if mode['login']:
                    login_failed(kind)
                else:
                    build_connect(enabled=True)

            def verify():
                """Whose login this is, and nothing more.

                🔴 The usage used to be read here too, and a 429 on
                THAT reading failed everything: on 25 September the user found
                the "paste the session key" window saying the login was fine,
                and the account had not been added. Whoever adds an account
                wants it linked; the numbers come with the first refresh.
                """
                try:
                    prof = fetch_claude_code_profile(force=True)
                except RateLimited:
                    # The identity is also in the credentials file: the
                    # server is not needed to know whose login is already
                    # on this computer.
                    live = cc_accounts.active()
                    if not live.get('org'):
                        dlg.after(0, lambda: fail(t('cc_rate_limited'), 'wait'))
                        return
                    # Only the organization is known here. The e-mail comes
                    # from the identity cache, which lags behind a switch for
                    # hours: it is used only when the cache is describing this
                    # very organization, and the identity is marked partial so
                    # that nothing already known about the account is replaced
                    # with less (review of 2026-09-26).
                    same = live.get('org_cache') == live['org']
                    prof = {'org_id': live['org'],
                            'email': (live.get('email') or '') if same else '',
                            'plan': live.get('plan') or '', 'name': '',
                            '_partial': True}
                except Exception as e:
                    dlg.after(0, lambda msg=str(e): fail(msg))
                    return
                # Renewing the account on show: a login of ANOTHER account is
                # said as such before its plan is read, as decide() says it.
                # Read first, that account's lapsed plan came out as advice to
                # remove this one (review of 2026-09-27).
                if on_success is None:
                    # The account the renewal was opened for: the one on show
                    # changes while the dialog is open (decide() says it when
                    # that account is gone).
                    target = pinned()
                    if (target is not None and target.get('org_id') and prof.get('org_id')
                            and target['org_id'] != prof['org_id']):
                        dlg.after(0, lambda: fail(t('dlg_login_other_account')))
                        return
                try:
                    fetch_usage_claude_code(expect_org=prof.get('org_id'))
                except NoActivePlan as e:
                    # The account itself has nothing to read: trying again
                    # cannot help, and a key would add the same lapsed account.
                    # "Add it again" only where it was being added: the renew
                    # window works on an account that is in the list (review
                    # of 2026-09-26, third iteration).
                    after = ('plan_inactive_add' if is_setup or on_success is not None
                             else 'plan_inactive_next')
                    dlg.after(0, lambda msg=str(e): fail(msg + ' ' + t(after), 'final'))
                    return
                except Exception as e:
                    # The login is proven by the answer above. A 429, or any
                    # other trouble reading the numbers right now, is no
                    # reason to refuse the account: they come with the first
                    # refresh (review of 2026-09-26).
                    wlog(f'CC     usage not read while adding the account: '
                         f'{_redact(str(e))}')
                dlg.after(0, lambda: decide(prof))

            def close():
                try:
                    dlg.destroy()
                except tk.TclError:
                    pass

            def decide(prof):
                mode['busy'] = False
                if not dlg.winfo_exists():
                    # Cancelled while the check ran (Cancel, the X, Escape, or
                    # another window of the same kind): the answer still came
                    # back, and it added the account the user had just
                    # declined (review of 2026-09-26).
                    wlog('CC     the login check ended after its window was '
                         'closed: nothing added')
                    return
                if renewal_gone():
                    wlog('RENEW  the account was removed meanwhile: nothing linked')
                    fail(t('dlg_renew_gone'), 'final')
                    if not mode['login']:
                        build_connect(enabled=False)
                    return
                # The identity decides where this login belongs: on the account
                # that already is this account, or on a new one. Two rows for
                # one Claude account would count the same usage twice and put
                # the user in front of a choice that has no right answer.
                target = pinned() if on_success is None else None
                # By organization only when that is all that is proven: the
                # e-mail fallback matched the account the cache still named,
                # and its organization was then replaced with this one.
                if prof.get('_partial'):
                    existing = find_account_by_org(self.cfg, prof.get('org_id'))
                else:
                    existing = find_account_by_identity(self.cfg, prof.get('org_id'),
                                                        prof.get('email'))
                if (existing is not None and existing.get('org_id')
                        and prof.get('org_id') and existing['org_id'] != prof['org_id']):
                    # Same e-mail, another organization: another account (a
                    # personal and a team organization share one address).
                    existing = None
                if (target is not None and target.get('org_id') and prof.get('org_id')
                        and target['org_id'] != prof['org_id']):
                    # Linking the login to the account on show while Claude
                    # Code is signed in as a different one used to give this
                    # account the other one's organization: every number
                    # after that was the other account's. Said here, while
                    # the window is still open to say it.
                    fail(t('dlg_login_other_account'))
                    return
                if existing is not None and existing is not target:
                    close()
                    self._confirm_dialog(
                        t('dlg_account_exists_title'),
                        t('dlg_account_exists').format(
                            name=existing.get('name') or '-'),
                        lambda: attach(existing, prof), label=t('dlg_update'))
                    return
                close()
                attach(target, prof)

            def attach(acc, prof):
                # Never another organization on top of the one the account
                # counts its usage against: refused before anything is
                # touched. decide() has already said why on screen; this is
                # the last line, not the message.
                if (acc is not None and acc.get('org_id') and prof.get('org_id')
                        and acc['org_id'] != prof['org_id']):
                    wlog('ACCT   refused to give an account another organisation')
                    return
                if acc is None:
                    acc = {'id': _new_id(), 'name': '', 'session_key': '',
                           'org_id': '', 'email': '', 'plan': ''}
                    self.cfg.setdefault('accounts', []).append(acc)
                acc['cc_linked'] = True
                # The identity comes from the profile. A session key already on
                # the account is left alone: the two credentials cover for each
                # other now, and throwing one away to add the other would undo
                # the only protection against an expired token. And a partial
                # identity (the server did not answer) never replaces what the
                # account knew.
                for field, value in (('org_id', prof.get('org_id')),
                                     ('email', prof.get('email')),
                                     ('plan', prof.get('plan'))):
                    if value:
                        acc[field] = value
                if not prof.get('_partial'):
                    acc['profile'] = {k: v for k, v in prof.items()
                                      if k not in ('org_id', 'email', 'plan')}
                if nm:
                    acc['name'] = nm
                elif not acc.get('name'):
                    acc['name'] = prof.get('name') or t('cc_account_name')
                if (on_success is None and target_id is not None
                        and acc.get('id') == target_id
                        and target_id != self.cfg.get('active_account')):
                    # A renewal whose account is no longer the one on show
                    # links its login without switching back to it, as the
                    # key does (review of 2.11, after the third iteration).
                    save_cfg(self.cfg)
                else:
                    self._set_active_account(acc['id'], force=True)
                if is_setup:
                    self._schedule()
                if on_done:
                    on_done()

            threading.Thread(target=verify, daemon=True).start()


        build_connect(enabled=True)
        # Not in the login way: its key field is hidden there, and Enter put
        # "paste the session key in the field above" in the place of the
        # reason the login failed (review of 2026-09-26, third iteration).
        entry.bind('<Return>', lambda e: None if mode['login'] else save_key())
        def clear_extra():
            for w_ in mode['extra']:
                try:
                    w_.destroy()
                except tk.TclError:
                    pass
            mode['extra'] = []
            if not cancel_pill.winfo_manager():
                cancel_pill.pack(side='right', padx=(0, 8))

        def enter_login_mode():
            """Show only what the chosen way needs.

            🔴 From the user, 2026-09-25: "I chose the Claude Code
            login, and got the page that asks me to paste the session key".
            The login way used to run inside the key form, so a login that
            failed left them facing a field they had not asked for, with an
            error about something else above it.
            """
            self._hold(dlg)
            for w_ in body.winfo_children():
                if w_ in (status_lbl, btn_frame) or w_.winfo_manager() != 'pack':
                    continue
                mode['hidden'].append((w_, w_.pack_info()))
                w_.pack_forget()
            if connect_state['btn'] is not None:
                connect_state['btn'].destroy()
                connect_state['btn'] = None
            mode['head'] = tk.Label(body, text=t('cc_linking'), font=FT_DLG_H,
                                    fg=FG, bg=BG, anchor='w', justify='left',
                                    wraplength=dw - 40)
            mode['head'].pack(fill='x', before=status_lbl)
            # The hidden key field kept the keyboard: what was typed went into
            # it, and came back under "Use a session key".
            body.focus_set()

        def login_failed(kind='retry'):
            """What can be done next, by what went wrong.

            'retry': the same login again, or a key instead. 'wait': Claude
            asked to slow down, so closing is the thing to do now and trying
            again stays second. 'final': the account itself cannot be read (no
            plan), so there is only Close: a retry cannot help, and a key would
            add the same lapsed account (review of 2026-09-26). The heading
            says the account was not added, and the window grows to show the
            whole reason.
            """
            self._hold(dlg)
            clear_extra()
            if mode['head'] is not None:
                mode['head'].config(text=t('cc_link_failed'))
            if kind == 'retry':
                main = self._primary_pill(btn_frame, t('dlg_retry'), use_claude_code)
                other = self._secondary_pill(btn_frame, t('dlg_use_key_instead'),
                                             leave_login_mode)
                place_main(main)
            else:
                main = self._primary_pill(btn_frame, t('cc_ui_close'), dlg.destroy)
                other = (self._secondary_pill(btn_frame, t('dlg_retry'), use_claude_code)
                         if kind == 'wait' else None)
                place_main(main, keep_cancel=False)
            mode['extra'] = [main]
            if other is not None:
                other.pack(side='left')
                mode['extra'].append(other)
            self._place_dialog(dlg, dw, dh_floor=dh)

        def leave_login_mode():
            self._hold(dlg)
            clear_extra()
            mode['login'] = False
            if mode['head'] is not None:
                mode['head'].destroy()
                mode['head'] = None
            for w_, info in mode['hidden']:
                info = dict(info)
                info.pop('in', None)
                w_.pack(before=status_lbl, **info)
            mode['hidden'] = []
            status_lbl.config(text='')
            build_connect(enabled=True)
            entry.focus_set()
            self._place_dialog(dlg, dw, dh_floor=dh_full)

        if start_cc:
            # The user already chose the login on the previous screen: this
            # window is about that login, not about a key.
            enter_login_mode()
            dlg.after(60, use_claude_code)

    def _setup_dialog(self):
        self._session_key_dialog(t('dlg_setup_title'), is_setup=True)

    def _org_picker_dialog(self, choices, on_choose, on_cancel=None,
                           preselect=None, tracked=()):
        """Pick which org to track, for a login that reads several.

        `choices` is [{'id', 'name', 'tier'}] (best first); on_choose(org_id,
        tier) runs with the selected org, on_cancel() if the user backs out.
        `preselect` starts on that org (the browser's last-active one);
        `tracked` marks the orgs an account in the list already follows, so
        the user can add the other one (#16).
        """
        dw, dh = self._dlg_size(420, 200)
        dlg, body = self._build_dialog_frame(t('dlg_pick_org_title'), dw, dh, key='org_picker')

        ids = [c['id'] for c in choices]
        start = preselect if preselect in ids else choices[0]['id']
        untracked = [i for i in ids if i not in tracked]
        if start in tracked and untracked:
            start = untracked[0]        # the one not in the list yet is the likely aim
        choice = tk.StringVar(value=start)
        cancelled = {'v': True}

        def use():
            sel = choice.get()
            tier = next((c['tier'] for c in choices if c['id'] == sel), '')
            cancelled['v'] = False
            dlg.destroy()
            on_choose(sel, tier)

        # Bottom controls first (see the layout contract in _build_dialog_frame).
        btn_frame = tk.Frame(body, bg=BG)
        btn_frame.pack(fill='x', side='bottom', pady=(12, 0))
        self._primary_pill(btn_frame, t('dlg_pick_org_use'), use).pack(side='right')
        self._secondary_pill(btn_frame, t('dlg_cancel'), dlg.destroy).pack(
            side='right', padx=(0, 8))

        tk.Label(body, text=t('dlg_pick_org_hint'), font=FT_DLG_BODY, fg=FG,
                 bg=BG, anchor='w', justify='left',
                 wraplength=dw - 2 * DLG_PAD_X).pack(fill='x')

        list_frame = tk.Frame(body, bg=BG)
        list_frame.pack(fill='x', pady=(12, 0))
        for c in choices:
            label = c['name']
            pl = plan_label(c.get('tier'))
            if pl:
                label = f'{label}  ({pl})'
            if c['id'] in tracked:
                label = f"{label}  · {t('dlg_pick_org_added')}"
            # Wrapped: a long team name ran past the edge, and what it cut was
            # the "already in the list" mark (review of 2.11, first iteration).
            tk.Radiobutton(
                list_frame, text=label, value=c['id'], variable=choice,
                font=FT_DLG_BODY, fg=FG, bg=BG, selectcolor=BAR_BG,
                activebackground=BG, activeforeground=FG,
                highlightthickness=0, bd=0, anchor='w', justify='left',
                wraplength=dw - 2 * DLG_PAD_X - self.dp(32)).pack(fill='x', pady=2)

        # Fire on_cancel on any close path (Cancel pill, title-bar X, Escape)
        # except an explicit choice, so the session dialog behind can re-enable
        # its Connect button. <Destroy> bubbles from children; guard on dlg.
        def on_close(e):
            if e.widget is dlg and cancelled['v'] and on_cancel:
                on_cancel()
        dlg.bind('<Destroy>', on_close, add='+')

        self._place_dialog(dlg, dw, dh_floor=dh)

    # ── Accounts ─────────────────────────────────────

    def _tooltip(self, widget, text, delay=0, persistent=False):
        """Hover tooltip for icon-only controls. Sits above the control (below
        it when there is no room), clamped to the widget's monitor. Bound with
        add='+' so it composes with the control's own hover handlers. delay=0
        shows it immediately so the icon meaning is instant.

        `text` may be a callable, read each time the tooltip is shown, for
        controls that outlive what they describe: the UI language can change
        under a permanent control, and the active account can change under a
        bar. Returning an empty string shows nothing."""
        state = {'win': None, 'job': None}

        def cancel():
            if state['job']:
                try:
                    widget.after_cancel(state['job'])
                except Exception:
                    pass
                state['job'] = None

        def hide(e=None):
            cancel()
            if state['win'] is not None:
                self._shown_tips.discard(state['win'])
                try:
                    state['win'].destroy()
                except Exception:
                    pass
                state['win'] = None

        def show():
            state['job'] = None
            if state['win'] is not None or not widget.winfo_exists():
                return
            label = text() if callable(text) else text
            if not label:
                return
            tw = tk.Toplevel(widget)
            tw.overrideredirect(True)
            tw.attributes('-topmost', True)
            # Wrapped: a long reason (an error, a hint) used to run off in one
            # line wider than the screen (review of 2026-09-26).
            tk.Label(tw, text=label, font=FT_DLG_HINT, fg=FG, bg=MENU_BG,
                     wraplength=self.dp(320), padx=8, pady=4,
                     justify='left').pack()
            # Cloaked from its first map and shown whole (_prepare): it used to
            # be mapped before its place was set, and moved there afterwards.
            self._prepare(tw, rounded=False)
            tw.update_idletasks()
            tipw, tiph = tw.winfo_reqwidth(), tw.winfo_reqheight()
            x = widget.winfo_rootx() + widget.winfo_width() // 2 - tipw // 2
            # A tip anchored inside the main window has to clear the WHOLE
            # window, not just its control: the widget re-asserts HWND_TOPMOST
            # every 10ms, so anywhere the two overlap the window is drawn in
            # front and the tip is simply invisible. Making the tip owned by
            # the window does not survive that either (measured). Menus and
            # dialogs keep the tighter placement: they are their own windows.
            host = widget.winfo_toplevel()
            if host is self.root:
                above_y = host.winfo_rooty() - tiph - 6
                below_y = host.winfo_rooty() + host.winfo_height() + 6
            else:
                above_y = widget.winfo_rooty() - tiph - 6
                below_y = widget.winfo_rooty() + widget.winfo_height() + 6
            y = above_y
            ml, mt, mr, mb = self._widget_monitor_area()
            x = max(ml + 4, min(x, mr - tipw - 4))
            if y < mt + 4:      # no room above: drop below instead
                y = below_y
            spot = f'+{int(x)}+{int(y)}'

            def place():
                tw.geometry(spot)
                return spot

            place()
            state['win'] = tw
            self._shown_tips.add(tw)
            self._present(tw, place)

        def enter(e):
            cancel()
            state['job'] = widget.after(delay, show)
        widget.bind('<Enter>', enter, add='+')
        widget.bind('<Leave>', hide, add='+')
        widget.bind('<Button-1>', hide, add='+')
        widget.bind('<Destroy>', hide, add='+')
        # The Button-1 binding above is not enough on the widget's own surface:
        # _bind_drag rebinds Button-1 without add='+' when the strip is
        # (re)built, which drops it, so a gesture has to close these by hand.
        # Only the permanent ones register: menu rows are rebuilt on every
        # open, and registering those would grow the list without bound.
        if persistent:
            self._tooltip_hides.append(hide)

    def _account_bubble(self, parent, acc, size=34):
        base = parent.cget('bg')
        cv = tk.Canvas(parent, width=size, height=size, bg=base,
                       highlightthickness=0, bd=0, cursor='hand2')
        self._paint_bubble(cv, acc, size)
        return cv

    def _paint_bubble(self, cv, acc, size):
        """Draw an account's picture on its canvas, again if it is there.

        A canvas drawn again changes in one repaint. Destroyed and made anew
        for every letter typed in the name, the picture blinked out and back:
        the old one went at once, the new one came at the next paint."""
        cv.delete('all')
        # Anti-aliased disc (Canvas ovals are jagged): render smooth via the
        # shared 4x-downscaled circle image, then overlay the initials.
        img = _dot_image(size, account_color(acc))
        cv._bubble_img = img
        cv.create_image(size / 2, size / 2, image=img, anchor='center')
        mark, kind, fg = account_avatar(acc)
        if kind == 'icon':
            # MDL2 glyphs are centred in their em box, so they need no optical
            # nudge; text does.
            cv.create_text(size / 2, size / 2, text=mark, fill=fg,
                           font=('Segoe MDL2 Assets', max(8, int(size * 0.38))))
        else:
            # Three characters have to fit the disc that two were drawn for.
            scale = 0.40 if len(mark) < 3 else 0.30
            cv.create_text(size / 2, size / 2 + 1, text=mark, fill=fg,
                           font=(FT_DLG_BTN_B.cget('family'),
                                 max(7, int(size * scale)), 'bold'))

    def _link_claude_code(self, acc, on_done, status=None):
        """Attach the Claude Code login on this machine to `acc`.

        The login is only attached when it belongs to this account. Attaching
        somebody else's would make the widget show one account's numbers under
        another account's name, which is the whole reason the identity is
        checked at all.
        """
        def work():
            try:
                prof = fetch_claude_code_profile(force=True)
            except Exception as e:
                self.root.after(0, lambda msg=str(e): done(None, msg))
                return
            self.root.after(0, lambda: done(prof, None))

        def done(prof, err):
            # The page that asked may be gone, closed or replaced by another
            # account's page. The link is still made when it is right, but
            # nothing is written on a dead label and nothing reopens: reopening
            # replaced whatever page was open by then (review of 2026-09-26).
            page_open = status is None or status.winfo_exists()
            if err:
                if page_open and status is not None:
                    status.config(text=err, fg=RED)
                return
            org = acc.get('org_id')
            if org and prof.get('org_id') and prof['org_id'] != org:
                if page_open and status is not None:
                    status.config(text=t('cc_other_account').format(
                        email=prof.get('email') or '?'), fg=RED)
                return
            acc['cc_linked'] = True
            # An account created from the login had no identity of its own
            # until now: fill it in, so the list stops showing a nameless row.
            for key, value in (('org_id', prof.get('org_id')),
                               ('email', prof.get('email')),
                               ('plan', prof.get('plan'))):
                if value and not acc.get(key):
                    acc[key] = value
                    acc['profile'] = {k: v for k, v in prof.items()
                                      if k not in ('org_id', 'email', 'plan')}
            if not acc.get('name') and prof.get('name'):
                acc['name'] = prof['name']
            mirror_active(self.cfg)
            save_cfg(self.cfg)
            self._clear_error()
            self.refresh()
            if page_open:
                on_done()
            else:
                wlog('CC     linked while its page was closed: not reopened')

        if status is not None:
            status.config(text=t('dlg_verifying'), fg=BLUE)
        threading.Thread(target=work, daemon=True).start()

    def _info_row(self, parent, label, value):
        """One `label   value` line, aligned, for the details window."""
        row = tk.Frame(parent, bg=BG)
        row.pack(fill='x', pady=1)
        tk.Label(row, text=label, font=FT_DLG_HINT, fg=DIM, bg=BG, anchor='w',
                 width=18).pack(side='left')
        tk.Label(row, text=value, font=FT_DLG_HINT, fg=FG, bg=BG, anchor='w',
                 justify='left').pack(side='left', fill='x', expand=True)
        return row

    def _reset_card(self, parent, acc, inner_w, dlg, dw, dh):
        """The account's limit resets on its page (#14): what claude.ai grants
        it, until when, and the button to use one, after a confirmation.

        claude.ai is asked off the main thread as the page opens; an answer
        under a minute old is shown at once instead (pages are drawn again
        after many gestures). The card is filled where it is, held still and
        measured again like any other change on the page.

        A status is shown, kept and acted on only while it is about the
        account as it stands: read after the last use of a reset ended, with
        the org the account tracks now and its key (or the key claude.ai
        rotated into it). One read before was shown again when the use
        ended, with its button, and one about another org reached the card
        of the new one, with that org's grant (review of 2.11, second
        iteration)."""
        cache = self.__dict__.setdefault('_reset_status', {})
        # The use of a reset, by account and outside any card: the page can be
        # drawn again, or closed and opened, while the request is out, and the
        # card then on screen must neither offer it again nor lose its outcome
        # (review of 2.11, first iteration). {'busy', 'note', 'at', 'org'}
        claims = self.__dict__.setdefault('_reset_claims', {})
        # The card on screen for each account: an answer goes to it, whichever
        # card asked.
        cards = self.__dict__.setdefault('_reset_cards', {})
        # One more each time a use of a reset ends: a read that went out
        # before may be about the resets of before, and is dropped.
        gens = self.__dict__.setdefault('_reset_gen', {})
        acc_id = acc.get('id')
        # A bench's credentials are fakes: nothing of theirs goes to claude.ai
        # (as with cc_post), and a card that changed after the page showed
        # moved every page a bench measures. A bench that wants the card at
        # work sets _reset_bench_net and replaces fetch_resets/claim_reset.
        quiet = cc_accounts.on_bench() and not getattr(self, '_reset_bench_net', False)
        box = tk.Frame(parent, bg=BAR_BG)
        box.pack(fill='x', pady=(0, self.dp(8)))
        inner = tk.Frame(box, bg=BAR_BG)
        inner.pack(fill='x', padx=self.dp(14), pady=self.dp(12))
        top = tk.Frame(inner, bg=BAR_BG)
        top.pack(fill='x')
        tk.Label(top, text=t('reset_title'), font=FT_DLG_BTN_B, fg=FG, bg=BAR_BG,
                 anchor='w').pack(side='left')
        chip = tk.Label(top, text='', font=FT_DLG_HINT, fg=DIM, bg=BAR_BG, anchor='e')
        chip.pack(side='right')
        # What the card says, built again on every answer: a block for the
        # full reset and one for the 5-hour reset, each with its sentence and
        # its button right under it. One row of buttons under all the
        # sentences left the reader to pair them (2026-10-15).
        content = tk.Frame(inner, bg=BAR_BG)
        content.pack(fill='x')
        # 'read': what the status shown is about, (uses ended, org, key
        # print); 'asked': the same for this card's last request.
        state = {'status': None, 'error': None, 'read': None, 'asked': None}

        def say(parent_, text, gap, tip=''):
            label = tk.Label(parent_, text=text, font=FT_DLG_HINT, fg=DIM, bg=BAR_BG,
                             anchor='w', justify='left',
                             wraplength=inner_w - self.dp(28))
            label.pack(fill='x', pady=(gap, 0))
            if tip:
                self._tooltip(label, tip)

        def block(lines, action, gap, tip=''):
            # `tip`: what the block's reset does, on hover. On the card it
            # was one more line, and with the advanced options open the page
            # ran past a 1080p screen at 150% (measured 2026-10-18); the
            # confirmation says it again before any use.
            frame = tk.Frame(content, bg=BAR_BG)
            frame.pack(fill='x')
            for i, line in enumerate(lines):
                say(frame, line, gap if i == 0 else 0, tip)
            if action:
                row = tk.Frame(frame, bg=BAR_BG)
                row.pack(fill='x', pady=(self.dp(6), 0))
                pill = self._small_pill(row, action[0], action[1])
                pill.pack(side='left')
                if tip:
                    self._tooltip(pill, tip)

        say(content, t('reset_checking'), 6)

        def alive():
            try:
                return bool(dlg.winfo_exists())
            except tk.TclError:
                return False

        def holder():
            """The account as the config holds it now; None once removed."""
            if acc_id is None:
                return acc
            return next((a for a in self.cfg.get('accounts', [])
                         if a.get('id') == acc_id), None)

        def want():
            """What a status has to be about to be shown, kept or used: (uses
            ended, org, key print) of the account now; None once removed."""
            a = holder()
            if a is None:
                return None
            return (gens.get(acc_id, 0), a.get('org_id'),
                    cc_accounts.fingerprint(a.get('session_key')))

        def same(tag, now):
            """Whether a read with `tag` is about the account as `now` says:
            the same uses ended and org, and the key it holds or one it held
            before claude.ai rotated it, which is the same login's
            (_apply_rotated_key)."""
            if tag is None or now is None or tag[:2] != now[:2]:
                return False
            moves = self.__dict__.get('_rotated_prints') or {}
            kprint, seen = tag[2], set()
            while kprint != now[2]:
                if kprint in seen or kprint not in moves:
                    return False
                seen.add(kprint)
                kprint = moves[kprint]
            return True

        def claim():
            """This account's use of a reset: under way, and the outcome of the
            last one while it is recent and about the org the account tracks
            now (a note about another org stayed on the new org's card)."""
            c = dict(claims.get(acc_id) or {})
            a = holder()
            if c.get('note') and (time.monotonic() - c.get('at', 0) > RESET_NOTE_S
                                  or a is None or c.get('org') != a.get('org_id')):
                c['note'] = ''
            return c

        def show(status, error=None, tag=None):
            if not alive():
                return
            state['status'], state['error'], state['read'] = status, error, tag
            self._hold(dlg)
            for w_ in content.winfo_children():
                w_.destroy()
            c = claim()
            using = bool(c.get('busy'))
            note = t('reset_using') if using else c.get('note') or ''
            if not same(tag, want()):
                # Read before the last use ended, or about another org or
                # key: neither said nor offered (see the docstring).
                status, error = None, None
            usable = False
            first = 6           # the gap under the title, then between blocks
            if note:
                block([note], None, first)
                first = self.dp(8)
            if error:
                block([t('reset_failed_check').format(why=error)], None, first)
            elif status is not None:
                grants = status['grants']
                pick = next((g for g in grants if g['id'] == status['next_grant']), None) \
                    or next((g for g in grants if g['usable']), None)
                if grants:
                    lines = [t('reset_grant_left').format(
                        left=g['left'], total=g['total'], when=reset_when(g['ends_at']) or '?')
                        for g in grants]
                    action = None
                    if pick is not None and pick['usable']:
                        # Being used: no button, and nothing that says it
                        # cannot be (review of 2.11, first iteration).
                        if not using:
                            usable = True
                            action = (t('reset_use'),
                                      lambda g=pick, tg=tag: ask(RESET_PROGRAM_FULL, g, tg))
                    elif pick is not None and pick['needs_limit']:
                        lines.append(t('reset_needs_limit'))
                    else:
                        lines.append(t('reset_unusable'))
                    block(lines, action, first, t('reset_full_what'))
                    first = self.dp(8)
                session = status['session']
                said_session = False
                if session and session['available']:
                    said_session = True
                    usable = usable or not using
                    block([t('reset_session_ready').format(n=session['per_week'])],
                          None if using else
                          (t('reset_session_use'),
                           lambda tg=tag: ask(RESET_PROGRAM_SESSION, None, tg)),
                          first)
                    first = self.dp(8)
                elif session and reset_when(session['next_at']):
                    said_session = True
                    block([t('reset_session_next').format(when=reset_when(session['next_at']))],
                          None, first)
                    first = self.dp(8)
                # Under an outcome, "none available" would only repeat it;
                # under "the account changed" it is the news (review of 2.11,
                # third iteration).
                if not grants and not said_session and (not note or c.get('changed')):
                    block([t('reset_none')], None, first)
            elif not using:
                # Under an outcome too: the status of after the use is on
                # its way.
                block([t('reset_checking')], None, first)
            chip.config(text=('● ' + t('reset_available')) if usable else '', fg=DOT_GREEN)
            self._place_dialog(dlg, dw, dh_floor=dh)

        def fetch():
            if quiet:
                return
            a = holder()
            if a is None:
                return
            key, org = a.get('session_key'), a.get('org_id')
            tag = (gens.get(acc_id, 0), org, cc_accounts.fingerprint(key))
            state['asked'] = tag

            def work():
                try:
                    status, error = fetch_resets(key, org), None
                except Exception as e:
                    status, error = None, str(e)
                    wlog(f'RESET  status not read: {_redact(error)}')
                try:
                    self.root.after(0, lambda: take(tag, status, error))
                except (tk.TclError, RuntimeError):
                    pass
            threading.Thread(target=work, daemon=True).start()

        def take(tag, status, error):
            """An answer, on the main thread. Kept, with what it is about, and
            shown on the card on screen, whichever card asked: only while it
            is about the account as it stands."""
            now = want()
            if now is None or tag[0] != now[0]:
                # A use of a reset ended since it went out, and asked again.
                return
            current = cards.get(acc_id)
            if not same(tag, now):
                # About an org or a key the account no longer holds: the card
                # on screen asks for its own.
                if current is not None and current['alive']():
                    current['ensure']()
                return
            # Under the key the account holds now, should it have rotated.
            if status is not None:
                cache[acc_id] = (time.monotonic(), status, now[1], now[2])
            if current is not None and current['alive']():
                current['show'](status, error, now)

        def ensure():
            """Ask for the account as it stands, unless this card shows it or
            has asked for it already."""
            now = want()
            if now is None or same(state['read'], now) or same(state['asked'], now):
                return
            fetch()

        def changed(tag):
            """Whether the account changed since the resets the card offered
            were read (its org or key, or a use that ended): then nothing is
            used, the card says so and asks again. The page stays open while
            a renewal moves the account to another org, and its button sent
            the old org's grant with the new org (review of 2.11, second
            iteration)."""
            if same(tag, want()):
                return False
            wlog('RESET  not used: the account changed since its resets were read')
            a = holder()
            if a is not None and not (claims.get(acc_id) or {}).get('busy'):
                claims[acc_id] = {'busy': False, 'note': t('reset_r_changed'),
                                  'at': time.monotonic(), 'org': a.get('org_id'),
                                  'changed': True}
                redraw_current()
                current = cards.get(acc_id)
                if current is not None and current['alive']():
                    current['ensure']()
            return True

        def ask(program, grant=None, tag=None):
            if changed(tag):
                return
            name = (holder() or acc).get('name') or '-'
            if program == RESET_PROGRAM_FULL:
                week = reset_when((state['status'] or {}).get('weekly_resets_at')) or '?'
                msg = t('reset_confirm_full').format(name=name, week=week)
            else:
                msg = t('reset_confirm_session').format(name=name)
            self._confirm_dialog(t('reset_confirm_title'), msg,
                                 lambda: use(program, grant, tag),
                                 label=t('reset_use') if program == RESET_PROGRAM_FULL
                                 else t('reset_session_use'))

        def redraw_current():
            current = cards.get(acc_id)
            if current is not None and current['alive']():
                st = current['state']
                current['show'](st['status'], st['error'], st['read'])

        def use(program, grant=None, tag=None):
            if (claims.get(acc_id) or {}).get('busy') or quiet:
                return
            # Checked again at the confirmation: it stays open as long as
            # the user wants.
            if changed(tag):
                return
            a = holder()
            key, org = a.get('session_key'), a.get('org_id')
            claims[acc_id] = {'busy': True, 'note': '', 'at': time.monotonic(), 'org': org}
            redraw_current()
            wlog(f'RESET  using a {"full" if program == RESET_PROGRAM_FULL else "5-hour"} reset')

            def work():
                try:
                    answer = claim_reset(key, org, program, (grant or {}).get('id'))
                except Exception as e:
                    wlog(f'RESET  no answer: {_redact(str(e))}')
                    answer = {'result': 'no_answer'}
                try:
                    self.root.after(0, lambda: done(answer, org))
                except (tk.TclError, RuntimeError):
                    pass
            threading.Thread(target=work, daemon=True).start()

        def done(answer, org):
            result = answer.get('result')
            # Every status read until now may be about the resets of before
            # this use: none of them is shown, kept or used any more (take,
            # show), and the one in the cache goes.
            gens[acc_id] = gens.get(acc_id, 0) + 1
            cache.pop(acc_id, None)
            if result == 'reset':
                wlog('RESET  used: cleared ' + (','.join(answer.get('cleared') or []) or '-'))
                note = t('reset_done')
                if acc_id == self.cfg.get('active_account'):
                    self.refresh()
            else:
                wlog(f'RESET  not used: {result} ({answer.get("reason") or "-"})')
                note = t(RESET_OUTCOME_KEYS.get(result, 'reset_r_no_answer'))
            claims[acc_id] = {'busy': False, 'note': note, 'at': time.monotonic(), 'org': org}
            # The card on screen now, whichever card asked: it shows the
            # outcome, without the status of before, and asks claude.ai again.
            redraw_current()
            current = cards.get(acc_id)
            if current is not None and current['alive']():
                current['fetch']()

        cards[acc_id] = {'show': show, 'fetch': fetch, 'ensure': ensure,
                         'state': state, 'alive': alive}
        now = want()
        entry = cache.get(acc_id)
        # An entry in the cache is always of after the last use (done() takes
        # it out, take() puts in only what is about the account as it stands):
        # its org and key are what is left to compare.
        if (now is not None and entry and len(entry) == 4
                and time.monotonic() - entry[0] < RESET_STATUS_REUSE_S
                and same((now[0],) + tuple(entry[2:4]), now)):
            show(entry[1], None, now)
        else:
            if claim().get('busy') or claim().get('note'):
                # A use under way, or its outcome, said before claude.ai
                # answers: a page opened then said only "Checking".
                show(None)
            fetch()

    def _account_details_dialog(self, acc, rebuild, advanced=False, scroll=0.0):
        """One account, in the order a person looks for it.

        🔴 Rebuilt on 2026-09-26 from the user's verdict, "too
        confusing": two cards about Claude Code nobody could tell apart (the
        login the widget reads with, and the saved copy a switch moves onto),
        technical facts in plain view (the live token's expiry shown on every
        linked account, a fingerprint, a snapshot date), one verdict written
        twice, a page taller than a laptop screen, and a bottom button called
        "Save name and appearance" because several gestures on the page wrote
        different things. Now: who it is, what its plan says, one card per way
        in with its state in plain words, the rest folded under Advanced
        options, and a bottom button that saves only what is edited at the
        top, so it can simply say Save. Every other gesture acts at once and
        says so.
        """
        st = {'color': account_color(acc), 'avatar': dict(acc.get('avatar') or {}),
              'advanced': False}
        dw, dh = self._dlg_size(470, 300)
        dlg, body = self._build_dialog_frame(t('dlg_account_details'), dw, dh,
                                             key='account_details')
        inner_w = dw - 2 * DLG_PAD_X

        def close_and(fn=None):
            try:
                dlg.destroy()
            except tk.TclError:
                pass
            rebuild()
            if fn:
                fn()

        def reopen(**kw):
            # Drawn again where it is and as it was: the new page takes this
            # one's place through its key, a picture of this one standing
            # until it shows (_build_dialog_frame). Closed first, the page left
            # the screen empty for as long as the new one took to build; and it
            # came back with the advanced options folded, under the finger of
            # someone who had just used one of them.
            kw.setdefault('advanced', st['advanced'])
            # And scrolled as it was, when its middle scrolls (_scroll_area).
            try:
                kw.setdefault('scroll', dlg._scroll_canvas.yview()[0])
            except (AttributeError, tk.TclError):
                pass
            rebuild()
            self._account_details_dialog(acc, rebuild, **kw)

        # Bottom controls first: the layout contract in _build_dialog_frame.
        bottom = tk.Frame(body, bg=BG)
        bottom.pack(fill='x', side='bottom')
        tk.Frame(bottom, bg=BAR_BG, height=1).pack(fill='x', pady=(14, 12))
        row_btn = tk.Frame(bottom, bg=BG)
        row_btn.pack(fill='x')

        def save_and_close():
            name = name_entry.get().strip()
            if name:
                acc['name'] = name
            acc['color'] = st['color']
            if st['avatar']:
                acc['avatar'] = st['avatar']
            mirror_active(self.cfg)
            save_cfg(self.cfg)
            close_and()

        # It saves what is edited at the top of the page - the name and the
        # picture - and nothing else: every gesture further down acts at once.
        self._primary_pill(row_btn, t('dlg_save'), save_and_close).pack(side='right')
        self._outline_pill(row_btn, t('dlg_remove_account'),
                           lambda: close_and(
                               lambda: self._remove_account(acc, rebuild))).pack(side='left')

        # Everything above the bottom row scrolls when the page is taller than
        # its screen's work area (_scroll_area): from here on, `body` is that
        # middle.
        body = self._scroll_area(dlg, body, start=scroll)

        # ── who it is: the picture with its pencil, the name you can edit ──
        head = tk.Frame(body, bg=BG)
        head.pack(fill='x')
        size, badge = self.dp(48), self.dp(22)
        half = badge // 2
        bub_host = tk.Frame(head, bg=BG, width=size + half, height=size + half)
        bub_host.pack(side='left', anchor='n', padx=(0, self.dp(14)))
        bub_host.pack_propagate(False)

        def pick_avatar(_e=None):
            def on_done(hexval, avatar):
                st['color'], st['avatar'] = hexval, avatar
                draw_bubble()
            self._avatar_dialog(acc, st, on_done)

        bubble = {'cv': None}

        def draw_bubble():
            # The picker can outlive the page (a page closed or drawn again
            # while it was open): nothing left to draw on.
            if not bub_host.winfo_exists():
                return
            try:
                typed = name_entry.get().strip()
            except NameError:
                typed = ''
            # The name being typed feeds the initials, so the picture is a
            # preview of a change that has not been saved yet.
            shown = dict(acc, color=st['color'], avatar=st['avatar'],
                         name=typed or acc.get('name'))
            if bubble['cv'] is not None and bubble['cv'].winfo_exists():
                # Drawn again on its own canvas (_paint_bubble): made anew, the
                # picture blinked with every letter typed in the name.
                self._paint_bubble(bubble['cv'], shown, size)
                return
            b = self._account_bubble(bub_host, shown, size=size)
            bubble['cv'] = b
            b.place(x=half, y=half)
            b.bind('<Button-1>', pick_avatar)
            # The pencil says the picture can be changed: a disc of its own,
            # outside the circle at the top left, ringed in the page colour so
            # it reads as a separate control rather than a smudge on the
            # picture, and in the colour of the cards so that it shows at all
            # (the hover grey of the first try all but vanished on the page).
            pen = tk.Canvas(bub_host, width=badge, height=badge, bg=BG,
                            highlightthickness=0, bd=0, cursor='hand2')
            pen._ring = _dot_image(badge, BG)
            pen._disc = _dot_image(badge - self.dp(4), BAR_BG)
            pen.create_image(badge / 2, badge / 2, image=pen._ring)
            pen.create_image(badge / 2, badge / 2, image=pen._disc)
            pen.create_text(badge / 2, badge / 2, text=ICON_EDIT, fill=FG,
                            font=('Segoe MDL2 Assets', 8))
            pen.place(x=0, y=0)
            pen.bind('<Button-1>', pick_avatar)
            for w_ in (b, pen):
                self._tooltip(w_, t('dlg_avatar_title'))

        fields = tk.Frame(head, bg=BG)
        fields.pack(side='left', fill='x', expand=True, pady=(half, 0))
        # The name reads as a title that can be edited: no grey band, a line
        # under it that lights up on hover and takes the focus colour.
        name_entry = tk.Entry(fields, font=FT_DLG_H, bg=BG, fg=FG,
                              insertbackground=FG, relief='flat', bd=0,
                              highlightthickness=0)
        name_entry.pack(fill='x', ipady=2)
        name_line = tk.Frame(fields, bg=BAR_BG, height=1)
        name_line.pack(fill='x')
        name_entry.insert(0, acc.get('name') or '')

        def underline(colour, thick):
            name_line.configure(bg=colour, height=thick)

        def hover(on):
            if dlg.focus_get() is not name_entry:
                underline(SOFT_BG_HV if on else BAR_BG, 1)

        name_entry.bind('<Enter>', lambda e: hover(True))
        name_entry.bind('<Leave>', lambda e: hover(False))
        name_entry.bind('<FocusIn>', lambda e: underline(FOCUS_RING, 2))
        name_entry.bind('<FocusOut>', lambda e: underline(BAR_BG, 1))
        name_entry.bind('<KeyRelease>', lambda e: draw_bubble())
        tk.Label(fields, text=acc.get('email') or t('dlg_account_unknown'),
                 font=FT_DLG_HINT, fg=DIM, bg=BG, anchor='w').pack(fill='x', pady=(6, 0))
        draw_bubble()

        # ── what the plan says ──
        prof = acc.get('profile') or {}
        info = tk.Frame(body, bg=BG)
        info.pack(fill='x', pady=(self.dp(14), 4))
        plan = acc.get('plan') or ''
        sub = prof.get('sub_status')
        if sub:
            known = {'active': t('sub_active'), 'canceled': t('sub_canceled'),
                     'cancelled': t('sub_canceled'), 'past_due': t('sub_past_due')}
            plan = ' · '.join(x for x in (plan, known.get(sub, sub)) if x)
        if plan:
            self._info_row(info, t('info_plan'), plan)
        # The windows that do renew, for the account actually being shown.
        if acc.get('id') == self.cfg.get('active_account') and self._last_data:
            for key, label in (('five_hour', t('info_session_reset')),
                               ('seven_day', t('info_week_reset'))):
                when = pretty_date((self._last_data.get(key) or {}).get('resets_at'),
                                   with_time=True)
                if when:
                    self._info_row(info, label, when)
        if prof:
            self._info_row(info, t('info_extra'),
                           t('info_on') if prof.get('extra_usage') else t('info_off'))
        # The organization only when it has a name of its own: for a personal
        # plan it is called "<email>'s Organization", which says nothing new.
        org_name = prof.get('org_name') or ''
        if org_name and (acc.get('email') or '\x00') not in org_name:
            self._info_row(info, t('info_org'), org_name)

        # Packed only when it has something to say, and the window grows to
        # make room for it: a message packed into a window of fixed height
        # used to be drawn below its bottom edge.
        status_lbl = tk.Label(body, text='', font=FT_DLG_HINT, fg=RED, bg=BG,
                              anchor='w', wraplength=inner_w, justify='left')
        _orig_status_config = status_lbl.config

        def status_config(**kw):
            if kw.get('text'):
                # Held still while the message goes in (_hold), and measured
                # again for it every time: a longer message than the last one
                # used to run under the cards below it.
                self._hold(dlg)
            _orig_status_config(**kw)
            if kw.get('text'):
                if not status_lbl.winfo_manager():
                    status_lbl.pack(fill='x', pady=(4, 6), after=info)
                self._place_dialog(dlg, dw, dh_floor=dh)

        status_lbl.config = status_config

        # ── the ways in: one card each, its state in plain words ──
        methods = account_methods(acc)
        cc_st = self._cc_state()
        profile = self._cc_profile_for(acc, cc_st)
        live = (cc_st or {}).get('live') or {}
        live_org = live.get('org')
        # Not by an organization left in a file with no login (2026-09-30):
        # nothing is in use then.
        here = bool(live_org and live_org == acc.get('org_id') and cc_accounts.holds_login(live))
        # Where the numbers really come from. Claude Code comes first when it
        # is on this account, or when there is nothing else; with Claude Code
        # on another account the widget falls back to the key, and the mark
        # has to follow it or it points at a login that is not being read.
        # With Claude Code on another account the widget reads this one with
        # its saved login while that token is good, and only then with the key
        # (fetch_usage): the mark follows what is really read (review of
        # 2026-09-26).
        saved_ok = bool(not here and acc.get('org_id')
                        and cc_accounts.usable_access_token(acc.get('org_id'))[0])
        if methods and methods[0] == AUTH_CC and (here or saved_ok
                                                  or AUTH_KEY not in methods):
            source = AUTH_CC
        elif AUTH_KEY in methods:
            source = AUTH_KEY
        else:
            source = methods[0] if methods else None
        cc_known = (bool(live_org) or cc_accounts.holds_no_login(live) or any(
            p.get('active') and p.get('certainty') == 'certain'
            for p in (cc_st or {}).get('profiles') or []))
        dead = bool(profile and not here and profile.get('verdict') == cc_accounts.BAD)
        # A login nobody has named yet: the file carries no organization after
        # a sign-in, and the server is asked as the page opens. Drawn from the
        # disk alone, the page offered neither Link nor "Save this login" and
        # pointed at a button that never came (review of 2026-09-26, third
        # iteration). It is drawn again only if it is still the page open and
        # the answer names the login: an answer that names nobody does not
        # start another check.
        unidentified = bool(live.get('read') and live.get('access_print') and not live_org)

        def page_busy():
            # Whether something on the page is in the user's hands: a name
            # being typed, a picture or colour chosen, the picker open, a
            # message on screen (Link or a save at work). A redraw threw those
            # away (review of 2026-09-27). None once the page is gone.
            try:
                picker = self._dialogs.get('avatar')
                return (name_entry.get().strip() != (acc.get('name') or '')
                        or st['color'] != account_color(acc)
                        or st['avatar'] != dict(acc.get('avatar') or {})
                        or bool(status_lbl.cget('text'))
                        or bool(picker is not None and picker.winfo_exists()))
            except tk.TclError:
                return None

        if unidentified:
            def identified():
                try:
                    if not dlg.winfo_exists():
                        return
                except tk.TclError:
                    return
                if not ((getattr(self, '_cc_saved', None) or {}).get('live') or {}).get('org'):
                    return
                # Drawn again only if nothing on the page is in the user's
                # hands (page_busy); the next opening shows the login
                # recognised anyway. The Advanced section stays as it was.
                if page_busy() is False:
                    reopen(advanced=st['advanced'])
            self._cc_sync_login(after=identified)

        def card(title, chip, chip_fg, text, actions, is_source, status=None):
            box = tk.Frame(body, bg=BAR_BG)
            box.pack(fill='x', pady=(0, self.dp(8)))
            inner = tk.Frame(box, bg=BAR_BG)
            inner.pack(fill='x', padx=self.dp(14), pady=self.dp(12))
            top = tk.Frame(inner, bg=BAR_BG)
            top.pack(fill='x')
            tk.Label(top, text=title, font=FT_DLG_BTN_B, fg=FG, bg=BAR_BG,
                     anchor='w').pack(side='left')
            if is_source:
                # Which way the numbers on the widget come from, on the card
                # itself instead of in a separate "Read with" line.
                tk.Label(top, text=t('acct_source'), font=FT_DLG_HINT, fg=BLUE,
                         bg=BAR_BG).pack(side='left', padx=(self.dp(8), 0))
            tk.Label(top, text=chip, font=FT_DLG_HINT, fg=chip_fg, bg=BAR_BG,
                     anchor='e').pack(side='right')
            if text:
                tk.Label(inner, text=text, font=FT_DLG_HINT, fg=DIM, bg=BAR_BG,
                         anchor='w', justify='left',
                         wraplength=inner_w - self.dp(28)).pack(fill='x', pady=(6, 0))
            if status:
                # The state of the saved login, as its row in the list says it:
                # a dot of its colour, then the words.
                colour, words, tip = status[:3]
                line = tk.Frame(inner, bg=BAR_BG)
                line.pack(fill='x', pady=(self.dp(6), 0))
                dot = tk.Label(line, image=_dot_image(self.dp(7), colour), bg=BAR_BG)
                dot.pack(side='left', anchor='n',
                         pady=(max(0, (FT_DLG_HINT.metrics('linespace') - self.dp(7)) // 2), 0))
                said = tk.Label(line, text=words, font=FT_DLG_HINT,
                                fg=DIM if colour in (DIM, BLUE, DOT_GREEN) else colour,
                                bg=BAR_BG, anchor='w', justify='left',
                                wraplength=inner_w - self.dp(48))
                said.pack(side='left', fill='x', padx=(self.dp(4), 0))
                tip_now = {'text': tip}
                self._tooltip(said, lambda: tip_now['text'])

                def set_status(colour_, words_, tip_, _more=''):
                    # The same line brought up to date where it is, for an
                    # answer that arrives while the page is in the user's
                    # hands (_cc_views_after_check); held still and measured
                    # again as for every other change on the page: a longer
                    # answer ran the bottom of the page out of the window
                    # (review of 2026-09-30, third iteration).
                    self._hold(dlg)
                    dot.config(image=_dot_image(self.dp(7), colour_))
                    said.config(text=words_,
                                fg=DIM if colour_ in (DIM, BLUE, DOT_GREEN) else colour_)
                    tip_now['text'] = tip_
                    self._place_dialog(dlg, dw, dh_floor=dh)

                dlg._cc_set_status = set_status
            if actions:
                row = tk.Frame(inner, bg=BAR_BG)
                row.pack(fill='x', pady=(self.dp(10), 0))
                for label, cmd in actions:
                    self._small_pill(row, label, cmd).pack(side='left',
                                                           padx=(0, self.dp(6)))

        def unlink():
            if AUTH_KEY not in methods:
                self._confirm_dialog(t('dlg_last_credential_title'),
                                     t('dlg_last_credential'), do_unlink)
                return
            do_unlink()

        def do_unlink():
            acc.pop('cc_linked', None)
            mirror_active(self.cfg)
            save_cfg(self.cfg)
            reopen()

        def save_login():
            self._cc_save_snapshot(self._cc_profile_name_for_account(acc), reopen,
                                   status_lbl, acc_id=acc.get('id'))

        linked = bool(acc.get('cc_linked'))
        cc_actions = []

        def verify_login():
            # The list is drawn again as the check starts and when it ends, and
            # this page too, while it is still this login's and idle
            # (_cc_views_after_check): an answer that took long no longer
            # replaced another account's page or brought back a closed one
            # (review of 2026-09-30).
            self._cc_verify(profile['name'], rebuild)

        def show_check():
            # The page drawn again with the login's new state when nothing on
            # it is in the user's hands; otherwise only the login's own line,
            # where it is. Skipping the page then left a Verify pressed after
            # "Save this login" showing nothing at all, and a page opened
            # during a check on "Checking..." for good (review of 2026-09-30,
            # second iteration).
            try:
                if not dlg.winfo_exists():
                    return
            except tk.TclError:
                return
            busy = page_busy()
            if busy is None:
                return
            if not busy:
                reopen(advanced=st['advanced'])
                return
            setter = getattr(dlg, '_cc_set_status', None)
            fresh = self._cc_state()
            prof = self._cc_profile_for(acc, fresh)
            if setter is None or prof is None:
                return
            lv = (fresh or {}).get('live') or {}
            now_here = bool(lv.get('org') and lv.get('org') == acc.get('org_id')
                            and cc_accounts.holds_login(lv))
            try:
                setter(*self._cc_status(prof, now_here, lv.get('refresh_print')))
            except tk.TclError:
                pass

        dlg._cc_profile_name = profile['name'] if profile else None
        dlg._cc_redraw_if_idle = show_check

        if here:
            chip, chip_fg = '● ' + t('acct_cc_here'), DOT_GREEN
            # "Reads this account through this login" only when it does: the
            # user may have chosen the key (review of 2026-09-26).
            text = (t('acct_cc_reads') if linked and source == AUTH_CC
                    else t('acct_cc_in_use'))
            if not linked:
                cc_actions.append((t('cc_link'), lambda: self._link_claude_code(
                    acc, reopen, status_lbl)))
            if profile is None:
                # The first saved login of an account is normally taken by the
                # widget on its own; this is the way to take it now.
                cc_actions.append((t('acct_save_login'), save_login))
        elif dead:
            chip, chip_fg = t('acct_cc_dead'), RED
            text = t('acct_cc_dead_how')
        elif profile:
            chip, chip_fg = t('acct_cc_saved'), DIM
            if cc_known:
                # With no login in Claude Code's file nothing is in use: not
                # "another account" (review of 2026-09-30, second iteration).
                text = t('acct_cc_no_login' if cc_accounts.holds_no_login(live)
                         else 'acct_cc_elsewhere')
                cc_actions.append((t('acct_switch_here'),
                                   lambda n=profile['name']: close_and(
                                       lambda: self._cc_switch_dialog(n, rebuild))))
            else:
                text = t('acct_cc_unknown')
        elif unidentified:
            chip, chip_fg = t('acct_cc_none'), DIM
            text = t('acct_cc_unknown')
        else:
            chip, chip_fg = t('acct_cc_none'), DIM
            # The login is saved by itself only for someone who uses the
            # switch (a linked account, or saved logins already on disk); to
            # anyone else the page promised what never happened (review of
            # 2026-09-26).
            text = (t('acct_cc_how') if linked or cc_accounts.profile_names()
                    else t('acct_cc_how_manual'))
        if unidentified and not linked:
            # As in 2.9.1, whenever Claude Code has a login here: Link asks
            # the server itself, and refuses another account's login.
            cc_actions.append((t('cc_link'), lambda: self._link_claude_code(
                acc, reopen, status_lbl)))
        cc_status = None
        if profile is not None:
            # Beside Switch: the one way to know the saved login still works.
            cc_actions.insert(1 if cc_actions and not here else 0,
                              (t('cc_ui_verify'), verify_login))
            cc_status = self._cc_status(profile, here, live.get('refresh_print'))
        if linked:
            cc_actions.append((t('cc_unlink'), unlink))
        card(t('auth_claude_code'), chip, chip_fg, text, cc_actions,
             source == AUTH_CC, cc_status)

        def drop_key():
            if not acc.get('cc_linked'):
                self._confirm_dialog(t('dlg_last_credential_title'),
                                     t('dlg_last_credential'), do_drop_key)
                return
            do_drop_key()

        def do_drop_key():
            acc['session_key'] = ''
            mirror_active(self.cfg)
            save_cfg(self.cfg)
            reopen()

        has_key = AUTH_KEY in methods
        key_actions = [(t('dlg_update_key') if has_key else t('key_add'),
                        lambda: close_and(lambda: self._edit_account_key(acc, rebuild)))]
        if has_key:
            key_actions.append((t('key_remove'), drop_key))
        card(t('auth_key'),
             t('key_state_present') if has_key else t('key_state_absent'),
             CLAUDE_TEXT if has_key else DIM,
             '' if has_key else t('acct_key_why'),
             key_actions, source == AUTH_KEY)

        # Limit resets (#14): claude.ai lets only the session key use one,
        # so the card is for an account that has it.
        if has_key and acc.get('org_id'):
            self._reset_card(body, acc, inner_w, dlg, dw, dh)

        # ── advanced: folded away, for the few who need it ──
        adv_head = tk.Label(body, text='', font=FT_DLG_HINT, fg=DIM, bg=BG,
                            anchor='w', cursor='hand2')
        adv_head.pack(fill='x', pady=(self.dp(4), 0))
        adv = tk.Frame(body, bg=BG)

        def set_pref(value):
            acc['auth_pref'] = value
            mirror_active(self.cfg)
            save_cfg(self.cfg)
            self.refresh()
            reopen()

        tk.Label(adv, text=t('acct_read_with'), font=FT_DLG_HINT, fg=DIM, bg=BG,
                 anchor='w').pack(fill='x', pady=(self.dp(8), self.dp(4)))
        current = acc.get('auth_pref', AUTH_AUTO)
        pref_box = tk.Frame(adv, bg=BG)
        pref_box.pack(fill='x')
        self._pill_flow(pref_box, [
            (t('pref_auto'), lambda: set_pref(AUTH_AUTO), current == AUTH_AUTO),
            (t('auth_claude_code'), lambda: set_pref(AUTH_CC), current == AUTH_CC),
            (t('auth_key'), lambda: set_pref(AUTH_KEY), current == AUTH_KEY),
        ], inner_w)
        if profile:
            facts = []
            if profile.get('refresh_expires'):
                facts.append(t('acct_saved_login').format(when=pretty_date(
                    profile['refresh_expires'].isoformat(), True)))
            if profile.get('saved_at'):
                facts.append(t('acct_saved_copy').format(when=pretty_date(
                    datetime.fromtimestamp(profile['saved_at']).isoformat(), True)))
            if facts:
                tk.Label(adv, text=' · '.join(facts), font=FT_DLG_HINT, fg=DIM,
                         bg=BG, anchor='w', justify='left',
                         wraplength=inner_w).pack(fill='x', pady=(self.dp(10), 0))
            if here:
                # Normally kept in step by the widget itself after every
                # renewal; this is the same thing, now.
                refresh_row = tk.Frame(adv, bg=BG)
                refresh_row.pack(fill='x', pady=(self.dp(6), 0))
                self._small_pill(refresh_row, t('acct_refresh_copy'),
                                 lambda n=profile['name']: self._cc_save_snapshot(
                                     n, reopen, status_lbl, acc_id=acc.get('id'))
                                 ).pack(side='left')

        def toggle_advanced(_e=None):
            self._hold(dlg)
            st['advanced'] = not st['advanced']
            adv_head.config(text=('▾ ' if st['advanced'] else '▸ ')
                            + t('acct_advanced'))
            if st['advanced']:
                adv.pack(fill='x', after=adv_head)
            else:
                adv.pack_forget()
            self._place_dialog(dlg, dw, dh_floor=dh)

        adv_head.config(text='▸ ' + t('acct_advanced'))
        adv_head.bind('<Button-1>', toggle_advanced)
        if advanced:
            toggle_advanced()

        # A message left by a gesture that had to reopen this window to show
        # its result: shown once, and only on the page of the account it is
        # about. A message kept for "whichever page opens next" told the user
        # about another account (review of 2026-09-26).
        flash = getattr(self, '_cc_flash', None)
        self._cc_flash = None
        if flash and (len(flash) < 3 or flash[2] == acc.get('id')):
            status_lbl.config(text=flash[0], fg=flash[1])

        if not st['advanced']:
            # The room the advanced options take is kept below the page while
            # they are folded (_place_dialog): opening them unfolds the page
            # downward, and the line just clicked stays under the mouse. The
            # page is still out of sight here, so trying them on shows nothing.
            adv.pack(fill='x', after=adv_head)
            dlg.update_idletasks()
            open_h = dlg.winfo_reqheight()
            adv.pack_forget()
            dlg.update_idletasks()
            dlg._reserve = max(0, open_h - dlg.winfo_reqheight())

        self._place_dialog(dlg, dw, dh_floor=dh)

    def _cc_profile_name_for_account(self, acc):
        """The folder name a widget account's saved login goes under.

        Its own name, as far as a folder can carry it; if a folder of that name
        already holds ANOTHER account's login, a number is added. A folder that
        holds this account's is reused, which is what keeps one saved login per
        account instead of a new one every time.
        """
        # Never from the e-mail: the folder name reaches the log, and an
        # account named in Japanese turned 'taro.yamada@gmail.com' into a
        # folder 'taro.yamada gmail.com' (review of 2026-09-26).
        base = (cc_accounts.profile_name_for(acc.get('name'))
                or 'account')[:36].rstrip()
        taken = {n.casefold(): n for n in cc_accounts.profile_names()}
        cand, i = base, 2
        while cand.casefold() in taken:
            data, failure = cc_accounts.read_profile(taken[cand.casefold()])
            if not failure and data.get('organizationUuid') == acc.get('org_id'):
                return taken[cand.casefold()]
            cand, i = f'{base} {i}', i + 1
        return cand


    # ── Claude Code accounts: the saved OAuth profiles ─────────────────────
    def _cc_state(self):
        """The saved profiles and the live login, from one read of the disk.

        Carries over what the server last said about the live login while it
        is still the same login (see cc_accounts.state), so a redraw does not
        undo the identity the login check has just established.

        Never raises: a store that is not there, or a file somebody has been
        editing, is one of the answers this window has to be able to show.
        """
        known = (getattr(self, '_cc_saved', None) or {}).get('live')
        try:
            return cc_accounts.state(known=known)
        except Exception as e:
            wlog(f'CC     could not read the profiles: {_redact(str(e))}')
            return None

    def _cc_sync_login(self, after=None):
        """Find out whose login is live, and bring its own snapshot in step.

        🔴 Signing in again mints a new renewal token AND leaves the
        organization out of the live file. Until the profile of that same
        account catches up, nothing here recognises the account: no badge, a
        "switch" button on the row of the account already in use, and a switch
        that writes a months-old token over a login made a minute earlier -
        which is what happened on 2026-09-21. It used to be a button the user
        had to know about and press. It happens by itself now.

        Off the main thread: it is one network call, and the window has to
        paint before it. One at a time: a second request made while one is
        running waits for it, and is answered by it.

        It says nothing on screen when it brings a snapshot in step: that is
        housekeeping, it follows every renewal of the login and not only a new
        sign-in, and announcing it on whichever account page opened next told
        the user about a sign-in they had not made (review of 2026-09-26). The
        log keeps it.
        """
        waiting = self.__dict__.setdefault('_cc_sync_after', [])
        if after:
            waiting.append(after)
        if getattr(self, '_cc_sync_busy', False):
            return
        self._cc_sync_busy = True

        def work():
            done, fresh = None, None
            try:
                done = cc_accounts.sync_live_into_profile(call=cc_http)
                fresh = cc_accounts.state(call=cc_http, confirm=True)
            except Exception as e:
                wlog(f'CC     login watch failed: {_redact(str(e))}')
            try:
                first = self._cc_first_snapshot(fresh)
            except (ValueError, OSError) as e:
                first = None
                wlog(f'CC     first saved login not taken: {_redact(str(e))}')
            if first:
                wlog(f'CC     first saved login of this account taken, as {first}')
                try:
                    fresh = cc_accounts.state(known=(fresh or {}).get('live'))
                except Exception as e:
                    wlog(f'CC     could not read the profiles: {_redact(str(e))}')
            if (done or {}).get('updated'):
                wlog(f"CC     saved login of {done['updated']} brought in step "
                     f"with the one Claude Code is using")
            elif (done or {}).get('pending'):
                wlog(f"CC     whose login this is cannot be settled yet "
                     f"({done['pending']}), looking again later")

            def apply():
                self._cc_sync_busy = False
                # Not settled is not forgotten: asked again in five minutes
                # when the server did not answer, at once when the file moved
                # under the read, and at the next rewrite of the file when the
                # short token had expired (Claude Code renews it at its next
                # command, and that rewrite is what brings this back).
                pending = (done or {}).get('pending')
                self._cc_sync_retry_at = (
                    time.time() + 300 if pending == 'unproven'
                    else time.time() if pending == 'changed' else None)
                if fresh is not None:
                    self._cc_saved = fresh
                calls, self._cc_sync_after = self._cc_sync_after, []
                for fn in calls:
                    try:
                        fn()
                    except (tk.TclError, RuntimeError) as e:
                        wlog(f'CC     could not redraw after the login check: '
                             f'{_redact(str(e))}')
            try:
                self.root.after(0, apply)
            except (tk.TclError, RuntimeError):
                # The window, or the interpreter, has gone while we were away.
                pass
        threading.Thread(target=work, daemon=True).start()

    def _cc_first_snapshot(self, fresh):
        """Take the first saved login of a widget account, when Claude Code is
        proven to be signed in as it and it has none yet. Returns the profile
        name written, or None.

        The account page promises it ("the widget saves the login by
        itself"), and without it the switch never appears at all: there is
        nothing to switch onto, and there was no way to make that first copy
        (review of 2026-09-26). Only on the server's word for the tokens in the
        file at this moment, and only for someone who uses the switch: an
        account linked to Claude Code, or saved logins already on disk. Runs
        on the login check's worker thread.
        """
        live = (fresh or {}).get('live') or {}
        org = live.get('org')
        if not org or live.get('org_source') != 'server':
            return None
        if cc_accounts.find_profile_for_org(org, (fresh or {}).get('profiles') or []):
            return None
        acc = find_account_by_org(self.cfg, org)
        if acc is None or not (acc.get('cc_linked') or cc_accounts.profile_names()):
            return None
        data, failure = cc_accounts.read_json(cc_accounts.cred_file())
        if failure or cc_accounts.fingerprint(
                (data.get('claudeAiOauth') or {}).get('refreshToken')) != live.get('refresh_print'):
            return None
        name = self._cc_profile_name_for_account(acc)
        cc_accounts.save_profile(name, data, expect_org=acc.get('org_id'),
                                 proven={'org': org, 'email': live.get('server_email'),
                                         'note': 'server'})
        return name

    def _cc_watch_login(self):
        """Notice a new sign-in the way the disk shows one: the file changed.

        Cheap enough for every refresh tick - one `stat` - and it costs a
        network call only when the file has actually been rewritten, or when
        an earlier look could not settle whose login it is (see
        _cc_sync_login).
        """
        # Whoever has saved logins, linked or not: the saved login of the
        # account in use kept a renewal token Claude Code had rotated away,
        # and a later switch put it live (review of 2026-09-26).
        if not (any(a.get('cc_linked') for a in self.cfg.get('accounts', []))
                or cc_accounts.profile_names()):
            return
        try:
            stamp = os.path.getmtime(cc_accounts.cred_file())
        except OSError:
            return
        retry_at = getattr(self, '_cc_sync_retry_at', None)
        due = retry_at is not None and time.time() >= retry_at
        if stamp == getattr(self, '_cc_cred_stamp', None) and not due:
            return
        self._cc_cred_stamp = stamp
        self._cc_sync_retry_at = None
        # Logged even when nothing comes of it: the next person diagnosing
        # "the widget did not notice my new login" needs to see whether this
        # ever ran, which is the lesson of every silent guard before it.
        wlog('CC     looking again at whose login is live' if due else
             'CC     credentials file changed, checking whose login it is')
        self._cc_sync_login()

    def _cc_plan_alive(self, org):
        """Whether that organization still has a plan. None when unknown.

        🔴 Measured on 2026-09-21 against a real lapsed account: the
        usage endpoint answers 200 with EVERY limit null and `limits` an empty
        list. Nothing in the OAuth credentials shows this - their dates stay in
        the future - so a switch onto such an account succeeded and Claude Code
        answered the next command with "OAuth session expired and could not be
        refreshed". A stale key answers 401 and a fault answers nothing: only
        the empty answer is turned into a NO, everything else stays unknown,
        because refusing a switch on a guess would block work that is fine.
        """
        acc = next((a for a in self.cfg.get('accounts', [])
                    if a.get('org_id') == org and a.get('session_key')), None)
        if acc is None:
            return None
        try:
            data, rotation = _fetch_usage_key(acc)
        except NoActivePlan as e:
            if e.rotation:
                try:
                    self.root.after(0, self._apply_rotated_key, *e.rotation)
                except (tk.TclError, RuntimeError):
                    pass
            # 🔴 The answer this function exists for. _fetch_usage_key turns
            # the lapsed shape into this exception, and the generic handler
            # below used to swallow it as "could not read": the guard on the
            # switch could never say no (review of 2026-09-26).
            return False
        except Exception as e:
            wlog(f'CC     could not read the plan: {_redact(str(e))}')
            return None
        if rotation:
            # Claude.ai rotated the key on this read: keep the new one, on the
            # main thread, on the account it was issued for.
            try:
                self.root.after(0, self._apply_rotated_key, *rotation)
            except (tk.TclError, RuntimeError):
                pass
        return None if not isinstance(data, dict) else not no_active_plan(data)

    def _cc_profile_for(self, acc, st=None):
        """The saved profile of a widget account, matched by ORGANIZATION.

        Not by name: names are chosen by hand on two sides that do not know
        about each other, and a map of names ages on its own.
        """
        if st is None:
            st = self._cc_state()
        if not st or not acc.get('org_id'):
            return None
        return cc_accounts.find_profile_for_org(acc['org_id'], st['profiles'])

    def _cc_verdict_text(self, profile):
        """The one-word verdict, and the sentence under it."""
        words = {cc_accounts.GOOD: t('cc_ui_verdict_good'),
                 cc_accounts.BAD: t('cc_ui_verdict_bad'),
                 cc_accounts.UNKNOWN: t('cc_ui_verdict_unknown')}
        colours = {cc_accounts.GOOD: DOT_GREEN, cc_accounts.BAD: RED,
                   cc_accounts.UNKNOWN: ORANGE}
        worst = profile.get('verdict', cc_accounts.UNKNOWN)
        # The judgement worth reading is the one that decided the verdict; a
        # warning beside a good verdict still deserves its line.
        speak = [j for j in profile.get('judgements', [])
                 if j['kind'] == worst] or profile.get('judgements', [])
        return words.get(worst, ''), colours.get(worst, DIM), speak[0] if speak else None

    def _cc_account_label(self, profile_name):
        """What to call a saved profile on screen: the widget's own name for
        its account when there is one, the folder name otherwise. A profile
        parked as `auto-20260921-121440` is an account the user knows by name."""
        try:
            data, failure = cc_accounts.read_profile(profile_name)
            org = None if failure else data.get('organizationUuid')
        except Exception:
            org = None
        acc = find_account_by_org(self.cfg, org) if org else None
        return (acc or {}).get('name') or profile_name

    def _cc_switch_dialog(self, name, rebuild=None):
        """Move Claude Code onto a saved profile, saying what is happening.

        The order is the one the whole design turns on: check first, refuse a
        profile that cannot work BEFORE anything is written, ask about one that
        cannot be checked instead of refusing it, and only then swap. The
        button goes quiet while it works, because a window that sits still for
        four seconds reads as "nothing happened".

        A switch that has started is not cancelled: there is no safe halfway
        point in a write of the credentials, so while it runs the window
        offers no Cancel and ignores its X and Escape. And the result reaches
        the widget even if the window is gone by then: the display follows
        the account that became signed in, and the list is redrawn (review of
        2026-09-26: Cancel used to close the window while the switch went on,
        and the result was dropped).
        """
        if getattr(self, '_cc_switch_busy', False):
            running = self._dialogs.get('cc_switch')
            try:
                if running is not None and running.winfo_exists():
                    running.lift()
            except tk.TclError:
                pass
            return
        label = self._cc_account_label(name)
        dw, dh = self._dlg_size(470, 250)
        dlg, body = self._build_dialog_frame(
            t('cc_ui_switch_to').format(name=label), dw, dh, key='cc_switch')
        st = {'busy': False, 'knowingly': False, 'done': False}
        dlg.hold_open = lambda: st['busy']

        bottom = tk.Frame(body, bg=BG)
        bottom.pack(fill='x', side='bottom')
        tk.Frame(bottom, bg=BAR_BG, height=1).pack(fill='x', pady=(14, 12))
        buttons = tk.Frame(bottom, bg=BG)
        buttons.pack(fill='x')

        head = tk.Label(body, text=t('cc_ui_checking'), font=FT_DLG_BTN_B,
                        fg=FG, bg=BG, anchor='w', justify='left',
                        wraplength=dw - self.dp(48))
        head.pack(fill='x')
        detail = tk.Label(body, text='', font=FT_DLG_HINT, fg=DIM, bg=BG,
                          anchor='w', justify='left',
                          wraplength=dw - self.dp(48))
        detail.pack(fill='x', pady=(8, 0))
        extra = tk.Label(body, text='', font=FT_DLG_HINT, fg=DIM, bg=BG,
                         anchor='w', justify='left',
                         wraplength=dw - self.dp(48))

        def show(title, title_fg, note='', more=''):
            if not dlg.winfo_exists():
                return
            # Held still (_hold) until this and the buttons that follow it
            # are in place.
            self._hold(dlg)
            head.config(text=title, fg=title_fg)
            detail.config(text=note)
            if more:
                extra.config(text=more)
                extra.pack(fill='x', pady=(10, 0))
            else:
                extra.pack_forget()
            self._place_dialog(dlg, dw, dh_floor=dh)

        def on_destroy(e):
            # Every way of closing redraws the list once: the X, Escape, Close,
            # or another window of this kind taking its place. While a switch
            # runs the redraw waits for its result instead.
            if e.widget is dlg and not st['busy'] and rebuild:
                rebuild()

        dlg.bind('<Destroy>', on_destroy, add='+')

        def close():
            try:
                dlg.destroy()
            except tk.TclError:
                pass

        def render_buttons(primary_label=None, primary_cmd=None, closing=False):
            self._hold(dlg)
            for w in buttons.winfo_children():
                w.destroy()
            if st['busy']:
                return
            # The main action on the right edge and Cancel beside it; alone,
            # Close on the right edge too, as in every other window (review of
            # 2026-09-26).
            if primary_label:
                self._primary_pill(buttons, primary_label, primary_cmd).pack(side='right')
            self._secondary_pill(
                buttons, t('cc_ui_close') if closing else t('dlg_cancel'),
                close).pack(side='right', padx=(0, 8) if primary_label else 0)

        def work():
            try:
                result = cc_accounts.switch(
                    name, knowingly=st['knowingly'], call=cc_http, post=cc_post,
                    plan_check=self._cc_plan_alive,
                    name_for_org=lambda org: (find_account_by_org(self.cfg, org)
                                              or {}).get('name'))
            except Exception as e:
                wlog(f'CC     switch raised: {_redact(str(e))}')
                result = {'done': False, 'asks': False,
                          'judgement': cc_accounts.verdict(
                              cc_accounts.BAD, 'cc_j_file_unreadable',
                              {'path': cc_accounts.cred_file(), 'why': str(e)},
                              'cc_r_try_again')}
            try:
                self.root.after(0, lambda: finish(result))
            except (tk.TclError, RuntimeError) as e:
                # The window or the interpreter has gone while we were away.
                # Logged rather than passed over in silence: a hop back to the
                # interface that is dropped leaves a dialog frozen on
                # "checking", and without this line there is nothing to find.
                wlog(f'CC     could not report back: {_redact(str(e))}')

        def finish(result):
            st['busy'] = False
            self._cc_switch_busy = False
            done = bool(result.get('done'))
            # The widget follows the account that is now signed in whether or
            # not anybody is still looking at this window.
            moved = self._cc_adopt_display(name) if done else None
            if done:
                wlog(f"CC     switched Claude Code to {name}"
                     + (f" (outgoing saved as {result.get('saved_out')})"
                        if result.get('saved_out') else ''))
            if not dlg.winfo_exists():
                if rebuild:
                    rebuild()
                return
            judgement = dict(result.get('judgement') or {})
            if (judgement.get('data') or {}).get('name') == name:
                # Named on screen as the widget names the account.
                judgement['data'] = dict(judgement['data'], name=label)
            note = cc_say(judgement)
            remedy = cc_say(judgement, 'remedy')

            if done:
                st['done'] = True
                lines = []
                if moved:
                    lines.append(t('cc_ui_now_widget').format(
                        name=moved.get('name') or label))
                # Said only when it is true: the check is made when the
                # profile still carries a short token that has not expired.
                # A check that could not be made is not news to the user.
                verified = (result.get('checked') or {}).get('judgement') or {}
                if verified.get('kind') == cc_accounts.GOOD:
                    lines.append(cc_say(verified))
                show(note or t('cc_j_switched').format(name=label), DOT_GREEN,
                     '\n'.join(lines), t('cc_r_applies_at_once'))
                render_buttons(closing=True)
                return

            if result.get('asks'):
                # Not refused: it could not be checked, which is a different
                # thing, and the user is the one who decides. What deciding
                # means is said: a login written unchecked may be dead.
                checked = result.get('checked') or {}
                if checked and not checked.get('renewed'):
                    remedy = '\n'.join(x for x in (remedy, t('cc_ui_unverified_risk')) if x)
                show(note, ORANGE, remedy)

                def proceed():
                    st['knowingly'] = True
                    start()

                render_buttons(t('cc_ui_proceed'), proceed)
                return

            # A refusal is red; "not now" (the login cannot be identified, the
            # file was moving) is not a fault, and is not painted as one.
            bad = judgement.get('kind') == cc_accounts.BAD
            show(note, RED if bad else ORANGE, remedy)
            render_buttons(closing=True)

        def start():
            st['busy'] = True
            self._cc_switch_busy = True
            # The saved login is renewed first (cc_accounts.switch), and that
            # is the step that takes time.
            show(t('cc_ui_switching'), FG, t('cc_ui_renewing'))
            render_buttons()
            threading.Thread(target=work, daemon=True).start()

        start()
        self._place_dialog(dlg, dw, dh_floor=dh)

    def _cc_adopt_display(self, profile_name):
        """Show the account that has just become the signed-in one.

        Whoever changes the account in Claude Code almost always wants to watch
        that account's usage next. It is done openly and named in the panel,
        because the silent version of this is exactly the confusion issue #11
        was filed about.
        """
        try:
            data, failure = cc_accounts.read_profile(profile_name)
            org = None if failure else data.get('organizationUuid')
        except Exception:
            org = None
        acc = find_account_by_org(self.cfg, org) if org else None
        if acc and acc.get('id') != self.cfg.get('active_account'):
            self._switch_account(acc.get('id'))
        elif acc:
            # Already on show: nothing read it again, and an error saying
            # "switch Claude Code back to it" stayed up for a whole interval
            # after the switch had done exactly that (review of 2026-09-26).
            self._clear_error()
            self.refresh()
        return acc

    def _cc_save_snapshot(self, name, on_done=None, status=None, acc_id=None):
        """Save the login signed in now, under `name`.

        It has to SAY it worked. The first version reopened the page and left
        it at that: the login really was saved, the file on disk really did
        change, and the user reported that nothing was being saved, because
        the only visible difference was a date and sixteen hex digits nobody
        compares. A gesture with no answer reads as a gesture that failed.

        The answer goes to the page that asked, and only while it is open: a
        message kept for "whichever page opens next" described one account on
        another account's page (review of 2026-09-26).
        """
        acc = next((a for a in self.cfg.get('accounts', [])
                    if acc_id and a.get('id') == acc_id), None)
        # The account the page is about: a page opened before Claude Code
        # moved to another account saved that account's login under this
        # one's name (review of 2026-09-26).
        expect = (acc or {}).get('org_id') or None

        def work():
            try:
                prof = cc_accounts.save_profile(name, call=cc_http, expect_org=expect)
                err, when = None, prof.get('savedAt')
            except Exception as e:
                why = str(e)
                # The module refuses with a message key, said through cc_say so
                # that a sentence needing a value gets one ('{path}' was shown
                # as it is); anything else is an error from the system, shown
                # with no secret in it.
                err = (cc_say({'key': why, 'data': {'path': cc_accounts.cred_file(),
                                                    'why': ''}})
                       if why.startswith('cc_') else _redact(why))
                when = None

            def apply():
                if status is not None and not status.winfo_exists():
                    # The page was closed meanwhile: nothing to reopen, and no
                    # message left behind for some other page to show.
                    wlog(f'CC     saving the login as {name}: '
                         f'{"refused" if err else "done"}, the page was closed')
                    return
                if err:
                    if status is not None:
                        status.config(text=err, fg=RED)
                    return
                # Carried across the reopen: the label showing it is about to
                # be destroyed with the window it lives in.
                self._cc_flash = (t('cc_ui_snapshot_saved').format(
                    when=pretty_date(when, True) or ''), DOT_GREEN, acc_id)
                if on_done:
                    on_done()
            try:
                self.root.after(0, apply)
            except (tk.TclError, RuntimeError) as e:
                wlog(f'CC     could not report the snapshot: {_redact(str(e))}')

        if status is not None:
            status.config(text=t('dlg_verifying'), fg=BLUE)
        threading.Thread(target=work, daemon=True).start()

    def _cc_verify(self, name, done=None):
        """Check a saved login with the server, off the main thread
        (cc_accounts.verify): renewed and kept renewed, or, for the login in
        use, asked about with its short token. `done` redraws the list (or
        whatever started it), once now (for "Checking...") and once with the
        answer; an open account page showing this login is drawn again too,
        when nothing on it is in the user's hands (_cc_views_after_check).

        A switch at work holds the renewal lock, and the check waits for it
        (cc_accounts.renewal_lock): dropped while a switch ran, it answered
        nothing at all (review of 2026-09-30).
        """
        busy = self.__dict__.setdefault('_cc_verifying', set())
        if name in busy:
            return
        busy.add(name)

        def refresh():
            if done:
                try:
                    done()
                except tk.TclError:
                    pass
            self._cc_views_after_check(name)

        def work():
            try:
                res = cc_accounts.verify(name, call=cc_http, post=cc_post)
            except Exception as e:
                wlog(f'CC     verify raised: {_redact(str(e))}')
                res = {'judgement': cc_accounts.verdict(
                    cc_accounts.UNKNOWN, 'cc_j_no_answer', {}, 'cc_r_check_connection')}
            # What the answer is about: the token verify looked at, under the
            # renewal lock, the saved login's or, for a check of the login in
            # use, the live one's. Once that changes (a sign-in, a switch,
            # Claude Code renewing), the answer says nothing any more
            # (_cc_status), as the verdict kept on disk (last_check). Keyed by
            # name alone, "refused, sign in again" outlived the sign-in it
            # asked for; read after the check, an answer that had waited for a
            # switch was pinned to the token the switch left (review of
            # 2026-09-30, both iterations).
            if res.get('in_use'):
                about = (None, res.get('about_live') or '')
            elif res.get('about') is not None:
                # "Cannot tell" and "the live login is refused" are about the
                # live login too: they stop meaning anything once it changes
                # (a sign-in, as their remedy asks).
                about = (res['about'], res.get('about_live'))
            else:
                # Nothing looked at (another switch or check held the lock):
                # about the saved login as it stands.
                try:
                    data, _failure = cc_accounts.read_profile(name)
                    about = (cc_accounts.fingerprint(((data or {}).get('claudeAiOauth') or {})
                                                     .get('refreshToken')), None)
                except Exception:
                    about = ('', None)      # about nothing known: never shown

            def apply():
                busy.discard(name)
                j = res.get('judgement') or {}
                wlog(f"CC     checked the saved login {name}: {j.get('kind')} ({j.get('key')})")
                self.__dict__.setdefault('_cc_verify_results', {})[name] = (
                    j, datetime.now()) + about
                refresh()
            try:
                self.root.after(0, apply)
            except (tk.TclError, RuntimeError) as e:
                wlog(f'CC     could not report the check: {_redact(str(e))}')

        threading.Thread(target=work, daemon=True).start()
        refresh()

    def _cc_views_after_check(self, name):
        """Draw again the account page showing this saved login, if it is open,
        and only through the page's own guard (_cc_redraw_if_idle): a name
        being typed, a colour chosen, a message on screen stay. The list is
        its caller's (`done`)."""
        page = self._dialogs.get('account_details')
        try:
            if page is None or not page.winfo_exists():
                return
        except tk.TclError:
            return
        if getattr(page, '_cc_profile_name', None) != name:
            return
        redraw = getattr(page, '_cc_redraw_if_idle', None)
        if redraw is not None:
            redraw()

    def _cc_status(self, profile, signed_in_here, live_print=None):
        """(colour, text, tooltip, more) of an account's Claude Code login,
        for its row and its page: in use here, saved and good until, verified
        when, refused, or not usable and why. `more` is what the tooltip of a
        line cut to fit adds after the whole line: the remedy, when the line
        does not already say it. Joined to the whole tooltip instead, the
        reason came twice (review of 2026-09-30, second and third iterations).
        `live_print`: the fingerprint of the renewal token in Claude Code's
        file now."""
        name = (profile or {}).get('name')
        if name and name in getattr(self, '_cc_verifying', set()):
            return BLUE, t('cc_ui_verifying'), '', ''
        check = (profile or {}).get('check')
        until = (profile or {}).get('refresh_expires')
        until_txt = cc_day(until)
        said = getattr(self, '_cc_verify_results', {}).get(name) if name else None
        # An answer of this session is about the tokens it was given for (see
        # _cc_verify): once the saved login, or for a check of the login in
        # use the live one, holds another, it says nothing any more.
        if said and ((said[2] is not None and said[2] != (profile or {}).get('refresh_print'))
                     or (said[3] is not None and said[3] != live_print)):
            said = None
        # A good answer about a saved login is kept in the login itself, with
        # its time: only the others need remembering here.
        session = None if said and said[0].get('kind') == cc_accounts.GOOD else said
        tip = ''
        if session:
            tip = '\n'.join(x for x in (cc_say(session[0]), cc_say(session[0], 'remedy')) if x)
        if signed_in_here:
            # The latest word on the login in use, whatever it was: the check
            # kept in its saved login, or the one made in this session, which
            # is not kept there when the saved login lags behind the live one
            # (cc_accounts.verify checks the live one all the same).
            # A refusal of our own rule keeps its own remedy, and is then said
            # as what it is, not "refused by the server" (review of 2.11,
            # first iteration).
            remedy = ''
            if check and check['kind'] == cc_accounts.BAD:
                remedy = ('cc_r_resave_while_active'
                          if check.get('key') in cc_accounts.OWN_REFUSALS
                          else 'cc_r_login_again')
            words = [(check['at'], check['kind'],
                      cc_accounts.verdict(check['kind'], check.get('key') or '', {'name': name},
                                          remedy))
                     ] if check else []
            if said:
                words.append((said[1], said[0].get('kind'), said[0]))
            if not words:
                return BLUE, t('cc_ui_st_in_use'), '', ''
            at, kind, j = max(words, key=lambda x: x[0])
            tip = '\n'.join(x for x in (cc_say(j), cc_say(j, 'remedy')) if x)
            if kind == cc_accounts.GOOD:
                return BLUE, t('cc_ui_st_in_use_ok').format(when=cc_when(at)), '', ''
            if kind == cc_accounts.BAD:
                if (j or {}).get('remedy') == 'cc_r_login_again':
                    # A refusal: the line itself says "sign in again".
                    return RED, t('cc_ui_st_refused').format(when=cc_when(at)), tip, ''
                # Not a refusal (another account's credentials, an earlier
                # token of this sign-in): said as what it is, with its own
                # remedy (review of 2026-09-30, fourth iteration).
                return (RED, t('cc_ui_st_bad').format(why=cc_say(j)), tip,
                        cc_say(j, 'remedy'))
            return (ORANGE, t('cc_ui_st_unverified').format(when=cc_when(at), why=cc_say(j)),
                    tip, cc_say(j, 'remedy'))
        if (profile.get('active') and said and said[0].get('key') == 'cc_j_verified_in_use'
                and (not check or said[1] > check['at'])):
            # Checked as the login in use, on the server's word, where the
            # disk alone could only say "likely" (right after a sign-in).
            return BLUE, t('cc_ui_st_in_use_ok').format(when=cc_when(said[1])), '', ''
        if profile.get('verdict') == cc_accounts.BAD:
            # A refusal kept by our own rule (an earlier token of the login in
            # use) is not the server's: it falls through to its own words.
            if (check and check['kind'] == cc_accounts.BAD
                    and check.get('key') not in cc_accounts.OWN_REFUSALS):
                j = next((x for x in profile.get('judgements') or []
                          if x.get('key') == 'cc_j_renew_refused_before'), None)
                return RED, t('cc_ui_st_refused').format(when=cc_when(check['at'])), \
                    '\n'.join(x for x in (cc_say(j), cc_say(j, 'remedy')) if x) if j else '', ''
            bad = next((x for x in profile.get('judgements') or []
                        if x.get('kind') == cc_accounts.BAD), None)
            return RED, t('cc_ui_st_bad').format(why=cc_say(bad) if bad else ''), \
                '\n'.join(x for x in (cc_say(bad), cc_say(bad, 'remedy')) if x) if bad else '', \
                cc_say(bad, 'remedy') if bad else ''
        if session and (not check or session[1] > check['at']):
            return ORANGE, t('cc_ui_st_unverified').format(
                when=cc_when(session[1]), why=cc_say(session[0])), tip, \
                cc_say(session[0], 'remedy')
        if check and check['kind'] == cc_accounts.GOOD:
            if not until:
                # A login saved without its date: nothing to tell, rather than
                # "good until ?".
                return DOT_GREEN, t('cc_ui_st_verified_no_date').format(
                    when=cc_when(check['at'])), tip, ''
            return DOT_GREEN, t('cc_ui_st_verified').format(
                when=cc_when(check['at']), until=until_txt), tip, ''
        if not until:
            return DIM, t('cc_ui_st_saved_no_date'), tip, ''
        return DIM, t('cc_ui_st_saved').format(until=until_txt), tip, ''

    def _account_row(self, parent, acc, rebuild, cc_state=None, width=None):
        """One account in the list.

        Two different things can be true of a row and they are shown apart: the
        account the widget is displaying, and the account Claude Code is signed
        in as on this computer right now. They are often not the same, and
        reading one as the other is exactly the confusion issue #11 was about.
        """
        active = acc.get('id') == self.cfg.get('active_account')
        base = BG
        accent = account_color(acc)
        row = tk.Frame(parent, bg=base, cursor='hand2')
        row.pack(fill='x', pady=1)
        cells = [row]

        stripe = tk.Frame(row, bg=(accent if active else base), width=3)
        stripe.pack(side='left', fill='y')

        bubble = self._account_bubble(row, acc)
        bubble.pack(side='left', padx=(8, 10), pady=8)
        cells.append(bubble)

        # One clear control instead of three small icons: everything about the
        # account lives on its own page now.
        # Controls carry their own command; cells carry the row's. Keeping the
        # two lists apart is what stops a button from being quietly rebound to
        # something else.
        controls = []
        btn = self._secondary_pill(row, t('dlg_account_manage'),
                                   lambda: self._account_details_dialog(acc, rebuild))
        btn.pack(side='right', padx=(6, 8))
        controls.append(btn)

        # Beside it, the one gesture worth reaching without opening the page:
        # move Claude Code onto this account. It appears only when there is
        # something to move onto, and not on the account already signed in,
        # where it would do nothing.
        st_cc = cc_state or getattr(self, '_cc_saved', None) or {}
        profile = self._cc_profile_for(acc, st_cc or None)
        # 🔴 One decision, two things drawn from it: this button and the
        # badge below. The module elects ONE profile as the account in use;
        # when each profile answered for itself, two of them could say yes at
        # once and the badge went to whichever name sorted first - and right
        # after a sign-in none of them said yes, so every row offered a switch
        # onto the account the user was already on.
        # The badge says a fact, so it needs a proof: a guess from the identity
        # cache, which lags behind a sign-in, is not one (the account page
        # already refused it; the list did not, and put the badge on the
        # account the user had just left).
        live_cc = st_cc.get('live') or {}
        if profile is not None:
            signed_in_here = bool(profile.get('active')
                                  and profile.get('certainty') == 'certain')
        else:
            # No saved login to carry the mark: the server's word for the live
            # login is proof enough. 2.9.1 showed the badge from it, and the
            # account page says "in use here" from the same reading (review of
            # 2026-09-26).
            signed_in_here = bool(live_cc.get('org_source') == 'server'
                                  and live_cc.get('org') == acc.get('org_id'))
        # And a switch is offered only when it is known whose login is live.
        # When nobody can say, the module refuses the switch anyway, and a
        # button that can only answer "not now" is a button that should not
        # be there.
        # With no login in Claude Code's file at all, nothing is in use and
        # a switch has nothing to protect (cc_accounts.switch): it is offered.
        cc_known = (bool(live_cc.get('org')) or cc_accounts.holds_no_login(live_cc) or any(
            p.get('active') and p.get('certainty') == 'certain'
            for p in st_cc.get('profiles') or []))
        # Nor onto a saved login that is dead: the switch could only refuse it,
        # while the account page says "sign in again" (review of 2026-09-26).
        if (profile and not profile.get('active') and cc_known
                and profile.get('verdict') != cc_accounts.BAD):
            switch_btn = self._secondary_pill(
                row, t('cc_ui_switch'),
                lambda n=profile['name']: self._cc_switch_dialog(n, rebuild))
            switch_btn.pack(side='right', padx=(0, 0))
            # 🔴 NOT added to `cells`. Every cell gets the row's own click
            # binding below, and binding <Button-1> on a pill REPLACES the
            # handler the pill was built with: the button then silently did
            # the row's job instead of its own, which from outside looks like
            # a button that does nothing at all.
            controls.append(switch_btn)
            word, colour, judgement = self._cc_verdict_text(profile)
            self._tooltip(switch_btn, '\n'.join(
                x for x in (t('cc_ui_switch_to').format(
                                name=acc.get('name') or profile['name']),
                            cc_say(judgement) if judgement else '') if x))

        txt = tk.Frame(row, bg=base)
        txt.pack(side='left', fill='x', expand=True)
        cells.append(txt)

        head = tk.Frame(txt, bg=base)
        head.pack(fill='x')
        cells.append(head)
        # What is left once the stripe, the bubble and the buttons on the
        # right have taken theirs. Measured from the widgets themselves, so a
        # translation that makes a button wider narrows the text instead of
        # pushing the row out of the window.
        room = (width or self.dp(540)) - self.dp(58)
        for c in controls:
            room -= c.winfo_reqwidth() + self.dp(8)
        room = max(self.dp(80), room)

        name_lbl = tk.Label(head, text=elide(acc.get('name') or '-', FT_DLG_BTN_B,
                                             room - self.dp(96)),
                            font=FT_DLG_BTN_B, fg=FG, bg=base, anchor='w')
        name_lbl.pack(side='left')
        cells.append(name_lbl)

        def badge(text_, color):
            b = tk.Label(head, text=text_, font=FT_DLG_HINT, fg=color, bg=base,
                         padx=6)
            b.pack(side='left', padx=(6, 0))
            cells.append(b)

        if active:
            badge(t('dlg_active'), accent)
        # Whose login Claude Code has here is said on the row's own line for
        # its login (below), with the state of that login, instead of by a
        # second badge beside the name.

        methods = account_methods(acc)
        how = (t('auth_claude_code') if methods and methods[0] == AUTH_CC
               else t('auth_key') if methods else t('dlg_account_no_method'))
        sub_txt = ' \u00b7 '.join([x for x in (acc.get('email'), acc.get('plan'), how) if x])
        shown = elide(sub_txt, FT_DLG_HINT, room)
        sub_lbl = tk.Label(txt, text=shown, font=FT_DLG_HINT, fg=DIM,
                           bg=base, anchor='w')
        sub_lbl.pack(fill='x')
        if shown != sub_txt:
            self._tooltip(sub_lbl, sub_txt)
        cells.append(sub_lbl)

        # The Claude Code login of this account, on a line of its own: whose
        # it is on this computer, whether the server still accepts the saved
        # one and since when, and the Verify beside the fact it verifies. The
        # row had room for two buttons on the right, not three (the user,
        # 2026-09-30: "the Verify button would not fit, review the UI").
        st_line = None
        st_cut = {'on': False}
        if profile is not None or signed_in_here:
            colour, st_text, st_tip, st_more = self._cc_status(
                profile, signed_in_here, live_cc.get('refresh_print'))
            st_line = tk.Frame(txt, bg=base)
            st_line.pack(fill='x', pady=(self.dp(2), 0))
            cells.append(st_line)
            # Drawn, not a glyph: the Japanese font's circle is twice the
            # Latin one's.
            dot = tk.Label(st_line, image=_dot_image(self.dp(7), colour), bg=base)
            dot.pack(side='left')
            cells.append(dot)
            st_lbl = tk.Label(st_line, text=st_text, font=FT_DLG_HINT,
                              fg=DIM if colour in (DIM, BLUE, DOT_GREEN) else colour,
                              bg=base, anchor='w')
            st_lbl.pack(side='left', padx=(self.dp(4), 0))
            cells.append(st_lbl)
            # One tooltip: the words the row had to cut (fit), then why, if
            # there is a why. Cut with no tooltip, the time of a check or the
            # date a login is good until could not be read at all (review of
            # 2026-09-30).
            def st_tip_now():
                if not st_cut['on']:
                    return st_tip
                # The whole line, then only what it does not already say (the
                # remedy, _cc_status): the reason twice read as a stutter
                # (review of 2026-09-30, second and third iterations).
                return '\n'.join(x for x in (st_text, st_more) if x)

            self._tooltip(st_lbl, st_tip_now)
            if profile is not None:
                # A switch at work does not grey it: the check waits for the
                # switch (_cc_verify).
                busy = profile['name'] in getattr(self, '_cc_verifying', set())
                link = tk.Label(st_line, text=t('cc_ui_verify'), font=FT_DLG_HINT,
                                fg=DIM if busy else BLUE, bg=base,
                                cursor='arrow' if busy else 'hand2')
                link.pack(side='left', padx=(self.dp(10), 0))
                if not busy:
                    link.bind('<Button-1>', lambda e, n=profile['name']:
                              self._cc_verify(n, rebuild))
                    link.bind('<Enter>', lambda e: link.config(
                        font=FT_DLG_HINT_LINK))
                    link.bind('<Leave>', lambda e: link.config(font=FT_DLG_HINT))
                    self._tooltip(link, t('cc_ui_verify_tip'))
                # A control, not a cell: the row's click would replace its own.
                controls.append(link)
            st_full = st_text

        def switch(_e=None):
            self._switch_account(acc.get('id'), rebuild)

        for c in cells:
            if c not in controls:
                c.bind('<Button-1>', switch)

        # The estimate above is only a first guess, made before anything is
        # laid out: at 100% it gave the text about 35 pixels that did not
        # exist, the name ran under Switch and nothing was cut, so no tooltip
        # either (review of 2026-09-26). Once the row is laid out, the text is
        # cut to the width its column really got, badges measured, not guessed.
        full_name = acc.get('name') or '-'
        badges = [b for b in head.winfo_children() if b is not name_lbl]
        tip_state = {'on': shown != sub_txt, 'name': False}

        def inner(lbl):
            # What a label adds around its text: padding and border, both sides.
            try:
                return 2 * sum(int(float(lbl.cget(o)))
                               for o in ('padx', 'bd', 'highlightthickness'))
            except (tk.TclError, ValueError):
                return self.dp(6)

        def fit(_e=None):
            avail = txt.winfo_width()
            if avail <= 1:
                return
            taken = sum(b.winfo_reqwidth() + self.dp(6) for b in badges)
            # The text is cut to what is left once the label's own padding and
            # border are paid for: cut to the whole column, the last letters
            # and the ellipsis went under the edge by those few pixels.
            name_now = elide(full_name, FT_DLG_BTN_B,
                             max(self.dp(40), avail - taken - inner(name_lbl)))
            name_lbl.config(text=name_now)
            if name_now != full_name and not tip_state['name']:
                self._tooltip(name_lbl, full_name)
                tip_state['name'] = True
            now = elide(sub_txt, FT_DLG_HINT, avail - inner(sub_lbl))
            sub_lbl.config(text=now)
            if now != sub_txt and not tip_state['on']:
                self._tooltip(sub_lbl, sub_txt)
                tip_state['on'] = True
            if st_line is not None:
                # What the dot and the Verify leave to the words between them.
                others = sum(c.winfo_reqwidth() + self.dp(10)
                             for c in st_line.winfo_children() if c is not st_lbl)
                now = elide(st_full, FT_DLG_HINT,
                            max(self.dp(40), avail - others - inner(st_lbl)))
                st_lbl.config(text=now)
                st_cut['on'] = now != st_full

        txt.bind('<Configure>', fit, add='+')


    def _accounts_dialog(self):
        # Wider than it was: each row carries two controls, and in Italian and
        # Japanese the labels beside them need the room. The rows cut their own
        # text to whatever is left, so this is comfort, not correctness.
        dw, dh = self._dlg_size(580, 150)
        # dh is just a floor; the dialog is resized to fit the list on every
        # rebuild (fit) so the height grows with the account count and the Add
        # button below the list stays visible without a cap or scrolling.
        dlg, body = self._build_dialog_frame(t('dlg_accounts_title'), dw, dh, key='accounts')

        # Bottom controls first (see the layout contract in _build_dialog_frame):
        # a long account list must eat into the list, never into these.
        add_host = tk.Frame(body, bg=BG)
        add_host.pack(fill='x', side='bottom')
        tk.Frame(add_host, bg=BAR_BG, height=1).pack(fill='x', pady=(12, 12))
        buttons = tk.Frame(add_host, bg=BG)
        buttons.pack(fill='x')
        self._secondary_pill(buttons, t('dlg_add_account'),
                             lambda: self._add_account(rebuild)).pack(side='left')
        def open_usage_page():
            # Close first: this dialog is topmost and frameless, so it would
            # otherwise float over the page the user just asked to read, with
            # no taskbar button to send it behind.
            dlg.destroy()
            self._open_claude_usage()

        # Bare U+2197, no text-presentation selector: the icon is drawn in
        # Segoe UI, which has no emoji form to suppress, and Tk would measure
        # the selector as an extra blank advance that pushes the arrow away
        # from its caption.
        self._outline_pill(buttons, t('menu_open_claude'),
                           open_usage_page, icon='↗').pack(side='right')

        # Which account Claude Code is signed in as on this machine. It is a
        # fact about the computer, not about the widget, so it sits above the
        # list rather than on any single row.
        cc_lbl = tk.Label(body, text='', font=FT_DLG_HINT, fg=DIM, bg=BG,
                          anchor='w', justify='left', wraplength=dw - 2 * DLG_PAD_X)
        cc_lbl.pack(fill='x', pady=(0, 10))
        # On hover, why no row carries the "in use here" mark when none does:
        # the module works it out on every read, and it used to be worked out
        # for nobody (review of 2026-09-26).
        cc_tip = {'text': ''}
        self._tooltip(cc_lbl, lambda: cc_tip['text'])

        list_frame = tk.Frame(body, bg=BG)
        list_frame.pack(fill='x')

        def rebuild():
            # An account page outlives the list it was opened from when the
            # list is opened again from the menu, and calls back into it on
            # save: by then this list is gone.
            if not list_frame.winfo_exists():
                return
            # Held still while its rows are destroyed and built again (_hold):
            # in view, they came back one by one.
            self._hold(dlg)
            # One read of the profiles for the whole list, not one per row: two
            # rows built from two reads could describe two different instants.
            self._cc_saved = self._cc_state()
            # The badge saying which account Claude Code is signed in as comes
            # from the SAME read that decides whether the row offers a switch.
            # It used to come from a network probe made once when the window
            # opened, so after a switch the button moved and the badge stayed
            # where it was until the window was closed and opened again.
            st_read = self._cc_saved or {}
            cc_lbl.config(text=self._cc_here_text(st_read))
            unrecognised = st_read.get('unrecognised')
            cc_tip['text'] = '\n'.join(x for x in (cc_say(unrecognised),
                                                   cc_say(unrecognised, 'remedy'))
                                       if x) if unrecognised else ''
            for w in list_frame.winfo_children():
                w.destroy()
            accounts = self.cfg.get('accounts', [])
            if not accounts:
                tk.Label(list_frame, text=t('dlg_no_accounts'), font=FT_DLG_BODY,
                         fg=DIM, bg=BG, anchor='w').pack(fill='x', padx=8, pady=10)
            for acc in accounts:
                self._account_row(list_frame, acc, rebuild, st_read, width=dw)
            self._place_dialog(dlg, dw)

        rebuild()
        # The live file may hold a sign-in nothing here has caught up with yet.
        # Asking the server whose it is, and bringing that profile back in
        # step, happens off the main thread; the list is drawn again after.
        self._cc_sync_login(after=rebuild)
        # Said again now that the check is under way: a login that names no
        # organization reads "checking" until the answer draws the list again.
        cc_lbl.config(text=self._cc_here_text(self._cc_saved))

    def _cc_here_text(self, state):
        """The line above the account list: whose login Claude Code has on
        this computer, from the same reading as the rows below it.

        It used to wait for a probe that only started when the file named an
        organization (so after a sign-in it said "Checking..." for as long as
        the window stayed open), and to call any failure of that probe "no
        login", a rate limit included (review of 2026-09-26). Each state now
        says what it is, and the e-mail comes from the server's answer, the
        widget's own account for that organization, or its saved login.
        """
        live = (state or {}).get('live') or {}
        if not live.get('read') or not live.get('access_print'):
            return t('cc_here_none')
        org = live.get('org')
        if not org:
            return (t('cc_here_checking') if getattr(self, '_cc_sync_busy', False)
                    else t('cc_here_unknown'))
        acc = find_account_by_org(self.cfg, org) or {}
        who = live.get('server_email') or acc.get('email')
        if not who:
            prof = cc_accounts.find_profile_for_org(org, (state or {}).get('profiles') or [])
            # An account in the list is named even when it has no e-mail yet:
            # "an account not in this list" would be false of it.
            who = (prof or {}).get('email') or acc.get('name')
        text = t('cc_here').format(email=who) if who else t('cc_here_unlisted')
        try:
            expired = int(live.get('access_expires') or 0) / 1000
        except (TypeError, ValueError):
            expired = 0
        if expired and expired <= time.time():
            text += ' ' + t('cc_here_renews')
        return text

    def _backfill_identity(self):
        """One-time identity fill for the active account. A config migrated
        from the pre-multi-account format has no email/plan and a placeholder
        name; fetch them in the background so the account list shows the real
        identity without the user re-entering the key."""
        a = active_account(self.cfg)
        if not a or a.get('email') or not a.get('session_key'):
            return
        key, acc_id = a['session_key'], a.get('id')

        def work():
            try:
                info = fetch_account_info(key)
            except Exception as e:
                wlog(f'ACCT   identity backfill failed: {e}')
                return

            def apply():
                acc = active_account(self.cfg)
                if not acc or acc.get('id') != acc_id:
                    return
                if info.get('email'):
                    acc['email'] = info['email']
                # The plan of the account's own org: for a login that reads
                # several, info['plan'] is the browser's last-active one (#16).
                own = next((c for c in info.get('org_choices') or []
                            if c['id'] == acc.get('org_id')), None)
                plan = plan_label(own['tier']) if own else info.get('plan')
                if plan:
                    acc['plan'] = plan
                # Replace only an auto-generated placeholder name.
                if info.get('name') and re.match(r'^Account \d+$', acc.get('name', '')):
                    acc['name'] = info['name']
                save_cfg(self.cfg)
            self.root.after(0, apply)
        threading.Thread(target=work, daemon=True).start()

    def _set_active_account(self, acc_id, force=False):
        """Make `acc_id` the account on show, and forget the previous one.

        🔴 Every change of the account on show comes through here.
        Only the switch in the list used to empty the bars: adding an account,
        adopting one, linking a login and removing one each changed it their
        own way, and a fetch already on its way painted the previous account's
        numbers under the new name (review of 2026-09-26, five findings about
        the same fault). The fetch generation moves on, so answers still in
        flight are dropped; the bars, the last data, the taskbar overlay and
        the countdown start again. `force` does all of it even when the id
        stays the same, for an account whose identity just changed under it.
        Returns whether anything changed.
        """
        if not force and self.cfg.get('active_account') == acc_id:
            return False
        self.cfg['active_account'] = acc_id
        mirror_active(self.cfg)
        save_cfg(self.cfg)
        self._fetch_gen += 1
        self._soft_note = None
        self._clear_error()
        for sec in self._all_sections():
            sec.clear()
        self._has_usage = False
        self._last_data = None
        self._last_session_pct = None
        self._resets_at = []
        self._push_taskbar_state()
        # Reset the countdown / dot so they restart for the new account.
        self._set_pulse(False)
        self._apply_dot_phase('off')
        if self._countdown_job:
            self.root.after_cancel(self._countdown_job)
            self._countdown_job = None
        if acc_id:
            self.refresh()
            # The periodic refresh starts here too. It used to start only at
            # launch with an account already there, or from the setup window:
            # a first account added from Accounts > Add account was read once
            # and never again, and no sign-in to Claude Code was noticed
            # (review of 2026-09-26, third iteration).
            if not getattr(self, '_job', None):
                self._schedule()
        return True

    def _switch_account(self, acc_id, rebuild=None):
        if self._set_active_account(acc_id) and rebuild:
            rebuild()

    def _add_account(self, rebuild):
        """Pick a way in, then run it. Both ways end on the same account model,
        and both check whether that account is already in the list."""
        def on_success(key, info, name):
            existing = find_account_by_identity(self.cfg, info.get('org_id'),
                                                info.get('email'))
            if (existing is not None and existing.get('org_id')
                    and info.get('org_id') and existing['org_id'] != info['org_id']):
                # Same e-mail, another organization: another account, not an
                # update of this one (a personal and a team organization share
                # one address, and adopting gave the account the other's).
                existing = None
            if existing is not None:
                # Not a second account: the same one, reached with another
                # credential. Offer to bring it up to date instead of leaving
                # two rows counting the same usage.
                self._confirm_dialog(
                    t('dlg_account_exists_title'),
                    t('dlg_account_exists').format(name=existing.get('name') or '-'),
                    lambda: adopt(existing, key, info, name), label=t('dlg_update'))
                return
            n = len(self.cfg.get('accounts', [])) + 1
            acc = {'id': _new_id(),
                   'name': name or org_account_name(info) or info.get('name') or f'Account {n}',
                   'session_key': key, 'org_id': info['org_id'],
                   'email': info.get('email', ''), 'plan': info.get('plan', '')}
            self.cfg.setdefault('accounts', []).append(acc)
            self._set_active_account(acc['id'])
            finish()

        def adopt(acc, key, info, name):
            acc['session_key'] = key
            acc['org_id'] = info.get('org_id') or acc.get('org_id')
            if name:
                acc['name'] = name
            for field in ('email', 'plan'):
                if info.get(field):
                    acc[field] = info[field]
            self._set_active_account(acc['id'], force=True)
            finish()

        def finish():
            save_cfg(self.cfg)
            rebuild()

        def with_key():
            close()
            self._session_key_dialog(t('dlg_add_account'), on_success=on_success,
                                     prefill='', show_name=True, on_done=rebuild,
                                     offer_cc=False)

        def with_login():
            close()
            self._session_key_dialog(t('dlg_add_account'), on_success=on_success,
                                     prefill='', show_name=True, on_done=rebuild,
                                     start_cc=True)

        def close():
            try:
                dlg.destroy()
            except tk.TclError:
                pass

        dw, dh = self._dlg_size(460, 250)
        dlg, body = self._build_dialog_frame(t('dlg_add_account'), dw, dh, key='add_account')
        tk.Label(body, text=t('dlg_add_how'), font=FT_DLG_H, fg=FG, bg=BG,
                 anchor='w').pack(fill='x', pady=(0, 12))

        def option(title, detail, cmd, enabled=True):
            card = tk.Frame(body, bg=BAR_BG, cursor='hand2' if enabled else 'arrow')
            card.pack(fill='x', pady=(0, 10), ipady=10)
            inner = tk.Frame(card, bg=BAR_BG)
            inner.pack(fill='x', padx=14)
            tk.Label(inner, text=title, font=FT_DLG_BTN_B,
                     fg=FG if enabled else DIM, bg=BAR_BG, anchor='w').pack(fill='x')
            tk.Label(inner, text=detail, font=FT_DLG_HINT, fg=DIM, bg=BAR_BG,
                     anchor='w', wraplength=dw - 70, justify='left').pack(fill='x')
            if enabled:
                for w in (card, inner, *inner.winfo_children()):
                    w.bind('<Button-1>', lambda e: cmd())
                    w.bind('<Enter>', lambda e, c=card: c.configure(bg=SOFT_BG_HV))
                    w.bind('<Leave>', lambda e, c=card: c.configure(bg=BAR_BG))
            return card

        cc_there = os.path.exists(cc_accounts.cred_file())
        option(t('auth_claude_code'),
               t('dlg_add_cc_detail') if cc_there else t('cc_state_absent'),
               with_login, enabled=cc_there)
        option(t('auth_key'), t('dlg_add_key_detail'), with_key)

        buttons = tk.Frame(body, bg=BG)
        buttons.pack(fill='x', pady=(4, 0))
        self._outline_pill(buttons, t('dlg_cancel'), close).pack(side='right')
        self._place_dialog(dlg, dw, dh_floor=dh)


    def _edit_account_key(self, acc, rebuild):
        def on_success(key, info, name):
            org_before = acc.get('org_id')
            acc['session_key'] = key
            acc['org_id'] = info['org_id']
            # A key puts the account back on the session-key path: leaving
            # auth set would keep every fetch on the Claude Code token and
            # quietly ignore the key just entered.
            acc.pop('auth', None)
            if name:
                acc['name'] = name
            if info.get('email'):
                acc['email'] = info['email']
            if info.get('plan'):
                acc['plan'] = info['plan']
            is_active = acc.get('id') == self.cfg.get('active_account')
            if is_active and org_before and org_before != acc.get('org_id'):
                # The key belongs to another organization: what is on the bars
                # is the old one's, and goes.
                self._set_active_account(acc['id'], force=True)
            else:
                if is_active:
                    mirror_active(self.cfg)
                save_cfg(self.cfg)
                if is_active:
                    # Only the account on show has its error on show: cleared
                    # for another account's key, it was gone until the next
                    # refresh (review of 2.11, second iteration).
                    self._clear_error()
                    self.refresh()
            rebuild()
        self._session_key_dialog(t('dlg_update_key'), on_success=on_success,
                                 prefill=acc.get('session_key', ''),
                                 show_name=True, name_prefill=acc.get('name', ''),
                                 keep_org=acc.get('org_id'))

    def _rename_account(self, acc, rebuild):
        def on_ok(name):
            name = name.strip()
            if name:
                acc['name'] = name
                save_cfg(self.cfg)
                rebuild()
        self._name_prompt(t('dlg_rename'), acc.get('name', ''), on_ok)

    def _remove_account(self, acc, rebuild):
        def on_yes():
            self.cfg['accounts'] = [a for a in self.cfg.get('accounts', [])
                                    if a.get('id') != acc.get('id')]
            if self.cfg.get('active_account') == acc.get('id'):
                accounts = self.cfg['accounts']
                # Through the one door: the removed account's numbers must not
                # stay on the bars under the next account's name.
                self._set_active_account(accounts[0]['id'] if accounts else None,
                                         force=True)
                if not accounts:
                    # Nothing active left to mirror: clear it, or the key the
                    # user just removed would stay in the config and in the
                    # backup that sync_backup refreshes below.
                    self.cfg['session_key'] = ''
                    self.cfg['org_id'] = ''
                    self._error(t('setup_required'),
                                action_label=t('action_setup_now'),
                                action_cmd=self._setup_dialog)
            save_cfg(self.cfg)
            # Removing an account means removing its key, backup included.
            sync_backup()
            rebuild()
        self._confirm_dialog(t('dlg_remove'), t('dlg_remove_confirm'), on_yes)

    def _name_prompt(self, title, initial, on_ok):
        """Small single-field text prompt (account rename)."""
        dw, dh = self._dlg_size(380, 170)
        dlg, body = self._build_dialog_frame(title, dw, dh, key='name_prompt')
        tk.Label(body, text=t('dlg_account_name'), font=FT_DLG_H, fg=FG,
                 bg=BG, anchor='w').pack(fill='x')
        wrap = tk.Frame(body, bg=BAR_BG, padx=1, pady=1)
        wrap.pack(fill='x', pady=(8, 0))
        entry = tk.Entry(wrap, font=FT_DLG_BODY, bg=BAR_BG, fg=FG,
                         insertbackground=FG, bd=0, highlightthickness=0, relief='flat')
        entry.pack(fill='x', ipady=7, ipadx=10)
        entry.insert(0, initial or '')
        entry.bind('<FocusIn>', lambda e: (wrap.configure(bg=FOCUS_RING),
                   entry.after_idle(lambda: entry.select_range(0, 'end'))))
        entry.bind('<FocusOut>', lambda e: wrap.configure(bg=BAR_BG))
        entry.focus_set()

        btns = tk.Frame(body, bg=BG)
        btns.pack(fill='x', side='bottom', pady=(12, 0))

        def confirm():
            on_ok(entry.get())
            dlg.destroy()
        self._primary_pill(btns, t('dlg_save'), confirm).pack(side='right')
        self._secondary_pill(btns, t('dlg_cancel'), dlg.destroy).pack(
            side='right', padx=(0, 8))
        entry.bind('<Return>', lambda e: confirm())
        self._place_dialog(dlg, dw)

    def _confirm_dialog(self, title, msg, on_yes, label=None):
        """Small yes/no confirmation. The button says what it will do: it
        said "Remove" on the question that offers to UPDATE an account."""
        dw, dh = self._dlg_size(380, 180)
        dlg, body = self._build_dialog_frame(title, dw, dh, key='confirm')
        tk.Label(body, text=msg, font=FT_DLG_BODY, fg=FG, bg=BG, anchor='w',
                 justify='left', wraplength=dw - 40).pack(fill='x')
        btns = tk.Frame(body, bg=BG)
        btns.pack(fill='x', side='bottom', pady=(16, 0))

        def confirm():
            on_yes()
            dlg.destroy()
        self._primary_pill(btns, label or t('dlg_remove'), confirm).pack(side='right')
        self._secondary_pill(btns, t('dlg_cancel'), dlg.destroy).pack(
            side='right', padx=(0, 8))
        self._place_dialog(dlg, dw)

    # ── Quit ─────────────────────────────────────────

    # ── Win32 toolwindow style ─────────────────────

    def _make_wintab_visible(self):
        """Cache the widget's HWND and apply the initial taskbar style."""
        try:
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            if not hwnd:
                hwnd = self.root.winfo_id()
            self._hwnd = hwnd
            self._apply_taskbar_visibility()
        except Exception:
            pass

    def _apply_taskbar_visibility(self):
        """Set or clear the taskbar icon based on the show_in_taskbar config.

        Off (default): WS_EX_TOOLWINDOW + WS_EX_NOACTIVATE - widget is a
        pure floating tool, no taskbar icon, no Win+Tab entry, never
        activates on click (which avoids the click-the-taskbar focus-
        transfer flash).

        On: WS_EX_APPWINDOW - widget gets a real taskbar icon, which is
        also a prerequisite for ITaskbarList3 progress drawing. Keep
        NOACTIVATE so the click-into-widget UX stays the same.
        """
        hwnd = getattr(self, '_hwnd', None)
        if not hwnd:
            return
        try:
            GWL_EXSTYLE      = -20
            WS_EX_APPWINDOW  = 0x00040000
            WS_EX_TOOLWINDOW = 0x00000080
            WS_EX_NOACTIVATE = 0x08000000
            SWP_FRAMECHANGED = 0x0020
            SWP_NOMOVE       = 0x0002
            SWP_NOSIZE       = 0x0001
            SWP_NOZORDER     = 0x0004
            SWP_NOACTIVATE   = 0x0010
            SW_HIDE          = 0
            SW_SHOWNOACTIVATE = 4

            show = bool(self.cfg.get('show_in_taskbar', False))
            exstyle = ctypes.windll.user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
            if show:
                # Real taskbar icon: drop NOACTIVATE so clicking the icon
                # can raise the widget like any other app. Trade-off: the
                # widget can flash briefly when the user clicks back into
                # the taskbar, since focus transfer is no longer
                # suppressed. That's the cost of having a real icon.
                exstyle = ((exstyle | WS_EX_APPWINDOW)
                           & ~WS_EX_TOOLWINDOW & ~WS_EX_NOACTIVATE)
            else:
                # Pure floating tool window: NOACTIVATE on, no taskbar
                # icon, no foreground transitions, no flash.
                exstyle = ((exstyle | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)
                           & ~WS_EX_APPWINDOW)

            # The taskbar/Win+Tab style change only takes effect once the
            # window has been hidden and shown again. Hide+show without
            # activation so the user's focus / topmost ordering doesn't
            # flicker.
            ctypes.windll.user32.ShowWindow(hwnd, SW_HIDE)
            ctypes.windll.user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, exstyle)
            ctypes.windll.user32.SetWindowPos(
                hwnd, 0, 0, 0, 0, 0,
                SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE
                | SWP_NOZORDER | SWP_NOACTIVATE)
            ctypes.windll.user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
            wlog(f'TASKBAR show_in_taskbar={show} exstyle={exstyle:#x}')
        except Exception as e:
            wlog(f'TASKBAR apply visibility failed: {e}')

    def _push_taskbar_state(self):
        """Push the cached usage % to the taskbar progress overlay.

        Colour mapping. ITaskbarList3 only exposes three coloured
        states (NORMAL / PAUSED / ERROR), so the bands collapse to:

            0-54   -> NORMAL  (Win11 accent colour, defaults to blue;
                                grey on the user's silver-themed setup)
            55-79  -> PAUSED  (yellow, warning)
            >=80   -> ERROR   (red, danger - same state at 100 %)

        The bar fill width tracks the actual percentage so even at low
        usage the wedge position gives a rough read in addition to the
        colour band.
        """
        tp = getattr(self, '_taskbar', None)
        hwnd = getattr(self, '_hwnd', None)
        if not tp or not hwnd:
            return
        if not self.cfg.get('show_in_taskbar', False):
            tp.set_state(hwnd, TaskbarProgress.NOPROGRESS)
            return
        pct = getattr(self, '_last_session_pct', None)
        if pct is None:
            tp.set_state(hwnd, TaskbarProgress.NOPROGRESS)
            return
        if pct >= 80:
            state = TaskbarProgress.ERROR     # red (incl. 100 %)
            label = 'ERROR/red'
        elif pct >= 55:
            state = TaskbarProgress.PAUSED    # yellow
            label = 'PAUSED/yellow'
        else:
            state = TaskbarProgress.NORMAL    # accent
            label = 'NORMAL/accent'
        # MSDN samples set the state BEFORE the value: SetProgressValue
        # is documented to "force the state to TBPF_NORMAL" the first
        # time it's called on a window that has no state, and the second
        # call (our SetProgressState) would then override that to the
        # right colour - which is fine, but doing state-then-value also
        # guarantees the colour is locked in.
        tp.set_state(hwnd, state)
        # Floor the rendered fill at 2 % so a freshly-reset session
        # (pct = 0) doesn't visually disappear. Windows renders
        # SetProgressValue(0, 100) as a 0-pixel fill - technically a
        # bar in NORMAL state, visually nothing - and the user reads
        # that as "the bar is gone" (reported after the session reset
        # from 100 % back to 0 %). A couple of pixels of accent colour
        # are enough to confirm the icon is still tracking.
        fill = max(2, min(100, int(pct)))
        tp.set_progress(hwnd, fill, 100)
        wlog(f'TASKBAR push pct={pct} state={label} hwnd={hwnd:#x}')

    # ── Keep topmost (above taskbar) ────────────────

    def _force_topmost(self):
        """Re-assert topmost for the widget (and for any open menu).

        Earlier versions skipped this step while a menu was open so the menu
        wouldn't be pushed under the widget. That was brittle: if the menu
        was never closed (user didn't click or press Escape), the widget
        stopped being re-raised and would eventually slip behind the taskbar.
        Now we raise the widget first and then the menu on top of it - both
        stay topmost and the menu keeps visual priority.
        """
        try:
            hwnd = getattr(self, '_hwnd', None)
            if not hwnd:
                hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
                if not hwnd:
                    hwnd = self.root.winfo_id()
                self._hwnd = hwnd
            # While the widget changes behind its picture (_reshape) it is
            # cloaked, and raising it changes nothing on screen. The picture
            # is not moved here: it stays where it was put, directly above the
            # widget as it was, under the widget's own popups (Windows keeps
            # a window's popups above it) and over what was under the widget.
            ctypes.windll.user32.SetWindowPos(
                hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
            self.root.attributes('-topmost', True)
            self._raise_covered_dialogs()
            # Keep the menu above the widget when it's open, and its flyout
            # with it: a re-raised page covered the flyout (third iteration).
            for m in (self._menu_win, self._flyout_win):
                if m and m.winfo_exists():
                    self._raise_window(m)
        except Exception:
            pass

    def _keep_topmost(self):
        """Re-assert topmost every 10ms to stay above taskbar.

        10ms is below the 16ms frame budget at 60Hz, so any taskbar-overlap
        flash is recovered within a single frame and stays imperceptible.
        SetWindowPos with NOMOVE/NOSIZE/NOACTIVATE is a cheap no-op when
        the window is already topmost, so the high cadence doesn't show
        up in CPU usage. Going lower (e.g. after(0)) would starve the Tk
        mainloop of drag/refresh/click events.
        """
        self._force_topmost()
        self._topmost_job = self.root.after(10, self._keep_topmost)

    @staticmethod
    def _raise_window(win):
        """To the top of the topmost band, without moving, resizing or
        activating it. A still picture standing in for it (_hold, and the one
        of the window it replaces) goes back above it: raised alone, the
        hidden window went over its own picture and the widget over both."""
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
        ctypes.windll.user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
        for still in (getattr(win, '_stand_in', None), getattr(win, '_still', None)):
            if still is not None:
                still.lift()

    def _raise_covered_dialogs(self):
        """Put back above the widget the windows it covers.

        The widget raises itself every 10 ms, and a tall window that found no
        room above or below it had part of itself covered for good: at 150% on
        a 1080p screen, the account page with its advanced options open lost
        "Save" and "Remove account" under a widget parked at the bottom
        (photographs of 2026-09-26). In the order they opened, so a
        confirmation stays above the page that asked for it.

        From the first window the widget covers onwards, every window opened
        after it is raised too, whether it touches the widget or not: a
        confirmation or a picture picker opened from a covered page is placed
        clear of the widget, and raising the page alone put it back under the
        page within 10 ms, the click on it apparently dead (third iteration).
        Then the tooltips on show, the update banner and the menu's flyout,
        which belong above all of them. Windows opened before the first
        covered one keep their order.
        """
        alive = []
        raising = False
        # The widget, and the picture standing in for it while it changes
        # (_reshape): the picture has the size it had.
        widget = [(self.root.winfo_rootx(), self.root.winfo_rooty(),
                   self.root.winfo_width(), self.root.winfo_height())]
        still = getattr(self.root, '_still', None)
        if still is not None and still.hwnd:
            widget.append(still.rect)
        for dlg in self._open_dialogs:
            try:
                if not dlg.winfo_exists():
                    continue
                alive.append(dlg)
                if not dlg.winfo_ismapped():
                    continue
                if not raising:
                    # The window, and the picture standing in for it: a page
                    # built out of sight in place of another is not yet where
                    # its picture is, and the widget went over the picture for
                    # a tick (review of 2026-09-27).
                    rects = [(dlg.winfo_rootx(), dlg.winfo_rooty(),
                              dlg.winfo_width(), dlg.winfo_height())]
                    for still in (getattr(dlg, '_stand_in', None),
                                  getattr(dlg, '_still', None)):
                        if still is not None and still.hwnd:
                            rects.append(still.rect)
                    raising = any(dx < wx + ww and wx < dx + dw_ and dy < wy + wh
                                  and wy < dy + dh_ for dx, dy, dw_, dh_ in rects
                                  for wx, wy, ww, wh in widget)
                if raising:
                    self._raise_window(dlg)
            except tk.TclError:
                continue
        self._open_dialogs = alive
        if not raising:
            return
        tips = set()
        for tip in self._shown_tips:
            try:
                if tip.winfo_exists():
                    tips.add(tip)
            except tk.TclError:
                continue
        self._shown_tips = tips
        for win in [*tips, self._update_banner, self._flyout_win]:
            try:
                if win is not None and win.winfo_exists() and win.winfo_ismapped():
                    self._raise_window(win)
            except tk.TclError:
                continue

    def _signal_quit(self, signum, frame):
        wlog(f'SIGNAL received {signum} -> saving and exiting')
        self._save_geometry()
        sys.exit(0)

    def _quit_hide(self):
        """Out of sight while the quit waits for a renewal: the widget, every
        window of its, and the timers that would fetch, notify and save
        meanwhile. Only the widget was hidden, and a Switch or a Verify could
        still be pressed on a dialog left open (review of 2026-09-30, second
        iteration)."""
        try:
            self._close_menu()
            self._save_geometry()
            self._quit_geometry_saved = True
        except tk.TclError:
            pass
        for job in ('_job', '_countdown_job', '_topmost_job', '_pulse_job'):
            jid = getattr(self, job, None)
            if jid:
                try:
                    self.root.after_cancel(jid)
                except (tk.TclError, ValueError):
                    pass
                setattr(self, job, None)
        for win in list(self._dialogs.values()):
            try:
                win.destroy()
            except (tk.TclError, AttributeError):
                pass
        # Not in _dialogs, and its Update led to the installer (review of
        # 2026-09-30, third iteration).
        self._dismiss_update_banner()
        self._hide_tooltips()
        try:
            self.root.withdraw()
        except tk.TclError:
            pass

    def _quit(self):
        # 🔴 A renewal between its request and the write of its answer holds
        # the only copy of a login: the server retires the old token as it
        # answers. Quitting then (the menu, an update, a restart) lost it. So
        # the quit first closes the gate, and nothing new starts (a check
        # queued behind another, a switch, a Verify pressed now); then it
        # waits, out of sight, while anything holds the renewal lock. Checked
        # once and capped at 30 s, a renewal starting behind the check, or one
        # slower than the cap, was still lost (review of 2026-09-30, both
        # iterations).
        cc_accounts.close_renewals()
        if cc_accounts.renewal_in_flight():
            now = time.monotonic()
            if getattr(self, '_quit_waiting', None) is None:
                self._quit_waiting = now
                wlog('QUIT   waiting for a renewal to be kept')
                self._quit_hide()
            if now - self._quit_waiting < QUIT_RENEWAL_WAIT_S:
                if getattr(self, '_quit_poll', None) is None:
                    def again():
                        self._quit_poll = None
                        self._quit()
                    self._quit_poll = self.root.after(100, again)
                return
            wlog('QUIT   a renewal is still at work: quitting anyway')
        if getattr(self, '_install_waiting', None) is not None:
            # Quit while an update waited for the lock: its installer never
            # started, and the mark would have held every script off for
            # nothing (review of 2026-09-30, fifth iteration).
            cc_accounts.clear_install_mark()
        wlog('QUIT   _quit() called')
        self._close_menu()
        if not getattr(self, '_quit_geometry_saved', False):
            self._save_geometry()
        if self._job:
            self.root.after_cancel(self._job)
        if self._countdown_job:
            self.root.after_cancel(self._countdown_job)
        if self._topmost_job:
            self.root.after_cancel(self._topmost_job)
        if self._pulse_job:
            self.root.after_cancel(self._pulse_job)
        # Clear any progress overlay before tearing the COM wrapper down.
        try:
            tp = getattr(self, '_taskbar', None)
            hwnd = getattr(self, '_hwnd', None)
            if tp and hwnd:
                tp.set_state(hwnd, TaskbarProgress.NOPROGRESS)
            if tp:
                tp.close()
        except Exception:
            pass
        self.root.destroy()


# ═══════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════

def _single_instance():
    """Ensure only one widget instance runs. Returns mutex handle or exits.

    Reads the last error via a use_last_error DLL binding so the
    ERROR_ALREADY_EXISTS check cannot be defeated by an intervening ctypes
    call resetting the thread's last error (which would let a second instance
    launch). The named mutex is released by the OS on process exit, so a crash
    can never leave a stale lock that wedges the next launch."""
    try:
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        mutex = kernel32.CreateMutexW(None, True, 'ClaudeUsageWidget_SingleInstance')
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            # Another instance is running - bring it to front and exit
            hwnd = ctypes.windll.user32.FindWindowW(None, 'Claude Usage')
            if hwnd:
                ctypes.windll.user32.SetForegroundWindow(hwnd)
            sys.exit(0)
        return mutex
    except Exception:
        return None


if __name__ == '__main__':
    # The saved Claude Code logins from the command line: the scripts that
    # switch accounts carry out every change through here, on these rules and
    # no others (2026-09-26). Before the single instance is claimed, because
    # the widget is normally running when a script asks, and with no window:
    # it answers and exits.
    if len(sys.argv) > 1 and sys.argv[1] == '--cc':
        # Whatever fails here, the answer is 1, "unknown": the scripts then
        # look at the credentials file themselves. Never 3, which says that
        # nothing was written, and on which claude-switch-to.ps1 drops its
        # way back without looking.
        try:
            import cc_cli
            _code = cc_cli.main(sys.argv[2:], sys.modules[__name__])
        except Exception:
            import traceback
            wlog('CLI    failed: ' + _redact(traceback.format_exc()))
            _code = 1
        sys.exit(_code)
    _mutex = None if os.environ.get('CLAUDE_USAGE_DEV') == '1' else _single_instance()
    # Started again: the mark of an update (mark_install) goes once that
    # update is over or never happened, not while its installer still runs
    # (a widget started again under a waiting elevation prompt).
    if _mutex is not None:
        cc_accounts.clear_lapsed_install_mark()

    # Catch unhandled exceptions globally (including tkinter callbacks)
    def _excepthook(exc_type, exc_value, exc_tb):
        import traceback
        tb = ''.join(traceback.format_exception(exc_type, exc_value, exc_tb))
        wlog(f'UNHANDLED  {tb}')
        write_crash('UNHANDLED', tb)
    sys.excepthook = _excepthook

    wlog('INIT   process started')
    try:
        Widget()
    except Exception:
        import traceback
        tb = traceback.format_exc()
        wlog(f'CRASH  {tb}')
        write_crash('CRASH', tb)
        # Startup failed before any window appeared. Without this the process
        # would just vanish with no feedback; show a native message box so the
        # user knows it crashed and where the log is.
        try:
            ctypes.windll.user32.MessageBoxW(
                None,
                'Claude Usage failed to start.\n\n'
                f'Details were written to:\n{CRASH_LOG_FILE}',
                'Claude Usage', 0x10)  # MB_ICONERROR
        except Exception:
            pass
