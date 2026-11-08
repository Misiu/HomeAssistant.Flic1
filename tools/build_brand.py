"""Build custom_components/flic1/brand/*.png (HA 2026.3+ local brand images) from brand-src/.

icon.png comes from appicon.png (256x256, used as is), logo.png from flicbutton.png (black line art): made
transparent, trimmed, with a white copy for dark themes. The @2x files are smooth 2x upscales rendered by
headless Chrome/Edge (the sources have no higher resolution). Stdlib only. Run: python tools/build_brand.py
"""

import struct
import subprocess
import tempfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "brand-src"
OUT = ROOT / "custom_components" / "flic1" / "brand"
BROWSERS = (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")


def screenshot(img: Path, w: int, h: int, dest: Path) -> None:
    """Draw `img` stretched to w x h on a transparent page (the browser does the smooth scaling)."""
    browser = next(b for b in BROWSERS if Path(b).exists())
    with tempfile.TemporaryDirectory() as td:
        page = Path(td) / "p.html"
        page.write_text('<!doctype html><meta charset="utf-8"><style>html,body{margin:0;background:transparent}'
                        f'img{{display:block;width:{w}px;height:{h}px}}</style><img src="{img.as_uri()}">',
                        encoding="utf-8")
        subprocess.run([browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--user-data-dir={td}/ud",
                        "--force-device-scale-factor=1", "--default-background-color=00000000",
                        f"--window-size={w},{h}", f"--screenshot={dest}", page.as_uri()],
                       check=True, capture_output=True, timeout=90)


def read_png(path: Path) -> tuple[int, int, list[bytearray]]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat = 8, b""
    while pos < len(data):
        n, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        if kind == b"IHDR":
            w, h, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", body)
            assert depth == 8 and ctype == 6 and interlace == 0, f"{path.name}: expected 8-bit RGBA, no interlace"
        elif kind == b"IDAT":
            idat += body
        pos += 12 + n
    raw, stride, rows = zlib.decompress(idat), w * 4, []
    prev = bytearray(stride)
    for y in range(h):
        f, line = raw[y * (stride + 1)], bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - 4] if i >= 4 else 0
            b, c = prev[i], (prev[i - 4] if i >= 4 else 0)
            if f == 1:
                line[i] = (line[i] + a) & 255
            elif f == 2:
                line[i] = (line[i] + b) & 255
            elif f == 3:
                line[i] = (line[i] + (a + b) // 2) & 255
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append(line)
        prev = line
    return w, h, rows


def write_png(path: Path, w: int, h: int, rows: list[bytearray]) -> None:
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    raw = b"".join(b"\x00" + bytes(r) for r in rows)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def bbox(w: int, h: int, rows: list[bytearray], thresh: int = 8) -> tuple[int, int, int, int]:
    xs = [x for r in rows for x in range(w) if r[x * 4 + 3] > thresh]
    ys = [y for y, r in enumerate(rows) if any(r[x * 4 + 3] > thresh for x in range(w))]
    return min(xs), min(ys), max(xs) + 1, max(ys) + 1


def line_art(rows: list[bytearray], w: int, color: int) -> list[bytearray]:
    """Black-on-white (or already transparent) art -> solid `color` with the stroke darkness as alpha."""
    out = []
    for r in rows:
        o = bytearray(len(r))
        for x in range(w):
            R, G, B, A = r[x * 4:x * 4 + 4]
            darkness = 255 - (R + G + B) // 3
            o[x * 4:x * 4 + 4] = bytes((color, color, color, darkness * A // 255))
        out.append(o)
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        # icon: the supplied 256x256 RGBA is used as is, @2x is a smooth upscale
        w, h, rows = read_png(SRC / "appicon.png")
        assert (w, h) == (256, 256)
        write_png(OUT / "icon.png", w, h, rows)
        screenshot(OUT / "icon.png", 512, 512, OUT / "icon@2x.png")

        # logo: decode through the browser (palette PNG), make it line art on transparent, trim
        screenshot(SRC / "flicbutton.png", 512, 512, tmp / "logo_raw.png")
        w, h, rows = read_png(tmp / "logo_raw.png")
        for name, color in (("logo", 0), ("dark_logo", 255)):
            art = line_art(rows, w, color)
            x0, y0, x1, y1 = bbox(w, h, art)
            cropped = [r[x0 * 4:x1 * 4] for r in art[y0:y1]]
            cw, ch = x1 - x0, y1 - y0
            write_png(OUT / f"{name}.png", cw, ch, cropped)
            screenshot(OUT / f"{name}.png", cw * 2, ch * 2, OUT / f"{name}@2x.png")

    for png in sorted(OUT.glob("*.png")):
        w, h, rows = read_png(png)
        x0, y0, x1, y1 = bbox(w, h, rows)
        print(f"{png.name:16} {w}x{h}  content {x1 - x0}x{y1 - y0}  corner alpha {rows[0][3]}")


if __name__ == "__main__":
    main()
