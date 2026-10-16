#!/usr/bin/env python
"""
Render text using one of Ink/Stitch's built-in digitized embroidery fonts
(fonts/src/<font_id>) and export it as a .jef file.

This uses real pre-digitized letterforms (proper satin columns, stitch
density already tuned per font) instead of tracing a raster image of text,
which gives much cleaner results, especially at small sizes.

Usage:
    tools/text_to_jef.py "Meg&Co" mam_script output.jef --height-mm 15
    tools/text_to_jef.py --list-fonts
    tools/text_to_jef.py --font-info mam_script

Requirements:
    - Run with the project's venv (venv/bin/python) so `inkex`/lib import correctly.
"""
import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent.resolve()
FONTS_DIR = SCRIPT_DIR / "fonts" / "src"

# Below this, a satin column's narrowest point risks piling many stitches on
# top of each other (see render_text_to_svg's narrowest_pinch_mm) rather than
# a clean machine failure threshold from any spec - chosen as a conservative
# reading of what a home embroidery machine's needle throw can reliably
# resolve.
PINCH_WARNING_MM = 0.3

sys.path.insert(0, str(SCRIPT_DIR))

import image_to_jef as pipeline  # noqa: E402  (reuse svg_to_jef/set_svg_size_mm)


def _blank_svg_doc(size_mm: float = 1000):
    import inkex

    svg_bytes = (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
        f'xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'width="{size_mm}mm" height="{size_mm}mm" viewBox="0 0 {size_mm} {size_mm}">'
        f'</svg>'
    ).encode()
    return inkex.load_svg(svg_bytes)


def _parse_path_subpaths(path_node):
    """Split a path's own `d` attribute into subpaths (point lists), parsed
    directly rather than via Ink/Stitch's SatinColumn element: instantiating
    SatinColumn repeatedly on freshly-built synthetic nodes (as we do here,
    one per glyph) triggers cross-contamination in its rail/rung caching -
    .rails ends up mixing in geometry from an unrelated sibling path. Parsing
    the raw `d` ourselves sidesteps that entirely.
    """
    import inkex

    path = inkex.Path(path_node.get("d")).to_absolute()
    subpaths = []
    current = []
    for seg in path:
        if seg.letter == "M":
            if current:
                subpaths.append(current)
            current = [seg.args]
        elif seg.letter in ("L", "C", "Q", "T", "S") and len(seg.args) >= 2:
            current.append(seg.args[-2:])
    if current:
        subpaths.append(current)
    return subpaths


def _satin_center_line(path_node, n_samples: int = 40):
    """Average a satin column's two rails (see _parse_path_subpaths) into a
    centerline, by sampling both at the same normalized distances along their
    length and averaging each pair. Returns a list of (x, y) points, or None
    if the path doesn't look like a two-rail satin."""
    from shapely.geometry import LineString

    subpaths = _parse_path_subpaths(path_node)
    if len(subpaths) < 2:
        return None
    rail1, rail2 = LineString(subpaths[0]), LineString(subpaths[1])
    if rail1.length <= 0 or rail2.length <= 0:
        return None
    center = []
    for i in range(n_samples + 1):
        t = i / n_samples
        p1 = rail1.interpolate(t, normalized=True)
        p2 = rail2.interpolate(t, normalized=True)
        center.append(((p1.x + p2.x) / 2, (p1.y + p2.y) / 2))
    return center


def _min_rail_width(path_node, n_samples: int = 40):
    """Narrowest gap between a satin column's two rails, sampled at evenly
    spaced points along their length (same sampling as _satin_center_line).
    Returns None if the path doesn't look like a two-rail satin.

    Real digitized letters can have a corner where two strokes join pinch
    down to a near-zero-width point even though the rest of the stroke is a
    comfortable width - harmless at the font's intended size, but pull
    compensation (which pushes the rails apart to counteract fabric pull)
    can push a pinch like that past itself once the whole letter is shrunk
    down for small text, piling up stitches at that one spot instead of
    spacing them along the stroke. Callers use this to cap compensation
    relative to the narrowest point actually present, not just the letter's
    overall size.
    """
    from shapely.geometry import LineString

    subpaths = _parse_path_subpaths(path_node)
    if len(subpaths) < 2:
        return None
    rail1, rail2 = LineString(subpaths[0]), LineString(subpaths[1])
    if rail1.length <= 0 or rail2.length <= 0:
        return None
    min_width = None
    for i in range(n_samples + 1):
        t = i / n_samples
        p1 = rail1.interpolate(t, normalized=True)
        p2 = rail2.interpolate(t, normalized=True)
        width = p1.distance(p2)
        if min_width is None or width < min_width:
            min_width = width
    return min_width


_REFERENCE_LETTER_CANDIDATES = ["M", "A", "N", "0", "a"]


def _measure_reference_letter_height(font) -> float:
    """Render a single reference capital letter at scale=100 and measure its
    height. This is the calibration basis: the recommended min/max size range
    (font.min_scale/max_scale) applies to a single letter's height, not to a
    whole word or phrase - a word is just its letters placed side by side at
    that same per-letter scale, so measuring the whole phrase's bounding box
    (which mixes different letters' ascenders/descenders) would give a
    misleading range.
    """
    import inkex

    for candidate in _REFERENCE_LETTER_CANDIDATES:
        doc = _blank_svg_doc()
        group = inkex.Group()
        doc.getroot().append(group)
        try:
            font.render_text(candidate, group, scale=100)
        except Exception:
            continue
        bbox = group.bounding_box()
        height = bbox.height if bbox is not None else 0
        if height > 0:
            return height
    raise ValueError("Could not measure a reference letter for this font")


def render_text_to_svg(font_id: str, text: str, letter_height_mm: float, svg_out: Path,
                        color: str | None = None,
                        pull_compensation_mm: float = 0.15, underlay: bool = True,
                        stitch_style: str = "satin", bean_stitch_repeats: int = 2) -> tuple[float, float, bool]:
    """Render text with the given font, scaled so a single reference capital
    letter is letter_height_mm tall (the word's overall size follows from
    that same per-letter scale).

    stitch_style controls how each letter's satin columns get stitched:
      - "satin" (default): the font's own satin columns, with
        pull_compensation_mm and underlay applied (both satin-specific - fonts
        that use fill instead, like cross-stitch styles, are left alone).
        Satin stitching pulls the fabric inward as it sews, so letters end up
        narrower than digitized; pull compensation widens the satin slightly
        to counteract that. A light center-walk underlay (a straight stitch
        down the middle before the satin top layer) stabilizes the fabric so
        the stitches on top have something to grip - both matter more,
        proportionally, the smaller the letters are. contour_underlay is
        skipped: it walks the underlay around the *edges* of the shape, which
        suits wide fill areas, but adds needless bulk on the narrow strokes
        letters are made of.
      - "running_stitch": replaces each satin column with a plain running
        stitch along its centerline (bean_stitch_repeats passes over it for
        boldness). No satin ribbon, no zigzag texture - just a single thin
        thread line, which can look cleaner and more like solid handwriting
        at very small sizes where satin's zigzag edge gets visually "fuzzy".
        Trades off satin's characteristic sheen/boldness for that clean-line
        look; pull_compensation_mm/underlay don't apply to this style.

    Returns (final_width_mm, final_height_mm, within_recommended_range,
    narrowest_pinch_mm). narrowest_pinch_mm is the tightest gap found between
    a satin column's two rails anywhere in the text (None if stitch_style
    isn't "satin" or there are no satin glyphs) - font corner joins that are
    fine at native size can pinch down to a near-zero width once shrunk for
    small text, and Ink/Stitch's zigzag will still try to stitch across that
    degenerate point, piling up many stitches on top of each other right
    there rather than spacing them along the stroke - a real machine risk
    (thread bunching, possible jam), not just a cosmetic issue. Compare
    against PINCH_WARNING_MM. Widening the letters (more than the font's own
    min_scale might suggest) or switching to stitch_style="running_stitch"
    (no rails to pinch) are the two ways out - there's no way to fix a
    genuinely near-zero-width corner in the font's own source geometry
    without either.
    Raises FileNotFoundError if the font id doesn't exist.
    """
    import re

    import inkex

    from lib.lettering.font import Font

    if stitch_style not in ("satin", "running_stitch"):
        raise ValueError(f"Unknown stitch_style: {stitch_style!r} (expected 'satin' or 'running_stitch')")

    font_path = FONTS_DIR / font_id
    if not font_path.is_dir():
        raise FileNotFoundError(f"Unknown font id: {font_id} (see --list-fonts)")

    font = Font(str(font_path))
    reference_height = _measure_reference_letter_height(font)

    doc = _blank_svg_doc()
    root = doc.getroot()
    group = inkex.Group()
    root.append(group)

    font.render_text(text, group, scale=100)
    if group.bounding_box() is None:
        raise ValueError("Rendered text has no visible glyphs (unsupported characters?)")

    scale = letter_height_mm / reference_height
    group.attrib["transform"] = f"scale({scale})"

    narrowest_pinch_mm = None
    for path in list(group.iter("{http://www.w3.org/2000/svg}path")):
        is_satin = (path.get("inkstitch:satin_column") or "").lower() in ("true", "1", "yes", "y", "t")
        if not is_satin:
            continue

        if stitch_style == "running_stitch":
            center_line = _satin_center_line(path)
            if center_line is None:
                continue
            stroke_match = re.search(r"stroke:([^;]+)", path.get("style", ""))
            stroke_color = stroke_match.group(1) if stroke_match else "#000000"
            d = "M " + " L ".join(f"{x},{y}" for x, y in center_line)
            running_path = inkex.PathElement()
            running_path.set("d", d)
            running_path.set("style", f"stroke:{stroke_color};stroke-width:1;fill:none")
            if bean_stitch_repeats:
                running_path.set("inkstitch:bean_stitch_repeats", str(bean_stitch_repeats))
            path.getparent().replace(path, running_path)
        else:
            min_width_raw = _min_rail_width(path)
            min_width_mm = min_width_raw * scale if min_width_raw is not None else None
            if min_width_mm is not None and (narrowest_pinch_mm is None or min_width_mm < narrowest_pinch_mm):
                narrowest_pinch_mm = min_width_mm

            effective_compensation = pull_compensation_mm
            if pull_compensation_mm and min_width_mm is not None:
                # Never let compensation approach the width of the stroke's
                # narrowest point (a corner join, fine at native size but
                # often near-zero-width once shrunk for small text) - pushing
                # the rails apart there can push them past each other, piling
                # up stitches at that one spot instead of the letter
                # stitching cleanly. This alone doesn't fully solve a very
                # tight pinch (see narrowest_pinch_mm/PINCH_WARNING_MM), since
                # even zero compensation can't widen out a corner that's
                # already near-degenerate in the font's own source geometry.
                effective_compensation = min(pull_compensation_mm, min_width_mm * 0.25)
            if effective_compensation:
                path.set("inkstitch:pull_compensation_mm", str(effective_compensation))
            if underlay:
                path.set("inkstitch:center_walk_underlay", "true")

    if color:
        for path in group.iter("{http://www.w3.org/2000/svg}path"):
            style = path.get("style", "")
            style = ";".join(s for s in style.split(";") if not s.strip().startswith("fill:"))
            path.set("style", f"{style};fill:{color}")

    bbox = group.bounding_box()
    # The min/max recommended range is expressed by each font's own author as a
    # multiple of font.size (in mm) - that's the same number the official
    # Ink/Stitch lettering tool shows, so we trust it directly rather than
    # re-deriving it from measured glyph geometry (whose raw coordinate units
    # aren't consistently "1 unit = 1mm" across all ~140 community fonts).
    within_range = (font.size * font.min_scale) <= letter_height_mm <= (font.size * font.max_scale)

    with open(svg_out, "wb") as f:
        f.write(root.tostring())

    return bbox.width, bbox.height, within_range, narrowest_pinch_mm


def iter_fonts():
    """Yield (font_id, display_name, min_letter_height_mm) for every available
    font, sorted smallest minimum size first (ties broken by name).

    min_letter_height_mm is font.size * font.min_scale - the smallest a single
    capital letter should go for this font to still stitch cleanly (see
    render_text_to_svg's docstring for why this is per-letter, not per-word).
    """
    fonts = []
    for font_dir in FONTS_DIR.iterdir():
        font_json = font_dir / "font.json"
        if not font_json.is_file():
            continue
        try:
            meta = json.loads(font_json.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        name = meta.get("name", font_dir.name)
        min_height_mm = meta.get("size", 0) * meta.get("min_scale", 1.0)
        fonts.append((font_dir.name, name, min_height_mm))
    fonts.sort(key=lambda f: (f[2], f[1].lower()))
    yield from fonts


def list_fonts():
    for font_id, name, min_height_mm in iter_fonts():
        print(f"{font_id:30s} {name:40s} min {min_height_mm:.1f}mm")


def font_info(font_id: str):
    sys.path.insert(0, str(SCRIPT_DIR))
    import inkex  # noqa: F401
    from lib.lettering.font import Font

    font = Font(str(FONTS_DIR / font_id))
    print(f"{font.name} ({font_id})")
    print(f"description: {font.description}")
    print(f"recommended letter height: {font.size * font.min_scale:.1f}mm - {font.size * font.max_scale:.1f}mm")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("text", nargs="?", help="Text to render")
    parser.add_argument("font", nargs="?", help="Font id (folder name under fonts/src)")
    parser.add_argument("output", nargs="?", type=Path, help="Output .jef path")
    parser.add_argument("--height-mm", type=float, default=15.0,
                         help="Target height of a single (capital) letter in mm - the word is built from letters at that scale")
    parser.add_argument("--color", help="Thread color override, e.g. '#9da274'")
    parser.add_argument("--pull-compensation-mm", type=float, default=0.15,
                         help="Widen satin letters by this much to counteract fabric pull during stitching (0 disables)")
    parser.add_argument("--no-underlay", action="store_true",
                         help="Skip the stabilizing center-walk underlay under satin letters")
    parser.add_argument("--stitch-style", choices=["satin", "running_stitch"], default="satin",
                         help="'running_stitch' replaces each letter's satin ribbon with a thin line along its "
                              "centerline (bean-stitched for boldness) - a cleaner solid-line look at tiny sizes")
    parser.add_argument("--bean-stitch-repeats", type=int, default=2,
                         help="Passes over each running_stitch letter (0 = single thin pass, higher = bolder line)")
    parser.add_argument("--keep-svg", type=Path, help="Also save the intermediate SVG to this path")
    parser.add_argument("--list-fonts", action="store_true", help="List available font ids and exit")
    parser.add_argument("--font-info", metavar="FONT_ID", help="Show details about one font and exit")
    args = parser.parse_args()

    if args.list_fonts:
        list_fonts()
        return
    if args.font_info:
        font_info(args.font_info)
        return

    if not (args.text and args.font and args.output):
        parser.error("text, font and output are required (unless using --list-fonts / --font-info)")

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        svg_path = Path(tmp) / "text.svg"
        width_mm, height_mm, within_range, narrowest_pinch_mm = render_text_to_svg(
            args.font, args.text, args.height_mm, svg_path, color=args.color,
            pull_compensation_mm=args.pull_compensation_mm, underlay=not args.no_underlay,
            stitch_style=args.stitch_style, bean_stitch_repeats=args.bean_stitch_repeats,
        )
        if args.keep_svg:
            args.keep_svg.write_bytes(svg_path.read_bytes())
        pipeline.svg_to_jef(sys.executable, svg_path, args.output)

    if not within_range:
        print(f"WARNING: {args.height_mm}mm per letter is outside this font's recommended size range for clean "
              f"stitching. Try --list-fonts to find a font digitized for smaller sizes, or increase --height-mm.",
              file=sys.stderr)
    if narrowest_pinch_mm is not None and narrowest_pinch_mm < PINCH_WARNING_MM:
        print(f"WARNING: this text has a corner that pinches down to {narrowest_pinch_mm:.3f}mm wide at this size - "
              f"Ink/Stitch will still try to zigzag across it, piling up many stitches on top of each other there. "
              f"This can jam a real machine, not just look bad. Try a bigger --height-mm, or "
              f"--stitch-style running_stitch (no satin rails to pinch).",
              file=sys.stderr)

    actual_w, actual_h = pipeline.jef_bounds_mm(args.output)
    print(f"Wrote {args.output} ({actual_w:.2f}mm x {actual_h:.2f}mm)")


if __name__ == "__main__":
    main()
