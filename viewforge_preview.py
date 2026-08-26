#!/usr/bin/env python3
"""
ViewForge 3D preview. Puts the planes you are about to export into space and
lets you walk round them, so you can see whether they line up before Blender
tells you they do not.

It renders from build_planes(), the same call export() makes, so what you see
here is what gets written.

The probe is the point of the thing. Drop it on a feature - a wheel centre, a
door line, the top of the roof - and every plane reports where that point lands
on it, magnified. If the drawing is consistent the same feature is under every
crosshair. If it is not, you can see by how much, and on which axis, without
having built anything.
"""

import math
import os
import tkinter as tk
import customtkinter as ctk
import numpy as np
from PIL import Image, ImageTk, ImageDraw
import viewforge_core as core

BG_PANEL = "#202226"
BG_CARD  = "#26282d"
BG_SUNK  = "#141517"
VIEW_BG  = "#1b1c1e"
FG_MUTED = "#8b919b"
ACCENT   = "#3b82f6"
ACCENT_H = "#2f6fd0"
COL_PROBE = "#ff5f5f"
COL_BOX   = "#6b7280"
COL_MIRROR = "#2fd4d4"
AXIS_COL  = {"X": "#e05555", "Y": "#63c264", "Z": "#5b8dd9"}

MONO   = ("Consolas", 11)
UI     = ("Segoe UI", 12)
UI_SM  = ("Segoe UI", 11)
UI_B   = ("Segoe UI", 13, "bold")
UI_CAP = ("Segoe UI", 11, "bold")

VIEW_W, VIEW_H = 760, 560
INSET = 112              # side of one probe inset, in screen pixels
TEX_MAX = 1100           # a plane's texture is resampled down to this to orbit
DRAG_Q = 0.55            # render this much smaller while the mouse is down


def look(az, el):
    """Unit vector from the target out to the eye. Azimuth 0 looks up the -Y
    axis, which is the front of the object."""
    a, e = math.radians(az), math.radians(el)
    return np.array([math.sin(a) * math.cos(e),
                     -math.cos(a) * math.cos(e),
                     math.sin(e)], float)


class Camera:
    """Orbit camera. Orthographic by default, because reference planes only
    line up in an orthographic view and pretending otherwise here would teach
    the wrong lesson."""

    def __init__(self, span=1000.0):
        self.az, self.el = 35.0, 22.0
        self.target = np.zeros(3)
        self.span = float(span)          # mm across the shorter side of the view
        self.ortho = True
        self.fov = 40.0

    def basis(self):
        d = look(self.az, self.el)
        up = np.array([0.0, 0.0, 1.0])
        if abs(float(np.dot(d, up))) > 0.999:
            up = np.array([0.0, 1.0, 0.0])
        right = np.cross(up, d)
        right /= max(np.linalg.norm(right), 1e-9)
        return d, right, np.cross(d, right)

    def project(self, pts, w, h):
        """World mm to screen pixels. Returns (Nx2 screen, N depth) with depth
        growing away from the eye."""
        p = np.atleast_2d(np.asarray(pts, float))
        d, right, up = self.basis()
        rel = p - self.target
        x = rel @ right
        y = rel @ up
        z = rel @ d                     # + is toward the eye
        s = min(w, h) / max(self.span, 1e-6)
        if self.ortho:
            sx, sy = x * s, y * s
        else:
            eye_dist = self.span / (2.0 * math.tan(math.radians(self.fov) / 2.0))
            denom = np.maximum(eye_dist - z, 1e-6)
            k = eye_dist / denom
            sx, sy = x * s * k, y * s * k
        return np.stack([w / 2.0 + sx, h / 2.0 - sy], axis=1), -z

    def frame(self, span):
        self.span = max(float(span), 1e-3)


def _coeffs(dst, src):
    """The eight numbers PIL wants to warp src's corners onto dst's.

    PIL's PERSPECTIVE transform runs backwards - for every destination pixel it
    asks where in the source to look - so this solves destination to source,
    not the other way about.
    """
    rows, rhs = [], []
    for (dx, dy), (sx, sy) in zip(dst, src):
        rows.append([dx, dy, 1, 0, 0, 0, -sx * dx, -sx * dy])
        rows.append([0, 0, 0, dx, dy, 1, -sy * dx, -sy * dy])
        rhs += [sx, sy]
    a, res, *_ = np.linalg.lstsq(np.asarray(rows, float),
                                 np.asarray(rhs, float), rcond=None)
    return tuple(a)


def texture(img, hide_paper, opacity):
    """One plane's image, ready to composite.

    Keying the paper out is not decoration. Five opaque white rectangles
    through the same origin hide each other completely, and the whole question
    here is whether what is drawn on them agrees.
    """
    im = img.convert("RGB")
    if max(im.size) > TEX_MAX:
        f = TEX_MAX / float(max(im.size))
        im = im.resize((max(1, int(im.width * f)), max(1, int(im.height * f))),
                       Image.LANCZOS)
    a = np.asarray(im.convert("L"), np.float32)
    if hide_paper:
        alpha = np.clip(255.0 - a, 0, 255)
    else:
        alpha = np.full(a.shape, 255.0, np.float32)
    out = im.convert("RGBA")
    out.putalpha(Image.fromarray((alpha * float(opacity)).astype(np.uint8), "L"))
    return out


def draw_plane(frame, tex, quad):
    """Warp one plane onto the frame, over its own screen area only.

    Transforming the whole frame per plane is the obvious way and about four
    times the work, since a plane rarely fills the view.
    """
    xs = [p[0] for p in quad]
    ys = [p[1] for p in quad]
    x0, y0 = int(math.floor(min(xs))), int(math.floor(min(ys)))
    x1, y1 = int(math.ceil(max(xs))), int(math.ceil(max(ys)))
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(frame.width, x1), min(frame.height, y1)
    w, h = x1 - x0, y1 - y0
    if w < 2 or h < 2:
        return
    # Edge on, the quad has no area and the solve is meaningless.
    area = 0.0
    for i in range(4):
        ax, ay = quad[i]
        bx, by = quad[(i + 1) % 4]
        area += ax * by - bx * ay
    if abs(area) / 2.0 < 4.0:
        return
    tw, th = tex.width - 1, tex.height - 1
    dst = [(p[0] - x0, p[1] - y0) for p in quad]
    try:
        c = _coeffs(dst, [(0, 0), (tw, 0), (tw, th), (0, th)])
    except np.linalg.LinAlgError:
        return
    tile = tex.transform((w, h), Image.PERSPECTIVE, c, Image.BILINEAR)
    frame.paste(tile, (x0, y0), tile)


def bbox_edges(L, W, H):
    """The nominal object as a wire box: width on X, length on Y, height on Z."""
    hx, hy, hz = W / 2.0, L / 2.0, H / 2.0
    c = [(sx * hx, sy * hy, sz * hz)
         for sz in (-1, 1) for sy in (-1, 1) for sx in (-1, 1)]
    e = [(0, 1), (2, 3), (0, 2), (1, 3), (4, 5), (6, 7), (4, 6), (5, 7),
         (0, 4), (1, 5), (2, 6), (3, 7)]
    return np.array(c, float), e


AXIS_VIEWS = {"front": (0, 0), "back": (180, 0), "left": (-90, 0),
              "right": (90, 0), "top": (0, 89.9), "bottom": (0, -89.9),
              "iso": (35, 22)}


class Preview3D(ctk.CTkToplevel):
    """The planes in space, with a probe you can put on a feature."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.images = {}
        self.meta = None
        self.rep = None
        self.tex = {}
        self.bases = []
        self.show = {}
        self.frame_img = None
        self.photo = None
        self.insets = {}
        self.drag = None
        self.job = None
        self.busy = False

        # The viewport takes what the screen can spare. A fixed size runs the
        # footer off the bottom of a laptop display, and the buttons that
        # rebuild and close the window are in it.
        #
        # The canvas is a plain tk widget and is sized in real pixels, but
        # everything around it is CustomTkinter and is multiplied by the
        # display's scaling. So the screen is measured as it is and only the
        # chrome is scaled - dividing both would give away a third of the
        # viewport on a 125% display for no reason.
        try:
            scl = ctk.ScalingTracker.get_window_scaling(self)
        except Exception:
            scl = 1.0
        self.vw = int(max(420, min(VIEW_W,
                                   self.winfo_screenwidth() - 440 * scl)))
        self.vh = int(max(300, min(VIEW_H,
                                   self.winfo_screenheight() - 330 * scl)))

        self.cam = Camera()
        self.probe = np.zeros(3)
        self.probe_on = tk.BooleanVar(value=True)
        self.axis = tk.StringVar(value="X")
        self.opacity = tk.IntVar(value=85)
        self.hide_paper = tk.BooleanVar(value=True)
        self.show_box = tk.BooleanVar(value=True)
        self.show_axes = tk.BooleanVar(value=True)
        self.show_mirror = tk.BooleanVar(value=True)
        self.ortho = tk.BooleanVar(value=True)
        self.push = tk.IntVar(value=0)
        self.mag = tk.StringVar(value="2x")

        self.title("ViewForge - 3D preview")
        self.configure(fg_color=BG_PANEL)
        self.transient(app)
        self.geometry("+%d+%d" % (max(0, app.winfo_rootx() + 30),
                                  max(0, app.winfo_rooty() + 20)))
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self._build()
        self.after(30, self.rebuild)
        self.after(260, self._focus)

    def _focus(self):
        try:
            self.lift()
            self.focus_force()
        except tk.TclError:
            pass

    # Layout
    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=0, minsize=330)
        self.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(self, fg_color="transparent")
        left.grid(row=0, column=0, padx=(12, 6), pady=(12, 0), sticky="nsew")
        shell = ctk.CTkFrame(left, corner_radius=10, fg_color=BG_CARD)
        shell.pack()
        self.canvas = tk.Canvas(shell, width=self.vw, height=self.vh, bg=VIEW_BG,
                                highlightthickness=0, bd=0)
        self.canvas.pack(padx=6, pady=6)

        bar = ctk.CTkFrame(left, fg_color="transparent")
        bar.pack(fill="x", pady=(8, 0))
        for name in ("front", "back", "left", "right", "top", "bottom", "iso"):
            ctk.CTkButton(bar, text=name, width=58, height=26, font=UI_SM,
                          fg_color=BG_CARD, hover_color="#33363c",
                          command=lambda n=name: self.snap(n)).pack(
                side="left", padx=(0, 4))
        self.hint = ctk.CTkLabel(bar, text="drag orbits  |  right-drag pans  |  "
                                           "wheel zooms  |  double-click drops "
                                           "the probe",
                                 font=UI_SM, text_color=FG_MUTED)
        self.hint.pack(side="right")

        self.inset_row = ctk.CTkFrame(left, corner_radius=10, fg_color=BG_CARD)
        self.inset_row.pack(fill="x", pady=(10, 0))

        right = ctk.CTkScrollableFrame(self, fg_color="transparent", width=310,
                                       height=self.vh + INSET - 20)
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 12), pady=(12, 0))
        self.controls(right)

        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=1, column=0, columnspan=2, sticky="ew", padx=12, pady=12)
        self.status = ctk.CTkLabel(foot, text="", font=MONO,
                                   text_color=FG_MUTED, anchor="w")
        self.status.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(foot, text="Close", width=100, height=34, font=UI,
                      fg_color=BG_CARD, hover_color="#33363c",
                      command=self.destroy).pack(side="right")
        ctk.CTkButton(foot, text="Rebuild from the boxes", width=190, height=34,
                      font=UI_B, fg_color=ACCENT, hover_color=ACCENT_H,
                      command=self.rebuild).pack(side="right", padx=8)

        c = self.canvas
        c.bind("<ButtonPress-1>", self.press)
        c.bind("<B1-Motion>", self.motion)
        c.bind("<ButtonRelease-1>", self.release)
        c.bind("<Double-Button-1>", self.drop_probe)
        for b in (2, 3):
            c.bind("<ButtonPress-%d>" % b, self.press_pan)
            c.bind("<B%d-Motion>" % b, self.motion)
            c.bind("<ButtonRelease-%d>" % b, self.release)
        c.bind("<MouseWheel>", self.wheel)
        c.bind("<Button-4>", self.wheel)
        c.bind("<Button-5>", self.wheel)

    def controls(self, p):
        def cap(text):
            ctk.CTkLabel(p, text=text.upper(), font=UI_CAP, anchor="w",
                         text_color=FG_MUTED).pack(fill="x", pady=(12, 4))

        def note(text):
            ctk.CTkLabel(p, text=text, font=UI_SM, text_color=FG_MUTED,
                         anchor="w", wraplength=280, justify="left").pack(
                fill="x", pady=(0, 2))

        cap("the planes")
        self.plane_box = ctk.CTkFrame(p, fg_color=BG_SUNK, corner_radius=8)
        self.plane_box.pack(fill="x")
        self.warn = ctk.CTkLabel(p, text="", font=UI_SM, text_color="#ffb020",
                                 anchor="w", wraplength=280, justify="left")
        self.warn.pack(fill="x", pady=(6, 0))

        cap("how they are drawn")
        ctk.CTkSwitch(p, text="hide the paper", variable=self.hide_paper,
                      font=UI, command=self.retexture).pack(anchor="w", pady=2)
        note("White is made transparent so the ink of one plane shows through "
             "another. Off, the nearest plane simply hides the rest.")
        row = ctk.CTkFrame(p, fg_color="transparent")
        row.pack(fill="x", pady=(8, 0))
        ctk.CTkLabel(row, text="opacity", font=UI, anchor="w").pack(side="left")
        self.op_lbl = ctk.CTkLabel(row, text="85%", font=MONO, width=44,
                                   anchor="e", text_color=FG_MUTED)
        self.op_lbl.pack(side="right")
        sl = ctk.CTkSlider(p, from_=10, to=100, number_of_steps=90,
                           command=self._op)
        sl.set(85)
        sl.pack(fill="x", pady=(0, 2))
        ctk.CTkSwitch(p, text="orthographic", variable=self.ortho, font=UI,
                      command=self.redraw).pack(anchor="w", pady=(8, 2))
        note("Reference planes only line up in an orthographic view. This is "
             "the same Numpad 5 that decides it in Blender.")
        ctk.CTkSwitch(p, text="show the nominal box", variable=self.show_box,
                      font=UI, command=self.redraw).pack(anchor="w", pady=2)
        note("A wire box of exactly the length, width and height you typed. "
             "The drawing should fill it.")
        ctk.CTkSwitch(p, text="show the axes", variable=self.show_axes,
                      font=UI, command=self.redraw).pack(anchor="w", pady=2)
        ctk.CTkSwitch(p, text="show the mirror plane", variable=self.show_mirror,
                      font=UI, command=self.redraw).pack(anchor="w", pady=2)
        note("X=0, where a mirror modifier folds. Look down the front view: "
             "the object's middle should sit on this line. If it does not, put "
             "a centre line on that view back on the blueprint.")
        row = ctk.CTkFrame(p, fg_color="transparent")
        row.pack(fill="x", pady=(8, 0))
        ctk.CTkLabel(row, text="pull apart", font=UI, anchor="w").pack(side="left")
        self.push_lbl = ctk.CTkLabel(row, text="0 mm", font=MONO, width=60,
                                     anchor="e", text_color=FG_MUTED)
        self.push_lbl.pack(side="right")
        self.push_slider = ctk.CTkSlider(p, from_=0, to=100, number_of_steps=100,
                                         command=self._push)
        self.push_slider.set(0)
        self.push_slider.pack(fill="x", pady=(0, 2))
        note("Slides each plane back along its own normal, the way the export's "
             "Blender script does so they do not fight with your model. It "
             "changes nothing you can measure.")

        cap("the probe")
        ctk.CTkSwitch(p, text="show the probe", variable=self.probe_on, font=UI,
                      command=self.redraw).pack(anchor="w", pady=2)
        note("Put it on a feature and every plane below reports where that "
             "point lands on it. Same feature under every crosshair means the "
             "views agree. Double-click a plane to drop it there.")
        self.pv = {}
        self.psl = {}
        for i, k in enumerate("XYZ"):
            row = ctk.CTkFrame(p, fg_color="transparent")
            row.pack(fill="x", pady=(6, 0))
            ctk.CTkLabel(row, text=k, font=MONO, width=14,
                         text_color=AXIS_COL[k]).pack(side="left")
            var = tk.StringVar(value="0.0")
            self.pv[k] = var
            e = ctk.CTkEntry(row, textvariable=var, width=76, font=MONO,
                             height=26)
            e.pack(side="right")
            e.bind("<Return>", lambda _e, kk=k: self._typed(kk))
            e.bind("<FocusOut>", lambda _e, kk=k: self._typed(kk))
            ctk.CTkLabel(row, text=("width", "length", "height")[i], font=UI_SM,
                         text_color=FG_MUTED, anchor="w").pack(side="left",
                                                               padx=(6, 0))
            s = ctk.CTkSlider(p, from_=-500, to=500, number_of_steps=1000,
                              command=lambda val, kk=k: self._slid(kk, val))
            s.set(0)
            s.pack(fill="x", pady=(0, 2))
            self.psl[k] = s
        row = ctk.CTkFrame(p, fg_color="transparent")
        row.pack(fill="x", pady=(8, 0))
        ctk.CTkButton(row, text="centre it", font=UI_SM, height=26,
                      fg_color=BG_CARD, hover_color="#33363c",
                      command=self.centre_probe).pack(side="left", expand=True,
                                                      fill="x", padx=(0, 4))
        ctk.CTkSegmentedButton(row, values=["1x", "2x", "4x"], font=UI_SM,
                               variable=self.mag,
                               command=lambda _=None: self.draw_insets()).pack(
            side="right")
        self.read = ctk.CTkLabel(p, text="", font=MONO, anchor="w",
                                 justify="left", text_color=FG_MUTED)
        self.read.pack(fill="x", pady=(8, 12))

    def _op(self, v):
        self.opacity.set(int(round(float(v))))
        self.op_lbl.configure(text="%d%%" % self.opacity.get())
        self.retexture()

    def _push(self, v):
        self.push.set(int(round(float(v))))
        self.push_lbl.configure(text="%d mm" % self.push.get())
        self.redraw()

    def _typed(self, k):
        try:
            val = float(self.pv[k].get())
        except ValueError:
            self.sync_probe()
            return
        self.probe["XYZ".index(k)] = val
        self.psl[k].set(max(self.psl[k].cget("from_"),
                            min(self.psl[k].cget("to"), val)))
        self.after_probe()

    def _slid(self, k, val):
        self.probe["XYZ".index(k)] = float(val)
        self.pv[k].set("%.1f" % float(val))
        self.after_probe()

    def centre_probe(self):
        self.probe = np.zeros(3)
        self.sync_probe()
        self.after_probe()

    def sync_probe(self):
        for i, k in enumerate("XYZ"):
            self.pv[k].set("%.1f" % self.probe[i])
            self.psl[k].set(max(self.psl[k].cget("from_"),
                                min(self.psl[k].cget("to"), self.probe[i])))

    # Building the scene
    def rebuild(self):
        """Render the planes exactly as export() would, and put them in space."""
        app = self.app
        if app.ink is None:
            self.status.configure(text="No blueprint open.")
            return
        self.busy = True
        self.configure(cursor="watch")
        self.update_idletasks()
        try:
            images, meta, rep = core.build_planes(
                app.views, app.ink, app.lab, app.dims(),
                detail=app.detail.get(), mode=app.mode.get(),
                proportional=app.proportional.get(), src=app.rgb,
                info=app.info, style=app.export_style())
        except core.SolveError as e:
            self.status.configure(text=str(e))
            self.plane_rows([])
            self.images, self.meta, self.bases = {}, None, []
            self.canvas.delete("all")
            self.configure(cursor="")
            self.busy = False
            return
        finally:
            self.configure(cursor="")
        self.images, self.meta, self.rep = images, meta, rep
        self.bases = [b for b in (core.plane_basis(f, meta) for f in meta["files"])
                      if b is not None]
        for b in self.bases:
            self.show.setdefault(b["file"], tk.BooleanVar(value=True))
        self.plane_rows(self.bases)
        ws = rep["warnings"]
        txt = "\n".join("- " + w for w in ws[:2])
        if len(ws) > 2:
            txt += "\n- and %d more. The report lists them all." % (len(ws) - 2)
        self.warn.configure(text=txt)
        d = meta["dims"]
        big = max(d.get("L", 0), d.get("W", 0), d.get("H", 0), 1.0)
        self.push_slider.configure(to=max(10, round(big * 0.75)))
        for k in "XYZ":
            lim = max(10.0, round(big * 0.75))
            self.psl[k].configure(from_=-lim, to=lim,
                                  number_of_steps=int(2 * lim))
        self.cam.frame(big * 1.9)
        self.sync_probe()
        self.busy = False           # retexture draws, and drawing checks this
        self.retexture()

    def plane_rows(self, bases):
        for w in self.plane_box.winfo_children():
            w.destroy()
        if not bases:
            ctk.CTkLabel(self.plane_box, text="Nothing assigned yet.", font=UI,
                         text_color=FG_MUTED).pack(padx=10, pady=8)
            return
        for b in bases:
            ctk.CTkCheckBox(self.plane_box, text=b["file"], font=MONO,
                            variable=self.show[b["file"]], checkbox_width=18,
                            checkbox_height=18,
                            command=self.redraw).pack(anchor="w", padx=10, pady=4)

    def retexture(self):
        self.tex = {b["file"]: texture(self.images[b["file"]],
                                       self.hide_paper.get(),
                                       self.opacity.get() / 100.0)
                    for b in self.bases}
        self.redraw()

    def visible(self):
        return [b for b in self.bases if self.show[b["file"]].get()]

    # Drawing
    def redraw(self, quality=1.0):
        if self.busy or not self.bases or self.meta is None:
            return
        self.cam.ortho = self.ortho.get()
        w = max(2, int(self.vw * quality))
        h = max(2, int(self.vh * quality))
        frame = Image.new("RGBA", (w, h), (27, 28, 30, 255))
        push = float(self.push.get())

        shown = []
        for b in self.visible():
            bb = core.plane_basis({"file": b["file"], "role": b["role"],
                                   "facing": b["facing"]}, self.meta, push)
            corners = np.array(core.plane_corners(bb), float)
            pts, dep = self.cam.project(corners, w, h)
            shown.append((float(dep.mean()), bb, pts))
        for _, bb, pts in sorted(shown, key=lambda t: -t[0]):
            draw_plane(frame, self.tex[bb["file"]], [tuple(p) for p in pts])

        if quality != 1.0:
            frame = frame.resize((self.vw, self.vh), Image.BILINEAR)
        self.frame_img = frame
        self.photo = ImageTk.PhotoImage(frame)
        c = self.canvas
        c.delete("all")
        c.create_image(0, 0, anchor="nw", image=self.photo)
        self.overlay(c)
        self.draw_insets()
        self.report()

    def overlay(self, c):
        d = self.meta["dims"]
        L, W, H = d.get("L", 0), d.get("W", 0), d.get("H", 0)
        big = max(L, W, H, 1.0)
        if self.show_box.get() and min(L, W, H) > 0:
            pts, _ = self.cam.project(bbox_edges(L, W, H)[0], self.vw, self.vh)
            for i, j in bbox_edges(L, W, H)[1]:
                c.create_line(pts[i][0], pts[i][1], pts[j][0], pts[j][1],
                              fill=COL_BOX, width=1, dash=(4, 3))
        if self.show_mirror.get() and min(L, H) > 0:
            q = np.array([[0, -L / 2, -H / 2], [0, L / 2, -H / 2],
                          [0, L / 2, H / 2], [0, -L / 2, H / 2]], float)
            pts, _ = self.cam.project(q, self.vw, self.vh)
            c.create_polygon([t for p in pts for t in p], outline=COL_MIRROR,
                             fill="", width=2)
            mid, _ = self.cam.project(np.array([[0, 0, H / 2]], float),
                                      self.vw, self.vh)
            c.create_text(mid[0][0], mid[0][1] - 10, text="X = 0",
                          fill=COL_MIRROR, font=("Segoe UI", 10, "bold"))
        if self.show_axes.get():
            n = big * 0.62
            ends = np.array([[0, 0, 0], [n, 0, 0], [0, n, 0], [0, 0, n]], float)
            pts, _ = self.cam.project(ends, self.vw, self.vh)
            for k, i in (("X", 1), ("Y", 2), ("Z", 3)):
                c.create_line(pts[0][0], pts[0][1], pts[i][0], pts[i][1],
                              fill=AXIS_COL[k], width=2)
                c.create_text(pts[i][0], pts[i][1], text=k, fill=AXIS_COL[k],
                              font=("Segoe UI", 10, "bold"))
        if not self.probe_on.get():
            return
        p = self.probe
        legs = np.array([p, [p[0], p[1], -H / 2], [p[0], -L / 2, p[2]],
                         [-W / 2, p[1], p[2]]], float)
        pts, _ = self.cam.project(legs, self.vw, self.vh)
        for k, i in (("Z", 1), ("Y", 2), ("X", 3)):
            c.create_line(pts[0][0], pts[0][1], pts[i][0], pts[i][1],
                          fill=AXIS_COL[k], width=1, dash=(3, 3))
        x, y = pts[0]
        r = 7
        c.create_oval(x - r, y - r, x + r, y + r, outline=COL_PROBE, width=2)
        c.create_line(x - r - 5, y, x + r + 5, y, fill=COL_PROBE, width=1)
        c.create_line(x, y - r - 5, x, y + r + 5, fill=COL_PROBE, width=1)

    # The probe insets: the actual alignment check
    def draw_insets(self):
        for w in self.inset_row.winfo_children():
            w.destroy()
        self.insets = {}
        vis = self.visible()
        if not vis or self.meta is None:
            ctk.CTkLabel(self.inset_row, text="  ", font=UI_SM).pack(pady=4)
            return
        if not self.probe_on.get():
            ctk.CTkLabel(self.inset_row, text="Switch the probe on to compare "
                                              "the same point across the planes.",
                         font=UI_SM, text_color=FG_MUTED).pack(pady=10)
            return
        mag = {"1x": 1, "2x": 2, "4x": 4}[self.mag.get()]
        wrap = ctk.CTkFrame(self.inset_row, fg_color="transparent")
        wrap.pack(padx=8, pady=8)
        for b in vis:
            col = ctk.CTkFrame(wrap, fg_color="transparent")
            col.pack(side="left", padx=(0, 8))
            ctk.CTkLabel(col, text=b["file"].replace(".png", ""), font=UI_SM,
                         text_color=FG_MUTED).pack()
            cv = tk.Canvas(col, width=INSET, height=INSET, bg=BG_SUNK,
                           highlightthickness=1, highlightbackground="#3a3d44",
                           bd=0)
            cv.pack()
            img = self.images[b["file"]]
            px, py, u, v = core.project_onto_plane(self.probe, b, self.meta)
            half = INSET / (2.0 * mag)
            box = (px - half, py - half, px + half, py + half)
            tile = img.convert("RGB").crop(tuple(int(round(t)) for t in box))
            if mag != 1:
                tile = tile.resize((INSET, INSET), Image.NEAREST)
            ph = ImageTk.PhotoImage(tile)
            self.insets[b["file"]] = ph          # tkinter keeps no reference
            cv.create_image(0, 0, anchor="nw", image=ph)
            m = INSET / 2.0
            cv.create_line(m, 0, m, INSET, fill=COL_PROBE, width=1)
            cv.create_line(0, m, INSET, m, fill=COL_PROBE, width=1)
            cv.create_oval(m - 5, m - 5, m + 5, m + 5, outline=COL_PROBE, width=1)
            inside = (0 <= px < img.width and 0 <= py < img.height)
            ctk.CTkLabel(col, font=MONO, text_color=(FG_MUTED if inside else
                                                     "#ffb020"),
                         text=("%.0f, %.0f px" % (px, py) if inside
                               else "off the canvas")).pack()

    def report(self):
        if self.meta is None:
            return
        p = self.probe
        L = ["probe   X %8.1f   Y %8.1f   Z %8.1f  mm" % (p[0], p[1], p[2])]
        for b in self.visible():
            _, _, u, v = core.project_onto_plane(p, b, self.meta)
            L.append("  %-16s %s %8.1f   %s %8.1f"
                     % (b["file"].replace(".png", ""),
                        "across", u, "down", -v))
        self.read.configure(text="\n".join(L))
        d = self.meta["dims"]
        self.status.configure(
            text="%d planes   canvas %d x %d px = %s x %s mm   %.2f px/mm   "
                 "L %.0f  W %.0f  H %.0f"
                 % (len(self.visible()), self.meta["canvas_px"][0],
                    self.meta["canvas_px"][1], self.meta["canvas_mm"][0],
                    self.meta["canvas_mm"][1], self.meta["px_per_mm"],
                    d.get("L", 0), d.get("W", 0), d.get("H", 0)))

    def after_probe(self):
        if self.job is not None:
            self.after_cancel(self.job)
        self.job = self.after(30, self._probe_now)

    def _probe_now(self):
        self.job = None
        self.redraw()

    # Navigation
    def snap(self, name):
        self.cam.az, self.cam.el = AXIS_VIEWS[name]
        self.cam.target = np.zeros(3)
        self.redraw()

    def press(self, e):
        self.canvas.focus_set()
        self.drag = ("orbit", e.x, e.y, self.cam.az, self.cam.el)

    def press_pan(self, e):
        self.drag = ("pan", e.x, e.y, self.cam.target.copy(), None)

    def motion(self, e):
        if not self.drag:
            return
        kind, sx, sy, a, b = self.drag
        if kind == "orbit":
            self.cam.az = (a + (e.x - sx) * 0.4) % 360.0
            self.cam.el = max(-89.9, min(89.9, b - (e.y - sy) * 0.35))
        else:
            _, right, up = self.cam.basis()
            s = self.cam.span / min(self.vw, self.vh)
            self.cam.target = a - right * (e.x - sx) * s + up * (e.y - sy) * s
        self.redraw(DRAG_Q)

    def release(self, _e=None):
        if self.drag:
            self.drag = None
            self.redraw()

    def wheel(self, e):
        d = getattr(e, "delta", 0)
        if d == 0:
            d = 120 if getattr(e, "num", 5) == 4 else -120
        self.cam.frame(self.cam.span * (0.88 ** (d / 120.0)))
        self.redraw()

    def drop_probe(self, e):
        """Put the probe where the pointer is, on the nearest plane under it."""
        if self.meta is None:
            return
        d, right, up = self.cam.basis()
        s = min(self.vw, self.vh) / max(self.cam.span, 1e-6)
        origin = (self.cam.target + right * ((e.x - self.vw / 2.0) / s)
                  + up * (-(e.y - self.vh / 2.0) / s))
        push = float(self.push.get())
        best = None
        for b in self.visible():
            bb = core.plane_basis({"file": b["file"], "role": b["role"],
                                   "facing": b["facing"]}, self.meta, push)
            denom = float(np.dot(d, bb["normal"]))
            if abs(denom) < 1e-6:
                continue
            t = float(np.dot(bb["centre"] - origin, bb["normal"])) / denom
            hit = origin + d * t
            rel = hit - bb["centre"]
            if (abs(float(np.dot(rel, bb["right"]))) > bb["half"][0] or
                    abs(float(np.dot(rel, bb["up"]))) > bb["half"][1]):
                continue
            if best is None or t < best[0]:
                best = (t, hit)
        if best is None:
            self.status.configure(text="No plane under the pointer there.")
            return
        self.probe = best[1]
        self.probe_on.set(True)
        self.sync_probe()
        self.redraw()
