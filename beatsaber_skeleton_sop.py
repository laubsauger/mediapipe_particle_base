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

_STUB_A = (0.0, 0.0, 0.0)
_STUB_B = (0.0, 0.0001, 0.0)

# Coord match the saber renderer: MediaPipe-UV (0..1, y-down). The
# renderer's worldscale + pivot transform is applied at the geo COMP
# level, same as the sabre/note geos.

# Pose landmarks we consume; each requires :x and :y at minimum.
LANDMARKS = ('left_elbow', 'left_wrist', 'right_elbow', 'right_wrist')


def _controller_comp():
    """The beatsaber_controller COMP — preferring a Renderer Controller
    par pointer, falling back to walking up two levels."""
    me_geo = parent()                   # skeleton_geo
    renderer = me_geo.parent()          # beatsaber_renderer
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
    return (float(cx[0]), float(cy[0]),
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

    for elbow_name, wrist_name in (('left_elbow',  'left_wrist'),
                                   ('right_elbow', 'right_wrist')):
        e = _read_xyz(sel, elbow_name)
        w = _read_xyz(sel, wrist_name)
        if e is None or w is None:
            _add_segment(scriptOp, _STUB_A, _STUB_B)
            continue
        _add_segment(scriptOp, e, w)
    return
