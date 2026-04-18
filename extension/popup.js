'use strict';

const EL = {
  status: document.getElementById('status'),
  keyBox: document.getElementById('keyBox'),
  btnFetch: document.getElementById('btnFetch'),
  btnCopy: document.getElementById('btnCopy'),
};

const COPIED_TIMEOUT_MS = 2000;
const CLAUDE_URL = 'https://claude.ai';
const COOKIE_NAME = 'sessionKey';
const SK_PREFIX = 'sk-ant-';

let sessionKey = '';
let copyTimer = null;

function show(el, display = 'block') { el.style.display = display; }
function hide(el) { el.style.display = 'none'; }

function setStatus(type, html) {
  EL.status.className = `status ${type}`;
  EL.status.replaceChildren();
  if (typeof html === 'string') {
    EL.status.textContent = html;
  } else {
    EL.status.append(...html);
  }
}

function loginLink(label) {
  const a = document.createElement('a');
  a.href = CLAUDE_URL;
  a.target = '_blank';
  a.rel = 'noopener';
  a.textContent = label;
  return a;
}

async function fetchKey() {
  setStatus('loading', 'Searching for session key...');
  hide(EL.btnFetch);
  hide(EL.btnCopy);
  hide(EL.keyBox);

  let cookie;
  try {
    cookie = await chrome.cookies.get({ url: CLAUDE_URL, name: COOKIE_NAME });
  } catch (err) {
    setStatus('error', `Error: ${err?.message ?? 'unknown'}`);
    EL.btnFetch.textContent = 'Retry';
    show(EL.btnFetch);
    return;
  }

  if (!cookie?.value) {
    setStatus('error', [
      document.createTextNode('Session key not found.'),
      document.createElement('br'),
      document.createTextNode('Make sure you are logged in to '),
      loginLink('claude.ai'),
    ]);
    EL.btnFetch.textContent = 'Retry';
    show(EL.btnFetch);
    return;
  }

  if (!cookie.value.startsWith(SK_PREFIX)) {
    setStatus('error', `Unexpected cookie format. Expected prefix "${SK_PREFIX}".`);
    EL.btnFetch.textContent = 'Retry';
    show(EL.btnFetch);
    return;
  }

  sessionKey = cookie.value;
  setStatus('success', 'Session key found!');
  EL.keyBox.textContent = sessionKey;
  show(EL.keyBox);
  show(EL.btnCopy);
}

async function copyToClipboard(text) {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch { /* fall through */ }
  }
  // Fallback for restricted contexts
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.setAttribute('readonly', '');
  ta.style.position = 'absolute';
  ta.style.left = '-9999px';
  document.body.appendChild(ta);
  ta.select();
  const ok = document.execCommand('copy');
  ta.remove();
  return ok;
}

function flashCopied() {
  clearTimeout(copyTimer);
  EL.btnCopy.textContent = 'Copied!';
  EL.btnCopy.classList.add('copied');
  copyTimer = setTimeout(() => {
    EL.btnCopy.textContent = 'Copy to Clipboard';
    EL.btnCopy.classList.remove('copied');
  }, COPIED_TIMEOUT_MS);
}

EL.btnCopy.addEventListener('click', async () => {
  if (!sessionKey) return;
  const ok = await copyToClipboard(sessionKey);
  if (ok) flashCopied();
  else setStatus('error', 'Copy failed. Select the key manually and press Ctrl+C.');
});

EL.btnFetch.addEventListener('click', fetchKey);

fetchKey();
