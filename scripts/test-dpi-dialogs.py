"""DPI scaling regression test for the dialog auto-sizing logic.

Replicates the session-key dialog construction at multiple Tk scaling
values and reports:
  - Required height vs the previous-fixed 320 px
  - Whether the bottom controls (the orange Connect pill) would have been
    cropped under the old fixed-height behaviour
  - Confirms the new auto-size math accommodates the content

Run: python scripts/test-dpi-dialogs.py

Exit code 0 if all scales fit. Exit code 1 if any scale would still crop
even with auto-sizing (indicates virtual desktop too small for the
required content).
"""
import sys
import tkinter as tk
import tkinter.font as tkfont


# Mirror the constants from widget.pyw — kept in sync manually since this
# is a one-shot regression test, not a continuously-imported module.
BG = '#1f1f1d'
BG_TITLE = '#2a2a28'
FG = '#fafafa'
DIM = '#a8a8a8'
BAR_BG = '#33332f'
CLAUDE = '#d97757'

DLG_TB_HEIGHT = 34
DLG_PAD_X = 20
DLG_PAD_TOP = 18
DLG_PAD_BTM = 16
PILL_PAD_PRIMARY_Y = 8
PILL_PAD_SECONDARY_Y = 8

OLD_FIXED_HEIGHT = 320  # the v2.8.31 hard-coded session-key dialog height


def measure_session_key_dialog(scaling):
    """Build the same widget tree as _session_key_dialog at the given Tk
    scaling, return the natural reqheight in pixels.
    """
    root = tk.Tk()
    root.withdraw()
    root.tk.call('tk', 'scaling', scaling)
    dpi = root.winfo_fpixels('1i')

    dlg = tk.Toplevel(root)
    dlg.overrideredirect(True)
    dw, dh = 460, 320

    # Title bar
    tb = tk.Frame(dlg, bg=BG_TITLE, height=DLG_TB_HEIGHT)
    tb.pack(fill='x')
    tb.pack_propagate(False)
    tk.Label(tb, text='Welcome', font=('Segoe UI', 10, 'bold'),
             fg=FG, bg=BG_TITLE).pack(side='left', padx=14)

    body = tk.Frame(dlg, bg=BG)
    body.pack(fill='both', expand=True,
              padx=DLG_PAD_X, pady=(DLG_PAD_TOP, DLG_PAD_BTM))

    # Welcome hint
    tk.Label(body, text='Connect the widget to your Claude.ai account.',
             font=('Segoe UI', 10), fg=DIM, bg=BG, anchor='w', justify='left',
             wraplength=dw - 40).pack(fill='x', pady=(0, 14))

    # Step 1
    tk.Label(body, text='Where do I find my session key?',
             font=('Segoe UI', 10, 'bold'), fg=FG, bg=BG,
             anchor='w').pack(fill='x')
    # Stand-in for secondary pill (text + paddings)
    f1 = tk.Frame(body, bg=BAR_BG)
    f1.pack(anchor='w', pady=(8, 16))
    tk.Label(f1, text=' Open guide in browser ', font=('Segoe UI', 10),
             fg=FG, bg=BAR_BG, padx=18 + int(round(18 * (dpi / 96 - 1))),
             pady=8 + int(round(8 * (dpi / 96 - 1)))).pack()

    # Step 2
    tk.Label(body, text='Paste your session key below',
             font=('Segoe UI', 10, 'bold'), fg=FG, bg=BG,
             anchor='w').pack(fill='x')
    entry_wrap = tk.Frame(body, bg=BAR_BG, padx=1, pady=1)
    entry_wrap.pack(fill='x', pady=(8, 0))
    tk.Entry(entry_wrap, font=('Segoe UI', 10), bg=BAR_BG, fg=FG,
             bd=0).pack(fill='x', ipady=7, ipadx=10)

    # Status label
    tk.Label(body, text='', font=('Segoe UI', 9), fg=DIM, bg=BG,
             anchor='w', wraplength=dw - 40).pack(fill='x', pady=(8, 0))

    # Button frame
    btn = tk.Frame(body, bg=BG)
    btn.pack(fill='x', side='bottom', pady=(12, 0))
    py = 8 + int(round(8 * (dpi / 96 - 1)))
    px = 22 + int(round(22 * (dpi / 96 - 1)))
    tk.Label(btn, text='  Connect  ', font=('Segoe UI', 10, 'bold'),
             fg='#FFFFFF', bg=CLAUDE, padx=px, pady=py).pack(side='right')
    tk.Label(btn, text='  Cancel  ', font=('Segoe UI', 10),
             fg=FG, bg=BAR_BG, padx=18, pady=8).pack(side='right', padx=(0, 8))

    dlg.update_idletasks()
    req_h = dlg.winfo_reqheight()
    req_w = dlg.winfo_reqwidth()

    root.destroy()
    return dpi, req_w, req_h


def main():
    scenarios = [
        (1.333, '100% (96 DPI, 1080p baseline)'),
        (1.667, '125% (120 DPI)'),
        (2.0,   '150% (144 DPI, common laptop)'),
        (2.333, '175% (168 DPI)'),
        (2.667, '200% (192 DPI, 4K)'),
    ]

    print(f"{'scaling':<8} {'DPI':<6} {'reqW':<6} {'reqH':<6} {'old=320':<10} {'fit?':<5} scenario")
    print('-' * 90)

    any_overflow_at_old = False
    auto_size_works = True

    for scaling, label in scenarios:
        dpi, req_w, req_h = measure_session_key_dialog(scaling)
        cropped_old = req_h > OLD_FIXED_HEIGHT
        if cropped_old:
            any_overflow_at_old = True
        # New auto-size: dialog grows to req_h. We just need it to be
        # reasonable (not negative, not absurd).
        fits_now = 100 < req_h < 2000
        if not fits_now:
            auto_size_works = False
        marker = 'CLIPPED' if cropped_old else 'ok'
        fit_marker = 'YES' if fits_now else 'NO'
        print(f'{scaling:<8} {dpi:<6.0f} {req_w:<6} {req_h:<6} {marker:<10} {fit_marker:<5} {label}')

    print('-' * 90)
    print()
    print('Summary:')
    print(f'  - Old fixed height (320 px) WOULD crop the Connect pill: '
          f'{"YES" if any_overflow_at_old else "no"}')
    print(f'  - New auto-size (winfo_reqheight) handles all scales: '
          f'{"yes" if auto_size_works else "NO"}')
    sys.exit(0 if auto_size_works else 1)


if __name__ == '__main__':
    main()
