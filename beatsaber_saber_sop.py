# beatsaber_saber_sop.py
# ======================
# Script SOP callback — builds two sabre geometries (left + right) from
# the beatsaber_controller's game_tick CHOP state.
#
# Outputs UNCOLORED line geometry with FOUR primitives, stable in this
# order so downstream Primitive SOPs can color them by index:
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
# by primitive number (see beatsaber_controller_setup.md). We don't set a
# per-point Cd attribute here because TD's Script SOP requires an
# attribute-creation call whose exact signature varies across builds;
# downstream Primitive SOPs handle Cd creation reliably on any build.
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
    """Append one 2-point line polygon. No Cd — color happens downstream."""
    p0 = scriptOp.appendPoint()
    p0.P = p0_xyz
    p1 = scriptOp.appendPoint()
    p1.P = p1_xyz
    poly = scriptOp.appendPoly(2, closed=False, addPoints=False)
    poly[0].point = p0
    poly[1].point = p1


def _add_sabre(scriptOp, hilt_base, hilt_top, tip):
    """Append the hilt segment and the blade segment for one saber. Two
    primitives appended in fixed order: hilt first, then blade."""
    _add_segment(scriptOp, hilt_base, hilt_top)   # hilt
    _add_segment(scriptOp, hilt_top,  tip)        # blade


def _add_stub_sabre(scriptOp):
    """Two degenerate primitives for a side with no tracking data, so
    primitive numbering stays stable."""
    _add_segment(scriptOp, _STUB_HILT_BASE, _STUB_HILT_TOP)
    _add_segment(scriptOp, _STUB_HILT_TOP,  _STUB_TIP)


def onCook(scriptOp):
    scriptOp.clear()

    tick = _controller_tick_op()
    if tick is None:
        # Controller not wired yet — emit four degenerate stubs so
        # primitive numbering is stable and downstream Color SOPs
        # don't fail.
        _add_stub_sabre(scriptOp)
        _add_stub_sabre(scriptOp)
        return

    for side in ('left', 'right'):
        hilt_base = _read_xyz(tick, f'{side}_hilt')
        # hilt_top is the new channel emitted by beatsaber_game_tick. If
        # it's missing (older controller version), synthesize it from
        # hilt + a small offset along the dir axis so the geometry still
        # renders something sensible.
        hilt_top  = _read_xyz(tick, f'{side}_hilt_top')
        tip       = _read_xyz(tick, f'{side}_tip')

        if hilt_base is None or tip is None:
            _add_stub_sabre(scriptOp)
            continue

        if hilt_top is None:
            # Older controller — fake a hilt_top one quarter of the way
            # from hilt to tip so we still have two primitives per side.
            hilt_top = (hilt_base[0] + 0.18 * (tip[0] - hilt_base[0]),
                        hilt_base[1] + 0.18 * (tip[1] - hilt_base[1]),
                        hilt_base[2] + 0.18 * (tip[2] - hilt_base[2]))

        _add_sabre(scriptOp, hilt_base, hilt_top, tip)
    return
