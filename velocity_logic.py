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
# Default landmark set
# ---------------------------------------------------------------------------
# You can override LANDMARKS at call time; this is just the default the
# Script CHOP and install_velocity_params.py agree on. Names match
# blankensmithing's pose channel prefixes.
LANDMARKS = (
    "left_wrist",
    "right_wrist",
    "left_ankle",
    "right_ankle",
    "nose",
)

# ---------------------------------------------------------------------------
# Per-landmark channel suffixes (emitted in this fixed order per landmark)
# ---------------------------------------------------------------------------
PER_LANDMARK_CHANS = (
    "x", "y",
    "vx", "vy",
    "speed",
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

def new_state(landmarks=LANDMARKS):
    """
    Build a fresh state dict. Keyed by landmark name. Each entry holds the
    previous sample, the smoothed velocity, and the smoothed acceleration /
    burst envelope.

    The Script CHOP stashes this on the parent COMP via op.store() so it
    survives cook-to-cook and is reset cleanly on reload.
    """
    return {
        lm: {
            "prev_x": None,     # previous position, None = uninitialised
            "prev_y": None,
            "vx": 0.0,          # smoothed velocity
            "vy": 0.0,
            "prev_vx": 0.0,     # previous smoothed velocity (for accel)
            "prev_vy": 0.0,
            "accel": 0.0,       # smoothed |a|
            "burst": 0.0,       # burst envelope (0..1, decays)
        }
        for lm in landmarks
    }


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

def update_landmark(sample, x, y, visible, dt, params):
    """
    Advance one landmark's state by one frame.

    sample   : dict entry from state[lm] — mutated in place
    x, y     : new position in 0..1 (MediaPipe-space)
    visible  : bool — if False the sample is ignored but the burst still decays
    dt       : seconds since previous cook
    params   : dict with keys
                 velocity_smooth, accel_smooth, speed_scale,
                 accel_threshold, accel_scale, burst_decay

    Returns a dict of per-landmark output values:
      x, y, vx, vy, speed, accel, emit, burst, visible
    """
    # ---- Burst envelope decays every cook regardless of visibility. -------
    decay_a = _ema_alpha(dt, params["burst_decay"])
    sample["burst"] *= (1.0 - decay_a)

    if not visible or dt <= 0.0:
        # No new sample. Don't fabricate motion from stale state: decay
        # vx, vy, and accel toward zero at the velocity-smooth rate. Also
        # blank prev_x/y so that the *next* visible frame seeds cleanly
        # instead of computing a jump-sized velocity across the blackout.
        alpha_v = _ema_alpha(dt, params["velocity_smooth"])
        sample["vx"] *= (1.0 - alpha_v)
        sample["vy"] *= (1.0 - alpha_v)
        sample["prev_vx"] = sample["vx"]
        sample["prev_vy"] = sample["vy"]
        alpha_a = _ema_alpha(dt, params["accel_smooth"])
        sample["accel"] *= (1.0 - alpha_a)
        sample["prev_x"] = None
        sample["prev_y"] = None
        return _emit(sample, x, y, visible, params)

    # ---- Velocity via finite diff + EMA smooth ----------------------------
    if sample["prev_x"] is None:
        # First valid sample: seed, don't diff (would produce a huge spike
        # because prev == 0).
        sample["prev_x"] = x
        sample["prev_y"] = y
        sample["vx"] = 0.0
        sample["vy"] = 0.0
        sample["prev_vx"] = 0.0
        sample["prev_vy"] = 0.0
        sample["accel"] = 0.0
        return _emit(sample, x, y, visible, params)

    raw_vx = (x - sample["prev_x"]) / dt
    raw_vy = (y - sample["prev_y"]) / dt

    alpha_v = _ema_alpha(dt, params["velocity_smooth"])
    new_vx = sample["vx"] + alpha_v * (raw_vx - sample["vx"])
    new_vy = sample["vy"] + alpha_v * (raw_vy - sample["vy"])

    # ---- Acceleration magnitude via diff of smoothed velocity -------------
    raw_ax = (new_vx - sample["prev_vx"]) / dt
    raw_ay = (new_vy - sample["prev_vy"]) / dt
    raw_a_mag = math.sqrt(raw_ax * raw_ax + raw_ay * raw_ay)

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
    sample["prev_vx"] = sample["vx"]
    sample["prev_vy"] = sample["vy"]
    sample["vx"] = new_vx
    sample["vy"] = new_vy
    sample["accel"] = smoothed_a

    return _emit(sample, x, y, visible, params)


def _emit(sample, x, y, visible, params):
    speed = math.sqrt(sample["vx"] * sample["vx"] + sample["vy"] * sample["vy"])
    emit = _clamp01(speed / max(params["speed_scale"], 1e-6))
    return {
        "x": x,
        "y": y,
        "vx": sample["vx"],
        "vy": sample["vy"],
        "speed": speed,
        "accel": sample["accel"],
        "emit": emit,
        "burst": sample["burst"],
        "visible": 1.0 if visible else 0.0,
    }


# ---------------------------------------------------------------------------
# Batch update — called once per cook by the Script CHOP with all landmarks
# ---------------------------------------------------------------------------

def update(state, samples, dt, params):
    """
    state   : dict from new_state()
    samples : dict {landmark_name: (x, y, visible_bool)}
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
    for lm, st in state.items():
        if lm in samples:
            x, y, vis = samples[lm]
        else:
            # No sample — decay envelopes, emit zeros.
            x, y, vis = 0.0, 0.0, False
        out = update_landmark(st, x, y, vis, dt, params)
        per_landmark[lm] = out
        total_motion += out["speed"]
        total_burst  += out["burst"]

    return per_landmark, {
        "total_motion": total_motion,
        "total_burst":  total_burst,
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

    # 10 frames visible=False — envelopes should decay cleanly
    print("\n--- landmark dropped (visible=False) ---")
    for i in range(10):
        per, glb = update(state, {"left_wrist": (0.8, 0.5, False)}, dt, params)
        o = per["left_wrist"]
        print(f"  f{i}: burst={o['burst']:.3f} accel={o['accel']:.3f} "
              f"visible={o['visible']}")

    # Sanity: burst strictly non-negative, emit in 0..1
    assert 0.0 <= o["burst"] <= 1.0
    assert 0.0 <= o["emit"] <= 1.0
    print("\nOK — envelopes decayed cleanly, burst/emit in range.")
