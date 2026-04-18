"""Preview menu icons at actual render size + text vs emoji presentation."""
import tkinter as tk

BG = '#2c2c2a'
BG2 = '#262624'
FG = '#e4e4e4'
DIM = '#d0d0ce'

FT_MENU = ('Segoe UI', 10)         # menu label text
FT_ICON = ('Segoe UI', 9)          # icon size for non-top items
FT_ICON_BIG = ('Segoe UI', 11)     # icon size for top items (Refresh, mode toggle)
FT_EMOJI = ('Segoe UI Emoji', 11)  # forces emoji font

root = tk.Tk()
root.title('Menu icon preview')
root.configure(bg=BG)
root.geometry('1800x950')

tk.Label(root, text='Icons shown at real menu size. Each cell: TEXT (Segoe UI 9) | TEXT BIG (Segoe UI 11) | EMOJI (Segoe UI Emoji 11) | Description',
         font=('Segoe UI', 10, 'bold'), fg=FG, bg=BG).pack(pady=(8, 0))
tk.Label(root, text='"BIG" is what Refresh and Mode toggle icons use; "TEXT" is what other menu items use.',
         font=('Segoe UI', 9), fg=DIM, bg=BG).pack(pady=(0, 10))

categories = {
    'MODE TOGGLE (essential / standard)': [
        ('\u21C5', 'U+21C5 up/down arrows (current)'),
        ('\u21F5', 'U+21F5 down then up arrows'),
        ('\u2B0D', 'U+2B0D up/down heavy arrow'),
        ('\u2195', 'U+2195 up/down arrow'),
        ('\u25F0', 'U+25F0 square upper-left quadrant'),
        ('\u25F1', 'U+25F1 square lower-left quadrant'),
        ('\u25A3', 'U+25A3 square rounded inside'),
        ('\u2922', 'U+2922 NE-SW arrow'),
        ('\u26F6', 'U+26F6 square four corners'),
        ('\U0001F5D6', 'U+1F5D6 maximize'),
        ('\U0001F5D5', 'U+1F5D5 minimize'),
        ('\U0001F5D4', 'U+1F5D4 overlapping windows'),
    ],
    'TIMER (refresh interval)': [
        ('\u23F1', 'U+23F1 stopwatch'),
        ('\u23F2', 'U+23F2 timer clock'),
        ('\u23F0', 'U+23F0 alarm clock'),
        ('\u231A', 'U+231A watch'),
        ('\u231B', 'U+231B hourglass'),
        ('\u29D6', 'U+29D6 white hourglass'),
        ('\u29D7', 'U+29D7 black hourglass'),
        ('\u23F3', 'U+23F3 hourglass sand'),
        ('\U0001F551', 'U+1F551 clock face two'),
        ('\U0001F55B', 'U+1F55B clock face twelve'),
        ('\u25F7', 'U+25F7 quarter circle'),
        ('\u29BF', 'U+29BF circled bullet'),
    ],
    'GLOBE / LANGUAGE': [
        ('\U0001F310', 'U+1F310 globe meridians'),
        ('\U0001F30D', 'U+1F30D earth EU-AF'),
        ('\U0001F30E', 'U+1F30E earth Americas'),
        ('\U0001F30F', 'U+1F30F earth Asia'),
        ('\u6587', 'U+6587 CJK text'),
        ('\u8A9E', 'U+8A9E CJK language'),
        ('\u2316', 'U+2316 position indicator'),
        ('\u25CE', 'U+25CE bullseye'),
        ('\u2295', 'U+2295 circled plus'),
    ],
    'SETTINGS / CONFIG': [
        ('\u2699', 'U+2699 gear (current)'),
        ('\U0001F527', 'U+1F527 wrench'),
        ('\U0001F528', 'U+1F528 hammer'),
        ('\U0001F6E0', 'U+1F6E0 hammer + wrench'),
        ('\U0001F4D0', 'U+1F4D0 triangular ruler'),
        ('\U0001F5C4', 'U+1F5C4 file cabinet'),
        ('\U0001F4C4', 'U+1F4C4 document'),
        ('\U0001F4DD', 'U+1F4DD memo'),
        ('\u26ED', 'U+26ED gear no hub'),
        ('\u26EE', 'U+26EE gear white hub'),
        ('{}', '{} braces (JSON)'),
    ],
    'SESSION KEY': [
        ('\u2692', 'U+2692 hammer + pick (current)'),
        ('\U0001F511', 'U+1F511 key'),
        ('\U0001F510', 'U+1F510 lock with key'),
        ('\U0001F5DD', 'U+1F5DD old key'),
        ('\U0001F50F', 'U+1F50F lock ink pen'),
        ('\U0001F512', 'U+1F512 lock'),
        ('\U0001F513', 'U+1F513 open lock'),
        ('\U0001F517', 'U+1F517 link'),
        ('\u26A1', 'U+26A1 high voltage'),
        ('\u270E', 'U+270E pencil'),
        ('\u270F', 'U+270F pencil'),
        ('\U0001F464', 'U+1F464 silhouette'),
    ],
}

col_frame = tk.Frame(root, bg=BG)
col_frame.pack(fill='both', expand=True, padx=10, pady=10)

for col, (cat_name, icons) in enumerate(categories.items()):
    col_container = tk.Frame(col_frame, bg=BG)
    col_container.grid(row=0, column=col, sticky='nw', padx=6)
    tk.Label(col_container, text=cat_name, font=('Segoe UI', 10, 'bold'),
             fg=FG, bg=BG, anchor='w').pack(fill='x', pady=(0, 4))

    header = tk.Frame(col_container, bg=BG)
    header.pack(fill='x')
    for label, w in [('T9', 3), ('T11', 3), ('E11', 3), ('desc', 20)]:
        tk.Label(header, text=label, font=('Segoe UI', 8), fg=DIM, bg=BG,
                 width=w).pack(side='left')

    for icon, desc in icons:
        row = tk.Frame(col_container, bg=BG2)
        row.pack(fill='x', pady=1)
        tk.Label(row, text=icon, font=FT_ICON, fg=FG, bg=BG2,
                 padx=4, pady=3, width=3).pack(side='left')
        tk.Label(row, text=icon, font=FT_ICON_BIG, fg=FG, bg=BG2,
                 padx=4, pady=3, width=3).pack(side='left')
        tk.Label(row, text=icon, font=FT_EMOJI, fg=FG, bg=BG2,
                 padx=4, pady=3, width=3).pack(side='left')
        tk.Label(row, text=desc, font=('Segoe UI', 8), fg=DIM, bg=BG2,
                 anchor='w', padx=6).pack(side='left', fill='x', expand=True)

for i in range(5):
    col_frame.grid_columnconfigure(i, weight=1)

root.mainloop()
