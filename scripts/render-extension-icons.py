"""Render the Chrome extension icons from the flower drawing.

Reads `assets-source/extension-icon.svg` (a 128-unit tile with the flower on
it) and writes extension/icon16.png, icon24.png, icon32.png, icon48.png and
icon128.png; a copy of the 128 px icon for the store listing,
installer/chrome-store-assets/store-icon-128x128.png; and
assets-source/extension-icon-512.png, the whole tile edge to edge, which the
promotional images are drawn from.

Every size is the same drawing, reduced: same outline, same tilt. Nothing is
redrawn or thickened. Three things keep the small sizes legible:

  - At 16 px the flower takes 72% of the tile instead of the 60% it takes in
    the master, or the toolbar shows a ten-pixel smudge.
  - At 16, 24 and 32 px the flower is shifted by a fraction of a pixel to
    wherever the fewest pixels come out half-covered, so the petal edges fall
    on pixel edges as far as the drawing allows.
  - Edge pixels mix the two colours as light does, in linear light, not in
    sRGB numbers: mixing the numbers darkens every edge pixel of a light shape
    on a darker ground, and at 16 px the thin petals would fade into the
    orange.

The 128 px icon is a 96 px tile inside 16 px of transparent margin: the Chrome
Web Store asks for 96x96 artwork in a 128x128 image.

Colours: #D97757, Claude's orange, and #FCF2EE, the warm white the extension
has always drawn on it.

Every size is drawn 16 times larger and averaged down, which gives each pixel
exactly the share of it the drawing covers.

Run: python scripts/render-extension-icons.py
"""
import os
import re

import numpy as np
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SVG_SRC = os.path.join(ROOT, 'assets-source', 'extension-icon.svg')
EXT_DIR = os.path.join(ROOT, 'extension')
STORE_DIR = os.path.join(ROOT, 'installer', 'chrome-store-assets')

SS = 16                       # supersampling factor
CORNER = 28 / 128             # tile corner radius, as a share of its side
# How much larger than in the master the flower is drawn, per size.
SCALE = {16: 1.20}
# Sizes whose placement is chosen on the pixel grid.
GRID_FIT = (16, 24, 32)


def hex_rgb(h):
    return np.array([int(h[i:i + 2], 16) for i in (1, 3, 5)], float)


def read_master():
    """The flower outline (on-curve points of the path) and the two colours."""
    svg = open(SVG_SRC, encoding='utf-8').read()
    tile_col, flower_col = re.findall(r'fill="(#[0-9A-Fa-f]{6})"', svg)
    d = re.search(r' d="([^"]+)"', svg).group(1)
    nums = [float(v) for v in re.findall(r'-?\d+(?:\.\d+)?', d)]
    # "M x y" and then cubic segments of six numbers; the vectoriser placed
    # the on-curve points about a third of a unit apart, so they trace the
    # outline to well under a hundredth of a pixel at every size made here.
    poly = [(nums[0], nums[1])] + [(nums[i + 4], nums[i + 5])
                                   for i in range(2, len(nums) - 5, 6)]
    return poly, hex_rgb(tile_col), hex_rgb(flower_col)


def coverage(mask, size):
    """Supersampled 0/255 mask -> share of each final pixel it covers."""
    return (np.asarray(mask, float) / 255).reshape(size, SS, size, SS).mean(axis=(1, 3))


def tile_mask(size, tile):
    S = size * SS
    off = (size - tile) / 2 * SS
    m = Image.new('L', (S, S), 0)
    ImageDraw.Draw(m).rounded_rectangle(
        (off, off, off + tile * SS - 1, off + tile * SS - 1),
        radius=tile * CORNER * SS, fill=255)
    return m


def to_linear(c):
    c = c / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def to_srgb(v):
    s = np.where(v <= 0.0031308, v * 12.92, 1.055 * np.power(v, 1 / 2.4) - 0.055)
    return s * 255.0


def compose(tile_cov, flower_cov, tile_rgb, flower_rgb):
    f = flower_cov[..., None]
    rgb = to_srgb(to_linear(tile_rgb) * (1 - f) + to_linear(flower_rgb) * f)
    rgba = np.dstack([rgb, tile_cov * 255]).round().clip(0, 255).astype(np.uint8)
    return Image.fromarray(rgba, 'RGBA')


def flower_coverage(size, poly, tile, scale, dx, dy):
    S = size * SS
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    mx, my = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    k = tile / 128 * scale * SS
    m = Image.new('L', (S, S), 0)
    ImageDraw.Draw(m).polygon(
        [((x - mx) * k + S / 2 + dx * SS, (y - my) * k + S / 2 + dy * SS) for x, y in poly],
        fill=255)
    return coverage(m, size)


def grid_fit(size, poly, tile, scale, steps=8):
    """The flower shifted by less than a pixel to where its edges come out
    sharpest: the placement with the least half-covered area."""
    best = None
    for i in range(steps):
        for j in range(steps):
            c = flower_coverage(size, poly, tile, scale, i / steps - 0.5, j / steps - 0.5)
            blur = float((c * (1 - c)).sum())
            if best is None or blur < best[0]:
                best = (blur, c)
    return best[1]


def render(size, poly, tile_rgb, flower_rgb, tile=None):
    tile = tile or size
    scale = SCALE.get(size, 1.0)
    if size in GRID_FIT:
        cov = grid_fit(size, poly, tile, scale)
    else:
        cov = flower_coverage(size, poly, tile, scale, 0, 0)
    return compose(coverage(tile_mask(size, tile), size), cov, tile_rgb, flower_rgb)


def main():
    poly, tile_rgb, flower_rgb = read_master()
    outputs = []
    for size in (16, 24, 32, 48, 128):
        im = render(size, poly, tile_rgb, flower_rgb, tile=96 if size == 128 else None)
        outputs.append((os.path.join(EXT_DIR, f'icon{size}.png'), im))
    # The store listing asks for the same 128 px icon; a copy sits with the
    # other uploads, under a name that says what it is.
    outputs.append((os.path.join(STORE_DIR, 'store-icon-128x128.png'), outputs[-1][1]))
    # The promotional tiles show the tile large and edge to edge, which the
    # store icon, inside its margin, is not. Not an upload, so it stays out of
    # the store folder.
    outputs.append((os.path.join(ROOT, 'assets-source', 'extension-icon-512.png'),
                    render(512, poly, tile_rgb, flower_rgb)))
    for path, im in outputs:
        im.save(path, optimize=True)
        print(f'wrote {os.path.relpath(path, ROOT)}')


if __name__ == '__main__':
    main()
