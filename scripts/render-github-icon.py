"""One-shot script: render the GitHub mark SVG into PNG assets.

Reads `assets-source/github.svg`, calls Edge headless to rasterize it at
multiple sizes, saves PNGs into `src/assets/`. Each variant is recolored
to the FG color used by the menu so it visually matches the other icons.

Run: python scripts/render-github-icon.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SVG_SRC = os.path.join(ROOT, 'assets-source', 'github.svg')
DST = os.path.join(ROOT, 'src', 'assets')

# Menu uses FG = '#fafafa' (light grey on dark menu). Keeping the icon a
# touch dimmer so it doesn't overpower the others.
FG_HEX = '#dadada'

EDGE_CANDIDATES = [
    r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
    r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
]

# Final raster sizes (square px). The menu uses 16 to match emoji icons
# (~14-16 px tall at FT_EMOJI 10 pt). Higher sizes are kept for HiDPI use.
SIZES = (16, 24, 32, 48)
# Render at SUPERSAMPLE x size, then PIL LANCZOS down to size. Smoother
# than Edge's native raster at small sizes (16 px especially).
SUPERSAMPLE = 4


def find_edge():
    for p in EDGE_CANDIDATES:
        if os.path.isfile(p):
            return p
    raise SystemExit('Microsoft Edge not found')


def hex_to_rgb(h):
    h = h.lstrip('#')
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def render_one(edge, size):
    """Render the SVG at `size` px with supersample + LANCZOS downscale.

    Edge's native raster at 16 px loses too much shape detail. Rendering
    at 4x then resizing with LANCZOS gives smooth edges and preserves
    the Octocat outline.
    """
    big = size * SUPERSAMPLE
    with open(SVG_SRC, 'r', encoding='utf-8') as f:
        svg_inline = f.read()

    # Black SVG -> recolor in PIL afterward; easier than CSS overrides.
    html = (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<style>html,body{margin:0;padding:0;background:transparent;}'
        f'svg{{width:{big}px;height:{big}px;display:block;}}'
        '</style></head><body>' + svg_inline + '</body></html>'
    )

    with tempfile.TemporaryDirectory() as tmp:
        html_path = os.path.join(tmp, 'wrap.html')
        png_path = os.path.join(tmp, 'out.png')
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(html)

        subprocess.run(
            [edge, '--headless=new', '--disable-gpu',
             f'--screenshot={png_path}',
             f'--window-size={big},{big}',
             '--default-background-color=00000000',
             '--hide-scrollbars',
             'file:///' + html_path.replace('\\', '/')],
            check=True, capture_output=True, timeout=30)

        img = Image.open(png_path).convert('RGBA')

    # Downscale with high-quality filter.
    if img.size != (size, size):
        img = img.resize((size, size), Image.LANCZOS)

    # Recolor: every visible pixel becomes FG; keep alpha.
    r, g, b = hex_to_rgb(FG_HEX)
    pixels = img.load()
    for y in range(img.height):
        for x in range(img.width):
            R, G, B, A = pixels[x, y]
            if A > 0:
                pixels[x, y] = (r, g, b, A)
    return img


def main():
    if not os.path.isfile(SVG_SRC):
        raise SystemExit(f'Source SVG missing: {SVG_SRC}')
    edge = find_edge()
    os.makedirs(DST, exist_ok=True)

    for size in SIZES:
        img = render_one(edge, size)
        out = os.path.join(DST, f'icon-github-{size}.png')
        img.save(out, 'PNG')
        print(f'wrote {out}')


if __name__ == '__main__':
    main()
