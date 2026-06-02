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
    # Presence detection. COUNT confidently-visible landmarks rather than SUM
    # all visibilities — a plain sum over many landmarks crosses any low
    # threshold from per-joint noise alone (e.g. 33 joints × ~0.05 = 1.65 > 0.5),
    # so standby never engaged. A real person lights several joints well above
    # the confidence floor; ambient noise lights ~none.
    vis_floor = float(p.par.Maskvisfloor.eval()) if hasattr(p.par, 'Maskvisfloor') else 0.5
    need      = int(p.par.Maskminjoints.eval()) if hasattr(p.par, 'Maskminjoints') else 3
    present = 0.0
    n_vis = 0
    count = 0
    pose = op('in_pose')
    if pose is not None and pose.numChans:
        # DEVICE-ROBUST: scan ALL channels for a visibility/confidence name by
        # SUBSTRING (works regardless of the pose source's naming — `p0:lm:visible`,
        # `lm:visible`, `visibility12`, `*_conf`, …). First pass finds the value
        # scale (some sources emit 0..100 instead of 0..1); second pass counts
        # joints above the floor on the normalised scale.
        vchans = []
        for c in pose.chans():
            nm = c.name.lower()
            if 'visib' in nm or nm.startswith('visibility') or nm.endswith(':conf') or '_conf' in nm:
                vchans.append(c)
        n_vis = len(vchans)
        vmax = 0.0
        raw = []
        for c in vchans:
            try:
                v = float(c.eval())
            except Exception:
                continue
            if _m.isfinite(v):
                raw.append(v)
                if v > vmax:
                    vmax = v
        scale = 100.0 if vmax > 1.5 else 1.0      # auto-detect 0..100 sources
        count = sum(1 for v in raw if (v / scale) > vis_floor)
        present = 1.0 if count >= max(1, need) else 0.0
    # Debug readout — read on the OTHER device to see why standby isn't flipping:
    #   op('mask_controller').fetch('_standby_debug')
    # → {'n_vis': how many visibility channels found, 'count': confident joints,
    #    'present': 0/1, 'mode': ...}. If n_vis==0 the pose source isn't exposing
    # a visibility channel under any known name (wire in_pose / check the source).
    p.store('_standby_debug', {'n_vis': n_vis, 'count': count,
                               'present': present, 'mode': mode})

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
