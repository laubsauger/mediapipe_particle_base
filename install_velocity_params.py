"""
install_velocity_params.py
==========================

One-shot installer for the `velocity_controller` Base COMP's custom parameter
page. Run this once, from a Text DAT *inside* the Base COMP:

    Right-click DAT -> Run Script

Idempotent: re-running it won't duplicate pars or reset values you've tuned.
After it finishes, you can delete this DAT — the pars live on the COMP itself.

Every parameter the Script CHOP callback reads is created here. Grouped onto
two pages ('Sensing', 'Renderer') so the sensing-side tuning stays clean
even if the reference renderer TOX is swapped out.
"""

comp = parent()


def _page(name):
    for p in comp.customPages:
        if p.name == name:
            return p
    return comp.appendCustomPage(name)


def _has(name):
    return getattr(comp.par, name, None) is not None


def add_float(page, name, label, default, rmin, rmax,
              clamp_min=True, clamp_max=True):
    if _has(name):
        return
    pg = page.appendFloat(name, label=label)
    p = pg[0]
    p.default = default
    p.val = default
    p.normMin = rmin
    p.normMax = rmax
    p.clampMin = clamp_min
    p.clampMax = clamp_max


def add_str(page, name, label, default):
    if _has(name):
        return
    pg = page.appendStr(name, label=label)
    p = pg[0]
    p.default = default
    p.val = default


def add_toggle(page, name, label, default):
    if _has(name):
        return
    pg = page.appendToggle(name, label=label)
    p = pg[0]
    p.default = 1 if default else 0
    p.val = p.default


# ---------------------------------------------------------------------------
# Page 1: Sensing  —  everything velocity_script_chop.py reads.
# ---------------------------------------------------------------------------
sensing = _page('Sensing')

# Landmarks list. Comma or space separated. Override per experiment without
# editing code.
add_str(sensing, 'Landmarks', 'Landmarks',
        'left_wrist right_wrist left_ankle right_ankle nose')

add_float(sensing, 'Visibilitythreshold', 'Visibility Threshold (gate)',
          0.5, 0.0, 1.0)
# Trustthreshold >= Visibilitythreshold gives a hysteresis band. Frames with
# confidence between the two are "visible but not trusted": emitter stays on
# but position is pinned to last_good. Above trust: fully commit the sample.
# Below gate: invisible. Prevents the "slide to garbage" during MediaPipe's
# confidence ramp-down when a limb leaves the frame.
add_float(sensing, 'Trustthreshold', 'Trust Threshold (commit last-good)',
          0.75, 0.0, 1.0)

# Smoothing time constants (seconds). Shorter = snappier, noisier.
add_float(sensing, 'Velocitysmooth', 'Velocity Smooth (s)',
          0.08, 0.0, 0.5, clamp_max=False)
add_float(sensing, 'Accelsmooth', 'Accel Smooth (s)',
          0.05, 0.0, 0.5, clamp_max=False)

# Speed normalisation — raw units/s divided by this hits emit=1.
# 0..1 space; 2.5 means "moving all the way across the frame in 0.4s = full emit".
add_float(sensing, 'Speedscale', 'Speed Scale (1/s -> 1.0)',
          2.5, 0.1, 10.0, clamp_max=False)

# Burst detection (on |a|).
add_float(sensing, 'Accelthreshold', 'Accel Threshold (1/s^2)',
          8.0, 0.0, 50.0, clamp_max=False)
add_float(sensing, 'Accelscale', 'Accel Scale (1/s^2 -> 1.0)',
          40.0, 1.0, 200.0, clamp_max=False)
add_float(sensing, 'Burstdecay', 'Burst Decay (s)',
          0.35, 0.0, 2.0, clamp_max=False)

# Teleport rejection. If raw position jumps more than this in UV space from
# the last trusted sample, treat as lost tracking: hold last-good position
# and decay envelopes (same as visible=False). 0 disables the check.
add_float(sensing, 'Maxjump', 'Max Jump (UV/frame, 0=off)',
          0.30, 0.0, 1.0)

# Settle grace: number of frames after re-acquisition (any dropout ending)
# during which the Maxjump check is skipped. Lets MediaPipe lock onto the
# real joint position over the first few trusted frames without our
# teleport rejection snapping the blob to the re-entry edge. 0 disables.
add_float(sensing, 'Settleframes', 'Settle Frames (after dropout)',
          5, 0, 30)

# How much the z (depth) velocity contributes to the 3D speed magnitude
# used for emit rate and burst detection. 1.0 = full 3D; 0.0 = z motion
# doesn't trigger emit/burst (but vz is still emitted as an output channel
# for the renderer to use). Default 0.35: depth motion registers but is
# less spiky than side-to-side motion. MediaPipe's z is noisier than x/y,
# so this also keeps burst detection robust against depth jitter.
add_float(sensing, 'Zspeedweight', 'Z Speed Weight (emit/burst sensitivity)',
          0.35, 0.0, 1.0)

# Downstream single-knob blend (Lag CHOP references this).
add_float(sensing, 'Blendtime', 'Blend Time (s)',
          0.08, 0.0, 1.0, clamp_max=False)


# ---------------------------------------------------------------------------
# Page 2: Renderer  —  POP network parameters. Referenced from the sibling
# particle_renderer TOX (Source POP rate, Force POP gain, Feedback TOP fade,
# etc. pick these up via parent().par.*).
# ---------------------------------------------------------------------------
render = _page('Renderer')

# Overall spawn budget (particles/sec when total_motion + total_burst = 1).
add_float(render, 'Spawnrate', 'Base Spawn Rate (pts/s)',
          5000.0, 0.0, 50000.0, clamp_max=False)

# Gain on the burst channel when mixing into the spawn-weight CHOP.
add_float(render, 'Burstgain', 'Burst Spawn Gain',
          6.0, 0.0, 20.0, clamp_max=False)

# --- Wavefront emission ---------------------------------------------------
# Number of sub-emitter points generated per landmark. They're placed along
# a line perpendicular to the limb's xy velocity so that particles spawn
# across a "wall" in the direction the limb is cutting through the air
# rather than all from the same point. Weight is divided evenly across
# them so total particles/sec per limb is independent of this count.
# 1 = classic single-point emission (old behaviour).
add_float(render, 'Spawncount', 'Spawn Sub-emitters per Limb',
          12, 1, 40, clamp_max=False)

# Max width of the wavefront line in UV units, reached when speed >=
# Spawnspreadref. At rest the spread collapses to 0 (single point).
add_float(render, 'Spawnspread', 'Wavefront Width at Full Speed (UV)',
          0.08, 0.0, 0.3)

# Speed (UV/s) at which the wavefront reaches its full Spawnspread width.
# Below it, width scales linearly with speed.
add_float(render, 'Spawnspreadref', 'Wavefront Full-width Speed (UV/s)',
          2.0, 0.1, 10.0, clamp_max=False)

# Multiplier on the limb's velocity when writing it to each particle's
# StartPartvel at birth. 1.0 = particles launch at full limb speed
# (flies off-screen in <1s on fast whips); 0.3 = gentle launch, velocity
# field and other forces take over from there. Lower values produce a
# "wavefront that lingers" look; higher values make limbs fling particles
# further in the motion direction.
add_float(render, 'Spawnvelscale', 'Spawn Velocity Scale',
          0.3, 0.0, 1.5, clamp_max=False)

# Angular fan on StartPartvel — tilts the edge sub-emitters' initial
# velocity outward along the perpendicular direction so the wavefront
# expands as it travels (cone instead of parallel wall). Center particle
# (t=0) stays parallel to limb motion; edge particles (t=±0.5) get a
# perpendicular kick scaled by this * limb_speed. 0 = parallel wavefront,
# 0.25 = mild curve, 0.5 = pronounced cone.
add_float(render, 'Spawnvelfan', 'Spawn Velocity Fan (0=parallel, 1=cone)',
          0.25, 0.0, 1.5, clamp_max=False)

# Velocity field splatter — base radius of each emitter's gaussian kernel
# (in 0..1 UV space of the velocity-field TOP). Smaller = tighter blob per
# limb, less dominant force field coverage; good if you want the particle
# cloud to feel like it emanates from a point rather than a wide zone.
# With default 0.05, 3-sigma spread is ~0.15 UV (~15% of the frame) —
# plenty of reach without dominating the scene.
add_float(render, 'Fieldradius', 'Field Splat Radius',
          0.05, 0.01, 0.5)
# Multiplier on emitted (vx,vy,vz) when writing into the field (tune this
# for "how hard do limbs push particles").
add_float(render, 'Fieldforce', 'Field Force Gain',
          1.5, 0.0, 10.0, clamp_max=False)
# Persistence of the velocity field between frames (0 = instantaneous,
# 1 = never fades). Applied externally via a Level TOP in the persistence
# feedback chain. Smaller values = more responsive / less trail buildup —
# a moving limb won't leave a field ghost 10 frames behind it. Raise
# toward 0.7 for "smoke trail" visuals; keep low for crisp reactive feel.
add_float(render, 'Fielddecay', 'Field Decay (0=snap, 1=hold)',
          0.30, 0.0, 0.99)
# Z → splat size. Negative z (limb toward camera) scales splat radius up;
# positive z scales it down. The shader clamps the result to [0.25, 1.8]
# so very-close limbs don't blow up the kernel. 0 disables depth scaling.
add_float(render, 'Zgain', 'Z Size Gain (depth -> radius)',
          0.35, 0.0, 3.0, clamp_max=False)
# Anisotropic kernel stretch along velocity direction. 0 = round splat;
# larger = elongated cone of force in the direction of motion, so
# particles ahead of a fast-moving limb get shoved further.
add_float(render, 'Velstretch', 'Velocity Stretch (0=round)',
          0.8, 0.0, 3.0, clamp_max=False)
# Reference speed (UV/s) at which full Velstretch is applied. Below this,
# stretch scales linearly with speed so gentle motion stays round.
add_float(render, 'Stretchspeedref', 'Stretch Speed Reference (UV/s)',
          2.0, 0.1, 10.0, clamp_max=False)

# Idle curl-noise drift so particles don't freeze when performer is still.
add_float(render, 'Curlgain', 'Curl Noise Gain',
          0.15, 0.0, 2.0, clamp_max=False)
add_float(render, 'Curlscale', 'Curl Noise Scale',
          3.0, 0.1, 20.0, clamp_max=False)

# Particle lifetime (seconds).
add_float(render, 'Lifemin', 'Life Min (s)',
          1.2, 0.1, 20.0, clamp_max=False)
add_float(render, 'Lifemax', 'Life Max (s)',
          3.0, 0.1, 20.0, clamp_max=False)

# Screen-space feedback TOP (for the smear look on top of the POP render).
add_toggle(render, 'Feedbackenable', 'Screen-Space Feedback', True)
add_float(render, 'Feedbackfade', 'Feedback Fade',
          0.92, 0.0, 0.999)
add_float(render, 'Feedbackzoom', 'Feedback Zoom',
          1.003, 0.95, 1.05)


print("velocity_controller: Sensing + Renderer pages installed "
      "({} params total).".format(
          len([pr for pr in comp.customPars
               if pr.page.name in ('Sensing', 'Renderer')])))
