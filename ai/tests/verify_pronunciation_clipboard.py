"""Validate the native driver's actual Windows clipboard payloads.

Run native_pronunciation_clipboard.exe first, then this script with an output folder.
"""
import base64
import ctypes
from ctypes import wintypes
import html
import io
import json
from pathlib import Path
import re
import sys

from PIL import Image, ImageChops


def main():
    output = Path(sys.argv[1])
    mode = sys.argv[2] if len(sys.argv) > 2 else 'combined'
    assert mode in ('image', 'combined', 'question')
    output.mkdir(parents=True, exist_ok=True)
    user = ctypes.windll.user32
    kernel = ctypes.windll.kernel32
    user.OpenClipboard.argtypes = [wintypes.HWND]
    user.GetClipboardData.argtypes = [wintypes.UINT]
    user.GetClipboardData.restype = wintypes.HANDLE
    user.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
    kernel.GlobalSize.argtypes = [wintypes.HGLOBAL]
    kernel.GlobalSize.restype = ctypes.c_size_t
    kernel.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel.GlobalLock.restype = ctypes.c_void_p
    kernel.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    formats = {
        'text': 13, 'dib': 8, 'dib_v5': 17,
        'png': user.RegisterClipboardFormatW('PNG'),
        'html': user.RegisterClipboardFormatW('HTML Format'),
    }
    all_formats = dict(formats)
    if mode == 'image':
        formats = {name: number for name, number in formats.items() if name not in ('text', 'html')}
    elif mode == 'question':
        formats = {'text': 13}
    assert user.OpenClipboard(None), 'Clipboard is busy'
    data = {}
    try:
        order = []
        number = 0
        while number := user.EnumClipboardFormats(number):
            order.append(number)
        if mode == 'question':
            assert 13 in order and 8 not in order and all_formats['png'] not in order, order
        else:
            assert order[0] == formats['png'], order
            if mode == 'image':
                assert 13 not in order and all_formats['html'] not in order, order
            else:
                assert order.index(formats['png']) < order.index(13), order
                assert order.index(17) < order.index(13), order
        for name, number in formats.items():
            handle = user.GetClipboardData(number)
            assert handle, f'Missing format: {name}'
            pointer = kernel.GlobalLock(handle)
            assert pointer
            try:
                data[name] = ctypes.string_at(pointer, kernel.GlobalSize(handle))
            finally:
                kernel.GlobalUnlock(handle)
    finally:
        user.CloseClipboard()

    prompt = data['text'].decode('utf-16-le').rstrip('\0') if 'text' in data else None
    expected = '这是发音测试発音&<练习>，帮我看看是否标准'
    if mode != 'image':
        assert prompt == expected, repr(prompt)
    if mode == 'question':
        report = dict(mode=mode, prompt=prompt, format_order=order,
                      checks=['unicode_template', 'text_only_no_image'])
        (output/'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=True))
        return
    png = Image.open(io.BytesIO(data['png'])).convert('RGB')
    dib = Image.open(io.BytesIO(data['dib'])).convert('RGB')
    dib_v5 = Image.open(io.BytesIO(data['dib_v5'])).convert('RGBA')
    assert png.size == dib.size
    assert dib_v5.getchannel('A').getextrema() == (255, 255), 'Screenshot must be opaque'
    assert ImageChops.difference(png, dib).getbbox() is None
    assert ImageChops.difference(png, dib_v5.convert('RGB')).getbbox() is None
    assert png.width > 300 and png.height > 150
    colors = png.get_flattened_data()
    blue_pixels = sum(b > r + 50 and b > g for r, g, b in colors)
    assert blue_pixels > 100, 'Waveform and pitch should be rendered'
    gray_pixels = sum(abs(r - g) < 3 and abs(g - b) < 3 and 20 < r < 230 for r, g, b in png.get_flattened_data())
    assert gray_pixels > 1000, 'Spectrogram should be rendered'

    checks = ['png_and_dib_identical', 'waveform_and_spectrogram', 'dib_v5_opaque_and_identical']
    if mode == 'combined':
        raw_html = data['html'].rstrip(b'\0')
        offsets = {key: int(value) for key, value in re.findall(
            rb'(StartHTML|EndHTML|StartFragment|EndFragment):(\d+)', raw_html)}
        assert raw_html[offsets[b'StartHTML']:offsets[b'EndHTML']].startswith(b'<html>')
        fragment = raw_html[offsets[b'StartFragment']:offsets[b'EndFragment']].decode('utf-8')
        assert html.unescape(re.search(r'<p>(.*?)</p>', fragment).group(1)) == expected
        image_bytes = base64.b64decode(re.search(r'base64,([^\"]+)', fragment).group(1), validate=True)
        assert image_bytes == data['png'], 'HTML image must match PNG clipboard image'
        (output/'copied.html').write_bytes(raw_html[offsets[b'StartHTML']:offsets[b'EndHTML']])
        checks += ['unicode_template', 'html_byte_offsets', 'escaped_filename', 'html_embedded_png', 'image_before_text']
    else:
        checks += ['image_only_no_text_or_html']
    (output/'copied.png').write_bytes(data['png'])
    report = dict(mode=mode, prompt=prompt, width=png.width, height=png.height,
                  formats=list(formats), format_order=order, blue_pixels=blue_pixels, gray_pixels=gray_pixels,
                  checks=checks)
    (output/'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True))


if __name__ == '__main__':
    main()
