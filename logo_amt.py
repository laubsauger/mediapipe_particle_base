# logo_amt.py
# ===========
# Script CHOP — outputs channel `amt` (0..1) = how strongly the logo affects
# the particle field (attractor force + brightness). Read as a uniform by
# bounds_reflect (force) and color_attr (brightness).
#
# Logomode (parent menu):
#   Off      → 0   (logo never shows)
#   Always   → 1   (logo always a factor)
#   Standby  → fades to 1 when NO pose is present, 0 when a person appears
#             (the logo is the passive-state "screensaver"; the body takes over).
#
# Lagged toward the target over `Logofade` seconds for a smooth crossfade.
# Reads lag1's visibility channels → that dependency also forces this Script
# CHOP to cook every frame (the no-input Script-CHOP gotcha).

import math as _m


def onCook(scriptOp):
    scriptOp.clear()
    p = parent()
    mode = str(p.par.Logomode.eval()) if hasattr(p.par, 'Logomode') else 'Standby'
    fade = float(p.par.Logofade.eval()) if hasattr(p.par, 'Logofade') else 1.0

    # pose presence from lag1 visibility (reading it forces per-frame cook).
    # Only count FINITE, in-range visibilities — MediaPipe emits NaN/±Inf on
    # absent/occluded joints, which must never poison the sum.
    present = 0.0
    lag = op('lag1')
    if lag is not None:
        s = 0.0
        for c in lag.chans('*:visible'):
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

    # exponential smoothing toward target over ~`fade` seconds. Guard the stored
    # previous value and clamp the result to [0,1] — amt is a fade fraction and
    # must NEVER go non-finite/out-of-range (it multiplies the logo force in the
    # GLSL POPs; a single Inf there blows up every particle's velocity).
    prev = float(p.fetch('logoamt', target))
    if not _m.isfinite(prev):
        prev = target
    dt = 1.0 / max(1e-6, me.time.rate)
    k = 1.0 - pow(0.01, dt / max(0.05, fade))   # ~99% of the way over `fade` s
    amt = prev + (target - prev) * k
    if not _m.isfinite(amt):
        amt = target
    amt = max(0.0, min(1.0, amt))
    p.store('logoamt', amt)

    scriptOp.numSamples = 1
    scriptOp.appendChan('amt')
    scriptOp['amt'][0] = amt
    return
