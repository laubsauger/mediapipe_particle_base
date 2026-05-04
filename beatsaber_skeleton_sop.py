# beatsaber_skeleton_sop.py
# =========================
# Visual debug overlay — renders the user's tracked forearms as line
# segments, one per side, so you can see live whether MediaPipe is
# producing usable elbow/wrist data BEFORE judging the saber math.
#
# Two primitives per cook:
#   prim 0 : left_elbow → left_wrist
#   prim 1 : right_elbow → right_wrist
#
# Reads the four landmark channels directly from the controller's
# upstream `select1` CHOP (which already gates on `:visible`). When
# a landmark is not visible we collapse the segment to a degenerate
# stub so primitive numbering stays stable across cooks.
#
# Renderer toggle: `Showskeleton` on the renderer COMP. When off, the
# whole skeleton geo is invisible (the geo COMP's `render` flag is
# bound to that par via expression).

# Stubs parked far behind the camera near plane so they're clipped out
# of every render and never produce visual artefacts. Same trick as
# beatsaber_saber_sop — needed because TD's Script SOP must always emit
# at least one primitive per branch for prim numbering stability.
_STUB_A = (0.5, 0.5, -1000.0)
_STUB_B = (0.5, 0.5, -1000.001)
EDGE_MARGIN = 0.015

# Coord match the saber renderer: MediaPipe-UV (0..1, y-down). The
# renderer's worldscale + pivot transform is applied at the geo COMP
# level, same as the sabre/note geos.

# Pose landmarks we consume; each requires :x and :y at minimum.
LANDMARKS = ('left_elbow', 'left_wrist', 'right_elbow', 'right_wrist')


def _controller_comp():
    """Resolve the beatsaber_controller COMP. In a Script SOP callback,
    `parent()` returns the RENDERER (the geo COMP's parent), not the
    geo COMP itself — TD's free-function `parent()` is shorthand for
    `me.parent(1)` which skips the script-owning geo. The renderer's
    `Controller` par gives us the controller directly."""
    renderer = parent()                 # beatsaber_renderer
    par = getattr(renderer.par, 'Controller', None)
    if par is not None:
        c = par.eval()
        if c is not None:
            return c
    return renderer.parent()


def _read_xyz(sel, base):
    cx = sel[f'{base}:x']
    cy = sel[f'{base}:y']
    cz = sel[f'{base}:z']
    if cx is None or cy is None:
        return None
    # Pose y is HIP-CENTERED with +y up; convert to image-normalized
    # (0 top, 1 bottom) so the renderer's sy=-flip produces correct
    # screen orientation. Same transform as beatsaber_game_tick.
    return (float(cx[0]),
            0.5 - float(cy[0]),
            float(cz[0]) if cz is not None else 0.0)


def _add_segment(scriptOp, p0_xyz, p1_xyz):
    p0 = scriptOp.appendPoint()
    p0.P = p0_xyz
    p1 = scriptOp.appendPoint()
    p1.P = p1_xyz
    poly = scriptOp.appendPoly(2, closed=False, addPoints=False)
    poly[0].point = p0
    poly[1].point = p1
    return poly


def onCook(scriptOp):
    scriptOp.clear()

    ctrl = _controller_comp()
    if ctrl is None:
        _add_segment(scriptOp, _STUB_A, _STUB_B)
        _add_segment(scriptOp, _STUB_A, _STUB_B)
        return

    # Pull from the controller's pre-routed select chop.
    sel = ctrl.op('select1') or ctrl.op('in1')
    if sel is None:
        _add_segment(scriptOp, _STUB_A, _STUB_B)
        _add_segment(scriptOp, _STUB_A, _STUB_B)
        return

    def _valid(p):
        # After the pose-to-image conversion the y range can extend past
        # [0,1] when the user is partially out of frame; we only reject
        # NaN/Inf and the total-zero pattern that means MediaPipe never
        # populated the landmark.
        if p is None:
            return False
        try:
            x, y, _z = float(p[0]), float(p[1]), float(p[2])
        except Exception:
            return False
        # Both x and y exactly zero = upstream returned default zeros
        # (no tracking). Treat as invalid to keep skeleton hidden.
        if x == 0.0 and y == 0.5:
            return False
        return True

    for elbow_name, wrist_name in (('left_elbow',  'left_wrist'),
                                   ('right_elbow', 'right_wrist')):
        e = _read_xyz(sel, elbow_name)
        w = _read_xyz(sel, wrist_name)
        if not (_valid(e) and _valid(w)):
            _add_segment(scriptOp, _STUB_A, _STUB_B)
            continue
        _add_segment(scriptOp, e, w)
    return
