"""Generate a clean Chrome Web Store screenshot of the extension popup.

Uses Edge headless to render the actual popup HTML with the success state
('Session key found! / Copy to Clipboard') at 2.5x zoom, centered on a
1280x800 dark canvas with a subtle vignette so the popup itself is the
focal point - no wasted empty background.

Run: python scripts/render-extension-screenshot.py
Output: installer/chrome-store-assets/screenshot-1280x800.png
"""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXT_DIR = os.path.join(ROOT, 'extension')
OUT = os.path.join(ROOT, 'installer', 'chrome-store-assets',
                   'screenshot-1280x800.png')

EDGE_CANDIDATES = [
    r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
    r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
]

# Final canvas (Chrome Web Store accepts 1280x800 or 640x400).
W, H = 1280, 800
# Popup native width is 320 px. Zoom 2.5 = 800 px wide, ~85% of frame.
ZOOM = 2.5


def find_edge():
    for p in EDGE_CANDIDATES:
        if os.path.isfile(p):
            return p
    raise SystemExit('Microsoft Edge not found')


def file_url(path):
    return 'file:///' + os.path.abspath(path).replace('\\', '/')


def main():
    # Inline popup.css so the render is self-contained and we don't need
    # to serve the file via a local server.
    with open(os.path.join(EXT_DIR, 'popup.css'), 'r',
              encoding='utf-8') as f:
        css = f.read()

    icon_url = file_url(os.path.join(EXT_DIR, 'icon48.png'))

    # Mockup popup HTML with the success state visible. Mirrors what
    # popup.js writes when chrome.cookies.get returns the key.
    popup_inner = f'''
    <h1>
      <img src="{icon_url}" alt="">
      <span>Claude Session Key</span>
    </h1>
    <div class="status success" role="status" aria-live="polite">
      <span class="status-icon">✓</span>
      <span>Session key found!</span>
    </div>
    <div class="key-box" aria-label="Session key">sk-ant-sid02-Z6vO...rUY2HgAA</div>
    <button type="button" class="btn btn-copy">Copy to clipboard</button>
    <div class="hint">Paste this key in the Claude Usage widget setup</div>
    '''

    page = f'''<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
{css}

/* Override popup.css's flat body for this screenshot mockup. The popup
   itself stays unmodified inside .popup. */
html, body {{
  width: {W}px;
  height: {H}px;
  margin: 0;
  padding: 0;
  background: #18181a;
  /* Soft radial vignette focuses attention on the popup. */
  background-image:
    radial-gradient(ellipse at center,
      rgba(218, 119, 86, 0.08) 0%,
      rgba(24, 24, 26, 0) 55%);
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
}}

.popup {{
  /* Reset the popup's natural body padding/width via a wrapper. */
  width: 320px;
  background: var(--bg);
  color: var(--fg);
  padding: 16px;
  border-radius: 14px;
  box-shadow:
    0 30px 60px rgba(0, 0, 0, 0.6),
    0 0 0 1px rgba(255, 255, 255, 0.04);
  transform: scale({ZOOM});
  transform-origin: center;
}}

.popup h1 {{ margin-bottom: 12px; }}
</style></head>
<body><div class="popup">{popup_inner}</div></body></html>
'''

    edge = find_edge()
    with tempfile.TemporaryDirectory() as tmp:
        html_path = os.path.join(tmp, 'screenshot.html')
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(page)
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        subprocess.run(
            [edge, '--headless=new', '--disable-gpu',
             f'--screenshot={OUT}',
             f'--window-size={W},{H}',
             '--default-background-color=ffffffff',
             '--hide-scrollbars',
             file_url(html_path)],
            check=True, capture_output=True, timeout=30)
    print(f'wrote {OUT}')
    print(f'   size: {W}x{H} px')
    print(f'   popup zoom: {ZOOM}x')


if __name__ == '__main__':
    main()
