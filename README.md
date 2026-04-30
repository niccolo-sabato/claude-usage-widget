# Claude Usage Widget for Windows

> **Track your Claude.ai usage limits in real time from a floating widget that lives above your Windows taskbar.** Free, open source, no telemetry.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Windows](https://img.shields.io/badge/Windows-10%2F11-blue.svg)](https://github.com/niccolo-sabato/claude-usage-widget/releases/latest)
[![Latest release](https://img.shields.io/github/v/release/niccolo-sabato/claude-usage-widget)](https://github.com/niccolo-sabato/claude-usage-widget/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/niccolo-sabato/claude-usage-widget/total.svg)](https://github.com/niccolo-sabato/claude-usage-widget/releases)

A lightweight desktop tool that shows your **Claude.ai session limit, weekly limit and Sonnet limit** as live progress bars, so you never get cut off mid-conversation. Sits permanently on top of the Windows taskbar in a compact essential mode, or anywhere on screen in standard mode.

If you've been looking for a **Claude Usage Bar / Claude Usage toolbar / Claude Usage tracker for Windows**, this is the one.

## Why this widget

If you use **Claude.ai** for hours every day (developers using Claude Code, writers, researchers, students), you've probably hit the dreaded *"You've reached your usage limit"* message at the worst possible moment. Anthropic doesn't show your usage anywhere visible while you work: you have to dig into the settings page.

This widget keeps that information one glance away:

- **Session bar (5 hours):** how much of the rolling 5-hour window you've burned
- **Weekly bar (7 days):** how much of your weekly quota you've used across all models
- **Sonnet bar (7 days):** Sonnet-specific usage for Pro / Max plan users
- **Reset countdown:** exactly when each bar refreshes (`reset 18:00 - 3h 26min`)

Color codes match the urgency: orange below 75 %, yellow 75 to 89 %, red 90 % and above. You learn to manage long conversations and avoid surprises.

## Download

[**Download v2.8.32 (latest)**](https://github.com/niccolo-sabato/claude-usage-widget/releases/latest) - one-click installer (~15 MB)

| | |
|---|---|
| **Platform** | Windows 10 (1809+) and Windows 11, x64 |
| **Install size** | ~50 MB on disk |
| **Auto-update** | Yes, in-app via GitHub Releases |
| **Telemetry** | None |
| **Source** | 100 % open source ([widget.pyw](src/widget.pyw)) |

## Setup in under a minute

1. **Install** `ClaudeUsage-Setup.exe` and launch the widget.
2. **Install the [Claude Session Key](https://chromewebstore.google.com/detail/claude-session-key/ppofmhjkjfinjpidlidepeonimpjmadj) Chrome extension** (Chrome / Edge / Brave / any Chromium browser).
3. Open Claude.ai, click the extension icon, click **Copy to Clipboard**.
4. Paste the key into the widget's setup dialog. Done.

The widget connects to Claude.ai using the same browser session you're already logged into. No API key, no password, no OAuth.

> **Don't want to install the extension?** The setup guide built into the widget shows you how to grab the session key manually from your browser settings or DevTools. Two extra clicks.

## Features

### Display
- Three live usage bars with reset times and countdown
- **Essential mode** (compact, single bar): designed to sit above the taskbar
- **Standard mode** (full): all three bars with labels and dividers
- Smooth expand / collapse animation that always grows upward, so the bottom edge stays anchored on the taskbar
- Always above the taskbar (re-asserted topmost every 10 ms)
- Hidden from taskbar and Win+Tab (it's a floating tool, not a window)
- Native Windows 11 Material design: rounded corners, translucent background, anti-aliased pill buttons

### Behavior
- Auto refresh every 3 minutes (10 to 3600 seconds, configurable)
- Instant refresh when a reset time is reached
- Auto-update from GitHub releases with one click (no manual reinstall)
- Drag to move, drag the orange corner dot to resize, double-click it to toggle essential mode
- Single instance: launching it twice just brings the running widget to front
- Persistent geometry: position, size and mode survive restarts
- Crash-resilient: structured logging, signal handlers, geometry auto-saved on every refresh

### Localization
- Three languages: **English, Italian (Italiano), Japanese (日本語)**
- The installer auto-selects the language matching your Windows system language

### Privacy
- The widget sends data **only** to `claude.ai/api/organizations/*/usage`, the exact endpoint Claude.ai itself uses
- No analytics, no telemetry, no third-party services, no phoning home
- Session key stored locally in `%LOCALAPPDATA%\Claude Usage\config.json`
- 100 % open source: every line of code is auditable

## How it compares to alternatives

| | Claude Usage Widget (this) | [ClaudeUsageBar](https://github.com/Artzainnn/ClaudeUsageBar) (Mac) |
|---|---|---|
| Platform | Windows 10 / 11 | macOS 12+ |
| Auth | Chrome extension (1 click) or DevTools | Manual cookie copy from DevTools |
| Display modes | Compact (taskbar) + standard (3 bars) | Menu bar icon + popup |
| Languages | EN / IT / JA | EN |
| Auto-update | Built-in (GitHub Releases) | Manual |
| Footprint | ~50 MB installed, ~15 MB installer | DMG installer |

There isn't a native Windows equivalent of the popular Mac menu bar app for Claude usage. This project exists to fill that gap.

## Controls

### Title bar (standard mode)
| Element | Action |
|---|---|
| Claude icon + "Claude Usage" | Drag to move |
| Current time | System clock (HH:MM) |
| ↻ | Force immediate refresh |
| ≡ | Settings menu |
| ✕ | Quit (saves geometry) |

### Corner dots (bottom)
| Dot | Gesture | Action |
|---|---|---|
| White (left) | Click | Expand / collapse extra bars |
| Orange (right) | Drag horizontal | Resize widget width |
| Orange (right) | Double-click | Toggle essential ↔ standard |

### Settings menu (≡ or right-click in essential mode)
- ↻ Refresh
- ⇅ Toggle Essential / Normal mode
- ⏳ Refresh interval (10 to 3600 s)
- 🗝 Update session key
- ↗ Open the Claude.ai usage page
- { } Open `config.json` in Notepad
- 🌐 Switch language
- ⬆ Check for updates
- ✕ Quit

## Configuration

The widget manages its own config at `%LOCALAPPDATA%\Claude Usage\config.json`. Most users never need to edit it; for power users, see [docs/CONFIGURATION.md](docs/CONFIGURATION.md). Notable options:

```jsonc
{
  "session_key": "sk-ant-sid01-...",     // managed automatically
  "language": "en",                      // "en" | "it" | "ja"
  "refresh_ms": 180000,                  // auto-refresh cadence
  "always_check_updates": false,         // skip the 24h update-check throttle
  "debug_tk_scaling": null               // simulate higher DPI for layout testing
}
```

## Build from source

Requirements: Python 3.11+, [PyInstaller](https://pyinstaller.org/), [Inno Setup 6+](https://jrsoftware.org/isdl.php). Curl ships with Windows 10/11.

```powershell
.\scripts\build.ps1
```

Output: `releases/ClaudeUsage-Setup.exe`. The script handles PyInstaller, copies the guide, runs Inno Setup, and zips the Chrome extension.

For architecture details (single-file Python source, Win32 integration, DWM rounded corners, etc.), see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Known behaviors

- **Windows 10:** square corners (DWM rounded corners require Windows 11)
- **Session expiry:** Claude.ai session keys typically last about 30 days or until you log out. The widget shows a clear notice when this happens; use **≡ > Update session key**
- **TLS via curl:** the widget uses bundled `curl` (schannel + Windows CA store) instead of Python's `urllib`, because Cloudflare in front of claude.ai fingerprints the TLS handshake (JA3) and blocks Python's OpenSSL stack

## Contributing

This is a personal project shared because it might be useful to others. Bugs, feature requests and pull requests are welcome via [GitHub Issues](https://github.com/niccolo-sabato/claude-usage-widget/issues).

If the widget saves you a frustrating mid-conversation cut-off, a star on the repo is the best thank-you.

## Disclaimer

This widget reads usage data from `claude.ai/api/organizations/{id}/usage`, the same internal endpoint Claude.ai uses to render the usage page in your browser. The endpoint may change without notice. The project is not affiliated with or endorsed by Anthropic.

## License

MIT License © 2026 Niccolò Sabato. See [LICENSE](LICENSE).

---

**Keywords:** Claude usage bar Windows, Claude usage widget, Claude usage toolbar, Claude.ai usage tracker, Claude limits monitor, Claude desktop widget, Claude session limit tracker, Claude weekly limit, always on top Claude widget, Windows taskbar Claude tool.
