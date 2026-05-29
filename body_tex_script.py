# body_tex_script.py
# ==================
# Script TOP callback. Packs the performer's skeleton (from in_pose) into a
# tiny RGBA32F texture that `shaders/body_field.frag` splats into the body force
# field. Thin wrapper over `body_logic.py` (pure) per the project convention.
#
# Attach as the Callbacks DAT of a Script TOP `body_tex` inside
# velocity_controller. No manual resolution needed — copyNumpyArray sizes it.
#
# Output layout (NJOINTS columns, 2 rows; column order == body_logic.JOINTS):
#   Row 0 (v=0): RGBA = (x, y, visible, 1)      position (MediaPipe-UV) + vis gate
#   Row 1 (v=1): RGBA = (vx, vy, 0, 1)          per-joint velocity (UV/sec)
# `body_field.frag` reads both rows by texelFetch; update both if this changes.
#
# Per-joint velocity is differenced here (in_pose carries position only for the
# full 33; the sensing velocity pipeline only covers the 5 Landmarks). Previous
# positions are stored on the parent COMP across cooks.

import sys
import numpy as np

try:
    if hasattr(mod, 'body_logic'):
        bl = mod.body_logic
    else:
        if project.folder not in sys.path:
            sys.path.append(project.folder)
        import body_logic as bl
except Exception as e:
    bl = None
    debug('body_tex_script: body_logic import failed: %s' % e)


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
    return


def onCook(scriptOp):
    src = op('in_pose')
    if src is None or bl is None:
        scriptOp.copyNumpyArray(np.zeros((1, 1, 4), dtype=np.float32))
        return

    joints = bl.JOINTS
    n = len(joints)

    # Read current joint positions + visibility.
    pos = []
    vis = []
    for name, mp_idx in joints:
        x = _read(src, '%s:x' % name)
        y = _read(src, '%s:y' % name)
        v = _read(src, bl.visibility_index_channel(mp_idx), 0.0)
        pos.append((x, y))
        vis.append(v)

    # Per-joint velocity via stored previous positions. dt from frame rate.
    prev = parent().fetch('body_prev', None)
    dt = 1.0 / max(1e-6, me.time.rate)
    vels = bl.joint_velocity(prev, pos, dt)
    parent().store('body_prev', pos)

    buf = np.zeros((2, n, 4), dtype=np.float32)
    for i in range(n):
        x, y = pos[i]
        vx, vy = vels[i]
        buf[0, i] = (x, y, vis[i], 1.0)
        buf[1, i] = (vx, vy, 0.0, 1.0)

    scriptOp.copyNumpyArray(buf)
    return
