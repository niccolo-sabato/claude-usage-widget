# Privacy Policy

**Claude Session Key** (Chrome extension) and **Claude Usage Widget** (desktop application).

Last updated: 2026-04-17

## Summary

**We do not collect, store, transmit, sell, or share any user data.**

Everything runs locally on the user's machine. No telemetry. No analytics. No external servers (other than claude.ai itself, which the user is already using).

## What the Chrome extension does

The "Claude Session Key" Chrome extension performs exactly one action:

1. When the user clicks the extension icon, it reads the `sessionKey` cookie from `claude.ai` using the `chrome.cookies.get()` API.
2. When the user clicks "Copy to Clipboard", the cookie value is copied to the user's system clipboard.

That's it. The extension:

- Does not send the cookie (or anything else) to any server.
- Does not store the cookie in extension storage or any other persistent location.
- Does not log, monitor, or transmit any user activity.
- Does not access any website other than `claude.ai`.
- Does not contain any remote code. All logic is in `popup.js` bundled with the extension.

## What the desktop widget does

The "Claude Usage Widget" desktop application:

- Reads the user's `sessionKey` and `org_id` from a local config file.
- Makes authenticated HTTPS requests to `claude.ai/api/organizations/{org_id}/usage` - the same endpoint `claude.ai` itself uses to show usage data in the user's browser.
- Stores configuration (position, size, language, session key) locally in `%LOCALAPPDATA%\Claude Usage\config.json`.
- Writes diagnostic logs locally to `%LOCALAPPDATA%\Claude Usage\widget.log`.

No data leaves the user's computer except the authenticated request to `claude.ai` (the user's own service).

## Data collected by us

**None.** We (the developer) have no servers, no databases, no analytics. We never see any user data.

## Third parties

None. The only external service the widget communicates with is `claude.ai`, which is the user's own Claude.ai account.

## Session key storage

The session key is an authentication cookie used by the widget to call the Claude.ai API on behalf of the logged-in user. It is stored locally only, in:

- Chrome extension: nowhere (read on demand, copied to clipboard, not persisted).
- Desktop widget: `%LOCALAPPDATA%\Claude Usage\config.json` on the user's machine.

The user can delete this file at any time to remove the stored key.

## Changes to this policy

If this policy ever changes, the updated version will be published at the same URL: https://github.com/niccolo-sabato/claude-usage-widget/blob/main/PRIVACY.md

## Contact

For privacy questions, open an issue on the project's public repository:
https://github.com/niccolo-sabato/claude-usage-widget/issues

## Source code

The full source code for both the Chrome extension and the desktop widget is open and publicly available at:
https://github.com/niccolo-sabato/claude-usage-widget

Anyone can audit the code to verify this policy.
