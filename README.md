# Claude Usage Widget for Windows

> **Track your Claude.ai usage limits in real time from a tiny widget that sits in an empty spot of your Windows 11 taskbar.** Free, open source, no telemetry.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Windows](https://img.shields.io/badge/Windows-10%2F11-blue.svg)](https://github.com/niccolo-sabato/claude-usage-widget/releases/latest)
[![Latest release](https://img.shields.io/github/v/release/niccolo-sabato/claude-usage-widget)](https://github.com/niccolo-sabato/claude-usage-widget/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/niccolo-sabato/claude-usage-widget/total.svg)](https://github.com/niccolo-sabato/claude-usage-widget/releases)

A lightweight desktop tool that shows your **Claude.ai session limit, weekly limit and Sonnet limit** as live progress bars so you never get cut off mid-conversation. The widget is **designed to sit on a free spot of the Windows 11 taskbar** in its compact **essential mode**: low profile, never overflows the screen, never blocks the windows you're working in. The position you choose is remembered across restarts, so once you place it once you never have to touch it again.

## Why this widget

If you use **Claude.ai** for hours every day (developers using Claude Code, writers, researchers, students), you've probably hit the dreaded *"You've reached your usage limit"* message at the worst possible moment. Anthropic doesn't show your usage anywhere visible while you work: you have to dig into the settings page.

This widget keeps that information one glance away:

- **Session bar (5 hours):** how much of the rolling 5-hour window you've burned
- **Weekly bar (7 days):** how much of your weekly quota you've used across all models
- **Sonnet bar (7 days):** Sonnet-specific usage for Pro / Max plan users
- **Reset countdown:** exactly when each bar refreshes (`reset 18:00 - 3h 26min`)

Colour codes track the urgency: orange below 75 %, yellow 75-89 %, red 90 % and above. You learn to manage long conversations and avoid surprises.

## Download

[**Download latest release**](https://github.com/niccolo-sabato/claude-usage-widget/releases/latest) - one-click installer (~15 MB)

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

## Designed to live on the Windows 11 taskbar

- **Sized for an empty spot of the taskbar.** Switch to **essential mode** (right-click → essential, or double-click the orange corner dot) and the widget collapses to a thin, low-profile bar that fits right above the taskbar without overflowing onto the desktop. You see your usage all the time without it ever getting in the way of the windows you're actually working in.
- **Stays out of the way of every other window.** The widget is a floating tool, hidden by default from the taskbar and from Win+Tab, so it never steals focus and never appears in the alt-tab rotation. It uses Win32 `WS_EX_NOACTIVATE` so clicking the widget itself never moves your foreground window.
- **Always on top, even over the taskbar.** Re-asserted topmost every 10 ms, plus on `<FocusOut>` and `<Visibility>` events, so it never slips behind another app or the taskbar's own panel.
- **Position is always saved.** Drag it once to wherever you want it (above the taskbar, on a secondary monitor, in a corner) and the position is persisted across restarts, refreshes and updates. Auto-saved on every successful refresh as a backstop against force-kills.
- **Smooth expand / collapse.** The optional second and third bars grow **upward**, never down, so the widget's bottom edge stays anchored exactly where you placed it on the taskbar.

## Features

### Live monitoring
- Three usage bars with reset times and a live countdown to the next refresh (`reset 18:00 - 3h 26min`)
- Auto refresh every 3 minutes by default, configurable from 10 seconds to 1 hour
- Instant refresh when a reset time is reached, no waiting for the next tick
- **Threshold notifications**: native Windows toast when session usage crosses 25 / 50 / 75 / 90 / 95 / 100 %, so you know you're approaching the limit even when you're focused on another window. Toggle on/off from the menu.
- **Optional Win11 taskbar progress overlay** under the app icon: bar fill width tracks session usage, colour escalates from accent (0-74 %) to yellow (75-89 %) to red (90 % +). Same overlay Edge or Explorer paint during a download.

### Display
- **Essential mode** for the taskbar: compact single bar, no titlebar, all controls condensed at the bottom right
- **Standard mode** for desktop placement: full titlebar, three bars with labels and section dividers
- Native Windows 11 design language: DWM rounded corners, translucent background, anti-aliased pill buttons rendered with a 4× supersample
- DPI-aware: tested at 100 %, 125 %, 150 %, 175 % and 200 % scaling, dialog auto-sizes to its content so nothing gets clipped on high-DPI displays

### Authentication and setup
- Companion **[Claude Session Key](https://chromewebstore.google.com/detail/claude-session-key/ppofmhjkjfinjpidlidepeonimpjmadj) Chrome extension** copies your session key with one click; works on Chrome, Edge, Brave and any Chromium browser
- Built-in setup guide with manual fallback (browser settings or DevTools) if you'd rather not install the extension
- **Multi-organization support**: if you belong to more than one Claude org (personal + work), the widget uses `/api/bootstrap` to track the org Claude.ai itself routes to, not just the first one in the API response

### Localization
- Three languages: **English, Italian (Italiano), Japanese (日本語)**
- The installer auto-selects the language matching your Windows system language

### Updates and maintenance
- Auto-update from GitHub releases with a single click; the new installer is downloaded, run silently and the widget relaunches itself
- Single-instance enforcement: launching the executable twice just brings the running widget to the front
- Crash-resilient: structured logs in `%LOCALAPPDATA%\Claude Usage\widget.log`, separate `crash.log` capped at 256 KB, geometry auto-saved every refresh
- Built-in "Open GitHub repo" entry in the settings menu and a matching button in the setup guide

### Privacy
- The widget sends data **only** to `claude.ai/api/organizations/*/usage`, the exact endpoint Claude.ai itself uses
- No analytics, no telemetry, no third-party services, no phoning home
- Session key stored locally in `%LOCALAPPDATA%\Claude Usage\config.json`
- 100 % open source: every line of code is auditable

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
- Refresh now
- Toggle Essential / Normal mode
- Refresh interval (10 to 3600 s)
- Notifications: ON / OFF (toast at threshold crossings)
- Taskbar icon: ON / OFF (shows the icon and the Win11 progress overlay)
- Update session key
- Open the Claude.ai usage page
- Open the project's GitHub repo
- Open `config.json` in Notepad
- Switch language (EN / IT / JA)
- Check for updates
- Quit

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

**Keywords:** Claude usage widget Windows, Claude usage bar, Claude usage toolbar, Claude.ai usage tracker Windows, Claude limits monitor, Claude desktop widget, Claude session limit tracker, Claude weekly limit, always-on-top Claude widget, Windows 11 taskbar Claude tool, Claude Code usage monitor, Anthropic Claude usage bar Windows.
