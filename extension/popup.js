/**
 * Claude Session Key - Popup controller
 * Follows a simple state machine: loading -> (success | error) -> copied
 */

import { getSessionKey, describeExpiry, CLAUDE_URL } from './lib/cookieService.js';

const COPIED_FEEDBACK_MS = 2000;
const AUTO_CLOSE_AFTER_COPY_MS = 900;

const EL = {
  status:     document.getElementById('status'),
  statusText: document.getElementById('statusText'),
  keyBox:     document.getElementById('keyBox'),
  expiryNote: document.getElementById('expiryNote'),
  btnFetch:   document.getElementById('btnFetch'),
  btnCopy:    document.getElementById('btnCopy'),
};

/** Current session key (kept only while popup is open). */
let sessionKey = '';
let copyResetTimer = null;

/* ─── Utilities ────────────────────────────────────── */

/** Localized string from _locales. */
const t = (key, ...args) => chrome.i18n.getMessage(key, args.map(String)) || key;

/** Apply [data-i18n] translations to the popup DOM. */
function applyI18n() {
  document.documentElement.lang = chrome.i18n.getUILanguage();
  document.querySelectorAll('[data-i18n]').forEach((el) => {
    const key = el.getAttribute('data-i18n');
    const msg = t(key);
    if (msg) el.textContent = msg;
  });
}

/** Replace children of a node with the given nodes / strings. */
function setContent(el, ...parts) {
  el.replaceChildren();
  for (const p of parts) {
    el.append(typeof p === 'string' ? document.createTextNode(p) : p);
  }
}

/** Show a node (clears hidden attribute). */
const show = (el) => el.removeAttribute('hidden');
/** Hide a node (sets hidden attribute). */
const hide = (el) => el.setAttribute('hidden', '');

function setStatus(type, nodes) {
  EL.status.className = `status ${type} fade-enter`;
  EL.status.replaceChildren();

  if (type === 'loading') {
    const sp = document.createElement('span');
    sp.className = 'spinner';
    sp.setAttribute('aria-hidden', 'true');
    EL.status.append(sp);
  } else {
    const icon = document.createElement('span');
    icon.className = 'status-icon';
    icon.setAttribute('aria-hidden', 'true');
    icon.textContent = type === 'success' ? '\u2714' : type === 'warning' ? '\u26A0' : '\u2716';
    EL.status.append(icon);
  }

  const textEl = document.createElement('span');
  if (nodes.length === 1 && typeof nodes[0] === 'string') {
    textEl.textContent = nodes[0];
  } else {
    for (const n of nodes) textEl.append(typeof n === 'string' ? document.createTextNode(n) : n);
  }
  EL.status.append(textEl);
}

function resetUi() {
  hide(EL.keyBox);
  hide(EL.expiryNote);
  hide(EL.btnFetch);
  hide(EL.btnCopy);
}

/* ─── State handlers ──────────────────────────────── */

function renderExpiry(expirationDate) {
  const exp = describeExpiry(expirationDate);
  if (!exp) {
    hide(EL.expiryNote);
    return;
  }
  EL.expiryNote.className = 'expiry-note';
  if (exp.expired) {
    EL.expiryNote.classList.add('error');
    EL.expiryNote.textContent = t('ui_expired');
  } else if (exp.days < 1) {
    EL.expiryNote.classList.add('warning');
    EL.expiryNote.textContent = t('ui_expiresSoon', exp.hours);
  } else {
    EL.expiryNote.textContent = t('ui_expiresIn', exp.days);
  }
  show(EL.expiryNote);
}

function renderError(result) {
  if (result.error === 'not_found') {
    const link = document.createElement('a');
    link.href = CLAUDE_URL;
    link.target = '_blank';
    link.rel = 'noopener';
    link.textContent = t('ui_openClaude');
    setStatus('error', t('ui_notLoggedIn'));
    EL.btnFetch.textContent = t('ui_retry');
    show(EL.btnFetch);
  } else if (result.error === 'invalid_format') {
    setStatus('error', t('ui_invalidFormat'));
    EL.btnFetch.textContent = t('ui_retry');
    show(EL.btnFetch);
  } else {
    setStatus('error', `${t('ui_genericError')}: ${result.message ?? ''}`.trim());
    EL.btnFetch.textContent = t('ui_retry');
    show(EL.btnFetch);
  }
}

async function loadKey() {
  resetUi();
  setStatus('loading', t('ui_searching'));

  const result = await getSessionKey();

  if (!result.ok) {
    renderError(result);
    return;
  }

  sessionKey = result.value ?? '';
  setStatus('success', t('ui_found'));
  EL.keyBox.textContent = sessionKey;
  show(EL.keyBox);
  renderExpiry(result.expirationDate);
  show(EL.btnCopy);
  EL.btnCopy.focus({ preventScroll: true });
}

/* ─── Clipboard ───────────────────────────────────── */

async function copyToClipboard(text) {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch { /* fall through */ }
  }
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.setAttribute('readonly', '');
  ta.style.cssText = 'position:absolute;left:-9999px;top:-9999px;';
  document.body.appendChild(ta);
  ta.select();
  const ok = document.execCommand('copy');
  ta.remove();
  return ok;
}

function flashCopied() {
  clearTimeout(copyResetTimer);
  EL.btnCopy.textContent = t('ui_copied');
  EL.btnCopy.classList.add('copied');
  copyResetTimer = setTimeout(() => {
    EL.btnCopy.textContent = t('ui_copy');
    EL.btnCopy.classList.remove('copied');
  }, COPIED_FEEDBACK_MS);
}

async function handleCopy() {
  if (!sessionKey) return;
  const ok = await copyToClipboard(sessionKey);
  if (ok) {
    flashCopied();
    setTimeout(() => window.close(), AUTO_CLOSE_AFTER_COPY_MS);
  } else {
    setStatus('error', t('ui_copyFailed'));
  }
}

/* ─── Bootstrap ───────────────────────────────────── */

EL.btnFetch.addEventListener('click', loadKey);
EL.btnCopy.addEventListener('click', handleCopy);

document.addEventListener('keydown', (e) => {
  // Enter anywhere triggers the primary action
  if (e.key === 'Enter' && !e.target.closest('a')) {
    e.preventDefault();
    if (!EL.btnCopy.hasAttribute('hidden')) handleCopy();
    else if (!EL.btnFetch.hasAttribute('hidden')) loadKey();
  }
});

(function init() {
  applyI18n();
  loadKey();
})();
