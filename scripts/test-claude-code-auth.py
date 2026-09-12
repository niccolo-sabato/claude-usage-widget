"""Self-check for the Claude Code login path (no UI).

Loads widget.pyw as a module, fetches usage through the OAuth endpoint with
the token in ~/.claude/.credentials.json, and checks the account plumbing
(mirror, has_credentials, fetch_usage dispatch, self-test, missing-file error).
Run:  python scripts/test-claude-code-auth.py
"""
import importlib.machinery
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, '..', 'src', 'widget.pyw')
loader = importlib.machinery.SourceFileLoader('widget', SRC)
spec = importlib.util.spec_from_file_location('widget', SRC, loader=loader)
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)

d = w.fetch_usage_claude_code()
assert isinstance(d['five_hour']['utilization'], (int, float))
assert isinstance(d['seven_day']['utilization'], (int, float))
print('five_hour', d['five_hour']['utilization'],
      'seven_day', d['seven_day']['utilization'],
      'scoped', w.scoped_model(d))

cfg = {'accounts': [{'id': 'x', 'name': 'Claude Code', 'session_key': '',
                     'org_id': '', 'auth': w.CC_AUTH}],
       'active_account': 'x'}
w.mirror_active(cfg)
assert cfg['auth'] == w.CC_AUTH
assert w.has_credentials(cfg)
assert not w.has_credentials({'session_key': '', 'org_id': ''})
data, rotation = w.fetch_usage(cfg)
assert rotation is None and data['five_hour']
status, detail = w._selftest_api(cfg)
assert status == 'ok', (status, detail)
print('selftest_api', status, detail)

w.CC_CREDS = os.path.join(HERE, 'does-not-exist.json')
try:
    w.fetch_usage(cfg)
    raise SystemExit('FAIL: missing credentials did not raise')
except PermissionError as e:
    print('missing creds ->', e)
print('OK')
