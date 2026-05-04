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
       auto-resolved to `../beatsaber_controller` if present, plus
       `Worldscale`, `Bladetrail`, `Bladetraillen`.
     - Synced Text DATs for the renderer callback files:
         `beatsaber_saber_sop` → beatsaber_saber_sop.py
         `beatsaber_trail_sop` → beatsaber_trail_sop.py
         `beatsaber_hud`       → beatsaber_hud.py
     - Geometry / SOP / CHOP / TOP chain:
         sabers_sop       Script SOP — four line polys for live blades
         sabers_geo       Geometry COMP — wraps sabers_sop, four
                          Primitive SOPs apply per-prim color (left
                          hilt grey, left blade red, right hilt grey,
                          right blade blue)
         trail_left_geo   Geometry COMP — left blade motion trail
         trail_right_geo  Geometry COMP — right blade motion trail
         mat_trail_left   Constant MAT — identity multiplier consuming
                          per-vertex Cd from the left trail SOP
         mat_trail_right  Constant MAT — same for right
         notes_geo        Geometry COMP — instanced cube from notes_chop
         game_cam         Camera COMP — perspective, +Z, looks -Z
         render_scene     Render TOP — cam + four geometries
         text_score / text_combo / text_accuracy / text_song_time /
         text_eventlog    Text TOPs — HUD elements with `text` bound
                          to expressions in beatsaber_hud
         flash_select / flash_lag                Trigger envelope chain
         flash_hit / flash_bad / flash_miss      Constant TOPs with
                          alpha bound to the lag envelope
         comp_out         Composite TOP — scene + flashes + text
         out1             Out TOP

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
    'beatsaber_trail_sop': 'beatsaber_trail_sop.py',
    'beatsaber_hud':       'beatsaber_hud.py',
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_or_create(owner, op_type, name):
    existing = owner.op(name)
    if existing is not None:
        return existing
    return owner.create(op_type, name)


def _set_render_display_flags(op_, render=True, display=True):
    """Set a SOP's Render and Display flags (red bracket + blue dot).
    These are OP-level FLAGS, not parameters — `op.par.render = True`
    silently does nothing because there is no such par. The correct
    API is direct attribute access on the operator itself."""
    if render is not None:
        try:
            op_.render = bool(render)
        except Exception:
            pass
    if display is not None:
        try:
            op_.display = bool(display)
        except Exception:
            pass
        # Some builds use `viewer` as an alias for the display flag.
        try:
            op_.viewer = bool(display)
        except Exception:
            pass


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
        debug("Place beatsaber_saber_sop.py, beatsaber_trail_sop.py, "
              "and beatsaber_hud.py next to the .toe before running.")
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

    # 2. Custom pars — `Controller` pointer + `Worldscale` ------------------
    controller_default = '../beatsaber_controller' if bsc is not None else ''
    _add_comp_par(comp, 'Renderer', 'Controller', 'Controller COMP',
                  controller_default)

    # `Worldscale` — uniform scale on sabers_geo + notes_geo so the
    # MediaPipe-UV (0..1) world fills more of the rendered viewport.
    # The default camera (z=+3, FOV=50°) sees a ~5×2.8 world region
    # at the hit plane; the sabres' 1×1 reach would only cover ~20%
    # of screen width without this scaling. 2.5× brings reach to
    # ~50% of width and ~90% of height, with the user able to tune
    # higher for more reach (3.0 ≈ full width).
    def _add_float_par(page_name, par_name, label, default,
                       lo=0.5, hi=4.0):
        page = None
        for p in comp.customPages:
            if p.name == page_name:
                page = p; break
        if page is None:
            page = comp.appendCustomPage(page_name)
        if hasattr(comp.par, par_name):
            return getattr(comp.par, par_name)
        pg = page.appendFloat(par_name, label=label)
        p = pg[0]
        p.default = default; p.val = default
        p.normMin = lo; p.normMax = hi
        return p

    _add_float_par('Renderer', 'Worldscale',
                   'World Scale (sabers + notes)', 2.5)

    # Blade trail toggle + length. Reads by `beatsaber_saber_sop.py`
    # each cook to decide how many historical (hilt_top, tip) pairs
    # to draw as additional primitives behind the live blade. Gives
    # a constant motion-feedback ribbon even when slashes don't
    # register as scoring hits.
    def _add_int_par(page_name, par_name, label, default,
                     lo=0, hi=120):
        page = None
        for p in comp.customPages:
            if p.name == page_name:
                page = p; break
        if page is None:
            page = comp.appendCustomPage(page_name)
        if hasattr(comp.par, par_name):
            return getattr(comp.par, par_name)
        pg = page.appendInt(par_name, label=label)
        p = pg[0]
        p.default = default; p.val = default
        p.normMin = lo; p.normMax = hi
        return p

    def _add_toggle_par(page_name, par_name, label, default):
        page = None
        for p in comp.customPages:
            if p.name == page_name:
                page = p; break
        if page is None:
            page = comp.appendCustomPage(page_name)
        if hasattr(comp.par, par_name):
            return getattr(comp.par, par_name)
        pg = page.appendToggle(par_name, label=label)
        p = pg[0]
        p.default = 1 if default else 0; p.val = p.default
        return p

    _add_toggle_par('Renderer', 'Bladetrail',
                    'Blade Motion Trail', True)
    _add_int_par('Renderer', 'Bladetraillen',
                 'Blade Trail Length (frames)', 16)

    # 3. Synced Text DATs -----------------------------------------------------
    saber_dat = _sync_text_dat(comp, 'beatsaber_saber_sop',
                               FILES['beatsaber_saber_sop'])
    hud_dat = _sync_text_dat(comp, 'beatsaber_hud',
                             FILES['beatsaber_hud'])
    # NOTE: beatsaber_trail_sop's Text DAT lives INSIDE each trail
    # Geo COMP (sibling of the Script SOP) — not at this level.
    # That keeps the standard "callback DAT is a sibling" pattern
    # for anyone reading the Geo COMP's network in isolation.
    for i, d in enumerate([saber_dat, hud_dat]):
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

    # Wire Sx/Sy to the renderer COMP's Worldscale par via expression so
    # tuning Worldscale rescales sabres and notes together. Pivot at
    # (0.5, 0.5, 0) so the centre of the (0..1) MediaPipe-UV cube stays
    # screen-centered when scaled.
    def _bind_worldscale(geo):
        for axis in ('sx', 'sy'):
            try:
                p = getattr(geo.par, axis)
                p.expr = "parent().par.Worldscale"
                p.mode = ParMode.EXPRESSION
            except Exception:
                # Fallback: set the value directly with the current
                # Worldscale so we at least scale once. The user can
                # bind to expression manually later.
                try:
                    setattr(geo.par, axis, 2.5)
                except Exception:
                    pass
        for axis, val in (('sz', 1.0), ('px', 0.5), ('py', 0.5), ('pz', 0.0)):
            try:
                setattr(geo.par, axis, val)
            except Exception:
                pass

    _bind_worldscale(sabers_geo)

    # Build the inner SOP chain. The In SOP is the external entry point;
    # we don't connect sabers_sop → In SOP from here (that wiring lives
    # outside the Geometry COMP in TD's node graph; a separate setup
    # step). We just lay out the SOPs and the downstream color chain.
    in_sabers = _find_or_create(sabers_geo, inSOP, 'in_sabers')
    in_sabers.nodeX = -400
    in_sabers.nodeY = 0

    # Primitive SOPs apply per-primitive color via the "Add" color mode.
    # (TD doesn't have a "Color SOP" — Primitive SOP is the standard op
    # for this.) Each Primitive SOP selects ONE primitive by index
    # (0/1/2/3 — the four live hilt+blade primitives) and applies its
    # diffuse color. Trail primitives (indices 4+) carry their own Cd
    # set per-point in the Script SOP, so these Primitive SOPs leave
    # them alone and the trail still renders in the correct saber color.
    def _color_prim(op, prim_idx, rgb):
        """Try several TD par naming conventions across builds."""
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
    # Out SOP is the COMP's render-from. Render+Display flags are
    # OP-level attributes, not pars — see _set_render_display_flags.
    _set_render_display_flags(out_sabers)

    _connect(in_sabers,        color_left_hilt)
    _connect(color_left_hilt,  color_left_blade)
    _connect(color_left_blade, color_right_hilt)
    _connect(color_right_hilt, color_right_blade)
    _connect(color_right_blade, out_sabers)

    # Wire sabers_sop (outside the Geo COMP) → sabers_geo's input,
    # which routes to in_sabers. Without this, the Geo COMP has no
    # source geometry and TD falls back to its default torus, hiding
    # everything else in the scene.
    _connect(sabers_sop, sabers_geo)

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

    # 6. Trail Geometry COMPs + Constant MATs --------------------------------
    # The motion trail for each saber lives in its OWN Geometry COMP
    # with its own dedicated Constant MAT carrying the side color.
    # This split keeps the live blade's per-prim-index coloring
    # cleanly separated from the trail coloring (which is a MATERIAL
    # choice, not a vertex attribute) and avoids the spotty per-vertex
    # color attribute API in TD's Script SOP across builds.
    #
    # The trail Script SOP (beatsaber_trail_sop) reads the blade
    # history that beatsaber_saber_sop maintains in renderer-COMP
    # storage and emits one line segment per historical frame.
    def _add_str_par(page_name, par_name, label, default):
        page = None
        for p in comp.customPages:
            if p.name == page_name:
                page = p; break
        if page is None:
            page = comp.appendCustomPage(page_name)
        return page  # caller adds the par

    def _configure_trail_mat(mat):
        """Configure a Constant MAT to consume per-vertex Cd
        (color + alpha) written by the trail Script SOP:

          1. Base color = (1, 1, 1, 1) so the MAT is an identity
             multiplier and per-vertex Cd renders verbatim.
          2. Use-point-color ON so the MAT actually reads Cd.
          3. Transparency ON so the per-vertex alpha < 1 produces
             translucent rendering.

        FOLLOW-UP: the "Use Point Color" toggle's exact par name in
        TD's Constant MAT hasn't been confirmed for the target build.
        For now we probe a small set of common names. Once the actual
        par name is known, replace the probe with a single direct
        `setattr(mat.par, '<actual_par>', 1)` and remove the loop.
        """
        # 1. Identity base color (1, 1, 1, 1).
        for r_name, g_name, b_name, a_name in (
            ('colorr', 'colorg', 'colorb', 'alpha'),
            ('colr',   'colg',   'colb',   'alphap'),
            ('cdr',    'cdg',    'cdb',    'cda'),
        ):
            if all(hasattr(mat.par, n) for n in (r_name, g_name, b_name)):
                try:
                    setattr(mat.par, r_name, 1.0)
                    setattr(mat.par, g_name, 1.0)
                    setattr(mat.par, b_name, 1.0)
                    if hasattr(mat.par, a_name):
                        setattr(mat.par, a_name, 1.0)
                    break
                except Exception:
                    continue

        # 2. Use-point-color toggle / Color Source.
        #
        # In TD's Constant MAT, the typical par for "consume per-vertex
        # Cd as color" is `usepointcolor` (toggle). On older builds
        # it's `useVertexColor` or set via `colorSource` menu to
        # 'pointColor' / 'vertexColor'. Try them all.
        enabled = False
        # Try toggle pars first.
        for toggle_name in ('usepointcolor', 'usevertexcolor',
                            'pointcolor', 'vertexcolor'):
            if hasattr(mat.par, toggle_name):
                try:
                    setattr(mat.par, toggle_name, 1)
                    enabled = True
                    break
                except Exception:
                    continue
        # Try menu pars — typical name `colorsource` with a
        # 'pointcolor' / 'vertexcolor' / 'point' / 'vertex' option.
        if not enabled:
            for menu_name in ('colorsource', 'colorinput', 'colormode'):
                if hasattr(mat.par, menu_name):
                    try:
                        p = getattr(mat.par, menu_name)
                        if hasattr(p, 'menuNames'):
                            for v in ('pointcolor', 'point',
                                      'vertexcolor', 'vertex'):
                                if v in p.menuNames:
                                    p.val = v
                                    enabled = True
                                    break
                            if enabled:
                                break
                    except Exception:
                        continue
        if not enabled:
            try:
                debug(f"bootstrap_beatsaber_renderer: couldn't find "
                      f"a 'use point color' par on Constant MAT "
                      f"`{mat.name}`. Manually set its 'Use Point "
                      f"Color' (or equivalent) toggle to ON, base "
                      f"color to (1,1,1,1), and transparency on. "
                      f"Available pars: "
                      f"{[p.name for p in mat.pars()][:30]}")
            except Exception:
                pass

        # 3. Transparency / alpha blending — required so the
        # per-vertex alpha < 1 actually produces translucent output.
        for tp_name in ('transparency', 'usealpha', 'blending',
                        'alphablending'):
            if hasattr(mat.par, tp_name):
                try:
                    p = getattr(mat.par, tp_name)
                    if hasattr(p, 'menuNames'):
                        for v in ('on', 'over', 'overadd', 'add', 'blend'):
                            if v in p.menuNames:
                                p.val = v
                                break
                    else:
                        setattr(mat.par, tp_name, 1)
                    break
                except Exception:
                    continue

    def _trail_pair(side, x_offset):
        """Build (Constant MAT, Geo COMP, inner Script SOP) for one side.

        Both sides use IDENTICAL MAT configurations — the per-side
        coloring lives entirely in the geometry (per-vertex Cd written
        by beatsaber_trail_sop based on the COMP's `Side` par). Using
        two MATs (one per Geo COMP) preserves the option to add per-side
        post-FX later (extra glow on one side, distinct blend mode, etc.)
        without coupling them.
        """
        mat_name = f'mat_trail_{side}'
        mat = _find_or_create(comp, constantMAT, mat_name)
        mat.nodeX, mat.nodeY = x_offset, -100
        _configure_trail_mat(mat)

        # Geometry COMP wrapping the trail Script SOP.
        geo_name = f'trail_{side}_geo'
        geo = _find_or_create(comp, geometryCOMP, geo_name)
        geo.nodeX, geo.nodeY = x_offset, 100
        try:
            geo.par.render = True
        except Exception:
            pass
        # Custom `Side` par on the Geo COMP — read by the inner
        # Script SOP to know which side's history to draw.
        if not hasattr(geo.par, 'Side'):
            page = None
            for p in geo.customPages:
                if p.name == 'Trail':
                    page = p; break
            if page is None:
                page = geo.appendCustomPage('Trail')
            pg = page.appendStr('Side', label='Side (left/right)')
            pg[0].default = side; pg[0].val = side
        else:
            try:
                geo.par.Side = side
            except Exception:
                pass
        # Worldscale binding so trails enlarge with the rest of the
        # scene.
        _bind_worldscale(geo)
        # Assign the Constant MAT.
        for mat_attr in ('material', 'mat'):
            if hasattr(geo.par, mat_attr):
                try:
                    setattr(geo.par, mat_attr, mat.path)
                    break
                except Exception:
                    continue

        # Local synced Text DAT — sibling of the Script SOP inside
        # this Geo COMP. Same pattern as every other Script callback
        # in the project: the DAT lives next to the SOP that uses
        # it, so the COMP is self-contained when read in isolation.
        # Both trail Geo COMPs end up with their own copy synced to
        # the same source file (beatsaber_trail_sop.py).
        local_dat = _sync_text_dat(geo, 'beatsaber_trail_sop',
                                   FILES['beatsaber_trail_sop'])
        local_dat.nodeX, local_dat.nodeY = -200, 0

        # Inner Script SOP that emits the trail line geometry.
        sop_name = f'trail_{side}_sop'
        sop = _find_or_create(geo, scriptSOP, sop_name)
        sop.par.callbacks = local_dat.name   # local sibling reference
        sop.nodeX, sop.nodeY = 0, 0
        # Set Render+Display FLAGS (not pars) so the Geo COMP picks
        # this SOP as its render-from. Without this the COMP shows
        # the default torus.
        _set_render_display_flags(sop)
        return mat, geo, sop

    # Per-side RGB lives in the geometry now (set by beatsaber_trail_sop
    # based on the COMP's `Side` par). The MATs here are identity
    # multipliers that pass per-vertex Cd through.
    mat_trail_left,  trail_left_geo,  _ = _trail_pair('left',  -200)
    mat_trail_right, trail_right_geo, _ = _trail_pair('right',  200)

    # 7. notes_geo Geometry COMP (instanced) ---------------------------------
    notes_geo = _find_or_create(comp, geometryCOMP, 'notes_geo')
    notes_geo.nodeX = 0
    notes_geo.nodeY = -200
    notes_geo.par.render = True
    _bind_worldscale(notes_geo)

    # Internal note-source SOP — a unit Box that the Geo COMP
    # instances per-note via its Instance page. Without an internal
    # SOP, the Geo COMP renders the DEFAULT TORUS — that's the giant
    # grey shape that was occluding the playfield. Adding this Box
    # SOP fixes that, and per-instance translate/scale/color (set
    # on the Geo COMP's Instance page) puts notes at their correct
    # positions.
    notes_box = _find_or_create(notes_geo, boxSOP, 'notes_box')
    notes_box.nodeX, notes_box.nodeY = -200, 0
    for sz_par, sz_val in (('sizex', 1.0), ('sizey', 1.0), ('sizez', 1.0),
                           ('size',  1.0)):
        if hasattr(notes_box.par, sz_par):
            try:
                setattr(notes_box.par, sz_par, sz_val)
            except Exception:
                pass
    _set_render_display_flags(notes_box)
    # Per-instance configuration (Instance OP par, Translate XYZ
    # attribs, Color attribs) is build-specific; see the renderer
    # setup guide for the recipe.

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

    # 9. render_scene Render TOP --------------------------------------------
    #
    # Resolution is bound by EXPRESSION to the project root's
    # resolution pars (`/project1`'s built-in `resolution1`/
    # `resolution2`, or whatever your TD build calls them). The HUD
    # ops below in turn bind THEIR resolution to render_scene, so the
    # whole chain follows the project's output resolution
    # automatically — no hardcoded 1920×1080 anywhere.
    render_scene = _find_or_create(comp, renderTOP, 'render_scene')
    render_scene.nodeX = 400
    render_scene.nodeY = 0
    render_scene.par.camera = cam.path
    # Render four geos: live sabres, both per-side trails, and notes.
    render_scene.par.geometry = (
        f'{sabers_geo.path} '
        f'{trail_left_geo.path} {trail_right_geo.path} '
        f'{notes_geo.path}'
    )
    # Bind render_scene's resolution to the project root's resolution
    # via expression. The HUD ops bind in turn to render_scene, so the
    # entire chain follows the project's output resolution
    # automatically. Falls back to a sensible 1920×1080 if the root
    # par doesn't exist on this build.
    def _bind_render_scene_res():
        for w_par, h_par, default_w, default_h in (
            ('resolution1', 'resolution2', 1920, 1080),  # std project pars
            ('resw',        'resh',        1920, 1080),  # older convention
        ):
            root_w = getattr(op('/project1').par, w_par, None) if op('/project1') else None
            if root_w is not None:
                ok_w = False
                ok_h = False
                pw = render_scene.par.resolutionw
                ph = render_scene.par.resolutionh
                try:
                    pw.expr = f"op('/project1').par.{w_par}"
                    pw.mode = ParMode.EXPRESSION
                    ok_w = True
                except Exception:
                    pass
                try:
                    ph.expr = f"op('/project1').par.{h_par}"
                    ph.mode = ParMode.EXPRESSION
                    ok_h = True
                except Exception:
                    pass
                if ok_w and ok_h:
                    return
        # Last resort: hardcode and warn.
        try:
            render_scene.par.resolutionw = 1920
            render_scene.par.resolutionh = 1080
            debug("bootstrap_beatsaber_renderer: couldn't bind "
                  "render_scene resolution to /project1 root; "
                  "using hardcoded 1920×1080. Set the expression "
                  "manually if you want it to follow the project.")
        except Exception:
            pass
    _bind_render_scene_res()

    # 10. HUD overlay — TD-native Text TOPs + flash chain --------------------
    #
    # Each HUD element is a separate Text TOP whose `text` parameter is
    # a Python expression calling a helper in beatsaber_hud.py. No PIL
    # — TD's built-in text rendering does the work. Hit/bad/miss flash
    # tints are Constant TOPs whose alpha is bound to a Lag CHOP
    # envelope of the per-frame trigger channels (full-strength on the
    # event cook, fades to 0 over Release seconds).
    #
    # All HUD-overlay TOPs (Text + Constant) bind their resolution to
    # `render_scene`'s resolution via expression, which itself follows
    # the project root. So the final composite always lands at the
    # project's output resolution and adjusting that propagates to
    # every overlay automatically.
    PROJECT_RES_W_EXPR = "op('/project1').par.resolution1"
    PROJECT_RES_H_EXPR = "op('/project1').par.resolution2"
    SCENE_RES_W_EXPR   = "op('render_scene').par.resolutionw"
    SCENE_RES_H_EXPR   = "op('render_scene').par.resolutionh"

    def _set_par(op_, name, value):
        if hasattr(op_, 'par') and hasattr(op_.par, name):
            try:
                setattr(op_.par, name, value)
                return True
            except Exception:
                pass
        return False

    def _set_par_expr(op_, name, expr):
        """Bind a parameter to a Python expression (continuously evaluated)."""
        p = getattr(op_.par, name, None)
        if p is None:
            return False
        try:
            p.expr = expr
            p.mode = ParMode.EXPRESSION
            return True
        except Exception:
            try:
                # Fallback: drop the expression in unmodified — some
                # builds expose `.expr` differently.
                p.expr = expr
                return True
            except Exception:
                return False

    def _bind_to_scene_resolution(top):
        """Bind a TOP's resolutionw/resolutionh to render_scene's
        resolution via expression. The Text/Constant TOPs that
        compose into comp_out all need to match render_scene so the
        composite alignment is consistent."""
        # First make sure the resolution mode is set to "Custom Res"
        # (or whatever the build calls it) — otherwise the resolution
        # pars are ignored. The exact menu values vary across builds.
        for mode_par in ('resolutionmode', 'resmode'):
            p = getattr(top.par, mode_par, None)
            if p is None:
                continue
            try:
                if hasattr(p, 'menuNames'):
                    for v in ('custom', 'customres', 'custres'):
                        if v in p.menuNames:
                            p.val = v
                            break
                else:
                    p.val = 0
                break
            except Exception:
                continue
        _set_par_expr(top, 'resolutionw', SCENE_RES_W_EXPR)
        _set_par_expr(top, 'resolutionh', SCENE_RES_H_EXPR)

    def _make_text_top(name, text_expr, *, font_size=72, fill=(1.0, 1.0, 1.0, 0.9),
                       align_x='right', align_y='top',
                       offset_x=0, offset_y=0,
                       node_x=600, node_y=0):
        """Create a HUD Text TOP whose resolution follows render_scene
        with `text` bound to the given expression. Background fully
        transparent so the rendered scene shows through."""
        t = _find_or_create(comp, textTOP, name)
        t.nodeX, t.nodeY = node_x, node_y
        _bind_to_scene_resolution(t)
        # Bind text via expression — recomputed each cook.
        _set_par_expr(t, 'text', text_expr)
        # Font size.
        for sz_par in ('fontsizex', 'fontsize', 'size'):
            if _set_par(t, sz_par, font_size):
                break
        # Foreground color.
        for r, g, b, a in [
            ('fontcolorr', 'fontcolorg', 'fontcolorb', 'fontcolora'),
            ('colorr',     'colorg',     'colorb',     'colora'),
        ]:
            if (_set_par(t, r, fill[0]) and _set_par(t, g, fill[1])
                    and _set_par(t, b, fill[2])):
                _set_par(t, a, fill[3])
                break
        # Background fully transparent.
        for bg_a in ('bgcolora', 'bgalpha'):
            if _set_par(t, bg_a, 0.0):
                break
        # Alignment + offsets.
        for ax_par in ('alignx', 'alignmentx', 'horizontalalign'):
            if hasattr(t.par, ax_par):
                try:
                    p = getattr(t.par, ax_par)
                    if hasattr(p, 'menuNames') and align_x in p.menuNames:
                        p.val = align_x
                    else:
                        p.val = align_x
                    break
                except Exception:
                    pass
        for ay_par in ('aligny', 'alignmenty', 'verticalalign'):
            if hasattr(t.par, ay_par):
                try:
                    p = getattr(t.par, ay_par)
                    if hasattr(p, 'menuNames') and align_y in p.menuNames:
                        p.val = align_y
                    else:
                        p.val = align_y
                    break
                except Exception:
                    pass
        for ox_par in ('positionx', 'alignx_offset', 'offsetx', 'tx'):
            if _set_par(t, ox_par, offset_x):
                break
        for oy_par in ('positiony', 'aligny_offset', 'offsety', 'ty'):
            if _set_par(t, oy_par, offset_y):
                break
        return t

    # Position offsets: TD's TOP coordinate convention is bottom-up
    # (origin at bottom-left, +y goes UP). With aligny='top', a
    # POSITIVE positiony pushes the anchor ABOVE the top edge —
    # off-screen. Use NEGATIVE y values to push DOWN from the top.
    # Same goes for x with right-aligned text: NEGATIVE x to move
    # in from the right edge, POSITIVE x to move right (off-screen).
    text_score = _make_text_top(
        'text_score',
        text_expr="mod('beatsaber_hud').score_text()",
        font_size=72, fill=(1.0, 1.0, 1.0, 0.9),
        align_x='right', align_y='top',
        offset_x=-50, offset_y=-30,
        node_x=400, node_y=-200,
    )
    text_combo = _make_text_top(
        'text_combo',
        text_expr="mod('beatsaber_hud').combo_text()",
        font_size=48, fill=(1.0, 0.86, 0.39, 0.9),
        align_x='right', align_y='top',
        offset_x=-50, offset_y=-120,
        node_x=400, node_y=-275,
    )
    text_accuracy = _make_text_top(
        'text_accuracy',
        text_expr="mod('beatsaber_hud').accuracy_text()",
        font_size=32, fill=(0.71, 0.86, 1.0, 0.78),
        align_x='right', align_y='top',
        offset_x=-50, offset_y=-180,
        node_x=400, node_y=-350,
    )
    text_song_time = _make_text_top(
        'text_song_time',
        text_expr="mod('beatsaber_hud').song_time_text()",
        font_size=40, fill=(0.78, 0.78, 0.78, 0.86),
        align_x='left', align_y='top',
        offset_x=50, offset_y=-30,
        node_x=400, node_y=-425,
    )
    text_eventlog = _make_text_top(
        'text_eventlog',
        text_expr="mod('beatsaber_hud').event_log_text()",
        font_size=20, fill=(0.85, 0.85, 0.85, 0.9),
        align_x='left', align_y='top',
        offset_x=50, offset_y=-240,
        node_x=400, node_y=-500,
    )

    # Event flash chain — Constant TOPs whose alpha is bound to a Lag
    # CHOP envelope of the per-frame trigger channels. Lag is fed by
    # an In CHOP that selects the relevant channels from game_tick.
    flash_in = _find_or_create(comp, inCHOP, 'flash_in')
    flash_in.nodeX, flash_in.nodeY = 100, -550
    # Source = the controller's game_tick CHOP. We rely on the user
    # connecting the controller's out1 into here (the renderer COMP
    # exposes flash_in as an input). Or, alternatively, we use an
    # absolute path via a Select CHOP inside.
    flash_select = _find_or_create(comp, selectCHOP, 'flash_select')
    flash_select.nodeX, flash_select.nodeY = 250, -550
    # Pull the relevant channels by name from the game_tick CHOP.
    bsc_path = bsc.path if bsc is not None else '/project1/beatsaber_controller'
    _set_par(flash_select, 'chop', f'{bsc_path}/game_tick')
    _set_par(flash_select, 'channames',
             'hit_this_frame bad_cut_this_frame miss_this_frame')

    flash_lag = _find_or_create(comp, lagCHOP, 'flash_lag')
    flash_lag.nodeX, flash_lag.nodeY = 400, -550
    _connect(flash_select, flash_lag)
    # Fast attack (snap to 1 on event), 0.3s release (linear-ish fade).
    for atk_par in ('lag1', 'attack', 'attacktime'):
        if _set_par(flash_lag, atk_par, 0.0):
            break
    for rel_par in ('lag2', 'release', 'releasetime'):
        if _set_par(flash_lag, rel_par, 0.30):
            break

    def _make_flash(name, channel, rgb, max_alpha, node_y):
        c = _find_or_create(comp, constantTOP, name)
        c.nodeX, c.nodeY = 600, node_y
        _bind_to_scene_resolution(c)
        for r_n, g_n, b_n, a_n in [
            ('colorr', 'colorg', 'colorb', 'alpha'),
            ('color1r', 'color1g', 'color1b', 'color1a'),
        ]:
            if (_set_par(c, r_n, rgb[0]) and _set_par(c, g_n, rgb[1])
                    and _set_par(c, b_n, rgb[2])):
                # Alpha bound to Lag CHOP envelope, scaled by max_alpha.
                _set_par_expr(c, a_n,
                              f"op('flash_lag')['{channel}'][0] * {max_alpha}")
                break
        return c

    flash_hit  = _make_flash('flash_hit',  'hit_this_frame',
                              (0.0, 1.0, 0.45),  0.45, node_y=-650)
    flash_bad  = _make_flash('flash_bad',  'bad_cut_this_frame',
                              (1.0, 0.30, 0.30), 0.60, node_y=-720)
    flash_miss = _make_flash('flash_miss', 'miss_this_frame',
                              (1.0, 1.0, 1.0),   0.25, node_y=-790)

    # 11. HUD composite — stack everything onto the rendered scene ----------
    # render_scene + flash_miss + flash_bad + flash_hit + text overlays.
    # Order matters: later inputs render OVER earlier ones, so flashes
    # go before text and text goes last.
    comp_out = _find_or_create(comp, compositeTOP, 'comp_out')
    comp_out.par.operand = 'over'
    comp_out.nodeX = 800
    comp_out.nodeY = 0

    # Wipe any existing inputs and re-attach in a known order. Composite
    # TOPs in TD support many inputs; we connect each layer to a
    # successive index.
    layers_in_order = [
        render_scene,
        flash_miss, flash_bad, flash_hit,
        text_eventlog, text_song_time,
        text_accuracy, text_combo, text_score,
    ]
    # First disconnect everything, then reconnect in our order.
    for ic in comp_out.inputConnectors:
        for c in list(ic.connections):
            try:
                c.destroy()
            except Exception:
                pass
    for i, layer in enumerate(layers_in_order):
        try:
            layer.outputConnectors[0].connect(comp_out.inputConnectors[i])
        except Exception:
            # Fall back to _connect helper (auto-detects existing).
            _connect(layer, comp_out, dst_index=i)

    # 12. Out TOP --------------------------------------------------------------
    out1 = _find_or_create(comp, outTOP, 'out1')
    out1.nodeX = 1000
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
