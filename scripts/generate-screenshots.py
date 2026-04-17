"""Generate widget screenshots for README."""
from PIL import Image, ImageDraw, ImageFont
import os

OUT = 'C:/Users/Kanjiro/Scripts/claude-usage-widget/docs/images'
os.makedirs(OUT, exist_ok=True)

BG = '#262624'
BG_TITLE = '#1e1e1c'
BAR_BG = '#3a3a38'
FG = '#e4e4e4'
DIM = '#d0d0ce'
CLAUDE = '#DA7756'
RED = '#E85858'
ORANGE = '#E8A838'
BLUE = '#5B9BD5'
PURPLE = '#9B72CF'
OCHRE = '#C8962A'
DOT_W = '#d0d0d0'
DOT_W_D = '#a0a09e'
GREEN_INFO = '#6BC275'
MENU_BG = '#2c2c2a'

S = 2  # scale for hi-res

def F():
    try:
        return {
            'regular': ImageFont.truetype('segoeui.ttf', 9 * S),
            'bold': ImageFont.truetype('segoeuib.ttf', 9 * S),
            'small': ImageFont.truetype('segoeui.ttf', 8 * S),
            'bar': ImageFont.truetype('segoeuib.ttf', 9 * S),
            'title': ImageFont.truetype('segoeuib.ttf', 9 * S),
            'btn': ImageFont.truetype('segoeui.ttf', 11 * S),
        }
    except Exception:
        d = ImageFont.load_default()
        return {k: d for k in ['regular', 'bold', 'small', 'bar', 'title', 'btn']}

f = F()

def pill(draw, x, y, w, h, color):
    r = h / 2
    draw.ellipse([x, y, x + h, y + h], fill=color)
    draw.ellipse([x + w - h, y, x + w, y + h], fill=color)
    if w > h:
        draw.rectangle([x + r, y, x + w - r, y + h], fill=color)

def rrect(draw, xy, radius, fill):
    draw.rounded_rectangle(xy, radius=radius, fill=fill)

def bar_color(pct, accent):
    if pct >= 90:
        return RED
    if pct >= 75:
        return ORANGE
    return accent

def draw_bar(draw, x, y, w, h, pct, color, text=None):
    pill(draw, x, y, w, h, BAR_BG)
    if pct > 0:
        fw = max(h, int(w * pct / 100))
        pill(draw, x, y, fw, h, color)
    if text:
        draw.text((x + w // 2, y + h // 2 - 1), text, fill='white',
                  font=f['bar'], anchor='mm')

# --- 1. Standard collapsed (single bar) ---
W, H = 280 * S, 90 * S
img = Image.new('RGBA', (W, H), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
rrect(d, [0, 0, W, H], 14 * S, BG)
rrect(d, [0, 0, W, 28 * S], 14 * S, BG_TITLE)
d.rectangle([0, 20 * S, W, 28 * S], fill=BG_TITLE)
d.ellipse([10 * S, 8 * S, 22 * S, 20 * S], fill=CLAUDE)
d.text((28 * S, 14 * S), 'Claude Usage', fill=FG, font=f['title'], anchor='lm')
d.text((200 * S, 14 * S), '10:16', fill='white', font=f['small'], anchor='lm')
d.text((228 * S, 14 * S), '\u21bb', fill=DIM, font=f['btn'], anchor='mm')
d.text((246 * S, 14 * S), '\u2261', fill=DIM, font=f['btn'], anchor='mm')
d.text((264 * S, 14 * S), '\u2715', fill=DIM, font=f['regular'], anchor='mm')
d.rectangle([0, 28 * S, W, 29 * S], fill=BAR_BG)
d.text((12 * S, 40 * S), 'Current Session', fill=FG, font=f['regular'], anchor='lm')
d.text((268 * S, 40 * S), '10:16 (3min 00s)', fill=GREEN_INFO, font=f['small'], anchor='rm')
draw_bar(d, 12 * S, 48 * S, W - 24 * S, 16 * S, 74, bar_color(74, CLAUDE), '74%')
d.text((18 * S, 73 * S), 'reset 11:00 (44min)', fill=DIM, font=f['small'], anchor='lm')
d.ellipse([6 * S, H - 14 * S, 18 * S, H - 2 * S], fill=DOT_W_D)
d.ellipse([W - 18 * S, H - 14 * S, W - 6 * S, H - 2 * S], fill=OCHRE)
img.save(os.path.join(OUT, 'widget-standard.png'))
print('1. widget-standard.png')

# --- 2. Standard expanded (three bars) ---
W, H = 280 * S, 220 * S
img = Image.new('RGBA', (W, H), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
rrect(d, [0, 0, W, H], 14 * S, BG)
rrect(d, [0, 0, W, 28 * S], 14 * S, BG_TITLE)
d.rectangle([0, 20 * S, W, 28 * S], fill=BG_TITLE)
d.ellipse([10 * S, 8 * S, 22 * S, 20 * S], fill=CLAUDE)
d.text((28 * S, 14 * S), 'Claude Usage', fill=FG, font=f['title'], anchor='lm')
d.text((200 * S, 14 * S), '10:16', fill='white', font=f['small'], anchor='lm')
d.text((228 * S, 14 * S), '\u21bb', fill=DIM, font=f['btn'], anchor='mm')
d.text((246 * S, 14 * S), '\u2261', fill=DIM, font=f['btn'], anchor='mm')
d.text((264 * S, 14 * S), '\u2715', fill=DIM, font=f['regular'], anchor='mm')
d.rectangle([0, 28 * S, W, 29 * S], fill=BAR_BG)

y = 34 * S
d.text((12 * S, y + 6 * S), 'Current Session', fill=FG, font=f['regular'], anchor='lm')
d.text((268 * S, y + 6 * S), '10:16 (3min 00s)', fill=GREEN_INFO, font=f['small'], anchor='rm')
draw_bar(d, 12 * S, y + 14 * S, W - 24 * S, 16 * S, 74, bar_color(74, CLAUDE), '74%')
d.text((18 * S, y + 39 * S), 'reset 11:00 (44min)', fill=DIM, font=f['small'], anchor='lm')

y = 94 * S
d.text((12 * S, y + 6 * S), 'All models (7d)', fill=FG, font=f['regular'], anchor='lm')
draw_bar(d, 12 * S, y + 14 * S, W - 24 * S, 16 * S, 51, bar_color(51, BLUE), '51%')
d.text((18 * S, y + 39 * S), 'reset Sat 11:00 (2d 17h)', fill=DIM, font=f['small'], anchor='lm')

y = 154 * S
d.text((12 * S, y + 6 * S), 'Sonnet only (7d)', fill=FG, font=f['regular'], anchor='lm')
draw_bar(d, 12 * S, y + 14 * S, W - 24 * S, 16 * S, 6, bar_color(6, PURPLE), '6%')
d.text((18 * S, y + 39 * S), 'reset Mon 10:00 (3d 23h)', fill=DIM, font=f['small'], anchor='lm')

d.ellipse([6 * S, H - 14 * S, 18 * S, H - 2 * S], fill=DOT_W)
d.ellipse([W - 18 * S, H - 14 * S, W - 6 * S, H - 2 * S], fill=OCHRE)
img.save(os.path.join(OUT, 'widget-standard-expanded.png'))
print('2. widget-standard-expanded.png')

# --- 3. Essential mode ---
W, H = 260 * S, 46 * S
img = Image.new('RGBA', (W, H), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
rrect(d, [0, 0, W, H], 14 * S, BG)
draw_bar(d, 10 * S, 6 * S, W - 20 * S, 16 * S, 74, bar_color(74, CLAUDE),
         '74%  10:16 (3min 00s)')
d.text((14 * S, 30 * S), 'reset 11:00 (44min)', fill=DIM, font=f['small'], anchor='lm')
d.text((W - 78 * S, H - 10 * S), '\u2715', fill=DIM, font=f['regular'], anchor='mm')
d.text((W - 60 * S, H - 10 * S), '\u21bb', fill=DIM, font=f['btn'], anchor='mm')
d.text((W - 30 * S, H - 10 * S), '10:16', fill='white', font=f['small'], anchor='mm')
d.ellipse([6 * S, H - 14 * S, 18 * S, H - 2 * S], fill=DOT_W_D)
d.ellipse([W - 18 * S, H - 14 * S, W - 6 * S, H - 2 * S], fill=OCHRE)
img.save(os.path.join(OUT, 'widget-essential.png'))
print('3. widget-essential.png')

# --- 4. Settings dropdown ---
W, H = 220 * S, 220 * S
img = Image.new('RGBA', (W, H), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
rrect(d, [0, 0, W, H], 12 * S, MENU_BG)

items = [
    ('\u21bb', 'Refresh', False, f['btn']),
    ('\u21c5', 'Essential mode', False, f['btn']),
    None,
    ('\u2692', 'Renew session\u2026', False, f['regular']),
    ('\u2699', 'Open config.json', False, f['regular']),
    ('\U0001F310', 'Language: English', False, f['regular']),
    None,
    ('\u2715', 'Quit', False, f['regular']),
    None,
    (None, 'v2.6.0', True, f['small']),
]

y = 6 * S
for item in items:
    if item is None:
        d.rectangle([12 * S, y + 2 * S, W - 12 * S, y + 3 * S], fill=BAR_BG)
        y += 6 * S
        continue
    icon, text, is_version, font = item
    row_h = 26 * S if not is_version else 20 * S
    if icon:
        d.text((20 * S, y + row_h // 2), icon, fill=FG if not is_version else DIM,
               font=font, anchor='mm')
    text_x = 40 * S if icon else 20 * S
    color = DIM if is_version else FG
    d.text((text_x, y + row_h // 2), text, fill=color,
           font=f['regular'] if not is_version else f['small'], anchor='lm')
    y += row_h

img.save(os.path.join(OUT, 'menu-dropdown.png'))
print('4. menu-dropdown.png')

print('\nAll screenshots generated in', OUT)
