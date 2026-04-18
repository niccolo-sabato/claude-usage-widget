# Changelog

All notable changes to the Claude Usage Widget.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this
project adheres to [Semantic Versioning](https://semver.org/).

For a more detailed narrative of early development phases, see
[`docs/CHANGELOG.md`](docs/CHANGELOG.md).

---

## [Unreleased]

## [2.8.2] - 2026-04-18

### Changed
- **Unified design system across all dialogs and menus.** One set of module-level
  fonts (`FT_DLG_TITLE`, `FT_DLG_H`, `FT_DLG_BODY`, `FT_DLG_HINT`,
  `FT_DLG_BTN_B`, `FT_DLG_BTN`, `FT_EMOJI_11`), one set of colors
  (`SOFT_BG`/`SOFT_BG_HV`, `PRIMARY_HV`, `FOCUS_RING`, `CLOSE_HV`), and one set
  of pill padding constants — every dialog now consumes them via
  `_primary_pill()`, `_secondary_pill()`, `_build_dialog_frame()`, so Connect,
  Save, Install, Cancel, Open guide, etc. are all visually identical.
- **Consistent positioning** via `_place_popup()` (dialogs) and
  `_place_submenu()` (hamburger / language menus). Each popup tries its
  preferred side first, falls back to the opposite edge, and is clamped to the
  visible screen with a taskbar margin — no more surprises on multi-monitor or
  near-edge layouts.
- **Refresh-interval dialog redesigned** to match the session-key dialog: same
  title bar (34 px), same 20 px body padding, same focus ring on the entry,
  pill Save (primary) + pill Cancel (secondary), bigger default size 460x260.
- **Update dialog redesigned** with the shared chrome: bigger 500x380 footprint,
  proper section headers, pill Install / Cancel / Open release page, progress
  bar tucked under the changelog.
- **Language submenu rebuilt** with a header line, checkmark column for the
  current language, generous row padding, minimum width 200 px.
- **Main menu** now aligns icon column widths (width=3), uses consistent
  vertical padding (6 px per row), adds a symmetric right margin, and uses the
  shared submenu positioning.
- **Session-key dialog** consolidated to the shared helpers; no more
  duplicated chrome code inside the method.
- **Info toast** now uses `_place_popup(prefer='below')` so its placement is
  predictable and screen-aware.

### Removed
- Per-dialog inline constants, duplicated title-bar builders, duplicated
  drag handlers, and duplicated positioning math. The whole dialog/menu
  surface is now smaller and easier to evolve.

## [2.8.1] - 2026-04-18

### Changed
- **Session key dialog redesigned in Material W11 style.** Title bar has proper
  padding, body uses a two-step layout (*Step 1: Where do I find it?* -> open
  guide, *Step 2: Paste here* -> input + Connect) with clear typography. Setup
  mode shows a welcome hint up top.
- **Guide button is now a pill-shaped Canvas button** with the book emoji
  rendered at Segoe UI Emoji 11 instead of a flat rectangular label.
- **Connect button** also restyled as a pill, sized for prominence; disabled
  state while verifying no longer swaps colors abruptly.
- **New Cancel button** beside Connect plus Esc key binding for consistent
  exit options.
- **Entry field** got bigger padding (ipady 7, ipadx 10), thinner focus ring
  (1 px via a wrapper frame), and a darker focus color tuned to the Claude
  palette.
- Dialog size 460x320 (was 400x220) with screen-edge clamping so it never
  opens off-screen on small or narrow layouts.
- Copy tightened: single ellipsis instead of "...", clearer section headings
  in all three locales.

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
