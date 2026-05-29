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
    'Speedscale':          5.0,
    'Accelthreshold':      8.0,
    'Accelscale':          40.0,
    'Burstdecay':          0.35,
    'Maxjump':             0.30,
    'Settleframes':        1,
    'Zspeedweight':        0.1,   # low: MediaPipe z (esp. nose depth) is noisy — keep it from triggering emission so emit/burst track real xy motion
    'Blendtime':           0.08,
}

# ---------------------------------------------------------------------------
# Renderer page
# ---------------------------------------------------------------------------
RENDERER = {
    # Emission
    'Spawnrate':       15000.0,  # informational only (Particle POP uses `w`)
    'Burstgain':       12.0,
    # Emission region (2D velocity-aligned scatter)
    'Spawncount':      18,
    'Spawnspread':     0.14,   # max along-velocity extent at full speed (enlarged area)
    'Spawnspreadref':  0.8,    # speed at which full size is reached
    'Spawnspreadmin':  0.05,   # rest birth zone (enlarged so emission isn't from a point)
    'Spawnperpratio':  0.6,    # perp/along aspect at speed (wider, rounder area)
    'Spawnvelscale':   0.12,   # soft initial launch (was flinging on slight motion)
    'Spawnvelfan':     0.1,    # how much the forward cone WIDENS with speed (rad)
    'Spawnangjitter':  0.1,    # at-rest forward-cone half-angle (rad, ±5.7°); spray hugs motion dir
    # Flow field
    'Fieldradius':     0.05,   # tight splat
    'Fieldforce':      0.45,   # field push magnitude — softer so slight motion doesn't fling
    'Fielddecay':      0.5,    # medium persistence (force trails ~1s)
    # Force integration + damping (bounds_reflect GLSL POP uniforms).
    # Damping lives HERE now, not on Particle POP (whose Velocity Damping /
    # Initial Drag stay at 0). See bounds_reflect.glsl.
    'Forcescale':      0.008,  # per-cook force gain (dt*gain) into PartVel
    'Velocitydamping': 0.15,   # fraction of velocity removed per cook (water feel)
    'Maxspeed':        8.0,    # hard clamp on |PartVel|
    'Forcedeadzone':   3.0,    # |f| below this = no push (kills rest-drift)
    'Forceref':        32.0,   # |f| mapped to full response — higher = more headroom, proportional
    'Forcegamma':      2.5,    # response curvature (>1 = gentle small / snappy big)
    # Z (depth) scaling
    'Zgain':           0.2,    # subtle depth-to-size
    'Zforceweight':    0.05,   # nearly zero z-force so depth jitter doesn't fling forward/back
    # Velocity-stretched kernel
    'Velstretch':      0.8,
    'Stretchspeedref': 2.0,
    # Curl noise (idle drift + organic bending)
    'Curlgain':        0.05,   # wired to curl_noise amp0 (Noise amplitude)
    'Curlscale':       0.5,    # < particle extent so curl directions vary across cloud
    'Curlspeed':       0.3,    # animates the Simplex-4D 4th axis so curls aren't frozen
    # Life
    'Lifemin':         2.0,
    'Lifemax':         8.0,
    # Bounding box for containment (particle space; x is aspect-correct 16:9).
    'Boundsminx':      0.0,
    'Boundsminy':      0.0,
    'Boundsminz':     -0.15,
    'Boundsmaxx':      1.77778,  # 16/9 — emitters_chop remaps x into this range
    'Boundsmaxy':      1.0,
    'Boundsmaxz':      0.15,
    'Boundsbounce':    0.95,
    'Boundsmargin':    0.005,
    # Ambient particle soup (constant population, advected by the same field).
    'Ambientrate':     6000.0,  # pts/s; steady alive ≈ rate × avg-life
    'Ambientpoints':   240,     # spatial scatter sample count
    # Particle size (drives sphere1 radius inside geo1).
    'Particlesize':    0.006,
    # Age gradient (Embers) + velocity bloom (read by color_attr).
    'Agegradient':     1.0,     # 0=flat, 1=full embers (movement particles)
    'Agefalloff':      1.6,     # brightness fade exponent over life
    'Velbloom':        0.12,    # speed → HDR brightness boost
    'Velref':          0.08,    # movement speed mapped to full hot/bloom (slow births stay dim)
    # Body force field (skeleton parts + drags the soup).
    'Bodypush':        0.04,    # repel strength (soup pushed away from bones)
    'Bodydrag':        0.03,    # advect strength (soup dragged along limb motion)
    'Bodyradius':      0.12,    # bone influence radius (world-y units)
    # Body VIZ (glowing skeleton render — our replacement for MediaPipe circles).
    'Bodyviz':         1,       # on
    'Bodyvizwidth':    0.014,   # bone capsule width
    'Bodyvizglow':     0.35,    # glow intensity (HDR core blooms; keep modest)
    'Bodyvizflow':     0.5,     # energy pulse along the limbs
    'Bodyviztint':     (0.4, 0.8, 1.0),  # cool cyan halo (white core)
    'Soupbright':      1.0,     # steady soup brightness; kept below Bloomthreshold so soup doesn't bloom
    'Soupturb':        0.05,    # drives soup curl drift; saturates the cap below
    'Soupmaxspeed':    0.008,   # hard cap on idle soup speed (the real "calm" knob)
    'Soupcyclespeed':  0.03,    # soup color-ramp cycle speed over time (spatial band sweep)
    'Soupevolve':      0.05,    # soup palette HUE rotation over time (color drifts through spectrum)
    'Soupspeedref':    0.2,     # soup speed mapped to "fast" for the velocity look
    'Soupvelbloom':    2.0,     # fast-soup brightness/bloom boost
    'Soupcolorscale':  0.6,     # spatial frequency of the soup color gradient (position-based)
    'Depthdim':        0.55,    # fake DoF: dim soup toward the back of the box
    # Bloom TOP (post-render glow).
    'Bloomenable':     True,
    'Bloomstrength':   1.0,
    'Bloomthreshold':  1.1,
    # Motion trails (screen-space feedback smear) — now wired before bloom.
    # Feedbackfade = trail length; the Cosmic preset (applied below) sets it.
    'Feedbackenable':  True,
    'Feedbackfade':    0.6,
    'Feedbackzoom':    1.0,
    # Logo attractor + brighten (standby screensaver). Logomode: Off/Standby/Always.
    # Standby fades the logo in when no pose is present, out when a person appears.
    'Logomode':        'Standby',
    'Logoattract':     0.5,    # soup pull up the logo luma gradient (gentle, edge-distributed)
    'Logobright':      2.5,    # extra glow on soup sitting on the logo mask (legible reveal)
    'Logofade':        1.5,    # standby crossfade seconds
    'Logoreach':       180.0,  # blur radius (px) = medium reach → pulls to nearest feature, not centroid
    'Logogradamp':     1.0,    # logo gradient amplification
    'Logotrap':        0.9,    # velocity damping on the mask (soup STICKS → fills the shape)
    'Logovigor':       0.5,    # liveliness inside the shape (0=static decal, 1=churning vessel)
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

# Look page (palette + post-FX) — single source of truth is presets.py.
# Apply the default 'Cosmic' preset so the look pars reset to a known-good
# bundle without duplicating their values here.
try:
    import sys
    if project.folder not in sys.path:
        sys.path.append(project.folder)
    import importlib, presets
    importlib.reload(presets)
    n = presets.apply(comp, 'Cosmic')
    comp.par.Preset = 'Cosmic'
    print(f"Look: 'Cosmic' preset applied ({n} pars).")
except Exception as e:
    print(f"Look preset apply skipped: {e}")

print("reset_velocity_params: done. All existing pars forced to current "
      "defaults. Save a note of your previous tuning if you had custom "
      "values — they're now overwritten.")
