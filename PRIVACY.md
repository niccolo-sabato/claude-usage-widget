# Privacy Policy

**Claude Session Key** (Chrome extension) and **Claude Usage Widget** (desktop application).

Last updated: 2026-09-13

## Summary

**We do not collect, store, transmit, sell, or share any user data.**

Everything runs locally on the user's machine. No telemetry. No analytics. No external servers other than Claude's own, which the user is already signed in to.

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

- Reads the credentials of the selected account from a local config file: a `sessionKey` and `org_id`, the Claude Code login described below, or both.
- Makes authenticated HTTPS requests to `claude.ai/api/organizations/{org_id}/usage` with the session key - the same endpoint `claude.ai` itself uses to show usage data in the user's browser - and to `api.anthropic.com/api/oauth/usage` and `api.anthropic.com/api/oauth/profile` with the Claude Code token, which is what that token is issued for.
- Stores configuration locally in `%LOCALAPPDATA%\Claude Usage\config.json`: position, size, language, theme, the session key, and for each account the identity the profile endpoint returns (e-mail address, organisation, plan), which is how the widget knows whose numbers it is showing.
- Writes diagnostic logs locally to `%LOCALAPPDATA%\Claude Usage\widget.log`. No credential is ever written to them, and text arriving from outside the program (the output of `curl`, for instance) has session keys, tokens, organisation ids, e-mail addresses and the Windows account name stripped out before it is stored.

No data leaves the user's computer except those authenticated requests, both to services the user's own Claude account is already signed in to.

## The Claude Code login

An account can be read through the login Claude Code keeps on the same machine, instead of a session key. When it is:

- The widget reads `%USERPROFILE%\.claude\.credentials.json`, the file Claude Code writes when you sign in, and takes the access token and the plan name from it.
- It never writes to that file. Signing in and renewing the token stay Claude Code's business.
- It never uses the refresh token, and never copies the access token into its own configuration or anywhere else on disk.
- The access token is sent only to `api.anthropic.com`, which issued it.
- The e-mail address and organisation that come back are stored in the widget's config, so it can check that the token still belongs to the account it is about to display.

## Data collected by us

**None.** We (the developer) have no servers, no databases, no analytics. We never see any user data.

## Third parties

None. The widget talks to `claude.ai` and to `api.anthropic.com`, and to nothing else. Both are Anthropic's own endpoints, reached with the user's own credentials.

## Where credentials live

The session key is an authentication cookie used by the widget to call the Claude.ai API on behalf of the logged-in user. It is stored locally only, in:

- Chrome extension: nowhere (read on demand, copied to clipboard, not persisted).
- Desktop widget: `%LOCALAPPDATA%\Claude Usage\config.json` on the user's machine.

The Claude Code access token is not stored by the widget at all: it is read from Claude Code's own file each time it is needed.

The user can delete the config file at any time to remove the stored key, and unlink the Claude Code login from the account page without touching Claude Code itself.

## Changes to this policy

If this policy ever changes, the updated version will be published at the same URL: https://github.com/niccolo-sabato/claude-usage-widget/blob/main/PRIVACY.md

## Contact

For privacy questions, open an issue on the project's public repository:
https://github.com/niccolo-sabato/claude-usage-widget/issues

## Source code

The full source code for both the Chrome extension and the desktop widget is open and publicly available at:
https://github.com/niccolo-sabato/claude-usage-widget

Anyone can audit the code to verify this policy.
