# body_tex_script.py
# ==================
# Script TOP callback. Packs the performer's skeleton (from in_pose) into a
# tiny RGBA32F texture that `shaders/body_field.frag` splats into the body force
# field. Thin wrapper over `body_logic.py` (pure) per the project convention.
#
# Attach as the Callbacks DAT of a Script TOP `body_tex` inside
# velocity_controller. No manual resolution needed — copyNumpyArray sizes it.
#
# Output layout (NJOINTS columns, 2 × MAX_PERSONS rows; column order ==
# body_logic.JOINTS, rows blocked per person):
#   Row 2·p + 0 (pos+vis): RGBA = (x, y, visible, 1)   person p's joint pos
#   Row 2·p + 1 (vel):     RGBA = (vx, vy, 0, 1)       person p's joint velocity
# `body_field.frag` / `body_viz.frag` loop over persons; absent persons emit
# visibility 0 so they contribute nothing.
#
# Per-person reading: try `p<N>:<lm>:x` first; for person 0 fall back to the
# LEGACY non-prefixed `<lm>:x` so existing single-person MediaPipe data flows
# in unchanged. Multi-person sensors (Kinect/Orbbec) emit the prefixed form.

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

    # Staleness guard — same _pose_same counter written by emitters_tex_script.
    # Output zero joints when stale so body_field / body_viz silently disappear
    # instead of locking onto last-known landmarks.
    try:
        stale_thresh = int(parent().par.Posestaleframes.eval())
    except Exception:
        stale_thresh = 90
    if int(parent().fetch('_pose_same', 0)) > stale_thresh:
        joints = bl.JOINTS
        scriptOp.copyNumpyArray(
            np.zeros((2 * bl.MAX_PERSONS, len(joints), 4), dtype=np.float32))
        return

    joints = bl.JOINTS
    n = len(joints)
    max_p = bl.MAX_PERSONS
    dt = 1.0 / max(1e-6, me.time.rate)

    buf = np.zeros((2 * max_p, n, 4), dtype=np.float32)

    for p in range(max_p):
        # Per-person channel resolution centralised in body_logic — same fallback
        # ladder used by velocity_script_chop + emitters_chop_script.
        pos = []
        vis = []
        for name, mp_idx in joints:
            x = bl.read_first(src, bl.per_person_chans(p, name, 'x'), 0.0)
            y = bl.read_first(src, bl.per_person_chans(p, name, 'y'), 0.0)
            v = bl.read_first(src, bl.per_person_vis_chans(p, mp_idx, name), 0.0)
            pos.append((x, y))
            vis.append(v)

        # Per-person prev-position store so velocity differencing stays correct
        # even when persons enter/leave the frame.
        prev_key = 'body_prev_p%d' % p
        prev = parent().fetch(prev_key, None)
        vels = bl.joint_velocity(prev, pos, dt)
        parent().store(prev_key, pos)

        # Skip dead persons cheaply (all-zero visibility → leave rows zero).
        # The shaders then see visibility 0 and contribute nothing for this p.
        if max(vis) < 0.01:
            continue

        for i in range(n):
            x, y = pos[i]
            vx, vy = vels[i]
            buf[2 * p + 0, i] = (x, y, vis[i], 1.0)
            buf[2 * p + 1, i] = (vx, vy, 0.0, 1.0)

    scriptOp.copyNumpyArray(buf)
    return
