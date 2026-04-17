# Features — Claude Usage Widget

## 1. Usage Monitoring

### Description
Fetches and displays real-time usage statistics from the Claude.ai API. Three metrics are tracked:

| Metric | API Field | Display Name | Accent |
|--------|-----------|--------------|:------:|
| 5-hour session window | `five_hour` | Sessione Corrente | `#DA7756` |
| 7-day all models | `seven_day` | Tutti i modelli (7gg) | `#5B9BD5` |
| 7-day Sonnet only | `seven_day_sonnet` | Solo Sonnet (7gg) | `#9B72CF` |

### How It Works
1. `refresh()` spawns a daemon thread that calls `fetch_usage(cfg)`
2. `fetch_usage()` executes curl as a subprocess with session cookie authentication
3. Response JSON is parsed and each Section is updated with utilization percentage and reset time
4. The API URL format is `https://claude.ai/api/organizations/{org_id}/usage`

### Edge Cases
- If a metric returns `null`, the section shows "N/D" (not available) with dim styling
- Percentage is clamped to 0-100 range via `max(0, min(100, pct))`
- If utilization is 0 and no reset time, shows "non utilizzato"
- Empty API response body raises `RuntimeError('Risposta vuota')`

---

## 2. Automatic Refresh with Countdown

### Description
Usage data is fetched automatically at a configurable interval (default: 5 minutes). A visual countdown shows seconds remaining until the next refresh.

### How It Works
1. `_schedule()` registers a tkinter `after()` callback with `refresh_ms` delay
2. `_schedule_tick()` calls `refresh()` then re-schedules itself (perpetual loop)
3. After each successful fetch, `_start_countdown()` calculates total seconds from `refresh_ms`
4. `_tick_countdown()` runs every 1000ms, decrementing and updating the time label
5. Display format: `HH:MM (Ns)` -> `HH:MM (286s)` -> ... -> `HH:MM (1s)` -> `HH:MM`

### Configuration
- `refresh_ms` in config.json (integer, milliseconds)
- Default: 300000 (5 minutes)

### Edge Cases
- If a manual refresh is triggered during countdown, the countdown is cancelled and restarted after the new fetch
- Countdown and schedule are independent: the countdown is purely visual, the actual refresh is driven by `_schedule()`

---

## 3. Reset Time Formatting

### Description
Each usage metric has an optional `resets_at` ISO 8601 timestamp. The widget formats this into a human-readable Italian-language string showing both the absolute time and a relative countdown.

### Format Rules

| Condition | Format | Example |
|-----------|--------|---------|
| Same day | `alle HH:MM (Xh MMmin)` | `alle 18:00 (3h 26min)` |
| Different day | `DOW HH:MM (Xgg Xh)` | `sab 11:00 (2gg 5h)` |
| Less than 1 hour | `alle HH:MM (Xmin)` | `alle 14:30 (45min)` |
| 48+ hours | Day format with `gg` | `lun 09:00 (3gg 12h)` |
| Already passed | `tra poco` | `tra poco` |
| No reset time | `None` returned | Sub-label shows nothing or "non utilizzato" |

### Day Names (Italian)
`lun`, `mar`, `mer`, `gio`, `ven`, `sab`, `dom`

### Technical Details
- Input is parsed with `datetime.fromisoformat()`
- Converted to local timezone via `.astimezone()`
- Remaining seconds calculated from UTC comparison
- Minutes formatted with leading zero (`{total_m:02d}`)

---

## 4. Color-Coded Usage Warnings

### Description
Progress bars and percentage labels change color based on usage level to provide at-a-glance warning.

### Thresholds

| Range | Color | Variable | Meaning |
|:-----:|:-----:|:--------:|---------|
| 0-74% | Accent | varies | Normal |
| 75-89% | `#E8A838` | `ORANGE` | Warning |
| 90-100% | `#E85858` | `RED` | Critical |

### Implementation
`bar_color(pct, accent)` returns the appropriate color. Both the pill bar fill and the percentage label text use this color.

---

## 5. Expand/Collapse Extra Bars

### Description
The widget defaults to showing only the session (5-hour) usage. A white dot in the bottom-left corner toggles visibility of the weekly and Sonnet usage bars.

### How It Works
1. Click the expand dot (●) at bottom-left
2. `_toggle_expand()` toggles `_expanded` flag
3. **Expanding**: `extra_frame` is packed between session section and bottom spacer; dot turns brighter (`DOT_W`)
4. **Collapsing**: `extra_frame` is pack_forget'd; dot dims (`DOT_W_D`)
5. `_auto_height()` adjusts window height to fit content

### Interaction with Essential Mode
- When expanding in essential mode: all three sections switch from compact to normal display (headers visible)
- When collapsing in essential mode: only session section returns to compact (percentage inside bar)
- Expanding is preserved in config as `"expanded": true/false`

---

## 6. Essential Mode

### Description
A compact display mode that strips the widget to its minimum: no title bar, no section labels, just the usage bar with percentage displayed inside it. Designed for unobtrusive persistent monitoring.

### How It Works

**Entering Essential Mode** (double-click ochre dot):
1. Title bar and separator are `pack_forget()`'d
2. Extra sections are collapsed if expanded
3. Session section set to compact mode (header hidden, % text drawn on canvas)
4. Essential controls placed at bottom-right: close (✕), refresh (↻), time label
5. Content frame and session widgets become draggable
6. Window minimum height changes to 46px

**Exiting Essential Mode** (double-click ochre dot again):
1. All sections restored to non-compact
2. Essential controls hidden (`place_forget()`)
3. Title bar and separator re-packed
4. Content frame drag bindings removed
5. Window minimum height changes to 90px

### Persistence
- Essential mode state is saved in config as `"essential": true/false`
- On startup, if `essential` is true, `_restore_essential()` is called after 100ms delay
- `_restore_essential()` calls `_toggle_essential()` then re-applies saved geometry

### Essential Mode Controls Layout
```
                          ✕  ↻  14:30 (Ns)  ●
                          ^   ^      ^       ^
                     close refresh  time   resize
```
Positioned from right: resize dot at relx=1.0 x=-6, time at x=-20, refresh at x=-82, close at x=-97.

---

## 7. Drag to Move

### Description
The widget can be repositioned by dragging the title bar (normal mode) or the content area (essential mode).

### How It Works
1. `<Button-1>` on draggable area: records cursor offset (`_dx`, `_dy`)
2. `<B1-Motion>`: calculates new position from root position + cursor delta - offset
3. `<ButtonRelease-1>`: calls `_save_geometry()` to persist position

### Draggable Elements (Normal Mode)
- Title bar frame (`tb`)
- Icon label
- Title label
- Time label

### Draggable Elements (Essential Mode)
- Content frame
- Session section frame
- All non-Canvas children of session frame

---

## 8. Drag to Resize

### Description
The ochre dot at the bottom-right corner serves as a resize handle.

### How It Works
1. `<Button-1>` on resize dot: records initial cursor position and window dimensions
2. `<B1-Motion>`: calculates new width and height from delta, enforcing minimums
3. Width minimum: 260px (`MIN_W`)
4. Height minimum: 46px essential / 90px normal
5. `<ButtonRelease-1>`: saves geometry

### Conflict with Essential Toggle
The resize dot also handles `<Double-Button-1>` for essential mode toggle. tkinter processes both single-click (resize start) and double-click events. The resize start on the first click is harmless since no motion follows during a double-click.

---

## 9. Single Instance Enforcement

### Description
Only one instance of the widget can run at a time. Launching a second instance brings the first to the foreground.

### How It Works
1. `_single_instance()` creates a Win32 named mutex: `ClaudeUsageWidget_SingleInstance`
2. If `CreateMutexW` returns `ERROR_ALREADY_EXISTS` (183):
   - `FindWindowW(None, 'Claude Usage')` finds the existing window
   - `SetForegroundWindow(hwnd)` brings it to front
   - `sys.exit(0)` terminates the duplicate
3. The mutex handle is stored to prevent garbage collection

### Edge Cases
- If Win32 API calls fail (non-Windows, permissions), the function returns `None` and the widget starts normally (no protection)
- The mutex is process-scoped; it is automatically released when the process exits

---

## 10. Keep-Topmost

### Description
The widget stays above all other windows, including the Windows taskbar, via periodic Win32 API calls.

### How It Works
1. `_keep_topmost()` calls `SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)` every 2 seconds
2. `HWND_TOPMOST = -1` sets the window to the topmost z-order
3. `SWP_NOACTIVATE = 0x10` prevents stealing focus from other windows
4. The 2-second interval handles cases where other windows or Windows itself de-topmost the widget

### Why Not Just `-topmost True`?
tkinter's `-topmost` attribute uses `SetWindowPos` once. Other topmost windows, full-screen apps, or the taskbar can still cover the widget. The periodic re-assertion ensures the widget stays visible.

### Cancellation
The `after()` job ID is stored in `_topmost_job` and cancelled during `_quit()`.

---

## 11. Win+Tab (Task View) Visibility

### Description
Despite being an `overrideredirect` window (no standard window frame), the widget appears in Windows' Win+Tab task view, allowing users to switch to it or see it in the virtual desktop overview.

### How It Works
1. `_make_wintab_visible()` modifies the window's extended styles via Win32 API
2. Adds `WS_EX_APPWINDOW` (0x00040000) — makes the window appear in task switchers
3. Removes `WS_EX_TOOLWINDOW` (0x00000080) — prevents "tool window" behavior that hides from taskbar/task view
4. Calls `SetWindowPos` with `SWP_FRAMECHANGED` to force the style update

### Why This Is Needed
`overrideredirect(True)` creates a window with no title bar and no taskbar entry. By default, such windows are invisible in Win+Tab. The extended style modification makes it behave like a normal application window in task switching while keeping the custom frameless appearance.

---

## 12. Windows 11 Rounded Corners

### Description
The widget uses native Windows 11 DWM (Desktop Window Manager) rounded corners rather than simulating them in tkinter.

### How It Works
`dwm_round(win, shadow=True)` calls:
```
DwmSetWindowAttribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE=33, DWMWCP_ROUND=2)
```

### Shadow Control
- Main widget: `shadow=False` — removes CS_DROPSHADOW from window class and zeroes DWM frame margins
- Menu dropdowns: `shadow=True` (default) — retains native shadow for depth effect
- Session dialog: `shadow=True` — retains shadow

### Applied To
| Window | Delay | Shadow |
|--------|:-----:|:------:|
| Main widget | 50ms after geometry | No |
| Dropdown menu | 10ms after creation | Yes |
| Session dialog | 10ms after creation | Yes |

---

## 13. Logging

### Description
Runtime events are logged to `widget.log` with automatic size management.

### Log Format
```
YYYY-MM-DD HH:MM:SS  CATEGORY  message
```

### Log Categories

| Category | Events |
|----------|--------|
| `INIT` | Process started |
| `START` | Widget initialized, mainloop entered |
| `EXIT` | Mainloop terminated |
| `FETCH` | Fetch thread start, data received, success with values, errors |
| `SCHED` | Scheduled refresh tick |
| `MODE` | Essential mode toggle |
| `MENU` | Menu closed |
| `SAVE` | Geometry save errors |
| `ERROR` | Error display |
| `SIGNAL` | OS signal received |
| `QUIT` | Quit method called |
| `ATEXIT` | Process terminating via atexit |
| `UNHANDLED` | Unhandled exception (full traceback) |
| `CRASH` | Top-level crash (full traceback) |

### Size Management
- Maximum: 200 lines (`MAX_LOG_LINES`)
- Truncation check: every 50 writes (`_log_count` counter)
- Truncation method: read all lines, keep last 200, overwrite file
- All log operations are wrapped in try/except to prevent log failures from crashing the widget

---

## 14. Crash Protection

### Description
Multiple layers ensure the widget's state survives crashes, external kills, and unexpected shutdowns.

### Layer 1: Auto-Save on Data Fetch
`_save_geometry()` is called after every successful API response in `_on_data()`. This means the widget's position, size, and mode are saved at least every `refresh_ms` milliseconds.

### Layer 2: Save on Interaction
Geometry is saved on every:
- Window drag release (`<ButtonRelease-1>` on title bar)
- Window resize release (`<ButtonRelease-1>` on resize dot)

### Layer 3: Signal Handlers
```python
for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGBREAK):
    signal.signal(sig, self._signal_quit)
```
`_signal_quit()` saves geometry before exiting. This handles:
- Ctrl+C (SIGINT)
- Task Manager "End Process" (SIGTERM)
- Console window close (SIGBREAK)
- PowerToys or other tools terminating the process

### Layer 4: atexit Handler
```python
atexit.register(lambda: wlog('ATEXIT processo in chiusura'))
```
Logs the termination event for diagnostic purposes.

### Layer 5: Global Exception Hook
```python
sys.excepthook = _excepthook
```
Catches ALL unhandled exceptions (including tkinter callback errors). Writes full traceback to both `widget.log` and `crash.log`.

### Layer 6: Top-Level Try/Except
The `Widget()` constructor call is wrapped in a try/except that catches any exception during initialization. Full traceback is written to `crash.log`.

### crash.log Format
```
--- YYYY-MM-DD HH:MM:SS ---
Traceback (most recent call last):
  ...
ExceptionType: message
```

---

## 15. Session Key Auto-Rotation

### Description
The Claude.ai API may rotate session keys by sending a `Set-Cookie` header in the response. The widget detects this and updates the config transparently.

### How It Works
1. `fetch_usage()` parses response headers with: `re.search(r'sessionKey=([^;\s]+)', headers)`
2. If a session key is found AND differs from the current one, it updates `cfg['session_key']` and calls `save_cfg()`
3. The user never needs to manually update the key when rotation happens

---

## 16. Session Key Renewal Dialog

### Description
When the session key expires (HTTP 401/403), the user can renew it through a guided dialog.

### How It Works
1. Menu -> "Rinnova sessione" opens claude.ai in the default browser
2. After 500ms delay, a dialog Toplevel appears with step-by-step instructions
3. User pastes the new sessionKey from browser DevTools
4. Validation: key must start with `sk-ant-`
5. On save: config is updated and an immediate refresh is triggered

### Dialog Features
- Draggable title bar
- Positioned above the widget (or below if above would be off-screen)
- Entry field with Claude orange focus highlight
- Enter key submits
- Inline error messages for validation failures

---

## 17. Hamburger Menu

### Description
A W11-styled dropdown menu accessible from the title bar.

### Menu Items

| Item | Icon | Action |
|------|:----:|--------|
| Aggiorna | ↻ | Manual refresh |
| Modalita normale/essential | ⇅ | Toggle essential mode |
| — separator — | | |
| Rinnova sessione... | ⚒ | Open session renewal flow |
| Apri config.json | ⚙ | Open config in Notepad |
| — separator — | | |
| Chiudi | ✕ | Quit widget |

### Behavior
- Toggle: clicking the hamburger when menu is open closes it
- Auto-close: Escape key or focus loss
- Positioning: right-aligned below the hamburger button
- DWM rounded corners with shadow
- Hover effect on each item

---

## 18. Multi-Monitor Support

### Description
The widget can be positioned on any monitor, including those with negative coordinates (left of or above the primary monitor).

### How It Works
- Uses `winfo_vrootwidth()` / `winfo_vrootheight()` for the full virtual screen
- Allows negative x/y coordinates in saved config
- Bounds check: at least 50px visible horizontally, window not above y=-20 or below screen
- If saved position is out of bounds, defaults to x=100, y=100

---

## 19. DPI Awareness

### Description
The widget renders correctly on high-DPI displays and mixed-DPI multi-monitor setups.

### How It Works
```python
ctypes.windll.shcore.SetProcessDpiAwareness(2)
```
Value 2 = `PROCESS_PER_MONITOR_DPI_AWARE_V2` — the highest level of DPI awareness on Windows 10/11. This means:
- The widget uses physical pixels, not logical pixels
- Font rendering adapts to the current monitor's DPI
- Moving between monitors with different DPIs is handled by Windows

---

## 20. Config Auto-Migration

### Description
The widget migrated from storing config.json beside the script to storing it in AppData. This feature handles the transition transparently.

### How It Works
On startup:
1. Check if `config.json` exists in `EXE_DIR` (old location)
2. Check if `config.json` does NOT exist in `DATA_DIR` (new location)
3. If both conditions are true, copy the file using `shutil.copy2()` (preserves timestamps)

This is a one-time migration that runs only when the old file exists and the new one doesn't.

---

## 21. Pill-Shaped Progress Bars

### Description
Usage bars are drawn as pill/capsule shapes using Canvas primitives, providing a modern look that aligns with Windows 11 design language.

### Rendering Steps
1. Draw full-width background pill in `BAR_BG` (#3a3a38)
2. If percentage > 0, draw foreground pill proportional to percentage
3. Foreground width: `max(BAR_H, canvas_width * pct / 100)` — minimum width equals bar height to prevent the pill from becoming a thin sliver
4. In compact mode, overlay centered percentage text in white bold

### Shape Construction
Three canvas primitives per pill:
- Left circle: `create_oval(x, y, x+h, y+h)`
- Right circle: `create_oval(x+w-h, y, x+w, y+h)`
- Center rectangle: `create_rectangle(x+r, y, x+w-r, y+h)` (only if w > h)
- All three use `outline=fill` to prevent visible seams

### Responsive
The bar redraws on `<Configure>` events, adapting to window resize.

---

## 22. Geometry Persistence

### Description
Window position, size, display mode, and expand state are saved to config.json and restored on startup.

### Saved Properties

| Config Key | Type | Description |
|------------|------|-------------|
| `x` | `int` | Window X position |
| `y` | `int` | Window Y position |
| `width` | `int` | Window width |
| `height` | `int` | Window height |
| `expanded` | `bool` | Whether extra sections are visible |
| `essential` | `bool` | Whether essential mode is active |

### Save Triggers
- After window drag release
- After window resize release
- After every successful data fetch (crash protection)
- On quit (both normal and signal-triggered)

### Restore on Startup
1. Position: x, y applied with bounds checking
2. Size: width x height applied; if no saved height, uses `winfo_reqheight()`
3. Essential mode: restored 100ms after initial render via `_restore_essential()`
4. Expanded state: saved but not explicitly restored on startup (defaults to collapsed)
