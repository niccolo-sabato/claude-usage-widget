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
import ssl
import time
import ctypes
import signal
import atexit
import tempfile
import threading
import subprocess
import webbrowser
import urllib.request
import urllib.error
import tkinter as tk
from datetime import datetime, timezone
from PIL import Image, ImageDraw, ImageTk

# ─── DPI awareness ──────────────────────────────────
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

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

def _find_res(name):
    """Locate a bundled resource: try _RES root, then _RES/assets, then EXE_DIR/assets."""
    for base in (_RES, os.path.join(_RES, 'assets'), os.path.join(EXE_DIR, 'assets')):
        p = os.path.join(base, name)
        if os.path.exists(p):
            return p
    return os.path.join(_RES, name)  # fallback (may not exist)

ICO = _find_res('claude.ico')
ICO_BAR = _find_res('icon-bar.png')
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
PCT_FG   = '#ffffff'
MENU_BG  = '#2c2c2a'

# ─── App ────────────────────────────────────────────
APP_VERSION = '2.8.8'

# ─── Auto-update ────────────────────────────────────
UPDATE_REPO = 'niccolo-sabato/claude-usage-widget'
UPDATE_API_URL = f'https://api.github.com/repos/{UPDATE_REPO}/releases/latest'
UPDATE_RELEASES_URL = f'https://github.com/{UPDATE_REPO}/releases'
UPDATE_ASSET_NAME = 'ClaudeUsage-Setup.exe'
UPDATE_CHECK_INTERVAL_S = 24 * 3600       # default throttle between auto-checks
UPDATE_STARTUP_DELAY_MS = 10_000          # check 10s after widget ready
UPDATE_CHANGELOG_MAX_CHARS = 900          # truncate release body shown in dialog

# ─── Layout ──────────────────────────────────────────
DEF_W    = 280
MIN_W    = 210
MIN_H_E  = 46   # essential mode minimum height
MIN_H_N  = 90   # normal mode minimum height
PAD      = 12
BAR_H    = 16
TITLE_H  = 28
REFRESH  = 180_000  # 3 minutes

# ─── Fonts ───────────────────────────────────────────
FT       = ('Segoe UI', 9)
FT_B     = ('Segoe UI', 9, 'bold')
FT_S     = ('Segoe UI', 8)
FT_BTN   = ('Segoe UI', 11)
FT_EMOJI = ('Segoe UI Emoji', 10)
FT_DOT   = ('Segoe UI', 10)
FT_BAR   = ('Segoe UI', 9, 'bold')

# ─── Dialog / menu design system ─────────────────────
FT_DLG_TITLE = ('Segoe UI Semibold', 10)   # Dialog title bars
FT_DLG_H     = ('Segoe UI', 11, 'bold')    # Section headers inside dialogs
FT_DLG_BODY  = ('Segoe UI', 10)            # Body text, entries
FT_DLG_HINT  = ('Segoe UI', 9)             # Hints / status lines
FT_DLG_BTN   = ('Segoe UI', 10)            # Secondary pill button text
FT_DLG_BTN_B = ('Segoe UI', 10, 'bold')    # Primary pill button text
FT_MENU      = ('Segoe UI', 10)            # Menu row text
FT_EMOJI_11  = ('Segoe UI Emoji', 11)      # Emoji icons in dialogs/menus

# Surface / state colors used by dialogs and menus (complement the theme)
SOFT_BG    = '#2e2e2c'   # secondary pill button / card surface
SOFT_BG_HV = '#363634'   # secondary pill hover
PRIMARY_HV = '#E08060'   # primary pill hover
FOCUS_RING = '#C8652E'   # darker Claude for focused entry outline
CLOSE_HV   = '#3a1818'   # title bar close button hover tint

# Pill button padding presets — every dialog/menu button uses these so sizes
# stay visually consistent across the app.
PILL_PAD_PRIMARY_X   = 22
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
MAX_LOG_LINES = 200

_log_count = 0

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

# ─── API ─────────────────────────────────────────────
API_URL  = 'https://claude.ai/api/organizations/{}/usage'

# ─── i18n ───────────────────────────────────────────
LANG = {
    'en': {
        'current_session': 'Current Session',
        'all_models': 'All models (7d)',
        'sonnet_only': 'Sonnet only (7d)',
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
        # Menu
        'menu_refresh': 'Refresh',
        'menu_mode_normal': 'Normal mode',
        'menu_mode_essential': 'Essential mode',
        'menu_renew': 'Session key\u2026',
        'menu_open_config': 'Open config.json',
        'menu_open_claude': 'Go to Claude Usage',
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
        'update_dlg_subtitle': 'Version {version} is available (you have {current}).',
        'update_dlg_changelog': "What's new:",
        'update_dlg_install': 'Install now',
        'update_dlg_cancel': 'Cancel',
        'update_dlg_no_changelog': '(no release notes)',
        'update_dlg_downloading': 'Downloading {percent}% ({done} / {total})',
        'update_dlg_launching': 'Launching installer\u2026',
        'update_dlg_failed': 'Update failed: {error}',
        'update_dlg_open_page': 'Open release page',
        'update_check_checking': 'Checking for updates\u2026',
        'update_check_uptodate': 'You are on the latest version (v{version}).',
        'update_check_failed': 'Could not reach GitHub. Try again later.',
        'update_check_no_asset': 'New version available but no installer asset was found.',
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
        # Kept for legacy references — consolidated into dlg_step_* above
        'dlg_howto': 'Where do I find my session key?',
        'dlg_paste_here': 'Paste your session key below',
    },
    'it': {
        'current_session': 'Sessione Corrente',
        'all_models': 'Tutti i modelli (7gg)',
        'sonnet_only': 'Solo Sonnet (7gg)',
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
        'menu_refresh': 'Aggiorna',
        'menu_mode_normal': 'Modalit\u00e0 normale',
        'menu_mode_essential': 'Modalit\u00e0 essential',
        'menu_renew': 'Session key\u2026',
        'menu_open_config': 'Apri config.json',
        'menu_open_claude': 'Vai a Claude Usage',
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
        'update_dlg_subtitle': 'La versione {version} \u00e8 disponibile (hai la {current}).',
        'update_dlg_changelog': 'Novit\u00e0:',
        'update_dlg_install': 'Installa ora',
        'update_dlg_cancel': 'Annulla',
        'update_dlg_no_changelog': '(nessuna nota di rilascio)',
        'update_dlg_downloading': 'Download {percent}% ({done} / {total})',
        'update_dlg_launching': 'Avvio installer\u2026',
        'update_dlg_failed': 'Aggiornamento fallito: {error}',
        'update_dlg_open_page': 'Apri pagina rilascio',
        'update_check_checking': 'Controllo aggiornamenti\u2026',
        'update_check_uptodate': 'Versione gi\u00e0 aggiornata (v{version}).',
        'update_check_failed': 'Impossibile contattare GitHub. Riprova pi\u00f9 tardi.',
        'update_check_no_asset': 'Nuova versione disponibile ma non trovo l\u2019installer.',
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
        'not_available': '\u5229\u7528\u4e0d\u53ef',
        'not_used': '\u672a\u4f7f\u7528',
        'soon': '\u307e\u3082\u306a\u304f',
        'reset_prefix': '\u30ea\u30bb\u30c3\u30c8',
        'days': ['\u6708', '\u706b', '\u6c34', '\u6728', '\u91d1', '\u571f', '\u65e5'],
        'unit_d': '\u65e5', 'unit_h': '\u6642\u9593', 'unit_min': '\u5206',
        'setup_required': 'Claude.ai \u306b\u63a5\u7d9a\u3059\u308b\u306b\u306f\u30bb\u30c3\u30b7\u30e7\u30f3\u30ad\u30fc\u304c\u5fc5\u8981\u3067\u3059\u3002',
        'session_expired': '\u30bb\u30c3\u30b7\u30e7\u30f3\u6709\u52b9\u671f\u5207\u308c\u3002\u30bb\u30c3\u30b7\u30e7\u30f3\u30ad\u30fc\u3092\u66f4\u65b0\u3057\u3066\u304f\u3060\u3055\u3044\u3002',
        'error': '\u30a8\u30e9\u30fc',
        'empty_response': '\u5fdc\u7b54\u304c\u7a7a\u3067\u3059',
        'no_org': '\u7d44\u7e54\u304c\u898b\u3064\u304b\u308a\u307e\u305b\u3093',
        'session_expired_short': '\u30bb\u30c3\u30b7\u30e7\u30f3\u6709\u52b9\u671f\u5207\u308c',
        'action_setup_now': '\u4eca\u3059\u3050\u8a2d\u5b9a',
        'action_renew_now': '\u30bb\u30c3\u30b7\u30e7\u30f3\u66f4\u65b0',
        'menu_refresh': '\u66f4\u65b0',
        'menu_mode_normal': '\u901a\u5e38\u30e2\u30fc\u30c9',
        'menu_mode_essential': '\u30b7\u30f3\u30d7\u30eb\u30e2\u30fc\u30c9',
        'menu_renew': '\u30bb\u30c3\u30b7\u30e7\u30f3\u30ad\u30fc\u2026',
        'menu_open_config': 'config.json\u3092\u958b\u304f',
        'menu_open_claude': 'Claude Usage\u306b\u79fb\u52d5',
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
        'update_dlg_subtitle': '\u30d0\u30fc\u30b8\u30e7\u30f3 {version} \u304c\u5229\u7528\u53ef\u80fd\u3067\u3059\uff08\u73fe\u5728 {current}\uff09\u3002',
        'update_dlg_changelog': '\u5909\u66f4\u70b9:',
        'update_dlg_install': '\u4eca\u3059\u3050\u30a4\u30f3\u30b9\u30c8\u30fc\u30eb',
        'update_dlg_cancel': '\u30ad\u30e3\u30f3\u30bb\u30eb',
        'update_dlg_no_changelog': '\uff08\u30ea\u30ea\u30fc\u30b9\u30ce\u30fc\u30c8\u306a\u3057\uff09',
        'update_dlg_downloading': '\u30c0\u30a6\u30f3\u30ed\u30fc\u30c9\u4e2d {percent}%\uff08{done} / {total}\uff09',
        'update_dlg_launching': '\u30a4\u30f3\u30b9\u30c8\u30fc\u30e9\u30fc\u3092\u8d77\u52d5\u4e2d\u2026',
        'update_dlg_failed': '\u66f4\u65b0\u306b\u5931\u6557: {error}',
        'update_dlg_open_page': '\u30ea\u30ea\u30fc\u30b9\u30da\u30fc\u30b8\u3092\u958b\u304f',
        'update_check_checking': '\u66f4\u65b0\u3092\u78ba\u8a8d\u4e2d\u2026',
        'update_check_uptodate': '\u6700\u65b0\u30d0\u30fc\u30b8\u30e7\u30f3\u3067\u3059\uff08v{version}\uff09\u3002',
        'update_check_failed': 'GitHub \u306b\u63a5\u7d9a\u3067\u304d\u307e\u305b\u3093\u3002\u5f8c\u3067\u518d\u8a66\u884c\u3057\u3066\u304f\u3060\u3055\u3044\u3002',
        'update_check_no_asset': '\u65b0\u3057\u3044\u30d0\u30fc\u30b8\u30e7\u30f3\u306f\u3042\u308a\u307e\u3059\u304c\u3001\u30a4\u30f3\u30b9\u30c8\u30fc\u30e9\u30fc\u304c\u898b\u3064\u304b\u308a\u307e\u305b\u3093\u3002',
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
        return cfg
    return {}


def save_cfg(data):
    with open(CFG, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)


def bar_color(pct, accent):
    if pct >= 90:
        return RED
    if pct >= 75:
        return ORANGE
    return accent


def format_reset(iso_str):
    """Format reset time: 'reset 18:00 (3h 26min)' or 'reset Sat 11:00 (2d 5h)'."""
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
    time_str = f'{local:%H:%M}'
    prefix = t('reset_prefix')
    if local.date() == now_local.date():
        return f'{prefix} {time_str} ({cd})'
    days = t('days')
    return f'{prefix} {days[local.weekday()]} {time_str} ({cd})'


def pill(cv, x, y, w, h, color):
    """Draw a pill-shaped bar — ovals + rect, outline=fill to seal seams."""
    r = h / 2
    cv.create_oval(x, y, x + h, y + h, fill=color, outline=color, width=1)
    cv.create_oval(x + w - h, y, x + w, y + h, fill=color, outline=color, width=1)
    if w > h:
        cv.create_rectangle(x + r, y, x + w - r, y + h, fill=color, outline=color, width=0)


_PILL_IMAGE_CACHE = {}


def _hex_to_rgb(color):
    h = color.lstrip('#')
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


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

    x = padx
    cy = btn_h / 2
    if icon:
        cv.create_text(x, cy - 1, text=icon, fill=fg,
                       font=icon_font or font, anchor='w')
        x += icon_w + gap
    cv.create_text(x, cy, text=text, fill=fg, font=font, anchor='w')

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
# API
# ═══════════════════════════════════════════════════════

def fetch_org_id(session_key):
    """Auto-detect org_id from session key via /api/organizations."""
    ua = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
          '(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36')
    result = subprocess.run(
        ['curl', '-s',
         '-H', f'Cookie: sessionKey={session_key}',
         '-H', f'User-Agent: {ua}',
         '-H', 'anthropic-client-platform: web_claude_ai',
         'https://claude.ai/api/organizations'],
        capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW
    )
    if result.returncode != 0:
        raise RuntimeError(f'curl: {result.stderr.strip()}')
    body = result.stdout.strip()
    if not body:
        raise RuntimeError(t('empty_response'))
    orgs = json.loads(body)
    if isinstance(orgs, list) and len(orgs) > 0:
        return orgs[0].get('uuid') or orgs[0].get('id')
    raise RuntimeError(t('no_org'))


def fetch_usage(cfg):
    """Fetch usage data from Claude.ai API via curl."""
    url = API_URL.format(cfg['org_id'])
    cookie = f"sessionKey={cfg['session_key']}; lastActiveOrg={cfg['org_id']}"
    ua = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
          '(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36')
    result = subprocess.run(
        ['curl', '-s', '-D', '-',
         '-H', f'Cookie: {cookie}',
         '-H', f'User-Agent: {ua}',
         '-H', 'anthropic-client-platform: web_claude_ai',
         url],
        capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW
    )
    if result.returncode != 0:
        raise RuntimeError(f'curl: {result.stderr.strip()}')
    parts = result.stdout.split('\r\n\r\n', 1)
    if len(parts) < 2:
        parts = result.stdout.split('\n\n', 1)
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
        cfg['session_key'] = km.group(1)
        save_cfg(cfg)
    return json.loads(body)


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

    def __init__(self, parent, label, accent):
        self.accent = accent
        self._pct = 0
        self._color = accent
        self._compact = False
        self._cd_txt = ''  # countdown text appended to pct

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

    def update(self, pct, resets_at):
        if pct is None:
            self._pct = 0
            self._color = BAR_BG
            self.lbl_sub.config(text=t('not_available'))
            self._draw(self.cv.winfo_width())
            return
        self._pct = max(0, min(100, pct))
        self._color = bar_color(self._pct, self.accent)
        cd = format_reset(resets_at)
        if cd:
            self.lbl_sub.config(text=cd)
        elif self._pct == 0:
            self.lbl_sub.config(text=t('not_used'))
        else:
            self.lbl_sub.config(text='')
        self._draw(self.cv.winfo_width())

    def _draw(self, w):
        if w < 2:
            return
        self.cv.delete('all')
        pill(self.cv, 0, 0, w, BAR_H, BAR_BG)
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


# ═══════════════════════════════════════════════════════
# Widget
# ═══════════════════════════════════════════════════════

class Widget:

    def __init__(self):
        self.cfg = load_cfg()
        # Load language from config, default English
        set_lang(self.cfg.get('language', 'en'))
        self.root = tk.Tk()
        self._job = None
        self._countdown_job = None
        self._topmost_job = None
        self._countdown_secs = 0
        self._last_time = ''
        self._resets_at = []  # ISO reset times — trigger refresh when reached
        self._dx = self._dy = 0
        self._expanded = False
        self._essential = False
        self._rs_x = self._rs_y = self._rs_w = self._rs_h = 0
        self._menu_win = None

        self.root.title('Claude Usage')
        self.root.overrideredirect(True)
        self.root.attributes('-topmost', True)
        self.root.attributes('-alpha', 0.94)
        self.root.configure(bg=BG)

        try:
            self.root.iconbitmap(ICO)
        except Exception:
            pass

        # Make visible in Win+Tab via WS_EX_APPWINDOW on the real window
        self.root.update_idletasks()
        self._make_wintab_visible()

        self._bar_icon = None
        try:
            self._bar_icon = tk.PhotoImage(file=ICO_BAR)
        except Exception:
            pass

        self._build()

        w = self.cfg.get('width', DEF_W)
        x = self.cfg.get('x', 100)
        y = self.cfg.get('y', 100)
        # Validate the saved position against the FULL virtual desktop —
        # winfo_vroot* can start at negative coordinates when secondary
        # monitors extend to the left or above the primary. The previous
        # check was `x < -w + 50` which resets any legitimate position on a
        # left-side secondary display back to (100, 100).
        vrx = self.root.winfo_vrootx()
        vry = self.root.winfo_vrooty()
        vrw = self.root.winfo_vrootwidth()
        vrh = self.root.winfo_vrootheight()
        # Require at least 50px of the widget to be inside the virtual desktop.
        if x + w < vrx + 50 or x > vrx + vrw - 50:
            x = 100
        if y + 20 < vry or y > vry + vrh - 50:
            y = 100
        self.root.geometry(f'+{x}+{y}')
        self.root.update_idletasks()
        rh = self.root.winfo_reqheight()
        self.root.geometry(f'{w}x{rh}+{x}+{y}')
        self.root.minsize(MIN_W, 0)

        self.root.after(50, lambda: dwm_round(self.root, shadow=False))
        # Force above taskbar — re-assert topmost every 500ms + on focus/visibility events
        self._keep_topmost()
        self.root.bind('<FocusOut>', lambda e: self.root.after(50, self._force_topmost))
        self.root.bind('<Visibility>', lambda e: self.root.after(50, self._force_topmost))

        # Restore essential mode if it was active when last closed
        if self.cfg.get('essential', False):
            self.root.after(100, self._restore_essential)

        if self.cfg.get('session_key') and self.cfg.get('org_id'):
            self.refresh()
            self._schedule()
        else:
            self._error(t('setup_required'),
                        action_label=t('action_setup_now'),
                        action_cmd=self._setup_dialog)
            self.root.after(300, self._setup_dialog)

        self._update_banner = None
        self._schedule_update_check()

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
        self.tb.bind('<ButtonRelease-1>', self._save_geometry)

        if self._bar_icon:
            ico = tk.Label(self.tb, image=self._bar_icon, bg=BG_TITLE, padx=4)
        else:
            ico = tk.Label(self.tb, text=' \u2731', font=('Segoe UI', 11),
                           fg=CLAUDE, bg=BG_TITLE)
        ico.pack(side='left', padx=(6, 0))
        ico.bind('<Button-1>', self._drag_start)
        ico.bind('<B1-Motion>', self._drag_move)
        ico.bind('<ButtonRelease-1>', self._save_geometry)

        title = tk.Label(self.tb, text='Claude Usage', font=FT_B, fg=FG, bg=BG_TITLE)
        title.pack(side='left', padx=(2, 0))
        title.bind('<Button-1>', self._drag_start)
        title.bind('<B1-Motion>', self._drag_move)
        title.bind('<ButtonRelease-1>', self._save_geometry)

        # Close (rightmost)
        self.btn_x = tk.Label(self.tb, text=' \u2715 ', font=('Segoe UI', 10),
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

        # Refresh button
        self.btn_r = tk.Label(self.tb, text=' \u21bb ', font=FT_BTN,
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
        self.lbl_time.bind('<ButtonRelease-1>', self._save_geometry)

        # Separator
        self.sep = tk.Frame(self.main, bg=BAR_BG, height=1)
        self.sep.pack(fill='x')

        # ── Content ──
        self.content = tk.Frame(self.main, bg=BG)
        self.content.pack(fill='both', expand=True)

        self.s_session = Section(self.content, t('current_session'), CLAUDE)

        # Expandable sections
        self.extra_frame = tk.Frame(self.content, bg=BG)
        self.s_weekly = Section(self.extra_frame, t('all_models'), BLUE)
        self.s_sonnet = Section(self.extra_frame, t('sonnet_only'), PURPLE)

        # Bottom spacer
        self.bottom_pad = tk.Frame(self.content, bg=BG, height=6)
        self.bottom_pad.pack(fill='x')

        # ── Overlay elements (place() on main — always at window corners) ──

        # Expand dot — bottom-left
        self.btn_expand = tk.Label(self.main, text='\u25cf', font=FT_DOT,
                                   fg=DOT_W_D, bg=BG, cursor='hand2',
                                   bd=0, highlightthickness=0, padx=0, pady=0)
        self.btn_expand.place(x=6, rely=1.0, y=-4, anchor='sw')
        self.btn_expand.bind('<Button-1>', lambda e: self._toggle_expand())
        self.btn_expand.bind('<Enter>', lambda e: self.btn_expand.config(fg=DOT_W_H))
        self.btn_expand.bind('<Leave>', lambda e: self.btn_expand.config(
            fg=DOT_W if self._expanded else DOT_W_D))

        # Resize dot — bottom-right (ALWAYS stays here)
        self.btn_resize = tk.Label(self.main, text='\u25cf', font=FT_DOT,
                                   fg=OCHRE, bg=BG, cursor='hand2',
                                   bd=0, highlightthickness=0, padx=0, pady=0)
        self.btn_resize.place(relx=1.0, x=-6, rely=1.0, y=-4, anchor='se')
        self.btn_resize.bind('<Button-1>', self._resize_start)
        self.btn_resize.bind('<B1-Motion>', self._resize_move)
        self.btn_resize.bind('<ButtonRelease-1>', self._save_geometry)
        self.btn_resize.bind('<Double-Button-1>', lambda e: self._toggle_essential())
        self.btn_resize.bind('<Enter>', lambda e: self.btn_resize.config(fg='#E06030'))
        self.btn_resize.bind('<Leave>', lambda e: self.btn_resize.config(fg=OCHRE))

        # Essential mode controls — dynamic stack, right-aligned
        # Visual order left to right: ✕ ↻ HH:MM [resize dot]
        self.ess_bar = tk.Frame(self.main, bg=BG)
        self.ess_close = tk.Label(self.ess_bar, text='\u2715', font=('Segoe UI', 9),
                                  fg=DIM, bg=BG, cursor='hand2',
                                  bd=0, highlightthickness=0, padx=4, pady=0)
        self.ess_close.pack(side='left')
        self.ess_close.bind('<Button-1>', lambda e: self._quit())
        self.ess_close.bind('<Enter>', lambda e: self.ess_close.config(fg=RED))
        self.ess_close.bind('<Leave>', lambda e: self.ess_close.config(fg=DIM))
        self.ess_refresh = tk.Label(self.ess_bar, text='\u21bb', font=FT_BTN,
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
        if step > total:
            self.root.geometry(f'{self.root.winfo_width()}x{end_h}+{self.root.winfo_x()}+{end_y}')
            self.root.update_idletasks()
            cover.destroy()

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
        start_h = self.root.winfo_height()
        start_y = self.root.winfo_y()
        bottom = start_y + start_h

        if not self._expanded:
            self._collapsed_h = start_h
            self._collapsed_y = start_y

        self._expanded = not self._expanded
        if self._expanded:
            self.bottom_pad.pack_forget()
            self.extra_frame.pack(fill='x')
            if self._essential:
                self.bottom_pad.config(height=24)
            else:
                self.bottom_pad.config(height=6)
            self.bottom_pad.pack(fill='x')
            self.btn_expand.config(fg=DOT_W)
            if self._essential:
                self.s_session.set_compact(False)
                self.s_weekly.set_compact(False)
                self.s_sonnet.set_compact(False)
        else:
            self.extra_frame.pack_forget()
            self.bottom_pad.config(height=6)
            self.btn_expand.config(fg=DOT_W_D)
            if self._essential:
                self.s_session.set_compact(True)

        self.root.update_idletasks()
        if self._expanded:
            end_h = self.root.winfo_reqheight()
            end_y = bottom - end_h
        else:
            end_h = self._collapsed_h
            end_y = self._collapsed_y
        # Cover content, reset to start, animate
        self.root.geometry(f'{self.root.winfo_width()}x{start_h}+{self.root.winfo_x()}+{start_y}')
        self._start_anim(start_y, start_h, end_y, end_h)

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
            self.extra_frame.pack_forget()
            self.bottom_pad.config(height=6)
            self.s_session.set_compact(True)
            self.ess_bar.place(relx=1.0, x=-18, rely=1.0, y=-1, anchor='se')
            self._bind_drag(self.content)
            self._bind_drag(self.s_session.frame)
            for child in self.s_session.frame.winfo_children():
                if not isinstance(child, tk.Canvas):
                    self._bind_drag(child)
        else:
            self.s_session.set_compact(False)
            self.s_weekly.set_compact(False)
            self.s_sonnet.set_compact(False)
            self.ess_bar.place_forget()
            self.content.pack_forget()
            self.tb.pack(fill='x')
            self.sep.pack(fill='x')
            self.content.pack(fill='both', expand=True)
            self._unbind_drag(self.content)
        self.root.update_idletasks()
        end_h = self.root.winfo_reqheight()
        end_y = bottom - end_h
        self._update_minsize()
        # Cover content, reset to start, animate
        self.root.geometry(f'{self.root.winfo_width()}x{start_h}+{self.root.winfo_x()}+{start_y}')
        self._start_anim(start_y, start_h, end_y, end_h)

    def _restore_essential(self):
        """Restore essential mode on startup — no animation, direct layout."""
        wlog(f'MODE   toggle essential: {self._essential} -> {not self._essential}')
        self._essential = True
        self.tb.pack_forget()
        self.sep.pack_forget()
        self.extra_frame.pack_forget()
        self.s_session.set_compact(True)
        self.ess_bar.place(relx=1.0, x=-18, rely=1.0, y=-1, anchor='se')
        self._bind_drag(self.content)
        self._bind_drag(self.s_session.frame)
        for child in self.s_session.frame.winfo_children():
            if not isinstance(child, tk.Canvas):
                self._bind_drag(child)
        # Apply saved position directly
        w = self.cfg.get('width', DEF_W)
        x = self.cfg.get('x', 100)
        y = self.cfg.get('y', 100)
        self.root.update_idletasks()
        rh = self.root.winfo_reqheight()
        self.root.geometry(f'{w}x{rh}+{x}+{y}')
        self.root.attributes('-alpha', 0.94)
        self._update_minsize()

    def _bind_drag(self, w):
        w.bind('<Button-1>', self._drag_start)
        w.bind('<B1-Motion>', self._drag_move)
        w.bind('<ButtonRelease-1>', self._save_geometry)
        w.bind('<Button-3>', self._show_menu)

    def _unbind_drag(self, w):
        w.unbind('<Button-1>')
        w.unbind('<B1-Motion>')
        w.unbind('<ButtonRelease-1>')
        w.unbind('<Button-3>')

    def _update_minsize(self):
        """Update minimum width to avoid clipping sub-label + controls."""
        self.root.update_idletasks()
        sub_w = self.s_session.lbl_sub.winfo_reqwidth() + PAD + 6
        ess_w = self.ess_bar.winfo_reqwidth() + 20 if self._essential else 20
        needed = max(MIN_W, sub_w + ess_w)
        self.root.minsize(needed, 0)

    def _auto_height(self):
        self.root.update_idletasks()
        x, y = self.root.winfo_x(), self.root.winfo_y()
        w = self.root.winfo_width()
        new_h = self.root.winfo_reqheight()
        self.root.geometry(f'{w}x{new_h}+{x}+{y}')

    # ── Resize via dot drag ────────────────────────

    def _resize_start(self, e):
        self._rs_x = e.x_root
        self._rs_y = e.y_root
        self._rs_w = self.root.winfo_width()
        self._rs_h = self.root.winfo_height()

    def _resize_move(self, e):
        w = max(MIN_W, self._rs_w + (e.x_root - self._rs_x))
        x, y = self.root.winfo_x(), self.root.winfo_y()
        self.root.update_idletasks()
        h = self.root.winfo_reqheight()
        self.root.geometry(f'{w}x{h}+{x}+{y}')
        self._update_region()

    # ── Data ─────────────────────────────────────────

    def refresh(self):
        if self._countdown_job:
            self.root.after_cancel(self._countdown_job)
            self._countdown_job = None
        self.btn_r.config(fg=BLUE)
        self.s_session.set_countdown('\u2022\u2022\u2022')
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
        ss = d.get('seven_day_sonnet')
        self.s_sonnet.update(ss['utilization'] if ss else None,
                             ss.get('resets_at') if ss else None)
        # Collect reset times for instant refresh when they arrive
        self._resets_at = []
        for section in (fh, sd, ss):
            if section and section.get('resets_at'):
                try:
                    t = datetime.fromisoformat(section['resets_at'])
                    self._resets_at.append(t)
                except (ValueError, TypeError):
                    pass
        now = f'{datetime.now():%H:%M}'
        self._last_time = now
        self.btn_r.config(fg=DIM)
        wlog(f'FETCH  ok: session={fh["utilization"] if fh else "?"} weekly={sd["utilization"] if sd else "?"} sonnet={ss["utilization"] if ss else "?"}')
        self._save_geometry()  # auto-save on each refresh (protection against kill)
        self._start_countdown()
        self._update_minsize()

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

    def _tick_countdown(self):
        """Update countdown with adaptive cadence:
          s >  60: tick every 30s
          30 < s <= 60: tick every 10s (so display updates at 60, 50, 40, 30)
          s <= 30: tick every 1s
        """
        now_utc = datetime.now(timezone.utc)
        for t in self._resets_at:
            if t <= now_utc:
                wlog('RESET  reset time reached, refreshing now')
                self._resets_at = []
                self._countdown_job = None
                self.refresh()
                return
        s = self._countdown_secs
        self._update_clock()
        if s > 0:
            if s >= 60:
                m, sec = divmod(s, 60)
                cd_txt = f'{self._last_time} ({m}min {sec:02d}s)'
            else:
                cd_txt = f'{self._last_time} ({s}s)'
            self.s_session.set_countdown(cd_txt)
            if s > 60:
                # Snap to the next 30s tick boundary on the way down to 60.
                skip = min(30, s - 60) if s > 60 else 30
                self._countdown_secs -= skip
                self._countdown_job = self.root.after(skip * 1000, self._tick_countdown)
            elif s > 30:
                # 60 -> 50 -> 40 -> 30 — one tick every 10s.
                skip = min(10, s - 30) if s > 30 else 10
                self._countdown_secs -= skip
                self._countdown_job = self.root.after(skip * 1000, self._tick_countdown)
            else:
                self._countdown_secs -= 1
                self._countdown_job = self.root.after(1000, self._tick_countdown)
        else:
            self.s_session.set_countdown('')
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
        self.s_session.set_countdown(t('error'))
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

    def _save_geometry(self, e=None):
        """Save current position, size, and mode to config."""
        try:
            self.cfg['x'] = self.root.winfo_x()
            self.cfg['y'] = self.root.winfo_y()
            self.cfg['width'] = self.root.winfo_width()
            self.cfg['height'] = self.root.winfo_height()
            self.cfg['expanded'] = self._expanded
            self.cfg['essential'] = self._essential
            save_cfg(self.cfg)
        except Exception as ex:
            wlog(f'SAVE   save_geometry error: {ex}')

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

    def _place_popup(self, dw, dh, prefer='above'):
        """Position a popup near the widget, preferring above/below, clamped
        to the virtual desktop so it lands on the same monitor as the widget.
        """
        vx, vy, vw, vh = self._virtual_bounds()
        wx = self.root.winfo_x() + (self.root.winfo_width() - dw) // 2
        widget_top = self.root.winfo_y()
        widget_bottom = widget_top + self.root.winfo_height()
        if prefer == 'above':
            wy = widget_top - dh - SCREEN_MARGIN
            if wy < vy + SCREEN_MARGIN:
                wy = widget_bottom + SCREEN_MARGIN
        else:
            wy = widget_bottom + SCREEN_MARGIN
            if wy + dh > vy + vh - TASKBAR_GAP:
                wy = widget_top - dh - SCREEN_MARGIN
        wx = max(vx + SCREEN_MARGIN, min(wx, vx + vw - dw - SCREEN_MARGIN))
        wy = max(vy + SCREEN_MARGIN, min(wy, vy + vh - dh - TASKBAR_GAP))
        return wx, wy

    def _place_submenu(self, dw, dh):
        """Right-aligned submenu anchored to the widget, virtual-desktop safe."""
        vx, vy, vw, vh = self._virtual_bounds()
        wx = self.root.winfo_rootx() + self.root.winfo_width() - dw
        widget_bottom = self.root.winfo_rooty() + self.root.winfo_height()
        if widget_bottom + dh > vy + vh - TASKBAR_GAP:
            wy = self.root.winfo_rooty() - dh - 2
        else:
            wy = self.root.winfo_rooty() + TITLE_H + 2
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

    def _primary_pill(self, parent, text, cmd, enabled=True):
        """Primary pill button (Claude orange). Fixed size across all dialogs."""
        if enabled:
            return make_pill_button(
                parent, text=text, font=FT_DLG_BTN_B,
                fg='#FFFFFF', bg=CLAUDE, hover_bg=PRIMARY_HV, cmd=cmd,
                padx=PILL_PAD_PRIMARY_X, pady=PILL_PAD_PRIMARY_Y)
        return make_pill_button(
            parent, text=text, font=FT_DLG_BTN_B,
            fg=DIM, bg=BAR_BG, hover_bg=BAR_BG, cmd=lambda: None,
            padx=PILL_PAD_PRIMARY_X, pady=PILL_PAD_PRIMARY_Y)

    def _secondary_pill(self, parent, text, cmd, icon=None):
        """Secondary pill button (soft surface). Fixed size across all dialogs."""
        return make_pill_button(
            parent, text=text, font=FT_DLG_BTN,
            fg=FG, bg=SOFT_BG, hover_bg=SOFT_BG_HV, cmd=cmd,
            icon=icon, icon_font=FT_EMOJI_11 if icon else None,
            padx=PILL_PAD_SECONDARY_X, pady=PILL_PAD_SECONDARY_Y)

    def _build_dialog_frame(self, title, dw, dh):
        """Create a Toplevel with the standard chrome. Returns (dlg, body).

        All dialogs use this: same title bar, same padding, same rounded
        corners, same screen-clamped positioning.
        """
        dlg = tk.Toplevel(self.root)
        dlg.title(title)
        dlg.configure(bg=BG)
        dlg.overrideredirect(True)
        dlg.attributes('-topmost', True)
        dlg.resizable(False, False)

        wx, wy = self._place_popup(dw, dh)
        dlg.geometry(f'{dw}x{dh}+{wx}+{wy}')
        dlg.update_idletasks()
        dlg.after(50, lambda: dwm_round(dlg))

        self._build_titlebar(dlg, title)

        body = tk.Frame(dlg, bg=BG)
        body.pack(fill='both', expand=True,
                  padx=DLG_PAD_X, pady=(DLG_PAD_TOP, DLG_PAD_BTM))
        dlg.bind('<Escape>', lambda e: dlg.destroy())
        return dlg, body

    # ── W11 Styled Menu ─────────────────────────────

    def _show_menu(self, e=None):
        if self._menu_win and self._menu_win.winfo_exists():
            self._menu_win.destroy()
            self._menu_win = None
            return

        m = tk.Toplevel(self.root)
        self._menu_win = m
        m.overrideredirect(True)
        m.attributes('-topmost', True)
        m.configure(bg=MENU_BG)

        mode_label = t('menu_mode_normal') if self._essential else t('menu_mode_essential')
        lang_names = {'en': 'English', 'it': 'Italiano', 'ja': '\u65e5\u672c\u8a9e'}
        cur_lang = lang_names.get(_current_lang, 'English')
        lang_label = f"{t('menu_language')}: {cur_lang}"
        cur_secs = self.cfg.get('refresh_ms', REFRESH) // 1000
        interval_label = f"{t('menu_refresh_interval')} ({cur_secs}s)"
        items = [
            ('\u21bb',        FT_BTN,   t('menu_refresh'),         self.refresh),
            ('\u21F5',        FT_EMOJI, mode_label,                self._toggle_essential),
            None,
            ('\u23F3',        FT_EMOJI, interval_label,            self._show_interval_dialog),
            ('\U0001F5DD',    FT_EMOJI, t('menu_renew'),           self._renew_session),
            ('\u2197\uFE0F',  FT_EMOJI, t('menu_open_claude'),     self._open_claude_usage),
            ('{ }',           FT_EMOJI, t('menu_open_config'),     self._open_config),
            ('\U0001F30D',    FT_EMOJI, lang_label,                self._show_language_menu),
            None,
            ('\u2B06',        FT_EMOJI, t('menu_check_updates'),   self._check_updates_manual),
            ('\u2715',        FT,       t('menu_quit'),            self._quit),
            None,
            (None, None, f'v{APP_VERSION}', None),
        ]

        ROW_PADY = 5
        ROW_ICON_W = 3
        for item in items:
            if item is None:
                tk.Frame(m, bg=BAR_BG, height=1).pack(fill='x', padx=12, pady=4)
                continue
            icon, icon_ft, text, cmd = item
            if cmd is None:
                tk.Label(m, text=text, font=FT_DLG_HINT, fg=DIM, bg=MENU_BG,
                         anchor='w', padx=14, pady=4).pack(fill='x')
                continue
            row = tk.Frame(m, bg=MENU_BG, cursor='hand2')
            row.pack(fill='x')
            ico_lbl = tk.Label(row, text=icon, font=icon_ft, fg=FG, bg=MENU_BG,
                               padx=6, pady=ROW_PADY, width=ROW_ICON_W)
            ico_lbl.pack(side='left')
            txt_lbl = tk.Label(row, text=text, font=FT_MENU, fg=FG, bg=MENU_BG,
                               anchor='w', padx=(0, 14), pady=ROW_PADY)
            txt_lbl.pack(side='left', fill='x', expand=True)
            for w in (row, ico_lbl, txt_lbl):
                w.bind('<Enter>', lambda e, r=row, i=ico_lbl, t=txt_lbl: (
                    r.config(bg=HOVER_BG), i.config(bg=HOVER_BG), t.config(bg=HOVER_BG)))
                w.bind('<Leave>', lambda e, r=row, i=ico_lbl, t=txt_lbl: (
                    r.config(bg=MENU_BG), i.config(bg=MENU_BG), t.config(bg=MENU_BG)))
                w.bind('<Button-1>', lambda e, c=cmd: (c(), self._close_menu()))

        m.update_idletasks()
        mw = max(m.winfo_reqwidth(), 220)
        mh = m.winfo_reqheight() + 4  # small bottom breathing room
        m.geometry(f'{mw}x{mh}')
        bx, by = self._place_submenu(mw, mh)
        m.geometry(f'{mw}x{mh}+{bx}+{by}')
        m.after(10, lambda: dwm_round(m))
        m.after(20, lambda: self._lift_menu(m))
        m.bind('<Escape>', lambda e: self._close_menu())
        self._bind_menu_autoclose(m)
        m.focus_set()

    def _bind_menu_autoclose(self, m):
        """Attach FocusOut-to-close with a 400ms grace period.

        Without the grace period, when a sibling topmost window (e.g. the
        update banner) steals focus the instant the menu is raised, the
        FocusOut handler fires and the menu is destroyed before the user can
        see it. The delay gives the OS time to settle focus on the menu first.
        """
        m._opened_at = time.monotonic()

        def on_focus_out(_e):
            if time.monotonic() - m._opened_at < 0.4:
                return
            self.root.after(100, self._close_menu)

        m.bind('<FocusOut>', on_focus_out)

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
        if self._menu_win and self._menu_win.winfo_exists():
            self._menu_win.destroy()
            wlog('MENU   closed')
        self._menu_win = None

    # ── Language submenu ─────────────────────────────

    def _show_language_menu(self):
        """Open language selection submenu (deferred to run after close_menu)."""
        self.root.after(10, self._show_language_menu_now)

    def _show_language_menu_now(self):
        if self._menu_win and self._menu_win.winfo_exists():
            self._menu_win.destroy()
        m = tk.Toplevel(self.root)
        self._menu_win = m
        m.overrideredirect(True)
        m.attributes('-topmost', True)
        m.configure(bg=MENU_BG)

        # Header so the submenu reads as a single purpose, not a floating list
        header = tk.Label(m, text=t('menu_language'), font=FT_DLG_HINT, fg=DIM,
                          bg=MENU_BG, anchor='w', padx=14, pady=6)
        header.pack(fill='x')
        tk.Frame(m, bg=BAR_BG, height=1).pack(fill='x', padx=10, pady=(0, 4))

        langs = [('en', 'English'), ('it', 'Italiano'), ('ja', '\u65e5\u672c\u8a9e')]
        for code, name in langs:
            is_current = (code == _current_lang)
            row = tk.Frame(m, bg=MENU_BG, cursor='hand2')
            row.pack(fill='x')
            # Checkmark slot — reserves space so every row aligns
            marker = tk.Label(row, text=('\u2713' if is_current else ''),
                              font=FT_MENU, fg=CLAUDE, bg=MENU_BG,
                              width=2, padx=4, pady=6)
            marker.pack(side='left')
            txt = tk.Label(row, text=name,
                           font=(FT_MENU[0], FT_MENU[1], 'bold') if is_current else FT_MENU,
                           fg=FG, bg=MENU_BG, anchor='w', padx=2, pady=6)
            txt.pack(side='left', fill='x', expand=True)
            for w in (row, marker, txt):
                w.bind('<Enter>', lambda e, r=row, mk=marker, tx=txt: (
                    r.config(bg=HOVER_BG), mk.config(bg=HOVER_BG), tx.config(bg=HOVER_BG)))
                w.bind('<Leave>', lambda e, r=row, mk=marker, tx=txt: (
                    r.config(bg=MENU_BG), mk.config(bg=MENU_BG), tx.config(bg=MENU_BG)))
                w.bind('<Button-1>', lambda e, c=code: self._set_language(c))

        # Force a minimum width so the submenu never feels cramped
        m.update_idletasks()
        mw = max(m.winfo_reqwidth(), 200)
        mh = m.winfo_reqheight() + 6  # small bottom padding
        # Pad width visually by resizing the toplevel; children still fill correctly.
        m.geometry(f'{mw}x{mh}')
        bx, by = self._place_submenu(mw, mh)
        m.geometry(f'{mw}x{mh}+{bx}+{by}')
        m.after(10, lambda: dwm_round(m))
        m.after(20, lambda: self._lift_menu(m))
        m.bind('<Escape>', lambda e: self._close_menu())
        self._bind_menu_autoclose(m)
        m.focus_set()

    def _set_language(self, code):
        """Apply new language, save to config, retranslate visible UI."""
        set_lang(code)
        self.cfg['language'] = code
        save_cfg(self.cfg)
        self._close_menu()
        # Retranslate section labels
        self.s_session.lbl.config(text=t('current_session'))
        self.s_weekly.lbl.config(text=t('all_models'))
        self.s_sonnet.lbl.config(text=t('sonnet_only'))
        # Refresh to update reset text + any visible messages
        if self.cfg.get('session_key') and self.cfg.get('org_id'):
            self.refresh()

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
        `always_check_updates: true` in config.json — useful during development
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
        """Compact two-row notification floating above the widget.

        Top row: icon + short message. Bottom row: Update / Later / Skip pills.
        This layout keeps the banner narrow (~340px) so it never looks huge
        next to a small widget. Placement follows the widget via <Configure>.
        """
        self._dismiss_update_banner()
        bar = tk.Toplevel(self.root)
        self._update_banner = bar
        bar.overrideredirect(True)
        bar.attributes('-topmost', True)
        bar.configure(bg=ORANGE)

        wrap = tk.Frame(bar, bg=ORANGE, padx=14, pady=10)
        wrap.pack()

        top = tk.Frame(wrap, bg=ORANGE)
        top.pack(fill='x')
        tk.Label(top, text='\u2B06', font=FT_EMOJI_11,
                 fg='#1e1e1c', bg=ORANGE).pack(side='left', padx=(0, 8))
        msg = t('update_banner_available').format(version=info['version'])
        tk.Label(top, text=msg, font=FT_DLG_H, fg='#1e1e1c',
                 bg=ORANGE, anchor='w').pack(side='left')

        actions = tk.Frame(wrap, bg=ORANGE)
        actions.pack(fill='x', pady=(10, 0))

        def banner_pill(parent, text, cmd, primary=False):
            if primary:
                return make_pill_button(
                    parent, text=text, font=FT_DLG_BTN_B,
                    fg='#FFFFFF', bg='#2c2c2a', hover_bg='#3c3c3a',
                    cmd=cmd, padx=14, pady=6, parent_bg=ORANGE)
            return make_pill_button(
                parent, text=text, font=FT_DLG_BTN,
                fg='#1e1e1c', bg='#D89018', hover_bg='#C88008',
                cmd=cmd, padx=12, pady=6, parent_bg=ORANGE)

        banner_pill(actions, t('update_banner_update'),
                    lambda: self._show_update_dialog(info),
                    primary=True).pack(side='left')
        banner_pill(actions, t('update_banner_later'),
                    self._dismiss_update_banner).pack(side='left', padx=(6, 0))
        banner_pill(actions, t('update_banner_skip'),
                    lambda: self._skip_update(info)).pack(side='left', padx=(6, 0))

        bar.update_idletasks()
        bw = max(bar.winfo_reqwidth(), 320)
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
        """Transient feedback toast aligned to the widget — used for manual checks."""
        dlg = tk.Toplevel(self.root)
        dlg.overrideredirect(True)
        dlg.attributes('-topmost', True)
        dlg.configure(bg=MENU_BG)
        tk.Label(dlg, text=message, font=FT_DLG_BODY, fg=FG, bg=MENU_BG,
                 padx=16, pady=10, wraplength=340, justify='left').pack()
        dlg.update_idletasks()
        dw, dh = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
        # Prefer below the widget, fall back above — same rule as dialogs.
        wx, wy = self._place_popup(dw, dh, prefer='below')
        dlg.geometry(f'{dw}x{dh}+{wx}+{wy}')
        dlg.after(50, lambda: dwm_round(dlg))
        dlg.after(3500, dlg.destroy)

    def _show_update_dialog(self, info):
        """Full update dialog: shows changelog + download button + progress."""
        self._dismiss_update_banner()
        dw, dh = 500, 380
        dlg, body = self._build_dialog_frame(t('update_dlg_title'), dw, dh)

        subtitle = t('update_dlg_subtitle').format(
            version=info['version'], current=APP_VERSION)
        tk.Label(body, text=subtitle, font=FT_DLG_H, fg=CLAUDE, bg=BG,
                 anchor='w').pack(fill='x')

        tk.Label(body, text=t('update_dlg_changelog'), font=FT_DLG_BODY, fg=FG,
                 bg=BG, anchor='w').pack(fill='x', pady=(10, 4))

        changelog = info.get('body') or t('update_dlg_no_changelog')
        if len(changelog) > UPDATE_CHANGELOG_MAX_CHARS:
            changelog = changelog[:UPDATE_CHANGELOG_MAX_CHARS].rstrip() + '\u2026'

        txt_frame = tk.Frame(body, bg=BAR_BG, bd=0, highlightthickness=0)
        txt_frame.pack(fill='both', expand=True)
        txt = tk.Text(txt_frame, font=FT_DLG_HINT, fg=DIM, bg=BAR_BG, bd=0,
                      highlightthickness=0, wrap='word',
                      padx=10, pady=8, height=7, relief='flat')
        txt.insert('1.0', changelog)
        txt.config(state='disabled')
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
        # Give the OS a beat to start the installer before we vanish — the
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

    def _session_key_dialog(self, title, is_setup=False):
        dw, dh = 460, 320
        dlg, body = self._build_dialog_frame(title, dw, dh)

        if is_setup:
            tk.Label(body, text=t('dlg_welcome_hint'), font=FT_DLG_BODY, fg=DIM,
                     bg=BG, anchor='w', justify='left',
                     wraplength=dw - 40).pack(fill='x', pady=(0, 14))

        # Step 1 — guide
        tk.Label(body, text=t('dlg_step_guide'), font=FT_DLG_H, fg=FG, bg=BG,
                 anchor='w').pack(fill='x')
        self._secondary_pill(body, t('dlg_open_guide'), self._open_guide,
                             icon='\U0001F4D6').pack(anchor='w', pady=(8, 16))

        # Step 2 — paste
        tk.Label(body, text=t('dlg_step_paste'), font=FT_DLG_H, fg=FG, bg=BG,
                 anchor='w').pack(fill='x')

        entry_wrap = tk.Frame(body, bg=BAR_BG, padx=1, pady=1)
        entry_wrap.pack(fill='x', pady=(8, 0))
        entry = tk.Entry(entry_wrap, font=FT_DLG_BODY, bg=BAR_BG, fg=FG,
                         insertbackground=FG, bd=0,
                         highlightthickness=0, relief='flat')
        entry.pack(fill='x', ipady=7, ipadx=10)
        entry.bind('<FocusIn>',  lambda e: entry_wrap.configure(bg=FOCUS_RING))
        entry.bind('<FocusOut>', lambda e: entry_wrap.configure(bg=BAR_BG))

        if self.cfg.get('session_key'):
            entry.insert(0, self.cfg['session_key'])
        entry.focus_set()

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
                    org_id = fetch_org_id(key)
                    dlg.after(0, lambda: on_ok(key, org_id))
                except Exception as e:
                    dlg.after(0, lambda: on_err(str(e)))
            def on_ok(key, org_id):
                self.cfg['session_key'] = key
                self.cfg['org_id'] = org_id
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

    # ── Quit ─────────────────────────────────────────

    # ── Win32 toolwindow style ─────────────────────

    def _make_wintab_visible(self):
        """Make overrideredirect window visible in Win+Tab (Task View)."""
        try:
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            if not hwnd:
                hwnd = self.root.winfo_id()
            self._hwnd = hwnd
            GWL_EXSTYLE = -20
            WS_EX_APPWINDOW = 0x00040000   # visible in Win+Tab
            WS_EX_TOOLWINDOW = 0x00000080  # hidden from taskbar
            # TOOLWINDOW: no taskbar icon, no Win+Tab
            exstyle = ctypes.windll.user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
            exstyle = (exstyle | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
            ctypes.windll.user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, exstyle)
            # Force style update
            SWP_FRAMECHANGED = 0x0020
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_NOZORDER = 0x0004
            ctypes.windll.user32.SetWindowPos(
                hwnd, 0, 0, 0, 0, 0,
                SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER)
        except Exception:
            pass

    # ── Keep topmost (above taskbar) ────────────────

    def _force_topmost(self):
        """Force topmost via Win32 SetWindowPos — paused while menu is open."""
        if self._menu_win and self._menu_win.winfo_exists():
            return  # Don't re-assert while menu is open — it would go behind
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
        except Exception:
            pass

    def _keep_topmost(self):
        """Re-assert topmost every 500ms to stay above taskbar."""
        self._force_topmost()
        self._topmost_job = self.root.after(500, self._keep_topmost)

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
        self.root.destroy()


# ═══════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════

def _single_instance():
    """Ensure only one widget instance runs. Returns mutex handle or exits."""
    try:
        mutex = ctypes.windll.kernel32.CreateMutexW(None, True, 'ClaudeUsageWidget_SingleInstance')
        if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            # Another instance is running — bring it to front and exit
            hwnd = ctypes.windll.user32.FindWindowW(None, 'Claude Usage')
            if hwnd:
                ctypes.windll.user32.SetForegroundWindow(hwnd)
            sys.exit(0)
        return mutex
    except Exception:
        return None


if __name__ == '__main__':
    _mutex = _single_instance()

    # Catch unhandled exceptions globally (including tkinter callbacks)
    def _excepthook(exc_type, exc_value, exc_tb):
        import traceback
        tb = ''.join(traceback.format_exception(exc_type, exc_value, exc_tb))
        wlog(f'UNHANDLED  {tb}')
        try:
            with open(os.path.join(DIR, 'crash.log'), 'a', encoding='utf-8') as f:
                f.write(f'\n--- {datetime.now():%Y-%m-%d %H:%M:%S} UNHANDLED ---\n{tb}')
        except Exception:
            pass
    sys.excepthook = _excepthook

    wlog('INIT   process started')
    try:
        Widget()
    except Exception:
        import traceback
        tb = traceback.format_exc()
        wlog(f'CRASH  {tb}')
        # Also write to dedicated crash.log for full traceback
        try:
            with open(os.path.join(DIR, 'crash.log'), 'a', encoding='utf-8') as f:
                f.write(f'\n--- {datetime.now():%Y-%m-%d %H:%M:%S} ---\n{tb}')
        except Exception:
            pass
