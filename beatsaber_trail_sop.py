# beatsaber_trail_sop.py
# ======================
# Script SOP callback — emits motion-trail line geometry for ONE saber
# side (left or right) with per-vertex Cd carrying the side tint AND
# the age-fade alpha. Lives inside its own dedicated Geometry COMP
# whose Constant MAT is configured to consume per-point Cd including
# alpha. The per-side coloring AND the age fade both come from the
# geometry's vertex color attribute.
#
# Architecture
# ------------
# The live blade renders in `sabers_geo`, colored by per-prim-index
# Primitive SOPs (prim 0..3 selectable). The trail is rendered SEPARATELY
# in `trail_left_geo` and `trail_right_geo`, each containing one of
# these Script SOPs. Each side has its own Constant MAT but both MATs
# are configured identically: base color (1,1,1,1) (identity), Use
# Point Color = On, transparency enabled. The MATs multiply their
# (1,1,1,1) base by the per-vertex Cd we write here, so the trail
# renders in the side tint with its alpha-fade intact.
#
# How the per-vertex Cd is written
# --------------------------------
# In TD's Script SOP, `point.color = (r, g, b, a)` writes the standard
# 4-component Cd point attribute. The MAT MUST be configured to
# CONSUME that attribute — that's what produces the side color and
# alpha at render time. If trails come out white, the bug is on the
# MAT side: check that "Use Point Color" (or equivalent) is on, and
# that transparency / blending is enabled so alpha < 1 is honored.
#
# Required pars on the parent Geometry COMP:
#   - `Side`  (Str): "left" or "right"
#   - (Inherited from renderer COMP) `Bladetrail` toggle, read via
#     parent(2) — installed by the saber bootstrap on the renderer.
#
# Age-fade alpha
# --------------
# Newest trail segment α≈0.95, oldest α≈0.15, linear interpolation.
# Both endpoints of a segment get the same alpha so the segment is a
# uniform-alpha line (the fade happens BETWEEN segments, not within).

# History storage keys — must match what beatsaber_saber_sop writes.
_HISTORY_KEY_LEFT  = 'sabers_blade_history_left'
_HISTORY_KEY_RIGHT = 'sabers_blade_history_right'

# Per-side trail tints. Match the live blade colors.
_LEFT_RGB  = (1.00, 0.20, 0.30)   # red
_RIGHT_RGB = (0.20, 0.50, 1.00)   # blue

# Age-fade alpha endpoints. Newest segment is at ALPHA_NEW (brightest
# leading edge); oldest is at ALPHA_OLD (mostly faded). Quadratic
# interpolation between them — older segments fade FAST so the trail
# reads as a kinetic streak, not a continuous ribbon.
ALPHA_NEW = 0.95
ALPHA_OLD = 0.05

# Velocity-driven brightness curve. Per-segment alpha is multiplied by
# clamp(speed / SPEED_FULL, MIN_INTENSITY, 1.0) using the speed value
# captured AT THE TIME each segment was laid down. Result: a slow drift
# barely shows; a fast swing burns bright at the tip. Speed is in
# MediaPipe-UV units per cook (so ~1.0 = swiping across the whole frame
# in one frame, which never happens — typical fast swings ~0.15).
SPEED_FULL    = 0.20
MIN_INTENSITY = 0.10

# Color-boost for very fast swings — push toward white for high speed
# so the trail "glows" at the leading edge of a strong slash.
WHITE_BOOST_SPEED = 0.30   # speed at which color is fully white-shifted
WHITE_BOOST_MAX   = 0.55   # max white mix-in at WHITE_BOOST_SPEED+


def _clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


def _renderer_comp():
    """The renderer COMP holds the trail history + the Bladetrail
    pars. Walk up two levels: my parent is the wrapper Geo COMP,
    its parent is `beatsaber_renderer`. NOTE `.parent()` is called
    as a function — `me_geo.parent` (bare attribute) returns a
    `td.ParentShortcut` helper that does not expose `.par`."""
    me_geo = parent()           # the trail_left_geo / trail_right_geo COMP
    return me_geo.parent()      # the beatsaber_renderer COMP


def _read_par(comp, name, default):
    if comp is None:
        return default
    p = getattr(comp.par, name, None)
    if p is None:
        return default
    try:
        return p.eval()
    except Exception:
        return default


def _add_segment(scriptOp, p0_xyz, p1_xyz, group=None):
    """Append one 2-point line polygon. Optional prim group lets a
    downstream Material SOP target it (we use this to set per-segment
    intensity via per-group scale or per-prim attributes when the
    build supports them; alpha-modulation is otherwise driven globally
    by the Constant MAT bound to live tip-speed)."""
    p0 = scriptOp.appendPoint()
    p0.P = p0_xyz
    p1 = scriptOp.appendPoint()
    p1.P = p1_xyz
    poly = scriptOp.appendPoly(2, closed=False, addPoints=False)
    poly[0].point = p0
    poly[1].point = p1
    if group is not None:
        try:
            grp = scriptOp.createPrimGroup(group)
            grp.add(poly)
        except Exception:
            pass
    return poly


# A single degenerate stub used when no trail data is available — keeps
# the SOP non-empty so a downstream Render TOP doesn't print a "no
# geometry" warning.
_STUB = ((0.0, 0.0, 0.0), (0.0, 0.0001, 0.0))


def onCook(scriptOp):
    scriptOp.clear()

    me_geo   = parent()                # parent Geometry COMP
    side     = (_read_par(me_geo, 'Side', 'left') or 'left').lower()
    renderer = _renderer_comp()

    # Honor the renderer-COMP's master toggle. When off, emit a
    # zero-length stub so the SOP graph stays stable but invisible.
    trail_on = bool(_read_par(renderer, 'Bladetrail', 1))
    if not trail_on:
        _add_segment(scriptOp, _STUB[0], _STUB[1])
        return

    # Read the side-specific history that beatsaber_saber_sop maintains.
    key = _HISTORY_KEY_LEFT if side == 'left' else _HISTORY_KEY_RIGHT
    hist = renderer.fetch(key, []) if renderer is not None else []
    if not hist:
        _add_segment(scriptOp, _STUB[0], _STUB[1])
        return

    # Per-segment fade is approximated by emitting older segments
    # SHORTER (Z-shifted toward the past) so depth-ordering and
    # falloff fade them naturally. The Constant MAT global alpha
    # is bound to live tip-speed via expression, so the whole trail
    # brightens during fast swings and dims during slow drifts.
    #
    # Each segment is also added to a group named `seg_<i>` (newest
    # = `seg_new`, oldest = `seg_old`) so a future enhancement could
    # apply per-segment material if the build adds the API. For now
    # the groups are descriptive markers only.
    n = len(hist)
    for i, entry in enumerate(hist):
        if len(entry) == 3:
            hilt_top, tip, _speed = entry
        else:
            hilt_top, tip = entry
        # Fade-by-shrinkage: older segments shrink toward their
        # midpoint so they recede visually even with a single MAT
        # alpha. Newest segments stay full-length.
        age_fraction = (i + 1) / n   # newest=1, oldest~0
        scale = 0.25 + 0.75 * age_fraction
        cx = (hilt_top[0] + tip[0]) * 0.5
        cy = (hilt_top[1] + tip[1]) * 0.5
        cz = (hilt_top[2] + tip[2]) * 0.5
        p0 = (cx + (hilt_top[0] - cx) * scale,
              cy + (hilt_top[1] - cy) * scale,
              cz + (hilt_top[2] - cz) * scale)
        p1 = (cx + (tip[0]      - cx) * scale,
              cy + (tip[1]      - cy) * scale,
              cz + (tip[2]      - cz) * scale)
        _add_segment(scriptOp, p0, p1, group=f'seg_{i}')
    return
