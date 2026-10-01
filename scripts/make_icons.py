#!/usr/bin/env python3
"""Generate the FunForge launcher icons (mipmap-*/ic_launcher[_round].png).

Drawn with PIL rather than shipped as binaries so the icon is reproducible and
tweakable. Everything is drawn at 4x and downsampled, which is what keeps the
curves smooth at 48 px (PIL has no antialiased shape drawing).

Usage:  python3 scripts/make_icons.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "android" / "app" / "src" / "main" / "res"

# mipmap bucket -> launcher icon size in px
DENSITIES = {
    "mdpi": 48,
    "hdpi": 72,
    "xhdpi": 96,
    "xxhdpi": 144,
    "xxxhdpi": 192,
}

SS = 4  # supersampling factor

# Warm purple -> pink, matching the app's kid-facing palette.
GRAD_TOP = (124, 77, 255)
GRAD_BOTTOM = (236, 64, 122)


def gradient(size):
    """Vertical two-stop gradient as an RGB image."""
    img = Image.new("RGB", (1, size))
    px = img.load()
    for y in range(size):
        t = y / max(1, size - 1)
        px[0, y] = tuple(
            round(GRAD_TOP[i] + (GRAD_BOTTOM[i] - GRAD_TOP[i]) * t) for i in range(3)
        )
    return img.resize((size, size), Image.NEAREST)


def note_mask(size):
    """White beamed-eighth-note silhouette as an alpha mask."""
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    u = size / 100.0  # 1 unit == 1% of the icon

    stem_w = 7 * u
    left_x, right_x = 34 * u, 66 * u
    top_y, bottom_y = 22 * u, 68 * u

    # Two stems
    d.rectangle([left_x, top_y, left_x + stem_w, bottom_y], fill=255)
    d.rectangle([right_x, top_y - 4 * u, right_x + stem_w, bottom_y - 6 * u], fill=255)
    # Beam joining them (slightly sloped, so it reads as a note and not an "H")
    d.polygon(
        [
            (left_x, top_y),
            (right_x + stem_w, top_y - 4 * u),
            (right_x + stem_w, top_y + 9 * u),
            (left_x, top_y + 13 * u),
        ],
        fill=255,
    )
    # Note heads
    head_w, head_h = 24 * u, 18 * u
    d.ellipse(
        [left_x + stem_w - head_w, bottom_y - head_h / 2,
         left_x + stem_w, bottom_y + head_h / 2],
        fill=255,
    )
    d.ellipse(
        [right_x + stem_w - head_w, bottom_y - 6 * u - head_h / 2,
         right_x + stem_w, bottom_y - 6 * u + head_h / 2],
        fill=255,
    )
    return m


def build(size, round_icon):
    big = size * SS
    icon = gradient(big).convert("RGBA")

    # Clip to the launcher shape.
    shape = Image.new("L", (big, big), 0)
    sd = ImageDraw.Draw(shape)
    if round_icon:
        sd.ellipse([0, 0, big - 1, big - 1], fill=255)
    else:
        sd.rounded_rectangle([0, 0, big - 1, big - 1], radius=big * 0.22, fill=255)

    icon.putalpha(shape)

    # Paint the note on top, then re-apply the shape mask so nothing escapes it.
    note = Image.new("RGBA", (big, big), (255, 255, 255, 0))
    note.putalpha(note_mask(big))
    icon.alpha_composite(note)
    icon.putalpha(Image.composite(icon.getchannel("A"), Image.new("L", (big, big), 0), shape))

    return icon.resize((size, size), Image.LANCZOS)


def main():
    for bucket, size in DENSITIES.items():
        out = RES / f"mipmap-{bucket}"
        out.mkdir(parents=True, exist_ok=True)
        build(size, round_icon=False).save(out / "ic_launcher.png")
        build(size, round_icon=True).save(out / "ic_launcher_round.png")
        print(f"mipmap-{bucket}: {size}x{size}")


if __name__ == "__main__":
    main()
