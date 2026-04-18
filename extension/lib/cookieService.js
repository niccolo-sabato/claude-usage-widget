/**
 * Cookie service: single source of truth for reading the Claude.ai sessionKey.
 * Used by both popup and background service worker.
 */

export const CLAUDE_URL = 'https://claude.ai';
export const COOKIE_NAME = 'sessionKey';
export const EXPECTED_PREFIX = 'sk-ant-';

/** @typedef {'not_found'|'invalid_format'|'unknown'} SessionErrorCode */

/**
 * Result object returned by {@link getSessionKey}.
 * @typedef {Object} SessionResult
 * @property {boolean} ok
 * @property {string}  [value]          The cookie value, when ok.
 * @property {number}  [expirationDate] Seconds since epoch (from chrome.cookies).
 * @property {SessionErrorCode} [error]
 * @property {string}  [message]        Human-readable error message.
 */

/**
 * Fetch the Claude.ai sessionKey cookie.
 * @returns {Promise<SessionResult>}
 */
export async function getSessionKey() {
  let cookie;
  try {
    cookie = await chrome.cookies.get({ url: CLAUDE_URL, name: COOKIE_NAME });
  } catch (err) {
    return {
      ok: false,
      error: 'unknown',
      message: err?.message ?? 'unknown error',
    };
  }

  if (!cookie?.value) {
    return { ok: false, error: 'not_found' };
  }
  if (!cookie.value.startsWith(EXPECTED_PREFIX)) {
    return { ok: false, error: 'invalid_format' };
  }
  return {
    ok: true,
    value: cookie.value,
    expirationDate: cookie.expirationDate,
  };
}

/**
 * Compute remaining validity of a cookie expirationDate in seconds-since-epoch.
 * @param {number|undefined} expirationDate
 * @returns {{ expired: boolean, hours: number, days: number } | null}
 */
export function describeExpiry(expirationDate) {
  if (!expirationDate) return null;
  const now = Date.now() / 1000;
  const secs = expirationDate - now;
  if (secs <= 0) return { expired: true, hours: 0, days: 0 };
  const hours = Math.floor(secs / 3600);
  const days = Math.floor(hours / 24);
  return { expired: false, hours, days };
}
