"""Render the brand assets for the integration.

HACS looks for ``custom_components/overkiz_realtime/brand/icon.png``; when that
exists the integration does not need an entry in the home-assistant/brands
repository. The sizes follow the rules that repository documents: a square
256x256 icon plus a 512x512 hDPI variant, and a landscape logo whose shortest
side is between 128 and 256 pixels (256 and 512 for the hDPI variant).

The artwork is deliberately an original mark rather than a copy of the Overkiz
or Somfy logo -- those are third-party trademarks. It picks up the blue of the
Somfy/Overkiz world and adds the motif this integration is about: a shutter,
and a live pulse standing for the interpolated realtime position.

Run with any Python that has Pillow available::

    python scripts/generate_brand_assets.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BRAND_DIR = Path(__file__).resolve().parent.parent / (
    "custom_components/overkiz_realtime/brand"
)

# Supersampling factor; everything is drawn large and scaled down so the edges
# come out smooth.
SS = 4

GRADIENT_TOP = (11, 79, 176)
GRADIENT_BOTTOM = (23, 176, 200)
SLAT = (255, 255, 255)
PULSE = (255, 200, 87)
WORDMARK = (12, 42, 82)

FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
)


def _gradient(size: int) -> Image.Image:
    """Vertical gradient, ``size`` x ``size``."""
    grad = Image.new("RGB", (1, size))
    for y in range(size):
        blend = y / max(size - 1, 1)
        grad.putpixel(
            (0, y),
            tuple(
                round(top + (bottom - top) * blend)
                for top, bottom in zip(GRADIENT_TOP, GRADIENT_BOTTOM, strict=True)
            ),
        )
    return grad.resize((size, size), Image.Resampling.NEAREST)


def _icon_mark(size: int) -> Image.Image:
    """The square tile, drawn at ``size`` x ``size``."""
    s = size * SS
    unit = s / 256  # the design is laid out on a 256 unit grid

    tile = _gradient(s).convert("RGBA")
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, s - 1, s - 1), radius=56 * unit, fill=255
    )
    tile.putalpha(mask)

    draw = ImageDraw.Draw(tile)

    # Two guide rails rather than a closed frame: a closed rectangle full of
    # slats reads as a sheet of paper, rails read as a blind.
    for x0 in (44 * unit, 198 * unit):
        draw.rounded_rectangle(
            (x0, 36 * unit, x0 + 14 * unit, 220 * unit), radius=7 * unit, fill=SLAT
        )

    # Head rail, overhanging the guide rails the way a real one does.
    draw.rounded_rectangle(
        (36 * unit, 34 * unit, 220 * unit, 56 * unit), radius=10 * unit, fill=SLAT
    )

    # Three slats: the blind is a third of the way down, leaving the open part
    # of the window below for the pulse.
    top = 70 * unit
    slat_h = 15 * unit
    gap = 11 * unit
    for index in range(3):
        y = top + index * (slat_h + gap)
        draw.rounded_rectangle(
            (62 * unit, y, 194 * unit, y + slat_h), radius=5 * unit, fill=SLAT
        )

    # Live pulse in the open part: the position interpolated between two
    # gateway reports.
    baseline = 176 * unit
    amplitude = 28 * unit
    draw.line(
        [
            (64 * unit, baseline),
            (96 * unit, baseline),
            (114 * unit, baseline - amplitude),
            (134 * unit, baseline + amplitude * 0.58),
            (151 * unit, baseline),
            (192 * unit, baseline),
        ],
        fill=PULSE,
        width=round(11 * unit),
        joint="curve",
    )

    return tile.resize((size, size), Image.Resampling.LANCZOS)


def _font(px: int) -> ImageFont.FreeTypeFont:
    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, px)
    raise SystemExit(f"none of the expected fonts exist: {FONT_CANDIDATES}")


def _logo(height: int) -> Image.Image:
    """Landscape lock-up: the mark plus the wordmark, on transparency."""
    s = height * SS
    mark = _icon_mark(s)

    font = _font(round(s * 0.27))

    pad = round(s * 0.06)
    gap = round(s * 0.09)

    top_text, bottom_text = "Overkiz", "Realtime"
    scratch = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    width_text = max(
        scratch.textlength(top_text, font=font),
        scratch.textlength(bottom_text, font=font),
    )

    canvas = Image.new(
        "RGBA", (round(s + gap + width_text + pad), s), (255, 255, 255, 0)
    )
    canvas.alpha_composite(mark, (0, 0))

    draw = ImageDraw.Draw(canvas)
    draw.text((s + gap, s * 0.31), top_text, font=font, fill=WORDMARK, anchor="lm")
    draw.text((s + gap, s * 0.69), bottom_text, font=font, fill=WORDMARK, anchor="lm")

    target_w = round(canvas.width / SS)
    return canvas.resize((target_w, height), Image.Resampling.LANCZOS)


def main() -> None:
    """Write every brand asset."""
    BRAND_DIR.mkdir(parents=True, exist_ok=True)

    assets = {
        "icon.png": _icon_mark(256),
        "icon@2x.png": _icon_mark(512),
        "logo.png": _logo(200),
        "logo@2x.png": _logo(400),
    }

    for name, image in assets.items():
        path = BRAND_DIR / name
        image.save(path, "PNG", optimize=True)
        print(f"{path.relative_to(BRAND_DIR.parents[2])}  {image.width}x{image.height}")


if __name__ == "__main__":
    main()
