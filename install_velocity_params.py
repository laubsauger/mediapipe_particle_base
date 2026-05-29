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


def add_rgb(page, name, label, default):
    """RGB color par (3 components). default = (r, g, b)."""
    if _has(name):
        return
    pg = page.appendRGB(name, label=label)
    for p, v in zip(pg, default):
        p.default = v
        p.val = v


def add_menu(page, name, label, names, default):
    if _has(name):
        return
    pg = page.appendMenu(name, label=label)
    p = pg[0]
    p.menuNames = names
    p.menuLabels = names
    p.default = default
    p.val = default


def add_pulse(page, name, label):
    if _has(name):
        return
    page.appendPulse(name, label=label)


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
# 0..1 space; 5.0 means "moving all the way across the frame in 0.2s = full emit".
add_float(sensing, 'Speedscale', 'Speed Scale (1/s -> 1.0)',
          5.0, 0.1, 10.0, clamp_max=False)

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
          1, 0, 30)

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
# Page 2: Renderer  —  in-COMP POP network parameters. Read as
# parent().par.* by the emitter Script ops, the velocity_field GLSL TOP
# uniforms, and the force-chain GLSL POPs (p_to_uv, bounds_reflect). The
# force-integration block (Forcescale … Forcegamma) feeds bounds_reflect,
# which is where per-cook force integration AND velocity damping now live
# (NOT on Particle POP — its Velocity Damping / Initial Drag stay at 0).
# ---------------------------------------------------------------------------
render = _page('Renderer')

# Overall spawn budget (particles/sec when total_motion + total_burst = 1).
# NOTE: currently informational — Particle POP emits via the per-point `w`
# birth attribute, not a global rate. Reserved for future total-rate scaling.
add_float(render, 'Spawnrate', 'Base Spawn Rate (pts/s)',
          15000.0, 0.0, 50000.0, clamp_max=False)

# Gain on the burst channel when mixing into the spawn-weight CHOP.
add_float(render, 'Burstgain', 'Burst Spawn Gain',
          12.0, 0.0, 20.0, clamp_max=False)

# --- Wavefront emission ---------------------------------------------------
# Number of sub-emitter points generated per landmark. They're placed along
# a line perpendicular to the limb's xy velocity so that particles spawn
# across a "wall" in the direction the limb is cutting through the air
# rather than all from the same point. Weight is divided evenly across
# them so total particles/sec per limb is independent of this count.
# 1 = classic single-point emission (old behaviour).
add_float(render, 'Spawncount', 'Spawn Sub-emitters per Limb',
          18, 1, 40, clamp_max=False)

# Maximum along-velocity extent of the emission region at full speed.
# Sub-emitters scatter pseudo-randomly inside a velocity-aligned region;
# this is the half-width of that region in the direction of motion,
# producing a "streak" shape when the limb is moving fast.
add_float(render, 'Spawnspread', 'Wavefront Width at Full Speed (UV)',
          0.14, 0.0, 0.3)

# Speed (UV/s) at which the region reaches its full Spawnspread extent.
# Below it, size scales linearly with speed down to Spawnspreadmin.
# Default 0.8 engages the full size at normal hand-waving speed;
# raise to 2.0 to require violent whips; lower to 0.3 so any motion
# reaches full size.
add_float(render, 'Spawnspreadref', 'Wavefront Full-width Speed (UV/s)',
          0.8, 0.1, 10.0, clamp_max=False)

# Minimum extent of the emission region in both axes, at rest.
# Gives the "lump" shape when the limb is stationary (matches the
# flow-field shader's gaussian-at-rest kernel). Too small = emission
# from a near-point; too large = always-visible cloud around every
# limb even when still.
add_float(render, 'Spawnspreadmin', 'Spawn Region Min (UV at rest)',
          0.05, 0.0, 0.1)

# Ratio of perpendicular to along-velocity extent at speed. 0 = pure
# along-velocity line (all sub-emitters on the motion axis), 1 = square
# region (as wide as it is long), default 0.3 = clearly elongated streak
# with some width. This is what gives the emission shape its "streak vs
# lump" feel during fast motion.
add_float(render, 'Spawnperpratio', 'Spawn Perp/Along Ratio',
          0.6, 0.0, 1.0)

# Multiplier on the limb's velocity when writing it to each particle's
# StartPartvel at birth. 1.0 = particles launch at full limb speed
# (flies off-screen in <1s on fast whips); 0.25 default = moderate launch,
# flowfield and curl noise do most of the work afterward. Lower values
# produce a "wavefront that lingers" look; higher values make limbs
# fling particles further in the motion direction.
add_float(render, 'Spawnvelscale', 'Spawn Velocity Scale',
          0.12, 0.0, 1.5, clamp_max=False)

# Angular fan on StartPartvel — tilts the edge sub-emitters' initial
# velocity outward along the perpendicular direction so the wavefront
# expands as it travels (cone instead of parallel wall). Center particle
# (t=0) stays parallel to limb motion; edge particles (t=±0.5) get a
# perpendicular kick scaled by this * limb_speed. 0 = parallel wavefront,
# 0.5 = ~27° edge tilt (visible cone), 1.0 = ~45° (strong fan),
# 1.5+ = explosive burst-outward.
add_float(render, 'Spawnvelfan', 'Spawn Velocity Fan (0=parallel, 1=cone)',
          1.2, 0.0, 2.0, clamp_max=False)
# Speed-independent angular jitter (radians) on each particle's launch
# direction. The fan scales with limb speed (so slight motion = tight stream);
# this spreads launch directions even at slow motion → soft shedding spray.
# 0 = directional, 0.45 ≈ ±26° spread, higher = wider/fuzzier.
add_float(render, 'Spawnangjitter', 'Spawn Angle Jitter (rad)',
          0.45, 0.0, 1.57, clamp_max=False)

# Velocity field splatter — base radius of each emitter's gaussian kernel
# (in 0..1 UV space of the velocity-field TOP). Smaller = tighter blob per
# limb, less dominant force field coverage; good if you want the particle
# cloud to feel like it emanates from a point rather than a wide zone.
# With default 0.05, 3-sigma spread is ~0.15 UV (~15% of the frame) —
# plenty of reach without dominating the scene.
add_float(render, 'Fieldradius', 'Field Splat Radius',
          0.05, 0.01, 0.5)
# Multiplier on emitted (vx,vy,vz) written into the velocity field.
# This sets the |force| that bounds_reflect's nonlinear response curve
# (Forcedeadzone / Forceref / Forcegamma) reshapes before integrating into
# PartVel. Terminal velocity is governed by that curve + Velocitydamping
# (the COMP par read by bounds_reflect), NOT by any Particle POP damping —
# Particle POP's own Velocity Damping / Initial Drag are left at 0. Pair
# higher Fieldforce with higher Velocitydamping to keep particles contained.
add_float(render, 'Fieldforce', 'Field Force Gain',
          0.45, 0.0, 10.0, clamp_max=False)
# Persistence of the velocity field between frames (0 = instantaneous,
# 1 = never fades). Applied externally via a Level TOP in the persistence
# feedback chain. Smaller values = more responsive / less trail buildup —
# a moving limb won't leave a field ghost 10 frames behind it. Raise
# toward 0.7 for "smoke trail" visuals; keep low for crisp reactive feel.
add_float(render, 'Fielddecay', 'Field Decay (0=snap, 1=hold)',
          0.5, 0.0, 0.99)
# Z → splat size. Negative z (limb toward camera) scales splat radius up;
# positive z scales it down. The shader clamps the result to [0.25, 1.8]
# so very-close limbs don't blow up the kernel. 0 disables depth scaling.
# Default 0.2 = subtle depth effect; crank to 0.5+ if you want near/far
# limbs to have dramatically different splat sizes.
add_float(render, 'Zgain', 'Z Size Gain (depth -> radius)',
          0.2, 0.0, 3.0, clamp_max=False)

# Z → force weight. Scales vz BEFORE it goes into the velocity-field
# texture, so MediaPipe's noisy depth estimation doesn't produce a
# constant z-drift on particles when the performer is still. 1.0 = full
# 3D force (particles feel real forward/back pushes), 0 = field is
# purely 2D (particles never move in z from field forces, though they
# can still spawn with vz from StartPartvel). Default 0.3 = strong
# damping of z-noise, just enough real z-motion to register.
# This is SEPARATE from Zspeedweight (which controls sensing-side
# emit/burst sensitivity to z-motion). Lower this one if particles
# drift forward/back at rest; lower Zspeedweight if leans/depth-motion
# cause too many particles to spawn.
add_float(render, 'Zforceweight', 'Z Force Weight (vz -> field)',
          0.05, 0.0, 1.0)
# Anisotropic kernel stretch along velocity direction. 0 = round splat;
# larger = elongated cone of force in the direction of motion, so
# particles ahead of a fast-moving limb get shoved further.
add_float(render, 'Velstretch', 'Velocity Stretch (0=round)',
          0.8, 0.0, 3.0, clamp_max=False)
# Reference speed (UV/s) at which full Velstretch is applied. Below this,
# stretch scales linearly with speed so gentle motion stays round.
add_float(render, 'Stretchspeedref', 'Stretch Speed Reference (UV/s)',
          2.0, 0.1, 10.0, clamp_max=False)

# --- Force integration (bounds_reflect GLSL POP) --------------------------
# bounds_reflect folds the sampled field force into PartVel each cook, then
# damps + reflects. These six knobs are its uniforms (bound on the GLSL POP
# as parent().par.*). Particle POP does NOT auto-apply PartForce, and its
# own Velocity Damping / Initial Drag are left at 0 — all damping is here.
#
# Per-cook force gain: PartVel += curved_force * Forcescale. Treat as dt*gain.
# Small = gentle push; the curve below shapes magnitude before this scales it.
add_float(render, 'Forcescale', 'Force Scale (per-cook)',
          0.008, 0.0, 0.1, clamp_max=False)
# Fraction of velocity REMOVED per cook (PartVel *= 1 - Velocitydamping).
# 0 = vacuum (coast forever), 0.15 default = light viscous drag, → 1 = stop.
# THIS is the water-feel knob now (moved off Particle POP into the GLSL POP).
add_float(render, 'Velocitydamping', 'Velocity Damping (per-cook)',
          0.15, 0.0, 1.0)
# Hard clamp on |PartVel| so a limb staring at a particle can't compound
# force into runaway velocity before the reflect step runs.
add_float(render, 'Maxspeed', 'Max Speed',
          8.0, 0.0, 50.0, clamp_max=False)
# Nonlinear force response: |f| below Forcedeadzone gets NO push (silences
# the slow drift from field persistence at rest). Forceref = |f| mapped to
# full magnitude. Forcegamma curves the response (1=linear, >1=gentler at
# small motion, snappier at big). t = pow(clamp((|f|-dead)/(ref-dead),0,1), gamma).
add_float(render, 'Forcedeadzone', 'Force Deadzone',
          3.0, 0.0, 100.0, clamp_max=False)
add_float(render, 'Forceref', 'Force Reference',
          32.0, 0.0, 200.0, clamp_max=False)
add_float(render, 'Forcegamma', 'Force Gamma',
          2.5, 0.1, 5.0, clamp_max=False)

# Curl-noise drift — keeps particles moving when the performer is still and
# bends trails organically. Bound to the curl_noise Noise POP's amplitude
# (amp0), so this is the live "how curly" knob. Drop toward 0 for crisp
# directional motion (kills the swirly trails), raise for turbulent feel.
# Pair with Curlspeed (below) so the curls FLOW rather than sit frozen.
add_float(render, 'Curlgain', 'Curl Noise Gain',
          0.05, 0.0, 2.0, clamp_max=False)
# Period of the noise field. CRITICAL for avoiding directional drift.
# With Period larger than the particle cloud's spatial extent, every
# particle samples essentially the same curl vector and feels a
# consistent push in that direction — which accumulates over time and
# can't be damped away directionally (damping is magnitude-only).
# Particle space is ~1 UV, so Period must be SMALLER than 1 to give
# particles varied curl directions that average to zero across the
# cloud. 0.5 default = particles sample ~2 cells across the volume;
# lower for micro-turbulence, higher tends toward biased drift.
add_float(render, 'Curlscale', 'Curl Noise Scale',
          0.5, 0.05, 20.0, clamp_max=False)
# Curl field animation speed. The Noise POP is Simplex 4D; its 4th axis
# (Translate 4D) is driven by absTime.seconds * Curlspeed so the curl field
# EVOLVES over time instead of being frozen. 0 = static field (particles trace
# the same fixed streamlines forever — reads as "static noise curls"); raise
# for livelier, non-repeating drift. ~0.3 is a gentle organic flow.
add_float(render, 'Curlspeed', 'Curl Noise Animation Speed',
          0.3, 0.0, 3.0, clamp_max=False)

# Particle lifetime (seconds). Shorter = particles die before they can
# drift off-screen, keeps the visual contained to where the limbs are.
# Raise if you want long persistent trails.
add_float(render, 'Lifemin', 'Life Min (s)',
          2.0, 0.1, 20.0, clamp_max=False)
add_float(render, 'Lifemax', 'Life Max (s)',
          8.0, 0.1, 20.0, clamp_max=False)

# Bounding box for particle containment, in particle space. x is
# aspect-correct: emitters_chop remaps MediaPipe x [0,1] into [0, 16/9]
# so the wider 16:9 frame fills, hence Boundsmaxx defaults to 1.77778.
# y stays [0,1]; z is a thin slab (±0.15) because MediaPipe depth is noisy.
# bounds_reflect GLSL POP uses these to clamp P and reflect PartVel at walls.
add_float(render, 'Boundsminx', 'Bounds Min X', 0.0, -1.0, 1.0, clamp_max=False, clamp_min=False)
add_float(render, 'Boundsminy', 'Bounds Min Y', 0.0, -1.0, 1.0, clamp_max=False, clamp_min=False)
add_float(render, 'Boundsminz', 'Bounds Min Z', -0.15, -2.0, 2.0, clamp_max=False, clamp_min=False)
add_float(render, 'Boundsmaxx', 'Bounds Max X', 1.77778, -1.0, 2.0, clamp_max=False, clamp_min=False)
add_float(render, 'Boundsmaxy', 'Bounds Max Y', 1.0, -1.0, 2.0, clamp_max=False, clamp_min=False)
add_float(render, 'Boundsmaxz', 'Bounds Max Z', 0.15, -2.0, 2.0, clamp_max=False, clamp_min=False)
# Restitution — 0 makes particles "stick" at walls (dead stop), 1 is a
# perfectly elastic bounce. 0.3–0.6 feels like water against a pool wall.
add_float(render, 'Boundsbounce', 'Bounds Bounce (0=stop, 1=elastic)',
          0.95, 0.0, 1.0)
# Small inset so particles visually clamp just inside the wall instead of
# clipping it. 0 = hard clamp exactly at the wall.
add_float(render, 'Boundsmargin', 'Bounds Margin (inset)', 0.005, 0.0, 0.1)

# --- Ambient particle soup ------------------------------------------------
# A constant population of particles scattered through the whole bounds
# volume, birthed by ambient_chop_script and merged into particle1 alongside
# the movement emitters. Advected by the same force chain, so a limb sweeping
# through DISPLACES the soup. Steady-state alive ≈ Ambientrate × avg-life.
add_float(render, 'Ambientrate', 'Ambient Soup Rate (pts/s)',
          6000.0, 0.0, 20000.0, clamp_max=False)
# Spatial sample count: how many scatter points the soup picks from each cook.
# Only `Ambientrate/fps` of them actually birth per cook (chosen at random),
# so this is about spatial coverage, not rate. Keep ≥ Ambientrate/fps.
add_float(render, 'Ambientpoints', 'Ambient Soup Scatter Points',
          240, 1, 2000, clamp_max=False)

# --- Particle size --------------------------------------------------------
# Uniform instance scale on geo1 (multiplies the sphere1 geometry). Smaller =
# finer, more numerous-looking soup. Particle COUNT is driven by spawn/ambient
# rates + Max Particles, not this.
add_float(render, 'Particlesize', 'Particle Size (instance scale)',
          0.004, 0.0005, 0.05, clamp_max=False)

# --- Age gradient (Embers) + velocity bloom -------------------------------
# color_attr ramps each particle from white-hot at birth through warm → red →
# dark ember → black over its life (age normalised by Lifemax). 0 = flat (no
# age tint), 1 = full embers.
add_float(render, 'Agegradient', 'Age Embers Strength (0=flat, 1=full)',
          1.0, 0.0, 1.0)
# Brightness falloff exponent over life. 1 = linear fade, >1 = stays bright
# longer then drops fast, <1 = dims quickly then lingers dark.
add_float(render, 'Agefalloff', 'Age Brightness Falloff',
          1.6, 0.2, 5.0, clamp_max=False)
# Velocity → HDR brightness boost. Fast particles emit > 1.0 so the Bloom TOP
# (threshold ~0.85) blooms them. 0 = no speed glow.
add_float(render, 'Velbloom', 'Velocity Bloom Boost',
          0.12, 0.0, 1.0, clamp_max=False)
# Movement speed (box-units/cook) mapped to "full" intensity for the velocity
# look: the white-hot ember birth flash + accent are gated by speed/Velref so
# SLOW emission stays dim/colored instead of blowing out to white. Movement
# PartVel is small (~0.01..0.13), so the ref is small too. Lower = more easily
# hot (everything blooms); higher = only fast swipes flash.
add_float(render, 'Velref', 'Velocity Reference (hot at this speed)',
          0.08, 0.005, 0.5, clamp_max=False)
# Body force field — the performer's skeleton parts the soup (push, away from
# bones) and drags it along limb motion (drag). Driven by body_tex → body_field
# → body_force lookup → bounds_reflect. Per-joint visibility gates each bone.
add_float(render, 'Bodypush', 'Body Push (repel soup)',
          0.04, 0.0, 0.2, clamp_max=False)
add_float(render, 'Bodydrag', 'Body Drag (advect soup along motion)',
          0.03, 0.0, 0.2, clamp_max=False)
add_float(render, 'Bodyradius', 'Body Influence Radius (bone thickness)',
          0.12, 0.01, 0.5, clamp_max=False)
# Steady brightness multiplier for the ambient soup (Lid>=5). The soup is
# exempt from the Embers decay-to-black so it persists as a thick cloud; this
# scales how visible it is. Keep below ~ the bloom threshold so the calm soup
# doesn't bloom (bloom is for fast movement).
# Kept below Bloomthreshold so the calm soup does NOT bloom (only movement /
# HDR embers do). Soup palette peaks ~0.86, so 1.0 keeps max ~0.86 < threshold.
add_float(render, 'Soupbright', 'Soup Brightness',
          0.65, 0.0, 5.0, clamp_max=False)
# Base turbulence: gentle curl drift applied DIRECTLY to soup particles in
# bounds_reflect (bypassing the movement force-curve, which would crush it).
# This is the idle swirl when no pose is present. Keep low for a calm soup;
# the flow field shoves it harder on top when a limb passes through.
# Low by default — terminal drift ≈ |curl|·Soupturb / Velocitydamping, so even
# small values drift particles across the screen over their multi-second life.
# 0.015 ≈ a gentle idle breeze; raise for livelier, 0 = fully static soup.
# Drives soup drift via curl; set high enough to saturate the Soupmaxspeed cap,
# so the cap below is the real "how fast does the soup drift" control.
add_float(render, 'Soupturb', 'Soup Turbulence (idle swirl)',
          0.05, 0.0, 1.0, clamp_max=False)
# HARD cap on idle soup speed (PartVel units; spatial drift = this × Particle
# POP Speed). Guarantees the soup stays calm no matter what residual force it
# picks up. 0.012 ≈ a slow gentle drift. Raise for livelier soup.
add_float(render, 'Soupmaxspeed', 'Soup Max Speed (calm cap)',
          0.008, 0.0, 1.0, clamp_max=False)
# Soup color-cycle speed: the soup samples the soup_ramp TOP at a phase that
# advances with time (offset per-particle), so the population drifts through
# the ramp. 0 = static palette spread; ~0.03 = full cycle every ~30s.
add_float(render, 'Soupcyclespeed', 'Soup Color Cycle Speed',
          0.03, 0.0, 1.0, clamp_max=False)
# Soup velocity look: soup speed at which it reads as "fast". Below it, slow;
# above, it hits full velocity-brightness. Match to the turbulence speed range.
add_float(render, 'Soupspeedref', 'Soup Speed Reference (fast=ref)',
          0.2, 0.01, 2.0, clamp_max=False)
# Fast-soup brightness/bloom boost: how much brighter fast soup gets vs slow,
# so velocity reads (and the fastest soup can cross the bloom threshold).
add_float(render, 'Soupvelbloom', 'Soup Velocity Bloom',
          2.0, 0.0, 6.0, clamp_max=False)
# Spatial frequency of the soup color gradient: how many color bands span the
# box. Low = one broad gradient swept across the whole field (smooth, painterly);
# higher = more, tighter bands. Color comes from particle POSITION (not per-
# particle), so the soup reads as gradients sweeping, not noise.
add_float(render, 'Soupcolorscale', 'Soup Color Gradient Scale',
          0.6, 0.0, 4.0, clamp_max=False)
# Fake depth-of-field: how much to dim soup particles toward the back of the
# box (−z). 0 = flat (all equal), 1 = back fully dark. Adds depth so the soup
# isn't a flat uniform ball-mess.
add_float(render, 'Depthdim', 'Depth Dim (back particles)',
          0.55, 0.0, 1.0)

# --- Soup structure: clustering + two-layer flow --------------------------
# Clustering: bias soup births toward a slow-drifting noise field so density
# clumps instead of being uniform. Scale = clump size, Amt 0=even / 1=strong.
add_float(render, 'Soupclumpscale', 'Soup Clump Scale', 2.0, 0.1, 12.0, clamp_max=False)
add_float(render, 'Soupclumpamt', 'Soup Clump Amount', 0.6, 0.0, 1.0)
# Second curl layer (curl_noise2): a large, slow broad swirl. Each soup
# particle follows layer A (curl_noise) or B (curl_noise2) by a PartId hash, so
# the two flows interleave. Souplayermix = fraction on layer B.
add_float(render, 'Soupturb2', 'Soup Turbulence 2 (broad layer)', 0.05, 0.0, 1.0, clamp_max=False)
add_float(render, 'Curlscale2', 'Curl Scale 2 (broad period)', 2.0, 0.05, 20.0, clamp_max=False)
add_float(render, 'Curlspeed2', 'Curl Speed 2', 0.12, 0.0, 3.0, clamp_max=False)
add_float(render, 'Souplayermix', 'Soup Layer Mix (B fraction)', 0.5, 0.0, 1.0)

# --- Bloom TOP (post-render) ----------------------------------------------
# bloom1 Bloom TOP sits between render1 and out2. render1 outputs 16-bit float
# so HDR (young/fast) particles survive > 1.0 and bloom.
add_toggle(render, 'Bloomenable', 'Bloom Enable', True)
add_float(render, 'Bloomstrength', 'Bloom Strength',
          1.0, 0.0, 4.0, clamp_max=False)
add_float(render, 'Bloomthreshold', 'Bloom Threshold (luminance)',
          1.1, 0.0, 4.0, clamp_max=False)

# Motion-trail (screen-space feedback smear) pars — NOW WIRED: render1 feeds a
# Feedback→Level(×fade)→Transform(zoom)→Composite(Add) loop before bloom, so
# moving particles leave light trails. Feedbackfade = trail length (→1 longer).
add_toggle(render, 'Feedbackenable', 'Motion Trails', True)
add_float(render, 'Feedbackfade', 'Trail Length (feedback fade)',
          0.85, 0.0, 0.999)
add_float(render, 'Feedbackzoom', 'Trail Zoom', 1.0, 0.95, 1.05)


# ---------------------------------------------------------------------------
# Page 3: Look  —  post-FX stack (streaks, color grade, lens finish) + the
# preset/macro controls. These drive the GLSL TOP uniforms (grade.frag,
# lens_finish.frag) and the native streak chain.
# ---------------------------------------------------------------------------
look = _page('Look')

# --- Preset selector (applied by apply_preset parexec via presets.py) ------
add_menu(look, 'Preset', 'Preset', ['Cosmic', 'Ember', 'Ink', 'Neon'], 'Cosmic')
add_pulse(look, 'Applypreset', 'Apply Preset')

# --- Logo attractor (passive-state hero; samples null_logo) ----------------
# Off / Always / Standby (fades in when no pose, out when a person appears).
add_menu(look, 'Logomode', 'Logo Mode', ['Off', 'Standby', 'Always'], 'Standby')
add_float(look, 'Logoattract', 'Logo Attract (pull into shape)', 1.5, 0.0, 4.0, clamp_max=False)
add_float(look, 'Logobright', 'Logo Brightness (glow on shape)', 2.5, 0.0, 6.0, clamp_max=False)
add_float(look, 'Logofade', 'Logo Fade (standby crossfade s)', 1.5, 0.05, 10.0, clamp_max=False)

# --- Palette (drives color_attr uniforms; presets recolor via these) -------
# Soup = cyclic 3-stop gradient A→B→C. Keep peaks below Bloomthreshold so the
# calm soup doesn't bloom. Embers = movement birth→death (Hot is HDR > 1).
add_rgb(look, 'Soupcola', 'Soup Color A', (0.15, 0.55, 0.65))
add_rgb(look, 'Soupcolb', 'Soup Color B', (0.30, 0.25, 0.70))
add_rgb(look, 'Soupcolc', 'Soup Color C', (0.65, 0.20, 0.55))
add_rgb(look, 'Emberhot', 'Ember Hot (birth, HDR)', (1.90, 1.55, 1.15))
add_rgb(look, 'Embermid', 'Ember Mid', (1.00, 0.42, 0.10))
add_rgb(look, 'Emberold', 'Ember Old (death)', (0.45, 0.06, 0.02))

# --- Motion-trail velocity gate -------------------------------------------
# Trails feed from a brightness-thresholded copy of the render so only fast /
# energetic particles (which color_attr makes HDR-bright) trail — the calm dim
# soup stays crisp. Higher = trailing only kicks in at higher speed/energy.
add_float(look, 'Trailthreshold', 'Trail Threshold (speed gate)',
          1.6, 0.0, 4.0, clamp_max=False)

# --- Anamorphic streaks (threshold → big H/V blur → add over bloom) --------
add_toggle(look, 'Streakenable', 'Streaks Enable', True)
add_float(look, 'Streakthresh', 'Streak Threshold', 0.8, 0.0, 4.0, clamp_max=False)
add_float(look, 'Streaklength', 'Streak Length (px)', 120.0, 0.0, 600.0, clamp_max=False)
add_float(look, 'Streakintensity', 'Streak Intensity', 0.7, 0.0, 4.0, clamp_max=False)

# --- Color grade (grade.frag) ---------------------------------------------
add_toggle(look, 'Gradeenable', 'Grade Enable', True)
add_float(look, 'Exposure', 'Exposure', 1.0, 0.0, 4.0, clamp_max=False)
add_float(look, 'Contrast', 'Contrast', 1.05, 0.0, 3.0, clamp_max=False)
add_float(look, 'Saturation', 'Saturation', 1.15, 0.0, 3.0, clamp_max=False)
add_rgb(look, 'Lift',  'Lift (shadows)',   (0.0, 0.0, 0.0))
add_rgb(look, 'Gammacolor', 'Gamma (mids)', (1.0, 1.0, 1.0))
add_rgb(look, 'Gain',  'Gain (highlights)', (1.0, 1.0, 1.0))
add_rgb(look, 'Tint',  'Tint',              (1.0, 1.0, 1.0))

# --- Lens finish (lens_finish.frag) ---------------------------------------
add_toggle(look, 'Lensenable', 'Lens Finish Enable', True)
add_float(look, 'Vignette', 'Vignette', 0.4, 0.0, 1.0)
add_float(look, 'Chromab', 'Chromatic Aberration', 0.003, 0.0, 0.05, clamp_max=False)
add_float(look, 'Grain', 'Film Grain', 0.04, 0.0, 0.3, clamp_max=False)


print("velocity_controller: Sensing + Renderer + Look pages installed "
      "({} params total).".format(
          len([pr for pr in comp.customPars
               if pr.page.name in ('Sensing', 'Renderer', 'Look')])))
