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
#   Row 0 (v=0.25): RGBA = (x, y, vx, vy)
#   Row 1 (v=0.75): RGBA = (emit, burst, visible, speed)
#
# Both `velocity_field.frag` and the Source POP's spawn texture path sample
# this layout directly — don't change row/column order without updating both.

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
    c = chop[name]
    if c is None:
        return default
    return float(c[0])


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
    n = len(lms)
    if n == 0:
        scriptOp.copyNumpyArray(np.zeros((1, 1, 4), dtype=np.float32))
        return

    # Build (height=2, width=n, RGBA) array. Note: TOP convention is the
    # array is indexed [row][col][channel], and the first row is the
    # BOTTOM of the image in UV space (v=0). We pack row0=(x,y,vx,vy) into
    # the array row that corresponds to v=0.25, i.e. the FIRST row (index 0)
    # because TD flips it on upload. If your GLSL sampling looks mirrored,
    # swap the two row indices below.
    buf = np.zeros((2, n, 4), dtype=np.float32)

    for i, lm in enumerate(lms):
        x  = _read(src, f'{lm}:x')
        y  = _read(src, f'{lm}:y')
        vx = _read(src, f'{lm}:vx')
        vy = _read(src, f'{lm}:vy')
        em = _read(src, f'{lm}:emit')
        bu = _read(src, f'{lm}:burst')
        vi = _read(src, f'{lm}:visible')
        sp = _read(src, f'{lm}:speed')
        buf[0, i] = (x, y, vx, vy)           # row 0 = position + velocity
        buf[1, i] = (em, bu, vi, sp)         # row 1 = envelopes

    scriptOp.copyNumpyArray(buf)
    return
