# beatsaber_saber_sop.py
# ======================
# Script SOP callback — builds the LIVE sabre line geometry from the
# beatsaber_controller's game_tick CHOP state. The motion-trail
# rendering lives in a SEPARATE Script SOP (beatsaber_trail_sop.py)
# fed into its own Geometry COMP per side, with a dedicated Constant
# MAT carrying the side color. This split keeps the live-blade
# coloring (which uses per-prim-index Primitive SOPs) cleanly
# separated from the trail coloring (which uses per-MAT side tint),
# and removes any per-vertex-attribute APIs that vary across TD builds.
#
# Outputs UNCOLORED line geometry with FOUR primitives in stable order
# so downstream Primitive SOPs can color them by index:
#
#   prim 0 : LEFT  hilt  (hilt_base → hilt_top)   — short stub at the wrist
#   prim 1 : LEFT  blade (hilt_top  → tip)        — main glowing segment, RED
#   prim 2 : RIGHT hilt  (hilt_base → hilt_top)
#   prim 3 : RIGHT blade (hilt_top  → tip)        — main glowing segment, BLUE
#
# Primitive numbering is STABLE even when one side has degraded tracking:
# a degenerate stub line is emitted for any missing-data side so that
# downstream Color SOPs and selectors keep working.
#
# Color is applied DOWNSTREAM via four chained Primitive SOPs that select
# by primitive number (see beatsaber_renderer_setup.md). We don't set a
# per-point Cd attribute here because TD's Script SOP `point.color`/
# `point.Cd` API support varies across builds — relying on it produced
# silent white-trail rendering for some users. Downstream Primitive SOPs
# handle Cd creation reliably on any build.
#
# Blade-trail history
# -------------------
# We still maintain the blade-history list (last N (hilt_top, tip)
# pairs per side) here, stored on the renderer COMP via fetch/store.
# The dedicated trail Script SOPs (beatsaber_trail_sop.py) READ that
# stored history to build their per-side trail geometry — keeping the
# history-update logic in one place (only this script writes it; the
# trail SOPs only read).
#
# The Script SOP reads the game_tick CHOP via an op() reference. By
# default we use the relative path `../beatsaber_controller/game_tick`;
# override via the parent COMP par `Controller` → pointer to the
# beatsaber_controller COMP.
#
# This file is paired with the matching synced Text DAT inside the
# beatsaber_renderer Base COMP and attached as the Callbacks DAT of a
# Script SOP called `sabers_sop`.


# Degenerate stub used when a saber side has no valid tracking data —
# placed FAR behind the camera near plane so it's clipped out of every
# render and never appears as a visual artefact. We still need a stub
# (rather than emitting no geometry) so primitive numbering stays
# stable for downstream operators.
_STUB_HILT_BASE = (0.5, 0.5, -1000.0)
_STUB_HILT_TOP  = (0.5, 0.5, -1000.001)
_STUB_TIP       = (0.5, 0.5, -1000.002)

# Coloring is applied DOWNSTREAM via Material SOPs that target the
# per-prim groups assigned in `_add_segment` — this build's Script SOP
# does not expose per-vertex Cd, so MAT-per-group is the route.


def _controller_tick_op():
    """Resolve the game_tick CHOP. Prefer a par pointer on the renderer
    COMP; fall back to a conventional relative path."""
    comp = parent()
    par = getattr(comp.par, 'Controller', None)
    if par is not None:
        ctrl = par.eval()
        if ctrl is not None:
            tick = ctrl.op('game_tick')
            if tick is not None:
                return tick
    # Fallback — sibling COMP via relative path.
    return comp.op('../beatsaber_controller/game_tick')


def _read_xyz(chop, base):
    """Read (base_x, base_y, base_z) from the game_tick CHOP, or None
    if any required channel is missing."""
    cx = chop[f'{base}_x']
    cy = chop[f'{base}_y']
    cz = chop[f'{base}_z']
    if cx is None or cy is None or cz is None:
        return None
    return (float(cx[0]), float(cy[0]), float(cz[0]))


def _add_segment(scriptOp, p0_xyz, p1_xyz, group=None):
    """Append one 2-point line polygon. Per-prim color/material is
    applied downstream by Material SOPs that select prims by group;
    if `group` is given, the prim is added to that point/prim group
    so a downstream Material SOP can target it."""
    p0 = scriptOp.appendPoint()
    p0.P = p0_xyz
    p1 = scriptOp.appendPoint()
    p1.P = p1_xyz
    poly = scriptOp.appendPoly(2, closed=False, addPoints=False)
    poly[0].point = p0
    poly[1].point = p1
    if group is not None:
        try:
            grp = scriptOp.primGroups.get(group) if hasattr(scriptOp.primGroups, 'get') else None
            if grp is None:
                grp = scriptOp.createPrimGroup(group)
            grp.add(poly)
        except Exception:
            pass
    return poly


def _add_sabre(scriptOp, hilt_base, hilt_top, tip, side):
    """Append ONE 2-point polyline per saber (hilt_base → tip). Skipping
    the intermediate hilt_top vertex avoids any visible kink the
    wireframeSOP could introduce at the bend (caps on internal verts
    can render with subtle angle artifacts even when the polyline is
    geometrically collinear). The hilt segment becomes the dim base of
    the same tube — single material, single cap on each end, fully
    straight."""
    p0 = scriptOp.appendPoint(); p0.P = hilt_base
    p1 = scriptOp.appendPoint(); p1.P = tip
    poly = scriptOp.appendPoly(2, closed=False, addPoints=False)
    poly[0].point = p0
    poly[1].point = p1
    try:
        grp = scriptOp.primGroups.get(f'sabre_{side}') if hasattr(scriptOp.primGroups, 'get') else None
        if grp is None:
            grp = scriptOp.createPrimGroup(f'sabre_{side}')
        grp.add(poly)
    except Exception:
        pass
    return poly


def _add_stub_sabre(scriptOp, side):
    p0 = scriptOp.appendPoint(); p0.P = _STUB_HILT_BASE
    p1 = scriptOp.appendPoint(); p1.P = _STUB_TIP
    poly = scriptOp.appendPoly(2, closed=False, addPoints=False)
    poly[0].point = p0; poly[1].point = p1
    try:
        grp = scriptOp.createPrimGroup(f'sabre_{side}')
        grp.add(poly)
    except Exception:
        pass


_HISTORY_KEY_LEFT  = 'sabers_blade_history_left'
_HISTORY_KEY_RIGHT = 'sabers_blade_history_right'


def _read_par(comp, name, default):
    """Read a par on the parent COMP if present; fall back to default
    so the script works even before the user installs the par."""
    p = getattr(comp.par, name, None)
    if p is None:
        return default
    try:
        return p.eval()
    except Exception:
        return default


def _push_blade_history(comp, key, hilt_top, tip, speed, max_len):
    """Append the latest (hilt_top, tip, speed) tuple to the per-side
    history list stored on the parent COMP. `speed` is the tip-velocity
    magnitude at this cook — trail_sop uses it to brighten fast strokes
    and dim slow ones, segment-by-segment at the moment of capture."""
    hist = comp.fetch(key, [])
    hist.append((hilt_top, tip, float(speed)))
    if len(hist) > max_len:
        hist = hist[-max_len:]
    comp.store(key, hist)
    return hist


def _read_speed(chop, side):
    """Read tip-velocity magnitude for `side` from the game_tick CHOP.
    Falls back to computing it from <side>_vel_x/y/z if the
    pre-summed channel isn't there."""
    c = chop[f'{side}_tip_speed']
    if c is not None:
        try:
            return float(c[0])
        except Exception:
            pass
    vx = chop[f'{side}_vel_x']; vy = chop[f'{side}_vel_y']; vz = chop[f'{side}_vel_z']
    if vx is None or vy is None or vz is None:
        return 0.0
    try:
        return (float(vx[0])**2 + float(vy[0])**2 + float(vz[0])**2) ** 0.5
    except Exception:
        return 0.0


def onCook(scriptOp):
    scriptOp.clear()

    tick = _controller_tick_op()
    if tick is None:
        # Controller not wired yet — emit four degenerate stubs so
        # primitive numbering stays stable.
        _add_stub_sabre(scriptOp, 'left')
        _add_stub_sabre(scriptOp, 'right')
        return

    comp = parent()

    # Trail config — read for HISTORY MAINTENANCE only. The actual
    # trail rendering happens in beatsaber_trail_sop, which reads the
    # history we maintain here.
    trail_len = int(_read_par(comp, 'Bladetraillen', 24))
    trail_len = max(1, min(trail_len, 120))

    # Emit the FOUR primary primitives in stable order:
    #   prim 0 : left  hilt  (grey)
    #   prim 1 : left  blade (red)
    #   prim 2 : right hilt  (grey)
    #   prim 3 : right blade (blue)
    for side in ('left', 'right'):
        key = _HISTORY_KEY_LEFT if side == 'left' else _HISTORY_KEY_RIGHT

        # If tracking is lost (`<side>_tracking_active=0`), emit a
        # degenerate stub so the saber disappears instead of snapping
        # to MediaPipe's (0, 0) corner whenever the pose model loses
        # the user. Trail history is also wiped so we don't leave a
        # ghost ribbon behind.
        ta = tick[f'{side}_tracking_active']
        if ta is not None and float(ta[0]) < 0.5:
            _add_stub_sabre(scriptOp, side)
            comp.store(key, [])
            continue

        hilt_base = _read_xyz(tick, f'{side}_hilt')
        hilt_top  = _read_xyz(tick, f'{side}_hilt_top')
        tip       = _read_xyz(tick, f'{side}_tip')

        if hilt_base is None or tip is None:
            _add_stub_sabre(scriptOp, side)
            comp.store(key, [])
            continue

        if hilt_top is None:
            hilt_top = (hilt_base[0] + 0.18 * (tip[0] - hilt_base[0]),
                        hilt_base[1] + 0.18 * (tip[1] - hilt_base[1]),
                        hilt_base[2] + 0.18 * (tip[2] - hilt_base[2]))

        _add_sabre(scriptOp, hilt_base, hilt_top, tip, side)

        # Capture per-frame tip speed so the trail can brighten/dim
        # each segment based on the swing speed at the moment that
        # segment was laid down.
        speed = _read_speed(tick, side)
        _push_blade_history(comp, key, hilt_top, tip, speed, trail_len)
    return
