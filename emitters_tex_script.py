# emitters_tex_script.py
# ======================
# Script TOP callback. Reshapes the sensing-side Lag CHOP output into a
# tiny RGBA32F texture that the `velocity_field` GLSL TOP samples.
#
# Paste into a Text DAT called `emitters_tex_script` inside velocity_controller.
# Attach it as the Callbacks DAT of a Script TOP called `emitters_tex`, and
# set that Script TOP's Common page Output Resolution to match the landmark
# count (width=N, height=2). The bootstrap can't set these easily because
# Script TOPs use numpy buffers sized at cook time; we just set our own
# output shape inside onCook so the setup is self-healing.
#
# Output layout (N landmarks = N columns, 2 rows):
#   Row 0 (v=0.25): RGBA = (x, y, z, visible)
#   Row 1 (v=0.75): RGBA = (vx, vy, vz, force_gain)
# where force_gain = (emit + Burstgain * burst) * visible — lets the shader
# skip the burst/emit math and just read one number for "how hard to push".
# `velocity_field.frag` samples both rows; update both if this layout changes.

import numpy as np


def _landmark_list():
    """Parent par 'Landmarks' is space- or comma-separated. Falls back to
    the velocity_logic default if the module is reachable."""
    par = parent().par.Landmarks.eval() if hasattr(parent().par, 'Landmarks') else ''
    items = [s.strip() for s in str(par).replace(',', ' ').split() if s.strip()]
    if items:
        return items
    try:
        return list(mod.velocity_logic.LANDMARKS)
    except Exception:
        return ['left_wrist', 'right_wrist',
                'left_ankle', 'right_ankle', 'nose']


def _read(chop, name, default=0.0):
    import math as _math
    try:
        c = chop[name]
    except Exception:
        return default
    if c is None or len(c) == 0:
        return default
    try:
        v = float(c[0])
    except (TypeError, ValueError):
        return default
    return v if _math.isfinite(v) else default


def onSetupParameters(scriptOp):
    # Nothing to install here; pars live on the parent COMP.
    return


def onCook(scriptOp):
    # Source CHOP: the sensing-side Lag CHOP. Hard-coded to sibling `lag1`
    # so the script is self-contained (no parent-par reference gymnastics).
    src = op('lag1')
    if src is None:
        # If we haven't been bootstrapped yet, emit a 1x1 black pixel so the
        # downstream shader doesn't error on a zero-size texture.
        scriptOp.copyNumpyArray(np.zeros((1, 1, 4), dtype=np.float32))
        return

    lms = _landmark_list()
    n_lm = len(lms)
    if n_lm == 0:
        scriptOp.copyNumpyArray(np.zeros((1, 1, 4), dtype=np.float32))
        return

    # MULTI-PERSON: pack persons × n_lm emitters into the texture so the
    # velocity field gets contributions from EVERY tracked body. velocity_field
    # reads uNumEmitters = MAX_PERSONS*n_lm and loops them; absent persons
    # contribute force_gain=0 → no splat.
    try:
        bl = mod.body_logic
        persons = bl.MAX_PERSONS
    except Exception:
        bl = None
        persons = 1
    n = persons * n_lm
    buf = np.zeros((2, n, 4), dtype=np.float32)

    # Burst gain is combined into the pre-computed force_gain so the shader
    # doesn't need to know about it. Fetch from parent par once per cook.
    try:
        burst_gain = float(parent().par.Burstgain.eval())
    except Exception:
        burst_gain = 1.0

    # Z-axis force weight — scales vz BEFORE it enters the force field so
    # MediaPipe's noisy depth jitter doesn't push particles back/forth in z
    # when the performer is standing still. 1.0 = full 3D force, 0 = field
    # is purely 2D (xy only). Default 0.05 on the Renderer page damps z
    # noise heavily while still registering real forward/back motion.
    # Different from Zspeedweight (sensing-side, emit/burst sensitivity):
    # this one is purely render-side, controls per-particle z force.
    try:
        z_force_weight = float(parent().par.Zforceweight.eval())
    except Exception:
        z_force_weight = 0.05

    def _rd(p, lm, suffix):
        if bl is None:
            return _read(src, '%s:%s' % (lm, suffix))
        return bl.read_person_chan(src, p, lm, suffix, 0.0)

    i = 0
    for p in range(persons):
        for lm in lms:
            x  = _rd(p, lm, 'x')
            y  = _rd(p, lm, 'y')
            z  = _rd(p, lm, 'z')
            vx = _rd(p, lm, 'vx')
            vy = _rd(p, lm, 'vy')
            vz = _rd(p, lm, 'vz') * z_force_weight
            em = _rd(p, lm, 'emit')
            bu = _rd(p, lm, 'burst')
            vi = _rd(p, lm, 'visible')
            force_gain = (em + burst_gain * bu) * vi
            buf[0, i] = (x, y, z, vi)
            buf[1, i] = (vx, vy, vz, force_gain)
            i += 1

    scriptOp.copyNumpyArray(buf)
    return
