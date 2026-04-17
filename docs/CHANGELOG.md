# Changelog - Claude Usage Widget

> Reconstructed from code structure, comments, and architectural patterns.
> Exact dates are not available; entries are ordered by inferred development sequence.

---

## Current Version (as of 2026-03-26)

Final state: single-file Python application compiled to EXE via PyInstaller (Python 3.14). Installed in `C:\Program Files\Claude Usage\` with user data in `%LOCALAPPDATA%\Claude Usage\`.

---

## Development Timeline

### Phase 1 - Initial Development

**Core widget with basic usage display**

- Created `widget.pyw` as a tkinter-based floating widget
- Implemented `overrideredirect(True)` frameless window with custom title bar
- Added three usage sections: session (5h), weekly (7d all models), weekly Sonnet
- Implemented pill-shaped progress bar rendering on Canvas
- Added API integration via curl subprocess
- Implemented automatic refresh on configurable interval
- Added config.json for session_key, org_id, and refresh interval
- Implemented drag-to-move via title bar
- Set up basic logging with `wlog()` function

### Phase 2 - Windows 11 Integration

**Native W11 look and feel**

- Added DWM rounded corners via `DwmSetWindowAttribute(hwnd, 33, 2)`
- Implemented DPI awareness (`SetProcessDpiAwareness(2)`)
- Added window transparency (`-alpha 0.94`)
- Removed DWM shadow from main window (CS_DROPSHADOW + frame margins)
- Retained shadow for dropdown menus and dialogs
- Added W11-styled hamburger dropdown menu with hover effects and rounded corners

### Phase 3 - Display Modes

**Essential mode and expand/collapse**

- Added expand/collapse for weekly and Sonnet bars (white dot, bottom-left)
- Implemented essential mode (double-click ochre dot)
  - Hides title bar and section labels
  - Percentage displayed inside bar (compact mode)
  - Bottom-right controls: close, refresh, time
  - Content area becomes draggable
- Added geometry persistence (x, y, width, height, expanded, essential)
- Added `_restore_essential()` for startup restoration with saved geometry
- Implemented resize via ochre dot drag

### Phase 4 - Robustness

**Crash protection and single instance**

- Added single instance enforcement via Win32 named mutex
- Implemented signal handlers (SIGTERM, SIGINT, SIGBREAK)
- Added atexit handler
- Added global exception hook (`sys.excepthook`)
- Added dedicated crash.log for full tracebacks
- Implemented auto-save on every successful data fetch (crash protection)
- Added log file size management (max 200 lines, truncation every 50 writes)

### Phase 5 - Window Management

**Win+Tab visibility and keep-topmost**

- Added `WS_EX_APPWINDOW` style for Win+Tab (Task View) visibility
- Removed `WS_EX_TOOLWINDOW` style
- Implemented 2-second keep-topmost loop via `SetWindowPos(HWND_TOPMOST)`
- Used `SWP_NOACTIVATE` to prevent focus stealing

### Phase 6 - Session Management

**Session key renewal and auto-rotation**

- Added session key renewal dialog with step-by-step instructions
- Implemented session key validation (`sk-ant-` prefix)
- Added automatic session key rotation detection from API response headers
- Added "Rinnova sessione" menu item that opens claude.ai + dialog

### Phase 7 - Polish

**Countdown timer, multi-monitor, and UX improvements**

- Added visual countdown timer to next refresh (`HH:MM (Ns)`)
- Added multi-monitor support with virtual screen bounds checking
- Allowed negative coordinates for monitors left of/above primary
- Added essential mode controls (refresh, close, time) positioned at bottom-right
- Added reset time formatting in Italian locale with relative countdown
- Improved error handling and display

### Phase 8 - Deployment

**PyInstaller packaging and AppData migration**

- Compiled to `Claude Usage.exe` using PyInstaller
- Separated EXE_DIR (Program Files) from DATA_DIR (AppData\Local)
- Added `_RES` path resolution for PyInstaller `_MEIPASS` bundled resources
- Implemented config.json migration from old location (beside script) to new (AppData)
- Bundled icons: `claude.ico`, `icon-app.png`, `icon-bar.png`
- Python 3.14 runtime in `_internal/` directory
