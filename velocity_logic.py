"""
velocity_logic.py
=================

Pure-Python logic for the velocity-driven particle controller. No TouchDesigner
imports in here, so you can run `python velocity_logic.py` outside TD and
see the state evolve through a synthetic trajectory as a sanity check.

Place this file in TD as a Text DAT named `velocity_logic` (extension set to
.py or not — both work when accessed via `mod`). It's the sibling of a
Script CHOP whose callback DAT is `velocity_script_chop`.

Design
------
The controller senses N landmarks (default: left_wrist, right_wrist,
left_ankle, right_ankle, nose) and, for each one, emits a small bundle of
per-landmark channels that a downstream particle renderer consumes:

    <L>:x        <L>:y         normalized position (pass-through, 0..1)
    <L>:vx       <L>:vy        smoothed velocity, units/sec in 0..1 space
    <L>:speed                  |v|
    <L>:accel                  |Δv|/Δt magnitude, smoothed
    <L>:emit                   emission rate 0..1  (driven by speed)
    <L>:burst                  burst gate 0..1    (driven by accel spikes)
    <L>:visible                visibility gate 0/1 (visibility > threshold)

Plus a handful of globals:

    total_motion, total_burst, frame_dt

Smoothing & burst semantics
---------------------------
Velocity is computed with a finite difference on position, then low-passed
with a one-pole EMA whose time constant is `velocity_smooth` seconds.

Acceleration is the EMA-smoothed finite difference of velocity. The burst
channel is an envelope that snaps to `|a| / accel_scale` whenever that
exceeds `accel_threshold`, then decays exponentially with time constant
`burst_decay` seconds. That gives you a "spike then tail" pulse per whip
motion rather than a noisy instantaneous accel reading.

The Script CHOP calls `update()` once per cook with (dt, positions, vis).
Everything user-tunable arrives as kwargs; no parameter lives inside this
module. Same parent-pars-only convention as painting_controller.
"""

import math


# ---------------------------------------------------------------------------
# NaN/Inf scrubbing
# ---------------------------------------------------------------------------
# MediaPipe has been observed to emit non-finite values (NaN, ±Inf) on:
#   - the first cook of an invisible landmark
#   - mid-dropout confidence jitter
#   - certain tox builds when the pose worker restarts
# Once a NaN lands in our stored state, the EMA math preserves it forever
# (`NaN * (1 - alpha)` is still NaN). The Lag CHOP then latches onto the
# NaN channel and freezes accel/burst/emit/etc. until it is manually reset.
# We defend on three layers: at input (Script CHOP _read), at state entry
# (update scrubs stored state), and at output (_emit clamps returns).

def _finite(v, default=0.0):
    """Coerce v to a finite float. Non-finite -> default."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(f):
        return default
    return f


def _scrub_sample(sample):
    """Mutate one landmark's state dict, replacing any non-finite scalars
    with safe defaults. last_good_{x,y,z} may legitimately be None — leave
    those alone. Called on every update() cook to heal any corruption that
    sneaked in on a previous frame."""
    for k in ("vx", "vy", "vz",
              "prev_vx", "prev_vy", "prev_vz",
              "accel", "burst"):
        sample[k] = _finite(sample.get(k, 0.0), 0.0)
    # Position-like fields: None is valid, non-finite numbers are not.
    for k in ("prev_x", "prev_y", "prev_z",
              "last_good_x", "last_good_y", "last_good_z"):
        v = sample.get(k, None)
        if v is None:
            continue
        try:
            f = float(v)
            if not math.isfinite(f):
                sample[k] = None
            else:
                sample[k] = f
        except (TypeError, ValueError):
            sample[k] = None

# ---------------------------------------------------------------------------
# Default landmark set
# ---------------------------------------------------------------------------
# You can override LANDMARKS at call time; this is just the default the
# Script CHOP and install_velocity_params.py agree on. Names match
# blankensmithing's pose channel prefixes.
LANDMARKS = (
    "left_wrist",
    "right_wrist",
    "left_elbow",
    "right_elbow"
)

# ---------------------------------------------------------------------------
# Per-landmark channel suffixes (emitted in this fixed order per landmark)
# ---------------------------------------------------------------------------
PER_LANDMARK_CHANS = (
    "x", "y", "z",
    "vx", "vy", "vz",
    "speed",         # 3D magnitude sqrt(vx²+vy²+vz²)
    "accel",
    "emit",
    "burst",
    "visible",
)

GLOBAL_CHANS = (
    "total_motion",
    "total_burst",
    "frame_dt",
)


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

def _fresh_landmark_state():
    """Per-landmark state template. Keep this as the single source of truth
    for the inner-dict schema — `new_state` and `ensure_schema` both use it
    so adding a new field never leaves old sessions in an inconsistent shape."""
    return {
        "prev_x":         None,  # previous position, None = uninitialised
        "prev_y":         None,
        "prev_z":         None,
        "vx":             0.0,   # smoothed velocity
        "vy":             0.0,
        "vz":             0.0,
        "prev_vx":        0.0,   # previous smoothed velocity (for accel)
        "prev_vy":        0.0,
        "prev_vz":        0.0,
        "accel":          0.0,   # smoothed |a|  (3D)
        "burst":          0.0,   # burst envelope (0..1, decays)
        "last_good_x":    None,  # last trusted position, held on dropout
        "last_good_y":    None,
        "last_good_z":    None,
        "settle_counter": 0,     # consecutive trusted frames since last dropout
    }


def new_state(landmarks=LANDMARKS):
    """
    Build a fresh state dict. Keyed by landmark name. The Script CHOP
    stashes this on the parent COMP via op.store() so it survives
    cook-to-cook and is reset cleanly on reload.
    """
    return {lm: _fresh_landmark_state() for lm in landmarks}


def ensure_schema(state, landmarks):
    """
    Migrate a state dict forward when this module adds new fields. Called
    by the Script CHOP every cook — cheap and idempotent.

    - Adds landmarks that are in `landmarks` but not in `state`.
    - Leaves landmarks in `state` that aren't in `landmarks` alone (they're
      harmless stale data; the caller decides whether to rebuild).
    - Backfills any missing inner-dict keys with defaults so old sessions
      don't KeyError after a schema bump.

    Mutates `state` in place and returns it.
    """
    template = _fresh_landmark_state()
    for lm in landmarks:
        if lm not in state or not isinstance(state[lm], dict):
            state[lm] = _fresh_landmark_state()
        else:
            for k, v in template.items():
                state[lm].setdefault(k, v)
    return state


def reset_state(state):
    """Clear all sampling history; call when the source drops out for a while."""
    for lm in state:
        s = state[lm]
        s["prev_x"] = None
        s["prev_y"] = None
        s["vx"] = 0.0
        s["vy"] = 0.0
        s["prev_vx"] = 0.0
        s["prev_vy"] = 0.0
        s["accel"] = 0.0
        s["burst"] = 0.0
        # Keep last_good_x/y across resets — they're the whole point of
        # holding position on dropout, and are harmless if stale.


# ---------------------------------------------------------------------------
# Math helpers
# ---------------------------------------------------------------------------

def _ema_alpha(dt, tau):
    """
    One-pole EMA coefficient for a target time constant `tau` (seconds).
    alpha=1 passes raw, alpha=0 freezes. Clamped so dt > tau still behaves.
    """
    if tau <= 0.0:
        return 1.0
    # 1 - exp(-dt/tau) is the exact form; small-dt approximation dt/tau
    # is also fine but we pay for the exp to keep big frame hitches sane.
    a = 1.0 - math.exp(-dt / tau)
    if a < 0.0:
        return 0.0
    if a > 1.0:
        return 1.0
    return a


def _clamp01(v):
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v


# ---------------------------------------------------------------------------
# Core update: called once per cook per landmark
# ---------------------------------------------------------------------------

def update_landmark(sample, x, y, z, visible, trusted, dt, params):
    """
    Advance one landmark's state by one frame.

    sample    : dict entry from state[lm] — mutated in place
    x, y, z   : new position. x/y are 0..1 MediaPipe-space. z is MediaPipe's
                depth estimate in roughly-same-unit-as-x (hip-center ~0,
                negative = toward camera, positive = away). Less reliable
                than x/y, but usable for direction.
    visible   : bool — output gate
    trusted   : bool — state-commit gate
    dt        : seconds since previous cook
    params    : dict with keys
                  velocity_smooth, accel_smooth, speed_scale,
                  accel_threshold, accel_scale, burst_decay, max_jump

    Three zones
    -----------
    1. trusted=True:        use raw (x, y), update last_good, compute velocity,
                            output visible=1.
    2. visible=True, trusted=False (marginal zone):
                            output last_good, NOT raw. last_good is NOT updated.
                            Envelopes still update normally. visible=1 so the
                            emitter stays on, but pinned to the last trusted
                            position. Prevents the "drift to garbage" slide
                            during MediaPipe's confidence ramp-down.
    3. visible=False:       output last_good, visible=0, envelopes decay.
                            Net: blob fades out in place.

    Also: even in the trusted zone, if (x, y) jumps by more than `max_jump`
    from last_good, the frame is rejected (treated as zone 2) — belt-and-
    suspenders against single-frame teleports that are somehow still flagged
    trusted.

    Returns a dict of per-landmark output values:
      x, y, vx, vy, speed, accel, emit, burst, visible
    """
    # ---- Burst envelope decays every cook regardless of visibility. -------
    decay_a = _ema_alpha(dt, params["burst_decay"])
    sample["burst"] *= (1.0 - decay_a)

    # ---- Jump rejection: catch a single-frame teleport within a running
    # trusted stream. Compared against `prev_x/y` (previous frame's position)
    # NOT `last_good_x/y`, because on re-acquisition we WANT to accept a
    # faraway new position. Additionally, skip the check entirely for the
    # first `settle_frames` cooks after a dropout — MediaPipe's first few
    # trusted frames often land near the re-entry edge before locking onto
    # the real joint position, and rejecting the second frame as "teleport"
    # leaves the emitter stuck at the edge for a cook. The settle counter
    # is reset on every non-trusted frame so any dropout re-arms it.
    jumped = False
    settling = sample.get("settle_counter", 0) < params.get("settle_frames", 5)
    if (trusted
            and not settling
            and sample["prev_x"] is not None
            and params.get("max_jump", 0.0) > 0.0):
        dx = x - sample["prev_x"]
        dy = y - sample["prev_y"]
        # z is noisier than xy; down-weight it in the jump check so a
        # wobbly depth estimate doesn't trigger false teleport rejection.
        # max_jump still applies primarily in the image plane.
        prev_z = sample["prev_z"] if sample["prev_z"] is not None else z
        dz = (z - prev_z) * 0.3
        if (dx * dx + dy * dy + dz * dz) > (params["max_jump"] * params["max_jump"]):
            jumped = True

    # A jumped frame is demoted out of the trusted zone but keeps its
    # `visible` flag — so it lands in the "marginal" branch below.
    if jumped:
        trusted = False

    # ---- Zone 2 & 3: not trusted (either marginal-visible or invisible) ---
    # Fall through to the fade-in-place path. In both cases we output
    # last_good (never raw), so the blob doesn't slide during confidence
    # ramp-downs. In zone 3 the output `visible` is 0 so downstream gates
    # kill spawning; in zone 2 it's still 1 so emitter stays on, just pinned.
    if not trusted or dt <= 0.0:
        # Any non-trusted frame re-arms the settle grace period.
        sample["settle_counter"] = 0
        if not visible:
            # Invisible — decay all three velocity axes + accel toward 0.
            alpha_v = _ema_alpha(dt, params["velocity_smooth"])
            sample["vx"] *= (1.0 - alpha_v)
            sample["vy"] *= (1.0 - alpha_v)
            sample["vz"] *= (1.0 - alpha_v)
            sample["prev_vx"] = sample["vx"]
            sample["prev_vy"] = sample["vy"]
            sample["prev_vz"] = sample["vz"]
            alpha_a = _ema_alpha(dt, params["accel_smooth"])
            sample["accel"] *= (1.0 - alpha_a)
            sample["prev_x"] = None
            sample["prev_y"] = None
            sample["prev_z"] = None
        else:
            # Marginal — pin position, don't aggressively decay velocity.
            sample["prev_x"] = None
            sample["prev_y"] = None
            sample["prev_z"] = None

        # Output last_good if we have one, else raw (first-frame fallback).
        if sample["last_good_x"] is not None:
            out_x = sample["last_good_x"]
            out_y = sample["last_good_y"]
            out_z = sample["last_good_z"] if sample["last_good_z"] is not None else 0.0
        else:
            out_x, out_y, out_z = x, y, z
        return _emit(sample, out_x, out_y, out_z, visible, params)

    # ---- Zone 1: trusted. Commit last_good and run normal velocity math. --
    sample["last_good_x"] = x
    sample["last_good_y"] = y
    sample["last_good_z"] = z
    # Count consecutive trusted frames so the settle grace naturally
    # expires after `settle_frames` frames of stable tracking.
    sample["settle_counter"] = sample.get("settle_counter", 0) + 1

    # ---- Velocity via finite diff + EMA smooth ----------------------------
    if sample["prev_x"] is None:
        # First valid sample: seed, don't diff (would produce a huge spike
        # because prev == 0).
        sample["prev_x"] = x
        sample["prev_y"] = y
        sample["prev_z"] = z
        sample["vx"] = 0.0
        sample["vy"] = 0.0
        sample["vz"] = 0.0
        sample["prev_vx"] = 0.0
        sample["prev_vy"] = 0.0
        sample["prev_vz"] = 0.0
        sample["accel"] = 0.0
        return _emit(sample, x, y, z, visible, params)

    raw_vx = (x - sample["prev_x"]) / dt
    raw_vy = (y - sample["prev_y"]) / dt
    raw_vz = (z - sample["prev_z"]) / dt

    alpha_v = _ema_alpha(dt, params["velocity_smooth"])
    new_vx = sample["vx"] + alpha_v * (raw_vx - sample["vx"])
    new_vy = sample["vy"] + alpha_v * (raw_vy - sample["vy"])
    new_vz = sample["vz"] + alpha_v * (raw_vz - sample["vz"])

    # ---- Acceleration magnitude (3D) via diff of smoothed velocity --------
    # z is down-weighted by z_speed_weight so depth noise doesn't dominate
    # burst detection — MediaPipe's z is noisier than x/y.
    raw_ax = (new_vx - sample["prev_vx"]) / dt
    raw_ay = (new_vy - sample["prev_vy"]) / dt
    raw_az = (new_vz - sample["prev_vz"]) / dt
    zw = params.get("z_speed_weight", 1.0)
    raw_a_mag = math.sqrt(raw_ax * raw_ax + raw_ay * raw_ay
                          + (zw * raw_az) * (zw * raw_az))

    alpha_a = _ema_alpha(dt, params["accel_smooth"])
    smoothed_a = sample["accel"] + alpha_a * (raw_a_mag - sample["accel"])

    # ---- Burst detection --------------------------------------------------
    # Normalised magnitude above the threshold arms the envelope.
    if smoothed_a > params["accel_threshold"]:
        # How much over? Map to 0..1 via accel_scale.
        over = (smoothed_a - params["accel_threshold"]) / max(params["accel_scale"], 1e-6)
        new_burst = _clamp01(over)
        # Use max(existing, new) so overlapping snaps don't cancel the tail.
        if new_burst > sample["burst"]:
            sample["burst"] = new_burst

    # ---- Commit state for next cook ---------------------------------------
    sample["prev_x"] = x
    sample["prev_y"] = y
    sample["prev_z"] = z
    sample["prev_vx"] = sample["vx"]
    sample["prev_vy"] = sample["vy"]
    sample["prev_vz"] = sample["vz"]
    sample["vx"] = new_vx
    sample["vy"] = new_vy
    sample["vz"] = new_vz
    sample["accel"] = smoothed_a

    return _emit(sample, x, y, z, visible, params)


def _emit(sample, x, y, z, visible, params):
    # Final safety net: even though update()/update_landmark() sanitize
    # inputs and state, clamp every outbound value to a finite float so
    # the Script CHOP can never emit NaN into the Lag CHOP.
    vx    = _finite(sample["vx"], 0.0)
    vy    = _finite(sample["vy"], 0.0)
    vz    = _finite(sample["vz"], 0.0)
    accel = _finite(sample["accel"], 0.0)
    burst = _finite(sample["burst"], 0.0)
    # Speed is a weighted 3D magnitude — vz contributes less than vx/vy by
    # default (z_speed_weight < 1.0) because MediaPipe's z is noisier and
    # because a small lean toward the camera shouldn't cause the same
    # emission spike as a full arm whip. Still a 3D signal — just tamer.
    zw    = params.get("z_speed_weight", 1.0)
    speed = math.sqrt(vx * vx + vy * vy + (zw * vz) * (zw * vz))
    emit  = _clamp01(speed / max(params["speed_scale"], 1e-6))
    return {
        "x":       _finite(x, 0.0),
        "y":       _finite(y, 0.0),
        "z":       _finite(z, 0.0),
        "vx":      vx,
        "vy":      vy,
        "vz":      vz,
        "speed":   speed,
        "accel":   accel,
        "emit":    emit,
        "burst":   burst,
        "visible": 1.0 if visible else 0.0,
    }


# ---------------------------------------------------------------------------
# Batch update — called once per cook by the Script CHOP with all landmarks
# ---------------------------------------------------------------------------

def update(state, samples, dt, params):
    """
    state   : dict from new_state()
    samples : dict {landmark_name: tuple}
              Accepted tuple forms (back-compat first):
                (x, y, visible)                     → z=0, trusted=visible
                (x, y, visible, trusted)            → z=0
                (x, y, z, visible, trusted)         → full 3D (preferred)
              `visible` gates the output (emit/burst can still fire).
              `trusted` governs whether to update last_good and run velocity
              math on this frame — should be stricter than `visible`.
              Missing landmarks are skipped (their state still decays).
    dt      : seconds since previous cook
    params  : dict — see update_landmark()

    Returns:
      per_landmark : {landmark_name: {...output fields...}}
      globals      : {total_motion, total_burst, frame_dt}
    """
    per_landmark = {}
    total_motion = 0.0
    total_burst = 0.0
    # Scrub dt once — a non-finite dt would pollute every landmark via
    # _ema_alpha / velocity division.
    dt = _finite(dt, 0.0)
    for lm, st in state.items():
        # Heal any NaN/Inf that sneaked into stored state on a prior frame.
        _scrub_sample(st)
        if lm in samples:
            s = samples[lm]
            if len(s) == 3:
                x, y, vis = s
                z, trust = 0.0, vis
            elif len(s) == 4:
                x, y, vis, trust = s
                z = 0.0
            else:
                x, y, z, vis, trust = s
            # Scrub inputs too — MediaPipe can send NaN for invisible joints.
            x = _finite(x, 0.0)
            y = _finite(y, 0.0)
            z = _finite(z, 0.0)
        else:
            # No sample — decay envelopes, emit zeros.
            x, y, z, vis, trust = 0.0, 0.0, 0.0, False, False
        out = update_landmark(st, x, y, z, vis, trust, dt, params)
        per_landmark[lm] = out
        total_motion += out["speed"]
        total_burst  += out["burst"]

    return per_landmark, {
        "total_motion": _finite(total_motion, 0.0),
        "total_burst":  _finite(total_burst, 0.0),
        "frame_dt":     dt,
    }


# ---------------------------------------------------------------------------
# Default params helper — matches the custom-page defaults in installer
# ---------------------------------------------------------------------------

def default_params():
    return {
        "velocity_smooth": 0.08,   # s — shorter = snappier velocity
        "accel_smooth":    0.05,   # s — shorter = crisper bursts, more noise
        "speed_scale":     2.5,    # 1/s — speed/scale is the emit rate
        "accel_threshold": 8.0,    # 1/s² — below this, no burst
        "accel_scale":     40.0,   # 1/s² — full-burst accel above the threshold
        "burst_decay":     0.35,   # s — burst tail length
        "max_jump":        0.30,   # UV units per frame — over this = teleport
        "settle_frames":   5,      # frames of max_jump-free grace after re-acquisition
        "z_speed_weight":  0.35,   # how much vz/az contributes to speed & accel
                                   # for emit/burst purposes. 1.0 = full 3D,
                                   # 0.0 = z motion doesn't trigger emit/burst
                                   # (but vz is still emitted as a channel).
    }


# ---------------------------------------------------------------------------
# Self-test: synthetic trajectory. Run `python velocity_logic.py`.
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Simulate a wrist being still, then doing a fast whip, then stopping.
    # Landmark: single 'left_wrist'.
    import random
    random.seed(0)

    params = default_params()
    state = new_state(("left_wrist",))
    dt = 1.0 / 60.0

    def feed(traj_fn, n_frames, label):
        print(f"\n--- {label} ---")
        print(f"{'f':>3} {'x':>5} {'y':>5} {'spd':>6} {'acc':>7} {'emit':>5} {'brst':>5}")
        for i in range(n_frames):
            x, y = traj_fn(i)
            per, glb = update(
                state, {"left_wrist": (x, y, True)}, dt, params
            )
            o = per["left_wrist"]
            if i % 5 == 0 or i == n_frames - 1:
                print(f"{i:>3} {x:>5.2f} {y:>5.2f} "
                      f"{o['speed']:>6.2f} {o['accel']:>7.2f} "
                      f"{o['emit']:>5.2f} {o['burst']:>5.2f}")

    # 30 frames of stillness at (0.5, 0.5)
    feed(lambda i: (0.5, 0.5), 30, "still at center")

    # 20 frames of a sharp horizontal whip from 0.2 → 0.8 then stop
    def whip(i):
        # Ease-in, snap to rest: cubic hermite-ish
        if i < 8:
            u = i / 7.0
            # fast acceleration
            return (0.2 + (0.8 - 0.2) * (u * u), 0.5)
        return (0.8, 0.5)
    feed(whip, 20, "whip right then stop (expect burst spike around frames 5-10)")

    # 10 frames visible=False — envelopes decay, but position should HOLD
    # at the last good value (0.8, 0.5) rather than use the passed-in garbage.
    print("\n--- landmark dropped (visible=False, input garbage (0, 1)) ---")
    print("    expected: out.x=0.80 out.y=0.50  (last good, not garbage)")
    for i in range(10):
        per, glb = update(state, {"left_wrist": (0.0, 1.0, False)}, dt, params)
        o = per["left_wrist"]
        print(f"  f{i}: out=({o['x']:.2f},{o['y']:.2f}) "
              f"burst={o['burst']:.3f} accel={o['accel']:.3f} "
              f"visible={o['visible']}")
        assert abs(o["x"] - 0.8) < 1e-6, "position should be held at last good"
        assert abs(o["y"] - 0.5) < 1e-6, "position should be held at last good"

    # Sanity: burst strictly non-negative, emit in 0..1
    assert 0.0 <= o["burst"] <= 1.0
    assert 0.0 <= o["emit"] <= 1.0

    # Intra-continuity teleport test: within a running trusted stream,
    # inject one frame with a big position jump. Must be rejected as a
    # single-frame glitch (output stays at last good). Crucial: this is
    # NOT "after invisibility" — for that case we WANT re-acquisition to
    # succeed (tested separately below).
    print("\n--- single-frame teleport inside a trusted stream ---")
    print("    expected: glitch frame output=last trusted (0.80, 0.50), no burst")
    state_tele = new_state(("left_wrist",))
    # Build up trusted continuous tracking at (0.80, 0.50).
    for _ in range(10):
        update(state_tele, {"left_wrist": (0.8, 0.5, True, True)}, dt, params)
    burst_before = state_tele["left_wrist"]["burst"]
    # Inject one frame of glitch at (0.05, 0.95)
    per, _ = update(state_tele, {"left_wrist": (0.05, 0.95, True, True)}, dt, params)
    o = per["left_wrist"]
    print(f"  glitch frame: out=({o['x']:.2f},{o['y']:.2f}) burst={o['burst']:.3f}")
    assert abs(o["x"] - 0.8) < 1e-6, "intra-continuity teleport should be rejected"
    assert o["burst"] <= burst_before + 1e-6, "teleport should NOT fire a burst"
    # Next frame back at a plausible position — should re-seed and accept.
    per, _ = update(state_tele, {"left_wrist": (0.81, 0.51, True, True)}, dt, params)
    o = per["left_wrist"]
    print(f"  recovery frame: out=({o['x']:.2f},{o['y']:.2f}) (tracking resumed)")
    assert abs(o["x"] - 0.81) < 1e-6, "stream should recover on next non-glitched frame"

    # Hysteresis test: marginal-zone frames with drifting position should
    # NOT update last_good. The blob stays pinned while visibility ramps down.
    print("\n--- marginal-zone drift (visible=True, trusted=False, position drifting) ---")
    print("    expected: out stays at last trusted (~0.80, 0.50) despite input drifting")
    # Reset state to a known-trusted position.
    state2 = new_state(("left_wrist",))
    for _ in range(5):
        update(state2, {"left_wrist": (0.8, 0.5, True, True)}, dt, params)
    # Now feed marginal frames where raw x drifts from 0.8 toward 0.1.
    drift_xs = [0.70, 0.55, 0.40, 0.25, 0.10]
    for i, dx_val in enumerate(drift_xs):
        per, _ = update(state2,
                        {"left_wrist": (dx_val, 0.95, True, False)},
                        dt, params)
        o = per["left_wrist"]
        print(f"  f{i}: raw_in=({dx_val:.2f}, 0.95)  out=({o['x']:.2f},{o['y']:.2f})  "
              f"visible={o['visible']}")
        assert abs(o["x"] - 0.8) < 1e-6, "marginal frame leaked drifting x"
        assert abs(o["y"] - 0.5) < 1e-6, "marginal frame leaked drifting y"
        assert o["visible"] == 1.0, "marginal frame should still be visible=1"

    # Then fully invisible — position should still be 0.8, visible=0
    per, _ = update(state2, {"left_wrist": (0.0, 1.0, False, False)}, dt, params)
    o = per["left_wrist"]
    print(f"  invisible: out=({o['x']:.2f},{o['y']:.2f}) visible={o['visible']}")
    assert abs(o["x"] - 0.8) < 1e-6 and o["visible"] == 0.0

    # Re-acquisition test: joint was tracked at (0.8, 0.5), went invisible,
    # then reappears at (0.2, 0.3) — a big distance from the stale last_good.
    # Must accept the new position on the first trusted frame, NOT freeze at
    # last_good forever just because the delta exceeds max_jump.
    print("\n--- re-acquisition at a faraway position ---")
    print("    last_good was (0.8, 0.5), joint reappears at (0.2, 0.3)")
    state3 = new_state(("left_wrist",))
    # Build up state: tracked at (0.8, 0.5)
    for _ in range(5):
        update(state3, {"left_wrist": (0.8, 0.5, True, True)}, dt, params)
    # Go invisible for a bit
    for _ in range(10):
        update(state3, {"left_wrist": (0.0, 1.0, False, False)}, dt, params)
    # Reappear at a totally different position
    per, _ = update(state3, {"left_wrist": (0.2, 0.3, True, True)}, dt, params)
    o = per["left_wrist"]
    print(f"  first trusted frame after dropout: out=({o['x']:.2f},{o['y']:.2f}) "
          f"visible={o['visible']}")
    assert abs(o["x"] - 0.2) < 1e-6, (
        f"re-acquisition FAILED: output stuck at {o['x']} instead of new pos 0.2")
    assert abs(o["y"] - 0.3) < 1e-6
    # Keep tracking at the new position; confirm it follows.
    for i in range(3):
        per, _ = update(state3,
                        {"left_wrist": (0.2 + 0.02 * i, 0.3, True, True)},
                        dt, params)
        o = per["left_wrist"]
        print(f"  follow-up f{i}: out=({o['x']:.2f},{o['y']:.2f})")
        expected_x = 0.2 + 0.02 * i
        assert abs(o["x"] - expected_x) < 1e-6, "follow-up not tracking"

    # Settle grace test: after dropout, MediaPipe's first trusted frame lands
    # near the re-entry edge, then settles to the real joint over the next
    # few frames. Without the grace period the 2nd trusted frame would be
    # rejected as a teleport (prev_x was just set to the edge, delta > max_jump)
    # and the blob would be stuck at the edge for a cook.
    print("\n--- settle grace after re-acquisition (edge-then-joint) ---")
    state4 = new_state(("left_wrist",))
    # Build up tracked state elsewhere, then drop out.
    for _ in range(5):
        update(state4, {"left_wrist": (0.5, 0.5, True, True)}, dt, params)
    for _ in range(15):
        update(state4, {"left_wrist": (0.0, 1.0, False, False)}, dt, params)
    # Re-acquisition sequence: frame 0 at edge (0.03, 0.4), next frames at
    # real joint position (0.35, 0.4). Jump is 0.32, exceeds max_jump=0.3.
    print("    input: edge (0.03, 0.40) then joint (0.35, 0.40)...")
    reacq = [(0.03, 0.40), (0.35, 0.40), (0.36, 0.40), (0.37, 0.40), (0.38, 0.40)]
    for i, (rx, ry) in enumerate(reacq):
        per, _ = update(state4, {"left_wrist": (rx, ry, True, True)}, dt, params)
        o = per["left_wrist"]
        print(f"  f{i}: raw=({rx:.2f},{ry:.2f}) out=({o['x']:.2f},{o['y']:.2f}) "
              f"settle={state4['left_wrist']['settle_counter']}")
    # After the settle period we want to be tracking the real joint, NOT
    # stuck at 0.03 (the edge).
    assert abs(o["x"] - 0.38) < 1e-6, \
        f"settle grace failed: still at {o['x']} instead of tracking"

    # NaN/Inf resilience: inject garbage, make sure state doesn't latch.
    print("\n--- NaN/Inf input resilience ---")
    state5 = new_state(("left_wrist",))
    for _ in range(5):
        update(state5, {"left_wrist": (0.5, 0.5, True, True)}, dt, params)
    nan = float('nan')
    inf = float('inf')
    # Feed NaN/Inf directly through update() — should NOT poison state.
    per, glb = update(state5, {"left_wrist": (nan, inf, True, True)}, dt, params)
    o = per["left_wrist"]
    print(f"  after NaN input: out=({o['x']:.2f},{o['y']:.2f}) "
          f"speed={o['speed']:.3f} accel={o['accel']:.3f}")
    for k, v in o.items():
        assert math.isfinite(float(v)), f"{k} leaked non-finite: {v}"
    # Follow up with clean frames; pipeline should recover.
    for _ in range(3):
        per, _ = update(state5, {"left_wrist": (0.5, 0.5, True, True)}, dt, params)
    o = per["left_wrist"]
    for k in ("x", "y", "vx", "vy", "speed", "accel", "emit", "burst"):
        assert math.isfinite(float(o[k])), f"{k} stayed non-finite after recovery"
    print(f"  recovered cleanly: vx={o['vx']:.4f} accel={o['accel']:.4f}")

    # 3D velocity test: motion purely along z (hand moving forward/back)
    # should show up in vz, contribute to 3D speed, and fire burst when
    # the z-velocity snaps. x/y stay constant.
    print("\n--- 3D velocity: forward/back motion (z only) ---")
    state6 = new_state(("left_wrist",))
    # Build stable tracking at (0.5, 0.5, z=0.0)
    for _ in range(10):
        update(state6, {"left_wrist": (0.5, 0.5, 0.0, True, True)}, dt, params)
    o = None
    # Push hand forward toward camera over 5 frames: z goes 0.0 → -0.5
    for i in range(5):
        zv = -0.1 * (i + 1)
        per, _ = update(state6,
                        {"left_wrist": (0.5, 0.5, zv, True, True)}, dt, params)
        o = per["left_wrist"]
        print(f"  f{i}: z={zv:+.2f} vz={o['vz']:+.3f} vx={o['vx']:+.3f} "
              f"speed={o['speed']:.3f} burst={o['burst']:.3f}")
    # Expect vz negative (moving toward camera), vx/vy ~0.
    assert o["vz"] < -0.3, f"vz should track z motion, got {o['vz']}"
    assert abs(o["vx"]) < 0.01 and abs(o["vy"]) < 0.01, "xy should stay still"
    assert o["speed"] > 0.3, "3D speed should include vz contribution"

    # Z speed weight: same z-only motion should produce LESS emit and LESS
    # burst than a comparable xy motion, once z_speed_weight < 1.
    print("\n--- z_speed_weight tames depth sensitivity ---")
    # First: xy motion at speed 2.5 (hits emit=1 at default Speedscale).
    state_xy = new_state(("wrist",))
    for i in range(5):
        update(state_xy, {"wrist": (0.5 + 0.04 * i, 0.5, 0.0, True, True)},
               dt, params)
    xy_speed = None
    for i in range(3):
        per, _ = update(state_xy,
                        {"wrist": (0.5 + 0.04 * (5 + i), 0.5, 0.0, True, True)},
                        dt, params)
        xy_speed = per["wrist"]["speed"]

    # Same magnitude of motion but purely in z.
    state_z = new_state(("wrist",))
    for i in range(5):
        update(state_z, {"wrist": (0.5, 0.5, 0.04 * i, True, True)},
               dt, params)
    z_speed = None
    for i in range(3):
        per, _ = update(state_z,
                        {"wrist": (0.5, 0.5, 0.04 * (5 + i), True, True)},
                        dt, params)
        z_speed = per["wrist"]["speed"]
    print(f"  xy-only speed: {xy_speed:.3f}")
    print(f"  z-only  speed: {z_speed:.3f}   (expect ≈ xy_speed * z_weight "
          f"= {xy_speed * params['z_speed_weight']:.3f})")
    # z contribution should be scaled by z_weight (0.35 default), so the
    # z-only speed should be ~0.35x the xy-only speed for the same raw motion.
    ratio = z_speed / xy_speed
    assert abs(ratio - params["z_speed_weight"]) < 0.05, \
        f"z weight not applied: z_speed/xy_speed = {ratio:.3f}"

    print("\nOK — invisible hold, envelope decay, teleport rejection,")
    print("     marginal freeze, re-acquisition, settle grace, NaN/Inf")
    print("     resilience, 3D z-axis tracking, and z-speed weighting")
    print("     all pass.")
