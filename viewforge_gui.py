#!/usr/bin/env python3
"""
viewforge - blueprint sheet to verified Blender reference planes.

Run:  python viewforge_gui.py
Needs: pip install pillow numpy scipy customtkinter
"""

import os, traceback
import tkinter as tk
from tkinter import filedialog, messagebox
import customtkinter as ctk
from PIL import Image, ImageTk
import viewforge_core as core

HERE = os.path.dirname(os.path.abspath(__file__))
ICON = os.path.join(HERE, "Assets", "icon.ico")

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

BG_PANEL = "#202226"
BG_CARD  = "#26282d"
BG_SUNK  = "#141517"
FG_MUTED = "#8b919b"
ACCENT   = "#3b82f6"
ACCENT_H = "#2f6fd0"
SHEET_BG = "#1b1c1e"

COL_SEL = "#ff5f5f"
COL_SET = "#3ddc84"
COL_OFF = "#9aa0aa"

MONO    = ("Consolas", 12)
MONO_SM = ("Consolas", 11)
UI      = ("Segoe UI", 12)
UI_B    = ("Segoe UI", 13, "bold")
UI_CAP  = ("Segoe UI", 11, "bold")

ZOOM_MIN, ZOOM_MAX = 0.05, 32.0


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
        self.pan = None
        self.fitted = False

        self._build()
        self._set_icon()

    # CustomTkinter takes its geometry in scaled units and multiplies by the
    # display's DPI factor, so a fixed 1520x950 becomes 1900x1154 on a 125%
    # screen and hangs off the edge. Everything here is worked out in the same
    # scaled units the geometry string is read in.
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

    # CustomTkinter re-applies its own window icon shortly after the window
    # appears, so ours has to be set again once that has happened.
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
        self._build_sheet()
        self._build_sidebar()
        self._build_console()

    def _build_header(self):
        bar = ctk.CTkFrame(self, corner_radius=0, fg_color=BG_PANEL, height=62)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        bar.grid_propagate(False)
        pad = dict(side="left", padx=6, pady=14)

        ctk.CTkLabel(bar, text="ViewForge", font=("Segoe UI", 17, "bold")).pack(
            side="left", padx=(16, 14), pady=14)
        ctk.CTkButton(bar, text="Open sheet", width=110, font=UI,
                      command=self.open_sheet).pack(**pad)
        self.file_lbl = ctk.CTkLabel(bar, text="no sheet loaded", font=UI,
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

    def _build_sheet(self):
        wrap = ctk.CTkFrame(self, fg_color="transparent")
        wrap.grid(row=1, column=0, sticky="nsew", padx=(12, 6), pady=(12, 6))
        wrap.grid_rowconfigure(0, weight=1)
        wrap.grid_columnconfigure(0, weight=1)

        shell = ctk.CTkFrame(wrap, corner_radius=10, fg_color=BG_PANEL)
        shell.grid(row=0, column=0, sticky="nsew")
        shell.grid_rowconfigure(0, weight=1)
        shell.grid_columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(shell, bg=SHEET_BG, highlightthickness=0, bd=0)
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
        self.hint = ctk.CTkLabel(nav, text="wheel zooms  |  middle drag pans",
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
        ctk.CTkLabel(b, text="front of object is at", font=UI, anchor="w").grid(
            row=1, column=0, sticky="w", pady=4)
        ctk.CTkOptionMenu(b, variable=self.facing, values=["left", "right"], width=160,
                          font=UI, command=lambda _=None: self.apply_meta()).grid(
            row=1, column=1, sticky="e", pady=4)
        ctk.CTkSwitch(b, text="mirror this view", variable=self.flip, font=UI,
                      command=self.apply_meta).grid(row=2, column=0, columnspan=2,
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

        a = ctk.CTkFrame(ed, fg_color="transparent")
        a.pack(fill="x", padx=12, pady=(0, 12))
        ctk.CTkButton(a, text="Apply box", font=UI, command=self.apply_edit).pack(
            side="left", expand=True, fill="x", padx=(0, 4))
        ctk.CTkButton(a, text="Delete view", font=UI, fg_color="#5a2b2b",
                      hover_color="#6d3434", command=self.del_view).pack(
            side="left", expand=True, fill="x", padx=(4, 0))

        dm = self._card(side, "Object dimensions (mm)")
        d = ctk.CTkFrame(dm, fg_color="transparent")
        d.pack(fill="x", padx=12, pady=(0, 12))
        d.grid_columnconfigure(1, weight=1)
        self.dim = {}
        for i, (k, lbl) in enumerate((("L", "length"), ("W", "width"), ("H", "height"))):
            ctk.CTkLabel(d, text=lbl, font=UI, anchor="w").grid(
                row=i, column=0, sticky="w", pady=4)
            v = tk.DoubleVar(value=0.0)
            self.dim[k] = v
            ctk.CTkEntry(d, textvariable=v, width=140, font=MONO).grid(
                row=i, column=1, sticky="e", pady=4)

        op = self._card(side, "Output")
        o = ctk.CTkFrame(op, fg_color="transparent")
        o.pack(fill="x", padx=12, pady=(0, 12))
        o.grid_columnconfigure(1, weight=1)
        self.ppm = tk.DoubleVar(value=4.0)
        self.margin = tk.DoubleVar(value=1.06)
        self.mode = tk.StringVar(value="fit")
        ctk.CTkLabel(o, text="px per mm", font=UI, anchor="w").grid(
            row=0, column=0, sticky="w", pady=4)
        ctk.CTkEntry(o, textvariable=self.ppm, width=140, font=MONO).grid(
            row=0, column=1, sticky="e", pady=4)
        ctk.CTkLabel(o, text="margin", font=UI, anchor="w").grid(
            row=1, column=0, sticky="w", pady=4)
        ctk.CTkEntry(o, textvariable=self.margin, width=140, font=MONO).grid(
            row=1, column=1, sticky="e", pady=4)
        ctk.CTkLabel(o, text="scaling", font=UI, anchor="w").grid(
            row=2, column=0, sticky="w", pady=4)
        ctk.CTkOptionMenu(o, variable=self.mode, values=["fit", "uniform"],
                          width=140, font=UI).grid(row=2, column=1, sticky="e", pady=4)

        # The two actions sit outside the scrolling area, so a sheet with a lot
        # of detected regions cannot push them off the bottom.
        act = ctk.CTkFrame(col, fg_color="transparent")
        act.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        ctk.CTkButton(act, text="Check the sheet", height=38, font=UI_B,
                      fg_color=BG_CARD, hover_color="#33363c",
                      command=self.analyse).pack(fill="x", pady=(0, 8))
        ctk.CTkButton(act, text="Export planes", height=42, font=UI_B,
                      fg_color=ACCENT, hover_color=ACCENT_H,
                      command=self.do_export).pack(fill="x")
        self.refresh_list()

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
        self.say("Open a blueprint sheet to begin.\n"
                 "  Detect views finds each blob of ink, or drag a box on the sheet by hand.\n"
                 "  Wheel zooms, middle or right drag pans, F fits the sheet to the window.")

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

    # Navigating the sheet
    def clamp(self):
        """Keep the sheet inside the viewport, centred on any axis it fits on."""
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
        """Set the zoom, keeping the sheet point under (cx, cy) where it is."""
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
            # Only the visible slice is ever resampled, so a big sheet stays
            # responsive at any zoom. Past 1:1 nearest keeps the pixel edges
            # hard, which is the point of zooming in to place a box edge.
            samp = Image.NEAREST if self.zoom > 2.0 else Image.BILINEAR
            reg = self.base.resize((dw, dh), samp, box=(sx0, sy0, sx1, sy1))
            self.photo = ImageTk.PhotoImage(reg)
            px, py = self.i2c(sx0, sy0)
            c.create_image(round(px), round(py), anchor="nw", image=self.photo)

        for i, v in enumerate(self.views):
            x0, y0 = self.i2c(v["x0"], v["y0"])
            x1, y1 = self.i2c(v["x1"] + 1, v["y1"] + 1)
            on = (i == self.sel)
            col = COL_SEL if on else (COL_SET if v["role"] != "ignore" else COL_OFF)
            c.create_rectangle(x0, y0, x1, y1, outline=col, width=3 if on else 2)
            c.create_text(x0 + 6, y0 + 4, anchor="nw", text="%d  %s" % (i, v["role"]),
                          fill=col, font=("Segoe UI", 10, "bold"))

        if self.drag:
            a = self.i2c(self.drag[0], self.drag[1])
            b = self.i2c(self.drag[2], self.drag[3])
            c.create_rectangle(a[0], a[1], b[0], b[1], outline=COL_SEL,
                               width=2, dash=(5, 4))
        self.zoom_lbl.configure(text="%.0f%%" % (self.zoom * 100))

    # Loading a sheet
    def open_sheet(self):
        p = filedialog.askopenfilename(
            parent=self,
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff"),
                       ("All files", "*.*")])
        if not p:
            return
        try:
            self.gray, self.ink, thr = core.load_sheet(p)
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
                 f"  Press Detect views, or drag a box on the sheet.")

    # Detecting and managing views
    def detect(self):
        if self.ink is None:
            messagebox.showinfo("No sheet open", "Open a blueprint image first.",
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
                 "'ignore' is skipped.", append=True)

    def clear_views(self):
        self.views, self.sel = [], None
        self.refresh_list()
        self.redraw()

    def _row_text(self, i):
        v = self.views[i]
        face = v["facing"] if v["role"] in ("side", "top", "bottom") else "-"
        return (f"{i:<3}{v['role']:<9}{face:<7}"
                f"{'mirror' if v['hflip'] else '':<8}{v['source']}")

    def refresh_list(self):
        for w in self.rows:
            w.destroy()
        self.rows = []
        if not self.views:
            lbl = ctk.CTkLabel(self.list, justify="left", font=UI, text_color=FG_MUTED,
                               text="Nothing yet. Detect views, or drag\na box on the "
                                    "sheet by hand.")
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
        for k in ("x0", "x1", "y0", "y1"):
            self.box[k].set(v[k])
        self.highlight()
        self.redraw()

    def apply_meta(self):
        """Role, facing and mirror take effect the moment they are changed."""
        if self.sel is None:
            return
        v = self.views[self.sel]
        v["role"] = self.role.get()
        v["facing"] = self.facing.get()
        v["hflip"] = self.flip.get()
        self.update_row(self.sel)
        self.redraw()

    def apply_edit(self):
        if self.sel is None or self.ink is None:
            return
        v = self.views[self.sel]
        self.apply_meta()
        try:
            nb = {k: int(self.box[k].get()) for k in ("x0", "x1", "y0", "y1")}
        except Exception:
            messagebox.showerror("Box edges must be whole numbers",
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
                "Box pulled back onto the sheet",
                f"The sheet is {w} x {h} px. Edges outside it have been clamped, "
                f"because the box is what the measurement is taken from.", parent=self)
            nb = cl
            for k in nb:
                self.box[k].set(nb[k])
        if (nb["x0"], nb["x1"], nb["y0"], nb["y1"]) != (v["x0"], v["x1"], v["y0"], v["y1"]):
            v.update(nb)
            v["source"] = "box"
        self.update_row(self.sel)
        self.redraw()

    def del_view(self):
        if self.sel is None:
            return
        self.views.pop(self.sel)
        self.sel = None
        self.refresh_list()
        self.redraw()

    # Dragging a box on the sheet, to add a view
    def on_press(self, e):
        self.canvas.focus_set()
        if self.base is None:
            return
        if e.state & 0x0004:          # ctrl-drag pans, for a mouse with no wheel click
            self.pan_start(e)
            return
        ix, iy = self.c2i(e.x, e.y)
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
        if not self.drag:
            return
        self.drag[2], self.drag[3] = self.c2i(e.x, e.y)
        self.redraw()

    def on_release(self, e):
        if self.pan:
            self.pan_end(e)
            return
        if not self.drag:
            return
        ax, ay = self.drag[0], self.drag[1]
        bx, by = self.c2i(e.x, e.y)
        self.drag = None
        x0, x1 = sorted((int(round(ax)), int(round(bx))))
        y0, y1 = sorted((int(round(ay)), int(round(by))))
        # A tiny drag is a misclick at any zoom, so it has to clear both a
        # minimum on the sheet and a minimum distance moved on screen.
        if (x1 - x0 < 3 or y1 - y0 < 3 or
                (x1 - x0) * self.zoom < 6 or (y1 - y0) * self.zoom < 6):
            self.redraw()
            return
        h, w = self.ink.shape
        self.views.append(core.new_view(source="box",
                                        x0=max(0, x0), x1=min(w - 1, x1),
                                        y0=max(0, y0), y1=min(h - 1, y1)))
        self.refresh_list()
        self.select(len(self.views) - 1)

    # Actions that use the core library to check the sheet or export planes
    def num(self, var, label):
        try:
            return float(var.get())
        except Exception:
            raise core.SolveError(
                f"'{label}' is blank or is not a number. Type a plain figure, "
                f"e.g. 273.5")

    def dims(self):
        out = {}
        for k, lbl in (("L", "length"), ("W", "width"), ("H", "height")):
            val = self.num(self.dim[k], lbl)
            if val > 0:
                out[k] = val
        return out

    def analyse(self):
        if self.ink is None:
            messagebox.showinfo("No sheet open", "Open a blueprint image first.",
                                parent=self)
            return
        try:
            rep = core.solve(self.views, self.ink, self.lab, self.dims())
        except core.SolveError as e:
            self.say("Cannot check the sheet yet\n  " + str(e))
            return
        except Exception:
            self.say(traceback.format_exc())
            return
        L = ["scale implied by each view", "",
             f"  {'view':10s} {'axis':5s} {'px':>7s} {'mm/px':>9s}   out of square"]
        for r in rep["views"]:
            L.append(f"  {r['role']:10s} {r['axis_h']:5s} {r['span_h']:7d} "
                     f"{r['mm_px_h']:9.4f}   {abs(r['anisotropy']-1)*100:5.1f}%")
            L.append(f"  {'':10s} {r['axis_v']:5s} {r['span_v']:7d} {r['mm_px_v']:9.4f}")
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
            messagebox.showinfo("No sheet open", "Open a blueprint image first.",
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
                                    outdir, px_per_mm=self.num(self.ppm, "px per mm"),
                                    margin=self.num(self.margin, "margin"),
                                    mode=self.mode.get())
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
                f"{worst:.2f} mm\n")
        if rep["warnings"]:
            head += "\nSHEET WARNINGS\n" + "\n".join("  - " + w for w in rep["warnings"]) + "\n"
        body = head + "\n" + btxt + "\n\nVERIFICATION\n" + vtxt + "\n"
        with open(os.path.join(outdir, "report.txt"), "w", encoding="utf-8") as f:
            f.write(body)
        self.say(body)


if __name__ == "__main__":
    App().mainloop()
