#!/usr/bin/env python3
"""
ViewForge. Turns a blueprint into verified Blender reference planes.

Run:  python viewforge_gui.py
"""

import os, traceback
import tkinter as tk
from tkinter import filedialog, messagebox
import customtkinter as ctk
from PIL import Image, ImageTk
import viewforge_core as core

HERE = os.path.dirname(os.path.abspath(__file__))
ICON = os.path.join(HERE, "Assets", "icon.ico")
LOGO = os.path.join(HERE, "Assets", "Logo.png")

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

BG_PANEL = "#202226"
BG_CARD  = "#26282d"
BG_SUNK  = "#141517"
FG_MUTED = "#8b919b"
ACCENT   = "#3b82f6"
ACCENT_H = "#2f6fd0"
BP_BG    = "#1b1c1e"

COL_SEL   = "#ff5f5f"
COL_SET   = "#3ddc84"
COL_OFF   = "#9aa0aa"
COL_GAUGE = "#ffb020"

MONO    = ("Consolas", 12)
MONO_SM = ("Consolas", 11)
UI      = ("Segoe UI", 12)
UI_SM   = ("Segoe UI", 11)
UI_B    = ("Segoe UI", 13, "bold")
UI_CAP  = ("Segoe UI", 11, "bold")

ZOOM_MIN, ZOOM_MAX = 0.05, 32.0

HANDLE = 9        # screen px you have to be within to grab a box edge or corner
GAUGE_OFF = 14    # how far outside the box a measure line's grab handle sits
MIN_BOX = 3       # a box smaller than this is not a measurement

CURSORS = {(-1, -1): "top_left_corner", (1, 1): "bottom_right_corner",
           (1, -1): "top_right_corner", (-1, 1): "bottom_left_corner",
           (-1, 0): "sb_h_double_arrow", (1, 0): "sb_h_double_arrow",
           (0, -1): "sb_v_double_arrow", (0, 1): "sb_v_double_arrow"}


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("ViewForge - Blueprint To Blender Reference Planes")
        self._size_window()

        self.path = None
        self.gray = self.ink = self.lab = None
        self.base = None
        self.views = []
        self.rows = []
        self.sel = None
        self.photo = None
        self.zoom = 1.0
        self.ox = self.oy = 0.0
        self.drag = None
        self.edit = None
        self.pan = None
        self.fitted = False
        self.syncing = False

        self._build()
        self._set_icon()

    # CustomTkinter multiplies geometry by the display's DPI factor, so a fixed
    # 1520x950 becomes 1900x1154 on a 125% screen and hangs off the edge. Work
    # in the same scaled units the geometry string is read in.
    def _size_window(self):
        try:
            scl = ctk.ScalingTracker.get_window_scaling(self)
        except Exception:
            scl = 1.0
        sw = self.winfo_screenwidth() / scl
        sh = self.winfo_screenheight() / scl
        w = int(min(1520, sw - 40))
        h = int(min(950, sh - 90))          # room for the taskbar
        self.minsize(min(1080, w), min(680, h))
        self.geometry("%dx%d+%d+%d" % (w, h, max(0, (sw - w) // 2),
                                       max(0, (sh - h) // 2 - 15)))

    # CustomTkinter re-applies its own icon just after the window appears, so
    # ours has to be set again afterwards.
    def _set_icon(self):
        if not os.path.exists(ICON):
            return
        self._apply_icon()
        for delay in (100, 300, 800):
            self.after(delay, self._apply_icon)

    def _apply_icon(self):
        try:
            self.wm_iconbitmap(default=ICON)
            self.iconbitmap(ICON)
        except tk.TclError:
            pass

    # Layout
    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=0, minsize=390)
        self.grid_rowconfigure(1, weight=1)
        self._build_header()
        self._build_canvas()
        self._build_sidebar()
        self._build_console()

    def _build_header(self):
        bar = ctk.CTkFrame(self, corner_radius=0, fg_color=BG_PANEL, height=62)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        bar.grid_propagate(False)
        pad = dict(side="left", padx=6, pady=14)

        if os.path.exists(LOGO):
            im = Image.open(LOGO)
            self.logo = ctk.CTkImage(light_image=im, dark_image=im, size=(32, 32))
            ctk.CTkLabel(bar, image=self.logo, text="").pack(
                side="left", padx=(14, 8), pady=14)
        ctk.CTkLabel(bar, text="ViewForge", font=("Segoe UI", 17, "bold")).pack(
            side="left", padx=(0, 14), pady=14)
        ctk.CTkButton(bar, text="Open blueprint", width=130, font=UI,
                      command=self.open_blueprint).pack(**pad)
        self.file_lbl = ctk.CTkLabel(bar, text="no blueprint loaded", font=UI,
                                     text_color=FG_MUTED)
        self.file_lbl.pack(side="left", padx=(8, 18))

        ctk.CTkLabel(bar, text="merge gap", font=UI, text_color=FG_MUTED).pack(
            side="left", padx=(0, 6))
        self.gap = tk.IntVar(value=9)
        sl = ctk.CTkSlider(bar, from_=3, to=41, number_of_steps=38, width=130,
                           command=self._gap_changed)
        sl.set(9)
        sl.pack(side="left")
        self.gap_lbl = ctk.CTkLabel(bar, text="9", width=26, font=MONO)
        self.gap_lbl.pack(side="left", padx=(6, 14))

        ctk.CTkButton(bar, text="Detect views", width=115, font=UI,
                      command=self.detect).pack(**pad)
        ctk.CTkButton(bar, text="Clear all", width=90, font=UI, fg_color=BG_CARD,
                      hover_color="#33363c", command=self.clear_views).pack(**pad)

    def _gap_changed(self, v):
        self.gap.set(int(round(float(v))))
        self.gap_lbl.configure(text=str(self.gap.get()))

    def _build_canvas(self):
        wrap = ctk.CTkFrame(self, fg_color="transparent")
        wrap.grid(row=1, column=0, sticky="nsew", padx=(12, 6), pady=(12, 6))
        wrap.grid_rowconfigure(0, weight=1)
        wrap.grid_columnconfigure(0, weight=1)

        shell = ctk.CTkFrame(wrap, corner_radius=10, fg_color=BG_PANEL)
        shell.grid(row=0, column=0, sticky="nsew")
        shell.grid_rowconfigure(0, weight=1)
        shell.grid_columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(shell, bg=BP_BG, highlightthickness=0, bd=0)
        self.canvas.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)

        nav = ctk.CTkFrame(wrap, corner_radius=10, fg_color=BG_PANEL, height=44)
        nav.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        small = dict(width=34, height=28, font=UI_B, fg_color=BG_CARD,
                     hover_color="#33363c")
        ctk.CTkButton(nav, text="-", command=lambda: self.zoom_to(self.zoom / 1.4),
                      **small).pack(side="left", padx=(10, 4), pady=8)
        self.zoom_lbl = ctk.CTkLabel(nav, text="100%", width=54, font=MONO)
        self.zoom_lbl.pack(side="left")
        ctk.CTkButton(nav, text="+", command=lambda: self.zoom_to(self.zoom * 1.4),
                      **small).pack(side="left", padx=4, pady=8)
        wide = dict(height=28, font=UI, fg_color=BG_CARD, hover_color="#33363c")
        ctk.CTkButton(nav, text="Fit", width=54, command=self.fit_view,
                      **wide).pack(side="left", padx=(10, 4), pady=8)
        ctk.CTkButton(nav, text="1:1", width=54, command=lambda: self.zoom_to(1.0),
                      **wide).pack(side="left", padx=4, pady=8)
        ctk.CTkButton(nav, text="Zoom to selection", width=140,
                      command=self.zoom_to_selection, **wide).pack(side="left",
                                                                   padx=4, pady=8)

        self.coord = ctk.CTkLabel(nav, text="", font=MONO, text_color=FG_MUTED)
        self.coord.pack(side="right", padx=12)
        self.hint = ctk.CTkLabel(nav, text="drag edges and corners to edit  |  "
                                           "shift-drag draws a new box",
                                 font=UI, text_color=FG_MUTED)
        self.hint.pack(side="right", padx=12)

        c = self.canvas
        c.bind("<Configure>", self.on_configure)
        c.bind("<ButtonPress-1>", self.on_press)
        c.bind("<B1-Motion>", self.on_move)
        c.bind("<ButtonRelease-1>", self.on_release)
        for b in (2, 3):
            c.bind("<ButtonPress-%d>" % b, self.pan_start)
            c.bind("<B%d-Motion>" % b, self.pan_move)
            c.bind("<ButtonRelease-%d>" % b, self.pan_end)
        c.bind("<MouseWheel>", self.on_wheel)
        c.bind("<Button-4>", self.on_wheel)
        c.bind("<Button-5>", self.on_wheel)
        c.bind("<Motion>", self.on_hover)
        c.bind("<Leave>", lambda e: self.coord.configure(text=""))
        c.bind("<Key>", self.on_key)
        c.configure(takefocus=True)

    def _card(self, parent, title):
        f = ctk.CTkFrame(parent, corner_radius=10, fg_color=BG_CARD)
        f.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(f, text=title.upper(), font=UI_CAP, text_color=FG_MUTED,
                     anchor="w").pack(fill="x", padx=14, pady=(10, 4))
        return f

    def _build_sidebar(self):
        col = ctk.CTkFrame(self, fg_color="transparent")
        col.grid(row=1, column=1, rowspan=2, sticky="nsew", padx=(6, 12), pady=(12, 12))
        col.grid_rowconfigure(0, weight=1)
        col.grid_columnconfigure(0, weight=1)
        side = ctk.CTkScrollableFrame(col, fg_color="transparent", width=380)
        side.grid(row=0, column=0, sticky="nsew")

        vs = self._card(side, "Views")
        self.list = ctk.CTkFrame(vs, fg_color=BG_SUNK, corner_radius=8)
        self.list.pack(fill="x", padx=12, pady=(0, 12))

        self._build_selected(side)
        self._build_dims(side)
        self._build_output(side)

        # Outside the scrolling area, so a long list of regions cannot push
        # these off the bottom.
        act = ctk.CTkFrame(col, fg_color="transparent")
        act.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        ctk.CTkButton(act, text="Check the blueprint", height=38, font=UI_B,
                      fg_color=BG_CARD, hover_color="#33363c",
                      command=self.analyse).pack(fill="x", pady=(0, 8))
        ctk.CTkButton(act, text="Export planes", height=42, font=UI_B,
                      fg_color=ACCENT, hover_color=ACCENT_H,
                      command=self.do_export).pack(fill="x")
        self.refresh_list()

    def _build_selected(self, side):
        ed = self._card(side, "Selected view")
        b = ctk.CTkFrame(ed, fg_color="transparent")
        b.pack(fill="x", padx=12, pady=(0, 4))
        b.grid_columnconfigure(1, weight=1)
        self.role = tk.StringVar(value="ignore")
        self.facing = tk.StringVar(value="left")
        self.flip = tk.BooleanVar(value=False)
        ctk.CTkLabel(b, text="role", font=UI, anchor="w").grid(
            row=0, column=0, sticky="w", pady=4)
        ctk.CTkOptionMenu(b, variable=self.role, values=core.ROLES, width=160,
                          font=UI, command=lambda _=None: self.apply_meta()).grid(
            row=0, column=1, sticky="e", pady=4)
        self.facing_lbl = ctk.CTkLabel(b, text="front of object is at", font=UI,
                                       anchor="w")
        self.facing_lbl.grid(row=1, column=0, sticky="w", pady=4)
        self.facing_menu = ctk.CTkOptionMenu(
            b, variable=self.facing, values=core.FACINGS, width=160, font=UI,
            command=lambda _=None: self.apply_meta())
        self.facing_menu.grid(row=1, column=1, sticky="e", pady=4)
        ctk.CTkLabel(b, text="'up' and 'down' are views drawn a quarter turn round",
                     font=UI_SM, text_color=FG_MUTED, anchor="w",
                     wraplength=340, justify="left").grid(
            row=2, column=0, columnspan=2, sticky="w")
        ctk.CTkSwitch(b, text="mirror this view", variable=self.flip, font=UI,
                      command=self.apply_meta).grid(row=3, column=0, columnspan=2,
                                                    sticky="w", pady=(10, 4))

        g = ctk.CTkFrame(ed, fg_color="transparent")
        g.pack(fill="x", padx=12, pady=(0, 8))
        for i in (1, 3):
            g.grid_columnconfigure(i, weight=1)
        self.box = {}
        for i, k in enumerate(("x0", "x1", "y0", "y1")):
            r, c = i // 2, (i % 2) * 2
            ctk.CTkLabel(g, text=k, font=MONO, width=24).grid(
                row=r, column=c, padx=(0, 4), pady=4)
            v = tk.IntVar(value=0)
            self.box[k] = v
            ctk.CTkEntry(g, textvariable=v, width=90, font=MONO).grid(
                row=r, column=c + 1, sticky="ew", padx=(0, 10), pady=4)

        # The box says where the view is; these say where the figure you typed
        # was taken from.
        ctk.CTkLabel(ed, text="MEASURE LINES", font=UI_CAP, text_color=FG_MUTED,
                     anchor="w").pack(fill="x", padx=14, pady=(6, 0))
        ctk.CTkLabel(ed, text="Off, the whole view is the measurement. On, the "
                             "dimension is taken between the two amber lines - "
                             "drag them onto the points the drawing measures.",
                     font=UI_SM, text_color=FG_MUTED, anchor="w",
                     wraplength=330, justify="left").pack(
            fill="x", padx=14, pady=(0, 4))

        m = ctk.CTkFrame(ed, fg_color="transparent")
        m.pack(fill="x", padx=12, pady=(0, 8))
        m.grid_columnconfigure(2, weight=1)
        m.grid_columnconfigure(3, weight=1)
        self.gauge_on, self.gauge_val, self.gauge_axis = {}, {}, {}
        for r, (key, word) in enumerate((("h", "across"), ("v", "down"))):
            on = tk.BooleanVar(value=False)
            self.gauge_on[key] = on
            ctk.CTkSwitch(m, text=word, variable=on, font=UI, width=78,
                          switch_width=36, switch_height=18,
                          command=lambda k=key: self.toggle_gauge(k)).grid(
                row=r, column=0, sticky="w", pady=4)
            lb = ctk.CTkLabel(m, text="-", font=MONO, width=22,
                              text_color=COL_GAUGE)
            lb.grid(row=r, column=1, padx=(2, 6))
            self.gauge_axis[key] = lb
            pair = []
            for c in (2, 3):
                iv = tk.IntVar(value=0)
                pair.append(iv)
                ctk.CTkEntry(m, textvariable=iv, width=78, font=MONO).grid(
                    row=r, column=c, sticky="ew", padx=(0, 6), pady=4)
            self.gauge_val[key] = pair

        a = ctk.CTkFrame(ed, fg_color="transparent")
        a.pack(fill="x", padx=12, pady=(0, 12))
        ctk.CTkButton(a, text="Apply", font=UI, command=self.apply_edit).pack(
            side="left", expand=True, fill="x", padx=(0, 4))
        ctk.CTkButton(a, text="Delete view", font=UI, fg_color="#5a2b2b",
                      hover_color="#6d3434", command=self.del_view).pack(
            side="left", expand=True, fill="x", padx=(4, 0))

    def _build_dims(self, side):
        dm = self._card(side, "Object dimensions (mm)")
        self.proportional = tk.BooleanVar(value=False)
        ctk.CTkSwitch(dm, text="proportional only - no real dimensions",
                      variable=self.proportional, font=UI,
                      command=self.toggle_proportional).pack(
            anchor="w", padx=14, pady=(0, 2))
        self.prop_note = ctk.CTkLabel(
            dm, text="", font=UI_SM, text_color=FG_MUTED, anchor="w",
            wraplength=330, justify="left")
        self.prop_note.pack(fill="x", padx=14, pady=(0, 6))

        d = ctk.CTkFrame(dm, fg_color="transparent")
        d.pack(fill="x", padx=12, pady=(0, 12))
        d.grid_columnconfigure(1, weight=1)
        self.dim, self.dim_entry = {}, {}
        for i, (k, lbl) in enumerate((("L", "length"), ("W", "width"),
                                      ("H", "height"))):
            ctk.CTkLabel(d, text=lbl, font=UI, anchor="w").grid(
                row=i, column=0, sticky="w", pady=4)
            v = tk.DoubleVar(value=0.0)
            self.dim[k] = v
            e = ctk.CTkEntry(d, textvariable=v, width=140, font=MONO)
            e.grid(row=i, column=1, sticky="e", pady=4)
            self.dim_entry[k] = e
        self.toggle_proportional()

    def _build_output(self, side):
        op = self._card(side, "Output")
        o = ctk.CTkFrame(op, fg_color="transparent")
        o.pack(fill="x", padx=12, pady=(0, 12))
        o.grid_columnconfigure(0, weight=1)

        # Resolution and margin both come from the drawing now. This only says
        # how hard to push it.
        ctk.CTkLabel(o, text="detail", font=UI, anchor="w").grid(
            row=0, column=0, sticky="w", pady=(0, 2))
        self.detail = tk.StringVar(value="normal")
        ctk.CTkSegmentedButton(o, values=["draft", "normal", "fine"],
                               variable=self.detail, font=UI).grid(
            row=1, column=0, sticky="ew")
        ctk.CTkLabel(o, text="normal keeps about one exported pixel per pixel of "
                             "the blueprint, which is all a scan can justify. "
                             "Canvas size and margin follow from it.",
                     font=UI_SM, text_color=FG_MUTED, anchor="w",
                     wraplength=330, justify="left").grid(
            row=2, column=0, sticky="w", pady=(4, 10))

        ctk.CTkLabel(o, text="scaling", font=UI, anchor="w").grid(
            row=3, column=0, sticky="w", pady=(0, 2))
        self.mode = tk.StringVar(value="fit")
        ctk.CTkSegmentedButton(o, values=["fit", "uniform"], variable=self.mode,
                               font=UI, command=self._mode_note).grid(
            row=4, column=0, sticky="ew")
        self.mode_lbl = ctk.CTkLabel(o, text="", font=UI_SM, text_color=FG_MUTED,
                                     anchor="w", wraplength=330, justify="left")
        self.mode_lbl.grid(row=5, column=0, sticky="w", pady=(4, 0))
        self._mode_note()

    def _mode_note(self, _=None):
        self.mode_lbl.configure(
            text=("fit: each view is stretched to hit your figures exactly, so "
                  "the planes always line up." if self.mode.get() == "fit" else
                  "uniform: one scale per view, so circles stay round and any "
                  "disagreement between views stays visible."))

    def _build_console(self):
        f = ctk.CTkFrame(self, corner_radius=10, fg_color=BG_PANEL, height=210)
        f.grid(row=2, column=0, sticky="nsew", padx=(12, 6), pady=(6, 12))
        f.grid_propagate(False)
        f.grid_rowconfigure(1, weight=1)
        f.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(f, text="REPORT", font=UI_CAP, text_color=FG_MUTED,
                     anchor="w").grid(row=0, column=0, sticky="ew", padx=14, pady=(8, 2))
        self.out = ctk.CTkTextbox(f, font=MONO_SM, wrap="none", fg_color=BG_SUNK,
                                  corner_radius=8)
        self.out.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        self.say("Open a blueprint to begin.\n"
                 "  Detect views finds each blob of ink, or drag a box by hand.\n"
                 "  Every box can be edited: drag an edge to move that edge, a "
                 "corner to move two,\n  or the middle to move the whole box. "
                 "Shift-drag draws a new one.\n"
                 "  Wheel zooms, middle or right drag pans, F fits, Delete "
                 "removes the selected view.")

    # Utilities
    def say(self, text, append=False):
        self.out.configure(state="normal")
        if not append:
            self.out.delete("1.0", "end")
        self.out.insert("end", text + "\n")
        self.out.see("end")
        self.out.configure(state="disabled")

    def cw(self):
        return max(1, self.canvas.winfo_width())

    def ch(self):
        return max(1, self.canvas.winfo_height())

    def i2c(self, x, y):
        return (x - self.ox) * self.zoom, (y - self.oy) * self.zoom

    def c2i(self, x, y):
        return self.ox + x / self.zoom, self.oy + y / self.zoom

    # Navigating the blueprint
    def clamp(self):
        """Keep the blueprint inside the viewport, centred on any axis it fits on."""
        w, h = self.base.size
        vw, vh = self.cw() / self.zoom, self.ch() / self.zoom
        self.ox = (w - vw) / 2 if vw >= w else max(0.0, min(self.ox, w - vw))
        self.oy = (h - vh) / 2 if vh >= h else max(0.0, min(self.oy, h - vh))

    def fit_view(self):
        if self.base is None:
            return
        w, h = self.base.size
        self.zoom = max(ZOOM_MIN, min(self.cw() / w, self.ch() / h))
        self.ox = self.oy = 0.0
        self.clamp()
        self.fitted = True
        self.redraw()

    def zoom_to(self, z, cx=None, cy=None):
        """Set the zoom, keeping the point under (cx, cy) where it is."""
        if self.base is None:
            return
        z = max(ZOOM_MIN, min(ZOOM_MAX, z))
        if cx is None:
            cx, cy = self.cw() / 2, self.ch() / 2
        ix, iy = self.c2i(cx, cy)
        self.zoom = z
        self.ox, self.oy = ix - cx / z, iy - cy / z
        self.clamp()
        self.redraw()

    def zoom_to_selection(self):
        if self.base is None or self.sel is None:
            return
        v = self.views[self.sel]
        bw = max(1.0, v["x1"] - v["x0"] + 1)
        bh = max(1.0, v["y1"] - v["y0"] + 1)
        z = max(ZOOM_MIN, min(ZOOM_MAX, min(self.cw() / bw, self.ch() / bh) * 0.85))
        self.zoom = z
        self.ox = (v["x0"] + v["x1"]) / 2 - self.cw() / (2 * z)
        self.oy = (v["y0"] + v["y1"]) / 2 - self.ch() / (2 * z)
        self.clamp()
        self.redraw()

    def on_configure(self, _=None):
        if self.base is None or self.cw() < 20:
            return
        if not self.fitted:
            self.fit_view()
        else:
            self.clamp()
            self.redraw()

    def on_wheel(self, e):
        if self.base is None:
            return
        d = getattr(e, "delta", 0)
        if d == 0:
            d = 120 if getattr(e, "num", 5) == 4 else -120
        self.zoom_to(self.zoom * (1.15 ** (d / 120.0)), e.x, e.y)

    def pan_start(self, e):
        if self.base is None:
            return
        self.pan = (e.x, e.y, self.ox, self.oy)
        self.canvas.configure(cursor="fleur")

    def pan_move(self, e):
        if not self.pan:
            return
        sx, sy, ox, oy = self.pan
        self.ox = ox - (e.x - sx) / self.zoom
        self.oy = oy - (e.y - sy) / self.zoom
        self.clamp()
        self.redraw()

    def pan_end(self, _=None):
        self.pan = None
        self.canvas.configure(cursor="")

    def on_key(self, e):
        if self.base is None:
            return
        step = 60 / self.zoom
        k = e.keysym
        if k in ("f", "F"):
            self.fit_view()
        elif k in ("Delete", "BackSpace"):
            self.del_view()
        elif k in ("plus", "equal", "KP_Add"):
            self.zoom_to(self.zoom * 1.4)
        elif k in ("minus", "KP_Subtract"):
            self.zoom_to(self.zoom / 1.4)
        elif k in ("Left", "Right", "Up", "Down"):
            self.ox += step * (k == "Right") - step * (k == "Left")
            self.oy += step * (k == "Down") - step * (k == "Up")
            self.clamp()
            self.redraw()

    def on_hover(self, e):
        if self.base is None:
            return
        ix, iy = self.c2i(e.x, e.y)
        self.coord.configure(text="x %5d   y %5d" % (int(ix), int(iy)))
        if self.pan or self.edit or self.drag:
            return
        hit = self._grab(e.x, e.y)
        if hit is None:
            self.canvas.configure(cursor="")
        elif hit["kind"] == "gauge":
            self.canvas.configure(cursor="sb_h_double_arrow" if hit["axis"] == 0
                                  else "sb_v_double_arrow")
        elif hit["kind"] == "move":
            self.canvas.configure(cursor="fleur")
        else:
            self.canvas.configure(cursor=CURSORS.get((hit["ex"], hit["ey"]), ""))

    # Drawing
    def _box_screen(self, v):
        x0, y0 = self.i2c(v["x0"], v["y0"])
        x1, y1 = self.i2c(v["x1"] + 1, v["y1"] + 1)
        return x0, y0, x1, y1

    def _gauge_pts(self, v):
        """Where each measure line's grab handle sits, in screen pixels.

        Outside the box on purpose: a new pair starts on the box edges, and the
        two would be impossible to tell apart if they shared a grab zone.
        """
        out = []
        x0, y0 = self._box_screen(v)[:2]
        for k, t in enumerate(v.get("gauge_h") or []):
            out.append(dict(axis=0, idx=k, cx=self.i2c(t + 0.5, 0)[0],
                            cy=y0 - GAUGE_OFF))
        for k, t in enumerate(v.get("gauge_v") or []):
            out.append(dict(axis=1, idx=k, cx=x0 - GAUGE_OFF,
                            cy=self.i2c(0, t + 0.5)[1]))
        return out

    def _draw_gauges(self, v, strong):
        c = self.canvas
        x0, y0, x1, y1 = self._box_screen(v)
        wd = 2 if strong else 1
        for g in self._gauge_pts(v):
            if g["axis"] == 0:
                c.create_line(g["cx"], y0 - GAUGE_OFF, g["cx"], y1 + 4,
                              fill=COL_GAUGE, width=wd, dash=(6, 4))
            else:
                c.create_line(x0 - GAUGE_OFF, g["cy"], x1 + 4, g["cy"],
                              fill=COL_GAUGE, width=wd, dash=(6, 4))
            if strong:
                r = 5
                c.create_polygon(g["cx"], g["cy"] - r, g["cx"] + r, g["cy"],
                                 g["cx"], g["cy"] + r, g["cx"] - r, g["cy"],
                                 fill=COL_GAUGE, outline="#141517")

    def _draw_handles(self, v):
        c = self.canvas
        x0, y0, x1, y1 = self._box_screen(v)
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        for hx, hy in ((x0, y0), (x1, y0), (x0, y1), (x1, y1),
                       (mx, y0), (mx, y1), (x0, my), (x1, my)):
            c.create_rectangle(hx - 4, hy - 4, hx + 4, hy + 4,
                               fill=COL_SEL, outline="#141517")

    def redraw(self):
        c = self.canvas
        c.delete("all")
        if self.base is None:
            return
        w, h = self.base.size
        sx0, sy0 = max(0.0, self.ox), max(0.0, self.oy)
        sx1 = min(float(w), self.ox + self.cw() / self.zoom)
        sy1 = min(float(h), self.oy + self.ch() / self.zoom)
        if sx1 - sx0 > 0.5 and sy1 - sy0 > 0.5:
            dw = max(1, int(round((sx1 - sx0) * self.zoom)))
            dh = max(1, int(round((sy1 - sy0) * self.zoom)))
            # Only the visible slice is resampled, so a big blueprint stays
            # responsive. Past 1:1, nearest keeps the pixel edges hard.
            samp = Image.NEAREST if self.zoom > 2.0 else Image.BILINEAR
            reg = self.base.resize((dw, dh), samp, box=(sx0, sy0, sx1, sy1))
            self.photo = ImageTk.PhotoImage(reg)
            px, py = self.i2c(sx0, sy0)
            c.create_image(round(px), round(py), anchor="nw", image=self.photo)

        for i, v in enumerate(self.views):
            x0, y0, x1, y1 = self._box_screen(v)
            on = (i == self.sel)
            col = COL_SEL if on else (COL_SET if v["role"] != "ignore" else COL_OFF)
            c.create_rectangle(x0, y0, x1, y1, outline=col, width=3 if on else 2)
            c.create_text(x0 + 6, y0 + 4, anchor="nw", text="%d  %s" % (i, v["role"]),
                          fill=col, font=("Segoe UI", 10, "bold"))
            self._draw_gauges(v, on)
            if on:
                self._draw_handles(v)

        if self.drag:
            a = self.i2c(self.drag[0], self.drag[1])
            b = self.i2c(self.drag[2], self.drag[3])
            c.create_rectangle(a[0], a[1], b[0], b[1], outline=COL_SEL,
                               width=2, dash=(5, 4))
        self.zoom_lbl.configure(text="%.0f%%" % (self.zoom * 100))

    # Loading a blueprint
    def open_blueprint(self):
        p = filedialog.askopenfilename(
            parent=self,
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff"),
                       ("All files", "*.*")])
        if not p:
            return
        try:
            self.gray, self.ink, thr = core.load_blueprint(p)
            self.base = Image.open(p).convert("L")
        except Exception as e:
            messagebox.showerror("Could not open that file", str(e), parent=self)
            return
        self.path = p
        self.views, self.sel, self.lab = [], None, None
        self.fitted = False
        h, w = self.ink.shape
        self.file_lbl.configure(text=os.path.basename(p), text_color="#dfe3e8")
        self.refresh_list()
        self.fit_view()
        self.say(f"{os.path.basename(p)}\n  {w} x {h} px, ink threshold {thr}\n"
                 f"  Press Detect views, or drag a box.")

    # Detecting and managing views
    def detect(self):
        if self.ink is None:
            messagebox.showinfo("No blueprint open", "Open a blueprint image first.",
                                parent=self)
            return
        self.views, self.lab = core.detect_views(self.ink, gap=self.gap.get())
        self.sel = None
        self.refresh_list()
        self.redraw()
        self.say(f"Found {len(self.views)} regions.")
        for m in core.segmentation_quality(self.views, self.ink):
            self.say("  " + m, append=True)
        self.say("  Select each one and give it a role. Anything left as "
                 "'ignore' is skipped.\n  Drag any region's edges or corners to "
                 "adjust it.", append=True)

    def clear_views(self):
        self.views, self.sel = [], None
        self.refresh_list()
        self.redraw()

    def _row_text(self, i):
        v = self.views[i]
        face = v["facing"] if v["role"] in core.FACING_ROLES else "-"
        marks = "".join(("g" if v.get("gauge_h") else "",
                         "G" if v.get("gauge_v") else ""))
        return (f"{i:<3}{v['role']:<9}{face:<7}"
                f"{'mirror' if v['hflip'] else '':<7}{marks:<3}{v['source']}")

    def refresh_list(self):
        for w in self.rows:
            w.destroy()
        self.rows = []
        if not self.views:
            lbl = ctk.CTkLabel(self.list, justify="left", font=UI, text_color=FG_MUTED,
                               text="Nothing yet. Detect views, or drag\na box by hand.")
            lbl.pack(fill="x", padx=10, pady=10)
            self.rows.append(lbl)
            return
        last = len(self.views) - 1
        for i in range(len(self.views)):
            b = ctk.CTkButton(self.list, text=self._row_text(i), font=MONO_SM,
                              anchor="w", height=28, corner_radius=6,
                              command=lambda i=i: self.select(i))
            b.pack(fill="x", padx=5, pady=(4 if i == 0 else 2, 5 if i == last else 0))
            self.rows.append(b)
        self.highlight()

    def highlight(self):
        for i, b in enumerate(self.rows):
            if not isinstance(b, ctk.CTkButton):
                continue
            on = (i == self.sel)
            b.configure(fg_color=ACCENT if on else "transparent",
                        hover_color=ACCENT_H if on else "#2b2e34",
                        text_color="#ffffff" if on else
                        (COL_SET if self.views[i]["role"] != "ignore" else FG_MUTED))

    def update_row(self, i):
        if i < len(self.rows) and isinstance(self.rows[i], ctk.CTkButton):
            self.rows[i].configure(text=self._row_text(i))
        self.highlight()

    def select(self, i):
        self.sel = i
        v = self.views[i]
        self.role.set(v["role"])
        self.facing.set(v["facing"])
        self.flip.set(v["hflip"])
        self.sync_fields()
        self.highlight()
        self.redraw()

    def sync_fields(self):
        """Push the selected view's numbers back into the entries. Guarded,
        since a handle drag calls this on every mouse motion."""
        if self.sel is None:
            return
        self.syncing = True
        try:
            v = self.views[self.sel]
            for k in ("x0", "x1", "y0", "y1"):
                self.box[k].set(v[k])
            ax = (core.view_axes(v) if v["role"] in core.ROLE_AXES else ("-", "-"))
            for n, key in ((0, "h"), (1, "v")):
                g = v.get("gauge_h" if key == "h" else "gauge_v")
                self.gauge_on[key].set(bool(g))
                self.gauge_axis[key].configure(text=ax[n])
                if g:
                    for j in (0, 1):
                        self.gauge_val[key][j].set(int(g[j]))
            self._facing_state(v)
        finally:
            self.syncing = False

    def _facing_state(self, v):
        """The facing question only means something on a side, top or bottom.
        Elsewhere the control is switched off rather than left showing a value
        that does nothing."""
        if v["role"] in core.FACING_ROLES:
            if v["facing"] not in core.FACINGS:
                v["facing"] = "left"
            self.facing.set(v["facing"])
            self.facing_menu.configure(state="normal", values=core.FACINGS)
            self.facing_lbl.configure(text="front of object is at",
                                      text_color=("gray10", "gray90"))
        else:
            v["facing"] = "n/a"
            self.facing.set("n/a")
            self.facing_menu.configure(state="disabled")
            self.facing_lbl.configure(text="front of object is at",
                                      text_color=FG_MUTED)

    def apply_meta(self):
        """Role, facing and mirror take effect the moment they are changed."""
        if self.sel is None or self.syncing:
            return
        v = self.views[self.sel]
        v["role"] = self.role.get()
        if self.facing.get() in core.FACINGS:
            v["facing"] = self.facing.get()
        v["hflip"] = self.flip.get()
        self.sync_fields()
        self.update_row(self.sel)
        self.redraw()

    def toggle_gauge(self, key):
        """Switch a pair of measure lines on or off. A new pair starts on the
        box edges, so it changes nothing until you move one."""
        if self.sel is None:
            if any(self.gauge_on[k].get() for k in ("h", "v")):
                self.gauge_on[key].set(False)
                messagebox.showinfo("No view selected",
                                    "Select a view first.", parent=self)
            return
        v = self.views[self.sel]
        fld = "gauge_h" if key == "h" else "gauge_v"
        if self.gauge_on[key].get():
            lo, hi = (v["x0"], v["x1"]) if key == "h" else (v["y0"], v["y1"])
            v[fld] = [int(lo), int(hi)]
        else:
            v[fld] = None
        self.sync_fields()
        self.update_row(self.sel)
        self.redraw()

    def toggle_proportional(self):
        on = self.proportional.get()
        for e in self.dim_entry.values():
            e.configure(state="disabled" if on else "normal")
        self.prop_note.configure(
            text=("Sizes come from the drawing itself. The planes keep the "
                  "blueprint's proportions and the object is scaled so its "
                  f"largest side is {core.PROPORTIONAL_MM:.0f} mm. Nothing "
                  "exported is a real world measurement."
                  if on else
                  "Length, width and height of the whole object, in mm."))

    def apply_edit(self):
        """Apply the typed box and measure line numbers."""
        if self.sel is None or self.ink is None:
            return
        v = self.views[self.sel]
        self.apply_meta()
        try:
            nb = {k: int(self.box[k].get()) for k in ("x0", "x1", "y0", "y1")}
            gv = {k: [int(x.get()) for x in self.gauge_val[k]] for k in ("h", "v")}
        except Exception:
            messagebox.showerror("Positions must be whole numbers",
                                 "Enter pixel positions, e.g. 153", parent=self)
            return
        if nb["x1"] <= nb["x0"] or nb["y1"] <= nb["y0"]:
            messagebox.showerror("Box is inside out",
                                 "x1 must be greater than x0, and y1 greater than y0.",
                                 parent=self)
            return
        h, w = self.ink.shape
        cl = dict(x0=max(0, min(nb["x0"], w - 1)), x1=max(0, min(nb["x1"], w - 1)),
                  y0=max(0, min(nb["y0"], h - 1)), y1=max(0, min(nb["y1"], h - 1)))
        if cl != nb:
            messagebox.showwarning(
                "Box pulled back onto the blueprint",
                f"The blueprint is {w} x {h} px. Edges outside it have been "
                f"clamped, because the box is what the measurement is taken "
                f"from.", parent=self)
            nb = cl
        if (nb["x0"], nb["x1"], nb["y0"], nb["y1"]) != (v["x0"], v["x1"], v["y0"], v["y1"]):
            v.update(nb)
            self._to_box(v)
        for key, fld, lim in (("h", "gauge_h", w - 1), ("v", "gauge_v", h - 1)):
            if self.gauge_on[key].get():
                v[fld] = [max(0, min(lim, t)) for t in gv[key]]
            else:
                v[fld] = None
        self.sync_fields()
        self.update_row(self.sel)
        self.redraw()

    def del_view(self):
        if self.sel is None:
            return
        self.views.pop(self.sel)
        self.sel = None
        self.refresh_list()
        self.redraw()

    # Editing: handles, and dragging a new box
    def _hit(self, i, cx, cy):
        """What part of view i is under the pointer, if any."""
        v = self.views[i]
        for g in self._gauge_pts(v):
            if abs(cx - g["cx"]) <= HANDLE and abs(cy - g["cy"]) <= HANDLE:
                return dict(i=i, kind="gauge", axis=g["axis"], idx=g["idx"])
        x0, y0, x1, y1 = self._box_screen(v)
        ex = -1 if abs(cx - x0) <= HANDLE else (1 if abs(cx - x1) <= HANDLE else 0)
        ey = -1 if abs(cy - y0) <= HANDLE else (1 if abs(cy - y1) <= HANDLE else 0)
        inx = x0 - HANDLE <= cx <= x1 + HANDLE
        iny = y0 - HANDLE <= cy <= y1 + HANDLE
        if ex and ey:
            return dict(i=i, kind="box", ex=ex, ey=ey)
        if ex and iny:
            return dict(i=i, kind="box", ex=ex, ey=0)
        if ey and inx:
            return dict(i=i, kind="box", ex=0, ey=ey)
        if inx and iny and i == self.sel:
            return dict(i=i, kind="move", ex=0, ey=0)
        return None

    def _grab(self, cx, cy):
        """Selected view's handles win, so a box under another one stays
        editable."""
        order = ([self.sel] if self.sel is not None else [])
        order += [j for j in reversed(range(len(self.views))) if j != self.sel]
        for i in order:
            hit = self._hit(i, cx, cy)
            if hit:
                return hit
        return None

    def _to_box(self, v):
        """An edited auto-detected region becomes a hand box.

        It has to. A component measures its own ink and ignores its rectangle,
        so the rectangle you just dragged would do nothing.
        """
        if v["source"] == "component":
            v["source"], v["cid"] = "box", None
            return True
        return False

    def on_press(self, e):
        self.canvas.focus_set()
        if self.base is None:
            return
        if e.state & 0x0004:          # ctrl-drag pans, for a mouse with no wheel click
            self.pan_start(e)
            return
        ix, iy = self.c2i(e.x, e.y)
        if not (e.state & 0x0001):    # shift forces a new box over an existing one
            hit = self._grab(e.x, e.y)
            if hit:
                if hit["i"] != self.sel:
                    self.select(hit["i"])
                v = self.views[hit["i"]]
                hit["box"] = (v["x0"], v["x1"], v["y0"], v["y1"])
                hit["g"] = (list(v.get("gauge_h") or []),
                            list(v.get("gauge_v") or []))
                hit["start"] = (ix, iy)
                hit["converted"] = False
                self.edit = hit
                return
            for i in reversed(range(len(self.views))):
                v = self.views[i]
                if v["x0"] <= ix <= v["x1"] and v["y0"] <= iy <= v["y1"]:
                    self.select(i)
                    return
        self.drag = [ix, iy, ix, iy]

    def on_move(self, e):
        if self.pan:
            self.pan_move(e)
            return
        if self.edit:
            self._drag_edit(*self.c2i(e.x, e.y))
            return
        if not self.drag:
            return
        self.drag[2], self.drag[3] = self.c2i(e.x, e.y)
        self.redraw()

    def _drag_edit(self, ix, iy):
        ed = self.edit
        v = self.views[ed["i"]]
        h, w = self.ink.shape
        dx, dy = ix - ed["start"][0], iy - ed["start"][1]

        if ed["kind"] == "gauge":
            fld = "gauge_h" if ed["axis"] == 0 else "gauge_v"
            g = list(ed["g"][ed["axis"]])
            if len(g) < 2:
                return
            lim = (w - 1) if ed["axis"] == 0 else (h - 1)
            g[ed["idx"]] = int(round(max(0, min(lim, g[ed["idx"]] +
                                                (dx if ed["axis"] == 0 else dy)))))
            v[fld] = g
        else:
            x0, x1, y0, y1 = ed["box"]
            if ed["kind"] == "move":
                bw, bh = x1 - x0, y1 - y0
                nx0 = int(round(max(0, min(w - 1 - bw, x0 + dx))))
                ny0 = int(round(max(0, min(h - 1 - bh, y0 + dy))))
                # Measure lines stay put on an edge drag, but the whole view
                # moving means they have to come with it.
                sx, sy = nx0 - x0, ny0 - y0
                for fld, s, lim in (("gauge_h", sx, w - 1), ("gauge_v", sy, h - 1)):
                    src = ed["g"][0 if fld == "gauge_h" else 1]
                    if src:
                        v[fld] = [int(max(0, min(lim, t + s))) for t in src]
                x0, x1, y0, y1 = nx0, nx0 + bw, ny0, ny0 + bh
            else:
                if ed["ex"] < 0:
                    x0 += dx
                elif ed["ex"] > 0:
                    x1 += dx
                if ed["ey"] < 0:
                    y0 += dy
                elif ed["ey"] > 0:
                    y1 += dy
            x0, x1 = sorted((int(round(x0)), int(round(x1))))
            y0, y1 = sorted((int(round(y0)), int(round(y1))))
            x0 = max(0, min(x0, w - 1 - MIN_BOX))
            y0 = max(0, min(y0, h - 1 - MIN_BOX))
            x1 = max(x0 + MIN_BOX, min(x1, w - 1))
            y1 = max(y0 + MIN_BOX, min(y1, h - 1))
            v.update(x0=x0, x1=x1, y0=y0, y1=y1)
            ed["converted"] |= self._to_box(v)
        self.sync_fields()
        self.redraw()

    def on_release(self, e):
        if self.pan:
            self.pan_end(e)
            return
        if self.edit:
            ed, self.edit = self.edit, None
            self.sync_fields()
            self.update_row(ed["i"])
            self.redraw()
            if ed.get("converted"):
                self.say(f"View {ed['i']} was auto-detected and has been edited, "
                         f"so it is now a hand box: its rectangle is the "
                         f"measurement, and ink outside it is clipped. Put the "
                         f"edges on the object's extremes.")
            return
        if not self.drag:
            return
        ax, ay = self.drag[0], self.drag[1]
        bx, by = self.c2i(e.x, e.y)
        self.drag = None
        x0, x1 = sorted((int(round(ax)), int(round(bx))))
        y0, y1 = sorted((int(round(ay)), int(round(by))))
        # A tiny drag is a misclick, so it has to clear a minimum both on the
        # blueprint and on screen.
        if (x1 - x0 < MIN_BOX or y1 - y0 < MIN_BOX or
                (x1 - x0) * self.zoom < 6 or (y1 - y0) * self.zoom < 6):
            self.redraw()
            return
        h, w = self.ink.shape
        self.views.append(core.new_view(source="box",
                                        x0=max(0, x0), x1=min(w - 1, x1),
                                        y0=max(0, y0), y1=min(h - 1, y1)))
        self.refresh_list()
        self.select(len(self.views) - 1)

    # Checking and exporting
    def num(self, var, label):
        try:
            return float(var.get())
        except Exception:
            raise core.SolveError(
                f"'{label}' is blank or is not a number. Type a plain figure, "
                f"e.g. 273.5")

    def dims(self):
        if self.proportional.get():
            return core.proportional_dims(self.views, self.ink, self.lab)
        out = {}
        for k, lbl in (("L", "length"), ("W", "width"), ("H", "height")):
            val = self.num(self.dim[k], lbl)
            if val > 0:
                out[k] = val
        return out

    def analyse(self):
        if self.ink is None:
            messagebox.showinfo("No blueprint open", "Open a blueprint image first.",
                                parent=self)
            return
        try:
            dims = self.dims()
            rep = core.solve(self.views, self.ink, self.lab, dims)
        except core.SolveError as e:
            self.say("Cannot check the blueprint yet\n  " + str(e))
            return
        except Exception:
            self.say(traceback.format_exc())
            return
        L = []
        if self.proportional.get():
            L += ["proportional mode - these millimetres are not measurements",
                  "  " + "   ".join(f"{k} {v:.1f}" for k, v in sorted(dims.items())),
                  ""]
        L += ["scale implied by each view", "",
              f"  {'view':10s} {'axis':5s} {'px':>7s} {'mm/px':>9s}   out of square"]
        for r in rep["views"]:
            for ax, px, mm, gauged, span in (
                    (r["axis_h"], r["gauge_h"], r["mm_px_h"], r["gauged_h"], r["span_h"]),
                    (r["axis_v"], r["gauge_v"], r["mm_px_v"], r["gauged_v"], r["span_v"])):
                head = r["role"] if ax == r["axis_h"] else ""
                skew = (f"   {abs(r['anisotropy']-1)*100:5.1f}%"
                        if ax == r["axis_h"] else "")
                note = f"   measure lines, view spans {span} px" if gauged else ""
                L.append(f"  {head:10s} {ax:5s} {px:7d} {mm:9.4f}{skew}{note}")
        L.append("")
        if rep["warnings"]:
            L.append("WARNINGS")
            for w in rep["warnings"]:
                L.append("  - " + w)
        else:
            L.append("No problems found. The views agree with each other.")
        self.say("\n".join(L))

    def do_export(self):
        if self.ink is None:
            messagebox.showinfo("No blueprint open", "Open a blueprint image first.",
                                parent=self)
            return
        outdir = filedialog.askdirectory(title="Where should the planes go?", parent=self)
        if not outdir:
            return
        try:
            stale = [f for f in os.listdir(outdir) if f.lower().endswith(".png")]
        except OSError:
            stale = []
        if stale and not messagebox.askyesno(
                "That folder already has planes in it",
                f"{outdir}\n\nalready contains {len(stale)} PNG file(s).\n\n"
                f"Leftovers from an earlier export will not line up with this "
                f"one, and nothing in Blender will show you which is which.\n\n"
                f"Export here anyway?", parent=self):
            return
        try:
            meta, rep = core.export(self.views, self.ink, self.lab, self.dims(),
                                    outdir, detail=self.detail.get(),
                                    mode=self.mode.get(),
                                    proportional=self.proportional.get())
            vtxt, worst, problems = core.verify(outdir, meta)
            btxt = core.blender_report(meta)
        except core.SolveError as e:
            self.say("Nothing exported\n  " + str(e))
            return
        except Exception:
            self.say(traceback.format_exc())
            return

        verdict = ("EVERY PLANE CHECKS OUT" if not problems
                   else f"{len(problems)} PROBLEM(S) - see VERIFICATION below")
        head = (f"Wrote {len(meta['files'])} planes to\n  {outdir}\n\n"
                f"{verdict}\n"
                f"Worst error against what this mode set out to produce: "
                f"{worst:.2f} mm ({worst * meta['px_per_mm']:.1f} canvas px)\n"
                f"Rendered at {meta['px_per_mm']:.2f} px/mm "
                f"({self.detail.get()} detail), canvas "
                f"{meta['canvas_px'][0]} x {meta['canvas_px'][1]} px\n")
        if rep["warnings"]:
            head += "\nBLUEPRINT WARNINGS\n" + "\n".join("  - " + w for w in rep["warnings"]) + "\n"
        body = head + "\n" + btxt + "\n\nVERIFICATION\n" + vtxt + "\n"
        with open(os.path.join(outdir, "report.txt"), "w", encoding="utf-8") as f:
            f.write(body)
        self.say(body)


if __name__ == "__main__":
    App().mainloop()
