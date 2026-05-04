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

# Per-side trail tints. Match the live blade colors (defined in
# bootstrap_beatsaber_renderer.py LEFT_BLADE_RGB / RIGHT_BLADE_RGB).
# Alpha is overridden per-segment by the age-fade computation below.
_LEFT_RGB  = (1.00, 0.20, 0.30)   # red
_RIGHT_RGB = (0.20, 0.50, 1.00)   # blue

# Age-fade alpha endpoints. Newest segment is at ALPHA_NEW (most
# opaque, brightest leading edge); oldest is at ALPHA_OLD (mostly
# faded). Linear interpolation between them.
ALPHA_NEW = 0.95
ALPHA_OLD = 0.15


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


def _ensure_cd_attribute(scriptOp):
    """Make sure the SOP has a Cd point attribute. Without this,
    `point.Cd = (r,g,b,a)` raises AttributeError because the
    attribute simply isn't on the geometry yet. The documented TD
    pattern is to call appendCustomAttribute once per cook after
    `clear()` — it's idempotent within a cook.

    Different TD builds have slightly different signatures; try the
    common ones in order."""
    try:
        scriptOp.appendCustomAttribute(td.AttribType.Color,
                                       default=(1.0, 1.0, 1.0, 1.0))
        return True
    except Exception:
        pass
    try:
        scriptOp.appendCustomAttribute(td.AttribType.Color, 'Cd',
                                       default=(1.0, 1.0, 1.0, 1.0))
        return True
    except Exception:
        pass
    if hasattr(scriptOp, 'createPointAttribute'):
        try:
            scriptOp.createPointAttribute('Cd', 4,
                                          (1.0, 1.0, 1.0, 1.0))
            return True
        except Exception:
            pass
    return False


def _set_point_cd(point, rgba):
    """Write per-point Cd (r,g,b,a). After `_ensure_cd_attribute`
    has run, `point.Cd = rgba` is the canonical TD write path. Try
    a couple of attribute names in case the build exposes the
    color attribute under a different property name."""
    for attr in ('Cd', 'color'):
        if hasattr(point, attr):
            try:
                setattr(point, attr, rgba)
                return True
            except Exception:
                continue
    return False


def _add_colored_segment(scriptOp, p0_xyz, p1_xyz, rgba):
    """Append one 2-point line polygon and write per-point Cd on
    both endpoints. Requires _ensure_cd_attribute to have been
    called earlier in the same cook."""
    p0 = scriptOp.appendPoint()
    p0.P = p0_xyz
    _set_point_cd(p0, rgba)
    p1 = scriptOp.appendPoint()
    p1.P = p1_xyz
    _set_point_cd(p1, rgba)
    poly = scriptOp.appendPoly(2, closed=False, addPoints=False)
    poly[0].point = p0
    poly[1].point = p1
    return poly


# A single degenerate stub used when no trail data is available — keeps
# the SOP non-empty so a downstream Render TOP doesn't print a "no
# geometry" warning. Stub uses zero alpha so it's invisible.
_STUB = ((0.0, 0.0, 0.0), (0.0, 0.0001, 0.0))
_STUB_RGBA = (0.0, 0.0, 0.0, 0.0)


def onCook(scriptOp):
    scriptOp.clear()
    # Cd point attribute MUST be created before any per-point Cd
    # write, otherwise the .Cd setter raises AttributeError on
    # the freshly-cleared SOP.
    _ensure_cd_attribute(scriptOp)

    me_geo   = parent()                # parent Geometry COMP
    side     = (_read_par(me_geo, 'Side', 'left') or 'left').lower()
    renderer = _renderer_comp()

    # Honor the renderer-COMP's master toggle. When off, emit a
    # zero-alpha stub only — keeps the SOP graph stable but invisible.
    trail_on = bool(_read_par(renderer, 'Bladetrail', 1))
    if not trail_on:
        _add_colored_segment(scriptOp, _STUB[0], _STUB[1], _STUB_RGBA)
        return

    # Read the side-specific history that beatsaber_saber_sop maintains.
    key = _HISTORY_KEY_LEFT if side == 'left' else _HISTORY_KEY_RIGHT
    hist = renderer.fetch(key, []) if renderer is not None else []
    if not hist:
        _add_colored_segment(scriptOp, _STUB[0], _STUB[1], _STUB_RGBA)
        return

    # Side tint — the trail's RGB. Alpha is computed per-segment
    # below (age fade).
    side_rgb = _LEFT_RGB if side == 'left' else _RIGHT_RGB

    # Emit one segment per historical (hilt_top, tip) pair, oldest
    # first → newest last. Per-segment alpha lerps from ALPHA_OLD
    # (oldest) to ALPHA_NEW (newest) so the leading edge of the
    # ribbon is brightest and trails fade to nearly transparent.
    n = len(hist)
    denom = max(1, n - 1)   # guards against single-frame history
    for i, (hilt_top, tip) in enumerate(hist):
        # i=0 = oldest, i=n-1 = newest
        age_fraction = i / denom        # 0 at oldest, 1 at newest
        alpha = ALPHA_OLD + age_fraction * (ALPHA_NEW - ALPHA_OLD)
        rgba = (side_rgb[0], side_rgb[1], side_rgb[2], alpha)
        _add_colored_segment(scriptOp, hilt_top, tip, rgba)
    return
