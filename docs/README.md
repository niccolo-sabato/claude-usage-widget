# Claude Usage Widget

A floating always-on-top desktop widget for Windows 11 that displays real-time Claude.ai usage statistics. Built with Python (tkinter), styled with Windows 11 Material design language, featuring rounded corners, translucent background, and pill-shaped progress bars.

## Overview

The widget fetches usage data from the Claude.ai API at configurable intervals and displays three usage metrics:

| Metric | Description | Accent Color |
|--------|-------------|:------------:|
| **Sessione Corrente** (Current Session) | 5-hour rolling window utilization | `#DA7756` (Claude orange) |
| **Tutti i modelli (7gg)** (All models, 7-day) | 7-day aggregate usage across all models | `#5B9BD5` (Blue) |
| **Solo Sonnet (7gg)** (Sonnet only, 7-day) | 7-day usage for Sonnet model specifically | `#9B72CF` (Purple) |

## Installation

### Directory Structure

```
C:\Program Files\Claude Usage\       # Application directory (EXE_DIR)
    Claude Usage.exe                  # PyInstaller-compiled executable
    claude.ico                        # Window icon (ICO format, 32KB)
    icon-app.png                      # Application icon (PNG, 17KB)
    icon-bar.png                      # Title bar icon (PNG, 1KB)
    widget.pyw                        # Source code
    _internal\                        # PyInstaller runtime (Python 3.14)
        python314.dll
        _tkinter.pyd
        _tcl_data\, _tk_data\
        ... (runtime DLLs)

C:\Users\<User>\AppData\Local\Claude Usage\   # Data directory (DATA_DIR)
    config.json                                 # User configuration
    widget.log                                  # Runtime log (max 200 lines)
    crash.log                                   # Unhandled exception log
```

### Auto-Start at Login

Create a shortcut in the Windows Startup folder:

```
Win+R -> shell:startup -> create shortcut to:
  "C:\Program Files\Claude Usage\Claude Usage.exe"
```

Or if running from source:
```
pythonw.exe "C:\Program Files\Claude Usage\widget.pyw"
```

### First-Time Setup

1. Go to [claude.ai](https://claude.ai) while logged in
2. Open DevTools (F12) -> Application -> Cookies -> `https://claude.ai`
3. Copy the `sessionKey` cookie value
4. Edit `config.json` and set `session_key` and `org_id`
5. Launch the widget

## Usage

### Controls

| Control | Location | Action |
|---------|----------|--------|
| **Title bar** | Top | Drag to move the widget |
| **Refresh button** (&#x21bb;) | Title bar, right | Manual refresh |
| **Hamburger menu** (&#x2261;) | Title bar, right | Open settings menu |
| **Close button** (&#x2715;) | Title bar, far right | Close widget |
| **White dot** (&#x25cf;) | Bottom-left corner | Expand/collapse extra bars |
| **Ochre dot** (&#x25cf;) | Bottom-right corner | Drag to resize; double-click for essential mode |

### Display Modes

#### Normal Mode (default)
- Title bar with icon, title, last update time, refresh button, menu, close button
- Session usage bar with label, percentage, and countdown to reset
- Expandable section with weekly and Sonnet bars (toggle via white dot)

#### Essential Mode (double-click ochre dot)
- Title bar and section labels hidden
- Only the session usage bar visible, with percentage shown inside the bar
- Compact controls at bottom-right: close (x), refresh (&#x21bb;), time display
- Content area becomes draggable
- Double-click ochre dot again to return to normal mode

### Menu Options

| Item | Description |
|------|-------------|
| &#x21bb; Aggiorna | Manual refresh |
| &#x21c5; Modalita normale/essential | Toggle between display modes |
| &#x2692; Rinnova sessione... | Opens claude.ai and shows session key renewal dialog |
| &#x2699; Apri config.json | Opens config file in Notepad |
| &#x2715; Chiudi | Close widget |

### Bar Colors

Bars change color based on usage percentage:

| Percentage | Color | Hex |
|:----------:|-------|:---:|
| 0-74% | Accent color (per-metric) | varies |
| 75-89% | Orange (warning) | `#E8A838` |
| 90-100% | Red (critical) | `#E85858` |

### Reset Time Display

The sub-label below each bar shows when the usage window resets:

- **Same day**: `alle 18:00 (3h 26min)` — "at 18:00 (3h 26min remaining)"
- **Different day**: `sab 11:00 (2gg 5h)` — "Sat 11:00 (2 days 5h remaining)"
- **Already reset**: `tra poco` — "shortly"
- **Not used**: `non utilizzato`

### Countdown Timer

After each data fetch, a countdown timer appears next to the last update time showing seconds until the next automatic refresh (e.g., `14:30 (287s)`).

## Session Key Renewal

When the session key expires (HTTP 401/403), the widget shows an error message. To renew:

1. Click menu (&#x2261;) -> "Rinnova sessione"
2. Browser opens to claude.ai
3. A dialog appears with step-by-step instructions
4. Paste the new `sessionKey` cookie value
5. Click "Salva e Aggiorna" or press Enter
6. The widget validates the key format (must start with `sk-ant-`) and refreshes

The widget also auto-detects session key rotation from API response `Set-Cookie` headers and updates the config transparently.

## Technical Details

- **Language**: Python 3.14 (compiled with PyInstaller)
- **GUI Framework**: tkinter with Win32 API integration via ctypes
- **API Client**: curl subprocess (avoids Python SSL certificate issues)
- **Single Instance**: Win32 named mutex (`ClaudeUsageWidget_SingleInstance`)
- **DPI Awareness**: Per-monitor DPI awareness v2 via `SetProcessDpiAwareness(2)`
- **Window Style**: `overrideredirect(True)` with DWM rounded corners (attribute 33, value 2)
- **Transparency**: Window alpha 0.94
