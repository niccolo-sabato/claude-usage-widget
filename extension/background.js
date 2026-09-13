/**
 * Claude Session Key - Background Service Worker
 *
 * Purpose: keep the toolbar badge in sync with the sessionKey state.
 *   green dot - valid session with >= 2 days remaining
 *   orange !  - session valid but expiring soon (< 2 days)
 *   red    !  - session expired
 *   no badge  - no session (user not logged in)
 *
 * It also decides what the toolbar icon opens: the side panel where the
 * browser supports it properly, the popup everywhere else.
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

/* ─── What the toolbar icon opens ──────────────────── */

async function isBrave() {
  try {
    if (navigator.brave && await navigator.brave.isBrave()) return true;
  } catch { /* not Brave */ }
  const brands = navigator.userAgentData?.brands ?? [];
  return brands.some((b) => /brave/i.test(b.brand));
}

/**
 * The side panel where it works, the popup where it does not.
 *
 * Brave shows an extension's side panel and drops it about a second later
 * (brave/brave-browser#32132), and a browser without chrome.sidePanel has no
 * panel to open. The manifest names no default popup, so the choice is made
 * here, and the popup is set explicitly whenever the panel is not an option.
 */
async function chooseSurface() {
  const panel = Boolean(chrome.sidePanel?.setPanelBehavior) && !(await isBrave());
  try {
    if (panel) {
      await chrome.action.setPopup({ popup: '' });
      await chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
      return;
    }
  } catch { /* fall back to the popup below */ }
  await chrome.action.setPopup({ popup: 'popup.html' });
}

// Every time the worker starts: the choice costs nothing and must never be
// missing when the icon is clicked.
chooseSurface();
chrome.runtime.onInstalled.addListener(chooseSurface);

// A click only reaches here when the action has no popup and the panel is not
// set to open by itself, which is the gap between a cold worker start and
// chooseSurface finishing. Opening the panel by hand needs the user gesture
// this listener is running on, so it cannot be done any later.
chrome.action.onClicked.addListener(async (tab) => {
  try {
    await chrome.sidePanel.open({ windowId: tab.windowId });
  } catch {
    await chooseSurface();
  }
});
