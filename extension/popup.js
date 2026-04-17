const status = document.getElementById('status');
const keyBox = document.getElementById('keyBox');
const btnFetch = document.getElementById('btnFetch');
const btnCopy = document.getElementById('btnCopy');

let sessionKey = '';

async function fetchKey() {
  status.className = 'status loading';
  status.textContent = 'Searching for session key...';
  btnFetch.style.display = 'none';
  btnCopy.style.display = 'none';
  keyBox.style.display = 'none';

  try {
    const cookie = await chrome.cookies.get({
      url: 'https://claude.ai',
      name: 'sessionKey'
    });

    if (cookie && cookie.value) {
      sessionKey = cookie.value;
      status.className = 'status success';
      status.textContent = 'Session key found!';
      keyBox.textContent = sessionKey;
      keyBox.style.display = 'block';
      btnCopy.style.display = 'block';
    } else {
      status.className = 'status error';
      status.innerHTML = 'Session key not found.<br>Make sure you are logged in to <a href="https://claude.ai" target="_blank" style="color:#DA7756">claude.ai</a>';
      btnFetch.style.display = 'block';
      btnFetch.textContent = 'Retry';
    }
  } catch (err) {
    status.className = 'status error';
    status.textContent = 'Error: ' + err.message;
    btnFetch.style.display = 'block';
    btnFetch.textContent = 'Retry';
  }
}

btnCopy.addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText(sessionKey);
    btnCopy.textContent = 'Copied!';
    btnCopy.classList.add('copied');
    setTimeout(() => {
      btnCopy.textContent = 'Copy to Clipboard';
      btnCopy.classList.remove('copied');
    }, 2000);
  } catch {
    // Fallback
    const ta = document.createElement('textarea');
    ta.value = sessionKey;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    ta.remove();
    btnCopy.textContent = 'Copied!';
    btnCopy.classList.add('copied');
    setTimeout(() => {
      btnCopy.textContent = 'Copy to Clipboard';
      btnCopy.classList.remove('copied');
    }, 2000);
  }
});

btnFetch.addEventListener('click', fetchKey);

// Auto-fetch on popup open
fetchKey();
