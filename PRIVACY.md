# Privacy Policy

**Claude Session Key** (Chrome extension) and **Claude Usage Widget** (desktop application).

Last updated: 2026-10-21

## Summary

**We do not collect, store, transmit, sell, or share any user data.**

Everything runs locally on the user's machine. No telemetry. No analytics. No external servers other than Claude's own, which the user is already signed in to, and GitHub, which the widget asks for updates without sending anything about the user.

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
- Makes authenticated HTTPS requests to `claude.ai/api/organizations/{org_id}/usage` with the session key - the same endpoint `claude.ai` itself uses to show usage data in the user's browser - and to `api.anthropic.com/api/oauth/usage` and `api.anthropic.com/api/oauth/profile` with the Claude Code token, which is what that token is issued for. To renew a saved login, described below, it also sends that login's refresh token to `platform.claude.com/v1/oauth/token`, Anthropic's sign-in server, the same request Claude Code makes to renew its own.
- Reads, from the same usage endpoint, the usage-limit resets Anthropic has granted an account. When the user chooses to use one on an account's page, and confirms, it sends that request to `claude.ai/api/organizations/{org_id}/reset_rate_limits` with the session key, as claude.ai's own Usage page does. Nothing is redeemed without that confirmation.
- Stores configuration locally in `%LOCALAPPDATA%\Claude Usage\config.json`: position, size, language, theme, the session key, and for each account the identity the profile endpoint returns (e-mail address, organisation, plan), which is how the widget knows whose numbers it is showing.
- Keeps a copy of Claude Code's login for each account it can switch Claude Code to, as described below.
- Writes diagnostic logs locally to `%LOCALAPPDATA%\Claude Usage\widget.log`. No credential is ever written to them, and text arriving from outside the program (the output of `curl`, for instance) has session keys, tokens, organisation ids, e-mail addresses and the Windows account name stripped out before it is stored.
- When the user turns on **Start with Windows**, writes one value, `ClaudeUsageWidget`, under `HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run`, holding the path of the widget. Turning the option off removes it, and so does uninstalling the widget.

No data leaves the user's computer except those authenticated requests, both to services the user's own Claude account is already signed in to. The only other request is the update check described under Third parties, and it carries nothing about the user.

## The Claude Code login

An account can be read through the login Claude Code keeps on the same machine, instead of a session key. When it is:

- The widget reads `%USERPROFILE%\.claude\.credentials.json`, the file Claude Code writes when you sign in, and takes the access token and the plan name from it.
- The access token is sent only to `api.anthropic.com`, which issued it: to read the usage, and to ask which account the login belongs to.
- The refresh token of the login Claude Code is using is never sent anywhere: signing in, and renewing that login, stay Claude Code's business. Only the refresh token of a saved login is ever sent, as described below.
- The e-mail address and organisation that come back are stored in the widget's config, so it can check that the token still belongs to the account it is about to display.
- It also reads Claude Code's identity cache, `%USERPROFILE%\.claude.json`, for the e-mail address and organisation of the account signed in. That e-mail can be stored in a saved login as its label; the organisation in this file is never taken as proof of whose a login is.

### Switching Claude Code between accounts

Claude Code keeps one login per computer. To move it from one account to another, the widget keeps a copy of each account's login, called a saved login:

- Saved logins are stored in `%USERPROFILE%\.claude-accounts\`, one folder per account, and only the Windows user who owns them can read them. They hold what Claude Code's own file holds: the access token, the refresh token and the plan.
- Once you use the switch (Claude Code linked to one of your accounts, or a first login saved from an account page), the widget saves an account's login while Claude Code is signed in as that account, and brings it up to date when Claude Code renews it. Before saving a login under an account's name, it makes sure the login belongs to that account, from the server's answer or from a saved login holding the same token; a login it cannot identify is left alone.
- While Claude Code is signed in as another account, the widget can read an account's usage with the access token of its saved login, for as long as that token is valid. It is sent only to `api.anthropic.com`.
- Before it switches Claude Code to a saved login, and when you press Verify on one, the widget renews that saved login: it sends its refresh token to `platform.claude.com/v1/oauth/token`, as Claude Code does, and keeps the new tokens in the saved login at once, since the server retires the old ones as it answers. This is how it knows the server still accepts the login before writing it where Claude Code reads it. It never renews the login Claude Code is using, nor a saved login of the account Claude Code is signed in as; for that account, Verify asks `api.anthropic.com` about the login in use with its access token, and renews nothing. The refresh token is handed to `curl` on its standard input, never on a command line.
- The widget writes `%USERPROFILE%\.claude\.credentials.json` in two cases only: when you switch accounts, where it replaces the login part of the file and keeps everything else as it is; and after a sign-in, where it adds the organization the login belongs to, which Claude Code leaves out, once it has proved which account the login is. Before writing, it keeps a copy of the file in `%USERPROFILE%\.claude-accounts\_backups\`, and it keeps a copy of a saved login before replacing it.
- The same saving, switching and verifying can be run from the command line (`"Claude Usage.exe" --cc ...`). It follows the same rules and writes the same files; it reads the widget's config for the language and the names of the accounts, and never writes it.
- Deleting the `.claude-accounts` folder removes every saved login. Claude Code's own login is not touched.

## Data collected by us

**None.** We (the developer) have no servers, no databases, no analytics. We never see any user data.

## Third parties

None that receives user data. The widget talks to `claude.ai`, `api.anthropic.com` and `platform.claude.com`, Anthropic's own endpoints, with the user's own credentials. It also asks GitHub (`api.github.com`) about once a day whether a new version exists, and downloads it from GitHub when the user chooses to update: no credential and nothing about the user is sent, and the check can be turned off with `"update_check_enabled": false` in the config file.

## Where credentials live

The session key is an authentication cookie used by the widget to call the Claude.ai API on behalf of the logged-in user. It is stored locally only, in:

- Chrome extension: nowhere (read on demand, copied to clipboard, not persisted).
- Desktop widget: `%LOCALAPPDATA%\Claude Usage\config.json` on the user's machine.

The Claude Code tokens are never copied into the widget's config. They live in Claude Code's own file and, for the accounts the widget can switch to, in the saved logins described above.

The user can delete the config file at any time to remove the stored key, unlink the Claude Code login from the account page without touching Claude Code itself, and delete `%USERPROFILE%\.claude-accounts\` to remove the saved logins.

## Changes to this policy

If this policy ever changes, the updated version will be published at the same URL: https://github.com/niccolo-sabato/claude-usage-widget/blob/main/PRIVACY.md

## Contact

For privacy questions, open an issue on the project's public repository:
https://github.com/niccolo-sabato/claude-usage-widget/issues

## Source code

The full source code for both the Chrome extension and the desktop widget is open and publicly available at:
https://github.com/niccolo-sabato/claude-usage-widget

Anyone can audit the code to verify this policy.
