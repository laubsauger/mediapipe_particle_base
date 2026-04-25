"""
bootstrap_beatsaber_renderer.py
===============================

One-shot builder for the `beatsaber_renderer` Base COMP. Drop this
script into a Text DAT at the *project root* (same level as the .toe,
next to bootstrap_velocity_controller.py), right-click ▸ Run Script.

What it creates
---------------
1. A Base COMP named `beatsaber_renderer`, positioned next to
   `beatsaber_controller` if that exists.
2. Inside:
     - A custom par page `Renderer` with a `Controller` COMP pointer,
       auto-resolved to `../beatsaber_controller` if present.
     - Two Text DATs synced to the renderer callback files:
         `beatsaber_saber_sop`   → beatsaber_saber_sop.py
         `beatsaber_ui_top`      → beatsaber_ui_top.py
     - Geometry / SOP / CHOP / TOP chain:
         sabers_sop    Script SOP — two line polys for the sabers
         sabers_geo    Geometry COMP wrapping sabers_sop (In SOP routed to
                       the Script SOP inside) with a Phong MAT (emissive
                       point colour, no lighting needed)
         notes_geo     Geometry COMP — instanced cube from notes_chop
         game_cam      Camera COMP — perspective, looking down +z
         render_scene  Render TOP — cam + geometries
         ui_top        Script TOP — score/combo/event overlay
         comp_out      Composite TOP — UI over scene
         out1          Out TOP

What it does NOT do
-------------------
- Doesn't wire the In SOPs inside geos for you perfectly — TD's SOP
  routing has enough edge cases that doing it by hand after this script
  runs is easier than debugging it once. The SETUP GUIDE walks through
  the 3 or 4 remaining clicks.
- Doesn't configure the notes_geo Instance page. Too many per-build
  differences in how the Instance page exposes slots. See setup guide
  for the recipe (same pattern we used for particles).
- Doesn't create bloom / post-processing. Add a Bloom TOP between
  render_scene and comp_out if you want the neon glow.

The script is idempotent — re-running will reuse existing ops and only
create what's missing. Values the user has tuned are preserved.
"""

import os

COMP_NAME = 'beatsaber_renderer'

FILES = {
    'beatsaber_saber_sop': 'beatsaber_saber_sop.py',
    'beatsaber_ui_top':    'beatsaber_ui_top.py',
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_or_create(owner, op_type, name):
    existing = owner.op(name)
    if existing is not None:
        return existing
    return owner.create(op_type, name)


def _sync_text_dat(comp, name, relpath):
    dat = _find_or_create(comp, textDAT, name)
    dat.par.file = relpath
    for p_name, p_val in (('syncfile', True), ('loadonstart', True)):
        if hasattr(dat.par, p_name):
            try:
                setattr(dat.par, p_name, p_val)
            except Exception:
                pass
    for pulse_name in ('loadpulse', 'loadonstartpulse',
                       'forceloadpulse', 'reload'):
        p = getattr(dat.par, pulse_name, None)
        if p is not None:
            try:
                p.pulse()
                break
            except Exception:
                continue
    return dat


def _connect(src_op, dst_op, src_index=0, dst_index=0):
    for c in dst_op.inputConnectors[dst_index].connections:
        if c.owner is src_op:
            return
    src_op.outputConnectors[src_index].connect(dst_op.inputConnectors[dst_index])


def _add_comp_par(comp, page_name, par_name, label, default_path=''):
    """Add a COMP par on the given page if missing."""
    # Find or create page
    page = None
    for p in comp.customPages:
        if p.name == page_name:
            page = p
            break
    if page is None:
        page = comp.appendCustomPage(page_name)

    if hasattr(comp.par, par_name):
        return getattr(comp.par, par_name)

    pg = page.appendCOMP(par_name, label=label)
    p = pg[0]
    if default_path:
        p.val = default_path
        p.default = default_path
    return p


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build():
    toe_dir = project.folder
    missing = [p for p in FILES.values()
               if not os.path.exists(os.path.join(toe_dir, p))]
    if missing:
        debug(f"bootstrap_beatsaber_renderer: missing source files at {toe_dir}: {missing}")
        debug("Place beatsaber_saber_sop.py and beatsaber_ui_top.py "
              "next to the .toe before running.")
        return None

    parent_comp = parent()

    # 1. Base COMP ------------------------------------------------------------
    comp = _find_or_create(parent_comp, baseCOMP, COMP_NAME)
    comp.color = (0.45, 0.15, 0.55)
    comp.nodeWidth = 150
    comp.nodeHeight = 150

    # Position it next to beatsaber_controller if it exists, else next to
    # velocity_controller, else wherever.
    bsc = parent_comp.op('beatsaber_controller')
    vc  = parent_comp.op('velocity_controller')
    if bsc is not None:
        comp.nodeX = bsc.nodeX + 250
        comp.nodeY = bsc.nodeY
    elif vc is not None:
        comp.nodeX = vc.nodeX + 250
        comp.nodeY = vc.nodeY - 300

    # 2. Custom par — `Controller` pointer ------------------------------------
    controller_default = '../beatsaber_controller' if bsc is not None else ''
    _add_comp_par(comp, 'Renderer', 'Controller', 'Controller COMP',
                  controller_default)

    # 3. Synced Text DATs -----------------------------------------------------
    saber_dat = _sync_text_dat(comp, 'beatsaber_saber_sop',
                               FILES['beatsaber_saber_sop'])
    ui_dat = _sync_text_dat(comp, 'beatsaber_ui_top',
                            FILES['beatsaber_ui_top'])
    for i, d in enumerate([saber_dat, ui_dat]):
        d.nodeX = -600
        d.nodeY = 300 - i * 150

    # 4. sabers_sop Script SOP -----------------------------------------------
    sabers_sop = _find_or_create(comp, scriptSOP, 'sabers_sop')
    sabers_sop.par.callbacks = saber_dat.name
    sabers_sop.nodeX = -200
    sabers_sop.nodeY = 0

    # 5. sabers_geo Geometry COMP --------------------------------------------
    # The Geometry COMP will contain:
    #   in_sabers (In SOP, receives sabers_sop output from OUTSIDE the COMP)
    #   color_left  (Color SOP — selects primitive 0, applies red)
    #   color_right (Color SOP — selects primitive 1, applies blue)
    #   out_sabers (Out SOP, Render flag on)
    sabers_geo = _find_or_create(comp, geometryCOMP, 'sabers_geo')
    sabers_geo.nodeX = 0
    sabers_geo.nodeY = 0
    sabers_geo.par.render = True

    # Build the inner SOP chain. The In SOP is the external entry point;
    # we don't connect sabers_sop → In SOP from here (that wiring lives
    # outside the Geometry COMP in TD's node graph; a separate setup
    # step). We just lay out the SOPs and the downstream color chain.
    in_sabers = _find_or_create(sabers_geo, inSOP, 'in_sabers')
    in_sabers.nodeX = -400
    in_sabers.nodeY = 0

    # Primitive SOPs apply per-primitive color via the "Add" color mode.
    # (TD doesn't have a "Color SOP" — Primitive SOP is the standard op
    # for this.) Each selects one primitive by Group pattern and sets
    # its Cd attribute; unaffected primitives pass through untouched.
    def _color_prim(op, prim_idx, rgb):
        """Try several TD par naming conventions across builds."""
        # Group selection: most builds use 'group' + 'grouptype'. Some
        # use 'primpattern' or similar. Try them.
        for gn in ('group',):
            if hasattr(op.par, gn):
                try:
                    setattr(op.par, gn, str(prim_idx))
                    break
                except Exception:
                    pass
        # Turn on the Color toggle (so the SOP actually applies color).
        for toggle in ('docolor', 'applycolor'):
            if hasattr(op.par, toggle):
                try:
                    setattr(op.par, toggle, 1)
                    break
                except Exception:
                    pass
        # Color mode — "Add" = apply this color, "Keep" = pass input through.
        for mode_par in ('colormethod', 'color', 'colormode'):
            if hasattr(op.par, mode_par):
                try:
                    p = getattr(op.par, mode_par)
                    # If it's a menu, set to "add"; if float, skip.
                    if hasattr(p, 'menuNames'):
                        p.val = 'add'
                        break
                except Exception:
                    pass
        # Diffuse color components — par names vary across TD versions.
        # Try all known conventions.
        for r_name, g_name, b_name in [
            ('diffr', 'diffg', 'diffb'),
            ('colorr', 'colorg', 'colorb'),
            ('color1r', 'color1g', 'color1b'),
            ('cdr', 'cdg', 'cdb'),
        ]:
            if all(hasattr(op.par, n) for n in (r_name, g_name, b_name)):
                try:
                    setattr(op.par, r_name, rgb[0])
                    setattr(op.par, g_name, rgb[1])
                    setattr(op.par, b_name, rgb[2])
                    break
                except Exception:
                    pass

    # FOUR Primitive SOPs in series — sabers_sop now emits four
    # primitives (left hilt = 0, left blade = 1, right hilt = 2,
    # right blade = 3). Hilts are dim metallic-grey, blades are
    # bright red/blue (the BeatSaber convention).
    HILT_RGB        = (0.45, 0.45, 0.50)   # neutral grey for both sides
    LEFT_BLADE_RGB  = (1.00, 0.25, 0.30)   # red
    RIGHT_BLADE_RGB = (0.25, 0.55, 1.00)   # blue

    color_left_hilt = _find_or_create(sabers_geo, primitiveSOP,
                                      'color_left_hilt')
    color_left_hilt.nodeX, color_left_hilt.nodeY = -250, 0
    _color_prim(color_left_hilt, prim_idx=0, rgb=HILT_RGB)

    color_left_blade = _find_or_create(sabers_geo, primitiveSOP,
                                       'color_left_blade')
    color_left_blade.nodeX, color_left_blade.nodeY = -100, 0
    _color_prim(color_left_blade, prim_idx=1, rgb=LEFT_BLADE_RGB)

    color_right_hilt = _find_or_create(sabers_geo, primitiveSOP,
                                       'color_right_hilt')
    color_right_hilt.nodeX, color_right_hilt.nodeY = 50, 0
    _color_prim(color_right_hilt, prim_idx=2, rgb=HILT_RGB)

    color_right_blade = _find_or_create(sabers_geo, primitiveSOP,
                                        'color_right_blade')
    color_right_blade.nodeX, color_right_blade.nodeY = 200, 0
    _color_prim(color_right_blade, prim_idx=3, rgb=RIGHT_BLADE_RGB)

    out_sabers = _find_or_create(sabers_geo, outSOP, 'out_sabers')
    out_sabers.nodeX = 350
    out_sabers.nodeY = 0
    try:
        out_sabers.par.render = True
        out_sabers.par.display = True
    except Exception:
        pass

    _connect(in_sabers,        color_left_hilt)
    _connect(color_left_hilt,  color_left_blade)
    _connect(color_left_blade, color_right_hilt)
    _connect(color_right_hilt, color_right_blade)
    _connect(color_right_blade, out_sabers)

    # Clean up any obsolete pre-rewrite primitive SOPs left over from
    # an earlier renderer build (when there were only two prims). They
    # would otherwise sit detached and confuse anyone reading the graph.
    for old_name in ('color_left', 'color_right'):
        old = sabers_geo.op(old_name)
        if old is not None:
            try:
                old.destroy()
            except Exception:
                pass

    # 6. notes_geo Geometry COMP (instanced) ---------------------------------
    notes_geo = _find_or_create(comp, geometryCOMP, 'notes_geo')
    notes_geo.nodeX = 0
    notes_geo.nodeY = -200
    notes_geo.par.render = True
    # Per-instance configuration is build-specific; see setup guide.

    # 7. Camera — dedicated game camera, TD-native orientation ---------------
    # Coordinate convention (see beatsaber/saber_logic.py):
    #   z = 0   is the hit plane (sabers live here)
    #   z < 0   is the approach tunnel (notes spawn at z = -10, travel toward 0)
    #   z > 0   is behind the player (where the camera sits)
    #
    # TD cameras look down their local -Z axis by default. Placing the
    # camera at +Z with zero rotation makes it look naturally into the
    # tunnel, no lookAt / rotate-180 hacks needed.
    cam = _find_or_create(comp, cameraCOMP, 'game_cam')
    cam.nodeX = 200
    cam.nodeY = -400
    cam.par.projection = 'perspective'
    cam.par.tx = 0.5
    cam.par.ty = 0.5
    cam.par.tz = 3.0      # +Z, behind the hit plane, looking -Z into tunnel
    cam.par.rx = 0
    cam.par.ry = 0
    cam.par.rz = 0
    cam.par.fov = 50
    # Near/far — include the full note travel range (z = -10 to 0) with headroom.
    try:
        cam.par.near = 0.1
        cam.par.far = 20.0
    except Exception:
        pass

    # 8. render_scene Render TOP --------------------------------------------
    render_scene = _find_or_create(comp, renderTOP, 'render_scene')
    render_scene.nodeX = 400
    render_scene.nodeY = 0
    render_scene.par.resolutionw = 1920
    render_scene.par.resolutionh = 1080
    render_scene.par.camera = cam.path
    render_scene.par.geometry = f'{sabers_geo.path} {notes_geo.path}'

    # 9. UI Script TOP --------------------------------------------------------
    ui_top = _find_or_create(comp, scriptTOP, 'ui_top')
    ui_top.par.callbacks = ui_dat.name
    ui_top.par.resolutionw = 1920
    ui_top.par.resolutionh = 1080
    ui_top.nodeX = 400
    ui_top.nodeY = -200

    # 10. Composite UI over render -------------------------------------------
    comp_out = _find_or_create(comp, compositeTOP, 'comp_out')
    comp_out.par.operand = 'over'
    comp_out.nodeX = 600
    comp_out.nodeY = 0
    _connect(render_scene, comp_out, dst_index=0)
    _connect(ui_top, comp_out, dst_index=1)

    # 11. Out TOP --------------------------------------------------------------
    out1 = _find_or_create(comp, outTOP, 'out1')
    out1.nodeX = 800
    out1.nodeY = 0
    _connect(comp_out, out1)

    debug(f"bootstrap_beatsaber_renderer: {COMP_NAME} built.")
    # Report camera config so the user can immediately verify correctness
    # without having to hunt through the Camera COMP's parameter page.
    try:
        debug(f"  game_cam: tx={cam.par.tx.eval()}, ty={cam.par.ty.eval()}, "
              f"tz={cam.par.tz.eval()}  (expect tz = 3.0)")
        debug(f"           rx={cam.par.rx.eval()}, ry={cam.par.ry.eval()}, "
              f"rz={cam.par.rz.eval()}  (expect all 0)")
        debug(f"           projection={cam.par.projection.eval()}, "
              f"fov={cam.par.fov.eval()}  (expect perspective, 50)")
        debug("  If tz is negative or rotation is nonzero, you have an old "
              "config — delete game_cam and re-run the bootstrap.")
    except Exception as e:
        debug(f"  (couldn't report camera config: {e})")
    debug("Remaining manual steps (see beatsaber_renderer_setup.md):")
    debug("  1. At the beatsaber_renderer level, connect sabers_sop → "
          "sabers_geo's input (the outer cable connects to in_sabers "
          "inside automatically via the Geometry COMP's input routing).")
    debug("  2. Add a Phong MAT inside sabers_geo with Emit Color Source "
          "= Point Color, assign as Material on out_sabers (or on "
          "sabers_geo itself).")
    debug("  3. Enter notes_geo: add a Box SOP (sized 1,1,1), connect to "
          "an In SOP, set up the Instance page (see guide).")
    debug("  4. Wire beatsaber_controller/notes_chop → notes_geo's "
          "Instance OP.")
    debug("  5. Connect beatsaber_renderer/out1 wherever you want the "
          "game visual to go.")
    return comp


build()
