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

4.  Two tokens, and only one of them decides anything. The RENEWAL lasts weeks
    and is what a switch actually uses; the short one lasts about eight hours
    and is renewed only for the account Claude Code is signed in as, so in
    every other saved profile it is expired almost all the time. A profile is
    therefore judged on its renewal date. The short token is used, when it
    happens to still be good, for a free read-only confirmation against the
    server - and when it is not, nothing is asked: a check that could not be
    made says nothing about the credential, and turning it into a question put
    one in front of the user on every switch, about a token they have never
    heard of and could do nothing about.

    🔴 The renewal is tested by USING it (renew), and what the server gives
    back is kept at once. It rotates when used, and until 2026-09-30 that was
    read as "a test would break the profile", so a saved login was judged on
    its date alone: the Omakase Planner switched Claude Code onto Work's saved
    login, dated 18/10, which the server had long stopped accepting, and
    Claude Code answered "OAuth session expired" and signed out. A date says
    when a sign-in ends, never whether its token is still the one the server
    knows. Renewing proves it, and saving the new pair at once is what keeps
    the profile alive in the act. Never the login Claude Code is using: that
    one is checked with its short token, which Claude Code keeps fresh.

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
import contextlib
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


# One writer at a time inside this process, for the live file and for every
# profile. Re-entrant because a switch holds it while it saves the outgoing
# account. Claude Code can still rewrite the live file from outside, so a write
# over it is made on a read taken inside the lock, through read_live_for_write,
# never on an older read. No network call is made while it is held.
_LOCK = threading.RLock()

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
        # What the file itself says, kept apart from `org`, which the
        # server's answer replaces (live_identity).
        'org_in_file': bool(org),
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

    refused = last_check(prof)
    if refused and refused['kind'] == BAD:
        # The server said so about this very token (renew): no date can
        # outweigh that, and asking it again would only spend a request.
        out.append(verdict(BAD, 'cc_j_renew_refused_before', {'when': refused['at']},
                           'cc_r_login_again'))

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
        # A note, not a question. It used to ask "switch anyway?", which
        # nobody can answer: the date is simply not in the file (a login
        # saved by an older tool), and the renewal token beside it is what
        # the switch uses. The user met it on 2026-09-27 and called it
        # senseless; it was.
        out.append(verdict(WARN, 'cc_j_no_refresh_expiry_access_dead', {'when': at},
                           'cc_r_switch_and_save'))
    else:
        out.append(verdict(WARN, 'cc_j_no_refresh_expiry_access_alive', {'when': at},
                           'cc_r_resave_while_active'))
    return out


# ── the historic copies ────────────────────────────────────────────────────
# path -> ((mtime, size), organization, refresh fingerprint, chain): what
# backups() has already read. Fingerprints only, never a token.
_BACKUP_SEEN = {}
# The name a switch gives the copy of an incoherent live file (see switch()).
INCOHERENT_COPY = 'credentials-incoherent'


def chain(oauth):
    """Which sign-in a login belongs to: its refreshTokenExpiresAt, or None.

    🔴 That date is fixed at the sign-in and stays the same through every
    renewal (measured on 2026-09-27: four renewal tokens of Work, 21 to 26
    September, one date), and one account can have two sign-ins alive at
    once. So it tells one chain of renewals from another, and it says
    NOTHING about which of two tokens is newer: read that way, a switch was
    refused because the login in use came from the earlier of two sign-ins.
    """
    try:
        return int((oauth or {}).get('refreshTokenExpiresAt')) or None
    except (TypeError, ValueError):
        return None


def backups(held=None):
    """{(organization, chain): newest backup seen for it}.

    By chain as well (see chain()): a copy speaks only for the sign-in it
    comes from. A copy of another sign-in of the same account, however new,
    says nothing about the tokens of this one, and read as if it did it
    declared a working saved login outdated (2026-09-27). A copy whose
    date is not in the file names no chain and is not read.

    The time comes from the file's own modification time, never from its name:
    the copies carry two prefixes (`credentials.` and `credentials-orgrecovery.`)
    and a hyphen sorts before a dot, so alphabetical order is not chronological
    order. With the name-based rule, yesterday's copy declared this morning's
    profile out of date.

    `held` maps renewal fingerprints to the organization of the saved login
    holding them. A copy whose token a saved login of ANOTHER organization
    holds is a copy of an incoherent file, one account's tokens under another
    account's key, which a switch backs up as it finds it. It says nothing
    about the account its key names: filed under that account, it declared the
    account's intact saved login outdated and the switch onto it was refused
    (review of 2026-09-26, second iteration). That only holds while the
    tokens stay held, so a switch now names such a copy apart
    (INCOHERENT_COPY), and it is never read here.
    """
    out = {}
    folder = backups_dir()
    if not os.path.isdir(folder):
        return out
    for name in os.listdir(folder):
        if not (name.startswith('credentials') and name.endswith('.bak')):
            continue
        if name.startswith(INCOHERENT_COPY):
            continue
        path = os.path.join(folder, name)
        try:
            st = os.stat(path)
        except OSError:
            continue
        # A copy never changes once written, and there are dozens of them:
        # each is parsed once and remembered for as long as its size and time
        # stay the same, instead of on every refresh.
        stamp = (st.st_mtime, st.st_size)
        known = _BACKUP_SEEN.get(path)
        if known is None or known[0] != stamp:
            try:
                with open(path, encoding='utf-8') as f:
                    data = json.load(f)
                oauth = data.get('claudeAiOauth') or {}
                known = (stamp, data.get('organizationUuid'),
                         fingerprint(oauth.get('refreshToken')), chain(oauth))
            except (OSError, ValueError, AttributeError):
                known = (stamp, None, '', None)
            _BACKUP_SEEN[path] = known
        _stamp, org, print_, chain_ = known
        if not org or chain_ is None:
            continue
        if held and print_ and held.get(print_) not in (None, org):
            continue
        seen = {'file': name, 'refresh_print': print_, 'at': st.st_mtime}
        key = (org, chain_)
        if key not in out or seen['at'] > out[key]['at']:
            out[key] = seen
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


def _saved_at(data, name):
    """When a profile was saved: its own `savedAt`, else the file's time."""
    saved_at = instant(data.get('savedAt'))
    if saved_at is None:
        try:
            saved_at = os.path.getmtime(profile_path(name))
        except OSError:
            saved_at = None
    return saved_at


def _outdated(data, saved_at, shots, name=None):
    """What the historic copies say about a saved profile: (aligned, judgements).

    A copy says the profile is behind only when it is NEWER than the profile
    and carries another renewal token of the same account AND the same
    sign-in (rule 4, and chain()). The sign-in has then renewed since the
    snapshot, and the renewal in it was retired when it rotated: switching
    to it would put a dead token live, so the remedy is to sign in with that
    account, never to switch to it. A copy of another sign-in proves nothing
    of the kind, and a profile whose date is not known is not judged.

    Nor does a copy of a state the saved login `name` has already moved past
    (history_prints): that copy is BEHIND the saved login, not ahead of it.
    A switch that went on from such a state backed it up, and the copy
    declared the account's working saved login outdated and refused every
    switch back (review of 2026-09-27).
    """
    org = data.get('organizationUuid')
    oauth = data.get('claudeAiOauth') or {}
    print_ = fingerprint(oauth.get('refreshToken'))
    chain_ = chain(oauth)
    shot = shots.get((org, chain_)) if chain_ is not None else None
    if not (shot and print_):
        return None, []
    if shot['refresh_print'] == print_:
        return True, []
    if saved_at is None or shot['at'] > saved_at + 60:
        if name and shot['refresh_print'] in history_prints(name):
            return True, []
        return False, [verdict(BAD, 'cc_j_profile_outdated',
                               {'when': datetime.fromtimestamp(shot['at'])},
                               'cc_r_sign_in_then_save')]
    return True, []


def profiles(live=None):
    """Every saved profile, with its health, and which one is signed in now.

    Recognition runs from the strongest proof down, and says which one it used:
      certain   the live refresh token has the same fingerprint as the profile
      certain   the organization known for the live file is the profile's
      likely    the organization in the identity cache is the profile's

    🔴 Exactly ONE profile comes back active. Each profile used to answer
    for itself, so two could both say yes: the account in use, recognised by
    its token, and an older parked snapshot of the same organization. The
    caller then took the first one in alphabetical order, which is how the
    badge for "Claude Code is signed in here" landed on a profile that was not
    the one in use. The claims are ranked, the strongest wins, and a tie goes
    to the newest snapshot.
    """
    live = active() if live is None else live

    seen = []
    for name in profile_names():
        data, failure = read_profile(name)
        if failure:
            seen.append({'name': name, 'readable': False, 'failure': failure})
            continue

        org = data.get('organizationUuid')
        print_ = fingerprint((data.get('claudeAiOauth') or {}).get('refreshToken'))
        # (rank, why, certainty, in step with the live token)
        if not _usable_name(name):
            # A folder no switch can use (a '_auto-' parking left by another
            # tool) never carries the mark: it took the badge while the
            # account's own saved login, the one every gesture uses, offered a
            # switch onto the account already in use (review of 2026-09-26).
            claim = None
        elif print_ and live.get('refresh_print') and print_ == live['refresh_print']:
            claim = (3, 'cc_why_same_refresh', 'certain', True)
        elif org and live.get('org') and org == live['org']:
            claim = (2, 'cc_why_same_org_live', 'certain', False)
        elif (org and live.get('org_cache') and org == live['org_cache']
              and live.get('org') in (None, live['org_cache'])):
            # The lagging cache only when nothing better names the account:
            # once the file or the server has named another one, the cache is
            # still describing the account of before (review of 2026-09-26).
            claim = (1, 'cc_why_same_org_cache', 'likely', False)
        else:
            claim = None

        saved_at = _saved_at(data, name)
        seen.append({'name': name, 'readable': True, 'data': data, 'org': org,
                     'print': print_, 'claim': claim, 'saved_at': saved_at})

    held = {s_['print']: s_['org'] for s_ in seen
            if s_.get('readable') and s_.get('print') and s_.get('org')}
    shots = backups(held)

    winner = None
    for s_ in seen:
        if not s_.get('claim'):
            continue
        if winner is None:
            winner = s_
            continue
        rank, best = s_['claim'][0], winner['claim'][0]
        if rank > best or (rank == best
                           and (s_['saved_at'] or 0) > (winner['saved_at'] or 0)):
            winner = s_

    out = []
    for s_ in seen:
        if not s_['readable']:
            failure = s_['failure']
            out.append({'name': s_['name'], 'readable': False,
                        'judgements': [failure], 'verdict': failure['kind'],
                        'active': False})
            continue

        data, org, print_ = s_['data'], s_['org'], s_['print']
        saved_at, name = s_['saved_at'], s_['name']
        is_active = s_ is winner
        why_active = s_['claim'][1] if is_active else ''
        certainty = s_['claim'][2] if is_active else None
        in_step = bool(is_active and s_['claim'][3])

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
            aligned, stale = _outdated(data, saved_at, shots, name)
            judgements += stale

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
            'check': last_check(data),
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
        # The file's own key, not `org`: after a sign-in the file has none
        # and `org` is the server's answer, and the list said the file's
        # organization had been tried (review of 2026-09-26, third iteration).
        tried.append('cc_try_org_live' if live.get('org_in_file')
                     else 'cc_try_no_org_live')
        if live.get('org_source') == 'server':
            tried.append('cc_try_org_server')
        tried.append('cc_try_org_cache' if live.get('org_cache')
                     else 'cc_try_no_org_cache')
    return verdict(UNKNOWN, 'cc_j_unrecognised',
                   {'tried': tried, 'profiles': [p['name'] for p in saved]},
                   'cc_r_save_current')


def live_identity(live, call=None, confirm=False):
    """Fill in whose login the live file holds, asking the server if it must.

    🔴 Signing in rewrites the live credentials WITHOUT
    `organizationUuid`, and for the hours that follow nothing on this machine
    knows whose login it is: no badge, a switch offered onto the account
    already in use, and a fresh login that cannot be matched to its own
    profile. The identity cache is not the answer - it still names the
    account of before. The server is, and asking it is one read that rotates
    nothing and spends nothing.

    `confirm` asks even when the file names an organization. That key is
    written by the tools that switch accounts, not by Claude Code, and a tool
    that swapped only the tokens, or stamped the key from the lagging cache,
    leaves a file whose tokens belong to one account and whose organization
    names another. The server answers about the TOKEN, so when the two
    disagree its answer is the identity and the dict says the file is
    incoherent (`org_file` keeps what the file said).

    Returns the same live dict, with `org` and `org_source` filled in when the
    server answered, and `server_email` when it gave one. Never raises: no
    transport, no network, no answer, all leave the dict as it was.
    """
    if not live.get('read') or call is None:
        return live
    if live.get('org') and not confirm:
        return live
    data, failure = read_json(cred_file())
    if failure:
        return live
    oauth = data.get('claudeAiOauth') or {}
    # The answer is pinned on the read it describes: a file rewritten since
    # `live` was taken holds another token, and an answer about that one must
    # not be filed under this one.
    if fingerprint(oauth.get('refreshToken')) != live.get('refresh_print'):
        return live
    # A token known to have expired would only earn a refusal, and a refusal
    # says nothing about whose it is.
    _at, at_expired, _ = when(oauth.get('expiresAt'))
    if at_expired:
        return live
    org, email = ask_server_identity(oauth.get('accessToken'), call)
    if not org:
        return live
    if live.get('org') and live['org'] != org:
        live['org_file'] = live['org']
        live['incoherent'] = True
    live['org'], live['org_source'] = org, 'server'
    if email:
        live['server_email'] = email
    return live


def state(call=None, confirm=False, known=None):
    """Everything the interface needs, from one read of the disk.

    One read, so that what is drawn cannot mix two different instants: the
    name of the active account coming from one read and its health from
    another is how a profile ends up described as somebody else.

    `call` is optional and buys one thing: the identity of a login the live
    file does not name (see `live_identity`), or with `confirm` the check that
    the organization it does name is really the tokens' own. Disk-only is the
    default so the first paint never waits on a network.

    `known` is a `live` dict from an earlier state. What the server said about
    a token stays true for as long as that token is the live one, so it is
    carried over when the renewal token is still the same: a list redrawn from
    the disk alone used to throw away the identity the server had just given,
    and put the badge and the switch back on the wrong rows.
    """
    live = active()
    if (known and known.get('org_source') == 'server' and known.get('org')
            and known.get('refresh_print')
            and known['refresh_print'] == live.get('refresh_print')):
        for k in ('org', 'org_source', 'server_email', 'incoherent', 'org_file'):
            if k in known:
                live[k] = known[k]
    live = live_identity(live, call, confirm=confirm)
    saved = profiles(live)
    active_name = next((p['name'] for p in saved if p['active']), None)
    out = {'live': live, 'profiles': saved, 'active_name': active_name,
           'read_at': time.time(), 'store': store(), 'cred': cred_file()}
    if live.get('read') and active_name is None:
        out['unrecognised'] = unrecognised(live, saved)
    return out


def _usable_name(name):
    """A folder name a switch could actually use: valid_name, unchanged."""
    try:
        return valid_name(name) == name
    except ValueError:
        return False


def find_profile_for_org(org, saved=None):
    """The ONE saved profile of an organization, chosen as profiles() elects.

    The profile Claude Code is using, when it is among them; otherwise one not
    judged unusable, the newest snapshot first (the latest sight of that
    account's login), then the longest renewal. Never a name valid_name
    refuses, since nothing could switch to it. The newest snapshot, not the
    longest renewal: the renewal's date belongs to the sign-in (chain()),
    and the longest one is only the latest sign-in, not the token seen last.

    🔴 It used to be the first profile of the organization in name order. With
    two snapshots of one account (a parked twin, or the `_auto-` copy another
    tool leaves), the row, the page, the sync and the saved-token read could
    each land on the stale one, and a switch onto it put a renewal token the
    account had already rotated away from back in the live file.
    """
    if not org:
        return None
    saved = saved if saved is not None else profiles()
    mine = [p for p in saved
            if p.get('readable', True) and p.get('org') == org
            and _usable_name(p.get('name'))]
    if not mine:
        return None
    for p in mine:
        if p.get('active'):
            return p
    return max(mine, key=lambda p: (p.get('verdict') != BAD,
                                    p.get('saved_at') or 0,
                                    p.get('refresh_expires') or datetime.min))


def has_profile_for_org(org):
    """Whether a saved login of this organization exists, and could be
    switched to, judged as the account list judges it. Light: it reads the
    profiles, and the historic copies (each parsed once, see backups()) only
    when there is a candidate to judge. It is asked to choose which advice a
    message gives, not to decide anything."""
    if not org:
        return False
    mine, held = [], {}
    for name in profile_names():
        data, failure = read_profile(name)
        if failure:
            continue
        print_ = fingerprint((data.get('claudeAiOauth') or {}).get('refreshToken'))
        if print_ and data.get('organizationUuid'):
            held[print_] = data['organizationUuid']
        if _usable_name(name) and data.get('organizationUuid') == org:
            mine.append((name, data))
    if not mine:
        return False
    shots = None
    for name, data in mine:
        # One that can still be switched to: an expired renewal is no way
        # back, and "switch Claude Code back to it" sent the user to a
        # switch that could only be refused (review of 2026-09-26). Nor is one
        # a newer copy shows to be behind: the list hides its Switch, and the
        # advice named a button that was not there (third iteration).
        if any(j['kind'] == BAD for j in health(data)):
            continue
        if shots is None:
            shots = backups(held)
        if any(j['kind'] == BAD
               for j in _outdated(data, _saved_at(data, name), shots, name)[1]):
            continue
        return True
    return False


def usable_access_token(org, margin_s=120):
    """(access token, profile name) of this organization's saved login while
    its short token is still good, else (None, None).

    Light on purpose: it runs on the refresh thread every time Claude Code is
    signed in as another account, so it reads the profiles and nothing else,
    none of the live file, the identity cache or the historic copies that
    profiles() walks. Among twins it takes the token that lasts longest.
    """
    if not org:
        return None, None
    best = None
    for name in profile_names():
        if not _usable_name(name):
            continue
        data, failure = read_profile(name)
        if failure or data.get('organizationUuid') != org:
            continue
        oauth = data.get('claudeAiOauth') or {}
        try:
            expires = int(oauth.get('expiresAt')) / 1000
        except (TypeError, ValueError):
            continue
        if not oauth.get('accessToken') or expires < time.time() + margin_s:
            continue
        if best is None or expires > best[0]:
            best = (expires, oauth['accessToken'], name)
    return (best[1], best[2]) if best else (None, None)


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
        # 🔴 The NORMAL state of any snapshot more than a few hours old, and
        # not a problem. The short token in a saved profile is frozen at the
        # moment it was taken: only the account Claude Code is signed in as
        # gets its one renewed, so every other profile's expires within about
        # eight hours and stays expired. Measured on 2026-09-14: one profile
        # stale by 24 minutes, the other still good for 5.7 hours, both with
        # renewals valid for nearly a month.
        #
        # So this is a check that could not be MADE, not a doubt about the
        # credential: what a switch actually uses is the renewal, and that is
        # judged from its own expiry date. Reported as skipped, and callers
        # must not turn it into a question - asking the user something they
        # have no way of answering, about a token they have never heard of,
        # every single time, is worse than not asking at all.
        rt, _, _ = when(oauth.get('refreshTokenExpiresAt'))
        return {'judgement': verdict(UNKNOWN, 'cc_j_not_checked_stale',
                                     {'when': at, 'renewal': rt}),
                'skipped': True, 'org': None, 'email': None}

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


# ── renewing a saved login ─────────────────────────────────────────────────
# What Claude Code itself does when its short token runs out, read from its
# own code (2.1.284, 2026-09-30): a POST of the renewal token to TOKEN_URL
# with its public client id and the scopes of the login. The server answers
# with a new short token and, as a rule, a new renewal token: from then on
# the old one is refused ("invalid_grant", measured with an invented token).
TOKEN_URL = 'https://platform.claude.com/v1/oauth/token'
CLIENT_ID = '9d1c250a-e61b-44d9-88ed-5944d1962f5e'
DEFAULT_SCOPES = ('user:profile', 'user:inference', 'user:sessions:claude_code',
                  'user:mcp_servers', 'user:file_upload', 'user:plugins')
# The short token's life when the answer does not say it: short on purpose,
# so that Claude Code renews it early rather than use a dead one.
SHORT_TOKEN_DEFAULT_S = 3600


def _oauth_error(body):
    """The `error` of an OAuth error answer, or ''."""
    try:
        answer = json.loads(body or '')
    except ValueError:
        return ''
    return str(answer.get('error') or '') if isinstance(answer, dict) else ''


def _ask_renewal(post, token, scopes):
    """(status or None, body, answer or None). `answer` is the parsed reply
    when it is a renewal: an object carrying a new access token."""
    body = json.dumps({'grant_type': 'refresh_token', 'refresh_token': token,
                       'client_id': CLIENT_ID, 'scope': ' '.join(scopes)})
    try:
        code, text = post(TOKEN_URL, body)
    except Exception:
        return None, '', None
    if code != 200:
        return code, text, None
    try:
        answer = json.loads(text or '')
    except ValueError:
        return code, text, None
    if not isinstance(answer, dict) or not answer.get('access_token'):
        return code, text, None
    return code, text, answer


def last_check(data):
    """The last verification of a saved login, when it is about the renewal
    token the login holds NOW: {'at': datetime, 'kind', 'key'}, else None.

    Kept in the profile beside the tokens, never inside `claudeAiOauth`: a
    switch writes that block into Claude Code's own file. A new token makes
    an old verdict meaningless, so the verdict carries the fingerprint of the
    token it was about.
    """
    rec = (data or {}).get('checked')
    if not isinstance(rec, dict) or rec.get('kind') not in (GOOD, BAD):
        return None
    mine = fingerprint(((data or {}).get('claudeAiOauth') or {}).get('refreshToken'))
    if not mine or rec.get('print') != mine:
        return None
    at = instant(rec.get('at'))
    if at is None:
        return None
    return {'at': datetime.fromtimestamp(at), 'kind': rec['kind'], 'key': rec.get('key')}


def _check_record(kind, key, token):
    return {'at': datetime.now().astimezone().isoformat(), 'kind': kind,
            'key': key, 'print': fingerprint(token)}


def _record_check(name, expect_print, kind, key):
    """Write a verdict into a saved login, if it still holds the token the
    verdict is about. Not a change of tokens, so no copy is taken: the
    copies are the login's history of tokens (history_prints)."""
    with _LOCK:
        data, failure = read_profile(name)
        if failure or not isinstance(data, dict):
            return False
        token = (data.get('claudeAiOauth') or {}).get('refreshToken')
        if fingerprint(token) != expect_print:
            return False
        data['checked'] = _check_record(kind, key, token)
        try:
            write_json_atomic(profile_path(name), data)
        except OSError:
            return False        # a note that could not be kept: the answer stands
        return True


# ── one renewal at a time ──────────────────────────────────────────────────
# A renewal token is spent by its use, so two renewals of one saved login at
# once send the same token twice: Verify on a row while a switch renews that
# login, or the widget and the command line the Planner runs. The loser is
# refused, or renews the token the winner has just written live, and Claude
# Code signs out at its next renewal: the failure of 2026-09-30 by another
# road. So a renewal holds a lock that the other processes of this computer
# see (a byte of a file in the store, locked with msvcrt: Windows lets go of it
# when the process ends, so a crash leaves nothing held), and a switch holds it
# from its first read to its write of the live file.
RENEWAL_WAIT_S = 60
RENEWAL_LOCK_FILE = '.renewal.lock'
_RENEWAL = threading.RLock()
_renewal_held = {'depth': 0, 'fd': None}


def _lock_renewal_file(deadline):
    """Lock the store's renewal file for this process: its descriptor, -1
    when the file cannot be had at all (the lock inside this process then
    stands alone: a store that cannot be written fails the renewal's own write
    anyway), or None when another process still holds it at `deadline`."""
    try:
        import msvcrt
    except ImportError:                 # not Windows: this process only
        return -1
    if not os.path.isdir(store()):
        try:
            ensure_store()
        except OSError:
            return -1
    try:
        fd = os.open(os.path.join(store(), RENEWAL_LOCK_FILE), os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        return -1
    while True:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return fd
        except OSError:
            if time.monotonic() >= deadline:
                os.close(fd)
                return None
            time.sleep(0.2)


def _unlock_renewal_file(fd):
    if fd is None or fd < 0:
        return
    try:
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    except (ImportError, OSError):
        pass                            # closing the file lets go of it too
    finally:
        os.close(fd)


@contextlib.contextmanager
def renewal_lock(wait_s=None):
    """Hold the renewal lock: yields True once it is held, False when it
    could not be had within `wait_s` (somebody else is renewing or switching:
    nothing is concluded, and nothing may be done). Re-entrant within a
    thread, so that a switch holding it can renew. `wait_s` None: RENEWAL_WAIT_S."""
    wait_s = max(0.0, RENEWAL_WAIT_S if wait_s is None else wait_s)
    deadline = time.monotonic() + wait_s
    if not _RENEWAL.acquire(timeout=wait_s):
        yield False
        return
    try:
        if _renewal_held['depth'] == 0:
            fd = _lock_renewal_file(deadline)
            if fd is None:
                yield False
                return
            _renewal_held['fd'] = fd
        _renewal_held['depth'] += 1
        try:
            yield True
        finally:
            _renewal_held['depth'] -= 1
            if _renewal_held['depth'] == 0:
                fd, _renewal_held['fd'] = _renewal_held['fd'], None
                _unlock_renewal_file(fd)
    finally:
        _RENEWAL.release()


def renew(name, post, wait_s=None):
    """Renew a saved login with the server, and keep what comes back.

    The one way to know that a saved login still works: a date says when the
    sign-in ends, not whether the server still knows its token. `post(url,
    body) -> (status or None, text)` is the caller's, as `call` is for the
    reads, so that a bench can answer anything it wants to try and so that the
    token travels only where the caller sends it (in the widget, curl's
    standard input, never its command line).

    🔴 Never the login Claude Code is using. Its renewal token would be
    replaced under it, and Claude Code, which holds it, would be signed out at
    its next renewal. That one is answered with 'in_use' and nothing is sent.
    Which login is in use is read under the renewal lock (renewal_lock), where
    no switch can be halfway: a login a switch has just put live is seen.

    🔴 What comes back is written into the profile before anything else is
    done with it. The old renewal token is dead from the moment the server
    answers, so a new one that was not kept would be a login lost.

    Returns {'judgement', 'renewed': bool}, with 'in_use', 'moved' or 'busy'
    when those are the reason nothing was renewed. GOOD 'cc_j_renewed' after a
    renewal that was kept; BAD 'cc_j_renew_refused' when the server refused
    the token (the saved login is dead: sign in again); UNKNOWN when there was
    no usable answer, which says nothing about the login, or when another
    renewal kept the lock for longer than `wait_s`.
    """
    with renewal_lock(wait_s) as held:
        if not held:
            return {'judgement': verdict(UNKNOWN, 'cc_j_renew_busy', {'name': name},
                                         'cc_r_try_again'),
                    'renewed': False, 'busy': True}
        return _renew_held(name, post)


def account_in_use(data, live):
    """Whether a saved login is of the account Claude Code is signed in to on
    this computer, whatever its token: the live file holds a login, and names
    that account (its own key, or the identity cache when the file says none,
    as right after a sign-in).

    Erring towards yes costs a check that is not made; towards no, it may cost
    the login in use (renew).
    """
    org = (data or {}).get('organizationUuid')
    if not org or not (live.get('refresh_print') or live.get('access_print')):
        return False
    return org == (live.get('org') or live.get('org_cache'))


def _renew_held(name, post):
    """renew, with the renewal lock held."""
    data, failure = read_profile(name)
    if failure:
        return {'judgement': failure, 'renewed': False}
    oauth = data.get('claudeAiOauth') or {}
    token = oauth.get('refreshToken')
    if not token:
        return {'judgement': verdict(BAD, 'cc_j_no_refresh', {}, 'cc_r_resave_while_active'),
                'renewed': False}
    mine = fingerprint(token)
    live = active()
    if not live.get('read') and (live.get('judgement') or {}).get('key') != 'cc_j_file_missing':
        # Claude Code's file is there and cannot be read (halfway through a
        # write, or damaged): which login is in use cannot be told, and it
        # may be this one. Said as what it is: about that file, not this login.
        return {'judgement': dict(live.get('judgement') or verdict(
                    UNKNOWN, 'cc_j_file_unreadable', {}, 'cc_r_try_again'), kind=UNKNOWN),
                'renewed': False}
    if live.get('refresh_print') and live['refresh_print'] == mine:
        return {'judgement': verdict(UNKNOWN, 'cc_j_renew_in_use', {'name': name}),
                'renewed': False, 'in_use': True}
    # 🔴 Nor the saved login of the account Claude Code is signed in to, with
    # whatever token. When it lags behind the live one (the widget brings it
    # in step when the file changes, and had not yet, or was not running),
    # its renewal token is one Claude Code has already rotated away: sent
    # again, it is refused at best, and a server that takes the reuse of a
    # retired token for theft retires the whole sign-in, the live token with
    # it. What is in use is Claude Code's to renew.
    if account_in_use(data, live):
        return {'judgement': verdict(UNKNOWN, 'cc_j_renew_account_in_use', {'name': name}),
                'renewed': False, 'in_use': True}

    scopes = [x for x in (oauth.get('scopes') or []) if isinstance(x, str) and x]
    scopes = scopes or list(DEFAULT_SCOPES)
    code, text, answer = _ask_renewal(post, token, scopes)
    if (answer is None and code == 400 and _oauth_error(text) == 'invalid_scope'
            and scopes != list(DEFAULT_SCOPES)):
        # Claude Code asks again with its own scopes too: a scope the server no
        # longer grants is refused before the token is looked at.
        code, text, answer = _ask_renewal(post, token, list(DEFAULT_SCOPES))

    moved = {'judgement': verdict(UNKNOWN, 'cc_j_renew_moved', {'name': name},
                                  'cc_r_try_again'),
             'renewed': False, 'moved': True}
    if answer is not None:
        now_ms = int(time.time() * 1000)
        new = dict(oauth)
        new['accessToken'] = answer['access_token']
        # As Claude Code: no new renewal token in the answer means the old one
        # goes on.
        new['refreshToken'] = answer.get('refresh_token') or token
        try:
            new['expiresAt'] = now_ms + int(float(answer['expires_in']) * 1000)
        except (KeyError, TypeError, ValueError):
            new['expiresAt'] = now_ms + SHORT_TOKEN_DEFAULT_S * 1000
        try:
            left = float(answer.get('refresh_token_expires_in'))
            if left > 0:
                new['refreshTokenExpiresAt'] = now_ms + int(left * 1000)
        except (TypeError, ValueError):
            pass
        if isinstance(answer.get('scope'), str) and answer['scope'].split():
            new['scopes'] = answer['scope'].split()
        with _LOCK:
            current, failure = read_profile(name)
            if failure or fingerprint(((current or {}).get('claudeAiOauth') or {})
                                      .get('refreshToken')) != mine:
                # Somebody wrote this login meanwhile (another process
                # renewing it, or a sign-in brought in): what is there now is
                # theirs, and is not replaced by an answer about an older one.
                return moved
            prof = dict(current)
            prof['claudeAiOauth'] = new
            prof['savedAt'] = datetime.now().astimezone().isoformat()
            prof['checked'] = _check_record(GOOD, 'cc_j_renewed', new['refreshToken'])
            # The server has retired the old token by now: the copy of the old
            # profile is only its history (history_prints), and a copy that
            # cannot be taken does not keep the new pair from being saved.
            try:
                ensure_store()
                backup_profile(name)
            except OSError:
                pass
            # A pair that could not be saved is a login lost, not a file to
            # restore: the copy holds the retired token.
            lost = {'judgement': verdict(BAD, 'cc_j_renew_not_kept', {'name': name},
                                         'cc_r_login_again'),
                    'renewed': False}
            try:
                write_json_atomic(profile_path(name), prof)
            except OSError:
                return lost
            back, failure = read_profile(name)
            if failure or fingerprint(((back or {}).get('claudeAiOauth') or {})
                                      .get('refreshToken')) != fingerprint(new['refreshToken']):
                return lost
        rt, _, _ = when(new.get('refreshTokenExpiresAt'))
        return {'judgement': verdict(GOOD, 'cc_j_renewed',
                                     {'name': name, 'until': rt, 'email': data.get('email')}),
                'renewed': True}

    if code in (400, 401) and _oauth_error(text) == 'invalid_grant':
        with _LOCK:
            current, failure = read_profile(name)
            if failure or fingerprint(((current or {}).get('claudeAiOauth') or {})
                                      .get('refreshToken')) != mine:
                return moved
            _record_check(name, mine, BAD, 'cc_j_renew_refused')
        return {'judgement': verdict(BAD, 'cc_j_renew_refused',
                                     {'name': name, 'email': data.get('email')},
                                     'cc_r_login_again'),
                'renewed': False}

    if code in (400, 401):
        # Refused, but not the token: our request, or the client. Nothing
        # about the login can be concluded from it.
        return {'judgement': verdict(UNKNOWN, 'cc_j_renew_other',
                                     {'code': code, 'error': _oauth_error(text) or '?'},
                                     'cc_r_try_again_soon'),
                'renewed': False}
    judgement = judge_response(code, text)
    if judgement['kind'] != UNKNOWN:
        # A 200 that is not a renewal, or a 403 carrying data: neither is the
        # answer asked for, and neither says the token is dead.
        judgement = verdict(UNKNOWN, 'cc_j_200_not_data' if code == 200
                            else 'cc_j_unexpected_code', {'code': code},
                            'cc_r_try_again_soon')
    return {'judgement': judgement, 'renewed': False}


def verify(name, call=None, post=None, live=None):
    """Whether a saved login still works, by the only test that can say so.

    The account Claude Code is signed in to is asked about with the LIVE
    login's short token, taken from Claude Code's own file, which Claude Code
    keeps fresh: a read that spends nothing (renewing would take the login
    from Claude Code). Also when its saved login lags behind the live one:
    what is in use is what works or not, and the widget brings the saved one
    in step by itself (sync_live_into_profile). The verdict is kept in the
    saved login only when it holds that very login. Any other saved login is
    renewed (renew), and kept renewed.
    """
    live = active() if live is None else live
    data, failure = read_profile(name)
    if failure:
        return {'judgement': failure, 'renewed': False}
    token = (data.get('claudeAiOauth') or {}).get('refreshToken')
    mine = fingerprint(token)
    same_login = bool(mine) and live.get('refresh_print') == mine
    if not same_login and not account_in_use(data, live):
        if post is None:
            return {'judgement': verdict(UNKNOWN, 'cc_j_no_answer', {},
                                         'cc_r_check_connection'), 'renewed': False}
        # renew looks again, under its lock, at whether it is in use.
        return renew(name, post)

    current, failure = read_json(cred_file())
    oauth = ((current or {}).get('claudeAiOauth') or {}) if not failure else {}
    if fingerprint(oauth.get('refreshToken')) != live.get('refresh_print'):
        return {'judgement': verdict(UNKNOWN, 'cc_j_live_moving', {}, 'cc_r_try_again'),
                'renewed': False, 'in_use': True}
    _at, expired, _ = when(oauth.get('expiresAt'))
    if expired or call is None or not oauth.get('accessToken'):
        # Claude Code renews it at its next command: nothing to ask with now.
        return {'judgement': verdict(UNKNOWN, 'cc_j_in_use_renews', {'name': name}),
                'renewed': False, 'in_use': True}
    code, body = call(PROFILE_URL, (f'Authorization: Bearer {oauth["accessToken"]}',
                                    'anthropic-beta: oauth-2025-04-20'))
    judgement = judge_response(code, body)
    if judgement['kind'] == BAD:
        if same_login:
            _record_check(name, mine, BAD, 'cc_j_in_use_refused')
        return {'judgement': verdict(BAD, 'cc_j_in_use_refused', {'name': name},
                                     'cc_r_login_again'),
                'renewed': False, 'in_use': True}
    if judgement['kind'] != GOOD:
        return {'judgement': judgement, 'renewed': False, 'in_use': True}
    try:
        answer = json.loads(body)
    except ValueError:
        answer = None
    answer = answer if isinstance(answer, dict) else {}
    org = (answer.get('organization') or {}).get('uuid')
    if org and data.get('organizationUuid') and org != data['organizationUuid']:
        if not same_login:
            # The file named this account and the server another: whose the
            # login in use is cannot be told from here, and a saved login
            # that might be in use is not renewed.
            return {'judgement': verdict(UNKNOWN, 'cc_j_live_unidentified', {},
                                         'cc_r_identify_live'),
                    'renewed': False, 'in_use': True}
        return {'judgement': verdict(BAD, 'cc_j_other_account',
                                     {'email': (answer.get('account') or {}).get('email_address')
                                      or '?'},
                                     'cc_r_resave_while_active'),
                'renewed': False, 'in_use': True}
    if same_login:
        _record_check(name, mine, GOOD, 'cc_j_verified_in_use')
    return {'judgement': verdict(GOOD, 'cc_j_verified_in_use', {'name': name}),
            'renewed': False, 'in_use': True}


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


def read_live_for_write(reference):
    """The live file read again right before writing on it, or None.

    🔴 The one place where "re-read the live file, then write on it" is
    allowed. In claude-account.ps1 the same gesture lived in three places, and
    three times the gate was fixed in one and forgotten in the others: once it
    left the machine with no credentials. The fresh object comes back ONLY when
    it still carries the token block and the same renewal token as
    `reference`, i.e. when it provably describes the same credentials. The
    renewal rotates, so a mismatch means "I do not know", never "same file":
    writing anyway is how older tokens were put back on disk, and an MCP token
    obtained meanwhile dropped (measured on 2026-09-13).
    """
    fresh, failure = read_json(cred_file())
    if failure or not isinstance(fresh, dict):
        return None
    mine = (fresh.get('claudeAiOauth') or {}).get('refreshToken')
    theirs = ((reference or {}).get('claudeAiOauth') or {}).get('refreshToken')
    return fresh if mine and theirs and mine == theirs else None


PROFILE_BACKUPS_KEPT = 5
# path -> ((mtime, size), refresh fingerprint): what history_prints() has read.
_HISTORY_SEEN = {}
_PROFILE_BACKUP = re.compile(r'^profile-(.+)\.\d{8}-\d{6}-\d{3}\.bak$')


def backup_profile(name):
    """A copy of a profile before it is overwritten.

    Rule 7 holds for profiles too: they carry tokens, and a profile written
    over with the wrong account's credentials used to survive only if some
    copy of the live file happened to hold its tokens. Only the newest few are
    kept per profile, because the automatic sync rewrites the profile of the
    account in use after every renewal; those are the only files this module
    ever deletes. The copies of the live file beside them are the user's own
    history, written by other tools as well, and are never touched.
    """
    path = profile_path(name)
    if not os.path.exists(path):
        return None
    ensure_store()
    now = time.time()
    dst = os.path.join(backups_dir(), 'profile-%s.%s-%03d.bak' % (
        name, time.strftime('%Y%m%d-%H%M%S', time.localtime(now)), int(now % 1 * 1000)))
    shutil.copyfile(path, dst)
    try:
        folder = backups_dir()
        mine = []
        for n in os.listdir(folder):
            m = _PROFILE_BACKUP.match(n)
            if m and m.group(1) == name:
                mine.append((os.path.getmtime(os.path.join(folder, n)), n))
        for _t, old in sorted(mine)[:-PROFILE_BACKUPS_KEPT]:
            os.remove(os.path.join(folder, old))
    except OSError:
        pass
    return dst


def history_prints(name):
    """The renewal fingerprints a saved login held BEFORE its current one:
    the states it has moved past, from its own copies (backup_profile keeps
    the newest few). Fingerprints only.

    This, and not the renewal's date, is what "older" means for a login:
    one this saved login has already been replaced from, as when a copy
    was put back live. The date belongs to the sign-in (chain()).
    """
    out = set()
    folder = backups_dir()
    try:
        names = os.listdir(folder)
    except OSError:
        return out
    for n in names:
        m = _PROFILE_BACKUP.match(n)
        if not (m and m.group(1) == name):
            continue
        path = os.path.join(folder, n)
        try:
            st = os.stat(path)
        except OSError:
            continue
        # Each copy parsed once, as backups() does: the list is redrawn often.
        stamp = (st.st_mtime, st.st_size)
        known = _HISTORY_SEEN.get(path)
        if known is None or known[0] != stamp:
            try:
                with open(path, encoding='utf-8') as f:
                    data = json.load(f)
                p = fingerprint(((data if isinstance(data, dict) else {})
                                 .get('claudeAiOauth') or {}).get('refreshToken'))
            except (OSError, ValueError, AttributeError):
                p = ''
            known = (stamp, p)
            _HISTORY_SEEN[path] = known
        if known[1]:
            out.add(known[1])
    return out


def valid_name(name):
    """A profile name that cannot break out of its folder, or ValueError."""
    clean = ' '.join(str(name or '').split())
    if not PROFILE_NAME.match(clean) or clean in RESERVED_NAMES:
        raise ValueError('cc_e_bad_name')
    return clean


# (transport, access-token fingerprint) -> (organization, email). The account
# a token belongs to never changes, so an answer is kept for as long as the
# process runs; a failure is not, so the next caller asks again. The transport
# is part of the key so that two different ways of asking never share answers.
_SERVER_SAID = {}


def ask_server_identity(access_token, call):
    """(organization, email) an access token really belongs to, from the server.

    A read: the profile endpoint answers with the account and does not rotate
    anything. It is the one source that cannot be behind, which is why a write
    that needs the organization asks it before trusting anything cached.
    (None, None) when it cannot be asked or does not answer with data.
    """
    if not access_token or call is None:
        return None, None
    key = (call, fingerprint(access_token))
    if key in _SERVER_SAID:
        return _SERVER_SAID[key]
    code, body = call(PROFILE_URL, (f'Authorization: Bearer {access_token}',
                                    'anthropic-beta: oauth-2025-04-20'))
    if judge_response(code, body)['kind'] != GOOD:
        return None, None
    try:
        answer = json.loads(body)
    except ValueError:
        return None, None
    if not isinstance(answer, dict):
        return None, None
    org = (answer.get('organization') or {}).get('uuid') or None
    if not org:
        return None, None
    account = answer.get('account') or {}
    said = (org, account.get('email_address') or account.get('email') or None)
    _SERVER_SAID[key] = said
    return said


def ask_server_org(access_token, call):
    """The organization alone: see ask_server_identity."""
    return ask_server_identity(access_token, call)[0]


UUID_SHAPE = '[0-9a-fA-F-]{36}'


def save_profile(name, live_data=None, call=None, proven=None, expect_org=None):
    """Snapshot the live credentials under `name`, repairing what a login left out.

    `live_data` lets a caller hand over the credentials IT has already read, so
    that the name and the content describe the same instant. A switch works out
    which profile the outgoing account belongs to from its own read; re-reading
    here would mean the name came from one read and the content from another.

    `proven` is an identity the caller has already established for exactly
    these tokens ({'org', 'email', 'note'}), so the server is not asked twice
    and nothing goes on the network while a switch holds the lock.
    `expect_org` is the account the caller means to save: a page opened before
    Claude Code moved to another account would otherwise save that account's
    login under this one's name, in a folder that did not exist yet and so had
    nothing to compare with (review of 2026-09-26, second iteration).

    🔴 A write needs a PROOF of whose the login is: the caller's, a saved
    profile holding the same renewal token, or the server. The organization
    written in the file is never enough. That key is written by the tools that
    switch accounts, not by Claude Code, and one that stamped it from the
    lagging cache left Personal's tokens under Work's organization: saving
    them "as Work" replaced Work's own saved login (review of 2026-09-26,
    second iteration). Without a proof nothing is written, and a renewal, or
    a moment's wait for the server, is what brings one.

    🔴 It REFUSES to write one account's credentials over another account's
    profile. On 2026-09-13 it did exactly that: after a switch to Personal, the
    identity cache still named Work, the widget took Work for the account in
    use and offered to save it, and this function wrote Personal's tokens into
    the Work profile under Work's name and Work's email. The organization is
    the identity, so it is compared before anything is written. It comes from
    the strongest source that can answer: what the caller proved, a saved
    profile holding the same renewal token, the live file's own key, and the
    server when those are missing or cannot vouch for a replacement.

    🔴 Never from the identity cache. It lags behind a sign-in and behind a
    switch, and a label taken from it is what every later decision reads: a
    fresh login parked under the previous account's organization and e-mail
    showed that account's usage under the wrong name and fed a switch that put
    one account's tokens under the other's organization (review of 2026-09-26).
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
    mine = fingerprint(oauth.get('refreshToken'))
    file_org = data.get('organizationUuid')

    org, org_note, email = None, None, None
    if proven and proven.get('org'):
        org, org_note = proven['org'], proven.get('note') or 'server'
        email = proven.get('email')
    if not org and mine:
        # A saved profile carrying the same renewal token IS this account.
        for other in profile_names():
            p, bad = read_profile(other)
            if bad or not p.get('organizationUuid'):
                continue
            if fingerprint((p.get('claudeAiOauth') or {}).get('refreshToken')) == mine:
                org, org_note, email = p['organizationUuid'], 'proof', p.get('email')
                break
    if not org and call is not None:
        # The server, which cannot be behind. Before the lock.
        srv_org, srv_email = ask_server_identity(oauth.get('accessToken'), call)
        if srv_org:
            org, org_note, email = srv_org, 'server', srv_email
    if not org:
        raise ValueError('cc_e_save_unproven')
    if expect_org and org != expect_org:
        raise ValueError('cc_e_save_other_account')

    with _LOCK:
        previous, _ = read_profile(name)
        previous_org = (previous or {}).get('organizationUuid')
        if previous_org and previous_org != org:
            raise ValueError('cc_e_save_other_account')
        # Never backwards, as the sync: a login this saved login has already
        # moved past (one of its own earlier states, as when a copy was put
        # back live) must not replace it. By its history, never by the
        # renewal's date: that date is the sign-in's, and an earlier one only
        # meant the other of two sign-ins (see chain(), 2026-09-27).
        prev_print = fingerprint(((previous or {}).get('claudeAiOauth') or {})
                                 .get('refreshToken'))
        if (previous_org == org and mine and mine != prev_print
                and mine in history_prints(name)):
            raise ValueError('cc_e_save_older')

        if not file_org:
            # Repair the live file too, but only with an organization that was
            # proved, and only on a read taken now that still carries these
            # very tokens. Claude Code may have rewritten the file since `data`
            # was read; writing `data` back would reinstall an older renewal
            # token and sign the user out at the next one (2026-09-26 review,
            # and claude-account.ps1's seventh review of 2026-09-13).
            # The copy first, then the read, then the write at once, as the
            # switch does: the copy runs icacls, a process of its own, and a
            # read taken before it was an older read by the time of the write
            # (review of 2026-09-26, third iteration).
            backup_live('credentials-orgrecovery')
            fresh = read_live_for_write(data)
            if fresh is not None and not fresh.get('organizationUuid'):
                fresh['organizationUuid'] = org
                write_json_atomic(cred_file(), fresh)

        if not email:
            # A label, never an identity. The profile's own e-mail when it
            # describes this account; the cache's only when the cache is
            # describing this very account too.
            if previous and previous.get('email') and previous_org == org:
                email = previous['email']
            elif from_cache('organizationUuid', UUID_SHAPE) == org:
                email = from_cache('emailAddress')

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
        backup_profile(name)
        write_json_atomic(profile_path(name), prof)
    prof['_org_note'] = org_note
    return prof


def sync_live_into_profile(live=None, call=None):
    """Bring the profile of the account Claude Code signed in as up to date.

    🔴 Signing in again mints a NEW renewal token in the live file while
    the profile saved for that same account still holds the old one. Until the
    two are back in step the account is unrecognisable: no badge, "switch"
    offered on every row, and a switch onto that profile writes the OLD token
    over the fresh login - which signs the user out of the account they had
    just signed into, and is exactly what happened on 2026-09-21. The remedy
    used to be a button the user had to know about and press after every
    login. It happens by itself now.

    🔴 It writes only when the SERVER says whose tokens these are. The
    organization written in the file is not enough (review of 2026-09-26): that
    key is written by the tools that switch accounts, and one that swapped only
    the tokens, or stamped the key from the lagging cache, leaves Personal's
    tokens under Work's organization - which this function then copied into
    Work's profile, with no click and no copy of Work's own tokens kept. Never
    from the identity cache either, which lags behind precisely the event this
    reacts to. It writes into the profile profiles() elects for that account,
    never into a twin, and never backwards.

    Returns None when there is nothing to do; {'updated': name, 'org': ...,
    'was': old fingerprint} after a write; {'pending': why} when it could not
    decide now - the server did not answer, the short token has expired, the
    file moved while it was being read - so the caller knows to try again.
    """
    live = active() if live is None else live
    if not live.get('read') or not live.get('refresh_print'):
        return None
    data, failure = read_json(cred_file())
    if failure:
        return None
    oauth = data.get('claudeAiOauth') or {}
    if fingerprint(oauth.get('refreshToken')) != live['refresh_print']:
        return {'pending': 'changed'}
    saved = profiles(live)
    if any(p.get('refresh_print') == live['refresh_print']
           and _usable_name(p.get('name'))
           for p in saved if p.get('readable', True)):
        # A profile already holds this login. Not a '_auto-' parking of
        # another tool: the account's own saved login would stay behind it
        # for good (review of 2026-09-26).
        return None
    _at, at_expired, _ = when(oauth.get('expiresAt'))
    if at_expired:
        # Nothing can be asked with it; Claude Code renews it at its next
        # command, and the file changing is what brings this back here.
        return {'pending': 'expired'}
    org, email = ask_server_identity(oauth.get('accessToken'), call)
    if not org:
        return {'pending': 'unproven'}
    if live.get('org') != org:
        live = dict(live, org=org, org_source='server')
        saved = profiles(live)

    target = find_profile_for_org(org, saved)
    if target is None or not target.get('readable', True):
        return None
    if target.get('refresh_print') == live['refresh_print']:
        return None                      # already in step, nothing to write

    # Never backwards: a login the saved one has already moved past, as when
    # a copy was put back live (see save_profile). Not by the renewal's
    # date, which is the sign-in's: the login of the earlier of two
    # sign-ins was never brought in, and the saved login fell behind it.
    if live['refresh_print'] in history_prints(target['name']):
        return None

    try:
        save_profile(target['name'], data,
                     proven={'org': org, 'email': email, 'note': 'server'})
    except (ValueError, OSError):
        # A refusal here is save_profile protecting another account's profile.
        # It is not this function's business to argue with it.
        return None
    return {'updated': target['name'], 'org': org,
            'was': target.get('refresh_print')}


def profile_name_for(label):
    """A profile folder name made from a free-text label, or None.

    Widget account names are chosen freely and can carry accents, brackets or
    Japanese; a profile folder takes letters, digits, spaces, dots, hyphens
    and underscores. What cannot be carried over is dropped, and a label that
    leaves nothing usable gives None.
    """
    clean = re.sub(r'[^A-Za-z0-9 ._-]+', ' ', str(label or ''))
    clean = ' '.join(clean.split()).lstrip('._- ')[:41].rstrip()
    try:
        return valid_name(clean)
    except ValueError:
        return None


def switch(name, *, knowingly=False, call=None, plan_check=None, name_for_org=None,
           post=None, wait_s=None):
    """Make `name` the account Claude Code authenticates with.

    `post` is the transport the saved login is renewed with before it goes
    live (renew): without it, the free read with its short token is all that
    is tried, as before 2026-09-30.

    The order matters and every step of it was paid for:

    0. find out whose login is live, asking the server when the file does not
       say, because right after a sign-in it does not: without this the switch
       cannot tell "the account you are already on" from "another account",
       and writes an old token over a fresh login. When nobody can say whose
       it is, nothing is written: an unidentified login may be a sign-in made
       a minute ago, and parking it to put an older snapshot live signs the
       user out (review of 2026-09-26);
    1. "already on it" is said only when the tokens prove it or the server
       confirms it: the organization in the file can name one account while
       the tokens belong to another;
    2. refuse a profile that cannot work, BEFORE touching anything, judged the
       way the account list judges it, historic copies included;
    3. an UNKNOWN profile is not refused, it is offered: the caller asks the
       user and comes back with knowingly=True;
    3b. the saved login is renewed (renew): a refusal stops the switch with
       nothing written, and a renewal is kept and is what goes live. No answer
       is offered, as in 3;
    4. work out whose the outgoing login is before taking the lock, so that no
       network call is made while it is held;
    5. snapshot the OUTGOING account from a read taken inside the lock, so the
       account being left always has a fresh copy of itself;
    6. back up, then write both identity keys together, atomically, on a read
       taken right now that still carries the tokens just snapshotted;
    7. read back from the disk and compare. A write that was not protested
       against is not a write that happened.

    `name_for_org(org)` may name the profile the outgoing login is saved under
    when its account has none yet (the widget passes its own name for that
    account), instead of an anonymous `auto-<date>`.

    🔴 All of it holds the renewal lock (renewal_lock), from the first read to
    the write of the live file: a renewal elsewhere, in this process or in
    another, cannot spend the token of the login being put live, and two
    switches do not interleave. When the lock cannot be had within `wait_s`
    nothing is done ('cc_j_renew_busy').

    Returns {'done': bool, 'judgement': ..., 'asks': bool, 'saved_out': name}.
    """
    with renewal_lock(wait_s) as held:
        if not held:
            return {'done': False, 'asks': False,
                    'judgement': verdict(UNKNOWN, 'cc_j_renew_busy', {'name': name},
                                         'cc_r_try_again')}
        return _switch_held(name, knowingly=knowingly, call=call, plan_check=plan_check,
                            name_for_org=name_for_org, post=post)


def _switch_held(name, *, knowingly, call, plan_check, name_for_org, post):
    """switch, with the renewal lock held."""
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

    live = live_identity(active(), call)
    saved = profiles(live)
    target_print = fingerprint((target.get('claudeAiOauth') or {}).get('refreshToken'))
    # 🔴 No login at all in Claude Code's file: the file is gone, or holds no
    # tokens. Claude Code takes its login out when the server refuses a
    # renewal, and on 2026-09-30 "Switch" back to the account that worked was
    # then refused as "cannot tell whose login is in use". Nothing is in use:
    # there is nothing to identify, to save or to lose, and the switch only
    # has to put a working login there. A corrupt or unreadable file is not
    # this: what is in it cannot be known, and it is not written over.
    live_missing = (not live.get('read')
                    and (live.get('judgement') or {}).get('key') == 'cc_j_file_missing')
    live_empty = live_missing or bool(live.get('read') and not live.get('refresh_print')
                                      and not live.get('access_print'))
    # The saved profile holding the very login that is live now: the one every
    # gesture uses for that account (the elected one among twins), and never a
    # folder no switch can use. A '_auto-' parking left by another tool made
    # the outgoing save fail on its name and blocked every switch away (review
    # of 2026-09-26, second iteration). Such a folder still proves whose the
    # login is: it holds the same token.
    holders = [p for p in saved
               if p.get('readable', True) and p.get('refresh_print')
               and p['refresh_print'] == live.get('refresh_print')]
    usable_holders = [p for p in holders if _usable_name(p.get('name'))]
    holder = next((p for p in usable_holders if p.get('active')),
                  usable_holders[0] if usable_holders else None)
    proof_holder = holder or (holders[0] if holders else None)

    # Already on this account? The fingerprint proves it. The organization
    # would too, but only when it is really the tokens' own: see below.
    same_token = bool(live.get('refresh_print')) and live['refresh_print'] == target_print
    if live.get('read') and same_token:
        return {'done': False, 'asks': False,
                'judgement': verdict(UNKNOWN, 'cc_j_already_active',
                                     {'name': name, 'behind': False},
                                     'cc_r_nothing_to_do')}

    if (live.get('read') and live.get('access_print') and not live.get('org')
            and proof_holder is None):
        # Nobody can say whose login this is: the file does not name it (the
        # state right after a sign-in), no saved profile holds these tokens,
        # and the server could not be asked. Treating "unknown" as "another
        # account" parked a fresh login under an 'auto-' name and put an older
        # snapshot live, and Claude Code answered the next command with
        # "OAuth session expired" (review of 2026-09-26). Nothing is written.
        return {'done': False, 'asks': False,
                'judgement': verdict(UNKNOWN, 'cc_j_live_unidentified', {},
                                     'cc_r_identify_live')}

    same_org = bool(live.get('org')) and target.get('organizationUuid') == live['org']
    # With no login in the file an organization left behind in it names nobody.
    if live.get('read') and same_org and not live_empty:
        # The organization says "this account", and the tokens cannot: they
        # have rotated since the snapshot. Deciding on the fingerprint alone
        # would let a switch onto a stale snapshot of the CURRENT account write
        # an old token over the live one. But the file's key can be wrong -
        # written by a tool that swapped only the tokens, or from the lagging
        # cache - and then "already on it" leaves the user on another account
        # while the widget says otherwise. A saved profile of another account
        # holding these tokens proves the file wrong; otherwise the server is
        # asked about the token, when it can be.
        foreign = proof_holder is not None and proof_holder.get('org') != live['org']
        if not foreign and live.get('org_source') == 'live' and call is not None:
            live = live_identity(live, call, confirm=True)
            if live.get('org') != target.get('organizationUuid'):
                foreign = True
                saved = profiles(live)
        if not foreign:
            return {'done': False, 'asks': False,
                    'judgement': verdict(UNKNOWN, 'cc_j_already_active',
                                         {'name': name, 'behind': True},
                                         'cc_r_save_now')}

    # Judged as the list judges it: a newer copy of the live file carrying
    # another renewal token for this account means the one in the profile
    # has been retired, and a switch would put a dead token live.
    held = {p['refresh_print']: p['org'] for p in saved
            if p.get('readable', True) and p.get('refresh_print') and p.get('org')}
    judgements = health(target) + _outdated(target, _saved_at(target, name),
                                            backups(held), name)[1]
    bad = [j for j in judgements if j['kind'] == BAD]
    if bad:
        return {'done': False, 'asks': False, 'judgement': bad[0]}

    # A profile whose subscription has lapsed still carries dates in the
    # future: nothing on this disk says it is dead. The server refuses to
    # renew it, and Claude Code answers the user's next command with "OAuth
    # session expired and could not be refreshed" - after the widget had said
    # everything was fine. When the caller can look the subscription up (the
    # widget holds a claude.ai key for these accounts), a lapsed one stops the
    # switch here, where it can still be explained and undone. Only a clear NO
    # stops it: None means the question could not be asked, and that is not an
    # answer to act on.
    if plan_check is not None and plan_check(target.get('organizationUuid')) is False:
        return {'done': False, 'asks': False,
                'judgement': verdict(BAD, 'cc_j_no_subscription',
                                     {'name': name, 'email': target.get('email')},
                                     'cc_r_renew_or_pick_other')}

    # 🔴 The saved login is renewed before it goes live (renew): the one test
    # that says whether the server still knows its token, and a login just
    # renewed is one Claude Code can use for hours. The Planner's switch of
    # 2026-09-30 went through on the date alone, onto a login the server had
    # stopped accepting, and Claude Code signed out. A refusal stops the
    # switch with nothing written. No answer is not a refusal: the user
    # decides (asks), and a switch made knowingly writes the login as saved.
    if not live.get('read') and not live_missing:
        # A file that is there and cannot be read is never written over
        # (below): renewing first would spend a renewal for nothing.
        return {'done': False, 'asks': False, 'judgement': live.get('judgement')}

    checked = None
    if post is not None:
        checked = renew(name, post)
        if checked['judgement']['kind'] == BAD:
            return {'done': False, 'asks': False, 'judgement': checked['judgement'],
                    'checked': checked}
        if checked.get('moved'):
            # The saved login changed while it was being renewed: what it
            # holds now has not been looked at, and the token read before may
            # have just been spent. Nothing is written; a new try reads it.
            return {'done': False, 'asks': False, 'judgement': checked['judgement'],
                    'checked': checked}
        if checked.get('renewed'):
            target, failure = read_profile(name)
            if failure:
                return {'done': False, 'asks': False, 'judgement': failure,
                        'checked': checked}
            # The server has just answered for it: a doubt from the disk no
            # longer stands.
            judgements = [j for j in judgements if j['kind'] != UNKNOWN]
        elif not knowingly:
            return {'done': False, 'asks': True, 'judgement': checked['judgement'],
                    'checked': checked}
    elif call is not None:
        # A caller that cannot renew: the free read with the short token,
        # when it is still good. It can only ever say NO.
        checked = check_alive(name, call)
        if checked['judgement']['kind'] == BAD:
            return {'done': False, 'asks': False, 'judgement': checked['judgement'],
                    'checked': checked}

    unsure = [j for j in judgements if j['kind'] == UNKNOWN]
    if unsure and not knowingly:
        return {'done': False, 'asks': True, 'judgement': unsure[0],
                'checked': checked}

    moving = {'done': False, 'asks': False,
              'judgement': verdict(UNKNOWN, 'cc_j_live_moving', {}, 'cc_r_try_again')}
    if live_empty:
        return _switch_onto_empty(name, target, checked, moving)

    # Whose the outgoing login is, decided before the lock, and PROVED: the
    # server (already asked above when the file did not say), a saved profile
    # holding the same tokens, or the server asked now.
    pre, failure = read_json(cred_file())
    if failure:
        return {'done': False, 'asks': False, 'judgement': failure}
    pre_oauth = pre.get('claudeAiOauth') or {}
    pre_print = fingerprint(pre_oauth.get('refreshToken'))
    out_proven = None
    if (live.get('org_source') == 'server' and live.get('org')
            and pre_print and pre_print == live.get('refresh_print')):
        out_proven = {'org': live['org'], 'email': live.get('server_email'),
                      'note': 'server'}
    elif (proof_holder is not None and proof_holder.get('org')
          and proof_holder.get('refresh_print') == pre_print):
        out_proven = {'org': proof_holder['org'], 'email': proof_holder.get('email'),
                      'note': 'proof'}
    elif (call is not None and pre_oauth.get('accessToken')
          and not when(pre_oauth.get('expiresAt'))[1]):
        srv_org, srv_email = ask_server_identity(pre_oauth.get('accessToken'), call)
        if srv_org:
            out_proven = {'org': srv_org, 'email': srv_email, 'note': 'server'}
    if out_proven is None:
        # 🔴 Only the file's own key would say whose the outgoing login is,
        # and that key is written by the tools that switch accounts: one that
        # stamped it from the lagging cache left Personal's tokens under Work's
        # organization, and the switch saved them into Work's saved login and
        # reported success (review of 2026-09-26, second iteration). Nothing
        # is written without a proof. A renewal brings one: the next check can
        # ask the server about the new token.
        return {'done': False, 'asks': False,
                'judgement': verdict(UNKNOWN, 'cc_j_live_unidentified', {},
                                     'cc_r_identify_live')}
    out_org = out_proven['org']
    if out_org == target.get('organizationUuid'):
        # The login in use IS this account: its tokens rotated after the
        # saved login was taken, and the file's key named another account, so
        # nothing above could tell. Saving the live tokens into the target
        # and then writing the target's older snapshot live put a retired
        # token in use (review of 2026-09-26, second iteration).
        return {'done': False, 'asks': False,
                'judgement': verdict(UNKNOWN, 'cc_j_already_active',
                                     {'name': name, 'behind': True},
                                     'cc_r_save_now')}
    out_prof = (holder if holder is not None and holder.get('org') == out_org
                and holder.get('refresh_print') == pre_print
                else find_profile_for_org(out_org, saved))
    if out_prof is not None:
        saved_out = out_prof['name']
    else:
        wanted = None
        if name_for_org is not None and out_org:
            try:
                wanted = profile_name_for(name_for_org(out_org))
            except Exception:
                wanted = None
        if wanted and not os.path.exists(os.path.join(store(), wanted)):
            saved_out = wanted
        else:
            # Not '_auto-': valid_name refuses a leading underscore, so the
            # parking name our own rule rejects made every switch from an
            # account with no saved profile fail at the last step.
            saved_out = 'auto-' + time.strftime('%Y%m%d-%H%M%S')

    with _LOCK:
        # Re-read here: Claude Code may have renewed, or signed in, since the
        # login was identified, and an identity proved for one token is not
        # carried over to another. Nothing is written then; the next try
        # identifies the new one.
        current, failure = read_json(cred_file())
        if failure:
            return {'done': False, 'asks': False, 'judgement': failure}
        if fingerprint((current.get('claudeAiOauth') or {}).get('refreshToken')) != pre_print:
            return moving
        try:
            save_profile(saved_out, current, proven=out_proven)
        except ValueError as e:
            # A saved login that already holds a later state of this account
            # needs nothing from the one being left: the switch goes on. It
            # used to stop here, on a rule that took an earlier sign-in for
            # an older token, and "Switch" became useless (the user,
            # 2026-09-27). Any other refusal still stops it.
            if str(e) != 'cc_e_save_older':
                return {'done': False, 'asks': False,
                        'judgement': verdict(BAD, 'cc_j_outgoing_not_saved',
                                             {'why': str(e)}, 'cc_r_try_again')}
            saved_out = None           # nothing was saved: say none
        except OSError as e:
            return {'done': False, 'asks': False,
                    'judgement': verdict(BAD, 'cc_j_outgoing_not_saved',
                                         {'why': str(e)}, 'cc_r_try_again')}

        # A copy of an incoherent file, one account's tokens under another
        # account's key, is named apart: backups() read it as a copy of the
        # key's account, and once the holder of these tokens renewed them it
        # declared that account's intact saved login outdated (review of
        # 2026-09-26, third iteration).
        file_key = current.get('organizationUuid')
        backup_live(INCOHERENT_COPY if file_key and file_key != out_org
                    else 'credentials')
        # The swap goes onto a read taken NOW that still carries the tokens
        # just snapshotted. The snapshot, the copy and the checks above leave
        # a window in which Claude Code can renew or pick up an MCP token, and
        # writing the older object back would undo it (claude-account.ps1,
        # fourth and seventh reviews of 2026-09-13).
        fresh = read_live_for_write(current)
        if fresh is None:
            return moving
        return _write_target(name, target, fresh, checked, saved_out)


def _write_target(name, target, fresh, checked, saved_out):
    """Put the target's login into `fresh` (a read of Claude Code's file taken
    just now, inside the lock) and write it, then read it back. The last step
    of every switch."""
    had_mcp = 'mcpOAuth' in fresh
    fresh['claudeAiOauth'] = target['claudeAiOauth']
    if target.get('organizationUuid'):
        fresh['organizationUuid'] = target['organizationUuid']
    # else: the old organization is LEFT IN PLACE. Writing null here is the
    # state that froze every chat, and a profile with no organization has
    # already been refused above, so this branch only exists as a guard.
    write_json_atomic(cred_file(), fresh)

    after, failure = read_json(cred_file())
    if failure:
        return {'done': False, 'asks': False, 'judgement': failure}
    token_ok = (after.get('claudeAiOauth') or {}).get('accessToken') == \
        (target.get('claudeAiOauth') or {}).get('accessToken')
    org_ok = (not target.get('organizationUuid')
              or after.get('organizationUuid') == target['organizationUuid'])
    mcp_kept = ('mcpOAuth' in after) or not had_mcp
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


def _switch_onto_empty(name, target, checked, moving):
    """The switch when Claude Code's file holds no login (switch, live_empty):
    nothing to identify and nothing to save, only a login to put there.

    Inside the lock the file is looked at again, after its copy: a sign-in
    that landed meanwhile is a login, and is not written over.
    """
    with _LOCK:
        exists = os.path.exists(cred_file())
        if exists:
            backup_live('credentials')
        current, failure = read_json(cred_file())
        if failure and failure['key'] != 'cc_j_file_missing':
            return {'done': False, 'asks': False, 'judgement': failure}
        current = current if isinstance(current, dict) else {}
        oauth = current.get('claudeAiOauth') or {}
        if oauth.get('refreshToken') or oauth.get('accessToken'):
            return moving
        return _write_target(name, target, current, checked, None)


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
