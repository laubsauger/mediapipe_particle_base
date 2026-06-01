# mask_standby.py
# ===============
# Script CHOP callback — outputs `amt` (0..1) = how strongly the mask attractor
# affects the particle field. Consumed by particle_system's in_mask_state CHOP
# channel of the same name.
#
# Maskmode (parent menu on mask_controller):
#   Off      → 0   (mask never active)
#   Always   → 1   (mask always a factor)
#   Standby  → fades to 1 when NO pose is present, 0 when a person appears
#             (the mask is the passive-state "screensaver"; the body takes over).
#
# Lagged toward the target over `Maskfade` seconds for a smooth crossfade.
# Reads `in_pose` visibility channels — that dependency also forces the Script
# CHOP to cook every frame (the no-input Script CHOP gotcha).

import math as _m


def onCook(scriptOp):
    scriptOp.clear()
    p = parent()
    mode = str(p.par.Maskmode.eval()) if hasattr(p.par, 'Maskmode') else 'Standby'
    fade = float(p.par.Maskfade.eval()) if hasattr(p.par, 'Maskfade') else 1.0

    # Pose presence from `in_pose` (CHOP input on mask_controller). Reading
    # visibility forces a per-frame cook dependency. Sum FINITE in-range
    # visibilities; ignore NaN/Inf (MediaPipe emits those on occluded joints).
    present = 0.0
    pose = op('in_pose')
    if pose is not None:
        s = 0.0
        # Try a few common channel patterns. Multi-person schema uses
        # `p<N>:<lm>:visible`; pre-Lag chains may use `<lm>:visible` or
        # `visibility<idx>`. Accept whichever is present.
        for pat in ('*:visible', '*:*:visible', 'visibility*'):
            for c in pose.chans(pat):
                try:
                    v = float(c.eval())
                except Exception:
                    continue
                if _m.isfinite(v):
                    s += max(0.0, min(1.0, v))
        present = 1.0 if s > 0.5 else 0.0

    if mode == 'Off':
        target = 0.0
    elif mode == 'Always':
        target = 1.0
    else:  # Standby
        target = 1.0 - present

    # Exponential smoothing toward target. Guard the stored prev — `amt`
    # multiplies the mask force in shaders; a single Inf there blows up every
    # particle's velocity. Clamp to [0,1] at the boundary.
    prev = float(p.fetch('maskamt', target))
    if not _m.isfinite(prev):
        prev = target
    dt = 1.0 / max(1e-6, me.time.rate)
    k = 1.0 - pow(0.01, dt / max(0.05, fade))
    amt = prev + (target - prev) * k
    if not _m.isfinite(amt):
        amt = target
    amt = max(0.0, min(1.0, amt))
    p.store('maskamt', amt)

    scriptOp.numSamples = 1
    scriptOp.appendChan('amt')
    scriptOp['amt'][0] = amt
    return
