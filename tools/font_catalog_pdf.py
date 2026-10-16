#!/usr/bin/env python
"""
Generate a browsable PDF catalog of every Ink/Stitch embroidery font
(fonts/src/<font_id>), each showing SAMPLE_TEXT rendered at that font's own
minimum recommended letter height - i.e. exactly how small it can go and
still be expected to stitch cleanly. Fonts are listed smallest-minimum
first, each with a "[ ]" checkbox to mark favorites while browsing.

This does NOT run the full embroidery stitch simulation (which can take
minutes per font for satin fonts - infeasible for all 140). Instead it
reconstructs a fast, solid-fill approximation of each glyph's shape:
  - Fill/cross-stitch glyphs: use their shape directly.
  - Satin column glyphs: sample paired points along the two rails
    (Ink/Stitch's own plot_points_on_rails) and union the small quads
    between consecutive pairs into one polygon. Unioning many small,
    individually-valid quads - rather than closing the two rails into one
    big polygon directly - avoids self-intersection artifacts on satins
    whose rails cross (a real, intentional shading technique in some
    fonts, but one that breaks a naive "rail1 + reversed(rail2)" polygon).
  - "autosatinrun*" paths are Ink/Stitch's own travel/jump-routing guides
    (dashed connectors between satin pieces), not visible stitching, and
    are stripped out.

Images are embedded at true physical scale (1mm on the font's minimum size
= 1mm on the page), so relative sizes across fonts are directly comparable
and honestly represent how large "minimum readable" actually is per font.

Usage:
    tools/font_catalog_pdf.py [output.pdf]

Requirements:
    - Run with the project's venv (venv/bin/python) so `inkex`/lib import correctly.
    - Inkscape must be installed and on PATH.
    - pip install reportlab (already installed in this project's venv).
"""
import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent.resolve()
FONTS_DIR = SCRIPT_DIR / "fonts" / "src"

sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR / "tools"))

SAMPLE_TEXT = "Meg&Co."
MM_TO_PT = 72.0 / 25.4
# The image is rendered at this fixed height regardless of the font's true
# minimum size, so every sample stays readable and comparable on the page.
# The font's real minimum (font.size * font.min_scale) is still shown as an
# exact text label next to it - that number is the one that matters for
# deciding whether a font will actually stitch cleanly at the size you need;
# the image is for judging style/legibility, not for measuring with a ruler.
DISPLAY_HEIGHT_MM = 12.0


def render_solid_svg(font, text, letter_height_mm, svg_out: Path):
    """Render `text` with `font` at letter_height_mm (per reference capital
    letter, matching text_to_jef's calibration), reconstructed as solid
    fill shapes. Returns the rendered group's (width_mm, height_mm), or
    None if nothing rendered."""
    import inkex
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    from lib.elements import node_to_elements
    from text_to_jef import _measure_reference_letter_height

    reference_height = _measure_reference_letter_height(font)

    doc = inkex.load_svg(
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        b'width="1000mm" height="1000mm" viewBox="0 0 1000 1000"></svg>'
    )
    root = doc.getroot()
    group = inkex.Group()
    root.append(group)
    font.render_text(text, group, scale=100)
    if group.bounding_box() is None:
        return None

    scale = letter_height_mm / reference_height
    group.attrib["transform"] = f"scale({scale})"

    for path in list(group.iter("{http://www.w3.org/2000/svg}path")):
        if "autosatinrun" in (path.get("id") or ""):
            path.getparent().remove(path)
            continue

        handled = False
        for el in node_to_elements(path):
            cls = type(el).__name__
            if cls == "SatinColumn":
                try:
                    pairs = el.plot_points_on_rails(2.0, (0, 0), (0, 0), False)
                except Exception:
                    pairs = []
                if len(pairs) < 2:
                    continue
                quads = []
                for i in range(len(pairs) - 1):
                    a1, b1 = pairs[i]
                    a2, b2 = pairs[i + 1]
                    quad = Polygon([(a1.x, a1.y), (a2.x, a2.y), (b2.x, b2.y), (b1.x, b1.y)])
                    if not quad.is_valid:
                        quad = quad.buffer(0)
                    quads.append(quad)
                merged = unary_union(quads)
                polys = merged.geoms if merged.geom_type == "MultiPolygon" else [merged]
                d = ""
                for poly in polys:
                    if poly.is_empty:
                        continue
                    p = inkex.Path(list(poly.exterior.coords))
                    p.close()
                    d += str(p) + " "
                if not d:
                    continue
                path.set("d", d)
                path.set("style", "fill:#000000;stroke:none")
                handled = True
                break
            elif cls == "FillStitch":
                path.set("style", "fill:#000000;stroke:none")
                handled = True
                break
        if not handled:
            path.getparent().remove(path)

    bbox = group.bounding_box()
    if bbox is None or bbox.width <= 0 or bbox.height <= 0:
        return None

    with open(svg_out, "wb") as f:
        f.write(root.tostring())
    return bbox.width, bbox.height


def build_pdf(entries: list[tuple[str, str, float, Path, float, float]], output_pdf: Path):
    """entries: list of (font_id, name, min_height_mm, png_path, width_mm, height_mm)"""
    from xml.sax.saxutils import escape

    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, HRFlowable

    styles = getSampleStyleSheet()
    header_style = styles["Heading3"]
    body_style = styles["Normal"]

    doc = SimpleDocTemplate(str(output_pdf), pagesize=A4,
                             topMargin=15 * mm, bottomMargin=15 * mm,
                             leftMargin=15 * mm, rightMargin=15 * mm)

    story = [
        Paragraph(f'Cat&aacute;logo de tipograf&iacute;as - texto de muestra "{escape(SAMPLE_TEXT)}"', styles["Title"]),
        Paragraph("La muestra se ve a un tama&ntilde;o legible consistente para poder comparar estilos; el "
                  "n&uacute;mero “m&iacute;n” es el tama&ntilde;o real m&iacute;nimo de letra recomendado "
                  "para esa tipograf&iacute;a (de menor a mayor). Marc&aacute; el casillero de las que te "
                  "interesen.", body_style),
        Spacer(1, 8 * mm),
    ]

    for font_id, name, min_height_mm, png_path, width_mm, height_mm in entries:
        story.append(Paragraph(f"[&nbsp;&nbsp;] <b>{escape(name)}</b> &mdash; m&iacute;n {min_height_mm:.1f}mm "
                                f"<font size=8 color='#888888'>({escape(font_id)})</font>", header_style))
        story.append(Image(str(png_path), width=width_mm * mm, height=height_mm * mm))
        story.append(Spacer(1, 4 * mm))
        story.append(HRFlowable(width="100%", color="#dddddd"))
        story.append(Spacer(1, 4 * mm))

    doc.build(story)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("output", nargs="?", type=Path, default=SCRIPT_DIR / "font_catalog.pdf")
    parser.add_argument("--inkscape", default="inkscape")
    parser.add_argument("--dpi", type=int, default=300)
    args = parser.parse_args()

    import inkex  # noqa: F401  (import early so failures show up before the long loop)
    from lib.lettering.font import Font
    from text_to_jef import iter_fonts

    fonts = list(iter_fonts())
    print(f"Rendering {len(fonts)} fonts...")

    entries = []
    skipped = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for i, (font_id, name, min_height_mm) in enumerate(fonts):
            try:
                font = Font(str(FONTS_DIR / font_id), show_font_path_warning=False)
                svg_path = tmp_path / f"{font_id}.svg"
                result = render_solid_svg(font, SAMPLE_TEXT, DISPLAY_HEIGHT_MM, svg_path)
                if result is None:
                    skipped.append((font_id, "no renderable glyphs for sample text"))
                    continue
                width_mm, height_mm = result
                max_width_mm = 170.0
                if width_mm > max_width_mm:
                    shrink = max_width_mm / width_mm
                    width_mm *= shrink
                    height_mm *= shrink

                png_path = tmp_path / f"{font_id}.png"
                proc = subprocess.run(
                    [args.inkscape, str(svg_path), "--export-type=png", f"--export-filename={png_path}",
                     "--export-background=white", f"--export-dpi={args.dpi}", "--export-area-drawing"],
                    capture_output=True, text=True,
                )
                if proc.returncode != 0 or not png_path.exists():
                    skipped.append((font_id, "inkscape export failed"))
                    continue

                entries.append((font_id, name, min_height_mm, png_path, width_mm, height_mm))
            except Exception as e:
                skipped.append((font_id, str(e)))

            if (i + 1) % 20 == 0:
                print(f"  {i + 1}/{len(fonts)}...")

        print(f"Rendered {len(entries)} fonts, skipped {len(skipped)}")
        for font_id, reason in skipped:
            print(f"  skip {font_id}: {reason}", file=sys.stderr)

        build_pdf(entries, args.output)

    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
