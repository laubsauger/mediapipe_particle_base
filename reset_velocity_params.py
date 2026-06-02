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
    'Speedscale':          0.9,   # emit saturates at moderate motion (was 5.0/1.5) so normal-speed gestures spawn, not just whips
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
    'Burstgain':       8.0,
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
    'Velocitydamping': 0.22,   # fraction of velocity removed per cook (higher = calmer average motion)
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
    'Boundsminz':     -0.4,    # widened Z for real depth/parallax (was -0.15)
    'Boundsmaxx':      1.77778,  # 16/9 — emitters_chop remaps x into this range
    'Boundsmaxy':      1.0,
    'Boundsmaxz':      0.4,
    'Boundsbounce':    0.6,    # rebound inward off walls (was 0.3 → particles stuck/accumulated)
    'Boundsmargin':    0.005,
    # Wall containment: INWARD repel force near each wall (bounds_reflect) so
    # particles turn back before piling into a cheap rectangular "frame".
    'Wallrepel':       0.25,   # inward push strength
    'Wallband':        0.03,   # distance from wall the push ramps over
    # Ambient particle soup (constant population, advected by the same field).
    'Ambientrate':     6000.0,  # pts/s; steady alive ≈ rate × avg-life
    'Ambientpoints':   1250,    # scatter sample count = HARD cap on births/cook (k=min(Ambientrate/60, Ambientpoints)). Keep >= Ambientrate/60 or it throttles the soup.
    # Particle size (drives sphere1 radius inside geo1).
    'Particlesize':    0.006,
    # Age gradient (Embers) + velocity bloom (read by color_attr).
    'Agegradient':     1.0,     # 0=flat, 1=full embers (movement particles)
    'Agefalloff':      1.6,     # brightness fade exponent over life
    'Velbloom':        0.12,    # speed → HDR brightness boost
    'Velref':          0.08,    # movement speed mapped to full hot/bloom (slow births stay dim)
    # Body force field (skeleton parts + drags the soup) — wider + stronger so
    # the displacement is clearly visible around the limbs and the soup parts
    # decisively (was 0.06/0.045/0.12 = too subtle, body felt "slabbed on top").
    'Bodypush':        0.10,    # repel strength (soup pushed away from bones)
    'Bodydrag':        0.07,    # advect strength (soup dragged along limb motion)
    'Bodyradius':      0.18,    # bone influence radius (world-y units)
    # Body VIZ (glowing skeleton render — our replacement for MediaPipe circles).
    'Bodyviz':         1,       # on
    'Bodyvizwidth':    0.012,   # bone capsule width (slimmer = less dominant)
    'Bodyvizglow':     0.22,    # glow intensity — kept LOW so the body sits IN the
                                # soup palette, not above it. Higher = "slabbed-on"
    'Bodyvizflow':     0.4,     # energy pulse along the limbs
    'Bodyviztint':     (0.4, 0.8, 1.0),  # fallback halo (used at Bodyvizblend=0)
    'Bodyvizblend':    1.0,     # blend body color with soup palette (1=full soup, harmonises)
    'Soupbright':      1.0,     # steady soup brightness; kept below Bloomthreshold so soup doesn't bloom
    'Soupturb':        0.05,    # drives soup curl drift; saturates the cap below
    'Soupmaxspeed':    0.008,   # hard cap on idle soup speed (the real "calm" knob)
    'Soupcyclespeed':  0.018,   # soup color-ramp cycle speed — SLOW so colour evolves organically (was 0.03)
    'Soupevolve':      0.025,   # soup palette HUE rotation over time — slow drift (was 0.05)
    'Soupgradrot':     0.04,    # slow direction-rotation of the colour bands (rad/s) — alive without input
    'Soupsetspeed':    0.04,    # palette-SET rotation (sets/s): soup crossfades through the color_attr triad bank (set0=preset triad). 0=stay on preset.
    'Fieldedgefade':   0.02,    # smooth border falloff on the sampled flow field (field_edge TOP) — kills the hard edge that clumped particles at the walls
    # Cosmic-web filaments (background-only — fades inside logo/vessel mask):
    'Clusterscale':    3.5,     # filament frequency
    'Clusterboost':    0.4,     # brightness boost on filament peaks — background, not soup-wide
    'Clustergamma':    4.0,     # filament sharpness (higher = thinner filaments)
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
    # Mask attractor (particle-FX side; consumes `in_mask` + `in_mask_state` from
    # the external mask_controller). All source-switching / standby-fade /
    # cycle-shockwave lives OUTSIDE on mask_controller now.
    'Maskattract':     0.05,   # soft pull — strong values overshoot and form a halo around the shape
    'Maskbright':      2.5,    # extra glow on soup sitting on the mask (legible reveal)
    'Maskreach':       180.0,  # blur radius (px) = medium reach → pulls to nearest feature, not centroid
    'Maskgradamp':     1.0,    # mask gradient amplification
    'Masktrap':        0.9,    # velocity damping on the mask (soup STICKS → fills the shape)
    'Maskvigor':       0.5,    # liveliness inside the shape (0=static decal, 1=churning vessel)
    'Maskcharge':      0.9,    # vessel feel — particles in the mask region get extra glow + hue shift
    'Maskpush':        1.4,    # swap-shockwave outward push (toned down — was too strong/silly)
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


# ---------------------------------------------------------------------------
# Audio page (ARE reactivity) — Punchy defaults
# ---------------------------------------------------------------------------
AUDIO = {
    'Audioenable':       1,      # master ON/OFF toggle (off → base look + color rotation only)
    'Audioreact':        1.0,
    'Audiokick':         0.9,
    'Audiobass':         0.7,
    'Audiobreath':       0.6,
    'Audiohat':          0.7,
    'Audiosnare':        0.25,   # subtle backbeat hue (was 0.6 — too flashy)
    'Audiobuild':        0.8,
    'Audiospectrum':     0.0,    # spectrum colour bands OFF (read as an ugly colored vignette)
    'Audiodrop':         0.4,
    'Audiosoupdir':      1.0,
    'Audioblur':         0.5,    # organic on-beat defocus blur (beat_blur TOP)
    'Audiobeat':         0.6,    # kick → visible radial push pulse (main "alive on beat" lever)
    'Audiointerval':     1,      # surge every Nth kick (raise for slow/atmospheric)
    'Audioduration':     1.4,    # transform duration scale (>1 = longer, more evolving)
    'Audiomidswirl':     0.1,    # mid-peak → rotational swirl burst (2nd organic disturbance)
    # Logo-vessel physics (standby) — ARE drives the trapped material's physics
    'Audiopressure':     0.8,    # low → boundary pressure (Maskattract)
    'Audiocirculation':  0.7,    # mid → internal circulation (Maskvigor)
    'Audiosurface':      0.4,    # high → surface fizz at the silhouette edge
    'Audioresonance':    0.9,    # reduced-FFT → segmented-logo regional resonance
    'Audiokickrelease':  0.16,
    'Audiohatrelease':   0.07,
    'Audiobreathsmooth': 0.10,
    'Audiobuildattack':  0.45,
}

_apply(SENSING, 'Sensing')
_apply(RENDERER, 'Renderer')
_apply(AUDIO, 'Audio')

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
