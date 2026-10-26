#!/usr/bin/env python3
"""Draw GitHub's social preview card: docs/assets/social-preview.png.

1280 x 640, under 1 MB, as GitHub asks. The icon is the integration's own brand
image (custom_components/bright_meter/brand/dark_icon@2x.png), the name and the
tagline are the README's. Upload the result in the repository's Settings,
Social preview; GitHub has no API for it.

Needs Pillow, which Home Assistant's test environment already has:

  .venv/bin/python tools/gen_social_preview.py

The type is Helvetica Neue on macOS and DejaVu Sans elsewhere, so the PNG is
reproducible on one machine, not byte-identical across platforms; that is why
`make check` does not compare it.
"""
from __future__ import annotations

import pathlib
import re

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = pathlib.Path(__file__).resolve().parents[1]
ICON = ROOT / "custom_components/bright_meter/brand/dark_icon@2x.png"
OUT = ROOT / "docs/assets/social-preview.png"

W, H = 1280, 640
S = 2  # drawn at twice the size, then reduced, for smooth edges

NAME = ("HalfPast ", "Meter")
CHIPS = ("Half-hourly", "Energy dashboard", "Any UK supplier", "HACS")

BG_TOP = (24, 42, 74)
BG_BOTTOM = (15, 27, 51)
GLOW = (252, 182, 7)
CREAM = (246, 241, 231)
GREY = (214, 216, 222)
AMBER = (252, 182, 7)
AMBER_DEEP = (255, 138, 31)
BLUE = (56, 129, 253)
CHIP_LINE = (74, 88, 116)
NAVY_TEXT = (20, 32, 56)

FONTS = {
    "bold": [("/System/Library/Fonts/HelveticaNeue.ttc", 1),
             ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 0)],
    "medium": [("/System/Library/Fonts/HelveticaNeue.ttc", 10),
               ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0)],
}


def font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    for path, index in FONTS[kind]:
        if pathlib.Path(path).exists():
            return ImageFont.truetype(path, size * S, index=index)
    raise SystemExit(f"no {kind} font found; tried {FONTS[kind]}")


def tagline() -> str:
    """The README's bold line under the H1."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    match = re.search(r"^# .*?\n\n\*\*(.+?)\*\*", readme, re.M | re.S)
    if not match:
        raise SystemExit("README.md has no bold tagline under its H1")
    return match.group(1)


def gradient(size: tuple[int, int], a, b, *, horizontal: bool) -> Image.Image:
    w, h = size
    steps = w if horizontal else h
    strip = Image.new("RGB", (steps, 1) if horizontal else (1, steps))
    for i in range(steps):
        t = i / max(steps - 1, 1)
        colour = tuple(round(a[c] + (b[c] - a[c]) * t) for c in range(3))
        strip.putpixel((i, 0) if horizontal else (0, i), colour)
    return strip.resize((w, h))


def wrap(text: str, face: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if face.getlength(trial) <= width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    return [*lines, line]


def draw() -> Image.Image:
    w, h = W * S, H * S
    card = gradient((w, h), BG_TOP, BG_BOTTOM, horizontal=False)

    # A soft amber glow behind the icon, as on the other house cards.
    icon_size, icon_x = 300 * S, 98 * S
    icon_y = (h - icon_size) // 2
    glow = Image.new("L", (w, h), 0)
    cx, cy, r = icon_x + icon_size // 2, h // 2, 330 * S
    ImageDraw.Draw(glow).ellipse((cx - r, cy - r, cx + r, cy + r), fill=32)
    glow = glow.filter(ImageFilter.GaussianBlur(150 * S))
    card = Image.composite(Image.new("RGB", (w, h), GLOW), card, glow)

    # The brand icon, with a drop shadow.
    icon = Image.open(ICON).convert("RGBA")
    icon = icon.resize((icon_size, icon_size), Image.LANCZOS)
    shadow = Image.new("L", (w, h), 0)
    shadow.paste(icon.getchannel("A"), (icon_x, icon_y + 18 * S))
    shadow = shadow.filter(ImageFilter.GaussianBlur(28 * S)).point(lambda v: v * 0.55)
    card = Image.composite(Image.new("RGB", (w, h), (5, 10, 22)), card, shadow)
    card.paste(icon, (icon_x, icon_y), icon)

    pen = ImageDraw.Draw(card)
    left, right = 480 * S, (W - 48) * S
    width = right - left

    title = font("bold", 96)
    tag = font("medium", 31)
    chip = font("bold", 21)
    lines = wrap(tagline(), tag, width)

    title_h, line_h, chip_h = 100 * S, 42 * S, 50 * S
    block = title_h + 30 * S + line_h * len(lines) + 38 * S + chip_h
    y = (h - block) // 2

    # The name: "HalfPast" in cream, "Meter" in the electricity amber.
    first, second = NAME
    pen.text((left, y), first, font=title, fill=CREAM)
    x2 = left + round(title.getlength(first))
    box = pen.textbbox((x2, y), second, font=title)
    if box[2] > right:
        raise SystemExit("the name is wider than the card")
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).text((x2, y), second, font=title, fill=255)
    span = (box[2] - box[0], box[3] - box[1])
    fill = gradient(span, AMBER, AMBER_DEEP, horizontal=False)
    layer = Image.new("RGB", (w, h))
    layer.paste(fill, (box[0], box[1]))
    card = Image.composite(layer, card, mask)
    pen = ImageDraw.Draw(card)
    y += title_h + 30 * S

    for line in lines:
        pen.text((left, y), line, font=tag, fill=GREY)
        y += line_h
    y += 38 * S

    # The chips: the first filled in amber, the rest outlined.
    x = left
    for i, label in enumerate(CHIPS):
        cw = round(chip.getlength(label)) + 44 * S
        rect = (x, y, x + cw, y + chip_h)
        if i == 0:
            pen.rounded_rectangle(rect, radius=chip_h // 2, fill=AMBER)
            colour = NAVY_TEXT
        else:
            pen.rounded_rectangle(
                rect, radius=chip_h // 2, outline=CHIP_LINE, width=2 * S
            )
            colour = CREAM
        middle = (x + cw // 2, y + chip_h // 2)
        pen.text(middle, label, font=chip, fill=colour, anchor="mm")
        x += cw + 12 * S
    if x - 12 * S > right:
        raise SystemExit("the chips are wider than the card")

    # Electricity amber into gas blue along the bottom edge.
    stripe = 8 * S
    card.paste(gradient((w, stripe), AMBER, BLUE, horizontal=True), (0, h - stripe))

    return card.resize((W, H), Image.LANCZOS)


def main() -> None:
    image = draw()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    image.save(OUT, optimize=True)
    size = OUT.stat().st_size
    if size >= 1_000_000:
        raise SystemExit(f"{OUT.name} is {size} bytes; GitHub wants under 1 MB")
    print(f"wrote {OUT.relative_to(ROOT)} ({W}x{H}, {size // 1024} KB)")


if __name__ == "__main__":
    main()
