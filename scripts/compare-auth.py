"""Compare curl vs urllib for the claude.ai usage API.

Reads the session key from the user's config.json, then makes the same
request with both transports and reports HTTP status / response length.
If both return 401, the session key itself has expired (renew in widget).
If curl returns 200 and urllib returns 401, urllib is broken.
"""
import json
import os
import ssl
import subprocess
import sys
import urllib.request
import urllib.error

CFG = os.path.join(os.environ['LOCALAPPDATA'], 'Claude Usage', 'config.json')
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36')

with open(CFG, 'r', encoding='utf-8') as f:
    cfg = json.load(f)
key = cfg['session_key']
org = cfg['org_id']
url = f'https://claude.ai/api/organizations/{org}/usage'
cookie = f'sessionKey={key}; lastActiveOrg={org}'

print(f'Testing org_id={org[:8]}... key={key[:20]}...{key[-10:]}')
print()

# 1) curl path (the v2.8.31 transport)
print('--- curl ---')
try:
    r = subprocess.run(
        ['curl', '-s', '-o', 'NUL', '-w', '%{http_code}',
         '-H', f'Cookie: {cookie}',
         '-H', f'User-Agent: {UA}',
         '-H', 'anthropic-client-platform: web_claude_ai',
         url],
        capture_output=True, text=True, timeout=20)
    print(f'  HTTP {r.stdout.strip() or "?"}  stderr: {r.stderr.strip()[:200]}')
except Exception as e:
    print(f'  ERROR: {e}')

# 2) urllib path (the new transport)
print('--- urllib ---')
try:
    req = urllib.request.Request(
        url,
        headers={
            'Cookie': cookie,
            'User-Agent': UA,
            'Accept': '*/*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'identity',
            'anthropic-client-platform': 'web_claude_ai',
        },
    )
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
            body = resp.read()
            print(f'  HTTP {resp.status}  body bytes: {len(body)}')
    except urllib.error.HTTPError as e:
        body = e.read() if e.fp else b''
        print(f'  HTTP {e.code}  body bytes: {len(body)}  reason: {e.reason}')
except Exception as e:
    print(f'  ERROR: {e}')

print()
print('Diagnosis:')
print('  curl 200 + urllib 200 -> all good')
print('  curl 401 + urllib 401 -> session key expired (renew in widget)')
print('  curl 200 + urllib 401 -> urllib broken (regression in v2.8.32)')
