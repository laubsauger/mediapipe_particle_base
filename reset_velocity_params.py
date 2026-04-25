"""
reset_velocity_params.py
========================

Force-reset every parent par on the `velocity_controller` COMP to its
current "known good" default. Unlike install_velocity_params.py (which
is idempotent and skips existing pars), this one FORCIBLY overwrites
every value.

Run this when you want the latest default values from the codebase to
take effect on a COMP that was installed earlier.

Usage
-----
- Paste into a Text DAT *inside* the velocity_controller COMP
  (same place install_velocity_params.py goes).
- Right-click the DAT ▸ Run Script.
- The DAT can then be deleted — the par values live on the COMP.

Assumes the pars have already been created (via
install_velocity_params.py). Missing pars are logged and skipped.
Existing pars are overwritten with the values below regardless of any
tuning you've done — so save a note first if you have values you want
to keep.

To tweak one default, change it here and re-run. To apply a set of
"presets" cleanly, make copies of this script with different values.
"""

comp = parent()

# ---------------------------------------------------------------------------
# Sensing page
# ---------------------------------------------------------------------------
SENSING = {
    'Landmarks':           'left_wrist right_wrist left_ankle right_ankle nose',
    'Visibilitythreshold': 0.5,
    'Trustthreshold':      0.75,
    'Velocitysmooth':      0.08,
    'Accelsmooth':         0.05,
    'Speedscale':          2.5,
    'Accelthreshold':      8.0,
    'Accelscale':          40.0,
    'Burstdecay':          0.35,
    'Maxjump':             0.30,
    'Settleframes':        5,
    'Zspeedweight':        0.35,
    'Blendtime':           0.08,
}

# ---------------------------------------------------------------------------
# Renderer page
# ---------------------------------------------------------------------------
RENDERER = {
    # Emission
    'Spawnrate':       5000.0,
    'Burstgain':       6.0,
    # Emission region (2D velocity-aligned scatter)
    'Spawncount':      12,
    'Spawnspread':     0.08,   # max along-velocity extent at full speed
    'Spawnspreadref':  0.8,    # speed at which full size is reached
    'Spawnspreadmin':  0.02,   # rest size (lump extent in both axes)
    'Spawnperpratio':  0.3,    # perp/along aspect at speed (streak shape)
    'Spawnvelscale':   0.04,   # near-zero initial launch
    'Spawnvelfan':     0.5,    # visible cone
    # Flow field
    'Fieldradius':     0.05,   # tight splat
    'Fieldforce':      0.05,   # near-zero force — Velocity Damping must be 2+ on Particle POP or particles still fling
    'Fielddecay':      0.30,   # short persistence
    # Z (depth) scaling
    'Zgain':           0.2,    # subtle depth-to-size
    'Zforceweight':    0.05,   # nearly zero z-force so depth jitter doesn't fling forward/back
    # Velocity-stretched kernel
    'Velstretch':      0.8,
    'Stretchspeedref': 2.0,
    # Curl noise (idle drift + organic bending)
    'Curlgain':        0.2,
    'Curlscale':       0.5,    # < particle extent so curl directions vary across cloud
    # Life
    'Lifemin':         0.6,
    'Lifemax':         1.5,
    # Bounding box for containment (particle space, MediaPipe coords)
    'Boundsminx':      0.0,
    'Boundsminy':      0.0,
    'Boundsminz':     -0.5,
    'Boundsmaxx':      1.0,
    'Boundsmaxy':      1.0,
    'Boundsmaxz':      0.5,
    'Boundsbounce':    0.4,
    'Boundsmargin':    0.0,
    # Screen-space feedback
    'Feedbackenable':  True,
    'Feedbackfade':    0.92,
    'Feedbackzoom':    1.003,
}


def _apply(mapping, page_label):
    applied = 0
    missing = []
    for name, val in mapping.items():
        p = getattr(comp.par, name, None)
        if p is None:
            missing.append(name)
            continue
        try:
            p.val = val
            applied += 1
        except Exception as e:
            print(f"  FAIL {page_label}.{name}: {e}")
    print(f"{page_label}: {applied}/{len(mapping)} pars set.")
    if missing:
        print(f"  missing (run install_velocity_params.py first): {missing}")


_apply(SENSING, 'Sensing')
_apply(RENDERER, 'Renderer')

print("reset_velocity_params: done. All existing pars forced to current "
      "defaults. Save a note of your previous tuning if you had custom "
      "values — they're now overwritten.")
