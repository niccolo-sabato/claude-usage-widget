/**
 * Claude Session Key - Background Service Worker
 *
 * Purpose: keep the toolbar badge in sync with the sessionKey state.
 *   green dot - valid session with >= 2 days remaining
 *   orange !  - session valid but expiring soon (< 2 days)
 *   red    !  - session expired
 *   no badge  - no session (user not logged in)
 */

import { getSessionKey, describeExpiry } from './lib/cookieService.js';

const BADGE = {
  FRESH:   { text: '',  color: '#6BC275' },
  STALE:   { text: '!', color: '#E8A838' },
  EXPIRED: { text: '!', color: '#E85858' },
  MISSING: { text: '',  color: '#E85858' },
};

async function updateBadge() {
  const result = await getSessionKey();
  let badge = BADGE.MISSING;

  if (result.ok) {
    const exp = describeExpiry(result.expirationDate);
    if (!exp) badge = BADGE.FRESH;
    else if (exp.expired) badge = BADGE.EXPIRED;
    else if (exp.days < 2) badge = BADGE.STALE;
    else badge = BADGE.FRESH;
  }

  try {
    await chrome.action.setBadgeBackgroundColor({ color: badge.color });
    await chrome.action.setBadgeText({ text: badge.text });
  } catch { /* action not available in all contexts */ }
}

chrome.cookies.onChanged.addListener(({ cookie }) => {
  if (cookie?.domain?.includes('claude.ai') && cookie?.name === 'sessionKey') {
    updateBadge();
  }
});

chrome.runtime.onInstalled.addListener(updateBadge);
chrome.runtime.onStartup.addListener(updateBadge);
