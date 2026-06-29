#!/usr/bin/env python3
"""Generate the social preview card (1200x630) for Tessera.

On-brand text card: black background, brass wordmark + rule, cream tagline.
Regenerate with: python scripts/gen_og_image.py
Output: web/static/og.png
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# Brand palette (from web/static/prototype.html :root)
BG = (5, 5, 7)
BG2 = (18, 17, 25)
BRASS = (218, 179, 106)
BRASS_DIM = (126, 101, 50)
TEXT = (236, 230, 218)
MUTED = (154, 147, 132)
SPLUNK = (236, 59, 155)

W, H = 1200, 630
MARGIN = 90

SERIF = "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"
SERIF_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"
SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def text_w(draw: ImageDraw.ImageDraw, s: str, f: ImageFont.FreeTypeFont) -> int:
    return draw.textbbox((0, 0), s, font=f)[2]


def main() -> None:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # Subtle top-left vignette panel for depth
    for i in range(H):
        t = i / H
        r = int(BG[0] + (BG2[0] - BG[0]) * t * 0.6)
        g = int(BG[1] + (BG2[1] - BG[1]) * t * 0.6)
        b = int(BG[2] + (BG2[2] - BG[2]) * t * 0.6)
        d.line([(0, i), (W, i)], fill=(r, g, b))

    # Top brass hairline
    d.line([(MARGIN, 70), (W - MARGIN, 70)], fill=BRASS_DIM, width=2)

    # Eyebrow / kicker
    f_kick = font(MONO, 24)
    kick = "COMPLIANCE EVIDENCE  ·  TRUST ENGINE"
    d.text((MARGIN, 100), kick, font=f_kick, fill=MUTED)

    # Wordmark
    f_brand = font(SERIF, 132)
    d.text((MARGIN, 150), "Tessera", font=f_brand, fill=BRASS)

    # Tagline (two lines, serif)
    f_tag = font(SERIF, 52)
    d.text((MARGIN, 320), "Turn Splunk data into", font=f_tag, fill=TEXT)
    d.text((MARGIN, 388), "audit-proof evidence.", font=f_tag, fill=TEXT)

    # Bottom rule + supporting line
    d.line([(MARGIN, 500), (W - MARGIN, 500)], fill=(42, 39, 51), width=2)
    f_sub = font(SANS, 26)
    sub = "Four competing AI vendors debate it · SHA-256 Merkle-chained · SOC 2 · ISO · NIST"
    d.text((MARGIN, 528), sub, font=f_sub, fill=MUTED)

    # Splunk accent dot on the kicker
    d.ellipse([MARGIN - 38, 108, MARGIN - 18, 128], fill=SPLUNK)

    out = Path(__file__).resolve().parent.parent / "web" / "static" / "og.png"
    img.save(out, "PNG", optimize=True)
    kb = out.stat().st_size / 1024
    print(f"wrote {out} ({kb:.0f} KB, {W}x{H})")


if __name__ == "__main__":
    main()
