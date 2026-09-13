# -*- coding: utf-8 -*-
"""Claude Code OAuth profiles: read them, judge them, switch between them.

Claude Code authenticates from ONE file, `~/.claude/.credentials.json`, and two
top-level keys in it identify the account together and must always agree:

    claudeAiOauth     the tokens, carrying subscriptionType and rateLimitTier
    organizationUuid  the organization those tokens belong to

A saved profile is a snapshot of both, kept under `~/.claude-accounts/<name>/
profile.json`. Switching account means writing one profile's pair over the live
file. This module does the reading, the judging and the writing; it draws
nothing and imports no UI, so it can be exercised on its own against fake
credentials in a temporary directory.

WHY THE RULES ARE THE WAY THEY ARE

Each of these came from a failure with a date, and the tests in
`tests/test_cc_accounts.py` hold them in place:

1.  A switch takes effect IMMEDIATELY and for every session already running.
    One account is active at a time, so work already in progress starts
    consuming the new account the moment the file changes. Nothing to restart.

2.  There are THREE outcomes, never two: GOOD, BAD and UNKNOWN, plus a WARNING
    that never changes a verdict. UNKNOWN is not a drawer for everything that
    is not green: it means "I could not look", as opposed to "I looked and I do
    not like it". A 403 carrying a page is the filter in front of the site and
    says nothing about the credential; a 403 carrying data is a real refusal.

3.  The verdict is about the ACCOUNT. The account you are using right now
    works, by the very act of using it. What can be out of date is its
    snapshot, and that is a warning with a remedy, not a broken account.

4.  The refresh token ROTATES on every renewal, which happens by itself every
    few hours of work. So "testing" a profile by refreshing it does not verify
    it, it replaces it, leaving the saved profile holding a token the server
    has already retired. Verification here therefore uses the profile's own
    ACCESS token against a read-only endpoint, and when that token has expired
    the honest answer is UNKNOWN.

5.  The identity is the organization. Not the email, which lives in a display
    cache that lags behind a login, and not the profile name, which changes.

6.  Both identity keys move together or the switch is refused, and a null
    organization is never written: that exact state left every chat frozen on
    "signed in".

7.  Every write is atomic, backed up first, and read back afterwards. A write
    interrupted halfway must leave the previous file intact.

8.  No secret ever leaves this module. Tokens are reported as a fingerprint,
    and anything a subprocess prints is filtered before it is shown.

MESSAGES

Nothing here produces prose. A judgement carries a message KEY and the values
that fill it, and the caller translates: the widget speaks three languages, and
a module that returned Italian sentences would make two of them impossible.
"""
import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import time
from datetime import datetime

# ── where things live ──────────────────────────────────────────────────────
# Every path can be redirected, which is what lets the tests run against a fake
# home instead of the real credentials.
HOME = os.path.expanduser('~')


def _from_env(name, default):
    v = os.environ.get(name)
    return os.path.abspath(v) if v else default


def store():
    """The profile store."""
    return _from_env('CLAUDE_WIDGET_CC_STORE', os.path.join(HOME, '.claude-accounts'))


def backups_dir():
    return os.path.join(store(), '_backups')


def cred_file():
    """The live credentials Claude Code authenticates with."""
    return _from_env('CLAUDE_WIDGET_CC_CRED',
                     os.path.join(HOME, '.claude', '.credentials.json'))


def cache_file():
    """The identity cache. Display only, and it lags behind a login."""
    return _from_env('CLAUDE_WIDGET_CC_CACHE', os.path.join(HOME, '.claude.json'))


def on_bench():
    """True when the paths have been redirected, i.e. during a test."""
    return bool(os.environ.get('CLAUDE_WIDGET_CC_STORE')
                or os.environ.get('CLAUDE_WIDGET_CC_CRED'))


# One writer at a time inside this process. Claude Code can still rewrite the
# file from outside, which is why every write re-reads immediately before.
_LOCK = threading.Lock()

# ── the vocabulary of a judgement ──────────────────────────────────────────
GOOD, BAD, UNKNOWN, WARN = 'good', 'bad', 'unknown', 'warn'

PROFILE_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9 ._-]{0,40}$')
RESERVED_NAMES = ('_backups',)


def verdict(kind, key, data=None, remedy=''):
    """One judgement: what it is, what to say, what to do about it.

    `key` and `remedy` are message keys, never sentences. `data` holds the
    values a message needs (a date, a name, a count).
    """
    assert kind in (GOOD, BAD, UNKNOWN, WARN), kind
    return {'kind': kind, 'key': key, 'data': data or {}, 'remedy': remedy}


def worst(judgements):
    """The verdict a list of judgements adds up to.

    BAD wins over everything, then UNKNOWN. A WARN never lowers a verdict: it
    is an observation about something that works.
    """
    kinds = [j['kind'] for j in judgements]
    if BAD in kinds:
        return BAD
    if UNKNOWN in kinds:
        return UNKNOWN
    return GOOD


# ── small readers ──────────────────────────────────────────────────────────
def fingerprint(secret):
    """A token as something that can be compared and shown. Never the token."""
    if not secret:
        return ''
    return hashlib.sha256(str(secret).encode('utf-8')).hexdigest()[:16]


def read_json(path):
    """(data, failure). A missing file is UNKNOWN; a corrupt one is BAD."""
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f), None
    except FileNotFoundError:
        return None, verdict(UNKNOWN, 'cc_j_file_missing', {'path': path},
                             'cc_r_sign_in')
    except ValueError as e:
        return None, verdict(BAD, 'cc_j_file_corrupt',
                             {'path': path, 'why': str(e)}, 'cc_r_from_backup')
    except OSError as e:
        return None, verdict(UNKNOWN, 'cc_j_file_unreadable',
                             {'path': path, 'why': str(e)}, 'cc_r_check_permissions')


def from_cache(key, shape='[^"]+'):
    """One field of the identity cache, read WITHOUT parsing the file.

    `~/.claude.json` can hold duplicate keys differing only by the case of a
    drive letter (`J:\\` and `j:\\` from different sessions), which a JSON
    parser refuses outright. The fields needed here sit in one flat object, so
    a scoped regular expression gets them without the parse.
    """
    try:
        with open(cache_file(), encoding='utf-8', errors='replace') as f:
            raw = f.read()
    except OSError:
        return None
    m = re.search(r'"oauthAccount"\s*:\s*\{[^}]*?"%s"\s*:\s*"(%s)"' % (key, shape), raw)
    return m.group(1) if m else None


def when(ms):
    """(datetime, expired, days left) from a millisecond timestamp.

    All local time. A calculation in UTC, run in Japan between midnight and
    nine in the morning, answers with yesterday.
    """
    if not ms:
        return None, None, None
    try:
        t = datetime.fromtimestamp(int(ms) / 1000)
    except (ValueError, OSError, OverflowError, TypeError):
        return None, None, None
    left = (t - datetime.now()).total_seconds()
    return t, left < 0, left / 86400.0


_ISO = re.compile(r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?(Z|[+-]\d{2}:\d{2})?$')


def instant(text, default=None):
    """`savedAt` as a timestamp. PowerShell writes seven decimal places, which
    datetime.fromisoformat refuses, so the fraction is trimmed first."""
    m = _ISO.match(str(text or ''))
    if not m:
        return default
    base, frac, zone = m.group(1), (m.group(2) or '')[:7], m.group(3) or ''
    try:
        return datetime.fromisoformat(
            base + frac + ('+00:00' if zone == 'Z' else zone)).timestamp()
    except ValueError:
        return default


def without_secrets(lines):
    """Anything long enough to be a token, taken out of text before it is shown.

    A path can be long too, so a word carrying a separator is left alone.
    """
    out = []
    for line in lines:
        out.append(' '.join(
            '[redacted]' if len(w) >= 40 and '/' not in w and '\\' not in w else w
            for w in str(line).split(' ')))
    return out


# ── the live account ───────────────────────────────────────────────────────
def active():
    """Who Claude Code is signed in as on this machine, and how sure we are.

    Never raises: a missing or broken credentials file is one of the answers.
    """
    data, failure = read_json(cred_file())
    if failure:
        return {'read': False, 'judgement': failure, 'org': None, 'org_source': None,
                'email': None, 'plan': None, 'tier': None,
                'refresh_print': '', 'access_print': '',
                'access_expires': None, 'refresh_expires': None}

    oauth = data.get('claudeAiOauth') or {}
    org = data.get('organizationUuid')
    org_cache = from_cache('organizationUuid', '[0-9a-fA-F-]{36}')
    out = {
        'read': True,
        'org': org or None,
        'org_cache': org_cache,
        'org_source': 'live' if org else ('cache' if org_cache else None),
        'email': from_cache('emailAddress'),
        'plan': oauth.get('subscriptionType'),
        'tier': oauth.get('rateLimitTier'),
        'refresh_print': fingerprint(oauth.get('refreshToken')),
        'access_print': fingerprint(oauth.get('accessToken')),
        'access_expires': oauth.get('expiresAt'),
        'refresh_expires': oauth.get('refreshTokenExpiresAt'),
    }
    if not oauth.get('accessToken'):
        out['judgement'] = verdict(BAD, 'cc_j_live_no_token', {}, 'cc_r_sign_in_then_save')
    elif not org:
        # The normal state right after a login, not a fault.
        out['judgement'] = verdict(UNKNOWN, 'cc_j_live_no_org', {}, 'cc_r_save_repairs_org')
    elif not out['email']:
        out['judgement'] = verdict(UNKNOWN, 'cc_j_live_no_email', {}, 'cc_r_email_is_a_label')
    else:
        out['judgement'] = verdict(GOOD, 'cc_j_live_ok')
    return out


# ── the health of a saved profile ──────────────────────────────────────────
WARN_DAYS = 3


def health(prof):
    """What is wrong with a profile, as a list of judgements. Empty means fine.

    A missing `refreshTokenExpiresAt` is an expiry we do not know, NOT a dead
    profile: if the access token is still valid the profile is usable now, with
    a warning. Treating it as dead is what produced the "switch refused" the
    user ran into.
    """
    oauth = (prof or {}).get('claudeAiOauth') or {}
    if not oauth:
        return [verdict(BAD, 'cc_j_no_block', {}, 'cc_r_resave_while_active')]

    out = []
    if not oauth.get('accessToken'):
        out.append(verdict(BAD, 'cc_j_no_access', {}, 'cc_r_resave_while_active'))
    if not oauth.get('refreshToken'):
        out.append(verdict(BAD, 'cc_j_no_refresh', {}, 'cc_r_resave_while_active'))
    if not (prof or {}).get('organizationUuid'):
        out.append(verdict(BAD, 'cc_j_no_org', {}, 'cc_r_resave_while_active'))

    rt, rt_expired, rt_days = when(oauth.get('refreshTokenExpiresAt'))
    at, at_expired, _ = when(oauth.get('expiresAt'))
    if rt:
        if rt_expired:
            out.append(verdict(BAD, 'cc_j_refresh_expired', {'when': rt},
                               'cc_r_sign_in_then_save'))
        elif rt_days is not None and rt_days < WARN_DAYS:
            out.append(verdict(WARN, 'cc_j_refresh_soon', {'when': rt},
                               'cc_r_switch_and_save'))
    elif at_expired:
        out.append(verdict(UNKNOWN, 'cc_j_no_refresh_expiry_access_dead', {'when': at},
                           'cc_r_switch_and_save'))
    else:
        out.append(verdict(WARN, 'cc_j_no_refresh_expiry_access_alive', {'when': at},
                           'cc_r_resave_while_active'))
    return out


# ── the historic copies ────────────────────────────────────────────────────
def backups():
    """{organization: newest backup seen for it}.

    The time comes from the file's own modification time, never from its name:
    the copies carry two prefixes (`credentials.` and `credentials-orgrecovery.`)
    and a hyphen sorts before a dot, so alphabetical order is not chronological
    order. With the name-based rule, yesterday's copy declared this morning's
    profile out of date.
    """
    out = {}
    folder = backups_dir()
    if not os.path.isdir(folder):
        return out
    for name in os.listdir(folder):
        if not (name.startswith('credentials') and name.endswith('.bak')):
            continue
        path = os.path.join(folder, name)
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
            org = data.get('organizationUuid')
            if not org:
                continue
            seen = {'file': name,
                    'refresh_print': fingerprint(
                        (data.get('claudeAiOauth') or {}).get('refreshToken')),
                    'at': os.path.getmtime(path)}
        except (OSError, ValueError):
            continue
        if org not in out or seen['at'] > out[org]['at']:
            out[org] = seen
    return out


# ── the saved profiles ─────────────────────────────────────────────────────
def profile_names():
    folder = store()
    if not os.path.isdir(folder):
        return []
    return sorted(n for n in os.listdir(folder)
                  if os.path.isdir(os.path.join(folder, n))
                  and n not in RESERVED_NAMES)


def profile_path(name):
    return os.path.join(store(), name, 'profile.json')


def read_profile(name):
    """(profile, failure)."""
    return read_json(profile_path(name))


def profiles(live=None):
    """Every saved profile, with its health, and which one is signed in now.

    Recognition runs from the strongest proof down, and says which one it used:
      certain   the live refresh token has the same fingerprint as the profile
      certain   the organization in the live file is the profile's
      likely    the organization in the identity cache is the profile's
    """
    live = active() if live is None else live
    shots = backups()
    out = []
    for name in profile_names():
        data, failure = read_profile(name)
        if failure:
            out.append({'name': name, 'readable': False, 'judgements': [failure],
                        'verdict': failure['kind'], 'active': False})
            continue

        org = data.get('organizationUuid')
        print_ = fingerprint((data.get('claudeAiOauth') or {}).get('refreshToken'))
        is_active, why_active, certainty, in_step = False, '', None, False
        if print_ and live.get('refresh_print') and print_ == live['refresh_print']:
            is_active, why_active, certainty, in_step = True, 'cc_why_same_refresh', 'certain', True
        elif org and live.get('org') and org == live['org']:
            is_active, why_active, certainty = True, 'cc_why_same_org_live', 'certain'
        elif org and live.get('org_cache') and org == live['org_cache']:
            is_active, why_active, certainty = True, 'cc_why_same_org_cache', 'likely'

        saved_at = instant(data.get('savedAt'))
        if saved_at is None:
            try:
                saved_at = os.path.getmtime(profile_path(name))
            except OSError:
                saved_at = None

        if is_active:
            # The account in use works: that is what using it proves. What can
            # be stale is the snapshot, and that is a warning with a remedy.
            judgements = [j for j in [live.get('judgement')] if j and j['kind'] == BAD]
            if in_step:
                aligned = True
                judgements += [j for j in health(data) if j['kind'] != GOOD]
            else:
                aligned = False
                judgements.append(verdict(
                    WARN, 'cc_j_snapshot_behind',
                    {'saved': datetime.fromtimestamp(saved_at) if saved_at else None},
                    'cc_r_save_now'))
        else:
            judgements = health(data)
            aligned = None
            shot = shots.get(org)
            if shot and print_:
                if shot['refresh_print'] == print_:
                    aligned = True
                elif saved_at is None or shot['at'] > saved_at + 60:
                    aligned = False
                    judgements.append(verdict(
                        BAD, 'cc_j_profile_outdated',
                        {'when': datetime.fromtimestamp(shot['at'])},
                        'cc_r_switch_and_save'))
                else:
                    aligned = True

        judgements = judgements or [verdict(GOOD, 'cc_j_usable')]
        rt, _, _ = when((data.get('claudeAiOauth') or {}).get('refreshTokenExpiresAt'))
        at, _, _ = when((data.get('claudeAiOauth') or {}).get('expiresAt'))
        out.append({
            'name': name, 'readable': True,
            'email': data.get('email'), 'plan': data.get('subscriptionType'),
            'tier': data.get('rateLimitTier'), 'org': org,
            'saved_at': saved_at, 'refresh_expires': rt, 'access_expires': at,
            'refresh_print': print_, 'aligned': aligned, 'active': is_active,
            'certainty': certainty, 'why_active': why_active,
            'judgements': judgements, 'verdict': worst(judgements),
        })
    return out


def unrecognised(live, saved):
    """Why no saved profile was recognised as the live account.

    "Not catalogued" tells the user nothing and cannot be acted on. What can be
    acted on is the list of things that were tried and what each was missing,
    so the answer names them.
    """
    tried = []
    if not live.get('read'):
        tried.append('cc_try_live_unreadable')
    else:
        tried.append('cc_try_refresh_print' if live.get('refresh_print')
                     else 'cc_try_no_refresh_print')
        tried.append('cc_try_org_live' if live.get('org') else 'cc_try_no_org_live')
        tried.append('cc_try_org_cache' if live.get('org_cache')
                     else 'cc_try_no_org_cache')
    return verdict(UNKNOWN, 'cc_j_unrecognised',
                   {'tried': tried, 'profiles': [p['name'] for p in saved]},
                   'cc_r_save_current')


def state():
    """Everything the interface needs, from one read of the disk.

    One read, so that what is drawn cannot mix two different instants: the
    name of the active account coming from one read and its health from
    another is how a profile ends up described as somebody else.
    """
    live = active()
    saved = profiles(live)
    active_name = next((p['name'] for p in saved if p['active']), None)
    out = {'live': live, 'profiles': saved, 'active_name': active_name,
           'read_at': time.time(), 'store': store(), 'cred': cred_file()}
    if live.get('read') and active_name is None:
        out['unrecognised'] = unrecognised(live, saved)
    return out


def find_profile_for_org(org, saved=None):
    """The saved profile of an organization, if there is one."""
    if not org:
        return None
    for p in (saved if saved is not None else profiles()):
        if p.get('org') == org:
            return p
    return None


# ── the live check, which is a read and nothing else ───────────────────────
PROFILE_URL = 'https://api.anthropic.com/api/oauth/profile'


def judge_response(code, body):
    """What an HTTP answer says about a credential.

    The code alone is never enough: the API answers with data, the filter in
    front of the site answers with a page. Measured on 2026-09-13, a profile
    whose access token was still valid answered 200 with JSON and its refresh
    token was untouched afterwards.
    """
    looks_json = str(body or '').lstrip().startswith(('{', '['))
    if not code:
        return verdict(UNKNOWN, 'cc_j_no_answer', {}, 'cc_r_check_connection')
    if code == 200:
        if looks_json:
            return verdict(GOOD, 'cc_j_answered')
        return verdict(UNKNOWN, 'cc_j_200_not_data', {}, 'cc_r_try_again_soon')
    if code == 401:
        return verdict(BAD, 'cc_j_refused_401', {}, 'cc_r_sign_in_then_save')
    if code == 403:
        if looks_json:
            return verdict(BAD, 'cc_j_refused_403', {}, 'cc_r_sign_in_then_save')
        return verdict(UNKNOWN, 'cc_j_403_page', {}, 'cc_r_try_again_soon')
    if code == 429:
        return verdict(UNKNOWN, 'cc_j_rate_limited', {}, 'cc_r_wait_a_few_minutes')
    if 500 <= code < 600:
        return verdict(UNKNOWN, 'cc_j_server_error', {'code': code}, 'cc_r_try_later')
    return verdict(UNKNOWN, 'cc_j_unexpected_code', {'code': code}, 'cc_r_try_again_soon')


def check_alive(name, call):
    """Ask the server whether a saved profile still works, without spending it.

    `call(url, headers) -> (status code or None, body)` is supplied by the
    caller so this module needs no transport of its own, and so a test can
    answer with whatever it wants to try.

    🔴 This uses the profile's ACCESS token. The refresh token is not touched,
    because using it would replace it: the profile would be broken by the very
    act of checking it. When the access token has expired there is no way to
    check without spending the refresh, and the answer is UNKNOWN, said plainly.
    """
    data, failure = read_profile(name)
    if failure:
        return {'judgement': failure, 'org': None, 'email': None}

    oauth = data.get('claudeAiOauth') or {}
    token = oauth.get('accessToken')
    if not token:
        return {'judgement': verdict(BAD, 'cc_j_no_access', {},
                                     'cc_r_resave_while_active'),
                'org': None, 'email': None}
    at, at_expired, _ = when(oauth.get('expiresAt'))
    if at_expired:
        return {'judgement': verdict(UNKNOWN, 'cc_j_cannot_check_access_expired',
                                     {'when': at}, 'cc_r_proceed_or_refresh'),
                'org': None, 'email': None}

    code, body = call(PROFILE_URL, (f'Authorization: Bearer {token}',
                                    'anthropic-beta: oauth-2025-04-20'))
    judgement = judge_response(code, body)
    if judgement['kind'] != GOOD:
        return {'judgement': judgement, 'org': None, 'email': None}

    try:
        answer = json.loads(body)
    except ValueError as e:
        return {'judgement': verdict(UNKNOWN, 'cc_j_200_not_data', {'why': str(e)},
                                     'cc_r_try_again_soon'),
                'org': None, 'email': None}
    org = (answer.get('organization') or {}).get('uuid') or ''
    account = answer.get('account') or {}
    email = account.get('email_address') or account.get('email') or ''
    expected = data.get('organizationUuid')
    if expected and org and org != expected:
        # The tokens are alive but they are somebody else's: switching to them
        # would put one account's usage under another account's name.
        return {'judgement': verdict(BAD, 'cc_j_other_account', {'email': email},
                                     'cc_r_resave_while_active'),
                'org': org, 'email': email}
    return {'judgement': verdict(GOOD, 'cc_j_checked_alive', {'email': email}),
            'org': org, 'email': email,
            'tier': (answer.get('organization') or {}).get('rate_limit_tier')}


# ── writing ────────────────────────────────────────────────────────────────
def write_json_atomic(path, data):
    """Write JSON so that an interrupted write cannot damage what is there.

    A temporary file in the SAME directory (so the replace stays on one volume
    and is atomic), flushed to the disk, read back to prove it parses, and only
    then moved over the target. UTF-8 with no BOM: a BOM breaks Claude Code's
    own parser.
    """
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.' + os.path.basename(path) + '.',
                               suffix='.tmp', dir=folder)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        with open(tmp, encoding='utf-8') as f:
            json.load(f)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def ensure_store():
    """The store exists and only this user can read it: it holds live tokens."""
    folder = store()
    os.makedirs(backups_dir(), exist_ok=True)
    if os.name == 'nt' and not on_bench():
        import subprocess
        try:
            subprocess.run(
                ['icacls', folder, '/inheritance:r', '/grant:r',
                 f'{os.environ.get("USERNAME", "")}:(OI)(CI)F'],
                capture_output=True, timeout=15,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (OSError, subprocess.SubprocessError):
            pass
    return folder


def backup_live(tag='credentials'):
    """A copy of the live file before touching it, named like the others."""
    path = cred_file()
    if not os.path.exists(path):
        return None
    ensure_store()
    dst = os.path.join(backups_dir(),
                       '%s.%s.bak' % (tag, time.strftime('%Y%m%d-%H%M%S')))
    shutil.copyfile(path, dst)
    return dst


def valid_name(name):
    """A profile name that cannot break out of its folder, or ValueError."""
    clean = ' '.join(str(name or '').split())
    if not PROFILE_NAME.match(clean) or clean in RESERVED_NAMES:
        raise ValueError('cc_e_bad_name')
    return clean


def save_profile(name, live_data=None):
    """Snapshot the live credentials under `name`, repairing what a login left out.

    `live_data` lets a caller hand over the credentials IT has already read, so
    that the name and the content describe the same instant. A switch works out
    which profile the outgoing account belongs to from its own read; re-reading
    here would mean the name came from one read and the content from another.
    """
    name = valid_name(name)
    data = live_data
    if data is None:
        data, failure = read_json(cred_file())
        if failure:
            raise ValueError(failure['key'])
    oauth = data.get('claudeAiOauth') or {}
    if not oauth.get('accessToken'):
        raise ValueError('cc_e_no_live_token')

    org = data.get('organizationUuid')
    org_note = None
    if not org:
        # A login rewrites the live file WITHOUT the organization. Prefer a
        # PROOF over the cache: a saved profile carrying the same refresh token
        # IS this account, so its organization is certain. The cache can
        # describe a different account for minutes after a login, and stamping
        # a foreign organization onto a token is the failure this module exists
        # to prevent.
        mine = fingerprint(oauth.get('refreshToken'))
        for other in profile_names():
            p, bad = read_profile(other)
            if bad or not p.get('organizationUuid'):
                continue
            if mine and fingerprint(
                    (p.get('claudeAiOauth') or {}).get('refreshToken')) == mine:
                org, org_note = p['organizationUuid'], 'proof'
                break
        if not org:
            org = from_cache('organizationUuid', '[0-9a-fA-F-]{36}')
            org_note = 'cache' if org else None
        if org:
            # Repair the live file too. An incoherent live file is the state
            # that freezes every chat, so fixing only the snapshot would fix
            # the wrong half.
            backup_live('credentials-orgrecovery')
            data['organizationUuid'] = org
            write_json_atomic(cred_file(), data)

    # The email is a label from a cache that lags. When a profile of the same
    # name already describes the same organization, its recorded email wins.
    email = from_cache('emailAddress')
    previous, _ = read_profile(name)
    if (previous and previous.get('email') and org
            and previous.get('organizationUuid') == org):
        email = previous['email']

    prof = {
        'schema': 2,
        'name': name,
        'savedAt': datetime.now().astimezone().isoformat(),
        'email': email,
        'subscriptionType': oauth.get('subscriptionType'),
        'rateLimitTier': oauth.get('rateLimitTier'),
        'organizationUuid': org,
        'claudeAiOauth': oauth,
    }
    ensure_store()
    write_json_atomic(profile_path(name), prof)
    prof['_org_note'] = org_note
    return prof


def switch(name, *, knowingly=False, check=None):
    """Make `name` the account Claude Code authenticates with.

    The order matters and every step of it was paid for:

    1. refuse a profile that cannot work, BEFORE touching anything;
    2. an UNKNOWN profile is not refused, it is offered: the caller asks the
       user and comes back with knowingly=True;
    3. re-read the live file immediately before writing, because Claude Code
       rewrites it on every renewal;
    4. snapshot the OUTGOING account from that same read, so the account being
       left always has a fresh copy of itself;
    5. back up, write both identity keys together, atomically;
    6. read back from the disk and compare. A write that was not protested
       against is not a write that happened.

    Returns {'done': bool, 'judgement': ..., 'asks': bool, 'saved_out': name}.
    """
    try:
        name = valid_name(name)
    except ValueError:
        return {'done': False, 'asks': False,
                'judgement': verdict(BAD, 'cc_j_bad_name', {'name': name},
                                     'cc_r_pick_another_name')}

    target, failure = read_profile(name)
    if failure:
        return {'done': False, 'asks': False,
                'judgement': verdict(BAD, 'cc_j_no_such_profile',
                                     {'name': name, 'known': profile_names()},
                                     'cc_r_pick_existing')}

    live = active()
    target_print = fingerprint((target.get('claudeAiOauth') or {}).get('refreshToken'))
    # Already on this account? The fingerprint proves it, but so does the
    # organization, and only the second still holds once the live token has
    # rotated. Deciding on the fingerprint alone would let a switch onto a
    # stale snapshot of the CURRENT account write an old token over the live
    # one: the account would be logged out by an action that claimed to change
    # nothing. When the snapshot is behind, the answer says so and offers the
    # gesture that fixes it rather than a bare "nothing to do".
    same_token = bool(live.get('refresh_print')) and live['refresh_print'] == target_print
    same_org = bool(live.get('org')) and target.get('organizationUuid') == live['org']
    if live.get('read') and (same_token or same_org):
        return {'done': False, 'asks': False,
                'judgement': verdict(
                    UNKNOWN, 'cc_j_already_active', {'name': name, 'behind': not same_token},
                    'cc_r_nothing_to_do' if same_token else 'cc_r_save_now')}

    judgements = health(target)
    bad = [j for j in judgements if j['kind'] == BAD]
    if bad:
        return {'done': False, 'asks': False, 'judgement': bad[0]}

    # The live check, when the caller gave us a way to make it.
    checked = None
    if check is not None:
        checked = check_alive(name, check)
        if checked['judgement']['kind'] == BAD:
            return {'done': False, 'asks': False, 'judgement': checked['judgement'],
                    'checked': checked}
        if checked['judgement']['kind'] == UNKNOWN:
            judgements = judgements + [checked['judgement']]

    unsure = [j for j in judgements if j['kind'] == UNKNOWN]
    if unsure and not knowingly:
        return {'done': False, 'asks': True, 'judgement': unsure[0],
                'checked': checked}

    with _LOCK:
        # Re-read here, not earlier: Claude Code may have renewed in between.
        current, failure = read_json(cred_file())
        if failure:
            return {'done': False, 'asks': False, 'judgement': failure}

        saved_out = None
        out_org = current.get('organizationUuid')
        mine = fingerprint((current.get('claudeAiOauth') or {}).get('refreshToken'))
        for other in profile_names():
            p, bad_read = read_profile(other)
            if bad_read:
                continue
            same_token = mine and fingerprint(
                (p.get('claudeAiOauth') or {}).get('refreshToken')) == mine
            same_org = out_org and p.get('organizationUuid') == out_org
            if same_token or same_org:
                saved_out = other
                break
        if saved_out is None:
            saved_out = '_auto-' + time.strftime('%Y%m%d-%H%M%S')
        try:
            save_profile(saved_out, current)
        except (ValueError, OSError) as e:
            return {'done': False, 'asks': False,
                    'judgement': verdict(BAD, 'cc_j_outgoing_not_saved',
                                         {'why': str(e)}, 'cc_r_try_again')}

        backup_live()
        current['claudeAiOauth'] = target['claudeAiOauth']
        if target.get('organizationUuid'):
            current['organizationUuid'] = target['organizationUuid']
        # else: the old organization is LEFT IN PLACE. Writing null here is the
        # state that froze every chat, and a profile with no organization has
        # already been refused above, so this branch only exists as a guard.
        write_json_atomic(cred_file(), current)

        after, failure = read_json(cred_file())
        if failure:
            return {'done': False, 'asks': False, 'judgement': failure}
        token_ok = (after.get('claudeAiOauth') or {}).get('accessToken') == \
            (target.get('claudeAiOauth') or {}).get('accessToken')
        org_ok = (not target.get('organizationUuid')
                  or after.get('organizationUuid') == target['organizationUuid'])
        mcp_kept = ('mcpOAuth' in after) or ('mcpOAuth' not in current)
        if not (token_ok and org_ok):
            return {'done': False, 'asks': False,
                    'judgement': verdict(BAD, 'cc_j_write_unverified',
                                         {'backups': backups_dir()},
                                         'cc_r_restore_from_backup')}

    return {'done': True, 'asks': False, 'saved_out': saved_out,
            'checked': checked, 'mcp_kept': mcp_kept,
            'judgement': verdict(GOOD, 'cc_j_switched',
                                 {'name': name, 'email': target.get('email'),
                                  'tier': target.get('rateLimitTier')},
                                 'cc_r_applies_at_once')}


def rename(name, new_name):
    """Rename a profile: the folder AND the name inside the file, together."""
    try:
        name, new_name = valid_name(name), valid_name(new_name)
    except ValueError:
        return {'done': False, 'judgement': verdict(BAD, 'cc_j_bad_name',
                                                    {'name': new_name},
                                                    'cc_r_pick_another_name')}
    src, dst = os.path.join(store(), name), os.path.join(store(), new_name)
    if not os.path.isdir(src):
        return {'done': False, 'judgement': verdict(BAD, 'cc_j_no_such_profile',
                                                    {'name': name,
                                                     'known': profile_names()},
                                                    'cc_r_pick_existing')}
    if os.path.exists(dst) and name.casefold() != new_name.casefold():
        return {'done': False, 'judgement': verdict(BAD, 'cc_j_name_taken',
                                                    {'name': new_name},
                                                    'cc_r_pick_another_name')}
    with _LOCK:
        try:
            os.rename(src, dst)
        except OSError as e:
            return {'done': False, 'judgement': verdict(BAD, 'cc_j_rename_failed',
                                                        {'why': str(e)},
                                                        'cc_r_close_what_uses_it')}
        data, failure = read_profile(new_name)
        if failure:
            return {'done': True, 'judgement': verdict(UNKNOWN, 'cc_j_renamed_not_inside',
                                                       {'name': new_name},
                                                       'cc_r_resave_while_active')}
        data['name'] = new_name
        try:
            write_json_atomic(profile_path(new_name), data)
        except OSError:
            return {'done': True, 'judgement': verdict(UNKNOWN, 'cc_j_renamed_not_inside',
                                                       {'name': new_name},
                                                       'cc_r_resave_while_active')}
    return {'done': True, 'judgement': verdict(GOOD, 'cc_j_renamed',
                                               {'old': name, 'new': new_name})}


def archive(name):
    """Take a profile out of the list without destroying it."""
    try:
        name = valid_name(name)
    except ValueError:
        return {'done': False, 'judgement': verdict(BAD, 'cc_j_bad_name', {'name': name},
                                                    'cc_r_pick_another_name')}
    src = os.path.join(store(), name)
    if not os.path.isdir(src):
        return {'done': False, 'judgement': verdict(BAD, 'cc_j_no_such_profile',
                                                    {'name': name,
                                                     'known': profile_names()},
                                                    'cc_r_pick_existing')}
    dst = os.path.join(backups_dir(), 'archived-%s' % time.strftime('%Y%m%d-%H%M%S'))
    os.makedirs(dst, exist_ok=True)
    with _LOCK:
        try:
            shutil.move(src, os.path.join(dst, name))
        except OSError as e:
            return {'done': False, 'judgement': verdict(BAD, 'cc_j_archive_failed',
                                                        {'why': str(e)},
                                                        'cc_r_close_what_uses_it')}
    return {'done': True, 'judgement': verdict(GOOD, 'cc_j_archived',
                                               {'name': name, 'where': dst},
                                               'cc_r_archive_is_reversible')}
