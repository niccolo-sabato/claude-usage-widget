# Changelog

All notable changes to the Claude Usage Widget.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this
project adheres to [Semantic Versioning](https://semver.org/).

For a more detailed narrative of early development phases, see
[`docs/CHANGELOG.md`](docs/CHANGELOG.md).

---

## [Unreleased]

## [2.8.0] - 2026-04-18

### Added
- **Auto-update from GitHub releases.** The widget silently checks
  `api.github.com` at most once every 24 hours and, when a newer version is
  published, shows an orange banner at the top with *Update / Later / Skip*
  buttons. Clicking *Update* opens a dialog with the release changelog, downloads
  `ClaudeUsage-Setup.exe` with a live progress bar, launches it, and exits so the
  installer can replace files in place. Config, geometry, and language are
  preserved across updates.
- New menu entry **⬆ Check for updates…** for manual checks (always reports
  either "up to date" or opens the update dialog).
- New config fields: `update_check_enabled` (default `true`), `last_update_check`
  (unix timestamp for throttling), `skip_version` (mutes the banner for a
  specific version).
- New i18n keys for the update UI in English, Italian, and Japanese.

### Changed
- **Countdown cadence reworked** for smoother feedback near the refresh:
  above 60 s tick every 30 s, between 60 s and 30 s tick every 10 s (display
  updates at 60 / 50 / 40), and the final 30 s tick every second.
- Log messages normalized to English across the entire widget for consistency in
  a public repository.

### Removed
- Dead code: duplicate `_session_dialog` method (replaced long ago by
  `_session_key_dialog`) that was still living in the file.

### Fixed
- Helper scripts (`generate-screenshots.py`, `save-clipboard-image.ps1`,
  `save-screenshots-interactive.ps1`) now resolve `docs/images` relative to
  their own location instead of a hard-coded personal path.
- Docstring example in `widget.pyw` no longer references a personal path.

## [2.7.5] - 2026-04

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
