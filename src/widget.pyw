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
import re
import json
import uuid
import colorsys
import math
import ssl
import time
import ctypes
import signal
import atexit
import tempfile
import threading
import base64
import subprocess
import webbrowser
import winreg
import urllib.request
import urllib.error
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime, timezone
from PIL import Image, ImageDraw, ImageTk

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
DATA_DIR = os.path.join(os.environ.get('LOCALAPPDATA', EXE_DIR), 'Claude Usage')
os.makedirs(DATA_DIR, exist_ok=True)
_RES = getattr(sys, '_MEIPASS', EXE_DIR)  # bundled resources (icons) or same as EXE_DIR
# Migrate config.json from old location if needed
_old_cfg = os.path.join(EXE_DIR, 'config.json')
_new_cfg = os.path.join(DATA_DIR, 'config.json')
if os.path.exists(_old_cfg) and not os.path.exists(_new_cfg):
    import shutil
    shutil.copy2(_old_cfg, _new_cfg)
CFG = _new_cfg
# Dev mode (env CLAUDE_USAGE_DEV=1): use a separate config so test runs never
# touch the real one, and skip single-instance (below) so the source can run
# alongside an installed copy. Invisible to normal users (env var unset).
if os.environ.get('CLAUDE_USAGE_DEV') == '1':
    CFG = os.path.join(DATA_DIR, 'config-dev.json')

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
RED      = '#E85858'
ORANGE   = '#E8A838'
BLUE     = '#5B9BD5'
PURPLE   = '#9B72CF'
HOVER_BG = '#3a3a38'
OCHRE    = '#C8962A'
DOT_W    = '#d0d0d0'
DOT_W_H  = '#ffffff'
DOT_W_D  = '#a0a09e'
DOT_GREEN   = '#6BC275'  # pre-refresh breathing dot (matches lbl_info green)
PCT_FG   = '#ffffff'
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
APP_VERSION = '2.8.46'

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
MIN_H_E  = 46   # essential mode minimum height
MIN_H_N  = 90   # normal mode minimum height
PAD      = 12
BAR_H    = 16
TITLE_H  = 28
DOT_INSET = BAR_H // 2   # dot centre sits on the centre of the bar's right rounded
                         # cap (the ideal circle that completes the end semicircle)
DOT_DIAM  = 7     # pre-refresh dot diameter (matches the corner dots' footprint)
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
            weight=spec[2] if len(spec) > 2 else 'normal')


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

# Surface / state colors used by dialogs and menus (complement the theme)
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

# ─── i18n ───────────────────────────────────────────
LANG = {
    'en': {
        'current_session': 'Current Session',
        'all_models': 'All models (7d)',
        'sonnet_only': 'Sonnet only (7d)',
        'model_scoped': '{model} only (7d)',
        'not_available': 'not available',
        'not_used': 'not used',
        'soon': 'soon',
        'reset_prefix': 'reset',
        'days': ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'],
        'unit_d': 'd', 'unit_h': 'h', 'unit_min': 'min',
        'setup_required': 'Session key required to connect to Claude.ai.',
        'session_expired': 'Session expired. Renew your session key to keep tracking usage.',
        'error': 'error',
        'empty_response': 'Empty response',
        'no_org': 'No organization found',
        'session_expired_short': 'Session expired',
        'action_setup_now': 'Configure now',
        'action_renew_now': 'Renew session',
        # Toast notifications
        'toast_title': 'Claude Usage',
        'toast_line_pct': 'Session: {pct}% reached at {now}',
        'toast_line_reset': 'Resets at {reset} (in {countdown})',
        'toast_line_no_reset': 'Session limit reached',
        # Menu
        'menu_refresh': 'Refresh',
        'menu_cat_display': 'Display',
        'menu_cat_data': 'Data & alerts',
        'menu_cat_account': 'Account',
        'menu_cat_general': 'General',
        'menu_mode_normal': 'Normal mode',
        'menu_mode_essential': 'Essential mode',
        'menu_renew': 'Session key\u2026',
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
        'menu_open_repo': 'Open GitHub repo',
        'menu_notifications_on': 'Notifications: ON',
        'menu_notifications_off': 'Notifications: OFF',
        'menu_taskbar_on': 'Taskbar icon: ON',
        'menu_taskbar_off': 'Taskbar icon: OFF',
        'menu_countdown': 'Countdown',
        'countdown_full': 'Numeric',
        'countdown_hidden': 'Hide countdown',
        'countdown_dot': 'Dot',
        'countdown_note': 'In multi-bar mode the dot (Essential) is always used.',
        'menu_essential_bars': 'Bars to show',
        'menu_sync_on': 'Sync time in bar: ON',
        'menu_colors_fixed': 'Bar colours: fixed',
        'menu_colors_dynamic': 'Bar colours: by usage',
        'menu_sync_off': 'Sync time in bar: OFF',
        'tip_countdown_dot': 'A pulsing dot signals when the next refresh is near.',
        'tip_countdown_full': 'Show the exact time left until the limit resets.',
        'tip_sync': 'Show the time of the last refresh next to each bar.',
        'tip_colors': 'Fixed: each bar keeps its own colour. By usage: every bar is coloured by its consumption (blue, amber, red).',
        'tip_notifications': 'Windows notification when session usage crosses a threshold.',
        'tip_taskbar': 'Show a taskbar button with a usage progress overlay.',
        'menu_refresh_interval': 'Refresh interval\u2026',
        'dlg_interval_title': 'Refresh interval',
        'dlg_interval_label': 'Interval in seconds (minimum 10):',
        'dlg_interval_invalid': 'Enter a number between 10 and 3600',
        'dlg_save': 'Save',
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
        'update_check_checking': 'Checking for updates\u2026',
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
        'dlg_connect': 'Connect',
        'dlg_cancel': 'Cancel',
        # Kept for legacy references - consolidated into dlg_step_* above
        'dlg_howto': 'Where do I find my session key?',
        'dlg_paste_here': 'Paste your session key below',
    },
    'it': {
        'current_session': 'Sessione Corrente',
        'all_models': 'Tutti i modelli (7gg)',
        'sonnet_only': 'Solo Sonnet (7gg)',
        'model_scoped': 'Solo {model} (7gg)',
        'not_available': 'non disponibile',
        'not_used': 'non utilizzato',
        'soon': 'tra poco',
        'reset_prefix': 'reset',
        'days': ['lun', 'mar', 'mer', 'gio', 'ven', 'sab', 'dom'],
        'unit_d': 'gg', 'unit_h': 'h', 'unit_min': 'min',
        'setup_required': 'Session key necessaria per connettersi a Claude.ai.',
        'session_expired': 'Sessione scaduta. Rinnova la session key per continuare.',
        'error': 'errore',
        'empty_response': 'Risposta vuota',
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
        'menu_cat_account': 'Account',
        'menu_cat_general': 'Generale',
        'menu_mode_normal': 'Modalit\u00e0 normale',
        'menu_mode_essential': 'Modalit\u00e0 essential',
        'menu_renew': 'Session key\u2026',
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
        'dlg_update_key': 'Aggiorna session key',
        'dlg_no_accounts': 'Nessun account.',
        'dlg_active': 'Attivo',
        'dlg_save': 'Salva',
        'menu_open_repo': 'Apri repo GitHub',
        'menu_notifications_on': 'Notifiche: attive',
        'menu_notifications_off': 'Notifiche: disattive',
        'menu_taskbar_on': 'Icona taskbar: visibile',
        'menu_taskbar_off': 'Icona taskbar: nascosta',
        'menu_countdown': 'Conto alla rovescia',
        'countdown_full': 'Numerico',
        'countdown_hidden': 'Nascondi conto alla rovescia',
        'countdown_dot': 'Puntino',
        'countdown_note': 'In multi-barra è sempre attivo Essenziale.',
        'menu_essential_bars': 'Barre da mostrare',
        'menu_sync_on': 'Orario sync nella barra: attivo',
        'menu_colors_fixed': 'Colori barre: fissi',
        'menu_colors_dynamic': 'Colori barre: per consumo',
        'menu_sync_off': 'Orario sync nella barra: disattivo',
        'tip_countdown_dot': 'Un puntino che pulsa segnala quando manca poco al prossimo aggiornamento.',
        'tip_countdown_full': 'Mostra il tempo esatto che manca al reset del limite.',
        'tip_sync': 'Mostra accanto a ogni barra quando e stato fatto l ultimo aggiornamento.',
        'tip_colors': 'Fissi: ogni barra tiene il suo colore. Per consumo: ogni barra e colorata in base al consumo (blu, giallo, rosso).',
        'tip_notifications': 'Notifica di Windows quando il consumo della sessione supera una soglia.',
        'tip_taskbar': 'Mostra un pulsante nella taskbar con la barra di avanzamento del consumo.',
        'menu_refresh_interval': 'Intervallo aggiornamento\u2026',
        'dlg_interval_title': 'Intervallo aggiornamento',
        'dlg_interval_label': 'Intervallo in secondi (minimo 10):',
        'dlg_interval_invalid': 'Inserisci un numero tra 10 e 3600',
        'dlg_save': 'Salva',
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
        'update_check_checking': 'Controllo aggiornamenti\u2026',
        'update_check_uptodate': 'Stai gi\u00e0 usando la versione pi\u00f9 recente (v{version}).',
        'update_check_failed': 'Impossibile contattare GitHub. Riprova pi\u00f9 tardi.',
        'update_check_no_asset': 'Nuova versione disponibile ma l\u2019installer non \u00e8 stato trovato nella release.',
        'dlg_renew_title': 'Rinnova sessione',
        'dlg_setup_title': 'Benvenuto',
        'dlg_welcome_hint': 'Collega il widget al tuo account Claude.ai.',
        'dlg_step_guide': 'Dove trovo la session key?',
        'dlg_step_paste': 'Incolla la session key qui sotto',
        'dlg_open_guide': 'Apri guida nel browser',
        'dlg_paste_empty': 'Incolla la session key nel campo sopra.',
        'dlg_invalid_prefix': 'Il valore deve iniziare con sk-ant-',
        'dlg_verifying': 'Verifica in corso\u2026',
        'dlg_error_prefix': 'Errore',
        'dlg_connect': 'Connetti',
        'dlg_cancel': 'Annulla',
        'dlg_howto': 'Dove trovo la session key?',
        'dlg_paste_here': 'Incolla la session key qui sotto',
    },
    'ja': {
        'current_session': '\u73fe\u5728\u306e\u30bb\u30c3\u30b7\u30e7\u30f3',
        'all_models': '\u5168\u30e2\u30c7\u30eb (7\u65e5)',
        'sonnet_only': 'Sonnet\u306e\u307f (7\u65e5)',
        'model_scoped': '{model}\u306e\u307f (7\u65e5)',
        'not_available': '\u5229\u7528\u4e0d\u53ef',
        'not_used': '\u672a\u4f7f\u7528',
        'soon': '\u307e\u3082\u306a\u304f',
        'reset_prefix': '\u30ea\u30bb\u30c3\u30c8',
        'days': ['\u6708', '\u706b', '\u6c34', '\u6728', '\u91d1', '\u571f', '\u65e5'],
        # Latin unit markers rather than the kanji forms: those are full-width
        # and push the reset label past the end of the bar, where it clips.
        'unit_d': 'd', 'unit_h': 'h', 'unit_min': 'm',
        'setup_required': 'Claude.ai \u306b\u63a5\u7d9a\u3059\u308b\u306b\u306f\u30bb\u30c3\u30b7\u30e7\u30f3\u30ad\u30fc\u304c\u5fc5\u8981\u3067\u3059\u3002',
        'session_expired': '\u30bb\u30c3\u30b7\u30e7\u30f3\u6709\u52b9\u671f\u5207\u308c\u3002\u30bb\u30c3\u30b7\u30e7\u30f3\u30ad\u30fc\u3092\u66f4\u65b0\u3057\u3066\u304f\u3060\u3055\u3044\u3002',
        'error': '\u30a8\u30e9\u30fc',
        'empty_response': '\u5fdc\u7b54\u304c\u7a7a\u3067\u3059',
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
        'menu_cat_account': '\u30a2\u30ab\u30a6\u30f3\u30c8',
        'menu_cat_general': '\u4e00\u822c',
        'menu_mode_normal': '\u901a\u5e38\u30e2\u30fc\u30c9',
        'menu_mode_essential': '\u30b7\u30f3\u30d7\u30eb\u30e2\u30fc\u30c9',
        'menu_renew': '\u30bb\u30c3\u30b7\u30e7\u30f3\u30ad\u30fc\u2026',
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
        'menu_open_repo': 'GitHub\u30ea\u30dd\u30b8\u30c8\u30ea\u3092\u958b\u304f',
        'menu_notifications_on': '\u901a\u77e5: \u30aa\u30f3',
        'menu_notifications_off': '\u901a\u77e5: \u30aa\u30d5',
        'menu_taskbar_on': '\u30bf\u30b9\u30af\u30d0\u30fc\u30a2\u30a4\u30b3\u30f3: \u8868\u793a',
        'menu_taskbar_off': '\u30bf\u30b9\u30af\u30d0\u30fc\u30a2\u30a4\u30b3\u30f3: \u975e\u8868\u793a',
        'menu_countdown': '\u30ab\u30a6\u30f3\u30c8\u30c0\u30a6\u30f3',
        'countdown_full': '\u6570\u5024',
        'countdown_hidden': '\u30ab\u30a6\u30f3\u30c8\u30c0\u30a6\u30f3\u3092\u975e\u8868\u793a',
        'countdown_dot': '\u30c9\u30c3\u30c8',
        'countdown_note': '\u30de\u30eb\u30c1\u30d0\u30fc\u3067\u306f\u5e38\u306b\u30a8\u30c3\u30bb\u30f3\u30b7\u30e3\u30eb\u3092\u4f7f\u7528\u3057\u307e\u3059\u3002',
        'menu_essential_bars': '\u8868\u793a\u3059\u308b\u30d0\u30fc',
        'menu_sync_on': '\u30d0\u30fc\u5185\u306e\u540c\u671f\u6642\u523b: \u30aa\u30f3',
        'menu_colors_fixed': '\u30d0\u30fc\u8272: \u56fa\u5b9a',
        'menu_colors_dynamic': '\u30d0\u30fc\u8272: \u6d88\u8cbb\u91cf',
        'menu_sync_off': '\u30d0\u30fc\u5185\u306e\u540c\u671f\u6642\u523b: \u30aa\u30d5',
        'tip_countdown_dot': '\u6b21\u306e\u66f4\u65b0\u304c\u8fd1\u3065\u304f\u3068\u70b9\u6ec5\u3059\u308b\u30c9\u30c3\u30c8\u3067\u77e5\u3089\u305b\u307e\u3059\u3002',
        'tip_countdown_full': '\u5236\u9650\u306e\u30ea\u30bb\u30c3\u30c8\u307e\u3067\u306e\u6b63\u78ba\u306a\u6b8b\u308a\u6642\u9593\u3092\u8868\u793a\u3057\u307e\u3059\u3002',
        'tip_sync': '\u5404\u30d0\u30fc\u306e\u6a2a\u306b\u6700\u5f8c\u306e\u66f4\u65b0\u6642\u523b\u3092\u8868\u793a\u3057\u307e\u3059\u3002',
        'tip_colors': '\u56fa\u5b9a: \u5404\u30d0\u30fc\u304c\u72ec\u81ea\u306e\u8272\u3092\u4fdd\u3061\u307e\u3059\u3002\u6d88\u8cbb\u91cf: \u3059\u3079\u3066\u306e\u30d0\u30fc\u304c\u6d88\u8cbb\u91cf\u306b\u5fdc\u3058\u3066\u8272\u5206\u3051\u3055\u308c\u307e\u3059 (\u9752\u3001\u9ec4\u3001\u8d64)\u3002',
        'tip_notifications': '\u30bb\u30c3\u30b7\u30e7\u30f3\u4f7f\u7528\u91cf\u304c\u3057\u304d\u3044\u5024\u3092\u8d85\u3048\u308b\u3068Windows\u901a\u77e5\u3092\u8868\u793a\u3057\u307e\u3059\u3002',
        'tip_taskbar': '\u30bf\u30b9\u30af\u30d0\u30fc\u306b\u4f7f\u7528\u72b6\u6cc1\u3092\u91cd\u306d\u305f\u30dc\u30bf\u30f3\u3092\u8868\u793a\u3057\u307e\u3059\u3002',
        'menu_refresh_interval': '\u66f4\u65b0\u9593\u9694\u2026',
        'dlg_interval_title': '\u66f4\u65b0\u9593\u9694',
        'dlg_interval_label': '\u79d2\u5358\u4f4d\u306e\u9593\u9694 (\u6700\u4f4e10):',
        'dlg_interval_invalid': '10\u304b\u30893600\u306e\u6570\u5024\u3092\u5165\u529b',
        'dlg_save': '\u4fdd\u5b58',
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
        'update_check_checking': '\u66f4\u65b0\u3092\u78ba\u8a8d\u4e2d\u2026',
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
        'dlg_connect': '\u63a5\u7d9a',
        'dlg_cancel': '\u30ad\u30e3\u30f3\u30bb\u30eb',
        'dlg_howto': '\u30bb\u30c3\u30b7\u30e7\u30f3\u30ad\u30fc\u306e\u53d6\u5f97\u65b9\u6cd5',
        'dlg_paste_here': '\u30bb\u30c3\u30b7\u30e7\u30f3\u30ad\u30fc\u3092\u4e0b\u306b\u8cbc\u308a\u4ed8\u3051\u307e\u3059',
    },
}

_current_lang = 'en'

def t(key):
    """Translate a key using the current language (fallback to English)."""
    return LANG.get(_current_lang, LANG['en']).get(key, LANG['en'].get(key, key))

def set_lang(code):
    global _current_lang
    if code in LANG:
        _current_lang = code


# ═══════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════

def load_cfg():
    if os.path.exists(CFG):
        with open(CFG, encoding='utf-8') as f:
            cfg = json.load(f)
        # Migrate old 5-min refresh to 3-min
        if cfg.get('refresh_ms') == 300_000:
            cfg['refresh_ms'] = REFRESH
            save_cfg(cfg)
        # Wrap a legacy single key into the accounts list on first run.
        if 'accounts' not in cfg:
            account_migrate(cfg)
            save_cfg(cfg)
        return cfg
    return {}


def save_cfg(data):
    with open(CFG, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)


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
    """Dark 'unused' track colour for a bar fill, reproducing the darkening of
    Claude's official fill/track pairs: keep the hue, push saturation up a
    touch (x1.123, clamped) and drop value to a fixed low 0.229."""
    r, g, b = [c / 255 for c in _hex_to_rgb(fill_hex)]
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    r, g, b = colorsys.hsv_to_rgb(h, min(1.0, s * 1.123), 0.229)
    return _rgb_to_hex((r * 255, g * 255, b * 255))


# Dynamic (percentage-driven) palette: every bar shares the same scale, so the
# colour reads the consumption level rather than which bar it is.
DYN_LOW  = '#2a78d6'   # blue  - low usage
DYN_MID  = '#fab219'   # amber - mid usage
DYN_HIGH = '#d03b3b'   # red   - high usage


def dynamic_fill(pct):
    """Fill colour for the dynamic palette, stepped by usage percentage."""
    if pct >= 85:
        return DYN_HIGH
    if pct >= 50:
        return DYN_MID
    return DYN_LOW


def format_reset(iso_str, compact=False):
    """Format reset time: 'reset 18:00 (3h 26min)' or 'reset Sat 11:00 (2d 5h)'.

    With compact=True returns the reduced form used by the side-by-side
    essential bars: just 'reset 3h 26min' / 'reset 2d 5h', with no absolute
    clock time, no weekday and no parentheses, so it fits under a narrow bar.
    """
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
    prefix = t('reset_prefix')
    if compact:
        return f'{prefix} {cd}'
    time_str = f'{local:%H:%M}'
    if local.date() == now_local.date():
        return f'{prefix} {time_str} ({cd})'
    days = t('days')
    return f'{prefix} {days[local.weekday()]} {time_str} ({cd})'


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


def _hex_to_rgb(color):
    h = color.lstrip('#')
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


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


def _render_pill_image(w, h, color, radius=None):
    """Render an anti-aliased pill as a PhotoImage.

    Supersamples at 4x then downscales with LANCZOS so the curves are smooth
    instead of the aliased staircase that tkinter's native oval produces.
    Cached by (w, h, color, radius) because buttons rarely change dimensions.
    """
    if radius is None:
        radius = h // 2
    key = (w, h, color, radius)
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
    )
    img = img.resize((w, h), Image.LANCZOS)
    photo = ImageTk.PhotoImage(img)
    _PILL_IMAGE_CACHE[key] = photo
    return photo


def make_pill_button(parent, *, text, font, fg, bg, hover_bg, cmd,
                     icon=None, icon_font=None, padx=16, pady=8, parent_bg=None):
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

    img_normal = _render_pill_image(btn_w, btn_h, bg)
    img_hover  = _render_pill_image(btn_w, btn_h, hover_bg)
    # Keep refs on the widget so Python GC doesn't reap the PhotoImages.
    cv._pill_normal = img_normal
    cv._pill_hover  = img_hover
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
# API
# ═══════════════════════════════════════════════════════

_BROWSER_UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36'
)


def _curl_get(url, session_key):
    """Minimal curl GET that returns the response body or raises.

    NOTE: claude.ai sits behind Cloudflare which fingerprints the TLS
    handshake (JA3) to detect non-browser clients. Python's urllib uses
    OpenSSL and gets a 403 challenge regardless of how browser-shaped
    the headers are. curl on Windows uses schannel - the same TLS stack
    Edge/Chrome use - so the JA3 matches a real browser. Schannel also
    uses the system CA store, so cert validation matches the browser.
    """
    # Capture bytes, not text: the API responses are UTF-8, but Python's
    # text mode would decode with the Windows locale (cp1252) and raise on any
    # non-latin1 byte (accented names, Japanese org names, emoji), silently
    # failing the whole call.
    result = subprocess.run(
        ['curl', '-s',
         '-H', f'Cookie: sessionKey={session_key}',
         '-H', f'User-Agent: {_BROWSER_UA}',
         '-H', 'anthropic-client-platform: web_claude_ai',
         url],
        capture_output=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW
    )
    if result.returncode != 0:
        raise RuntimeError(f'curl: {result.stderr.decode("utf-8", "replace").strip()}')
    body = result.stdout.decode('utf-8', 'replace').strip()
    if not body:
        raise RuntimeError(t('empty_response'))
    return body


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


def fetch_account_info(session_key):
    """Resolve org_id plus the account identity (email, name, plan) from a
    session key.

    Org selection strategy:
      1. List the user's orgs via /api/organizations.
      2. If there is exactly one, use it.
      3. If there are multiple, ask /api/bootstrap which one was last active
         in the browser (account.lastActiveOrgId), matching what Claude.ai
         shows the user. Fall back to the first org if bootstrap fails.

    The same bootstrap call carries the account's email and name, and the
    chosen org carries the rate-limit tier we turn into a plan label.
    """
    body = _curl_get('https://claude.ai/api/organizations', session_key)
    try:
        orgs = json.loads(body)
    except json.JSONDecodeError as e:
        raise RuntimeError(f'invalid response: {e}')
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

    chosen = None
    if len(orgs) == 1:
        chosen = orgs[0]
        org_id = chosen.get('uuid') or chosen.get('id')
    elif active_id:
        for o in orgs:
            if o.get('uuid') == active_id or o.get('id') == active_id:
                chosen = o
                break
        org_id = active_id  # bootstrap is authoritative even if not in the list
    else:
        chosen = orgs[0]
        org_id = chosen.get('uuid') or chosen.get('id')

    plan = plan_label((chosen or {}).get('rate_limit_tier'))
    return {'org_id': org_id, 'email': email, 'name': name, 'plan': plan}


def fetch_org_id(session_key):
    """Back-compat shim: just the org_id from fetch_account_info."""
    return fetch_account_info(session_key)['org_id']


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


def fetch_usage(cfg):
    """Fetch usage data from Claude.ai API. See fetch_org_id for why curl."""
    url = API_URL.format(cfg['org_id'])
    cookie = f"sessionKey={cfg['session_key']}; lastActiveOrg={cfg['org_id']}"
    result = subprocess.run(
        ['curl', '-s', '-D', '-',
         '-H', f'Cookie: {cookie}',
         '-H', f'User-Agent: {_BROWSER_UA}',
         '-H', 'anthropic-client-platform: web_claude_ai',
         url],
        capture_output=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW
    )
    if result.returncode != 0:
        raise RuntimeError(f'curl: {result.stderr.decode("utf-8", "replace").strip()}')
    # UTF-8, not the Windows locale: see _curl_get.
    stdout = result.stdout.decode('utf-8', 'replace')
    parts = stdout.split('\r\n\r\n', 1)
    if len(parts) < 2:
        parts = stdout.split('\n\n', 1)
    headers = parts[0] if len(parts) == 2 else ''
    body = parts[-1].strip()
    if not body:
        raise RuntimeError(t('empty_response'))
    sm = re.search(r'HTTP/[\d.]+ (\d+)', headers)
    if sm:
        code = int(sm.group(1))
        if code in (401, 403):
            raise PermissionError(t('session_expired_short'))
        if code >= 400:
            raise RuntimeError(f'HTTP {code}')
    km = re.search(r'sessionKey=([^;\s]+)', headers)
    if km and km.group(1) != cfg.get('session_key'):
        # Claude.ai rotated the key: persist it on the active account too, not
        # just the mirror, so the account list keeps a working key.
        set_active_key(cfg, km.group(1))
        save_cfg(cfg)
    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        raise RuntimeError(f'invalid response: {e}')


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
        self._dot_phase = 'off'      # 'off' | 'on' (pre-refresh breathing dot)
        self._dot_level = 1.0        # breathing brightness 0..1 (0 = invisible)
        self._dot_bg = BAR_BG        # colour behind the dot, for the fade-out
        self._dot_img = None         # keeps the current dot PhotoImage from GC
        self._reset_compact = False  # reduced reset sub-label for side-by-side bars

        self.frame = tk.Frame(parent, bg=BG)
        self.frame.pack(fill='x', padx=PAD, pady=(3, 0))

        self.hdr = tk.Frame(self.frame, bg=BG)
        self.hdr.pack(fill='x')
        self.lbl = tk.Label(self.hdr, text=label, font=FT, fg=FG, bg=BG)
        self.lbl.pack(side='left')
        self.lbl_info = tk.Label(self.hdr, text='', font=FT_S, fg='#6BC275', bg=BG)
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

    def update(self, pct, resets_at):
        if pct is None:
            self._pct = 0
            self._color = BAR_BG
            self.lbl_sub.config(text=t('not_available'))
            self._draw(self.cv.winfo_width())
            return
        self._pct = max(0, min(100, pct))
        if self._dynamic:
            self._color = dynamic_fill(self._pct)
            self._trackc = derive_track(self._color)
        else:
            self._color = self._fill
            self._trackc = self._track
        cd = format_reset(resets_at, compact=self._reset_compact)
        if cd:
            self.lbl_sub.config(text=cd)
        elif self._pct == 0:
            self.lbl_sub.config(text=t('not_used'))
        else:
            self.lbl_sub.config(text='')
        self._draw(self.cv.winfo_width())

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
        self.cv.create_text(w / 2, BAR_H / 2 - 1, text=txt,
                            fill='#ffffff', font=FT_BAR, anchor='center')
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
        # Keep the top-level key/org in sync with the active account (config
        # could have been hand-edited, or an account removed).
        mirror_active(self.cfg)
        # Label for the weekly per-model bar. Claude.ai names the model in the
        # usage payload (see scoped_model); persist the last-seen name so the
        # bar reads correctly before the first fetch of a new session.
        self._model_label = self.cfg.get('model_label', 'Sonnet')
        # Load language from config, default English
        set_lang(self.cfg.get('language', 'en'))
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
        self._topmost_job = None
        self._pulse_job = None
        self._pulse_phase = 0.0
        self._countdown_secs = 0
        self._last_time = ''
        self._resets_at = []  # ISO reset times - trigger refresh when reached
        self._last_data = None  # last fetched usage dict (for ess-bar re-render)
        self._dx = self._dy = 0
        self._expanded = False
        self._essential = False
        self._rs_x = self._rs_y = self._rs_w = self._rs_h = 0
        self._menu_win = None
        self._menu_click_job = None   # outside-click watcher while a menu is open
        self._menu_btn_down = False
        self._flyout_win = None       # category side-flyout Toplevel
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
        self._gh_icon = None
        try:
            if os.path.isfile(ICO_GITHUB):
                self._gh_icon = tk.PhotoImage(file=ICO_GITHUB)
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

        if self.cfg.get('session_key') and self.cfg.get('org_id'):
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
                           fg=CLAUDE, bg=BG_TITLE)
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

        # Refresh button - Segoe MDL2 refresh glyph at a compact size so it
        # matches the original ↻ footprint without the thin-arrow look.
        self.btn_r = tk.Label(self.tb, text=f' {ICON_REFRESH} ', font=FT_MDL2_TB,
                              fg=DIM, bg=BG_TITLE, cursor='hand2')
        self.btn_r.pack(side='right')
        self.btn_r.bind('<Button-1>', lambda e: self.refresh())
        self.btn_r.bind('<Enter>', lambda e: self.btn_r.config(fg=BLUE))
        self.btn_r.bind('<Leave>', lambda e: self.btn_r.config(fg=DIM))

        # Last update time
        self.lbl_time = tk.Label(self.tb, text='', font=FT_S, fg='#ffffff', bg=BG_TITLE)
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
        # _reset_compact is set dynamically (reduced only when >1 bar is shown).

        # Apply the saved palette mode to every bar now that they all exist.
        if self.cfg.get('bar_dynamic', False):
            for sec in self._all_sections():
                sec._dynamic = True

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

        # Resize dot - bottom-right (ALWAYS stays here)
        self.btn_resize = tk.Label(self.main, text='\u25cf', font=FT_DOT,
                                   fg=OCHRE, bg=BG, cursor='hand2',
                                   bd=0, highlightthickness=0, padx=0, pady=0)
        self.btn_resize.place(relx=1.0, x=-6, rely=1.0, y=-4, anchor='se')
        self.btn_resize.bind('<Button-1>', self._resize_start)
        self.btn_resize.bind('<B1-Motion>', self._resize_move)
        self.btn_resize.bind('<ButtonRelease-1>', self._save_geometry_user)
        self.btn_resize.bind('<Double-Button-1>', lambda e: self._toggle_essential())
        self.btn_resize.bind('<Enter>', lambda e: self.btn_resize.config(fg='#E06030'))
        self.btn_resize.bind('<Leave>', lambda e: self.btn_resize.config(fg=OCHRE))

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
        self.ess_time = tk.Label(self.ess_bar, text='', font=FT_S, fg='#ffffff', bg=BG,
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

    def _animate(self, start_y, start_h, end_y, end_h, cover, step=0):
        """Smooth upward expand/collapse with cover overlay to prevent artifacts."""
        total = 10
        if step == 0:
            try:
                self.root.attributes('-alpha', 1.0)  # opaque animates smoother
            except Exception:
                pass
        if step > total:
            self.root.geometry(f'{self.root.winfo_width()}x{end_h}+{self.root.winfo_x()}+{end_y}')
            self.root.update_idletasks()
            cover.destroy()
            try:
                self.root.attributes('-alpha', 0.94)  # restore translucency
            except Exception:
                pass
            self._animating = False
            return
        t = 1 - (1 - step / total) ** 3  # ease-out cubic
        cur_h = int(start_h + (end_h - start_h) * t)
        cur_y = int(start_y + (end_y - start_y) * t)
        self.root.geometry(f'{self.root.winfo_width()}x{cur_h}+{self.root.winfo_x()}+{cur_y}')
        self.root.after(8, self._animate, start_y, start_h, end_y, end_h, cover, step + 1)

    def _start_anim(self, start_y, start_h, end_y, end_h):
        """Create cover overlay and start animation."""
        cover = tk.Frame(self.root, bg=BG)
        # Extend beyond bounds to cover any edge artifacts
        cover.place(x=-10, y=-10, relwidth=1, relheight=1, width=20, height=20)
        cover.lift()
        self.root.update_idletasks()
        self._animating = True
        self._animate(start_y, start_h, end_y, end_h, cover)

    def _toggle_expand(self):
        if getattr(self, '_animating', False):
            return
        # Expand is an essential-mode affordance only; normal mode already
        # shows the selected bars stacked (the dot is hidden there).
        if not self._essential:
            return
        start_h = self.root.winfo_height()
        start_y = self.root.winfo_y()
        bottom = start_y + start_h

        # Cover the window during the relayout + animation so the brief paint of
        # the new layout squished into the old height never flashes. Re-lifted
        # after the repack so it stays above the freshly packed widgets.
        cover = tk.Frame(self.root, bg=BG)
        cover.place(x=-10, y=-10, relwidth=1, relheight=1, width=20, height=20)

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

        cover.lift()  # above the freshly packed widgets, so the reflow is hidden
        self.root.update_idletasks()
        if self._essential and not self._expanded:
            # Collapsing into the side-by-side strip: the bar count may need a
            # wider minimum than the last compute (e.g. bars added while
            # expanded), so re-floor the width before measuring/animating.
            self._update_minsize()
        # Size to the freshly-laid-out content in BOTH directions, keeping the
        # bottom edge anchored. A saved collapsed height was wrong when the mode
        # changed between expand and collapse (e.g. expand in essential, switch
        # to normal, then collapse): it restored essential's small height
        # instead of normal's. winfo_reqheight() always reflects current mode.
        end_h = self.root.winfo_reqheight()
        end_y = bottom - end_h
        # Reset to the start height (the cover already hides the content) and
        # animate; _animate destroys the cover when it finishes.
        self.root.geometry(f'{self.root.winfo_width()}x{start_h}+{self.root.winfo_x()}+{start_y}')
        self._animating = True
        self._animate(start_y, start_h, end_y, end_h, cover)

    # ── Essential mode ─────────────────────────────

    def _toggle_essential(self):
        if getattr(self, '_animating', False):
            return
        wlog(f'MODE   toggle essential: {self._essential} -> {not self._essential}')
        start_h = self.root.winfo_height()
        start_y = self.root.winfo_y()
        bottom = start_y + start_h
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
        else:
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
        end_h = self.root.winfo_reqheight()
        end_y = bottom - end_h
        self._update_minsize()
        # Cover content, reset to start, animate
        self.root.geometry(f'{self.root.winfo_width()}x{start_h}+{self.root.winfo_x()}+{start_y}')
        self._start_anim(start_y, start_h, end_y, end_h)

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
        self._restore_ess_width()

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

    def _sync_ess_reset_mode(self):
        """Reduced reset sub-label ('reset 43min') only when >1 bar is shown;
        single-bar essential keeps the full 'reset 17:10 (43min)' form."""
        multi = len(self._essential_bar_ids()) > 1
        for sec in self.ess_bars.values():
            sec._reset_compact = multi

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
        self._sync_ess_reset_mode()
        fh = d.get('five_hour')
        sd = d.get('seven_day')
        sp, sr, _ = scoped_model(d)
        self.ess_bars['session'].update(fh['utilization'] if fh else None,
                                        fh.get('resets_at') if fh else None)
        self.ess_bars['weekly'].update(sd['utilization'] if sd else None,
                                        sd.get('resets_at') if sd else None)
        self.ess_bars['sonnet'].update(sp, sr)

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

    def _update_minsize(self):
        """Single minimum width for both modes, tuned to essential-mode content.

        Both essential and normal mode use the same floor (whichever is wider
        between the essential content and the normal title bar). This keeps
        the essential-mode controls (close / refresh / time on the right)
        from clipping when the user switches modes, and the standard-mode
        title bar from clipping either.
        """
        self.root.update_idletasks()
        sub_w = self.s_session.lbl_sub.winfo_reqwidth() + 8
        # Reserve a little extra right-side padding so the subtitle text
        # never slides under the essential-mode controls (close / refresh /
        # time) which are placed on top of the widget via place().
        ess_w = self.ess_bar.winfo_reqwidth() + 18
        tb_w = self.tb.winfo_reqwidth() + 8
        needed = max(MIN_W, sub_w + ess_w, tb_w)
        # Collapsed essential mode with multiple side-by-side bars needs more
        # width: current min already fits 2 bars (each halved); 3 bars need
        # +50% (one extra third) to stay readable.
        if self._essential and not self._expanded:
            n = len(self._essential_bar_ids())
            if n > 1:
                # Each bar must be wide enough to show its reduced reset text,
                # plus the reserved hamburger column on the right.
                # Multi-bar always shows the dot (see _countdown_mode), so each
                # bar must fit '13% 19:11' + the dot without overlap; the sync
                # time adds width when shown.
                per_bar = 104 if self.cfg.get('show_sync_time', True) else 92
                needed = max(needed, n * per_bar + ESS_MENU_W + 2 * PAD)
                if n >= 3:
                    needed = max(needed, int(round(MIN_W * 1.5)))
            else:
                # Single bar keeps the full reset text, so measure it rather
                # than using the reduced per-bar estimate. The hamburger takes
                # the same reserved column here, and the bar shrinks to make
                # room for it, so the text needs that width back.
                needed = max(needed, sub_w + ESS_MENU_W + 2 * PAD)
        self.root.minsize(needed, 0)
        # Widen to the minimum only when the current width is below it (never
        # more than needed, and never shrink the user's width). Anchor the
        # RIGHT edge so the extra width grows to the LEFT, keeping the menu /
        # hamburger on the right in place.
        cur_w = self.root.winfo_width()
        if cur_w > 1 and cur_w < needed:
            right = self.root.winfo_x() + cur_w
            y = self.root.winfo_y()
            h = self.root.winfo_reqheight()
            self.root.geometry(f'{needed}x{h}+{right - needed}+{y}')

    def _auto_height(self):
        self.root.update_idletasks()
        x, y = self.root.winfo_x(), self.root.winfo_y()
        w = self.root.winfo_width()
        new_h = self.root.winfo_reqheight()
        self.root.geometry(f'{w}x{new_h}+{x}+{y}')

    def _restore_ess_width(self):
        """Essential-collapsed: set the width to the user's saved width for the
        current bar count (if wider than the minimum), else the minimum. Grows
        or shrinks, anchored to the right edge, and updates the height too."""
        if not (self._essential and not self._expanded):
            return
        self.root.update_idletasks()
        minw = self.root.minsize()[0]
        n = str(len(self._essential_bar_ids()))
        saved = self.cfg.get('ess_width', {}).get(n)
        target = max(minw, saved) if saved else minw
        cur = self.root.winfo_width()
        if cur > 1 and cur != target:
            right = self.root.winfo_x() + cur
            y = self.root.winfo_y()
            h = self.root.winfo_reqheight()
            self.root.geometry(f'{target}x{h}+{right - target}+{y}')

    # ── Resize via dot drag ────────────────────────

    def _resize_start(self, e):
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
        min_w = self.root.wm_minsize()[0] or MIN_W
        w = max(min_w, self._rs_w + (e.x_root - self._rs_x))
        x, y = self.root.winfo_x(), self.root.winfo_y()
        self.root.geometry(f'{w}x{self._rs_h}+{x}+{y}')

    # ── Data ─────────────────────────────────────────

    def refresh(self):
        if self._countdown_job:
            self.root.after_cancel(self._countdown_job)
            self._countdown_job = None
        self._set_pulse(False)
        self._apply_dot_phase('off')
        self.btn_r.config(fg=BLUE)
        for tgt in self._countdown_targets():
            tgt.set_countdown('\u2022\u2022\u2022')
        threading.Thread(target=self._fetch, daemon=True).start()

    def _fetch(self):
        wlog('FETCH  thread started')
        try:
            data = fetch_usage(self.cfg)
            wlog('FETCH  data received, dispatching to main thread')
            self.root.after(0, self._on_data, data)
        except PermissionError:
            wlog('FETCH  session expired (401/403)')
            try:
                self.root.after(
                    0,
                    lambda: self._error(
                        t('session_expired'),
                        action_label=t('action_renew_now'),
                        action_cmd=self._renew_session))
            except Exception as ex:
                wlog(f'FETCH  error after PermissionError: {ex}')
        except Exception as e:
            wlog(f'FETCH  exception: {e}')
            try:
                self.root.after(0, self._error, str(e))
            except Exception as ex:
                wlog(f'FETCH  error after Exception: {ex}')

    def _on_data(self, d):
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
        # Collect reset times for instant refresh when they arrive
        self._resets_at = []
        for rs in (fh.get('resets_at') if fh else None,
                   sd.get('resets_at') if sd else None, sr):
            if rs:
                try:
                    self._resets_at.append(datetime.fromisoformat(rs))
                except (ValueError, TypeError):
                    pass
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
        self._save_geometry()  # auto-save on each refresh (protection against kill)
        self._start_countdown()
        self._update_minsize()

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
        self.err_btn.pack_forget()
        self.err_frame.pack_forget()
        if self._err_action is not None:
            try:
                self.err_btn.unbind('<Button-1>')
            except Exception:
                pass
            self._err_action = None

    def _error(self, msg, action_label=None, action_cmd=None):
        """Show an error panel. If action_label/action_cmd are given, render a button below."""
        wlog(f'ERROR  {msg}')
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
        self._set_pulse(False)
        self._apply_dot_phase('off')
        for tgt in self._countdown_targets():
            tgt.set_countdown(t('error'))
        self.btn_r.config(fg=DIM)
        self._update_minsize()

    def _schedule(self):
        ms = self.cfg.get('refresh_ms', REFRESH)
        self._job = self.root.after(ms, self._schedule_tick)

    def _schedule_tick(self):
        wlog('SCHED  tick -> scheduled refresh')
        try:
            self.refresh()
        except Exception as e:
            wlog(f'SCHED  refresh error: {e}')
        self._schedule()

    # ── Drag ─────────────────────────────────────────

    def _drag_start(self, e):
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

    def _save_geometry(self, e=None, update_anchor=None):
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
            self.cfg['expanded'] = self._expanded
            self.cfg['essential'] = self._essential
            # Per-bar-count width memory (essential-collapsed only): remember a
            # width the user set WIDER than the minimum, keyed by how many bars
            # are shown, so it is restored when that bar count is shown again.
            # A width at the minimum stores nothing (means "no preference").
            if self._essential and not self._expanded:
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
        """Drag/resize release: persist geometry AND refresh the home anchor
        (the user deliberately moved or resized the widget)."""
        self._save_geometry(e, update_anchor=True)

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
        close_btn.bind('<Button-1>', lambda e: dlg.destroy())
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

    def _secondary_pill(self, parent, text, cmd, icon=None):
        """Secondary pill button (soft surface). Padding scales with DPI."""
        return make_pill_button(
            parent, text=text, font=FT_DLG_BTN,
            fg=FG, bg=SOFT_BG, hover_bg=SOFT_BG_HV, cmd=cmd,
            icon=icon, icon_font=FT_EMOJI_11 if icon else None,
            padx=self.dp(PILL_PAD_SECONDARY_X),
            pady=self.dp(PILL_PAD_SECONDARY_Y))

    def _build_dialog_frame(self, title, dw, dh):
        """Create a Toplevel with the standard chrome. Returns (dlg, body).

        Same title bar, padding, rounded corners and screen-clamped
        positioning across every dialog. The height passed in is treated
        as a MINIMUM: after the caller has finished populating `body`,
        an idle callback grows the dialog to whatever height the actual
        layout requires. This keeps the bottom controls (the orange
        Connect pill, in particular) on screen at higher Windows DPI
        scaling, where text/widgets render larger and the fixed minimum
        height would otherwise crop them.
        """
        dlg = tk.Toplevel(self.root)
        dlg.title(title)
        dlg.configure(bg=BG)
        dlg.overrideredirect(True)
        dlg.attributes('-topmost', True)
        dlg.resizable(False, False)

        # Park off-screen until the body is populated and we know the
        # real height. Avoids a flash at the default Tk spawn position.
        dlg.geometry(f'{dw}x{dh}+10000+10000')
        # Apply rounded corners early on the off-screen window so the
        # DWM attribute is in place before the dialog is first painted at
        # its visible position. Re-applied below after the final move to
        # be safe - Windows occasionally drops the corner preference if
        # the window is moved before being mapped.
        dlg.after(50, lambda: dwm_round(dlg))

        self._build_titlebar(dlg, title)

        body = tk.Frame(dlg, bg=BG)
        body.pack(fill='both', expand=True,
                  padx=DLG_PAD_X, pady=(DLG_PAD_TOP, DLG_PAD_BTM))
        dlg.bind('<Escape>', lambda e: dlg.destroy())

        def _finalize():
            try:
                dlg.update_idletasks()
                # max(passed-in, required) - at 100% DPI the two usually
                # match; on scaled displays req > passed-in.
                actual_h = max(dh, dlg.winfo_reqheight())
                # Clamp to the virtual desktop so a tall dialog on a
                # short monitor still fits; place_popup already handles
                # x clamping.
                vx, vy, vw, vh = self._virtual_bounds()
                actual_h = min(actual_h, vh - 40)
                wx, wy = self._place_popup(dw, actual_h)
                dlg.geometry(f'{dw}x{actual_h}+{wx}+{wy}')
                # Re-apply DWM rounding after the final position settles.
                dlg.after(50, lambda: dwm_round(dlg))
            except Exception:
                pass
        dlg.after_idle(_finalize)
        return dlg, body

    def _place_dialog(self, dlg, dw, dh_floor=0):
        """Resize a dialog to fit its content and re-anchor it to the widget.

        Runs now and again after idle: image-backed widgets (pill buttons,
        avatar discs) report their height only after the first render, so a
        single early measurement under-sizes the dialog and clips the bottom
        controls. The position is recomputed from the new height so a dialog
        above the widget grows upward, its bottom edge clear of the widget."""
        def _apply():
            dlg.update_idletasks()
            ml, mt, mr, mb = self._widget_monitor_area()
            need = min(dlg.winfo_reqheight(), (mb - mt) - 2 * SCREEN_MARGIN)
            x, y = self._place_popup(dw, need)
            dlg.geometry(f'{dw}x{need}+{x}+{y}')
        _apply()
        dlg.after_idle(_apply)

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
        # Move the Toplevel off-screen before populating so it doesn't flash
        # at the default (0, 0) corner while we compute its final position.
        m.geometry('+10000+10000')
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
        cats = (
            ('display', '\uECA5', FT_MDL2_MENU, t('menu_cat_display')),
            ('data',    '\U0001F514\uFE0E', FT_EMOJI, t('menu_cat_data')),
            ('account', '\U0001F5DD\uFE0E', FT_EMOJI, t('menu_cat_account')),
            ('general', '\u2699\uFE0E',     FT_EMOJI, t('menu_cat_general')),
        )
        for key, icon, ift, label in cats:
            r = self._menu_row(m, label, None, icon=icon, icon_ft=ift,
                               trailing='\u203A')
            self._bind_subtree(r, '<Button-1>',
                               lambda e, k=key, rr=r: self._open_flyout(k, rr))
        self._menu_sep(m)
        self._menu_row(m, t('menu_quit'), lambda: self._menu_do(self._quit),
                       icon='\u2715', icon_ft=FT_EMOJI)
        tk.Label(m, text=f'v{APP_VERSION}', font=FT_DLG_HINT, fg=DIM, bg=MENU_BG,
                 anchor='w', padx=14, pady=4).pack(fill='x')

        m.update_idletasks()
        mw = max(m.winfo_reqwidth(), 220)
        mh = m.winfo_reqheight() + 4  # small bottom breathing room
        bx, by = self._place_submenu(mw, mh)
        wlog(f'MENU   size={mw}x{mh} pos=({bx},{by})')
        # Single geometry call - both size and position applied atomically so
        # the window never renders with a stale size at its off-screen slot.
        m.geometry(f'{mw}x{mh}+{bx}+{by}')
        m.after(10, lambda: dwm_round(m))
        m.after(20, lambda: self._lift_menu(m))
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
        tk.Label(parent, text=text, font=FT_DLG_HINT, fg=DIM, bg=MENU_BG,
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
            if icon_ft is None and isinstance(icon, tk.PhotoImage):
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
        m.geometry('+10000+10000')
        self._populate_flyout(cat, m)
        m.update_idletasks()
        mw = max(m.winfo_reqwidth(), 200)
        mh = m.winfo_reqheight() + 6
        fx, fy = self._place_flyout(mw, mh, anchor)
        m.geometry(f'{mw}x{mh}+{fx}+{fy}')
        m.after(10, lambda: dwm_round(m))
        m.after(20, lambda: self._lift_menu(m))
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
        """Repopulate the open flyout in place after a stateful toggle."""
        m = self._flyout_win
        cat = self._flyout_cat
        if not m or not m.winfo_exists() or not cat:
            return
        for c in m.winfo_children():
            c.destroy()
        self._populate_flyout(cat, m)
        m.update_idletasks()
        mw = max(m.winfo_reqwidth(), 200)
        mh = m.winfo_reqheight() + 6
        fx, fy = self._place_flyout(mw, mh, self._flyout_anchor)
        m.geometry(f'{mw}x{mh}+{fx}+{fy}')
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
            tb = self.cfg.get('show_in_taskbar', False)
            self._menu_row(m, (t('menu_taskbar_on') if tb else t('menu_taskbar_off')),
                           lambda: self._flyout_set(self._toggle_taskbar),
                           icon='\U0001F4CC︎', icon_ft=FT_EMOJI, tip=t('tip_taskbar'))
        elif cat == 'account':
            # No standalone "Session key" entry: a key means nothing without
            # the account it belongs to, so keys are edited per account from
            # the accounts dialog (key icon / double-click on a row).
            self._menu_row(m, t('menu_accounts'),
                           lambda: self._menu_do(self._accounts_dialog),
                           icon=ICON_KEY, icon_ft=FT_MDL2_MENU)
            self._menu_row(m, t('menu_open_claude'),
                           lambda: self._menu_do(self._open_claude_usage),
                           icon='↗︎', icon_ft=FT_EMOJI)
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
        # Refresh to update reset text + any visible messages
        if self.cfg.get('session_key') and self.cfg.get('org_id'):
            self.refresh()

    def _set_countdown_mode(self, mode, close=True):
        """Apply a countdown display mode, persist it, re-render immediately."""
        self.cfg['countdown_display'] = mode
        save_cfg(self.cfg)
        wlog(f'CDOWN  countdown_display -> {mode}')
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

    def _all_sections(self):
        """Every usage bar (normal + essential-strip), for palette changes."""
        return [self.s_session, self.s_weekly, self.s_sonnet,
                *self.ess_bars.values()]

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

    def _color_picker_dialog(self, title, initial, on_pick):
        """In-tool HSV colour picker: preset swatches + a saturation/value
        square, a hue strip and a hex field, all kept in sync."""
        dw = 300
        dlg, body = self._build_dialog_frame(title, dw, 320)
        SVW, SVH, HUEH = dw - 40, 140, 14
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
        dlg, body = self._build_dialog_frame(t('dlg_interval_title'), 460, 260)

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
                              anchor='w', wraplength=460 - 40)
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

        btn_frame = tk.Frame(body, bg=BG)
        btn_frame.pack(fill='x', side='bottom', pady=(12, 0))
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
        bar.geometry('+10000+10000')  # off-screen until final placement

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
        bar.after(50, lambda: dwm_round(bar))
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

    def _on_banner_follow(self):
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
        dlg.geometry('+10000+10000')  # off-screen until positioned
        tk.Label(dlg, text=message, font=FT_DLG_BODY, fg=FG, bg=MENU_BG,
                 padx=16, pady=10, wraplength=340, justify='left').pack()
        dlg.update_idletasks()
        dw, dh = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
        wx, wy = self._place_popup(dw, dh, prefer='below')
        dlg.geometry(f'{dw}x{dh}+{wx}+{wy}')
        dlg.after(50, lambda: dwm_round(dlg))
        dlg.after(3500, dlg.destroy)

    def _show_update_dialog(self, info):
        """Full update dialog: shows changelog + download button + progress."""
        self._dismiss_update_banner()
        dw, dh = 520, 440
        dlg, body = self._build_dialog_frame(t('update_dlg_title'), dw, dh)

        subtitle = t('update_dlg_subtitle').format(
            version=info['version'], current=APP_VERSION)
        tk.Label(body, text=subtitle, font=FT_DLG_H, fg=CLAUDE, bg=BG,
                 anchor='w').pack(fill='x')

        tk.Label(body, text=t('update_dlg_changelog'), font=FT_DLG_BODY, fg=FG,
                 bg=BG, anchor='w').pack(fill='x', pady=(10, 4))

        raw_changelog = info.get('body') or ''
        raw_changelog = strip_boilerplate_sections(raw_changelog)
        changelog = raw_changelog or t('update_dlg_no_changelog')
        if len(changelog) > UPDATE_CHANGELOG_MAX_CHARS:
            changelog = changelog[:UPDATE_CHANGELOG_MAX_CHARS].rstrip() + '\u2026'

        txt_frame = tk.Frame(body, bg=BAR_BG, bd=0, highlightthickness=0)
        txt_frame.pack(fill='both', expand=True)
        txt = tk.Text(txt_frame, font=FT_DLG_BODY, fg=DIM, bg=BAR_BG, bd=0,
                      highlightthickness=0, wrap='word',
                      padx=12, pady=10, height=9, relief='flat',
                      cursor='arrow', spacing1=2, spacing3=2)
        render_markdown_into(txt, changelog,
                             base_font=FT_DLG_BODY, fg=DIM, header_fg=FG)
        txt.pack(fill='both', expand=True)

        status_lbl = tk.Label(body, text='', font=FT_DLG_HINT, fg=DIM, bg=BG,
                              anchor='w', wraplength=dw - 40)
        status_lbl.pack(fill='x', pady=(10, 0))

        progress_cv = tk.Canvas(body, height=6, bg=BG, bd=0, highlightthickness=0)
        progress_cv.pack(fill='x', pady=(4, 0))
        progress_cv.pack_forget()

        btn_frame = tk.Frame(body, bg=BG)
        btn_frame.pack(fill='x', side='bottom', pady=(12, 0))

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
            build_install_btn(enabled=False)
            progress_cv.pack(fill='x', pady=(4, 0))
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
                    dlg.after(0, lambda: status_lbl.config(
                        text=t('update_dlg_failed').format(error=str(e)), fg=RED))
                    dlg.after(0, lambda: build_install_btn(enabled=True))

            threading.Thread(target=worker, daemon=True).start()

        def build_install_btn(enabled=True):
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

    def _launch_installer(self, path):
        """Run the downloaded installer silently and exit so it can replace files.

        /VERYSILENT hides the wizard entirely (no language picker, no Next/Finish);
        /SUPPRESSMSGBOXES swallows info prompts; /NORESTART prevents the rare
        reboot request. The ISS [Run] section auto-relaunches the widget after
        install, so the whole cycle is: click Install -> UAC prompt -> brief
        pause while files are swapped -> new version is up.
        """
        try:
            subprocess.Popen(
                [path, '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART'],
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                close_fds=True)
        except Exception as e:
            wlog(f'UPDATE  launch failed: {e}')
            return
        wlog('UPDATE  installer launched (/VERYSILENT), exiting widget')
        self._save_geometry()
        # Give the OS a beat to start the installer before we vanish - the
        # UAC prompt needs to appear while our process still has focus.
        self.root.after(400, self._quit)

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
        self._session_key_dialog(t('dlg_renew_title'))

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
                            prefill=None, show_name=False, name_prefill=''):
        """Key entry dialog, shared by setup / renew / add / edit-key.

        on_success(key, info, name): when given, called with the verified key,
        its account info (org_id, email, name, plan) and the account name from
        the optional name field, instead of the default behaviour of writing
        the active account. prefill seeds the key entry; without it the dialog
        shows the active key (legacy renew flow). show_name adds an account
        name field (add / edit), so the key and name are set together.
        """
        dw, dh = 460, (392 if show_name else 320)
        dlg, body = self._build_dialog_frame(title, dw, dh)

        if is_setup:
            tk.Label(body, text=t('dlg_welcome_hint'), font=FT_DLG_BODY, fg=DIM,
                     bg=BG, anchor='w', justify='left',
                     wraplength=dw - 40).pack(fill='x', pady=(0, 14))

        # Step 1 - guide
        tk.Label(body, text=t('dlg_step_guide'), font=FT_DLG_H, fg=FG, bg=BG,
                 anchor='w').pack(fill='x')
        self._secondary_pill(body, t('dlg_open_guide'), self._open_guide,
                             icon='\U0001F4D6').pack(anchor='w', pady=(8, 16))

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

        btn_frame = tk.Frame(body, bg=BG)
        btn_frame.pack(fill='x', side='bottom', pady=(12, 0))
        connect_state = {'btn': None}

        def build_connect(enabled=True):
            if connect_state['btn'] is not None:
                connect_state['btn'].destroy()
            b = self._primary_pill(btn_frame, t('dlg_connect'),
                                   save_key if enabled else (lambda: None),
                                   enabled=enabled)
            b.pack(side='right')
            connect_state['btn'] = b

        self._secondary_pill(btn_frame, t('dlg_cancel'), dlg.destroy).pack(
            side='right', padx=(0, 8))

        def save_key():
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
                    dlg.after(0, lambda: on_ok(key, info))
                except Exception as e:
                    dlg.after(0, lambda: on_err(str(e)))
            def on_ok(key, info):
                if on_success is not None:
                    name = name_entry.get().strip() if name_entry else ''
                    dlg.destroy()
                    on_success(key, info, name)
                    return
                # Default: write the active account in place, creating the
                # first account on initial setup.
                a = active_account(self.cfg)
                if a is None:
                    a = {'id': _new_id(),
                         'name': info.get('name') or 'Account 1',
                         'session_key': key, 'org_id': info['org_id'],
                         'email': info.get('email', ''), 'plan': info.get('plan', '')}
                    self.cfg.setdefault('accounts', []).append(a)
                    self.cfg['active_account'] = a['id']
                else:
                    a['session_key'] = key
                    a['org_id'] = info['org_id']
                    if info.get('email'):
                        a['email'] = info['email']
                    if info.get('plan'):
                        a['plan'] = info['plan']
                mirror_active(self.cfg)
                save_cfg(self.cfg)
                dlg.destroy()
                self._clear_error()
                if is_setup:
                    self._schedule()
                self.refresh()
            def on_err(msg):
                status_lbl.config(text=f"{t('dlg_error_prefix')}: {msg}", fg=RED)
                build_connect(enabled=True)
            threading.Thread(target=detect, daemon=True).start()

        build_connect(enabled=True)
        entry.bind('<Return>', lambda e: save_key())

    def _setup_dialog(self):
        self._session_key_dialog(t('dlg_setup_title'), is_setup=True)

    # ── Accounts ─────────────────────────────────────

    def _tooltip(self, widget, text, delay=0):
        """Hover tooltip for icon-only controls. Sits above the control (below
        it when there is no room), clamped to the widget's monitor. Bound with
        add='+' so it composes with the control's own hover handlers. delay=0
        shows it immediately so the icon meaning is instant."""
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
                try:
                    state['win'].destroy()
                except Exception:
                    pass
                state['win'] = None

        def show():
            state['job'] = None
            if state['win'] is not None or not widget.winfo_exists():
                return
            tw = tk.Toplevel(widget)
            tw.overrideredirect(True)
            tw.attributes('-topmost', True)
            tk.Label(tw, text=text, font=FT_DLG_HINT, fg=FG, bg=MENU_BG,
                     padx=8, pady=4).pack()
            tw.update_idletasks()
            tipw, tiph = tw.winfo_reqwidth(), tw.winfo_reqheight()
            x = widget.winfo_rootx() + widget.winfo_width() // 2 - tipw // 2
            y = widget.winfo_rooty() - tiph - 6
            ml, mt, mr, mb = self._widget_monitor_area()
            x = max(ml + 4, min(x, mr - tipw - 4))
            if y < mt + 4:      # no room above: drop below the control
                y = widget.winfo_rooty() + widget.winfo_height() + 6
            tw.geometry(f'+{int(x)}+{int(y)}')
            state['win'] = tw

        def enter(e):
            cancel()
            state['job'] = widget.after(delay, show)
        widget.bind('<Enter>', enter, add='+')
        widget.bind('<Leave>', hide, add='+')
        widget.bind('<Button-1>', hide, add='+')
        widget.bind('<Destroy>', hide, add='+')

    def _account_bubble(self, parent, acc, size=34):
        base = parent.cget('bg')
        cv = tk.Canvas(parent, width=size, height=size, bg=base,
                       highlightthickness=0, bd=0, cursor='hand2')
        # Anti-aliased disc (Canvas ovals are jagged): render smooth via the
        # shared 4x-downscaled circle image, then overlay the initials.
        img = _dot_image(size, bubble_color(acc.get('id')))
        cv._bubble_img = img
        cv.create_image(size / 2, size / 2, image=img, anchor='center')
        cv.create_text(size / 2, size / 2 + 1, text=account_initials(acc.get('name')),
                       fill='#ffffff', font=FT_DLG_BTN_B)
        return cv

    def _account_row(self, parent, acc, rebuild):
        active = acc.get('id') == self.cfg.get('active_account')
        # Uniform background for every row: the active one is marked by a left
        # accent stripe, not a lighter panel (which read as a floating overlay).
        base = BG
        accent = bubble_color(acc.get('id'))
        row = tk.Frame(parent, bg=base, cursor='hand2')
        row.pack(fill='x', pady=1)
        cells = [row]

        stripe = tk.Frame(row, bg=(accent if active else base), width=3)
        stripe.pack(side='left', fill='y')

        bubble = self._account_bubble(row, acc)
        bubble.pack(side='left', padx=(8, 10), pady=8)
        cells.append(bubble)

        def icon_btn(glyph, cmd, tip, fg=DIM):
            b = tk.Label(row, text=glyph, font=FT_MDL2_MENU, fg=fg, bg=base,
                         cursor='hand2', padx=6, pady=8)
            b.pack(side='right')
            b.bind('<Button-1>', lambda e: (cmd(), 'break')[1])
            b.bind('<Enter>', lambda e, w=b: w.config(fg=FG))
            b.bind('<Leave>', lambda e, w=b, c=fg: w.config(fg=c))
            self._tooltip(b, tip)   # icon-only: say what it does
            cells.append(b)

        icon_btn(ICON_DELETE, lambda: self._remove_account(acc, rebuild),
                 t('dlg_remove'))
        icon_btn(ICON_EDIT, lambda: self._rename_account(acc, rebuild),
                 t('dlg_rename'))
        icon_btn(ICON_KEY, lambda: self._edit_account_key(acc, rebuild),
                 t('dlg_update_key'), fg=CLAUDE)

        txt = tk.Frame(row, bg=base)
        txt.pack(side='left', fill='x', expand=True)
        cells.append(txt)
        name_lbl = tk.Label(txt, text=acc.get('name') or '-', font=FT_DLG_BTN_B,
                            fg=FG, bg=base, anchor='w')
        name_lbl.pack(fill='x')
        cells.append(name_lbl)
        sub = ' · '.join([x for x in (acc.get('email'), acc.get('plan')) if x])
        if active:
            sub = (sub + ' · ' if sub else '') + t('dlg_active')
        sub_lbl = tk.Label(txt, text=sub, font=FT_DLG_HINT, fg=DIM,
                           bg=base, anchor='w')
        sub_lbl.pack(fill='x')
        cells.append(sub_lbl)

        def switch(e=None):
            self._switch_account(acc['id'], rebuild)

        def edit_key(e=None):
            self._edit_account_key(acc, rebuild)
            return 'break'
        for c in (row, bubble, txt, name_lbl, sub_lbl):
            c.bind('<Button-1>', switch)
            c.bind('<Double-Button-1>', edit_key)

        # Hover affordance. The stripe is not recoloured (it keeps the active
        # accent), but every widget in the row shares the same enter/leave so
        # the highlight does not flicker as the pointer crosses children: on
        # leave we repaint only when the pointer is truly outside the row, not
        # merely over one of its children.
        def paint(bg):
            for c in cells:
                try:
                    c.config(bg=bg)
                except tk.TclError:
                    pass

        def on_leave(e):
            try:
                w = row.winfo_containing(*row.winfo_pointerxy())
            except Exception:
                w = None
            if not (w is row or (w is not None and str(w).startswith(str(row) + '.'))):
                paint(base)
        for c in cells + [stripe]:
            c.bind('<Enter>', lambda e: paint(HOVER_BG), add='+')
            c.bind('<Leave>', on_leave, add='+')

    def _accounts_dialog(self):
        dw = 480
        # dh is just a floor; the dialog is resized to fit the list on every
        # rebuild (fit) so the height grows with the account count and the Add
        # button below the list stays visible without a cap or scrolling.
        dlg, body = self._build_dialog_frame(t('dlg_accounts_title'), dw, 150)

        list_frame = tk.Frame(body, bg=BG)
        list_frame.pack(fill='x')
        add_host = tk.Frame(body, bg=BG)
        add_host.pack(fill='x')
        tk.Frame(add_host, bg=BAR_BG, height=1).pack(fill='x', pady=(12, 12))
        self._secondary_pill(add_host, t('dlg_add_account'),
                             lambda: self._add_account(rebuild)).pack(anchor='w')

        def rebuild():
            for w in list_frame.winfo_children():
                w.destroy()
            accounts = self.cfg.get('accounts', [])
            if not accounts:
                tk.Label(list_frame, text=t('dlg_no_accounts'), font=FT_DLG_BODY,
                         fg=DIM, bg=BG, anchor='w').pack(fill='x', padx=8, pady=10)
            for acc in accounts:
                self._account_row(list_frame, acc, rebuild)
            self._place_dialog(dlg, dw)
        rebuild()

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
                if info.get('plan'):
                    acc['plan'] = info['plan']
                # Replace only an auto-generated placeholder name.
                if info.get('name') and re.match(r'^Account \d+$', acc.get('name', '')):
                    acc['name'] = info['name']
                save_cfg(self.cfg)
            self.root.after(0, apply)
        threading.Thread(target=work, daemon=True).start()

    def _switch_account(self, acc_id, rebuild=None):
        if self.cfg.get('active_account') == acc_id:
            return
        self.cfg['active_account'] = acc_id
        mirror_active(self.cfg)
        save_cfg(self.cfg)
        self._clear_error()
        # Reset the countdown / dot so they restart for the new account, the
        # same way the other setters do.
        self._set_pulse(False)
        self._apply_dot_phase('off')
        if self._countdown_job:
            self.root.after_cancel(self._countdown_job)
            self._countdown_job = None
        self.refresh()
        if rebuild:
            rebuild()

    def _add_account(self, rebuild):
        def on_success(key, info, name):
            n = len(self.cfg.get('accounts', [])) + 1
            acc = {'id': _new_id(),
                   'name': name or info.get('name') or f'Account {n}',
                   'session_key': key, 'org_id': info['org_id'],
                   'email': info.get('email', ''), 'plan': info.get('plan', '')}
            self.cfg.setdefault('accounts', []).append(acc)
            self.cfg['active_account'] = acc['id']
            mirror_active(self.cfg)
            save_cfg(self.cfg)
            self._clear_error()
            self.refresh()
            rebuild()
        self._session_key_dialog(t('dlg_add_account'), on_success=on_success,
                                 prefill='', show_name=True)

    def _edit_account_key(self, acc, rebuild):
        def on_success(key, info, name):
            acc['session_key'] = key
            acc['org_id'] = info['org_id']
            if name:
                acc['name'] = name
            if info.get('email'):
                acc['email'] = info['email']
            if info.get('plan'):
                acc['plan'] = info['plan']
            is_active = acc.get('id') == self.cfg.get('active_account')
            if is_active:
                mirror_active(self.cfg)
            save_cfg(self.cfg)
            self._clear_error()
            if is_active:
                self.refresh()
            rebuild()
        self._session_key_dialog(t('dlg_update_key'), on_success=on_success,
                                 prefill=acc.get('session_key', ''),
                                 show_name=True, name_prefill=acc.get('name', ''))

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
                self.cfg['active_account'] = accounts[0]['id'] if accounts else None
                mirror_active(self.cfg)
                if self.cfg.get('active_account'):
                    self.refresh()
                else:
                    self._error(t('setup_required'),
                                action_label=t('action_setup_now'),
                                action_cmd=self._setup_dialog)
            save_cfg(self.cfg)
            rebuild()
        self._confirm_dialog(t('dlg_remove'), t('dlg_remove_confirm'), on_yes)

    def _name_prompt(self, title, initial, on_ok):
        """Small single-field text prompt (account rename)."""
        dw, dh = 380, 170
        dlg, body = self._build_dialog_frame(title, dw, dh)
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

    def _confirm_dialog(self, title, msg, on_yes):
        """Small yes/no confirmation (account removal)."""
        dw, dh = 380, 180
        dlg, body = self._build_dialog_frame(title, dw, dh)
        tk.Label(body, text=msg, font=FT_DLG_BODY, fg=FG, bg=BG, anchor='w',
                 justify='left', wraplength=dw - 40).pack(fill='x')
        btns = tk.Frame(body, bg=BG)
        btns.pack(fill='x', side='bottom', pady=(16, 0))

        def confirm():
            on_yes()
            dlg.destroy()
        self._primary_pill(btns, t('dlg_remove'), confirm).pack(side='right')
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
            ctypes.windll.user32.SetWindowPos(
                hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
            self.root.attributes('-topmost', True)
            # Keep the menu above the widget when it's open.
            m = self._menu_win
            if m and m.winfo_exists():
                menu_hwnd = ctypes.windll.user32.GetParent(m.winfo_id())
                if not menu_hwnd:
                    menu_hwnd = m.winfo_id()
                ctypes.windll.user32.SetWindowPos(
                    menu_hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
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

    def _signal_quit(self, signum, frame):
        wlog(f'SIGNAL received {signum} -> saving and exiting')
        self._save_geometry()
        sys.exit(0)

    def _quit(self):
        wlog('QUIT   _quit() called')
        self._close_menu()
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
    """Ensure only one widget instance runs. Returns mutex handle or exits."""
    try:
        mutex = ctypes.windll.kernel32.CreateMutexW(None, True, 'ClaudeUsageWidget_SingleInstance')
        if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            # Another instance is running - bring it to front and exit
            hwnd = ctypes.windll.user32.FindWindowW(None, 'Claude Usage')
            if hwnd:
                ctypes.windll.user32.SetForegroundWindow(hwnd)
            sys.exit(0)
        return mutex
    except Exception:
        return None


if __name__ == '__main__':
    _mutex = None if os.environ.get('CLAUDE_USAGE_DEV') == '1' else _single_instance()

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
