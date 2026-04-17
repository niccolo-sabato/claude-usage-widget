# Claude Usage Widget

A floating always-on-top desktop widget for Windows 11 that displays real-time
Claude.ai usage statistics. Built with Python + tkinter, styled in the Windows 11
Material design language: rounded corners, translucent background, pill-shaped
progress bars.

Shows three metrics with live progress bars:

| Metric | Description |
|---|---|
| Sessione Corrente | 5-hour rolling session window |
| Tutti i modelli (7gg) | 7-day aggregate across all models |
| Solo Sonnet (7gg) | 7-day Sonnet-only usage |

Version: **2.5.7**

---

## Installation (end users)

1. Download `ClaudeUsage-Setup.exe` from the latest GitHub Release.
2. Run the installer (requires admin).
3. Launch **Claude Usage** from the desktop/start menu.
4. First-time setup: paste your Claude.ai `sessionKey`. See
   [guide/session-key-guide.html](guide/session-key-guide.html) or the companion
   Chrome extension (below).

User data (config, logs) lives in `%LOCALAPPDATA%\Claude Usage\`.

### Session key setup (2 methods)

**Method A — Chrome extension (recommended)**

Install the unpacked extension from `extension/` (or from the release ZIP). Click
the extension icon while logged into claude.ai; it copies your session key
directly into the widget's config.

**Method B — Manual**

1. Open claude.ai while logged in.
2. DevTools (F12) -> Application -> Cookies -> `https://claude.ai`.
3. Copy the `sessionKey` value.
4. In the widget's menu, choose "Rinnova sessione" and paste.

Full instructions: [guide/session-key-guide.html](guide/session-key-guide.html).

---

## Development

### Prerequisites

- Python 3.14 (tkinter included)
- [PyInstaller](https://pyinstaller.org)
- [Inno Setup 6](https://jrsoftware.org/isinfo.php) (for building the installer)

### Run from source

```powershell
pythonw "src\widget.pyw"
```

### Build the installer

```powershell
.\scripts\build.ps1
```

This will:

1. Stop any running `Claude Usage.exe`.
2. Run PyInstaller (onedir, windowed) with icons bundled.
3. Copy `guide/` alongside the exe.
4. Run Inno Setup to build `releases\ClaudeUsage-Setup.exe`.
5. Zip `extension/` into `releases\claude-session-key-extension.zip`.

### Package just the extension

```powershell
.\scripts\package-extension.ps1
```

---

## Project structure

```
claude-usage-widget/
├── src/
│   ├── widget.pyw                      # Main application (tkinter)
│   └── assets/                         # Runtime icons (bundled by PyInstaller)
│       ├── claude.ico
│       ├── icon-app.png
│       ├── icon-bar.png
│       └── icon-symbol.png
├── extension/                          # Chrome extension (session key helper)
│   ├── manifest.json
│   ├── popup.html
│   ├── popup.js
│   └── icon16/48/128.png
├── guide/
│   └── session-key-guide.html          # In-app session key walkthrough
├── installer/
│   ├── claude-usage-setup.iss          # Inno Setup script
│   └── chrome-store-assets/            # Promo images + screenshots
├── docs/                               # Architecture, design, features, changelog
├── scripts/
│   ├── build.ps1                       # Full build pipeline
│   └── package-extension.ps1           # Extension ZIP only
├── releases/                           # Built artifacts (gitignored)
├── .gitignore
├── LICENSE
├── CHANGELOG.md
└── README.md
```

`build/`, `dist/`, `releases/*.exe`, `releases/*.zip`, `config.json`, and `*.log`
are gitignored. The `releases/` folder itself is tracked (as a placeholder) but
its built artifacts are published only via GitHub Releases.

---

## Documentation

In-depth docs live under [`docs/`](docs/):

- [README.md](docs/README.md) — detailed user guide
- [ARCHITECTURE.md](docs/ARCHITECTURE.md) — internals & threading model
- [FEATURES.md](docs/FEATURES.md) — full feature list
- [CONFIGURATION.md](docs/CONFIGURATION.md) — `config.json` reference
- [DESIGN.md](docs/DESIGN.md) — visual design system
- [CHANGELOG.md](docs/CHANGELOG.md) — detailed history

---

## License

[MIT](LICENSE) — Copyright (c) 2026 Niccolò Sabato / Omakase.
