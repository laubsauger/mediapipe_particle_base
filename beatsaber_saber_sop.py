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
# emitted as a tiny line so primitive numbering stays stable (4 prims
# always, in the same order).
_STUB_HILT_BASE = (0.0, 0.0, 0.0)
_STUB_HILT_TOP  = (0.0, 0.0001, 0.0)
_STUB_TIP       = (0.0, 0.0002, 0.0)


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


def _add_segment(scriptOp, p0_xyz, p1_xyz):
    """Append one 2-point line polygon. No Cd — color happens
    downstream via the four Primitive SOPs that select prims 0..3
    by index."""
    p0 = scriptOp.appendPoint()
    p0.P = p0_xyz
    p1 = scriptOp.appendPoint()
    p1.P = p1_xyz
    poly = scriptOp.appendPoly(2, closed=False, addPoints=False)
    poly[0].point = p0
    poly[1].point = p1
    return poly


def _add_sabre(scriptOp, hilt_base, hilt_top, tip):
    """Append the hilt segment and the blade segment for one saber.
    Two primitives appended in fixed order: hilt first (prim N), then
    blade (prim N+1). No Cd is set — downstream Primitive SOPs select
    by index (0/1/2/3) and apply per-prim color."""
    _add_segment(scriptOp, hilt_base, hilt_top)
    _add_segment(scriptOp, hilt_top,  tip)


def _add_stub_sabre(scriptOp):
    """Two degenerate primitives for a side with no tracking data, so
    primitive numbering stays stable across cooks. Numbering matters
    because downstream Primitive SOPs select prims 0..3 by index."""
    _add_segment(scriptOp, _STUB_HILT_BASE, _STUB_HILT_TOP)
    _add_segment(scriptOp, _STUB_HILT_TOP,  _STUB_TIP)


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


def _push_blade_history(comp, key, hilt_top, tip, max_len):
    """Append the latest (hilt_top, tip) pair to the per-side history
    list stored on the parent COMP. Trim to `max_len` entries (oldest
    dropped first). Returns the resulting list."""
    hist = comp.fetch(key, [])
    hist.append((hilt_top, tip))
    if len(hist) > max_len:
        hist = hist[-max_len:]
    comp.store(key, hist)
    return hist


def onCook(scriptOp):
    scriptOp.clear()

    tick = _controller_tick_op()
    if tick is None:
        # Controller not wired yet — emit four degenerate stubs so
        # primitive numbering stays stable and downstream Primitive
        # SOPs don't fail on a missing prim 0/1/2/3.
        _add_stub_sabre(scriptOp)
        _add_stub_sabre(scriptOp)
        return

    comp = parent()

    # Trail config — read for HISTORY MAINTENANCE only. The actual
    # trail rendering happens in beatsaber_trail_sop, which reads the
    # history we maintain here.
    trail_len = int(_read_par(comp, 'Bladetraillen', 16))
    trail_len = max(1, min(trail_len, 120))

    # Emit the FOUR primary primitives (left+right hilt+blade) in
    # stable order:
    #   prim 0 : left  hilt   ← Primitive SOP "color_left_hilt"  (Group=0)
    #   prim 1 : left  blade  ← Primitive SOP "color_left_blade" (Group=1)
    #   prim 2 : right hilt   ← Primitive SOP "color_right_hilt" (Group=2)
    #   prim 3 : right blade  ← Primitive SOP "color_right_blade"(Group=3)
    for side in ('left', 'right'):
        hilt_base = _read_xyz(tick, f'{side}_hilt')
        hilt_top  = _read_xyz(tick, f'{side}_hilt_top')
        tip       = _read_xyz(tick, f'{side}_tip')

        key = _HISTORY_KEY_LEFT if side == 'left' else _HISTORY_KEY_RIGHT

        if hilt_base is None or tip is None:
            _add_stub_sabre(scriptOp)
            # Clear history when tracking drops so we don't leave a
            # stale ghost trail floating in space.
            comp.store(key, [])
            continue

        if hilt_top is None:
            # Older controller — fake a hilt_top one quarter of the way
            # from hilt to tip so we still have two primitives per side.
            hilt_top = (hilt_base[0] + 0.18 * (tip[0] - hilt_base[0]),
                        hilt_base[1] + 0.18 * (tip[1] - hilt_base[1]),
                        hilt_base[2] + 0.18 * (tip[2] - hilt_base[2]))

        _add_sabre(scriptOp, hilt_base, hilt_top, tip)

        # Maintain the trail history for the dedicated trail SOPs to read.
        _push_blade_history(comp, key, hilt_top, tip, trail_len)
    return
