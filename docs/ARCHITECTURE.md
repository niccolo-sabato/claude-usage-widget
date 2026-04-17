# Architecture — Claude Usage Widget

## Source File

Single-file application: `widget.pyw` (~1039 lines)

## Module Structure

```
widget.pyw
├── Module docstring & imports (lines 1-36)
├── DPI Awareness setup (lines 38-42)
├── Path resolution & config migration (lines 44-62)
├── Constants
│   ├── Theme colors (lines 64-81)
│   ├── Layout dimensions (lines 83-91)
│   ├── Font definitions (lines 93-99)
│   ├── Logging constants (lines 101-103)
│   └── API URL template (lines 126-127)
├── Helper functions (lines 130-214)
│   ├── load_cfg(), save_cfg()
│   ├── bar_color(), format_reset()
│   ├── pill(), dwm_round()
├── API function: fetch_usage() (lines 220-256)
├── Class: Section (lines 262-334)
├── Class: Widget (lines 340-989)
└── Main entry point (lines 992-1039)
    ├── _single_instance()
    ├── Exception hooks
    └── Widget() instantiation
```

## Imports

| Module | Purpose |
|--------|---------|
| `sys` | Exit, argv, excepthook, _MEIPASS |
| `os` | Path operations, makedirs, environ, startfile |
| `re` | HTTP status code and cookie parsing |
| `json` | Config load/save, API response parsing |
| `ctypes` | Win32 API: DPI, DWM, window styles, mutex, SetWindowPos |
| `signal` | SIGTERM/SIGINT/SIGBREAK handlers |
| `atexit` | Process exit logging |
| `threading` | Background API fetch thread |
| `subprocess` | curl execution, Notepad launch |
| `webbrowser` | Open claude.ai for session renewal |
| `tkinter` | GUI framework |
| `datetime` | Timestamps, reset time formatting |
| `shutil` | Config migration (conditional import) |
| `traceback` | Crash log formatting (conditional import) |

## Path Resolution

```python
EXE_DIR  = dirname(abspath(sys.argv[0]))          # Where exe/script lives
DATA_DIR = %LOCALAPPDATA%\Claude Usage             # Writable data folder
_RES     = sys._MEIPASS or EXE_DIR                 # Bundled resources (PyInstaller)
CFG      = DATA_DIR\config.json
LOG_FILE = DATA_DIR\widget.log
ICO      = _RES\claude.ico
ICO_BAR  = _RES\icon-bar.png
```

**Config Migration**: On first run after installation, if `config.json` exists in `EXE_DIR` but not in `DATA_DIR`, it is copied over. This handles migration from the old layout (config beside script) to the new layout (config in AppData).

## Class: Section

Represents a single usage metric with its visual components.

### Constructor

```python
Section(parent: tk.Frame, label: str, accent: str)
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `parent` | `tk.Frame` | Parent container |
| `label` | `str` | Display name (e.g., "Sessione Corrente") |
| `accent` | `str` | Hex color for the bar at normal usage levels |

### Instance Variables

| Variable | Type | Description |
|----------|------|-------------|
| `accent` | `str` | Base accent color |
| `_pct` | `float` | Current percentage (0-100) |
| `_color` | `str` | Current bar color (may differ from accent if >= 75%) |
| `_compact` | `bool` | Whether in compact/essential display mode |
| `frame` | `tk.Frame` | Outer container |
| `hdr` | `tk.Frame` | Header row (label + percentage) |
| `lbl` | `tk.Label` | Section name label |
| `lbl_pct` | `tk.Label` | Percentage text label |
| `cv` | `tk.Canvas` | Bar drawing canvas |
| `lbl_sub` | `tk.Label` | Sub-label (reset countdown or status text) |

### Methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `set_compact` | `(compact: bool) -> None` | Toggle compact mode. In compact mode, the header is hidden and percentage is drawn inside the bar. When exiting compact, re-packs header, canvas, and sub-label in order. |
| `update` | `(pct: float|None, resets_at: str|None) -> None` | Update the section with new data. `pct=None` shows "N/D" (not available). Clamps percentage to 0-100. Updates bar color via `bar_color()`. Formats reset countdown via `format_reset()`. |
| `_draw` | `(w: int) -> None` | Redraws the bar on the canvas. First draws full-width background pill, then foreground pill proportional to percentage. In compact mode, overlays centered percentage text. Bound to `<Configure>` event for responsive resizing. |

### Widget Hierarchy (per Section)

```
frame (tk.Frame, bg=#262624, fill='x', padx=12, pady=(3,0))
├── hdr (tk.Frame, bg=#262624, fill='x')
│   ├── lbl (tk.Label, side='left', font=Segoe UI 9)
│   └── lbl_pct (tk.Label, side='right', font=Segoe UI 9 bold)
├── cv (tk.Canvas, height=16, fill='x', pady=(1,0))
└── lbl_sub (tk.Label, font=Segoe UI 8, fg=#7a7a78)
```

## Class: Widget

Main application class. The constructor creates the window, builds all UI, restores saved state, starts the refresh loop, registers signal handlers, and enters the tkinter mainloop. Everything happens within `__init__`.

### Constructor Flow

```
Widget.__init__()
├── load_cfg()
├── Create tk.Tk root window
│   ├── overrideredirect(True)
│   ├── attributes('-topmost', True)
│   ├── attributes('-alpha', 0.94)
│   └── iconbitmap(claude.ico)
├── _make_wintab_visible()          # Win32 WS_EX_APPWINDOW
├── Load bar icon (icon-bar.png)
├── _build()                         # Construct all UI
├── Restore geometry from config
│   ├── Multi-monitor bounds check
│   └── Apply saved x, y, width, height
├── dwm_round() after 50ms          # DWM rounded corners
├── _keep_topmost()                  # Start 2s topmost loop
├── Restore essential mode if saved
├── Initial refresh() + _schedule()  # Or show config error
├── Register WM_DELETE_WINDOW
├── Register atexit + signal handlers
├── wlog('START')
├── root.mainloop()                  # BLOCKING
└── wlog('EXIT')
```

### Instance Variables

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `cfg` | `dict` | from config.json | Configuration data |
| `root` | `tk.Tk` | — | Root window |
| `_job` | `str|None` | `None` | Scheduled refresh `after()` ID |
| `_countdown_job` | `str|None` | `None` | Countdown timer `after()` ID |
| `_topmost_job` | `str|None` | `None` | Keep-topmost `after()` ID |
| `_countdown_secs` | `int` | `0` | Seconds remaining until next refresh |
| `_last_time` | `str` | `''` | Last update time string (HH:MM) |
| `_dx, _dy` | `int` | `0` | Drag offset for window movement |
| `_expanded` | `bool` | `False` | Whether extra bars are visible |
| `_essential` | `bool` | `False` | Whether essential (compact) mode is active |
| `_rs_x, _rs_y, _rs_w, _rs_h` | `int` | `0` | Resize start state |
| `_menu_win` | `tk.Toplevel|None` | `None` | Active menu window |
| `_bar_icon` | `tk.PhotoImage|None` | — | Title bar icon image |
| `_hwnd` | `int` | — | Win32 window handle |

### Methods

#### UI Construction

| Method | Description |
|--------|-------------|
| `_build()` | Constructs the entire UI hierarchy. Creates title bar with icon, title, time label, refresh/menu/close buttons. Creates content area with session Section. Creates expandable frame with weekly and sonnet Sections. Creates overlay controls (expand dot, resize dot, essential mode controls, error label). |

#### Display Mode Management

| Method | Description |
|--------|-------------|
| `_toggle_expand()` | Toggles visibility of weekly/sonnet sections. When expanding in essential mode, switches all sections to non-compact. Updates dot color (bright white when expanded, dim when collapsed). Calls `_auto_height()`. |
| `_toggle_essential()` | Toggles essential mode. When entering: hides title bar and separator, collapses extra sections, sets session to compact, shows essential controls (time, refresh, close), makes content draggable. When exiting: restores all sections to non-compact, hides essential controls, re-packs title bar and separator, unbinds drag from content. |
| `_restore_essential()` | Called 100ms after startup if essential mode was saved. Calls `_toggle_essential()` then re-applies saved geometry. |
| `_auto_height()` | Forces window height to match content's `winfo_reqheight()`, preserving x, y, and width. |

#### Drag and Resize

| Method | Signature | Description |
|--------|-----------|-------------|
| `_drag_start` | `(e: Event) -> None` | Records cursor offset within widget |
| `_drag_move` | `(e: Event) -> None` | Moves window by delta from recorded offset |
| `_resize_start` | `(e: Event) -> None` | Records initial cursor position and window size |
| `_resize_move` | `(e: Event) -> None` | Resizes window, enforcing `MIN_W` and mode-dependent `MIN_H_E`/`MIN_H_N` |
| `_bind_drag` | `(w: Widget) -> None` | Binds Button-1, B1-Motion, ButtonRelease-1 for drag |
| `_unbind_drag` | `(w: Widget) -> None` | Removes drag bindings |

#### Data Fetching

| Method | Description |
|--------|-------------|
| `refresh()` | Cancels countdown, shows loading indicator ("..."), spawns daemon thread calling `_fetch()`. |
| `_fetch()` | Runs in background thread. Calls `fetch_usage()`. On success, schedules `_on_data()` on main thread via `root.after(0, ...)`. On `PermissionError` (401/403), shows session expired error. On other exceptions, shows error message. All cross-thread communication goes through `root.after()`. |
| `_on_data(d: dict)` | Processes API response. Extracts `five_hour`, `seven_day`, `seven_day_sonnet` objects. Updates each Section. Updates timestamp. Saves geometry (crash protection). Starts countdown. |
| `_start_countdown()` | Calculates seconds from `refresh_ms` config, starts `_tick_countdown()` loop. |
| `_tick_countdown()` | Decrements counter every second, updates time labels with format `HH:MM (Ns)`. Stops when reaching 0. |
| `_error(msg: str)` | Logs error, displays message in error label, shows "errore"/"err" in time labels. |
| `_schedule()` | Schedules next `_schedule_tick()` call after `refresh_ms` milliseconds. |
| `_schedule_tick()` | Calls `refresh()` wrapped in try/except, then `_schedule()` for next cycle. |

#### Menu System

| Method | Description |
|--------|-------------|
| `_show_menu(e)` | Creates a `Toplevel` dropdown menu with DWM rounded corners. Positioned below the hamburger button. Contains: Aggiorna, mode toggle, separator, Rinnova sessione, Apri config, separator, Chiudi. Each item has hover highlighting. Menu auto-closes on Escape or FocusOut. |
| `_close_menu()` | Destroys the menu Toplevel if it exists. |

#### Configuration and Session

| Method | Description |
|--------|-------------|
| `_open_config()` | Opens `config.json` in Notepad. Falls back to `os.startfile()`. |
| `_renew_session()` | Opens claude.ai in browser, then shows `_session_dialog()` after 500ms. |
| `_session_dialog()` | Creates a modal-like `Toplevel` (420x310) with step-by-step instructions, a text entry for the session key, and a "Salva e Aggiorna" button. Validates key format (`sk-ant-` prefix). On save, updates config and triggers refresh. |
| `_save_geometry(e=None)` | Saves x, y, width, height, expanded, essential to config.json. Called on drag release, resize release, and after each data fetch. |

#### Window Management (Win32)

| Method | Description |
|--------|-------------|
| `_make_wintab_visible()` | Makes the `overrideredirect` window visible in Win+Tab (Task View) by setting `WS_EX_APPWINDOW` and clearing `WS_EX_TOOLWINDOW` on the window's extended style. Forces style update via `SetWindowPos` with `SWP_FRAMECHANGED`. |
| `_keep_topmost()` | Re-asserts `HWND_TOPMOST` via `SetWindowPos` every 2 seconds. Uses `SWP_NOACTIVATE` to avoid stealing focus. Necessary because other windows or the taskbar can de-topmost the widget. |

#### Shutdown

| Method | Description |
|--------|-------------|
| `_signal_quit(signum, frame)` | Signal handler for SIGTERM/SIGINT/SIGBREAK. Saves geometry and exits. |
| `_quit()` | Closes menu, saves geometry, cancels all scheduled jobs (_job, _countdown_job, _topmost_job), destroys root window. |

## Standalone Functions

| Function | Signature | Description |
|----------|-----------|-------------|
| `load_cfg` | `() -> dict` | Reads config.json. Returns empty dict if file missing. |
| `save_cfg` | `(data: dict) -> None` | Writes config.json with 2-space indent. |
| `bar_color` | `(pct: float, accent: str) -> str` | Returns RED if >= 90%, ORANGE if >= 75%, else accent. |
| `format_reset` | `(iso_str: str) -> str|None` | Parses ISO 8601 reset time, returns Italian-locale human-readable countdown string. |
| `pill` | `(cv, x, y, w, h, color) -> None` | Draws a pill/capsule shape on a canvas using two ovals + one rectangle. Uses `outline=fill` to seal visual seams. |
| `dwm_round` | `(win, shadow=True) -> None` | Applies Windows 11 rounded corners via `DwmSetWindowAttribute(hwnd, 33, 2)`. Optionally removes DWM shadow by zeroing `CS_DROPSHADOW` class style and extending frame with zero margins. |
| `wlog` | `(msg: str) -> None` | Appends timestamped line to widget.log. Truncates file to last 200 lines every 50 writes. |
| `fetch_usage` | `(cfg: dict) -> dict` | Calls Claude.ai usage API via curl subprocess. Parses HTTP headers for status code and session key rotation. Returns JSON-parsed response body. |
| `_single_instance` | `() -> handle|None` | Creates a Win32 named mutex. If mutex already exists (another instance running), brings existing window to front and exits. |
| `_excepthook` | `(exc_type, exc_value, exc_tb)` | Global unhandled exception handler. Logs to both widget.log and crash.log. |

## Threading Model

```
Main Thread (tkinter)
├── UI rendering and event handling
├── root.mainloop() — never blocks
├── root.after() — scheduled callbacks
│   ├── _schedule_tick() — every refresh_ms (default 300000ms)
│   ├── _tick_countdown() — every 1000ms
│   ├── _keep_topmost() — every 2000ms
│   └── _on_data() / _error() — from fetch thread
│
Background Threads (daemon=True)
└── _fetch() — one at a time, spawned by refresh()
    ├── subprocess.run(curl, timeout=20) — blocking
    └── root.after(0, callback) — returns result to main thread
```

**Thread Safety**: The fetch thread never touches UI directly. It communicates with the main thread exclusively via `root.after(0, callback)`, which schedules the callback on the tkinter event loop. The `daemon=True` flag ensures the thread dies with the process.

## Data Flow

```
1. Timer fires (_schedule_tick) or user clicks refresh
2. refresh() spawns daemon thread -> _fetch()
3. _fetch() calls fetch_usage(cfg)
4. fetch_usage() runs curl as subprocess
   ├── Sends session cookie + org_id
   ├── Parses HTTP status code from response headers
   ├── Auto-rotates session key if Set-Cookie present
   └── Returns parsed JSON
5. _fetch() posts result to main thread via root.after(0, _on_data, data)
6. _on_data() extracts five_hour, seven_day, seven_day_sonnet
7. Each Section.update() receives (utilization%, resets_at)
8. Section._draw() renders pill bar on canvas
9. _save_geometry() persists state to config.json
10. _start_countdown() begins visual countdown to next refresh
```

## API Integration

### Endpoint

```
GET https://claude.ai/api/organizations/{org_id}/usage
```

### Request Headers

| Header | Value |
|--------|-------|
| `Cookie` | `sessionKey=<key>; lastActiveOrg=<org_id>` |
| `User-Agent` | Chrome 146 UA string |
| `anthropic-client-platform` | `web_claude_ai` |

### Response Structure

```json
{
  "five_hour": {
    "utilization": 54.0,
    "resets_at": "2026-03-27T20:00:00Z"
  },
  "seven_day": {
    "utilization": 78.0,
    "resets_at": "2026-04-01T00:00:00Z"
  },
  "seven_day_sonnet": {
    "utilization": 0.0,
    "resets_at": null
  }
}
```

Each field can be `null` if the metric is not available.

### Error Handling

| HTTP Code | Behavior |
|-----------|----------|
| 401, 403 | Raises `PermissionError` -> shows session expired message |
| >= 400 | Raises `RuntimeError` with HTTP code |
| curl failure | Raises `RuntimeError` with stderr |
| Empty body | Raises `RuntimeError("Risposta vuota")` |

### Session Key Auto-Rotation

The response headers are scanned for a `sessionKey=` Set-Cookie. If found and different from the current key, the config is updated transparently. This handles Anthropic's periodic key rotation without user intervention.

## Widget Hierarchy (Complete)

```
root (tk.Tk, overrideredirect, topmost, alpha=0.94, bg=#262624)
└── main (tk.Frame, bg=#262624, fill='both', expand=True)
    ├── tb (tk.Frame, bg=#1e1e1c, height=28, fill='x')  [title bar]
    │   ├── ico (tk.Label, image=icon-bar.png or "✱")
    │   ├── title (tk.Label, "Claude Usage", bold)
    │   ├── lbl_time (tk.Label, right side, dim)
    │   ├── btn_r (tk.Label, "↻", right side)
    │   ├── btn_menu (tk.Label, "≡", right side)
    │   └── btn_x (tk.Label, "✕", far right)
    ├── sep (tk.Frame, bg=#3a3a38, height=1)  [separator line]
    ├── content (tk.Frame, bg=#262624, fill='both', expand=True)
    │   ├── s_session (Section)  [always visible]
    │   ├── extra_frame (tk.Frame)  [toggleable]
    │   │   ├── s_weekly (Section)
    │   │   └── s_sonnet (Section)
    │   ├── bottom_pad (tk.Frame, height=6)
    │   └── lbl_err (tk.Label, red, hidden by default)
    ├── btn_expand (tk.Label, "●", place at bottom-left)  [overlay]
    ├── btn_resize (tk.Label, "●", place at bottom-right)  [overlay]
    ├── ess_refresh (tk.Label, "↻", hidden)  [essential mode]
    ├── ess_time (tk.Label, hidden)  [essential mode]
    └── ess_close (tk.Label, "✕", hidden)  [essential mode]
```

## Win32 API Calls

| API | Purpose |
|-----|---------|
| `shcore.SetProcessDpiAwareness(2)` | Per-monitor DPI awareness v2 |
| `dwmapi.DwmSetWindowAttribute(hwnd, 33, 2)` | W11 rounded corners (DWMWA_WINDOW_CORNER_PREFERENCE = DWMWCP_ROUND) |
| `dwmapi.DwmExtendFrameIntoClientArea(hwnd, MARGINS(0,0,0,0))` | Remove DWM frame shadow |
| `user32.GetWindowLongPtrW/SetWindowLongPtrW` | Modify extended window styles |
| `user32.SetWindowPos(hwnd, HWND_TOPMOST, ...)` | Force topmost z-order |
| `user32.GetClassLongPtrW/SetClassLongPtrW` | Remove CS_DROPSHADOW from window class |
| `kernel32.CreateMutexW` | Single instance enforcement |
| `user32.FindWindowW` | Find existing instance window |
| `user32.SetForegroundWindow` | Bring existing instance to front |
| `user32.GetParent` | Get real HWND from tkinter's embedded window |

## Crash Protection

Multiple layers of crash protection ensure state is preserved:

1. **Auto-save on data fetch**: `_save_geometry()` called after every successful API response
2. **Save on drag/resize release**: geometry saved on `<ButtonRelease-1>` events
3. **atexit handler**: logs process termination
4. **Signal handlers**: SIGTERM, SIGINT, SIGBREAK all save geometry before exit
5. **Global excepthook**: catches unhandled exceptions, writes to both widget.log and crash.log
6. **Top-level try/except in main**: catches Widget() constructor failures, writes full traceback to crash.log
