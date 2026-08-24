# ViewForge

Turns a multi-view blueprint sheet into orthographic reference planes that
actually line up in Blender, and prints the numbers to place them with.

## Install

```
pip install pillow numpy scipy
python viewforge_gui.py
```

Tkinter ships with Python on Windows and macOS. On Linux: `sudo apt install python3-tk`.

## Workflow

1. **Open sheet.** Any image with several views of one object on it.
2. **Detect views.** Each blob of ink becomes a candidate region. If the tool
   reports that it merged everything into one region, the sheet's dimension
   lines are joining the views together, skip to step 3 and draw boxes by hand.
3. **Assign roles.** Select each region, set its role (side / top / bottom /
   front / back), and say which edge the object's **front** sits against.
   Anything left on `ignore` is skipped, which is how you exclude detail insets,
   title blocks, and parts diagrams.
4. **Enter dimensions.** Length, width, height in mm.
5. **Check the sheet.** Read the warnings before exporting.
6. **Export planes.** Writes one PNG per view, a `report.txt`, and a
   `placement.json`.

## Drawing boxes by hand

Drag on the sheet. For a hand-drawn box **the box is the measurement**. Put its
edges exactly on the object's extremes, because the tool takes the box width as
your stated length and the box height as your stated height. Ink outside the box
is clipped, which is how you keep annotation text and neighbouring views out.

Auto-detected regions work differently: they measure their own ink, and they
render from their own connected component only, so nothing sitting in their
whitespace can leak in.

Box edges typed past the edge of the sheet are pulled back onto it, and you are
told. They cannot be left where you typed them the box *is* the measurement, so
an edge hanging off the sheet would be measured at its stated position while
only the ink inside it actually got used, a scale error with nothing on screen
to reveal it.

## Using the engine without the GUI

`viewforge_core` has no interface and never opens a window, so it can be
imported and driven directly to batch a pile of sheets. `verify()` returns
`(text, worst_mm, problems)`. `problems` is a list of strings, empty when every
plane checks out.

## Two things the checker tells you

**"Drawn N% out of square"**: that view's two dimensions imply two different
scales. Either one of the numbers you typed is wrong, or the view really is
stretched. A dimension that measures the wrong feature (a slide width when you
meant the overall width) shows up here immediately, as a very large percentage.

**"Views disagree on scale by N%"**: the sheet is not internally consistent.
In `fit` mode the planes will still line up, because each view gets stretched
independently to hit your numbers. In `uniform` mode each view keeps one scale,
so circles stay round and the mismatch stays visible instead of being hidden.
Use `uniform` when the object has round parts you must model (wheels, bores);
use `fit` when alignment matters more than fidelity to the drawing.

## Verification

After exporting, `report.txt` contains extents re-measured **from the written
PNG files**, not from the export step's own arithmetic. That is the number to
trust. Below it is an advisory internal-drift check, which compares ink profiles
along shared axes and openly reports when two views have too little in common
for the comparison to mean anything.

Two different numbers get reported, and confusing them will mislead you:

- **Export error**: how far each file is from what the chosen mode set out to
  produce. This is the tool grading itself, and it should be a fraction of a
  millimetre. In `uniform` mode it stays near zero even on a badly skewed sheet.
- **Deviation from the dimensions you typed**: in `fit` mode this matches the
  above. In `uniform` mode it is *expected* to be non-zero: it is the drawing's
  own inconsistency, left visible on purpose rather than squashed out.

A `PROBLEMS` block appears above both if any plane came out blank, touched the
canvas edge (i.e. got cut off), landed off centre, or was centred a long way
from its own bounding-box midpoint. That last one is deliberate behaviour,
`top`, `bottom`, `front` and `back` are centred on the mirror line found in the
drawing rather than on their bounding box, which is what you want for modelling
against a Mirror modifier, but it is only correct if the view really is
symmetric, so it is now stated outright instead of applied silently.

## Canvas size

`margin` is a multiplier on the canvas and must be at least `1.0`. Below that
the canvas is smaller than the object and the edges of your reference are cut
off, which is not something you want to discover after tracing.

The canvas is normally `max(L,W) × max(H,W)` scaled by `margin`, but it will
**grow beyond that** if centring on a mirror line would otherwise push part of a
view over the edge. When that happens the report says so. Every plane still
shares the one canvas, so the single Size and Offset pair still applies to all
of them, that property is never traded away.

## Blender

Every export writes **`viewforge_import.py`** alongside the planes. It builds all
of them for you, loads each PNG, sizes it, rotates it, positions it, and drops
the lot into an `viewforge` collection:

```
Blender > Scripting tab > Open > viewforge_import.py > Run Script
```

or `blender --python viewforge_import.py` from a terminal. It finds the PNGs in
its own folder, so keep the two together; move the whole folder and it still
works. Running it twice is safe. It clears its previous collection first rather
than stacking a second set of planes on the first, which is the usual way to end
up with two exports mixed together.

There is a settings block at the top for opacity, draw depth, whether the planes
are locked against being dragged, and whether each one only appears in its own
orthographic view.

The planes are created locked. Unlock in the N-panel, or set `LOCK = False`.

If you would rather place them by hand, `report.txt` still lists every rotation
and location. It gives one Size and one pair of Offsets that apply to **every**
plane, only rotation differs per view. That is deliberate: per-view Size values
are the easiest thing in this whole process to get wrong.

> The Size figure is the canvas's **longer** side in metres. That is not a guess
> about how Blender treats image empties. An image empty is drawn with its
> larger pixel dimension equal to `Size`, which was measured in Blender 5.2 by
> rendering a 636×1272 empty at `Size 0.5` through a 1.0-unit orthographic
> camera: it came out 99×199 px in a 400 px frame, not 200×400.

The object ends up centred on the world origin, length on Y with the front at
−Y, width on X, height on Z.

Press Numpad 5 before checking anything. Reference planes never line up in
perspective view, and that alone accounts for a good share of "my blueprint is
broken".
