# Changelog

All notable changes to the Claude Usage Widget.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this
project adheres to [Semantic Versioning](https://semver.org/).

For a more detailed narrative of early development phases, see
[`docs/CHANGELOG.md`](docs/CHANGELOG.md).

---

## [Unreleased]

### Changed
- Reorganized repository layout: source moved to `src/`, runtime icons to
  `src/assets/`, installer script to `installer/`, built artifacts to
  `releases/`, and a `scripts/` folder with PowerShell build/packaging scripts.
- Updated `widget.pyw` resource resolution to search both `_RES` root and
  `_RES/assets` so it works from source and from the bundled exe.
- Updated `claude-usage-setup.iss` to reference the new paths.

## [2.5.7] - 2026-03-26

Current stable release. See [`docs/CHANGELOG.md`](docs/CHANGELOG.md) for the
full development history (Phases 1-8) covering:

- Core widget and Windows 11 integration (rounded corners, DPI, transparency)
- Essential mode, expand/collapse, geometry persistence
- Crash protection, single-instance mutex, rotating logs
- Win+Tab visibility and keep-topmost loop
- Session key renewal dialog and auto-rotation detection
- Countdown timer, multi-monitor support, Italian locale formatting
- PyInstaller onedir packaging with AppData-based config migration
