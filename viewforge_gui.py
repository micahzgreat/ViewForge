#!/usr/bin/env python3
"""
viewforge - blueprint sheet to verified Blender reference planes.

Run:  python viewforge_gui.py
Needs: pip install pillow numpy scipy
"""

import os, traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageTk
import viewforge_core as core

PREVIEW_W, PREVIEW_H = 940, 620


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("viewforge - blueprint to Blender reference planes")
        self.geometry("1400x820")
        self.path = None
        self.gray = self.ink = self.lab = None
        self.views = []
        self.sel = None
        self.scale = 1.0
        self.photo = None
        self.drag = None
        self._build()

    # ------------------------------------------------------------ layout --
    def _build(self):
        left = ttk.Frame(self, padding=6); left.pack(side="left", fill="both", expand=True)
        right = ttk.Frame(self, padding=6); right.pack(side="right", fill="y")

        bar = ttk.Frame(left); bar.pack(fill="x")
        ttk.Button(bar, text="Open sheet...", command=self.open_sheet).pack(side="left")
        ttk.Label(bar, text="  merge gap").pack(side="left")
        self.gap = tk.IntVar(value=9)
        ttk.Spinbox(bar, from_=3, to=41, textvariable=self.gap, width=4).pack(side="left")
        ttk.Button(bar, text="Detect views", command=self.detect).pack(side="left", padx=4)
        ttk.Button(bar, text="Clear all", command=self.clear_views).pack(side="left")
        self.hint = ttk.Label(bar, text="  Drag on the sheet to add a view by hand.")
        self.hint.pack(side="left", padx=8)

        self.canvas = tk.Canvas(left, bg="#3a3a3a", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, pady=6)
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_move)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)

        # ---- views list
        ttk.Label(right, text="Views", font=("", 10, "bold")).pack(anchor="w")
        self.tree = ttk.Treeview(right, columns=("role", "facing", "flip", "src"),
                                 show="headings", height=9, selectmode="browse")
        for c, w in (("role", 76), ("facing", 60), ("flip", 44), ("src", 76)):
            self.tree.heading(c, text=c); self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill="x")
        self.tree.bind("<<TreeviewSelect>>", self.on_pick)

        ed = ttk.LabelFrame(right, text="Selected view", padding=6)
        ed.pack(fill="x", pady=6)
        self.role = tk.StringVar(value="ignore")
        self.facing = tk.StringVar(value="left")
        self.flip = tk.BooleanVar(value=False)
        ttk.Label(ed, text="role").grid(row=0, column=0, sticky="w")
        ttk.Combobox(ed, textvariable=self.role, values=core.ROLES, width=10,
                     state="readonly").grid(row=0, column=1, sticky="w")
        ttk.Label(ed, text="front of object is at").grid(row=1, column=0, sticky="w")
        ttk.Combobox(ed, textvariable=self.facing, values=["left", "right"], width=10,
                     state="readonly").grid(row=1, column=1, sticky="w")
        ttk.Checkbutton(ed, text="mirror this view", variable=self.flip).grid(
            row=2, column=0, columnspan=2, sticky="w")
        self.box = {}
        for i, k in enumerate(("x0", "x1", "y0", "y1")):
            ttk.Label(ed, text=k).grid(row=3 + i // 2, column=(i % 2) * 2, sticky="e")
            v = tk.IntVar(value=0); self.box[k] = v
            ttk.Entry(ed, textvariable=v, width=7).grid(row=3 + i // 2,
                                                        column=(i % 2) * 2 + 1, sticky="w")
        ttk.Button(ed, text="Apply", command=self.apply_edit).grid(row=5, column=0,
                                                                   pady=4, sticky="w")
        ttk.Button(ed, text="Delete view", command=self.del_view).grid(row=5, column=2,
                                                                       pady=4, sticky="w")

        dm = ttk.LabelFrame(right, text="Object dimensions (mm)", padding=6)
        dm.pack(fill="x")
        self.dim = {}
        for i, (k, lbl) in enumerate((("L", "length"), ("W", "width"), ("H", "height"))):
            ttk.Label(dm, text=lbl).grid(row=i, column=0, sticky="e")
            v = tk.DoubleVar(value=0.0); self.dim[k] = v
            ttk.Entry(dm, textvariable=v, width=10).grid(row=i, column=1, sticky="w")

        op = ttk.LabelFrame(right, text="Output", padding=6); op.pack(fill="x", pady=6)
        self.ppm = tk.DoubleVar(value=4.0)
        self.margin = tk.DoubleVar(value=1.06)
        self.mode = tk.StringVar(value="fit")
        ttk.Label(op, text="px per mm").grid(row=0, column=0, sticky="e")
        ttk.Entry(op, textvariable=self.ppm, width=8).grid(row=0, column=1, sticky="w")
        ttk.Label(op, text="margin").grid(row=1, column=0, sticky="e")
        ttk.Entry(op, textvariable=self.margin, width=8).grid(row=1, column=1, sticky="w")
        ttk.Label(op, text="scaling").grid(row=2, column=0, sticky="e")
        ttk.Combobox(op, textvariable=self.mode, values=["fit", "uniform"], width=8,
                     state="readonly").grid(row=2, column=1, sticky="w")

        ttk.Button(right, text="Check the sheet", command=self.analyse).pack(fill="x")
        ttk.Button(right, text="Export planes", command=self.do_export).pack(fill="x", pady=4)

        self.out = tk.Text(right, width=62, height=22, wrap="none",
                           font=("Courier New", 8))
        self.out.pack(fill="both", expand=True)

    # -------------------------------------------------------------- utils --
    def say(self, text, append=False):
        if not append:
            self.out.delete("1.0", "end")
        self.out.insert("end", text + "\n")
        self.out.see("end")

    def i2c(self, x, y):
        return x * self.scale, y * self.scale

    def c2i(self, x, y):
        return int(round(x / self.scale)), int(round(y / self.scale))

    # --------------------------------------------------------------- load --
    def open_sheet(self):
        p = filedialog.askopenfilename(
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff"),
                       ("All files", "*.*")])
        if not p:
            return
        try:
            self.gray, self.ink, thr = core.load_sheet(p)
        except Exception as e:
            messagebox.showerror("Could not open that file", str(e)); return
        self.path = p
        self.views, self.sel, self.lab = [], None, None
        h, w = self.ink.shape
        self.scale = min(PREVIEW_W / w, PREVIEW_H / h, 1.0)
        disp = Image.open(p).convert("L").resize(
            (max(1, int(w * self.scale)), max(1, int(h * self.scale))), Image.LANCZOS)
        self.photo = ImageTk.PhotoImage(disp)
        self.redraw()
        self.say(f"{os.path.basename(p)}\n  {w} x {h} px, ink threshold {thr}\n"
                 f"  Press Detect views, or drag a box on the sheet.")

    def redraw(self):
        self.canvas.delete("all")
        if self.photo:
            self.canvas.create_image(0, 0, anchor="nw", image=self.photo)
        for i, v in enumerate(self.views):
            x0, y0 = self.i2c(v["x0"], v["y0"]); x1, y1 = self.i2c(v["x1"], v["y1"])
            on = (i == self.sel)
            col = "#ff4444" if on else ("#22aa55" if v["role"] != "ignore" else "#888888")
            self.canvas.create_rectangle(x0, y0, x1, y1, outline=col, width=3 if on else 2)
            self.canvas.create_text(x0 + 4, y0 + 4, anchor="nw",
                                    text=f"{i}:{v['role']}", fill=col)

    # ------------------------------------------------------------ detect --
    def detect(self):
        if self.ink is None:
            messagebox.showinfo("No sheet open", "Open a blueprint image first."); return
        self.views, self.lab = core.detect_views(self.ink, gap=self.gap.get())
        self.sel = None
        self.refresh_list(); self.redraw()
        msgs = core.segmentation_quality(self.views, self.ink)
        self.say(f"Found {len(self.views)} regions.")
        for m in msgs:
            self.say("  " + m, append=True)
        self.say("  Select each one and give it a role. Anything left as "
                 "'ignore' is skipped.", append=True)

    def clear_views(self):
        self.views, self.sel = [], None
        self.refresh_list(); self.redraw()

    def refresh_list(self):
        self.tree.delete(*self.tree.get_children())
        for i, v in enumerate(self.views):
            self.tree.insert("", "end", iid=str(i),
                             values=(v["role"], v["facing"],
                                     "yes" if v["hflip"] else "", v["source"]))

    # ---------------------------------------------------------- selection --
    def on_pick(self, _=None):
        s = self.tree.selection()
        if not s:
            return
        self.sel = int(s[0]); v = self.views[self.sel]
        self.role.set(v["role"]); self.facing.set(v["facing"]); self.flip.set(v["hflip"])
        for k in ("x0", "x1", "y0", "y1"):
            self.box[k].set(v[k])
        self.redraw()

    def apply_edit(self):
        if self.sel is None:
            return
        v = self.views[self.sel]
        v["role"], v["facing"], v["hflip"] = self.role.get(), self.facing.get(), self.flip.get()
        try:
            nb = {k: int(self.box[k].get()) for k in ("x0", "x1", "y0", "y1")}
        except Exception:
            messagebox.showerror("Box edges must be whole numbers",
                                 "Enter pixel positions, e.g. 153"); return
        if nb["x1"] <= nb["x0"] or nb["y1"] <= nb["y0"]:
            messagebox.showerror("Box is inside out",
                                 "x1 must be greater than x0, and y1 greater than y0.")
            return
        h, w = self.ink.shape
        cl = dict(x0=max(0, min(nb["x0"], w - 1)), x1=max(0, min(nb["x1"], w - 1)),
                  y0=max(0, min(nb["y0"], h - 1)), y1=max(0, min(nb["y1"], h - 1)))
        if cl != nb:
            # The box IS the measurement, so an edge off the sheet would
            # otherwise be measured at its typed position while only the ink
            # inside got used - a scale error with nothing on screen to show it.
            messagebox.showwarning(
                "Box pulled back onto the sheet",
                f"The sheet is {w} x {h} px. Edges outside it have been clamped, "
                f"because the box is what the measurement is taken from.")
            nb = cl
            for k in nb:
                self.box[k].set(nb[k])
        if (nb["x0"], nb["x1"], nb["y0"], nb["y1"]) != (v["x0"], v["x1"], v["y0"], v["y1"]):
            v.update(nb); v["source"] = "box"   # hand-edited edges define the size
        self.refresh_list(); self.redraw()

    def del_view(self):
        if self.sel is None:
            return
        self.views.pop(self.sel); self.sel = None
        self.refresh_list(); self.redraw()

    # ------------------------------------------------------- box drawing --
    def on_press(self, e):
        if self.ink is None:
            return
        ix, iy = self.c2i(e.x, e.y)
        for i in reversed(range(len(self.views))):
            v = self.views[i]
            if v["x0"] <= ix <= v["x1"] and v["y0"] <= iy <= v["y1"]:
                self.tree.selection_set(str(i)); self.on_pick(); return
        self.drag = (e.x, e.y)

    def on_move(self, e):
        if not self.drag:
            return
        self.redraw()
        self.canvas.create_rectangle(self.drag[0], self.drag[1], e.x, e.y,
                                     outline="#ff4444", width=2, dash=(4, 3))

    def on_release(self, e):
        if not self.drag:
            return
        x0, y0 = self.c2i(min(self.drag[0], e.x), min(self.drag[1], e.y))
        x1, y1 = self.c2i(max(self.drag[0], e.x), max(self.drag[1], e.y))
        self.drag = None
        if x1 - x0 < 8 or y1 - y0 < 8:
            self.redraw(); return
        h, w = self.ink.shape
        self.views.append(core.new_view(source="box",
                                        x0=max(0, x0), x1=min(w - 1, x1),
                                        y0=max(0, y0), y1=min(h - 1, y1)))
        self.sel = len(self.views) - 1
        self.refresh_list(); self.tree.selection_set(str(self.sel)); self.on_pick()

    # ------------------------------------------------------------- action --
    def num(self, var, label):
        """Read a numeric entry. Tk raises on a blank or malformed box, and an
        unhandled TclError reads as a crash rather than as 'you left that
        empty', so it is turned into something actionable here."""
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
            messagebox.showinfo("No sheet open", "Open a blueprint image first."); return
        try:
            rep = core.solve(self.views, self.ink, self.lab, self.dims())
        except core.SolveError as e:
            self.say("Cannot check the sheet yet\n  " + str(e)); return
        except Exception:
            self.say(traceback.format_exc()); return
        L = ["scale implied by each view", ""]
        L.append(f"  {'view':10s} {'axis':5s} {'px':>7s} {'mm/px':>9s}   out of square")
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
            messagebox.showinfo("No sheet open", "Open a blueprint image first."); return
        outdir = filedialog.askdirectory(title="Where should the planes go?")
        if not outdir:
            return
        # Mixing a new export with an old one is the single easiest way to end
        # up with planes that do not line up: the files look right, and only
        # the canvas height differs between runs.
        try:
            stale = [f for f in os.listdir(outdir) if f.lower().endswith(".png")]
        except OSError:
            stale = []
        if stale and not messagebox.askyesno(
                "That folder already has planes in it",
                f"{outdir}\n\nalready contains {len(stale)} PNG file(s).\n\n"
                f"Leftovers from an earlier export will not line up with this "
                f"one, and nothing in Blender will show you which is which.\n\n"
                f"Export here anyway?"):
            return
        try:
            meta, rep = core.export(self.views, self.ink, self.lab, self.dims(),
                                    outdir, px_per_mm=self.num(self.ppm, "px per mm"),
                                    margin=self.num(self.margin, "margin"),
                                    mode=self.mode.get())
            vtxt, worst, problems = core.verify(outdir, meta)
            btxt = core.blender_report(meta)
        except core.SolveError as e:
            self.say("Nothing exported\n  " + str(e)); return
        except Exception:
            self.say(traceback.format_exc()); return

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
