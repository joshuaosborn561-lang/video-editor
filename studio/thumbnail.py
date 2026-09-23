"""Thumbnail from a real face still. One to three words, one of them in color."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

WIDTH = 1280
HEIGHT = 720
YELLOW = (255, 225, 74, 255)
RED = (255, 59, 48, 255)
WHITE = (255, 255, 255, 255)
BLACK = (0, 0, 0, 255)

FONT_DIR = Path(__file__).resolve().parent / "fonts"
BOLD_ITALIC = str(FONT_DIR / "Inter-BoldItalic.ttf")
BOLD = str(FONT_DIR / "Inter-Bold.ttf")


def render(
    source: Path | None,
    lines: list[str],
    highlight: str,
    dest: Path,
    desaturate: bool = True,
    accent: str = "yellow",
) -> Path:
    if not lines or len(lines) > 3:
        raise ValueError("thumbnail needs 1 to 3 lines")
    words = " ".join(lines).split()
    if len(words) > 8:
        raise ValueError("thumbnail is a glance: keep it to a few words")

    canvas = _background(source, desaturate)
    color = RED if accent == "red" else YELLOW
    _draw_lines(canvas, [line.upper() for line in lines], highlight.upper(), color)
    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(dest, quality=92)
    return dest


def _background(source: Path | None, desaturate: bool) -> Image.Image:
    if source is None or not source.exists():
        image = Image.new("RGB", (WIDTH, HEIGHT), (18, 18, 18))
    else:
        image = Image.open(source).convert("RGB")
        image = ImageOps.fit(image, (WIDTH, HEIGHT), method=Image.Resampling.LANCZOS)
        if desaturate:
            image = ImageOps.grayscale(image).convert("RGB")
            image = ImageEnhance.Contrast(image).enhance(1.15)
    shade = Image.new("L", (1, HEIGHT), 0)
    for y in range(HEIGHT):
        # Darken toward the bottom so the type holds over a face.
        strength = 0 if y < int(HEIGHT * 0.42) else int(190 * ((y - HEIGHT * 0.42) / (HEIGHT * 0.58)))
        shade.putpixel((0, y), min(210, strength))
    shade = shade.resize((WIDTH, HEIGHT))
    black = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
    image = Image.composite(black, image, shade)
    return image.filter(ImageFilter.UnsharpMask(radius=1, percent=80, threshold=3))


def _font(size: int) -> ImageFont.FreeTypeFont:
    path = BOLD_ITALIC if Path(BOLD_ITALIC).exists() else BOLD
    return ImageFont.truetype(path, size)


def _text_width(font: ImageFont.FreeTypeFont, text: str) -> int:
    return int(font.getlength(text))


def _draw_lines(image: Image.Image, lines: list[str], highlight: str, accent: tuple) -> None:
    draw = ImageDraw.Draw(image)
    size = 132
    fonts = []
    while size > 48:
        font = _font(size)
        if all(_text_width(font, line) < WIDTH - 120 for line in lines):
            fonts = [font] * len(lines)
            break
        size -= 4
    if not fonts:
        font = _font(48)
        fonts = [font] * len(lines)
    gap = 8
    heights = [font.getbbox("Hg")[3] - font.getbbox("Hg")[1] for font in fonts]
    block = sum(heights) + gap * (len(lines) - 1)
    y = HEIGHT - 80 - block
    for line, font, height in zip(lines, fonts, heights):
        _draw_line(draw, line, highlight, font, accent, y)
        y += height + gap


def _draw_line(draw: ImageDraw.ImageDraw, line: str, highlight: str, font, accent, y: int) -> None:
    tokens = line.split()
    space = _text_width(font, " ")
    widths = [_text_width(font, token) for token in tokens]
    total = sum(widths) + space * (len(tokens) - 1)
    x = (WIDTH - total) / 2
    highlight_tokens = set(highlight.split())
    for token, width in zip(tokens, widths):
        fill = accent if token in highlight_tokens or token == highlight else WHITE
        for dx, dy in ((4, 4), (0, 0)):
            paint = BLACK if (dx, dy) == (4, 4) else fill
            draw.text((x + dx, y + dy), token, font=font, fill=paint)
        x += width + space
