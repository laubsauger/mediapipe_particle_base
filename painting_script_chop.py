# painting_script_chop.py
# ========================
# Script CHOP callback for the painting controller.
#
# Design: this callback is intentionally thin. It reads a handful of named
# CHOP channels and a handful of parent-COMP parameters, then delegates to
# painting_logic.py for the actual weight computation.
#
# IMPORTANT: parameters live on the ENCLOSING Base COMP, not on the Script
# CHOP itself. That way the whole controller ships as one tidy component
# whose user-facing knobs are visible at the top level. See the setup guide
# in chat for the exact parameter list.
#
# Required neighbours (siblings of this Callbacks DAT inside the Base COMP):
#   - Text DAT named `painting_logic` (contents = painting_logic.py)
#   - A Script CHOP that references this DAT as its Callbacks DAT
#
# Input channel contract (rename upstream to match — these are the names
# blankensmithing's MediaPipe pose tracker already emits for wrist x/y):
#   left_wrist:x, left_wrist:y                    (required)
#   right_wrist:x, right_wrist:y                  (required)
#   left_wrist:visibility, right_wrist:visibility (optional, 0..1)
#   scene                                         (required, 0..1)

# --------------------------------------------------------------------------
# Helpers for reading named channels across all inputs of the Script CHOP.
# --------------------------------------------------------------------------

def _find_chan(scriptOp, name):
    for cin in scriptOp.inputs:
        if cin is None:
            continue
        c = cin[name]
        if c is not None:
            return c
    return None


def _read(scriptOp, name, default=0.0):
    c = _find_chan(scriptOp, name)
    if c is None:
        return default
    return float(c[0])


# --------------------------------------------------------------------------
# Main cook.
# --------------------------------------------------------------------------

def onCook(scriptOp):
    scriptOp.clear()
    # We emit a single instantaneous target per cook and let a downstream
    # Lag CHOP do the blending, so Time Slice mode must be off (otherwise
    # TD manages numSamples itself and setting it here warns).
    if scriptOp.isTimeSlice:
        scriptOp.isTimeSlice = False

    logic = mod.painting_logic  # sibling Text DAT named `painting_logic`
    par = parent().par          # params live on the enclosing Base COMP

    # ----- Inputs ---------------------------------------------------------
    left_xy  = (_read(scriptOp, 'left_wrist:x'),  _read(scriptOp, 'left_wrist:y'))
    right_xy = (_read(scriptOp, 'right_wrist:x'), _read(scriptOp, 'right_wrist:y'))

    # Visibility channels are optional. If the channel exists we read it;
    # otherwise we pass None so the logic treats the wrist as fully visible.
    left_vis  = _read(scriptOp, 'left_wrist:visibility',  1.0) \
                if _find_chan(scriptOp, 'left_wrist:visibility')  else None
    right_vis = _read(scriptOp, 'right_wrist:visibility', 1.0) \
                if _find_chan(scriptOp, 'right_wrist:visibility') else None

    # Scene: prefer the second input's first channel (typical wiring:
    # in1 = pose/wrists, in2 = scene). Fall back to a named 'scene' channel
    # on any input (useful if everything is merged into a single input).
    if len(scriptOp.inputs) >= 2 and scriptOp.inputs[1] is not None \
            and scriptOp.inputs[1].numChans > 0:
        scene = float(scriptOp.inputs[1][0][0])
    else:
        scene = _read(scriptOp, 'scene', 0.0)

    # ----- Parameters (from parent COMP) ----------------------------------
    bt = par.Bordertop.eval()
    bb = par.Borderbottom.eval()
    bl = par.Borderleft.eval()
    br = par.Borderright.eval()
    vt = par.Visibilitythreshold.eval()
    mode = par.Wristmode.eval()
    blend_scene = bool(par.Blendscene.eval())
    src_a = par.Sourceaspect.eval() or None
    view_a = par.Viewaspect.eval() or None

    # ----- Logic ----------------------------------------------------------
    hands = logic.wrists_in_bounds(
        left_xy, right_xy,
        left_vis, right_vis,
        bl, br, bt, bb,
        visibility_threshold=vt,
        mode=mode,
        source_aspect=src_a,
        view_aspect=view_a,
    )

    if blend_scene:
        weights = logic.compute_targets_blended_scene(scene, hands)
    else:
        weights = logic.compute_targets(scene, hands)

    # ----- Output ---------------------------------------------------------
    scriptOp.numSamples = 1
    scriptOp.rate = me.time.rate

    for name in logic.OUTPUT_NAMES:
        scriptOp.appendChan(name)[0] = weights[name]

    # Debug channels — handy on a Trail CHOP while tuning the dead-zone.
    scriptOp.appendChan('hands_in_bounds')[0] = 1.0 if hands else 0.0
    scriptOp.appendChan('scene_in')[0] = scene
    scriptOp.appendChan('left_in_view')[0] = 1.0 if logic.is_point_in_bounds(
        left_xy[0], left_xy[1], bl, br, bt, bb) else 0.0
    scriptOp.appendChan('right_in_view')[0] = 1.0 if logic.is_point_in_bounds(
        right_xy[0], right_xy[1], bl, br, bt, bb) else 0.0
    return
