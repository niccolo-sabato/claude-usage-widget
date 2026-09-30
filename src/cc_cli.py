"""The saved Claude Code logins from the command line, on the widget's rules.

    "Claude Usage.exe" --cc list    [--quiet] [--out FILE]
    "Claude Usage.exe" --cc save    NAME [--quiet] [--out FILE]
    "Claude Usage.exe" --cc switch  NAME [--force] [--quiet] [--out FILE]
    "Claude Usage.exe" --cc verify  NAME [--quiet] [--out FILE]
    "Claude Usage.exe" --cc resolve NAME [--out FILE]
    "Claude Usage.exe" --cc sync    [--quiet] [--out FILE]
    "Claude Usage.exe" --cc doctor  [--out FILE]

Written on 2026-09-26, on the user's word: the scripts that switch accounts
must use the saved logins the widget uses. They had rules of their own for
which saved login belongs to which account, took the first twin of an account
in name order, and left Work's saved login hours behind the one in use, with
a renewal token Claude Code had already rotated away and a switch by name
ready to put it back. From now on there is one
set of rules, the widget's: claude-account.ps1 keeps its commands and its exit
codes, and asks here.

The installed widget is a windowed program with no console, so --out names a
file the output is written to; without it the output goes to stdout.

Exit codes are claude-account.ps1's, so every script built on it keeps
working: 0 done, or nothing to do; 2 refused before anything was written;
3 stopped before the login was switched (the login in use could not be
identified, it moved meanwhile, or the outgoing one could not be saved: that
save may have added the organization to the file, next to the same account's
tokens); 4 written, but not read back as expected; 1 an error that cannot be
placed, before or after a write,
as a failure of the old script was: the scripts then look at the
credentials file themselves. Never a 3 for that: claude-switch-to.ps1 drops
its way back on a 3 without looking.

Nothing here reaches the widget's own configuration except to READ it (the
language, and the names of the accounts): the widget that is running owns
that file. No token and no key is ever printed.

The widget that is running and this command are two processes, and the
module's lock holds inside one only. What keeps them from undoing each
other is what already kept Claude Code and the old scripts from it: the
credentials file is re-read just before every write and the write stops if
it moved, a saved login is never replaced by an older one, and every file
is replaced whole (write_json_atomic), never patched in place.
"""
import json
import traceback

import cc_accounts as cc

EXIT_DONE, EXIT_ERROR, EXIT_REFUSED, EXIT_STOPPED, EXIT_UNVERIFIED = 0, 1, 2, 3, 4
ACTIONS = ('list', 'save', 'switch', 'verify', 'resolve', 'sync', 'doctor')

# A switch stopped by these was not refused on the saved login's merits, and
# stopped before the credentials file was written: the login in use could not
# be told apart, it moved meanwhile, or the outgoing one could not be saved
# (that save may have added the organization to the file, next to the same
# account's tokens, as with the old script's 3).
_STOPPED = {'cc_j_live_unidentified', 'cc_j_live_moving', 'cc_j_outgoing_not_saved',
            'cc_j_renew_moved', 'cc_j_renew_busy', 'cc_j_renew_live_refused'}
# The credentials file could not be read: before the write, or right after it
# when the switch reads back what it wrote, and the answer does not say which.
# So "unknown" (1), and the scripts look at the file themselves.
_UNREADABLE = {'cc_j_file_missing', 'cc_j_file_corrupt', 'cc_j_file_unreadable'}


def _config(w):
    """The widget's configuration, read and never written."""
    try:
        with open(w.CFG, encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _say(w, judgement):
    """A judgement and its remedy, in the words the widget uses."""
    if not judgement:
        return ''
    data = dict(judgement.get('data') or {})
    data.setdefault('path', cc.cred_file())
    data.setdefault('why', '')
    judgement = dict(judgement, data=data)
    return ' '.join(x for x in (w.cc_say(judgement), w.cc_say(judgement, 'remedy')) if x)


def _named(name, cfg):
    """(org, is_folder): the account `name` stands for.

    A saved login's folder comes first, with the organization written in it
    (None for an old one that has none); otherwise the widget's account of
    that name. (None, False) for a name that stands for nothing yet.
    """
    data, failure = cc.read_profile(name)
    if not failure:
        return data.get('organizationUuid'), True
    low = (name or '').casefold()
    acc = next((a for a in cfg.get('accounts', [])
                if (a.get('name') or '').casefold() == low), None)
    return (acc or {}).get('org_id'), False


def resolve(name, cfg):
    """The saved login the widget uses for the account `name` stands for, or None.

    `name` is a saved login's folder, or the name of an account in the widget.
    Among twins of one organization the widget elects one (find_profile_for_org)
    and so does this: a script switching "to Work" gets the Work login the
    widget would use, never an older twin that happens to carry the name.
    """
    org, is_folder = _named(name, cfg)
    elected = cc.find_profile_for_org(org) if org else None
    if elected:
        return elected['name']
    return name if is_folder else None


def _in_use(p):
    """Whether a saved login is the one in use, as the widget's rows say it:
    only on a certain proof. The identity cache's guess (profiles() calls it
    'likely') lags behind a sign-in by hours, and said "in use here" of the
    account Claude Code had just left (review of 2026-09-26, third iteration)."""
    return bool(p.get('active') and p.get('certainty') == 'certain')


def cmd_list(w, out, quiet):
    st = cc.state(call=w.cc_http, confirm=True)
    profiles = [p for p in st.get('profiles') or [] if p.get('readable', True)]
    if quiet:
        here = next((p['name'] for p in profiles if _in_use(p)), '')
        out.append('active="%s" profiles="%s"' % (here, ','.join(p['name'] for p in profiles)))
        return EXIT_DONE
    for p in profiles:
        mark = '*' if _in_use(p) else ' '
        until = w.t('acct_saved_login').format(
            when=w.pretty_date(p['refresh_expires'].isoformat(), True)
            if p.get('refresh_expires') else '?')
        line = '%s %-22s %-32s %s' % (mark, p['name'], p.get('email') or '', until)
        if _in_use(p):
            line += '  (%s)' % w.t('acct_cc_here')
        check = p.get('check')
        if check:
            # When the server last answered for this very token (verify, or
            # the renewal a switch makes): the date alone never said it.
            line += '  [%s]' % w.t('cc_ui_st_verified' if check['kind'] == cc.GOOD
                                   else 'cc_ui_st_refused').format(
                when=w.cc_when(check['at']),
                until=w.pretty_date(p['refresh_expires'].isoformat())
                if p.get('refresh_expires') else '?')
        out.append(line)
        if p.get('verdict') != cc.GOOD:
            bad = next((j for j in p.get('judgements') or [] if j.get('kind') != cc.GOOD), None)
            if bad:
                out.append('    ' + _say(w, bad))
    if st.get('unrecognised'):
        out.append(_say(w, st['unrecognised']))
    elif not any(_in_use(p) for p in profiles) and (st.get('live') or {}).get('read'):
        out.append(w.t('cc_here_unknown'))
    return EXIT_DONE


def cmd_resolve(w, out, name, cfg):
    got = resolve(name, cfg)
    if got is None:
        out.append('resolved=""')
        return EXIT_REFUSED
    out.append('resolved="%s"' % got)
    return EXIT_DONE


def cmd_save(w, out, name, quiet, cfg):
    # Whose the login in use is: the server's word, or a saved login holding
    # the very same renewal token; never the organization written in the file
    # alone. The saved login it belongs in is then the one the widget uses for
    # that account, whatever name the script passed: a second folder for the
    # same account is a twin, and twins are how Work's saved login fell behind.
    st = cc.state(call=w.cc_http, confirm=True)
    live = st.get('live') or {}
    saved = st.get('profiles') or []
    org = live.get('org') if live.get('org_source') == 'server' else None
    if not org and live.get('refresh_print'):
        org = next((p['org'] for p in saved
                    if p.get('readable', True) and p.get('org')
                    and p.get('refresh_print') == live['refresh_print']), None)
    named, _ = _named(name, cfg)
    target = name
    if org:
        elected = cc.find_profile_for_org(org, saved)
        if elected:
            target = elected['name']

    def refused(key):
        out.append('refused="%s" why="%s"' % (name, key) if quiet
                   else _say(w, {'key': key, 'data': {}}))
        return EXIT_REFUSED

    # A name that stands for ANOTHER account (its saved login, or the widget's
    # name for it) is refused, never saved into: "save Personal" typed while
    # Claude Code is on Work would otherwise put Work's login under that name.
    if org and named and named != org:
        return refused('cc_e_save_other_account')
    try:
        prof = cc.save_profile(target, call=w.cc_http, expect_org=org or named)
    except ValueError as e:
        return refused(str(e))
    except OSError as e:
        out.append('aborted="%s" why="%s"' % (name, type(e).__name__) if quiet
                   else w._redact(str(e)))
        return EXIT_STOPPED
    if quiet:
        out.append('saved="%s" tier="%s" org="True" live_org="%s"' % (
            target, prof.get('rateLimitTier') or '', bool(cc.active().get('org'))))
        return EXIT_DONE
    if target != name:
        out.append(w.t('cc_cli_saved_into').format(name=target))
    out.append(w.t('cc_ui_snapshot_saved').format(
        when=w.pretty_date(prof.get('savedAt'), True)))
    return EXIT_DONE


def cmd_switch(w, out, name, force, quiet, cfg):
    target = resolve(name, cfg)
    if target is None:
        j = cc.verdict(cc.BAD, 'cc_j_no_such_profile',
                       {'name': name, 'known': cc.profile_names()}, 'cc_r_pick_existing')
        out.append('refused="%s" why="%s"' % (name, j['key']) if quiet else _say(w, j))
        return EXIT_REFUSED
    if target != name and not quiet:
        out.append(w.t('cc_cli_uses').format(name=target))
    names = {a.get('org_id'): a.get('name') for a in cfg.get('accounts', [])
             if a.get('org_id')}
    # No subscription check here: it reads the account with its session key,
    # and a key claude.ai rotates on that read would have to be written into
    # the configuration that the running widget owns. The widget's own switch
    # makes that check.
    # The saved login is renewed before it is written (cc_accounts.renew):
    # a login the server no longer accepts is refused here, before Claude
    # Code is touched. The Planner's scheduled switch of 2026-09-30 went
    # through on the date alone, onto such a login, and signed Claude Code
    # out. With --force a switch that could not be checked (no answer) goes
    # on; a refusal never does.
    r = cc.switch(target, knowingly=force, call=w.cc_http, post=w.cc_post,
                  name_for_org=lambda org: names.get(org))
    j = r.get('judgement') or {}
    key = j.get('key') or ''
    renewed = bool((r.get('checked') or {}).get('renewed'))
    if r.get('done'):
        if quiet:
            out.append('switched="%s" tier="%s" saved_out="%s" renewed="%s"' % (
                target, (j.get('data') or {}).get('tier') or '', r.get('saved_out') or '',
                'yes' if renewed else 'no'))
        else:
            out.append(_say(w, j))
            if renewed:
                out.append(w.cc_say(r['checked']['judgement']))
        return EXIT_DONE
    if key == 'cc_j_already_active':
        out.append('already-active="%s"' % target if quiet else _say(w, j))
        return EXIT_DONE
    if key == 'cc_j_write_unverified':
        out.append('postflight-failed="%s" backups="%s"' % (target, cc.backups_dir())
                   if quiet else _say(w, j))
        return EXIT_UNVERIFIED
    if key in _UNREADABLE:
        out.append('error="%s" why="%s"' % (target, key) if quiet else _say(w, j))
        return EXIT_ERROR
    if key in _STOPPED:
        out.append('aborted="%s" why="%s"' % (target, key) if quiet else _say(w, j))
        return EXIT_STOPPED
    if quiet:
        out.append('refused="%s" why="%s"' % (target, key))
    else:
        out.append(_say(w, j))
        if r.get('asks'):
            out.append(w.t('cc_cli_force').format(name=name))
    return EXIT_REFUSED


def cmd_verify(w, out, name, quiet, cfg):
    """Whether a saved login still works, by the only test that says so: the
    login in use is asked about with its short token (a read), any other is
    renewed and kept renewed (cc_accounts.verify). 0 it works, 2 the server
    refused it (sign in again), 3 no answer: nothing is known."""
    target = resolve(name, cfg)
    if target is None:
        j = cc.verdict(cc.BAD, 'cc_j_no_such_profile',
                       {'name': name, 'known': cc.profile_names()}, 'cc_r_pick_existing')
        out.append('refused="%s" why="%s"' % (name, j['key']) if quiet else _say(w, j))
        return EXIT_REFUSED
    r = cc.verify(target, call=w.cc_http, post=w.cc_post)
    j = r.get('judgement') or {}
    kind = j.get('kind')
    if quiet:
        out.append('verified="%s" result="%s" why="%s" renewed="%s"' % (
            target, kind or '', j.get('key') or '', 'yes' if r.get('renewed') else 'no'))
    else:
        if target != name:
            out.append(w.t('cc_cli_uses').format(name=target))
        out.append(_say(w, j))
    if kind == cc.GOOD:
        return EXIT_DONE
    if kind == cc.BAD:
        return EXIT_REFUSED
    return EXIT_STOPPED


def cmd_sync(w, out, quiet):
    """What the widget does by itself whenever Claude Code's login changes:
    the saved login of the account in use brought in step with it, on the
    server's word, in the saved login the widget uses for that account. Never
    a new saved login: naming an account is the user's decision (save)."""
    live = cc.active()
    res = cc.sync_live_into_profile(live=live, call=w.cc_http) or {}
    if res.get('updated'):
        # Read back: a write that was not protested against is not a write
        # that happened.
        data, failure = cc.read_profile(res['updated'])
        got = None if failure else cc.fingerprint(
            (data.get('claudeAiOauth') or {}).get('refreshToken'))
        if got != live.get('refresh_print'):
            out.append('unverified="%s"' % res['updated'] if quiet
                       else w.t('cc_cli_sync_unverified').format(name=res['updated']))
            return EXIT_UNVERIFIED
        out.append('synced="%s"' % res['updated'] if quiet
                   else w.t('cc_cli_synced').format(name=res['updated']))
        return EXIT_DONE
    if res.get('pending'):
        out.append('pending="" why="%s"' % res['pending'] if quiet
                   else w.t('cc_r_identify_live'))
        return EXIT_STOPPED
    st = cc.state(call=w.cc_http, confirm=True)
    now = st.get('live') or {}
    holder = next((p for p in st.get('profiles') or []
                   if p.get('active') and p.get('why_active') == 'cc_why_same_refresh'), None)
    if holder:
        out.append('in-step="%s"' % holder['name'] if quiet
                   else w.t('cc_cli_in_step').format(name=holder['name']))
        return EXIT_DONE
    if not now.get('read'):
        j = now.get('judgement') or {}
        out.append('not-synced="" why="%s"' % j.get('key', '') if quiet else _say(w, j))
        return EXIT_STOPPED
    org = now.get('org') if now.get('org_source') == 'server' else None
    if not org:
        out.append('not-synced="" why="unproven"' if quiet else w.t('cc_r_identify_live'))
        return EXIT_STOPPED
    elected = cc.find_profile_for_org(org, st.get('profiles') or [])
    if elected is None:
        out.append('not-synced="" why="unlisted"' if quiet else w.t('cc_cli_unlisted'))
        return EXIT_STOPPED
    # The login in use is one its saved login has already moved past (a copy
    # put back live): the widget never replaces a saved login with an earlier
    # state of itself. By that history, not by the renewal's date, which is
    # the sign-in's (cc_accounts.chain).
    if now.get('refresh_print') and now['refresh_print'] in cc.history_prints(elected['name']):
        out.append('not-synced="%s" why="older-live"' % elected['name'] if quiet
                   else w.t('cc_cli_not_synced').format(name=elected['name']))
        return EXIT_REFUSED
    out.append('not-synced="%s" why="not-written"' % elected['name'] if quiet
               else w.t('cc_cli_not_written').format(name=elected['name']))
    return EXIT_STOPPED


def cmd_doctor(w, out):
    st = cc.state(call=w.cc_http, confirm=True)
    live = st.get('live') or {}
    out.append(_say(w, live.get('judgement')))
    if live.get('incoherent'):
        out.append(w.t('cc_cli_incoherent'))
    here = next((p for p in st.get('profiles') or [] if _in_use(p)), None)
    if here:
        out.append('%s: %s' % (w.t('acct_cc_here'), here['name']))
    elif st.get('unrecognised'):
        out.append(_say(w, st['unrecognised']))
    elif live.get('read'):
        out.append(w.t('cc_here_unknown'))
    # The one repair there is: the saved login of the account in use brought
    # in step, with the organization put back in the live file, both only on
    # the server's word, exactly as the widget does it on its own.
    res = cc.sync_live_into_profile(call=w.cc_http) or {}
    if res.get('updated'):
        out.append(w.t('cc_cli_synced').format(name=res['updated']))
    elif res.get('pending'):
        out.append(w.t('cc_r_identify_live'))
    return EXIT_DONE


def main(argv, w):
    """Run one command. `w` is the widget module, for its transport, its
    words and its log. Returns the exit code."""
    args = list(argv)
    out_file = None
    if '--out' in args:
        i = args.index('--out')
        out_file = args[i + 1] if i + 1 < len(args) else None
        del args[i:i + 2]
    # PowerShell's spelling of the flags too: the advice to force a switch is
    # followed by people who read claude-account.ps1's help, where it is
    # -Force, and '-Force' used to stay behind as a name and be ignored.
    flags = {a.lower() for a in args if a.lower() in ('--quiet', '-quiet', '--force', '-force')}
    quiet = bool(flags & {'--quiet', '-quiet'})
    force = bool(flags & {'--force', '-force'})
    args = [a for a in args if a.lower() not in ('--quiet', '-quiet', '--force', '-force')]
    action = args[0] if args else 'list'
    name = args[1] if len(args) > 1 else None
    cfg = _config(w)
    w.set_lang(cfg.get('language') or 'en')
    lines = []
    # Only a known verb is logged as typed, and the name as typed can be
    # anything, an e-mail address included: both come from outside.
    shown = action if action in ACTIONS else '?'
    w.wlog('CLI    %s%s' % (shown, ' ' + w._redact(name) if name else ''))
    try:
        if action == 'list':
            code = cmd_list(w, lines, quiet)
        elif action in ('save', 'switch', 'verify', 'resolve') and not name:
            lines.append(w.t('cc_cli_usage'))
            code = EXIT_REFUSED
        elif action == 'save':
            code = cmd_save(w, lines, name, quiet, cfg)
        elif action == 'switch':
            code = cmd_switch(w, lines, name, force, quiet, cfg)
        elif action == 'verify':
            code = cmd_verify(w, lines, name, quiet, cfg)
        elif action == 'resolve':
            code = cmd_resolve(w, lines, name, cfg)
        elif action == 'sync':
            code = cmd_sync(w, lines, quiet)
        elif action == 'doctor':
            code = cmd_doctor(w, lines)
        else:
            lines.append(w.t('cc_cli_usage'))
            code = EXIT_REFUSED
    except Exception:
        # Where it failed is not known, possibly after a write: 1.
        w.wlog('CLI    failed: ' + w._redact(traceback.format_exc()))
        lines.append('error="%s" why="error"' % (name or action) if quiet
                     else w.t('cc_cli_failed'))
        code = EXIT_ERROR
    w.wlog(f'CLI    {shown} -> {code}')
    text = '\n'.join(x for x in lines if x) + '\n'
    if out_file:
        try:
            with open(out_file, 'w', encoding='utf-8', newline='\n') as f:
                f.write(text)
        except (OSError, ValueError):
            # The answer is lost. A refusal or a stop wrote nothing and stays
            # what it is; anything else becomes "unknown" for the caller.
            w.wlog('CLI    the answer could not be written')
            return code if code in (EXIT_REFUSED, EXIT_STOPPED) else EXIT_ERROR
    else:
        try:
            print(text, end='')
        except (OSError, AttributeError, ValueError):
            pass
    return code
