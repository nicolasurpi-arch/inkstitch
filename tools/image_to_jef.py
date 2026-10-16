#!/usr/bin/env python
"""
Convert a raster image (PNG/JPG) or an SVG into a .jef embroidery file
using Inkscape's bitmap tracing (for raster input) and Ink/Stitch's own
output pipeline (for the stitch generation and JEF writing).

Raster images are traced one color at a time: we find the dominant colors
in the image ourselves (Pillow), build a binary mask per color, and trace
each mask independently with Inkscape. This avoids Inkscape's built-in
multi-scan (brightness-band) tracing, which can split a single flat color
across two brightness bands and produce spurious near-duplicate thread
colors in the output.

This is a personal dev utility, not part of the Ink/Stitch extension itself.

Usage:
    tools/image_to_jef.py input.png output.jef
    tools/image_to_jef.py input.svg output.jef
    tools/image_to_jef.py input.png output.jef --width-mm 21 --height-mm 30

Requirements:
    - Run with the project's venv (venv/bin/python) so `inkex`/lib import correctly.
    - Inkscape must be installed and on PATH (or pass --inkscape /path/to/inkscape).
"""
import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.parent.resolve()


def trace_bitmap(inkscape_bin: str, image_path: Path, svg_path: Path,
                  scans: int, smooth: bool, remove_background: bool,
                  speckles: int, smooth_corners: float, optimize: float) -> None:
    trace_args = f"{scans},{str(smooth).lower()},true,{str(remove_background).lower()},{speckles},{smooth_corners},{optimize}"
    # object-trace adds a vector path on top of the original raster <image>;
    # we strip the <image> back out afterwards (see strip_raster_images) since
    # Ink/Stitch only cares about the traced path.
    actions = ";".join([
        "select-all",
        f"object-trace:{trace_args}",
        "export-type:svg",
        f"export-filename:{svg_path}",
        "export-do",
    ])
    result = subprocess.run(
        [inkscape_bin, str(image_path), f"--actions={actions}"],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not svg_path.exists():
        print(result.stdout, file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        raise SystemExit(f"Inkscape tracing failed (exit {result.returncode})")


def strip_raster_images(svg_path: Path) -> None:
    """Remove <image> elements, keeping only the traced vector path(s)."""
    from lxml import etree
    tree = etree.parse(str(svg_path))
    root = tree.getroot()
    ns = {"svg": "http://www.w3.org/2000/svg"}
    for image in root.findall(".//svg:image", ns):
        image.getparent().remove(image)
    tree.write(str(svg_path))


def _color_distance(a, b) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def _is_grayscale(rgb, saturation_threshold: float = 20.0) -> bool:
    return (max(rgb) - min(rgb)) < saturation_threshold


def _detect_gradient(flat_arr, mask, min_r2: float = 0.6, min_delta: float = 30.0, max_local_jump: float = 80.0):
    """Check whether a color region is a smooth gradient rather than a flat
    color: fit each RGB channel as a linear function of position along the
    region's dominant axis (found via PCA on pixel coordinates), and accept
    it as a gradient only if the fit is good (min_r2) and the color actually
    changes by a meaningful amount (min_delta, Euclidean RGB distance) - a
    flat color's apparent "trend" from anti-aliasing noise fits poorly and/or
    barely moves, so both checks are needed to avoid false positives.

    Before any of that, reject the region if it contains a sharp *local*
    jump: two mask pixels that are immediate neighbors (not just mask-vs-
    background, which is legitimately sharp) but more than max_local_jump
    apart in color. A genuine gradient is smooth by definition - however
    steep overall, no single step between adjacent pixels is ever large.
    This case shows up when two different flat-colored shapes touch with no
    gap (e.g. two design elements placed edge to edge): the region-finding
    step above groups them as one connected blob since it only looks at
    foreground vs. background, and the real seam between the two colors can
    still pass the global linear-fit check decently if each side is fairly
    uniform - caught a real case where a pink shape touching a green one
    got read as a pink-to-green gradient instead of two flat fills.

    Returns (start_rgb, end_rgb, (x1, y1, x2, y2)) in pixel coordinates, or
    None if this region looks flat or isn't a single smooth region.
    """
    import numpy as np

    ys, xs = np.nonzero(mask)
    if len(xs) < 50:
        return None

    region_mask = np.zeros(flat_arr.shape[:2], dtype=bool)
    region_mask[mask] = True
    dx = np.linalg.norm(flat_arr[:, 1:].astype(float) - flat_arr[:, :-1].astype(float), axis=-1)
    dy = np.linalg.norm(flat_arr[1:, :].astype(float) - flat_arr[:-1, :].astype(float), axis=-1)
    mask_dx = region_mask[:, 1:] & region_mask[:, :-1]
    mask_dy = region_mask[1:, :] & region_mask[:-1, :]
    max_jump = 0.0
    if mask_dx.any():
        max_jump = max(max_jump, dx[mask_dx].max())
    if mask_dy.any():
        max_jump = max(max_jump, dy[mask_dy].max())
    if max_jump > max_local_jump:
        return None

    colors = flat_arr[mask].astype(float)
    coords = np.stack([xs, ys], axis=1).astype(float)
    center = coords.mean(axis=0)
    centered = coords - center

    cov = np.cov(centered.T)
    eigvals, eigvecs = np.linalg.eigh(cov)
    direction = eigvecs[:, int(np.argmax(eigvals))]

    t = centered @ direction
    A = np.vstack([t, np.ones_like(t)]).T

    # A channel with barely any variance (e.g. green in a pure red->blue
    # gradient) has an inherently unstable/meaningless R² - checking it would
    # unfairly reject an otherwise clean gradient just because one channel
    # happens to be flat. Only channels that actually vary need a good fit.
    flat_channel_variance = 9.0  # ~3 (out of 255) of standard deviation
    r2s = []
    channel_fits = []
    for c in range(3):
        y = colors[:, c]
        coef, _, _, _ = np.linalg.lstsq(A, y, rcond=None)
        y_pred = A @ coef
        ss_res = np.sum((y - y_pred) ** 2)
        ss_tot = np.sum((y - y.mean()) ** 2)
        if ss_tot / len(y) >= flat_channel_variance:
            r2s.append(1 - ss_res / ss_tot)
        channel_fits.append(coef)

    if not r2s or min(r2s) < min_r2:
        return None

    t_min, t_max = float(t.min()), float(t.max())
    start_rgb = tuple(int(np.clip(coef[0] * t_min + coef[1], 0, 255)) for coef in channel_fits)
    end_rgb = tuple(int(np.clip(coef[0] * t_max + coef[1], 0, 255)) for coef in channel_fits)
    if _color_distance(start_rgb, end_rgb) < min_delta:
        return None

    p1 = center + direction * t_min
    p2 = center + direction * t_max
    return start_rgb, end_rgb, (float(p1[0]), float(p1[1]), float(p2[0]), float(p2[1]))


def find_color_clusters(image_path: Path, max_colors: int, min_fraction: float, merge_threshold: float = 60.0,
                         background_distance: float = 30.0, despeckle_fraction: float = 0.00005):
    """Find the dominant foreground colors in an image, plus any smooth color
    gradients.

    Gradients are found first, on spatially-connected regions of the image
    (scipy connected-component labeling on a foreground/background mask) so
    that a region's full color span - which for a real gradient can easily
    exceed merge_threshold (e.g. red fading to blue) - doesn't get chopped
    into separate discrete-color clusters the way the color-distance-based
    merge below would. Gradient pixels are then excluded before the
    remaining, non-gradient part of the image goes through the normal
    per-color pipeline: quantize to `max_colors` bins (deliberately more than
    the number of real colors expected), then greedily merge bins whose mean
    colors are close together (anti-aliased edge pixels between a flat color
    and the background, or between two flat colors, otherwise show up as
    spurious extra clusters and would become spurious extra thread colors).
    This merge only pairs up bins that are on the same side of
    background_distance from the background color - without that, a pale
    foreground color within merge_threshold of the background (e.g. a light
    pastel outline on white) would merge directly into the true-background
    bin, which usually has a vastly larger pixel count, and get dragged
    almost all the way to the background color instead of just absorbing its
    own anti-aliased edge. Finally drops whichever merged cluster is closest
    to the background (sampled from the top-left corner pixel) - using
    background_distance, not merge_threshold, for the same reason - and
    anything smaller than min_fraction.

    Before returning, each non-gradient cluster is also despeckled: its mask
    is split into connected components, and a component is dropped if it's
    both smaller than despeckle_fraction of the image AND vanishes under a
    1px erosion. Quantizing to a handful of colors buckets by color, not
    position, so anti-aliased transition pixels along an edge between two
    *saturated* colors (not just a color and the background - already
    handled separately in the merge loop above by _is_grayscale) can land in
    a third color's bucket just because they're nearer that mean than either
    real neighbor. These show up as either small islands (a pure area cutoff
    catches them) or, just as often, thin 1px-wide rims running the length of
    some other shape's outline - individually long enough in total pixel
    count to dodge a pure area cutoff, but, unlike a real filled region,
    they disappear completely under a single erosion pass (confirmed on a
    case where one of these rims got long and isolated enough to read as a
    smooth pink-to-olive spatial trend and get misclassified as an actual
    color gradient - see _detect_gradient). Raising Inkscape's own
    --speckles trace-time cleanup globally isn't a safe alternative either -
    it would also erase genuinely small same-color details (e.g. a dotted
    font) elsewhere in the same image. Despeckling per cluster here, before
    any color ever reaches Inkscape, removes the specific noise without that
    tradeoff. A despeckled cluster gets a "mask" key (full-resolution boolean
    array) like a gradient cluster, so callers can use the same
    `cluster.get("mask", ...)` fallback either way.

    Returns a list of cluster dicts, largest first, plus the quantized
    palette-index array (needed to build a mask for clusters that don't
    carry their own "mask" - those have "indices": set[int] into it instead).
    Every cluster has "count", "mean" (r,g,b), and "gradient" - None for a
    flat color, or (start_rgb, end_rgb, (x1,y1,x2,y2)) in pixel coordinates
    for a gradient.
    """
    import numpy as np
    from PIL import Image
    from scipy import ndimage

    img = Image.open(image_path).convert("RGBA")
    w, h = img.size
    white_bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
    flat = Image.alpha_composite(white_bg, img).convert("RGB")
    bg_color = flat.getpixel((0, 0))
    flat_arr = np.array(flat)
    total = w * h

    # --- gradients: connected components of the foreground, checked before
    # any color-based clustering so a wide color span doesn't get split up ---
    pixel_bg_distance = np.sqrt(((flat_arr.astype(float) - np.array(bg_color)) ** 2).sum(axis=-1))
    foreground_mask = pixel_bg_distance >= background_distance

    gradient_clusters = []
    working_arr = flat_arr.copy()
    if foreground_mask.any():
        labeled, n_components = ndimage.label(foreground_mask, structure=np.ones((3, 3)))
        for label in range(1, n_components + 1):
            component_mask = labeled == label
            count = int(component_mask.sum())
            if count / total < min_fraction:
                continue
            gradient = _detect_gradient(flat_arr, component_mask)
            if gradient is None:
                continue
            mean_rgb = tuple(int(round(v)) for v in flat_arr[component_mask].mean(axis=0))
            gradient_clusters.append({"mask": component_mask, "count": count, "mean": mean_rgb, "gradient": gradient})
            working_arr[component_mask] = bg_color  # exclude from the discrete-color pass below

    working_img = Image.fromarray(working_arr)
    quantized = working_img.quantize(colors=max_colors, method=Image.Quantize.MEDIANCUT)
    counts = quantized.getcolors(maxcolors=w * h)
    idx_arr = np.array(quantized)

    clusters = []
    for count, idx in counts:
        mean_rgb = tuple(int(round(v)) for v in working_arr[idx_arr == idx].mean(axis=0))
        clusters.append({"indices": {idx}, "count": count, "mean": mean_rgb})

    merged_any = True
    while merged_any:
        merged_any = False
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                mean_i, mean_j = clusters[i]["mean"], clusters[j]["mean"]
                # Two grayscale (low-saturation) clusters are almost always the
                # same ink at different anti-aliasing dilution, not a genuine
                # second thread color - tiny, sparse detail (e.g. a dotted font,
                # where most rendered pixels are partial-coverage edge pixels)
                # can end up with a much lighter *average* than a solid black
                # border does, even though neither ever contains a truly black
                # pixel to anchor a same-color comparison against. Regular
                # color-distance merging alone can't catch that: it would also
                # have to merge genuinely different hues (checked against the
                # red/green and pink/olive test cases this threshold is tuned
                # against) to reach that far, so grayscale-ness is checked
                # explicitly instead, narrowly targeting shades of black/gray.
                grayscale_merge = (
                    _is_grayscale(mean_i) and _is_grayscale(mean_j)
                    and _color_distance(mean_i, bg_color) >= merge_threshold
                    and _color_distance(mean_j, bg_color) >= merge_threshold
                )
                # Only merge bins that are on the same side of the
                # background-distance line. Without this, a pale foreground
                # color sitting within merge_threshold of the background
                # (e.g. a light pastel outline) merges directly into the
                # true-background bin - which usually has a vastly larger
                # pixel count - and the merged mean gets dragged almost all
                # the way to the background color, erasing the real color
                # entirely instead of just absorbing its anti-aliased edge.
                same_side = (_color_distance(mean_i, bg_color) < background_distance) == \
                            (_color_distance(mean_j, bg_color) < background_distance)
                if same_side and (_color_distance(mean_i, mean_j) < merge_threshold or grayscale_merge):
                    ci, cj = clusters[i]["count"], clusters[j]["count"]
                    clusters[i] = {
                        "indices": clusters[i]["indices"] | clusters[j]["indices"],
                        "count": ci + cj,
                        "mean": tuple((a * ci + b * cj) / (ci + cj) for a, b in zip(clusters[i]["mean"], clusters[j]["mean"])),
                    }
                    del clusters[j]
                    merged_any = True
                    break
            if merged_any:
                break

    clusters.sort(key=lambda c: _color_distance(c["mean"], bg_color))
    if clusters and _color_distance(clusters[0]["mean"], bg_color) < background_distance:
        clusters = clusters[1:]

    clusters = [c for c in clusters if c["count"] / total >= min_fraction]

    despeckle_min_px = max(4, despeckle_fraction * total)
    despeckle_structure = np.ones((3, 3))
    for cluster in clusters:
        mask = np.isin(idx_arr, list(cluster["indices"]))
        labeled, n_components = ndimage.label(mask, structure=despeckle_structure)
        if n_components > 1:
            eroded = ndimage.binary_erosion(mask, structure=despeckle_structure)
            sizes = ndimage.sum(mask, labeled, range(1, n_components + 1))
            eroded_sizes = ndimage.sum(eroded, labeled, range(1, n_components + 1))
            keep_labels = [i + 1 for i, (size, eroded_size) in enumerate(zip(sizes, eroded_sizes))
                           if size >= despeckle_min_px and eroded_size > 0]
            if len(keep_labels) < n_components:
                mask = np.isin(labeled, keep_labels)
                cluster["mask"] = mask
        cluster["gradient"] = _detect_gradient(working_arr, mask)

    clusters = gradient_clusters + clusters
    clusters.sort(key=lambda c: -c["count"])
    return clusters, idx_arr


def trace_multicolor(inkscape_bin: str, image_path: Path, svg_path: Path, *,
                      max_colors: int = 12, min_fraction: float = 0.005,
                      smooth: bool = False, speckles: int = 2,
                      smooth_corners: float = 1.0, optimize: float = 0.2) -> None:
    """Trace each dominant color in the image as its own path with an explicit
    fill, then combine all paths into a single SVG. One color in -> one thread
    color out, deterministically."""
    import numpy as np
    from lxml import etree
    from PIL import Image

    clusters, idx_arr = find_color_clusters(image_path, max_colors, min_fraction)
    if not clusters:
        raise ValueError("No non-background colors detected in image")

    ns = {"svg": "http://www.w3.org/2000/svg"}
    combined_root = None

    with tempfile.TemporaryDirectory() as tmp:
        for i, cluster in enumerate(clusters):
            mean_rgb = tuple(int(round(v)) for v in cluster["mean"])
            cluster_mask = cluster["mask"] if "mask" in cluster else np.isin(idx_arr, list(cluster["indices"]))
            mask_arr = np.where(cluster_mask, 0, 255).astype("uint8")
            mask_path = Path(tmp) / f"mask_{i}.png"
            Image.fromarray(mask_arr, mode="L").convert("RGB").save(mask_path)

            layer_svg = Path(tmp) / f"layer_{i}.svg"
            # scans=1 (single brightness-cutoff scan) has a reproducible Inkscape 1.4.4
            # bug on these synthetic pure-B&W masks: it sometimes returns the full
            # canvas rectangle instead of the actual traced shape. scans=2 with
            # remove_background reliably keeps just the foreground (black) region.
            trace_bitmap(
                inkscape_bin, mask_path, layer_svg,
                scans=2, smooth=smooth, remove_background=True,
                speckles=speckles, smooth_corners=smooth_corners, optimize=optimize,
            )
            strip_raster_images(layer_svg)

            layer_root = etree.parse(str(layer_svg)).getroot()
            paths = layer_root.findall(".//svg:path", ns)
            hex_color = "#{:02x}{:02x}{:02x}".format(*mean_rgb)

            if combined_root is None:
                combined_root = layer_root
            else:
                for path in paths:
                    combined_root.append(path)

            gradient = cluster["gradient"]
            if gradient is None:
                for path in paths:
                    path.set("style", f"fill:{hex_color};stroke:none")
            else:
                start_rgb, end_rgb, (x1, y1, x2, y2) = gradient
                gradient_id = f"gradient{i}"
                defs = combined_root.find("{http://www.w3.org/2000/svg}defs")
                if defs is None:
                    defs = etree.SubElement(combined_root, "{http://www.w3.org/2000/svg}defs")
                grad_el = etree.SubElement(defs, "{http://www.w3.org/2000/svg}linearGradient", {
                    "id": gradient_id, "gradientUnits": "userSpaceOnUse",
                    "x1": str(x1), "y1": str(y1), "x2": str(x2), "y2": str(y2),
                })
                for offset, rgb in ((0, start_rgb), (1, end_rgb)):
                    stop_hex = "#{:02x}{:02x}{:02x}".format(*rgb)
                    etree.SubElement(grad_el, "{http://www.w3.org/2000/svg}stop", {
                        "offset": str(offset), "style": f"stop-color:{stop_hex};stop-opacity:1",
                    })
                for path in paths:
                    path.set("style", f"fill:url(#{gradient_id});stroke:none")
                    path.set("{http://inkstitch.org/namespace}fill_method", "linear_gradient_fill")

    etree.ElementTree(combined_root).write(str(svg_path))


def _split_ring_into_rails(coords: list):
    """Split a closed ring's coordinates into two open rails by cutting at the
    two vertices that are farthest apart from each other. Works well for a
    simple ribbon-shaped outline (a traced thin stroke); not meant for shapes
    with holes or branching (T/X junctions), which are left as fill instead."""
    import numpy as np

    coords = coords[:-1]  # drop the duplicated closing point
    pts = np.array(coords)
    if len(pts) < 4:
        return None
    d2 = ((pts[:, None, :] - pts[None, :, :]) ** 2).sum(-1)
    i, j = (int(x) for x in np.unravel_index(np.argmax(d2), d2.shape))
    if i > j:
        i, j = j, i
    if i == j:
        return None
    rail1 = coords[i:j + 1]
    rail2 = list(reversed(coords[j:] + coords[:i + 1]))
    if len(rail1) < 2 or len(rail2) < 2:
        return None
    return rail1, rail2


def _ribbon_width_mm(poly, px_per_mm: float, min_elongation: float = 2.5):
    """Estimate a shape's width in mm, but only if it's elongated enough to be
    a ribbon/stroke (as opposed to a compact blob like a square or circle,
    which should stay a fill no matter how small). Returns None for shapes
    that aren't elongated enough to qualify.

    Uses the shape's minimum rotated bounding rectangle: its short side is the
    ribbon width, and the long/short side ratio measures elongation - a plain
    square or circle has a ratio near 1 regardless of size, while a genuine
    line/stroke has a high ratio.
    """
    import warnings

    import numpy as np

    if poly.area <= 1e-6:
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            rect = poly.minimum_rotated_rectangle
    except Exception:
        return None
    if rect.geom_type != "Polygon":
        return None
    coords = np.array(list(rect.exterior.coords)[:-1])
    if len(coords) != 4:
        return None
    edges = [float(np.linalg.norm(coords[i] - coords[(i + 1) % 4])) for i in range(4)]
    side_a = (edges[0] + edges[2]) / 2
    side_b = (edges[1] + edges[3]) / 2
    short_side, long_side = sorted((side_a, side_b))
    if short_side <= 0 or long_side / short_side < min_elongation:
        return None
    return short_side * px_per_mm


def _solidity(poly) -> float | None:
    """area / convex_hull_area. A straight-edged blob or rectangle is close to
    1; a shape that bends back on itself (a crescent, a wave, a letter like S
    or C) is well below 1, because its convex hull has to bridge over the
    concave parts. Used to pick contour_fill (which follows the shape's own
    outline inward) over tatami's straight rows for such shapes - tatami rows
    fight a bent shape's curve and leave an uneven, strained-looking fill.
    """
    if poly.area <= 1e-6:
        return None
    hull = poly.convex_hull
    if hull.area <= 1e-6:
        return None
    return poly.area / hull.area


def _viewbox_width_units(root) -> float:
    view_box = root.get("viewBox")
    if view_box:
        return float(view_box.split()[2])
    return float(root.get("width"))


def _polys_to_closed_d(polys) -> str:
    import inkex

    d = ""
    for poly in polys:
        path = inkex.Path(list(poly.exterior.coords))
        path.close()
        d += str(path) + " "
        for interior in poly.interiors:
            path = inkex.Path(list(interior.coords))
            path.close()
            d += str(path) + " "
    return d


def classify_stitch_types(svg_path: Path, *, thin_width_mm: float = 1.2,
                           target_width_mm: float | None = None,
                           min_solidity: float = 0.0,
                           min_curvy_area_mm2: float = 15.0) -> None:
    """Split each plain fill path into up to three kinds of sub-regions:

    - Thin ribbon-shaped ones become satin columns (follow the line
      direction, sew more cleanly for fine linework than a fill would).
    - Bent/concave ones (a crescent, a wave, a letter like S or C) can get
      contour_fill instead of the default tatami via min_solidity (area /
      convex_hull area - a straight-edged blob stays near 1, a shape that
      bends back on itself drops well below it). Disabled by default
      (min_solidity=0.0): contour_fill's concentric rings need consistent
      room to spiral, and plenty of real bent shapes - not just small ones,
      also large-but-narrow ones like an illustration's limbs - don't have
      it, leaving visible gaps in the fill. Raise min_solidity (e.g. 0.85) to
      re-enable it for shapes below that solidity and at least
      min_curvy_area_mm2.
    - Everything else keeps the default tatami fill.

    (An earlier version of this also auto-converted "chain" shapes - several
    small blobs merged into one by touching, e.g. a dotted/perforated font -
    into satin columns. It's gone: every geometric test tried (perimeter/area
    ratio, fragmenting under erosion at various depths) also fired on
    perfectly ordinary bent letters like E, S and G, converting them to satin
    too and breaking normal lettering more often than it fixed dotted fonts.
    A dotted-font image still benefits from the grayscale color-merge fix in
    find_color_clusters; getting a continuous line rather than a scatter of
    tiny fills for that case needs handling it deliberately, not guessing
    from the traced shape alone.)

    Width is estimated per disjoint sub-polygon as 2*area/perimeter (exact for
    a stadium/rounded-rectangle shape, a good approximation for any elongated
    blob) and compared in mm against thin_width_mm, using target_width_mm (or
    the standard 96dpi assumption if the design isn't being resized) to convert
    the SVG's user units to mm. Only affects nodes that resolve to a plain
    FillStitch element - anything already marked as satin/stroke is untouched.
    """
    import inkex
    from lxml import etree

    sys.path.insert(0, str(SCRIPT_DIR))
    from lib.elements import node_to_elements  # noqa: E402

    ns = {"svg": "http://www.w3.org/2000/svg"}
    with open(svg_path, "rb") as f:
        doc = inkex.load_svg(f)
    root = doc.getroot()

    natural_width_units = _viewbox_width_units(root)
    px_per_mm = (target_width_mm / natural_width_units) if target_width_mm else (25.4 / 96)

    for path_node in list(root.findall(".//svg:path", ns)):
        elements = node_to_elements(path_node)
        fills = [e for e in elements if type(e).__name__ == "FillStitch"]
        if not fills:
            continue
        fill = fills[0]
        color = fill.color

        thin_polys, curvy_polys, flat_polys = [], [], []
        for poly in fill.shape.geoms:
            if poly.exterior.length <= 0 or poly.interiors:
                flat_polys.append(poly)
                continue
            width_mm = _ribbon_width_mm(poly, px_per_mm)
            if width_mm is not None and width_mm < thin_width_mm:
                thin_polys.append(poly)
                continue

            solidity = _solidity(poly)
            area_mm2 = poly.area * px_per_mm * px_per_mm
            if solidity is not None and solidity < min_solidity and area_mm2 >= min_curvy_area_mm2:
                curvy_polys.append(poly)
            else:
                flat_polys.append(poly)

        if not thin_polys and not curvy_polys:
            continue

        parent = path_node.getparent()
        index = list(parent).index(path_node)

        for poly in thin_polys:
            rails = _split_ring_into_rails(list(poly.exterior.coords))
            if rails is None:
                flat_polys.append(poly)
                continue
            d = str(inkex.Path(rails[0])) + " " + str(inkex.Path(rails[1]))
            satin_node = inkex.PathElement()
            satin_node.set("d", d)
            satin_node.set("style", f"stroke:{color};stroke-width:1;fill:none")
            satin_node.set("inkstitch:satin_column", True)
            parent.insert(index, satin_node)
            index += 1

        for poly in curvy_polys:
            # One contour_fill node per polygon, not all of them combined into
            # one: contour_fill's inner-to-outer spiral logic is designed for
            # a single region and gets confused when a path combines several
            # disconnected, separately-holed pieces (e.g. multiple dotted
            # letters merged into one curvy_polys batch) - it produced a much
            # messier result than plain tatami did on exactly that case, even
            # though contour_fill is the better choice for an individual bent
            # shape (see _solidity's docstring).
            contour_node = inkex.PathElement()
            contour_node.set("d", _polys_to_closed_d([poly]))
            contour_node.set("style", f"fill:{color};stroke:none")
            contour_node.set("inkstitch:fill_method", "contour_fill")
            parent.insert(index, contour_node)
            index += 1

        if flat_polys:
            path_node.set("d", _polys_to_closed_d(flat_polys))
        else:
            parent.remove(path_node)

    with open(svg_path, "wb") as f:
        f.write(etree.tostring(root))


def svg_to_jef(python_bin: str, svg_path: Path, jef_path: Path) -> None:
    with open(jef_path, "wb") as out:
        result = subprocess.run(
            [python_bin, str(SCRIPT_DIR / "inkstitch.py"), "--extension=output", "--format=jef", str(svg_path)],
            stdout=out, stderr=subprocess.PIPE,
        )
    if result.returncode != 0:
        print(result.stderr.decode(), file=sys.stderr)
        raise SystemExit(f"Ink/Stitch JEF export failed (exit {result.returncode})")


def jef_bounds_mm(jef_path: Path) -> tuple[float, float]:
    import pystitch
    pattern = pystitch.read(str(jef_path))
    x0, y0, x1, y1 = pattern.bounds()
    return (x1 - x0) / 10, (y1 - y0) / 10  # JEF units are 0.1mm


def set_svg_size_mm(svg_path: Path, width_mm: float, height_mm: float) -> None:
    from lxml import etree
    tree = etree.parse(str(svg_path))
    root = tree.getroot()
    root.set("width", f"{width_mm}mm")
    root.set("height", f"{height_mm}mm")
    tree.write(str(svg_path))


def resize_svg_to_target(python_bin: str, svg_path: Path, target_width_mm: float, target_height_mm: float) -> None:
    """Scale the SVG so the resulting JEF's stitch bounding box matches the target size.

    Two-pass: an initial guess proportioned from the viewBox rarely lands exactly
    on target because the stitched bounding box excludes any surrounding
    whitespace in the traced artwork, so we measure and correct once.
    """
    set_svg_size_mm(svg_path, target_width_mm, target_height_mm)

    with tempfile.TemporaryDirectory() as tmp:
        probe_jef = Path(tmp) / "probe.jef"
        svg_to_jef(python_bin, svg_path, probe_jef)
        actual_w, actual_h = jef_bounds_mm(probe_jef)

    corrected_w = target_width_mm * target_width_mm / actual_w
    corrected_h = target_height_mm * target_height_mm / actual_h
    set_svg_size_mm(svg_path, corrected_w, corrected_h)


def convert(input_path: Path, output_jef: Path, *,
            inkscape_bin: str = "inkscape", python_bin: str = sys.executable,
            max_colors: int = 12, min_color_fraction: float = 0.005,
            smooth: bool = False, speckles: int = 2, smooth_corners: float = 1.0, optimize: float = 0.2,
            width_mm: float | None = None, height_mm: float | None = None,
            thin_width_mm: float = 1.2, min_solidity: float = 0.0,
            min_curvy_area_mm2: float = 15.0,
            keep_svg: Path | None = None) -> tuple[float, float]:
    """Run the full input -> (trace) -> (resize) -> jef pipeline. Returns (width_mm, height_mm) achieved."""
    suffix = input_path.suffix.lower()

    with tempfile.TemporaryDirectory() as tmp:
        if suffix == ".svg":
            svg_path = Path(tmp) / "input.svg"
            svg_path.write_bytes(input_path.read_bytes())
        elif suffix in (".png", ".jpg", ".jpeg"):
            svg_path = Path(tmp) / "traced.svg"
            trace_multicolor(
                inkscape_bin, input_path, svg_path,
                max_colors=max_colors, min_fraction=min_color_fraction,
                smooth=smooth, speckles=speckles, smooth_corners=smooth_corners, optimize=optimize,
            )
        else:
            raise ValueError(f"Unsupported input type: {suffix}")

        if thin_width_mm > 0:
            classify_stitch_types(svg_path, thin_width_mm=thin_width_mm, target_width_mm=width_mm,
                                   min_solidity=min_solidity, min_curvy_area_mm2=min_curvy_area_mm2)

        if width_mm and height_mm:
            resize_svg_to_target(python_bin, svg_path, width_mm, height_mm)

        if keep_svg:
            keep_svg.write_bytes(svg_path.read_bytes())

        svg_to_jef(python_bin, svg_path, output_jef)

    return jef_bounds_mm(output_jef)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", type=Path, help="Input image (.png/.jpg) or vector (.svg)")
    parser.add_argument("output", type=Path, help="Output .jef path")
    parser.add_argument("--inkscape", default="inkscape", help="Path to the inkscape binary")
    parser.add_argument("--python", default=sys.executable, help="Python interpreter to run inkstitch.py with")
    parser.add_argument("--max-colors", type=int, default=12,
                         help="Quantization bins before merging near-duplicates (raise for images with many colors)")
    parser.add_argument("--min-color-fraction", type=float, default=0.005,
                         help="Ignore colors covering less than this fraction of the image")
    parser.add_argument("--smooth", action="store_true", help="Smooth the traced edges")
    parser.add_argument("--speckles", type=int, default=2, help="Suppress speckles smaller than N px")
    parser.add_argument("--smooth-corners", type=float, default=1.0)
    parser.add_argument("--optimize", type=float, default=0.2)
    parser.add_argument("--keep-svg", type=Path, help="Also save the intermediate traced SVG to this path")
    parser.add_argument("--width-mm", type=float, help="Target design width in mm (requires --height-mm)")
    parser.add_argument("--height-mm", type=float, help="Target design height in mm (requires --width-mm)")
    parser.add_argument("--thin-width-mm", type=float, default=1.2,
                         help="Shapes narrower than this become satin columns instead of fill (0 disables)")
    parser.add_argument("--min-solidity", type=float, default=0.0,
                         help="Shapes less solid (area/convex_hull_area) than this - bent, concave - get "
                              "contour fill instead of tatami (0 disables, the default; try 0.85 to re-enable)")
    parser.add_argument("--min-curvy-area-mm2", type=float, default=15.0,
                         help="Minimum area for a bent/concave shape to get contour fill - below this it's too "
                              "small for the concentric-ring effect to work and stays plain fill (0 disables the minimum)")
    args = parser.parse_args()

    if not args.input.exists():
        raise SystemExit(f"Input file not found: {args.input}")

    if bool(args.width_mm) != bool(args.height_mm):
        raise SystemExit("--width-mm and --height-mm must be given together")

    try:
        actual_w, actual_h = convert(
            args.input, args.output,
            inkscape_bin=args.inkscape, python_bin=args.python,
            max_colors=args.max_colors, min_color_fraction=args.min_color_fraction,
            smooth=args.smooth, speckles=args.speckles, smooth_corners=args.smooth_corners, optimize=args.optimize,
            width_mm=args.width_mm, height_mm=args.height_mm, thin_width_mm=args.thin_width_mm,
            min_solidity=args.min_solidity, min_curvy_area_mm2=args.min_curvy_area_mm2,
            keep_svg=args.keep_svg,
        )
    except ValueError as e:
        raise SystemExit(str(e))

    print(f"Wrote {args.output} ({actual_w:.2f}mm x {actual_h:.2f}mm)")


if __name__ == "__main__":
    main()
