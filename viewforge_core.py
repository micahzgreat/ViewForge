"""
ViewForge core. No interface, opens no windows, safe to import and drive
directly.

A view is either a detected blob of ink (source 'component', rendered from that
blob alone) or a rectangle you drew (source 'box', where the rectangle is the
measurement and ink outside it is clipped). Either can carry measure lines: a
pair of guides marking what a stated dimension is actually taken between.
"""

from __future__ import annotations
import json, math, os, time
import numpy as np
from PIL import Image
from scipy import ndimage

# Which two of L/W/H a view shows, as (horizontal, vertical) in the image.
# Only right for a view drawn upright. Use view_axes() instead.
ROLE_AXES = {
    "side":   ("L", "H"),
    "top":    ("L", "W"),
    "bottom": ("L", "W"),
    "front":  ("W", "H"),
    "back":   ("W", "H"),
}
ROLES = ["ignore"] + list(ROLE_AXES)

# On front and back views the object's front faces you, so the facing question
# has no answer and those are stored as 'n/a'.
FACING_ROLES = ("side", "top", "bottom")
FACINGS = ["left", "right", "up", "down"]

# Length on Y with the front at -Y, width on X, height on Z, centred on the
# origin. 'facing' is which image edge the object's front sits against.
BLENDER = {
    ("side",   "left"):  dict(rot=(90, 0,  90), normal="+X", key="Numpad 3"),
    ("side",   "right"): dict(rot=(90, 0, -90), normal="-X", key="Ctrl+Numpad 3"),
    ("side",   "up"):    dict(rot=(0, -90, 180), normal="+X", key="Numpad 3"),
    ("side",   "down"):  dict(rot=(0,  90,  0), normal="+X", key="Numpad 3"),
    ("top",    "left"):  dict(rot=(0,  0,  90), normal="+Z", key="Numpad 7"),
    ("top",    "right"): dict(rot=(0,  0, -90), normal="+Z", key="Numpad 7"),
    ("top",    "up"):    dict(rot=(0,  0, 180), normal="+Z", key="Numpad 7"),
    ("top",    "down"):  dict(rot=(0,  0,   0), normal="+Z", key="Numpad 7"),
    ("bottom", "left"):  dict(rot=(180, 0, 90), normal="-Z", key="Ctrl+Numpad 7"),
    ("bottom", "right"): dict(rot=(180, 0, -90), normal="-Z", key="Ctrl+Numpad 7"),
    ("bottom", "up"):    dict(rot=(180, 0,  0), normal="-Z", key="Ctrl+Numpad 7"),
    ("bottom", "down"):  dict(rot=(180, 0, 180), normal="-Z", key="Ctrl+Numpad 7"),
    ("front",  "n/a"):   dict(rot=(90, 0,   0), normal="-Y", key="Numpad 1"),
    ("back",   "n/a"):   dict(rot=(90, 0, 180), normal="+Y", key="Ctrl+Numpad 1"),
}

MARGIN = 1.06     # fixed. The canvas also grows on its own if a view would
                  # otherwise be cut off.

# 'normal' is about one output pixel per source pixel, then the canvas is held
# between these bounds.
DETAIL = {"draft": 0.5, "normal": 1.0, "fine": 2.0}
CANVAS_MAX_PX = 6000
CANVAS_MIN_PX = 500

# Proportional mode sizes the object so its largest side is this.
PROPORTIONAL_MM = 1000.0


class SolveError(ValueError):
    """Something the user can fix. The message says how."""


# Blueprint i/o and auto-detect

# The lightest an ink pixel may come out in an exported plane. The self-check
# re-measures the written files by looking for pixels under 200, so tone that is
# kept has to still read as ink there.
INK_MAX = 199
INK_MIN_T = 16          # never call a difference smaller than this a mark


def read_image(path):
    """The blueprint as HxWx3 uint8, transparency flattened onto white.

    Converting an RGBA drawing straight to RGB turns every transparent pixel
    black, which arrives as one solid ink rectangle covering the page.
    """
    im = Image.open(path)
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        im = Image.alpha_composite(
            Image.new("RGBA", im.size, (255, 255, 255, 255)), im.convert("RGBA"))
    return np.asarray(im.convert("RGB"), dtype=np.uint8)


def _luma(a):
    return a[..., 0] * 0.299 + a[..., 1] * 0.587 + a[..., 2] * 0.114


def _dist_from(rgb, paper):
    """How far each pixel is from the paper colour, in its furthest channel.

    Distance, not brightness. A colour drawing's fills are not reliably dark: a
    pale yellow panel is lighter than mid grey linework, so a brightness test
    drops it, while a saturated red one is dark enough to pass and comes back
    as solid black.
    """
    p = np.asarray(paper, dtype=np.int16).reshape(1, 1, 3)
    return np.abs(rgb.astype(np.int16) - p).max(axis=2).astype(np.uint8)


def _pick_threshold(dist):
    """Above the paper's own grain, below the real marks.

    The old rule was the 90th percentile of brightness minus 25, which on clean
    white paper calls everything under 230 ink - grey watermarks, scanner
    shading and JPEG mush included.

    Most of a drawing is paper, so the 75th percentile of the distance from the
    paper colour measures how much the paper itself wanders: nothing on a clean
    export, a good deal on a photograph with a shadow across it. Sit above that.
    """
    hist = np.bincount(dist.ravel(), minlength=256).astype(float)
    sm = np.convolve(hist, np.ones(5) / 5.0, mode="same")
    peak = int(np.argmax(sm[:48]))
    top = sm[peak]
    if top <= 0:
        return INK_MIN_T
    # Walk out of the paper cluster until its tail has died away. Where that
    # happens is the paper's spread, whatever share of the page it occupies -
    # a percentile cannot do this, because a sheet with four large views on it
    # is more than half ink and any high percentile lands inside the drawing.
    t = INK_MIN_T
    for i in range(peak + 1, 161):
        if sm[i] < 0.06 * top:
            t = i + 2
            break
    return int(min(max(t, INK_MIN_T), 200))


def ink_mask(rgb, threshold=None, invert=None):
    """(ink, info). info says what was worked out about the drawing."""
    rgb = np.asarray(rgb, dtype=np.uint8)
    flat = rgb[::3, ::3].reshape(-1, 3)
    light = np.percentile(flat, 90, axis=0)
    dark = np.percentile(flat, 10, axis=0)
    if invert is None:
        d = _dist_from(rgb, light)
        t = threshold if threshold is not None else _pick_threshold(d)
        # A drawing is not two thirds ink. If it looks like it is, the paper is
        # the dark side: pale linework on a dark ground.
        invert = bool((d > t).mean() > 0.60)
    paper = dark if invert else light
    dist = _dist_from(rgb, paper)
    t = int(threshold) if threshold is not None else _pick_threshold(dist)
    ink = dist > t

    s = rgb[::4, ::4].astype(np.int16)
    m = ink[::4, ::4]
    s = s[m] if m.any() else s.reshape(-1, 3)
    chroma = float(np.mean(s.max(axis=1) - s.min(axis=1))) if len(s) else 0.0
    cov = float(ink.mean())
    inner = float(ndimage.binary_erosion(ink, np.ones((5, 5), bool)).mean())
    return ink, dict(threshold=t, inverted=bool(invert), coverage=cov,
                     paper=[int(round(x)) for x in paper], chroma=chroma,
                     colour=bool(chroma > 18.0),
                     filled=bool(cov > 0.02 and inner > 0.45 * cov))


def load_blueprint(path, threshold=None, invert=None):
    """(grey, ink, info) for a path, a PIL image or an RGB array."""
    if isinstance(path, (str, bytes, os.PathLike)):
        rgb = read_image(path)
    elif isinstance(path, Image.Image):
        rgb = np.asarray(path.convert("RGB"), dtype=np.uint8)
    else:
        rgb = np.asarray(path, dtype=np.uint8)
    ink, info = ink_mask(rgb, threshold, invert)
    return _luma(rgb.astype(np.float32)).astype(np.int16), ink, info


def render_source(rgb, ink, info=None, style="auto"):
    """(pixels, PIL mode) an exported plane is drawn from, paper lifted to white.

    'line' is the old behaviour: every mark flattened to solid black. That is
    what wrecks a drawing with colour or filled areas in it, because a red car
    body and the outline around it are equally ink, so the view exports as one
    black blob. 'tone' and 'colour' keep the drawing as drawn and throw away
    only the paper.
    """
    rgb = np.asarray(rgb, dtype=np.uint8)
    if info is None:
        inverted = bool(ink.mean() > 0.60)
        info = dict(inverted=inverted, colour=True,
                    paper=[int(round(x)) for x in
                           np.percentile(rgb[::3, ::3].reshape(-1, 3),
                                         10 if inverted else 90, axis=0)])
    if style == "auto":
        style = "colour" if info.get("colour") else "tone"
    if style == "line":
        return np.where(ink, 0, 255).astype(np.uint8), "L"
    if style not in ("tone", "colour"):
        raise SolveError(f"Unknown render style '{style}'. Use 'auto', "
                         f"'colour', 'tone' or 'line'.")
    a = rgb.astype(np.float32)
    paper = np.asarray(info.get("paper", [255, 255, 255]), np.float32)
    if info.get("inverted"):
        a, paper = 255.0 - a, 255.0 - paper
    # Paper to white, so a grey scan does not export as a grey rectangle.
    a = np.clip(a * (255.0 / np.maximum(paper, 40.0)).reshape(1, 1, 3), 0.0, 255.0)
    if style == "tone":
        return (np.where(ink, np.minimum(_luma(a), INK_MAX), 255.0)
                .astype(np.uint8), "L")
    a = a * np.minimum(1.0, INK_MAX / np.maximum(_luma(a), 1.0))[..., None]
    return np.where(ink[..., None], a, 255.0).astype(np.uint8), "RGB"


def new_view(**kw):
    v = dict(cid=None, source="box", x0=0, x1=10, y0=0, y1=10,
             role="ignore", facing="left", hflip=False,
             gauge_h=None, gauge_v=None, centre_h=None, centre_v=None)
    v.update(kw)
    return v


def view_axes(v):
    """(horizontal, vertical) dimensions for this view. A quarter turn round
    swaps them, which is why nothing indexes ROLE_AXES directly."""
    ah, av = ROLE_AXES[v["role"]]
    if v["role"] in FACING_ROLES and v.get("facing") in ("up", "down"):
        return av, ah
    return ah, av


def detect_views(ink, gap=9, min_side=60):
    lab, n = ndimage.label(ndimage.binary_dilation(ink, np.ones((gap, gap), bool)))
    out = []
    for i, sl in enumerate(ndimage.find_objects(lab), start=1):
        if sl is None:
            continue
        own = ink & (lab == i)
        ys, xs = np.nonzero(own)
        if len(xs) == 0:
            continue
        x0, x1 = int(xs.min()), int(xs.max())
        y0, y1 = int(ys.min()), int(ys.max())
        # Filter on the region's own ink; the dilated slice is inflated by gap.
        if (x1 - x0 + 1) < min_side or (y1 - y0 + 1) < min_side:
            continue
        out.append(new_view(cid=i, source="component",
                            x0=x0, x1=x1, y0=y0, y1=y1))
    out.sort(key=lambda v: -((v["x1"] - v["x0"]) * (v["y1"] - v["y0"])))
    return out, lab


def segmentation_quality(views, ink):
    h, w = ink.shape
    for v in views:
        if (v["x1"] - v["x0"]) > 0.9 * w and (v["y1"] - v["y0"]) > 0.35 * h:
            return ["Auto-detect merged a large area into one region. This "
                    "blueprint's dimension lines probably join the views "
                    "together. Drag the region's edges apart by hand, or draw a "
                    "box over each view with its edges on the object's extremes."]
    return []


# Per view metrics, and symmetry detection
def clamp_box(v, shape):
    """Order a box and pull it onto the blueprint.

    numpy slices silently, so an edge past the border would trim the mask but
    leave the span at its typed value, quietly wrecking the scale.
    """
    h, w = shape
    x0, x1 = sorted((int(v["x0"]), int(v["x1"])))
    y0, y1 = sorted((int(v["y0"]), int(v["y1"])))
    cx0, cx1 = max(0, min(x0, w - 1)), max(0, min(x1, w - 1))
    cy0, cy1 = max(0, min(y0, h - 1)), max(0, min(y1, h - 1))
    return (cx0, cx1, cy0, cy1), ((cx0, cx1, cy0, cy1) != (x0, x1, y0, y1))


def view_metrics(v, ink, lab):
    if v["source"] == "component" and v["cid"]:
        if lab is None:
            raise SolveError(
                "A view still refers to an auto-detected region, but the "
                "blueprint has not been analysed. Press Detect views.")
        mask = ink & (lab == v["cid"])
        ys, xs = np.nonzero(mask)
        if len(xs) == 0:
            raise SolveError(
                f"The auto-detected region for view '{v['role']}' no longer "
                f"exists - the merge gap was probably changed after it was "
                f"assigned. Press Detect views and set the roles again.")
        box = (int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max()))
    else:
        box, _ = clamp_box(v, ink.shape)
        x0, x1, y0, y1 = box
        if x1 - x0 < 2 or y1 - y0 < 2:
            raise SolveError(
                f"The box for view '{v['role']}' is too small, or lies off the "
                f"blueprint. Drag it again with its edges on the object's "
                f"extremes.")
        mask = np.zeros_like(ink)
        mask[y0:y1 + 1, x0:x1 + 1] = ink[y0:y1 + 1, x0:x1 + 1]
        if not mask.any():
            raise SolveError(
                f"The box for view '{v['role']}' contains no ink. It is sitting "
                f"on blank paper - drag it over the view itself, or set this "
                f"view to 'ignore'.")
    return mask, box[1] - box[0] + 1, box[3] - box[2] + 1, box


def gauge_span(v, box, axis, shape):
    """Pixels the stated dimension covers on one axis: the whole view, or the
    gap between its measure lines. Returns (pixels, lines_were_used)."""
    key = "gauge_h" if axis == 0 else "gauge_v"
    full = (box[1] - box[0] + 1) if axis == 0 else (box[3] - box[2] + 1)
    g = v.get(key)
    if not g:
        return full, False
    lim = (shape[1] if axis == 0 else shape[0]) - 1
    a, b = sorted(int(round(max(0, min(lim, t)))) for t in g[:2])
    if b - a < 2:
        raise SolveError(
            f"The measure lines on view '{v['role']}' are on top of each other "
            f"({'across' if axis == 0 else 'down'} the view). Drag them onto "
            f"the two points your dimension is taken between, or switch them "
            f"off to measure the whole view.")
    return b - a + 1, True


def gauge_outside(v, box, axis, shape):
    """True when a measure line sits outside its own view. Legal, but usually a
    line left behind after the box moved."""
    g = v.get("gauge_h" if axis == 0 else "gauge_v")
    if not g:
        return False
    lo, hi = (box[0], box[1]) if axis == 0 else (box[2], box[3])
    return any(t < lo - 1 or t > hi + 1 for t in g[:2])


SYM_BAND = 0.05      # axis may sit this far from the bbox midpoint, as a
                     # fraction of the view's span
SYM_GAIN = 0.05      # and only if it beats the midpoint by this much


def symmetry_axis(mask, box, axis):
    """Find a view's mirror line. axis=0 returns a row, 1 returns a column.

    Candidates stay in a band around the midpoint, every one is scored over the
    same width so an edge axis cannot win by comparing almost nothing, and the
    winner has to beat the plain midpoint or the midpoint stands.
    """
    x0, x1, y0, y1 = box
    sub = mask[y0:y1 + 1, x0:x1 + 1]
    p = sub.sum(axis=1 - axis).astype(float)
    base = y0 if axis == 0 else x0
    mid_box = (len(p) - 1) / 2
    nz = np.nonzero(p)[0]
    if len(nz) < 16:
        return base + mid_box
    lo, hi = float(nz[0]), float(nz[-1])
    mid, span = (lo + hi) / 2, hi - lo
    band = max(2.0, SYM_BAND * span)      # how far the axis may stray
    reach = max(8, int(0.40 * span))      # fixed comparison half width
    idx = np.arange(len(p), dtype=float)
    t = np.arange(1, reach + 1, dtype=float)

    def score(c):
        lv = np.interp(c - t, idx, p, left=0.0, right=0.0)
        rv = np.interp(c + t, idx, p, left=0.0, right=0.0)
        return float(np.abs(lv - rv).mean())

    d_mid = score(mid)
    best, bestd = mid, d_mid
    c = mid - band
    while c <= mid + band + 1e-9:
        d = score(c)
        if d < bestd:
            best, bestd = c, d
        c += 0.5
    # Then again, finely, around the winner. Half a pixel of resolution sounds
    # like plenty until you notice it is the whole error budget: on a drawing
    # scanned at seven millimetres to the pixel, half a pixel is nearly four
    # millimetres of mirror line, which is the difference between the two
    # halves of a model meeting and not.
    c = best - 0.5
    while c <= best + 0.5 + 1e-9:
        d = score(c)
        if d < bestd:
            best, bestd = c, d
        c += 0.05
    if bestd > d_mid * (1.0 - SYM_GAIN):
        best = mid
    return base + best


def mirror_line(v, ink, lab):
    """Where this view looks symmetric, on whichever axis carries the width.

    Returns ('h', column) or ('v', row) in blueprint pixels, or None when the
    view does not show W at all - a side view carries length and height, and
    has no middle to find.
    """
    if v["role"] not in ROLE_AXES:
        return None
    ah, av = view_axes(v)
    if ah == "W":
        mask, _, _, box = view_metrics(v, ink, lab)
        return "h", float(symmetry_axis(mask, box, 1))
    if av == "W":
        mask, _, _, box = view_metrics(v, ink, lab)
        return "v", float(symmetry_axis(mask, box, 0))
    return None


def box_centre(v, ink, lab, key):
    """The middle of a view's box on one axis, which is where it is centred
    when no centre line is set."""
    _, _, _, box = view_metrics(v, ink, lab)
    return ((box[0] + box[1]) / 2.0 if key == "h" else (box[2] + box[3]) / 2.0)


# Solving
def active_views(views):
    out = [v for v in views if v["role"] != "ignore"]
    if not out:
        raise SolveError("No views assigned yet. Give at least one view a role.")
    for v in out:
        if v["role"] not in ROLE_AXES:
            raise SolveError(f"'{v['role']}' is not a view role. Use one of: "
                             + ", ".join(ROLE_AXES))
    return out


def proportional_dims(views, ink, lab, size_mm=PROPORTIONAL_MM):
    """Dimensions from the drawing itself, for when real sizes do not matter.

    One scale for the whole blueprint, sized so the largest side is size_mm.
    Views measuring the same axis are combined with a median so one bad box
    cannot run away with it. None of this is a real measurement.
    """
    px = {}
    for v in active_views(views):
        _, _, _, box = view_metrics(v, ink, lab)
        ah, av = view_axes(v)
        gh, _ = gauge_span(v, box, 0, ink.shape)
        gv, _ = gauge_span(v, box, 1, ink.shape)
        px.setdefault(ah, []).append(float(gh))
        px.setdefault(av, []).append(float(gv))
    med = {k: float(np.median(val)) for k, val in px.items()}
    biggest = max(med.values())
    if biggest <= 0:
        raise SolveError("The assigned views have no measurable size.")
    s = size_mm / biggest
    return {k: round(val * s, 4) for k, val in med.items()}


def solve(views, ink, lab, dims):
    active = active_views(views)
    for k, val in dims.items():
        if not math.isfinite(val) or val <= 0:
            raise SolveError(
                f"Dimension {k} is {val}. Dimensions must be positive numbers "
                f"in millimetres.")

    report = {"views": [], "warnings": []}
    for v in active:
        ah, av = view_axes(v)
        missing = [a for a in (ah, av) if not dims.get(a)]
        if missing:
            raise SolveError(
                f"View '{v['role']}' measures {ah} and {av}, but "
                f"{' and '.join(missing)} is blank. Enter it, or set this view "
                f"to 'ignore'.")
        mask, sh_px, sv_px, box = view_metrics(v, ink, lab)
        gh, used_h = gauge_span(v, box, 0, ink.shape)
        gv, used_v = gauge_span(v, box, 1, ink.shape)
        rec = dict(role=v["role"], facing=v["facing"], axis_h=ah, axis_v=av,
                   span_h=sh_px, span_v=sv_px, gauge_h=gh, gauge_v=gv,
                   gauged_h=used_h, gauged_v=used_v, box=box,
                   mm_px_h=dims[ah] / gh, mm_px_v=dims[av] / gv)
        rec["anisotropy"] = rec["mm_px_h"] / rec["mm_px_v"]
        # The whole view in mm, which is what the exported plane measures.
        rec["full_h"] = sh_px * rec["mm_px_h"]
        rec["full_v"] = sv_px * rec["mm_px_v"]
        # Where the object's origin sits on this view. The middle of the box
        # unless you have put a centre line on it.
        #
        # The box centre is a good guess and a bad guarantee. It is the middle
        # of the ink, and the middle of the ink is only the middle of the
        # object when the drawing is perfectly even - one wing mirror caught by
        # the box, a dimension arrow, an edge drawn a little heavier, and it is
        # not. On the width axis that matters more than anywhere else, because
        # a mirror modifier works about X=0 and nothing else will do.
        rec["bc_h"] = (box[0] + box[1]) / 2
        rec["bc_v"] = (box[2] + box[3]) / 2
        # Where the ink itself sits, which is not the middle of a hand-drawn
        # box unless the box hugs it. The self-check measures ink, so this is
        # what an intended offset has to be stated against.
        iys, ixs = np.nonzero(mask)
        rec["ic_h"] = (int(ixs.min()) + int(ixs.max())) / 2.0
        rec["ic_v"] = (int(iys.min()) + int(iys.max())) / 2.0
        rec["set_h"] = v.get("centre_h") is not None
        rec["set_v"] = v.get("centre_v") is not None
        rec["cen_h"] = float(v["centre_h"]) if rec["set_h"] else rec["bc_h"]
        rec["cen_v"] = float(v["centre_v"]) if rec["set_v"] else rec["bc_v"]
        rec["off_h"] = rec["off_v"] = 0.0     # filled in once the scale is known
        # The mirror line the drawing itself suggests, measured against
        # whatever centre is actually being used.
        rec["sym_px"] = rec["sym_key"] = None
        rec["sym_off"] = 0.0
        if ah == "W":
            rec["sym_px"] = symmetry_axis(mask, box, 1)
            rec["sym_key"] = "h"
            rec["sym_off"] = (rec["sym_px"] - rec["cen_h"]) * rec["mm_px_h"]
        elif av == "W":
            rec["sym_px"] = symmetry_axis(mask, box, 0)
            rec["sym_key"] = "v"
            rec["sym_off"] = (rec["sym_px"] - rec["cen_v"]) * rec["mm_px_v"]
        # A component renders from its own blob alone. Ink inside its bounds
        # that belongs to something else is not exported, and a hole or an
        # island floating free of the outline is exactly that.
        if v["source"] == "component":
            x0, x1, y0, y1 = box
            here = ink[y0:y1 + 1, x0:x1 + 1]
            lost = int(here.sum()) - int(mask[y0:y1 + 1, x0:x1 + 1].sum())
            own = int(mask.sum())
            if lost > 50 and lost > 0.02 * own:
                report["warnings"].append(
                    f"{v['role']}: {lost} pixels of ink inside this region are "
                    f"not part of it and will not be exported - detail that "
                    f"floats free of the outline, like a hole or an island of "
                    f"hatching, is its own region. Raise the merge gap until it "
                    f"joins on, or draw a box over the view instead, where "
                    f"everything inside the box is kept.")

        rec["_v"] = v
        report["views"].append(rec)

        for axis, ax in ((0, ah), (1, av)):
            if gauge_outside(v, box, axis, ink.shape):
                report["warnings"].append(
                    f"{v['role']}: a measure line for {ax} sits outside the "
                    f"view's own box. That is allowed, but check it is not a "
                    f"line left behind after the box was moved.")

    # The width axis, which is the one a mirror modifier cares about.
    sym = [r for r in report["views"] if r["sym_key"]]
    for r in sym:
        if abs(r["sym_off"]) > 0.01 * dims["W"]:
            fixed = r["set_h"] if r["sym_key"] == "h" else r["set_v"]
            report["warnings"].append(
                f"{r['role']}: the line this view looks symmetric about sits "
                f"{abs(r['sym_off']):.1f} mm off the centre it is being drawn "
                f"around. Exported like this its middle is {abs(r['sym_off']):.1f} "
                f"mm off X=0, so a mirror modifier will not line up with it. "
                + ("Its centre line is set by hand - check you put it on the "
                   "object's middle." if fixed else
                   "Switch on this view's centre line and put it on the "
                   "object's middle, or press 'Centre on the mirror line'."))
    if len(sym) > 1:
        spread = max(r["sym_off"] for r in sym) - min(r["sym_off"] for r in sym)
        if spread > 0.01 * dims["W"]:
            report["warnings"].append(
                f"Views disagree about where the object's middle is, by "
                f"{spread:.1f} mm across W. Even centred, at least one of them "
                f"will sit off the others in Blender. Put a centre line on each "
                f"by hand, on the same feature.")

    for r in report["views"]:
        off = abs(r["anisotropy"] - 1) * 100
        if off > 1.0:
            report["warnings"].append(
                f"{r['role']}: drawn {off:.1f}% out of square - {r['axis_h']} "
                f"implies {r['mm_px_h']:.4f} mm/px but {r['axis_v']} implies "
                f"{r['mm_px_v']:.4f}. One of those two dimensions is wrong, or "
                f"this view is stretched.")

    report["max_skew"] = max((abs(r["anisotropy"] - 1) * 100
                              for r in report["views"]), default=0.0)
    sc = [s for r in report["views"] for s in (r["mm_px_h"], r["mm_px_v"])]
    if sc and (max(sc) / min(sc) - 1) > 0.02:
        report["warnings"].append(
            f"Views disagree on scale by {(max(sc)/min(sc)-1)*100:.1f}%. In "
            f"'fit' mode they will still line up, but the worst view is being "
            f"stretched that much to get there.")
    return report



# Project files
PROJECT_EXT = ".vf"
PROJECT_KIND = "viewforge-project"
PROJECT_VERSION = 1

VIEW_KEYS = ("cid", "source", "x0", "x1", "y0", "y1", "role", "facing",
             "hflip", "gauge_h", "gauge_v", "centre_h", "centre_v")


def project_data(blueprint, views, dims, settings, note=""):
    """Everything needed to pick the job back up, as plain data."""
    return {
        "kind": PROJECT_KIND, "version": PROJECT_VERSION,
        "saved": time.strftime("%Y-%m-%d %H:%M:%S"),
        "blueprint": blueprint,
        "views": [{k: v.get(k) for k in VIEW_KEYS if k in v} for v in views],
        "dims": dict(dims or {}),
        "settings": dict(settings or {}),
        "note": note,
    }


def save_project(path, data):
    """Write a .vf. The blueprint is recorded twice, absolute and relative to
    the project file, so moving the pair to another machine still works."""
    path = os.path.abspath(path)
    bp = data.get("blueprint") or {}
    if bp.get("path"):
        full = os.path.abspath(bp["path"])
        bp["path"] = full
        try:
            bp["relative"] = os.path.relpath(full, os.path.dirname(path))
        except ValueError:                 # a different drive on Windows
            bp["relative"] = None
        if os.path.exists(full):
            st = os.stat(full)
            bp["bytes"] = int(st.st_size)
    data["blueprint"] = bp
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return path


def load_project(path):
    """Read a .vf and hand back (blueprint info, views, dims, settings).

    Views are rebuilt through new_view(), so a project written by an older
    version gains whatever fields have been added since rather than arriving
    half formed.
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or data.get("kind") != PROJECT_KIND:
        raise SolveError(
            f"{os.path.basename(path)} is not a ViewForge project. A project "
            f"file is the one you saved with File > Save project, and ends "
            f"in {PROJECT_EXT}.")
    if int(data.get("version", 0)) > PROJECT_VERSION:
        raise SolveError(
            f"{os.path.basename(path)} was written by a newer version of "
            f"ViewForge (project format {data.get('version')}, this one reads "
            f"{PROJECT_VERSION}). Update ViewForge.")
    views = []
    for raw in data.get("views", []):
        v = new_view()
        for k in VIEW_KEYS:
            if k in raw:
                v[k] = raw[k]
        for k in ("x0", "x1", "y0", "y1"):
            v[k] = int(v[k])
        for k in ("gauge_h", "gauge_v"):
            v[k] = [int(t) for t in v[k]] if v.get(k) else None
        for k in ("centre_h", "centre_v"):
            v[k] = None if v.get(k) is None else float(v[k])
        if v["role"] not in ROLES:
            v["role"] = "ignore"
        views.append(v)
    return data.get("blueprint") or {}, views, data.get("dims") or {}, \
        data.get("settings") or {}


def find_blueprint(bp, project_path):
    """The blueprint a project refers to, wherever it has got to.

    Beside the project file first: a project and its drawing are usually moved
    together, and the absolute path recorded at save time is the one that goes
    stale.
    """
    here = os.path.dirname(os.path.abspath(project_path))
    tries = []
    if bp.get("relative"):
        tries.append(os.path.normpath(os.path.join(here, bp["relative"])))
    if bp.get("path"):
        tries.append(bp["path"])
        tries.append(os.path.join(here, os.path.basename(bp["path"])))
    for t in tries:
        if t and os.path.exists(t):
            return t, [t for t in dict.fromkeys(tries)]
    return None, list(dict.fromkeys(tries))

# Exporting
def auto_px_per_mm(rep, need_h, need_v, detail=1.0, margin=MARGIN):
    """Pick the output resolution from the drawing rather than asking for it.

    One output pixel per source pixel is the ceiling worth having; past that
    the export is just enlarging linework it never had.
    """
    src = [1.0 / r[k] for r in rep["views"] for k in ("mm_px_h", "mm_px_v")]
    p = float(np.median(src)) * float(detail)
    side_mm = 2.0 * max(need_h, need_v) * margin
    if side_mm > 0:
        p = min(p, CANVAS_MAX_PX / side_mm)
        p = max(p, CANVAS_MIN_PX / side_mm)
    return max(p, 1e-6)


def build_planes(views, ink, lab, dims, px_per_mm=None, margin=MARGIN,
                 mode="fit", prefix="", detail="normal", proportional=False,
                 src=None, info=None, style="auto"):
    """Render one image per assigned view onto one shared canvas.

    Returns (images, meta, rep) and writes nothing. images is {filename: PIL
    image}; meta is the same record export() writes, without an outdir. The 3D
    preview renders from this, so what it shows is what export() will write.
    """
    return _plan(views, ink, lab, dims, px_per_mm, margin, mode, prefix,
                 detail, proportional, src, info, style)


def export(views, ink, lab, dims, outdir, px_per_mm=None, margin=MARGIN,
           mode="fit", prefix="", detail="normal", proportional=False,
           src=None, info=None, style="auto"):
    """Write one PNG per assigned view onto one shared canvas.

    Every file shares that canvas, so all planes take the same Size and Offset
    in Blender and only rotation differs.

    mode 'fit'     scales each axis of each view to hit the stated dimensions
                   exactly, so views always agree.
    mode 'uniform' keeps one scale per view, so circles stay round and any
                   mismatch stays visible.

    px_per_mm=None works the resolution out from the drawing, nudged by detail
    ('draft', 'normal', 'fine', or a multiplier). Pass a number to override.

    src is the blueprint's own pixels, from read_image(). Given them, the planes
    are drawn as the drawing was drawn - style 'colour', 'tone', or 'auto' to
    pick between the two. Without them, or with style 'line', every mark
    flattens to solid black, which is only right for a pure line drawing.
    """
    images, meta, rep = build_planes(views, ink, lab, dims, px_per_mm, margin,
                                     mode, prefix, detail, proportional, src,
                                     info, style)
    os.makedirs(outdir, exist_ok=True)
    meta["outdir"] = os.path.abspath(outdir)
    for name, im in images.items():
        im.save(os.path.join(outdir, name))
    with open(os.path.join(outdir, "placement.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    with open(os.path.join(outdir, "viewforge_import.py"), "w",
              encoding="utf-8") as f:
        f.write(blender_script(meta))
    return meta, rep


def _plan(views, ink, lab, dims, px_per_mm, margin, mode, prefix, detail,
          proportional, src, info, style):
    if mode not in ("fit", "uniform"):
        raise SolveError(f"Unknown scaling mode '{mode}'. Use 'fit' or 'uniform'.")
    if isinstance(detail, str):
        if detail not in DETAIL:
            raise SolveError(f"Unknown detail '{detail}'. Use one of: "
                             + ", ".join(DETAIL))
        detail = DETAIL[detail]
    if not math.isfinite(margin) or margin < 1.0:
        raise SolveError(
            f"margin is {margin}. It is a multiplier on the canvas and must be "
            f"at least 1.0 - below that the canvas is smaller than the object "
            f"and the edges of your reference get cut off.")

    rep = solve(views, ink, lab, dims)
    L, W, H = dims.get("L", 0), dims.get("W", 0), dims.get("H", 0)

    # Scale first: the canvas must hold the widest view after centring, which
    # the nominal dimensions alone do not tell us.
    for r in rep["views"]:
        if mode == "uniform":
            r["kh"] = r["kv"] = math.sqrt(r["mm_px_h"] * r["mm_px_v"])
        else:
            r["kh"], r["kv"] = r["mm_px_h"], r["mm_px_v"]
        b = r["box"]
        r["reach_h"] = max(r["cen_h"] - b[0], b[1] - r["cen_h"]) * r["kh"]
        r["reach_v"] = max(r["cen_v"] - b[2], b[3] - r["cen_v"]) * r["kv"]
        # How far this plane is deliberately off, in mm. off_mm is stated
        # against the ink because that is what the self-check can measure, and
        # taking it back out leaves pure placement error. centre_mm is stated
        # against the box, because that is where the view would have sat had
        # you not put a centre line on it, and is the figure worth reading.
        r["off_h"] = (r["cen_h"] - r["ic_h"]) * r["kh"]
        r["off_v"] = (r["cen_v"] - r["ic_v"]) * r["kv"]
        r["ctr_h"] = (r["cen_h"] - r["bc_h"]) * r["kh"]
        r["ctr_v"] = (r["cen_v"] - r["bc_v"]) * r["kv"]

    # Never smaller than nominal, or the Blender numbers stop meaning what
    # they say.
    need_h = max([max(L, W) / 2.0] + [r["reach_h"] for r in rep["views"]])
    need_v = max([max(H, W) / 2.0] + [r["reach_v"] for r in rep["views"]])

    if px_per_mm is None:
        px_per_mm = auto_px_per_mm(rep, need_h, need_v, detail, margin)
    if not math.isfinite(px_per_mm) or px_per_mm <= 0:
        raise SolveError("px per mm must be a positive number.")

    CW = max(3, int(round(2 * need_h * margin * px_per_mm)))
    CH = max(3, int(round(2 * need_v * margin * px_per_mm)))
    # An odd canvas puts the origin in the middle of a pixel rather than on the
    # seam between two. Either is exactly symmetric, but only one of them can
    # be pointed at: zoom in on an odd canvas and the centre column of pixels
    # IS X=0, which is the line a mirror modifier folds about.
    CW += 1 - CW % 2
    CH += 1 - CH % 2
    grew = (CW > int(round(max(L, W) * margin * px_per_mm)) + 2 or
            CH > int(round(max(H, W) * margin * px_per_mm)) + 2)
    if grew:
        rep["warnings"].append(
            f"Canvas grown to {round(CW/px_per_mm, 1)} x {round(CH/px_per_mm, 1)} mm "
            f"so no view is cut off. Every plane still shares it, so the Blender "
            f"numbers below remain identical across all of them.")
    if src is None:
        pix, pmode = np.where(ink, 0, 255).astype(np.uint8), "L"
    else:
        pix, pmode = render_source(src, ink, info, style)
        if pix.shape[:2] != ink.shape:
            raise SolveError(
                "The blueprint pixels and the ink mask are different sizes. "
                "Reload the blueprint.")
    ih, iw = ink.shape
    blank = 255 if pmode == "L" else (255, 255, 255)

    written, seen, images = [], {}, {}
    for r in rep["views"]:
        v = r["_v"]
        mask, _, _, box = view_metrics(v, ink, lab)
        kx, ky = r["kh"] * px_per_mm, r["kv"] * px_per_mm
        dw, dh = round(r["span_h"] * kx), round(r["span_v"] * ky)
        if dw < 2 or dh < 2:
            raise SolveError(
                f"At {px_per_mm:.3f} px per mm the '{v['role']}' view comes out "
                f"{max(dw,0)} x {max(dh,0)} pixels, which is not an image. "
                f"Raise the detail setting.")

        # The slice of blueprint this canvas covers, in continuous source
        # coordinates, placed so the view's centre lands on the canvas's own
        # geometric centre. Nothing here is rounded, and that is the point:
        # cropping to whole pixels and pasting at a whole-pixel offset leaves a
        # perfectly symmetric object up to a pixel off its own mirror line, by
        # a different amount in every view, because the amount depends on where
        # the object's middle happens to fall between two pixels. Half a canvas
        # pixel is nothing to look at and everything to a mirror modifier.
        cs_h, cs_v = r["cen_h"] + 0.5, r["cen_v"] + 0.5
        sl, sr = cs_h - (CW / 2.0) / kx, cs_h + (CW / 2.0) / kx
        st, sb = cs_v - (CH / 2.0) / ky, cs_v + (CH / 2.0) / ky
        i0, j0 = int(math.floor(sl)), int(math.floor(st))
        i1, j1 = int(math.ceil(sr)), int(math.ceil(sb))

        # Paper everywhere this view does not own, so a neighbouring view or a
        # title block caught inside the same rectangle is not exported with it.
        shape = (j1 - j0, i1 - i0) + ((3,) if pmode == "RGB" else ())
        tile = np.full(shape, 255, np.uint8)
        ax0, ay0 = max(i0, 0), max(j0, 0)
        ax1, ay1 = min(i1, iw), min(j1, ih)
        if ax1 > ax0 and ay1 > ay0:
            m = mask[ay0:ay1, ax0:ax1]
            tile[ay0 - j0:ay1 - j0, ax0 - i0:ax1 - i0] = np.where(
                m[..., None] if pmode == "RGB" else m,
                pix[ay0:ay1, ax0:ax1], 255)
        canvas = Image.fromarray(tile, pmode).resize(
            (CW, CH), Image.LANCZOS,
            box=(sl - i0, st - j0, sr - i0, sb - j0))
        # Downscale far enough and the linework thins away to nothing. The file
        # still writes, blank, so catch it while we can still blame resolution.
        if np.asarray(canvas.convert("L")).min() >= 200:
            raise SolveError(
                f"At {px_per_mm:.3f} px per mm the '{v['role']}' view renders to "
                f"{dw} x {dh} pixels and its linework disappears. Raise the "
                f"detail setting.")
        if v["hflip"]:
            canvas = canvas.transpose(Image.FLIP_LEFT_RIGHT)

        stem = (f"{v['role']}_{v['facing']}" if v["role"] in FACING_ROLES
                else v["role"])
        seen[stem] = seen.get(stem, 0) + 1
        if seen[stem] > 1:
            stem = f"{stem}{seen[stem]}"
        name = f"{prefix}{stem}.png"
        images[name] = canvas
        # want_mm is the whole view at your scale; expect_mm is what this mode
        # should produce. Keeping both lets verify tell an export fault from
        # the drawing's own inconsistency.
        written.append(dict(file=name, role=v["role"], facing=v["facing"],
                            axes=[r["axis_h"], r["axis_v"]],
                            gauged=bool(r["gauged_h"] or r["gauged_v"]),
                            want_mm=[r["full_h"], r["full_v"]],
                            expect_mm=[r["span_h"] * r["kh"], r["span_v"] * r["kv"]],
                            off_mm=[r["off_h"], r["off_v"]],
                            centre_mm=[r["ctr_h"], r["ctr_v"]],
                            centred=[bool(r["set_h"]), bool(r["set_v"])]))

    meta = dict(canvas_px=[CW, CH],
                canvas_mm=[round(CW / px_per_mm, 2), round(CH / px_per_mm, 2)],
                px_per_mm=px_per_mm, mode=mode, margin=margin, dims=dims,
                detail=detail, proportional=bool(proportional),
                style=style, render=pmode, outdir=None, files=written)
    return images, meta, rep


# Verification
def file_axes(f):
    """Axes a written file was measured on. Stored per file, since a rotated
    view's axes do not follow from its role."""
    ax = f.get("axes")
    return (ax[0], ax[1]) if ax else ROLE_AXES[f["role"]]


def verify(outdir, meta):
    """Remeasure the finished files off disk. Does not trust export().

    Returns (text, worst_mm, problems). worst_mm is the export's own error,
    which stays near zero in 'uniform' mode too; deviation from the expected
    extent is reported separately because the two mean different things.
    """
    P = meta["px_per_mm"]
    CW, CH = meta["canvas_px"]
    # Placing a view rounds to whole pixels twice, so a pixel of slop is the
    # floor on what any export can manage. A fixed figure in mm would call a
    # coarse canvas broken, and means nothing at all in proportional mode.
    SLOP = 1.5 / P
    imgs, info = {}, {}
    for f in meta["files"]:
        a = np.asarray(Image.open(os.path.join(outdir, f["file"])).convert("L"))
        imgs[f["file"]] = a < 200
        info[f["file"]] = f

    problems = []
    lines, worst, worst_dim = ["measured extents (mm)"], 0.0, 0.0
    for fn, m in imgs.items():
        f = info[fn]
        ah, av = file_axes(f)
        ys, xs = np.nonzero(m)
        if len(xs) == 0:
            problems.append(f"{fn} came out completely blank.")
            lines.append(f"  {fn:24s} BLANK - no ink at all")
            worst = float("inf")
            continue
        want = f.get("want_mm", [meta["dims"].get(ah, 0), meta["dims"].get(av, 0)])
        exp = f.get("expect_mm", want)
        gh = (xs.max() - xs.min() + 1) / P
        gv = (ys.max() - ys.min() + 1) / P
        worst = max(worst, abs(gh - exp[0]), abs(gv - exp[1]))
        worst_dim = max(worst_dim, abs(gh - want[0]), abs(gv - want[1]))
        tag = "  (measure lines)" if f.get("gauged") else ""
        lines.append(f"  {fn:24s} {ah}={gh:8.2f} (want {want[0]:7.2f})   "
                     f"{av}={gv:8.2f} (want {want[1]:7.2f}){tag}")

        # A clipped view still reports an extent, just a short one, so ink on
        # the border needs its own check.
        if xs.min() == 0 or ys.min() == 0 or xs.max() == CW - 1 or ys.max() == CH - 1:
            problems.append(f"{fn} touches the canvas edge - it is being cut off.")

        # Did export put the view where solve asked? Intended offsets come
        # out, leaving placement error.
        off = f.get("off_mm", [0.0, 0.0])
        dh = ((xs.min() + xs.max()) / 2 - (CW - 1) / 2) / P + off[0]
        dv = ((ys.min() + ys.max()) / 2 - (CH - 1) / 2) / P + off[1]
        if max(abs(dh), abs(dv)) > max(SLOP, 0.002 * max(want)):
            problems.append(f"{fn} is off centre by ({dh:+.2f}, {dv:+.2f}) mm.")

        # Taking that offset out hides a centre line put in the wrong place,
        # since the view lands exactly where it was told. So say what was
        # intended, separately, and let it be read.
        ctr = f.get("centre_mm", [0.0, 0.0])
        for k, ax in ((0, ah), (1, av)):
            if f.get("centred", [False, False])[k] or abs(ctr[k]) > SLOP:
                lines.append(f"  {fn:24s} centred {ctr[k]:+8.2f} mm off the "
                             f"middle of its box on {ax}, on its centre line")
    lines.append(f"  worst deviation from the expected extent: {worst_dim:.2f} mm "
                 f"({worst_dim * P:.1f} canvas px)")

    def corr(a, b, axis, win_px):
        """Best lag within +/-win_px and how strong that peak is. The window
        matters: unbounded, this locks onto a big meaningless peak."""
        pa, pb = a.sum(axis=axis).astype(float), b.sum(axis=axis).astype(float)
        pa = (pa - pa.mean()) / (pa.std() + 1e-9)
        pb = (pb - pb.mean()) / (pb.std() + 1e-9)
        full = np.correlate(pa, pb, "full") / len(pa)
        zero = len(pb) - 1
        lo, hi = max(0, zero - win_px), min(len(full), zero + win_px + 1)
        seg = full[lo:hi]
        k = int(np.argmax(seg))
        return (lo + k - zero) / P, float(seg[k])

    # Advisory only. Two views can share an axis and still have very different
    # ink profiles, in which case this says so rather than guessing.
    lines.append("internal drift (advisory - extents above are the real test)")
    names = [n for n in imgs if imgs[n].any()]
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            fa, fb = file_axes(info[a]), file_axes(info[b])
            for axa, axb, ax in ((fa[0], fb[0], 0), (fa[1], fb[1], 1)):
                if axa != axb:
                    continue
                win = int(round(max(5.0, 0.02 * meta["dims"][axa]) * P))
                ia, ib = imgs[a], imgs[b]
                mir = ib[:, ::-1] if ax == 0 else ib[::-1, :]
                d0, p0 = corr(ia, ib, ax, win)
                d1, p1 = corr(ia, mir, ax, win)
                use, peak, note = ((d0, p0, "") if p0 >= p1
                                   else (d1, p1, " (mirrored)"))
                if peak < 0.50:
                    lines.append(f"  {axa}  {a} vs {b}: no common structure "
                                 f"to compare (peak {peak:.2f})")
                    continue
                tol = max(1.0, 0.005 * meta["dims"][axa])
                flag = "" if abs(use) <= tol else "   <-- worth a look"
                lines.append(f"  {axa}  {a} vs {b}: {use:+.2f} mm{note}{flag}")
    if problems:
        lines = (["PROBLEMS"] + [f"  - {p}" for p in problems] + [""]) + lines
    return "\n".join(lines), worst, problems


# Blender script generation
# Size sets an image empty's LARGER pixel dimension, not both. Measured in
# Blender 5.2: a 636x1272 empty at size 0.5 through a 1.0-unit ortho camera came
# out 99x199 px in a 400 px frame, not 200x400.

SCRIPT_HEAD = '''"""
Generated by ViewForge. Builds the reference planes for this export.

Run it one of two ways:
  - Blender: Scripting tab > Open > pick this file > Run Script
  - Terminal: blender --python viewforge_import.py

It looks for the PNGs in its own folder, so keep this file with them. Running
it twice is safe: it clears out what it made last time first, rather than
stacking a second set of planes on top of the first.
"""
import bpy, os, math

# ------------------------------------------------------------- settings ----
OPACITY   = 0.5          # 0 = invisible, 1 = solid
DEPTH     = 'BACK'       # 'BACK' draws behind your model, 'DEFAULT' respects depth
SIDE      = 'FRONT'      # 'FRONT' hides the plane from behind, 'DOUBLE_SIDED' shows both
LOCK      = True         # stop the planes being dragged around by accident
AXIS_ONLY = False        # True = each plane only appears in its own ortho view
'''


def placement_key(f):
    return (f["role"], f["facing"] if f["role"] in FACING_ROLES else "n/a")


def blender_script(meta):
    """Emit a standalone .py that builds the planes in Blender. Everything is
    baked in, so it needs the PNGs beside it and nothing else."""
    CW, CH = meta["canvas_px"]
    cw_mm, ch_mm = meta["canvas_mm"]
    size_m = max(cw_mm, ch_mm) / 1000.0
    d = meta["dims"]
    L, W, H = d.get("L", 0), d.get("W", 0), d.get("H", 0)
    push = max(L, W, H) * 0.75 / 1000.0

    rows = []
    for f in meta["files"]:
        b = BLENDER.get(placement_key(f))
        if b is None:
            continue
        ax = "XYZ".index(b["normal"][1])
        loc = [0.0, 0.0, 0.0]
        loc[ax] = push if b["normal"][0] == "+" else -push
        rows.append("    dict(file=%r, rot=(%d, %d, %d), loc=(%.6f, %.6f, %.6f), "
                    "key=%r)," % (f["file"], b["rot"][0], b["rot"][1], b["rot"][2],
                                  loc[0], loc[1], loc[2], b["key"]))

    body = f'''
# ---------------------------------------------------- generated numbers ----
SIZE       = {size_m:.6f}          # metres, the canvas's longer side
OFFSET     = (-0.5, -0.5)
COLLECTION = "viewforge"
CLIP_START = {0.001 if max(L, W, H) < 500 else 0.01:.4f}
FALLBACK   = r"{os.path.abspath(meta.get('outdir', '.'))}"

PLANES = [
{chr(10).join(rows)}
]


def image_dir():
    """Find the PNGs. Beside this script first, wherever it now lives."""
    seen = []
    try:
        seen.append(os.path.dirname(os.path.abspath(__file__)))
    except NameError:            # running from a pasted text block
        pass
    for t in bpy.data.texts:     # ...or a text block saved to disk
        if t.filepath:
            seen.append(os.path.dirname(bpy.path.abspath(t.filepath)))
    if bpy.data.filepath:
        seen.append(os.path.dirname(bpy.data.filepath))
    seen.append(FALLBACK)
    for c in seen:
        if c and all(os.path.exists(os.path.join(c, p["file"])) for p in PLANES):
            return c
    raise RuntimeError(
        "Cannot find the plane PNGs. Keep this script in the same folder as "
        "them, or edit FALLBACK at the top to point at that folder. Looked in:"
        "\\n  " + "\\n  ".join(dict.fromkeys(seen)))


def build():
    src = image_dir()

    # Clear a previous run rather than stacking a second set on top. Mixing two
    # exports is the classic way to end up with planes that do not line up.
    old = bpy.data.collections.get(COLLECTION)
    if old:
        for ob in list(old.objects):
            bpy.data.objects.remove(ob, do_unlink=True)
        bpy.data.collections.remove(old)
    col = bpy.data.collections.new(COLLECTION)
    bpy.context.scene.collection.children.link(col)

    made = []
    for p in PLANES:
        path = os.path.join(src, p["file"])
        img = bpy.data.images.load(path, check_existing=True)
        img.reload()                       # pick up a fresh re-export

        ob = bpy.data.objects.new("viewforge_" + os.path.splitext(p["file"])[0], None)
        col.objects.link(ob)
        ob.empty_display_type = 'IMAGE'
        ob.data = img
        ob.empty_display_size = SIZE
        ob.empty_image_offset = OFFSET
        ob.empty_image_depth = DEPTH
        ob.empty_image_side = SIDE
        ob.use_empty_image_alpha = True
        ob.color[3] = OPACITY
        ob.show_empty_image_only_axis_aligned = AXIS_ONLY
        ob.location = p["loc"]
        ob.rotation_euler = tuple(math.radians(a) for a in p["rot"])
        if LOCK:
            ob.lock_location = ob.lock_rotation = ob.lock_scale = (True,) * 3

        # Say so rather than leaving you to find it in the sidebar. Both of
        # these only stick on an empty that is already display type IMAGE with
        # an image assigned, which is why they are set after ob.data above.
        got = (ob.empty_image_depth, ob.empty_image_side)
        if got != (DEPTH, SIDE):
            print("viewforge: WARNING %s kept depth=%s side=%s, not %s/%s"
                  % (ob.name, got[0], got[1], DEPTH, SIDE))
        made.append((ob.name, p["key"]))

    for area in getattr(bpy.context.screen, "areas", []):
        if area.type == 'VIEW_3D':
            area.spaces.active.clip_start = CLIP_START

    print("viewforge: built %d planes from %s" % (len(made), src))
    for name, key in made:
        print("   %-34s %s" % (name, key))
    print("   Size %.4f on every plane. Press Numpad 5 - none of this holds "
          "in perspective." % SIZE)
    return made


if __name__ == "__main__":
    build()
'''
    return SCRIPT_HEAD + body


# Where a plane sits in space
# Everything here is in millimetres, in the same frame the Blender script uses:
# length on Y with the front at -Y, width on X, height on Z, origin at the
# centre of the object.

def _rot(rx, ry, rz):
    """Blender's XYZ euler as a matrix: Rz @ Ry @ Rx, degrees in."""
    cx, sx = math.cos(math.radians(rx)), math.sin(math.radians(rx))
    cy, sy = math.cos(math.radians(ry)), math.sin(math.radians(ry))
    cz, sz = math.cos(math.radians(rz)), math.sin(math.radians(rz))
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]], float)
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]], float)
    Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]], float)
    return Rz @ Ry @ Rx


def plane_basis(f, meta, push=0.0):
    """How one exported plane is oriented and where it sits.

    Returns a dict with the plane's centre, the world directions its image's
    right and up run along, its normal, and its half extents in mm. An image
    empty lies in its own XY plane with the image's right along +X and its top
    along +Y, so those are just the first two columns of the rotation.

    push moves the plane back along its own normal, which is what the export's
    Blender script does so the planes do not fight with your model. It makes no
    difference to anything measured: sighting down a plane's normal, a point
    lands in the same place whatever the offset.
    """
    b = BLENDER.get(placement_key(f))
    if b is None:
        return None
    R = _rot(*b["rot"])
    cw, ch = meta["canvas_mm"]
    ax = "XYZ".index(b["normal"][1])
    centre = np.zeros(3)
    centre[ax] = push if b["normal"][0] == "+" else -push
    return dict(file=f["file"], role=f["role"], facing=f["facing"],
                centre=centre, right=R[:, 0], up=R[:, 1], normal=R[:, 2],
                half=(cw / 2.0, ch / 2.0), key=b["key"], rot=b["rot"])


def plane_corners(basis):
    """The plane's four corners, in image order: top left, top right, bottom
    right, bottom left. PIL's transform wants them that way round."""
    c, r, u = basis["centre"], basis["right"], basis["up"]
    hw, hh = basis["half"]
    return [c - r * hw + u * hh, c + r * hw + u * hh,
            c + r * hw - u * hh, c - r * hw - u * hh]


def project_onto_plane(point, basis, meta):
    """Where a point in space lands on a plane, as a pixel in its image.

    Sighted straight down the plane's normal, which is how you look at a
    reference plane in an orthographic view - and the only way the question has
    an answer at all.
    """
    d = np.asarray(point, float) - basis["centre"]
    u = float(np.dot(d, basis["right"]))
    v = float(np.dot(d, basis["up"]))
    p = meta["px_per_mm"]
    CW, CH = meta["canvas_px"]
    return ((CW - 1) / 2.0 + u * p, (CH - 1) / 2.0 - v * p, u, v)

# Blender report
def blender_report(meta):
    CW, CH = meta["canvas_px"]
    cw_mm, ch_mm = meta["canvas_mm"]
    size_m = max(cw_mm, ch_mm) / 1000.0
    d = meta["dims"]
    L, W, H = d.get("L", 0), d.get("W", 0), d.get("H", 0)
    push = max(L, W, H) * 0.75 / 1000.0
    o = ["BLENDER SETUP",
         f"  canvas   {CW} x {CH} px  =  {cw_mm} x {ch_mm} mm "
         f"at {meta['px_per_mm']:.3f} px/mm",
         f"  origin   the middle pixel, column {(CW - 1) // 2} row "
         f"{(CH - 1) // 2}. Both sides are odd on purpose, so X=0",
         f"           is the centre of a pixel and not the seam between two.",
         ""]
    if meta.get("proportional"):
        o += ["  PROPORTIONAL EXPORT. These millimetres are not measurements.",
              f"  The drawing's own proportions were kept and the object scaled",
              f"  so its largest side is {max(L, W, H):.0f} mm. Everything lines up",
              "  and is in proportion; nothing here is a real world size.", ""]
    o += ["  DO IT FOR ME:  viewforge_import.py, written beside these planes.",
          "     Blender > Scripting > Open > Run Script, and it builds all of",
          "     them. Everything below is the same job done by hand.", "",
          "  Add > Empty > Image, one per file. Set these on EVERY empty,",
          "  identically, only rotation differs:",
          f"      Size      {size_m:.4f}",
          "      Offset X  -0.5        Offset Y  -0.5",
          "      Depth     Back        Side  Front       Opacity  0.5", "",
          f"  {'file':24s} {'RotX':>5s} {'RotY':>5s} {'RotZ':>5s}  "
          f"{'location':13s} {'view key':14s}"]
    for f in meta["files"]:
        b = BLENDER.get(placement_key(f))
        if b is None:
            o.append(f"  {f['file']:24s}  ?  no placement for role "
                     f"'{f['role']}' facing '{f['facing']}'")
            continue
        o.append(f"  {f['file']:24s} {b['rot'][0]:5d} {b['rot'][1]:5d} "
                 f"{b['rot'][2]:5d}  {b['normal'][1]} = {b['normal'][0]}{push:.3f}"
                 f"    {b['key']:14s}")
    o += ["", "CHECK IN THE VIEWPORT   (object centred on the world origin)"]
    if L:
        o.append(f"  front  Y = {-L/2000:+.4f}        back    Y = {L/2000:+.4f}")
    if H:
        o.append(f"  top    Z = {H/2000:+.4f}        bottom  Z = {-H/2000:+.4f}")
    if W:
        o.append(f"  sides  X = +/-{W/2000:.4f}")
    o.append("  Press Numpad 5 first - none of this holds in perspective.")
    if max(L, W, H) < 500:
        o += ["  Small object: set View > Clip Start to 0.001 or you will clip",
              "  through your own geometry when you zoom in."]
    return "\n".join(o)
