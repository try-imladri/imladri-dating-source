"""Build the original, code-native dating share card (Pillow + fontTools)."""
from pathlib import Path
from html import escape

from PIL import Image, ImageDraw, ImageFont
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen

ROOT = Path(__file__).resolve().parents[2]
PUBLIC = ROOT / "frontend" / "public"
OUT = PUBLIC / "img" / "imladri-dating-social-v1"
DISPLAY = PUBLIC / "fonts" / "barlowcondensed" / "BarlowCondensed-Black.ttf"
BODY = PUBLIC / "fonts" / "plusjakartasans" / "PlusJakartaSans-Variable.ttf"
INK = "#0A0A0A"
image = Image.new("RGB", (1200, 630), "#FFFFFF")
draw = ImageDraw.Draw(image)
svg = [
    '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630" role="img" aria-labelledby="title desc">',
    '<title id="title">Imladri — Meet your type. Understand your fit.</title>',
    '<desc id="desc">Intentional dating. Your attraction. Your preferences. Your choice. Founding beta for adults 18 and older.</desc>',
    '<rect width="1200" height="630" fill="#FFFFFF"/>',
]


def text_line(value, x, baseline, size, path, color=INK):
    """Use the same repo font geometry for the PNG and portable SVG paths."""
    font = ImageFont.truetype(str(path), size)
    tt = TTFont(path)
    glyphs = tt.getGlyphSet()
    cmap = tt.getBestCmap()
    scale = size / tt["head"].unitsPerEm
    advance = 0
    svg.append(f'<g aria-label="{escape(value)}" fill="{color}">')
    for char in value:
        name = cmap.get(ord(char), ".notdef")
        draw.text((x + advance * scale, baseline), char, font=font, fill=color, anchor="ls")
        pen = SVGPathPen(glyphs)
        glyphs[name].draw(pen)
        commands = pen.getCommands()
        if commands:
            svg.append(f'<path transform="translate({x + advance * scale:.3f} {baseline}) scale({scale:.7f} {-scale:.7f})" d="{commands}"/>')
        advance += tt["hmtx"][name][0]
    svg.append("</g>")
    tt.close()


def rectangle(box, fill):
    x, y, right, bottom = box
    draw.rectangle(box, fill=fill)
    svg.append(f'<rect x="{x}" y="{y}" width="{right-x}" height="{bottom-y}" fill="{fill}"/>')


text_line("IMLADRI", 68, 88, 40, DISPLAY)
text_line("FOUNDING BETA · 18+", 857, 82, 18, BODY)
rectangle((68, 119, 1132, 121), INK)
text_line("MEET YOUR TYPE.", 64, 264, 112, DISPLAY)
text_line("UNDERSTAND YOUR FIT.", 64, 382, 112, DISPLAY)
text_line("Intentional dating. Your attraction.", 68, 443, 26, BODY)
text_line("Your preferences. Your choice.", 68, 482, 26, BODY)
rectangle((68, 528, 1132, 530), INK)
text_line("Join the founding community", 68, 581, 23, BODY)
text_line("IMLADRI.COM", 935, 581, 23, DISPLAY)
svg.append("</svg>")
OUT.with_suffix(".svg").write_text("\n".join(svg), encoding="utf-8")
image.save(OUT.with_suffix(".png"), optimize=True)
print(f"Built {OUT.name}.svg and .png (1200 x 630)")
