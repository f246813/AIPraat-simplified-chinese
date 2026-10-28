"""Check native premultiplied toast pixels at ten display scales; export previews."""
from pathlib import Path
import json
import math
import sys

from PIL import Image, ImageChops, ImageDraw


def main():
    folder = Path(sys.argv[1])
    scales = [96, 120, 144, 168, 192, 216, 240, 288, 336, 384]
    rows, reports = [], []
    for dpi in scales:
        width = (120 * dpi + 48) // 96 + 2
        height = (44 * dpi + 48) // 96 + 2
        raw = (folder / f'toast-{dpi}.bgra').read_bytes()
        assert len(raw) == width * height * 4
        premultiplied = Image.frombytes('RGBA', (width, height), raw, 'raw', 'BGRA')
        alpha = premultiplied.getchannel('A')
        for box in [(0, 0, width, 1), (0, height-1, width, height),
                    (0, 0, 1, height), (width-1, 0, width, height)]:
            assert alpha.crop(box).getextrema() == (0, 0), (dpi, 'clipped fringe')
        radius = 12 * dpi / 96
        corner_size = math.ceil(radius) + 2
        corner = alpha.crop((0, 0, corner_size, corner_size))
        for other in [alpha.transpose(Image.Transpose.FLIP_LEFT_RIGHT),
                      alpha.transpose(Image.Transpose.FLIP_TOP_BOTTOM),
                      alpha.transpose(Image.Transpose.ROTATE_180)]:
            assert ImageChops.difference(corner, other.crop(
                (0, 0, corner_size, corner_size))).getbbox() is None, (dpi, 'unequal corners')
        levels = set(corner.get_flattened_data())
        assert len(levels - {0, 226}) >= 8, (dpi, 'missing antialiasing')
        for y in range(corner_size):
            values = [alpha.getpixel((x, y)) for x in range(corner_size)]
            assert values == sorted(values), (dpi, 'hole in rounded edge')
        # An independent circle test catches missing interior pixels and leaked exterior pixels.
        for y in range(1, corner_size):
            for x in range(1, corner_size):
                distance = math.hypot(max(0, radius-(x-.5)), max(0, radius-(y-.5)))
                value = alpha.getpixel((x, y))
                if distance < radius-.75:
                    assert value == 226, (dpi, 'missing interior coverage', x, y)
                if distance > radius+.75:
                    assert value == 0, (dpi, 'exterior halo', x, y)
        rgba = bytearray()
        for red, green, blue, opacity in premultiplied.get_flattened_data():
            assert max(red, green, blue) <= opacity, (dpi, 'invalid premultiplied alpha')
            rgba.extend((min(255, (red*255+opacity//2)//opacity) if opacity else 0,
                         min(255, (green*255+opacity//2)//opacity) if opacity else 0,
                         min(255, (blue*255+opacity//2)//opacity) if opacity else 0, opacity))
        image = Image.frombytes('RGBA', (width, height), bytes(rgba))
        image.save(folder/f'toast-{dpi}.png')
        row = Image.new('RGB', (1120, height+36), '#eeeeee')
        draw = ImageDraw.Draw(row)
        draw.text((12, 8), f'{dpi*100//96}% / {dpi} DPI / {width} x {height}', fill='#111111')
        for left, color in [(0, '#ffffff'), (560, '#111827')]:
            panel = Image.new('RGBA', (560, height+10), color)
            panel.alpha_composite(image, ((560-width)//2, 5))
            row.paste(panel.convert('RGB'), (left, 26))
        rows.append(row)
        reports.append(dict(scale=dpi*100//96, dpi=dpi, width=width, height=height,
                            corner_alpha_levels=len(levels), checks=['transparent_padding',
                            'identical_four_corners', 'smooth_alpha', 'no_holes',
                            'circle_coverage', 'premultiplied_alpha']))
    atlas = Image.new('RGB', (1120, sum(row.height for row in rows)))
    offset = 0
    for row in rows:
        atlas.paste(row, (0, offset))
        offset += row.height
    atlas.save(folder/'scaling-preview.png')
    (folder/'result.json').write_text(json.dumps(reports, indent=2), encoding='utf-8')
    print(f'Passed pixel coverage checks at {len(scales)} scales: 100%, 125%, 150%, 175%, 200%, 225%, 250%, 300%, 350%, 400%.')


if __name__ == '__main__':
    main()
