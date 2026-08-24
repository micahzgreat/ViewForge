"""
Two ways to define a view:

  source='component'  Auto detected. The view is one connected blob of ink and
                      is rendered from that blob ALONE, so a detail inset or a
                      neighbouring view sitting in its whitespace cannot leak
                      in. Its measured span is the blob's own ink extents.

  source='box'        You draw the rectangle, and the rectangle IS the
                      measurement: its edges sit on the object's extremes. Ink
                      outside is clipped. Use this on blueprints where dimension
                      lines join the views together, which defeats auto detect.

Either kind can carry MEASURE LINES: a pair of guides on the horizontal axis,
the vertical axis, or both. Without them the view's own span is what your stated
dimension refers to. With them the dimension is taken between the two lines
instead, which is how you handle a drawing whose figure is measured between two
points that are not the object's extremes - a shoulder, a bore centre, a face
set back from the silhouette. The rendered plane still shows the whole view; only
the scale changes.
"""

from __future__ import annotations
import json, math, os
import numpy as np
from PIL import Image
from scipy import ndimage

# Each view shows two of the object's three dimensions: 'h' is the image's
# horizontal axis, 'v' the vertical. L=length, W=width, H=height. This is the
# mapping for a view drawn the right way up; see view_axes() for the rest.
ROLE_AXES = {
    "side":   ("L", "H"),
    "top":    ("L", "W"),
    "bottom": ("L", "W"),
    "front":  ("W", "H"),
    "back":   ("W", "H"),
}
ROLES = ["ignore"] + list(ROLE_AXES)

# Roles where 'front of object is at' is a real question. On front and back the
# object's front faces the viewer, so the answer is neither left nor right and
# the control is not offered; those views are stored as 'n/a'.
FACING_ROLES = ("side", "top", "bottom")
FACINGS = ["left", "right", "up", "down"]

# Blender placement. Convention: length on Y with the object's FRONT at -Y,
# width on X, height on Z, bounding box centred on the world origin.
# 'facing' = which edge of the image the object's front sits against. 'up' and
# 'down' are views drawn a quarter turn round, which also swaps which of the
# object's dimensions runs across the image - that part is view_axes()'s job.
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

MARGIN = 1.06            # canvas multiplier. Not a knob any more: it is small
                         # enough not to waste resolution and the canvas grows
                         # on its own whenever a view would otherwise be cut off.

# Output resolution is worked out from the drawing instead of being typed in.
# 'normal' keeps roughly one output pixel per source pixel, which is the most a
# blueprint can actually justify; the canvas is then held inside these bounds so
# a big blueprint cannot produce a 20000 px plane and a tiny object cannot make a
# 60 px one.
DETAIL = {"draft": 0.5, "normal": 1.0, "fine": 2.0}
CANVAS_MAX_PX = 6000
CANVAS_MIN_PX = 500

# In proportional mode nothing is measured in the real world: the drawing's own
# proportions are kept and the object is sized so its largest side is this.
PROPORTIONAL_MM = 1000.0


class SolveError(ValueError):
    """Something the user can fix. The message says how."""


# Blueprint i/o and auto-detect
def load_blueprint(path, threshold=None):
    g = np.asarray(Image.open(path).convert("L")).astype(np.int16)
    if threshold is None:
        paper = np.percentile(g, 90)
        threshold = int(max(60, min(240, paper - 25)))
    return g, (g < threshold), threshold


def new_view(**kw):
    v = dict(cid=None, source="box", x0=0, x1=10, y0=0, y1=10,
             role="ignore", facing="left", hflip=False,
             gauge_h=None, gauge_v=None)
    v.update(kw)
    return v


def view_axes(v):
    """Which of the object's dimensions run across and down this view's image.

    A view drawn a quarter turn round - facing 'up' or 'down' - has the two
    swapped, so this cannot be read off the role alone. Everything downstream
    asks here rather than indexing ROLE_AXES, or a rotated view would be
    measured against the wrong figure and still look plausible.
    """
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
        # Size filter on the region's OWN ink, not on the dilated slice, which
        # is inflated by `gap` and would let specks through at a wide setting.
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
    """Put a hand drawn box in order and inside the blueprint.

    numpy slices silently, so an x1 typed past the right hand edge would trim
    the MASK but leave the reported span at the typed value, handing solve() a
    scale several times too small with nothing on screen to reveal it. The box
    is the measurement, so it has to be a box that actually exists.
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
    """How many pixels the stated dimension covers, on one axis.

    Without measure lines that is the whole view: the box, or the blob's ink,
    IS the measurement. With them it is the distance between the two lines, so
    a figure taken between two points inside the silhouette scales the drawing
    correctly instead of being stretched onto its extremes.

    Returns (pixels, whether measure lines were used).
    """
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
    """True when a measure line sits outside the view it belongs to.

    Legal - a dimension can be taken to a centre line drawn past the edge - but
    it is far more often a line left behind after the box was moved, so it is
    worth saying out loud.
    """
    g = v.get("gauge_h" if axis == 0 else "gauge_v")
    if not g:
        return False
    lo, hi = (box[0], box[1]) if axis == 0 else (box[2], box[3])
    return any(t < lo - 1 or t > hi + 1 for t in g[:2])


SYM_BAND = 0.05      # axis may sit this far from the bbox midpoint, as a
                     # fraction of the view's span
SYM_GAIN = 0.05      # and only if it beats the midpoint by this much


def symmetry_axis(mask, box, axis):
    """Locate a view's mirror line. axis=0 -> horizontal, 1 -> vertical.

    Three things keep this honest. The candidate axis is confined to a narrow
    band around the bounding box midpoint, because a symmetric object's mirror
    line cannot be far from its own centre. Every candidate is scored over the
    SAME comparison width, so a near edge axis with only a few overlapping
    samples cannot win by comparing almost nothing. And the winner has to beat
    the plain midpoint by SYM_GAIN, so a near tie on noise leaves the midpoint
    alone. The search band and the accept threshold are deliberately the same
    number, or the outer part of the search would be unreachable.
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
    if bestd > d_mid * (1.0 - SYM_GAIN):
        best = mid
    return base + best


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
    """Dimensions taken from the drawing itself, for when real sizes do not matter.

    One scale for the whole blueprint, so what comes out is the drawing's own
    proportions; the object is then sized so its largest side is `size_mm`.
    Views that measure the same axis are combined with a median, so one badly
    placed box shifts the result less than it would if any single view were
    trusted. Measure lines still apply, so a gauged view contributes the span
    between its lines rather than its full width.

    Nothing here is a real measurement, and the report says so.
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
        # The whole view in mm, which is what the exported plane will measure.
        # Equal to the dimensions you typed unless measure lines are in use.
        rec["full_h"] = sh_px * rec["mm_px_h"]
        rec["full_v"] = sv_px * rec["mm_px_v"]
        # Centre on the mirror line wherever the object is symmetric about it.
        # That is the W axis - the object is mirrored about its own centre
        # plane - so this follows the axis, not the image, and stays right on a
        # view drawn a quarter turn round.
        if ah == "W":
            rec["cen_h"] = symmetry_axis(mask, box, 1)
            rec["cen_v"] = (box[2] + box[3]) / 2
        elif av == "W":
            rec["cen_h"] = (box[0] + box[1]) / 2
            rec["cen_v"] = symmetry_axis(mask, box, 0)
        else:
            rec["cen_h"] = (box[0] + box[1]) / 2
            rec["cen_v"] = (box[2] + box[3]) / 2
        # How far centring moved the view off its own bounding-box midpoint,
        # so export can size the canvas around it and verify can check it.
        rec["off_h"] = (rec["cen_h"] - (box[0] + box[1]) / 2) * rec["mm_px_h"]
        rec["off_v"] = (rec["cen_v"] - (box[2] + box[3]) / 2) * rec["mm_px_v"]
        rec["_v"] = v
        report["views"].append(rec)

        for axis, ax in ((0, ah), (1, av)):
            if gauge_outside(v, box, axis, ink.shape):
                report["warnings"].append(
                    f"{v['role']}: a measure line for {ax} sits outside the "
                    f"view's own box. That is allowed, but check it is not a "
                    f"line left behind after the box was moved.")

    for r in report["views"]:
        for ax, off in (("h", r["off_h"]), ("v", r["off_v"])):
            lim = 0.02 * dims[r["axis_" + ax]]
            if abs(off) > lim:
                report["warnings"].append(
                    f"{r['role']}: centred {abs(off):.1f} mm off its bounding-box "
                    f"midpoint, on the {r['axis_' + ax]} axis. That is the mirror "
                    f"line this view was drawn about. If the view is not actually "
                    f"symmetric, this shifts it against the others.")
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


# Exporting
def auto_px_per_mm(rep, need_h, need_v, detail=1.0, margin=MARGIN):
    """Pick the output resolution from the drawing instead of asking for it.

    One output pixel per source pixel is the ceiling on what a blueprint can
    actually justify; past that the export is enlarging linework it does not
    have. The median across views is used so one small inset does not drag the
    whole export down, and the result is then held inside CANVAS_MIN_PX and
    CANVAS_MAX_PX so neither a wall-sized drawing nor a thumbnail-sized object
    produces an unusable canvas.
    """
    src = [1.0 / r[k] for r in rep["views"] for k in ("mm_px_h", "mm_px_v")]
    p = float(np.median(src)) * float(detail)
    side_mm = 2.0 * max(need_h, need_v) * margin
    if side_mm > 0:
        p = min(p, CANVAS_MAX_PX / side_mm)
        p = max(p, CANVAS_MIN_PX / side_mm)
    return max(p, 1e-6)


def export(views, ink, lab, dims, outdir, px_per_mm=None, margin=MARGIN,
           mode="fit", prefix="", detail="normal", proportional=False):
    """Write one PNG per assigned view onto one shared canvas.

    Every file gets the same canvas with the object centred in it, so all
    planes take identical Size and Offset values in Blender and only the
    rotation differs.

    mode 'fit'     - scale each axis of each view independently so it hits the
                     stated dimensions exactly. Views always agree; a skewed
                     drawing gets skewed to match.
    mode 'uniform' - one scale per view. Keeps circles round and leaves any
                     mismatch visible instead of hiding it.

    px_per_mm defaults to None, meaning work it out from the drawing; `detail`
    ('draft', 'normal', 'fine', or a plain multiplier) nudges that up or down.
    Pass a number to px_per_mm to override both.
    """
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

    # Scale per view first: the canvas has to be big enough to hold the widest
    # one AFTER centring. Sizing it from the nominal dimensions alone assumes
    # every view sits on its own midpoint, which stops being true once a view is
    # centred on its mirror line instead.
    for r in rep["views"]:
        if mode == "uniform":
            r["kh"] = r["kv"] = math.sqrt(r["mm_px_h"] * r["mm_px_v"])
        else:
            r["kh"], r["kv"] = r["mm_px_h"], r["mm_px_v"]
        b = r["box"]
        r["reach_h"] = max(r["cen_h"] - b[0], b[1] - r["cen_h"]) * r["kh"]
        r["reach_v"] = max(r["cen_v"] - b[2], b[3] - r["cen_v"]) * r["kv"]

    # Never smaller than the nominal canvas, so Size and the viewport numbers
    # in the Blender report keep meaning what they say.
    need_h = max([max(L, W) / 2.0] + [r["reach_h"] for r in rep["views"]])
    need_v = max([max(H, W) / 2.0] + [r["reach_v"] for r in rep["views"]])

    if px_per_mm is None:
        px_per_mm = auto_px_per_mm(rep, need_h, need_v, detail, margin)
    if not math.isfinite(px_per_mm) or px_per_mm <= 0:
        raise SolveError("px per mm must be a positive number.")

    CW = max(2, int(round(2 * need_h * margin * px_per_mm)))
    CH = max(2, int(round(2 * need_v * margin * px_per_mm)))
    grew = (CW > int(round(max(L, W) * margin * px_per_mm)) + 1 or
            CH > int(round(max(H, W) * margin * px_per_mm)) + 1)
    if grew:
        rep["warnings"].append(
            f"Canvas grown to {round(CW/px_per_mm, 1)} x {round(CH/px_per_mm, 1)} mm "
            f"so no view is cut off. Every plane still shares it, so the Blender "
            f"numbers below remain identical across all of them.")
    cx, cy = (CW - 1) / 2.0, (CH - 1) / 2.0

    os.makedirs(outdir, exist_ok=True)
    written, seen = [], {}
    for r in rep["views"]:
        v = r["_v"]
        mask, _, _, box = view_metrics(v, ink, lab)
        img = Image.fromarray(np.where(mask, 0, 255).astype(np.uint8), "L")
        kh, kv = r["kh"] * px_per_mm, r["kv"] * px_per_mm

        if v["source"] == "component":
            pad = 40
            x0 = max(0, box[0] - pad); x1 = min(img.width - 1,  box[1] + pad)
            y0 = max(0, box[2] - pad); y1 = min(img.height - 1, box[3] + pad)
        else:
            x0, x1, y0, y1 = box

        dw, dh = round((x1 - x0 + 1) * kh), round((y1 - y0 + 1) * kv)
        if dw < 2 or dh < 2:
            raise SolveError(
                f"At {px_per_mm:.3f} px per mm the '{v['role']}' view comes out "
                f"{max(dw,0)} x {max(dh,0)} pixels, which is not an image. "
                f"Raise the detail setting.")
        reg = img.crop((x0, y0, x1 + 1, y1 + 1)).resize((dw, dh), Image.LANCZOS)
        # Downscaling far enough thins the linework until nothing survives the
        # ink threshold. The file still writes, and it is blank, so catch it
        # here, where the resolution can actually be named as the cause.
        if np.asarray(reg).min() >= 200:
            raise SolveError(
                f"At {px_per_mm:.3f} px per mm the '{v['role']}' view renders to "
                f"{dw} x {dh} pixels and its linework disappears. Raise the "
                f"detail setting.")
        canvas = Image.new("L", (CW, CH), 255)
        # PIL maps the crop's full width onto dw, so step through dw/src
        # rather than kh; at small scales the two differ by enough to see.
        sh, sv = dw / (x1 - x0 + 1), dh / (y1 - y0 + 1)
        canvas.paste(reg, (round(cx - (r["cen_h"] - x0) * sh),
                           round(cy - (r["cen_v"] - y0) * sv)))
        if v["hflip"]:
            canvas = canvas.transpose(Image.FLIP_LEFT_RIGHT)

        stem = (f"{v['role']}_{v['facing']}" if v["role"] in FACING_ROLES
                else v["role"])
        seen[stem] = seen.get(stem, 0) + 1
        if seen[stem] > 1:
            stem = f"{stem}{seen[stem]}"
        name = f"{prefix}{stem}.png"
        canvas.save(os.path.join(outdir, name))
        # want_mm is the whole view at the scale your figures set - the same as
        # the figures themselves unless measure lines are in use, where the view
        # runs past them on purpose. expect_mm is what THIS mode should produce:
        # equal to want_mm in 'fit', different by the view's skew in 'uniform'.
        # Keeping both lets verify separate an export fault from the drawing's
        # own inconsistency.
        written.append(dict(file=name, role=v["role"], facing=v["facing"],
                            axes=[r["axis_h"], r["axis_v"]],
                            gauged=bool(r["gauged_h"] or r["gauged_v"]),
                            want_mm=[r["full_h"], r["full_v"]],
                            expect_mm=[r["span_h"] * r["kh"], r["span_v"] * r["kv"]],
                            off_mm=[r["off_h"], r["off_v"]]))

    meta = dict(canvas_px=[CW, CH],
                canvas_mm=[round(CW / px_per_mm, 2), round(CH / px_per_mm, 2)],
                px_per_mm=px_per_mm, mode=mode, margin=margin, dims=dims,
                detail=detail, proportional=bool(proportional),
                outdir=os.path.abspath(outdir), files=written)
    with open(os.path.join(outdir, "placement.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    with open(os.path.join(outdir, "viewforge_import.py"), "w",
              encoding="utf-8") as f:
        f.write(blender_script(meta))
    return meta, rep


# Verification
def file_axes(f):
    """The axes a written file was measured on. Stored per file, because a
    rotated view's axes do not follow from its role."""
    ax = f.get("axes")
    return (ax[0], ax[1]) if ax else ROLE_AXES[f["role"]]


def verify(outdir, meta):
    """Remeasure the finished files off disk. Does not trust export().

    Returns (text, worst_mm, problems). worst_mm is the export's own error, how
    far each written file is from what this mode set out to produce, so it stays
    near zero in 'uniform' mode too. Deviation from the expected extent is
    reported separately, because the two mean different things.
    """
    P = meta["px_per_mm"]
    CW, CH = meta["canvas_px"]
    # Tolerances are a canvas pixel and a half, converted to mm, rather than a
    # fixed fraction of one. Placing a view involves two roundings to whole
    # pixels, so a pixel of slop is the floor on what any export can achieve -
    # and a hard 0.5 mm floor calls that a fault on a coarse canvas while
    # letting real drift through on a fine one. In proportional mode, where the
    # millimetres are arbitrary, a figure in mm means nothing at all.
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

        # Ink on the border means the canvas cut the view off. A clipped view
        # still reports an extent, just a short one, so this needs its own check.
        if xs.min() == 0 or ys.min() == 0 or xs.max() == CW - 1 or ys.max() == CH - 1:
            problems.append(f"{fn} touches the canvas edge - it is being cut off.")

        # Did export put the view where solve asked? Deliberate centring
        # offsets are subtracted, so what is left is placement error.
        off = f.get("off_mm", [0.0, 0.0])
        dh = ((xs.min() + xs.max()) / 2 - (CW - 1) / 2) / P + off[0]
        dv = ((ys.min() + ys.max()) / 2 - (CH - 1) / 2) / P + off[1]
        if max(abs(dh), abs(dv)) > max(SLOP, 0.002 * max(want)):
            problems.append(f"{fn} is off centre by ({dh:+.2f}, {dv:+.2f}) mm.")

        # And was that what solve should have asked for? Subtracting the
        # intended offset above hides a wrong mirror line, because the view
        # lands exactly where it was told to, so the size of the intention is
        # checked on its own.
        for k, ax in ((0, ah), (1, av)):
            if abs(off[k]) > max(SLOP, 0.02 * want[k]):
                problems.append(
                    f"{fn} was centred {off[k]:+.2f} mm off its bounding-box "
                    f"midpoint on {ax}, to sit on the mirror line found in the "
                    f"drawing. Correct only if this view really is symmetric "
                    f"about that line - check it against the others.")
    lines.append(f"  worst deviation from the expected extent: {worst_dim:.2f} mm "
                 f"({worst_dim * P:.1f} canvas px)")

    def corr(a, b, axis, win_px):
        """Best lag within +/-win_px and how strong that peak is.

        The window matters: an unbounded search locks onto a strong but
        meaningless peak far from zero. Gross errors are already caught by the
        extents check, so only small lags are of interest.
        """
        pa, pb = a.sum(axis=axis).astype(float), b.sum(axis=axis).astype(float)
        pa = (pa - pa.mean()) / (pa.std() + 1e-9)
        pb = (pb - pb.mean()) / (pb.std() + 1e-9)
        full = np.correlate(pa, pb, "full") / len(pa)
        zero = len(pb) - 1
        lo, hi = max(0, zero - win_px), min(len(full), zero + win_px + 1)
        seg = full[lo:hi]
        k = int(np.argmax(seg))
        return (lo + k - zero) / P, float(seg[k])

    # The extents check above is the authoritative one. What follows looks for
    # small internal drift and is advisory: two views can share an axis and
    # still have quite different ink profiles, in which case it says so rather
    # than guessing.
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
# What Size means, measured rather than assumed: an image empty is drawn with
# its LARGER pixel dimension equal to empty_display_size, the smaller one scaled
# by the aspect ratio. Checked in Blender 5.2 by rendering a 636x1272 empty at
# size 0.5 through a 1.0-unit ortho camera: 99 x 199 px in a 400 px frame, not
# 200 x 400. Offset -0.5,-0.5 then centres it on the object origin.

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
    """Emit a standalone .py that builds the planes in Blender.

    Everything it needs is baked in, so it does not import viewforge and does
    not need the blueprint, just the PNGs sitting beside it.
    """
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
         f"at {meta['px_per_mm']:.3f} px/mm", ""]
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
