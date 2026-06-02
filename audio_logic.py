"""
audio_logic.py
==============

Pure-Python audio-reactivity logic for the particle system. No TouchDesigner
imports — run `python3 audio_logic.py` to exercise the envelopes/AGC/build
state on a synthetic signal.

It turns the raw ARE (Audio Reactive Engine v1.2) feature channels into a small
set of normalised, well-behaved MODULATION signals the particle render binds to.
The TD wrapper (`audio_react_chop.py`) feeds raw features in each cook and writes
the outputs to a Script CHOP; downstream uniforms/scripts read those as
`base + mod·depth` so audio NEVER overwrites hand-tuned base values and the whole
layer switches off when the master depth is 0.

ARE feature inputs (see ARE docs, Outputs table)
-------------------------------------------------
  drums_low / drums_mid / drums_high : 0..1 percussive DETECTIONS (spiky)
  burst                              : 0..1 manual build-up square wave
  pulse_dynamic                      : 0..1 global-dynamics event signal
  natural_dynamic                    : 0..1 smoothed RMS "breathing"
  bass / mid / high                  : LMH RMS levels (small magnitude, ~0..0.3)
  spec[0..N-1]                       : reduced-FFT bin amplitudes (small)

Two signal classes, two treatments
-----------------------------------
  TRANSIENTS (drums_*, pulse_dynamic): already 0..1 from ARE → run a peak-hold
    envelope (instant attack, exponential release) so a 1-frame detection reads
    as a visible decaying pulse instead of a single-frame flicker.
  CONTINUOUS (natural_dynamic, bass/mid/high, spec): low-pass smooth + a slow
    running-max AGC so a quiet track and a loud track both reach a usable 0..1
    range without per-track gain tweaking.

`burst` drives a build/release state: it ramps a `build` signal up while held
(slow attack) and releases fast — the consumer contracts particles during the
build, then a kick on the drop reads as an explosion outward.
"""

import math
import random as _RND

# Force-mode playlist the beat surge steps through (one step per drop, or every
# `mode_every` kicks). REST steps (0) are sprinkled in so the field periodically
# goes calm = downtime/breathing room instead of relentless motion. Mode IDs:
#   0 REST · 1 GATHER · 2 VORTEX · 3 WAVEFORM · 4 CURRENT · 5 FOLD
#   6 SPHERE · 7 TORUS · 8 SHEET · 9 TUNNEL  (shape attractors — particles
#   briefly assume an abstract 3D form, tumbled into a new orientation each time)
# ~1 in 4 steps is a rest; vortex is rare (it read as a cheap twirl). Edit freely.
MODE_SEQ = [1, 3, 0, 6, 9, 0, 7, 5, 0, 8, 4, 0, 9, 6, 0, 1, 7, 0, 9, 8, 0, 3, 5, 0]


def _finite(v, default=0.0):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def env_follow(prev, x, dt, release):
    """Peak-hold envelope: instant attack to x, exponential decay toward 0 with
    time-constant `release` (s). prev/x assumed >= 0."""
    if release <= 0.0:
        return max(0.0, x)
    decayed = prev * math.exp(-dt / release)
    return x if x > decayed else decayed


def smooth(prev, x, dt, tau):
    """One-pole EMA toward x with time-constant `tau` (s)."""
    if tau <= 0.0:
        return x
    a = 1.0 - math.exp(-dt / tau)
    return prev + a * (x - prev)


def agc_normalize(x, running_max, dt, decay, floor):
    """Slow automatic-gain: track a decaying running max and normalise x by it.
    Returns (normalised 0..1, new_running_max). `floor` keeps a silent passage
    from amplifying noise to full scale."""
    rm = running_max * math.exp(-dt / max(decay, 1e-3))
    if x > rm:
        rm = x
    norm = x / rm if rm > floor else 0.0
    return (norm if norm < 1.0 else 1.0), rm


def default_params():
    return {
        # transient release times (s) — bigger = longer visible tail
        "kick_release":   0.16,
        "snare_release":  0.13,
        "hat_release":    0.07,
        "pulse_release":  0.18,
        # continuous smoothing (s)
        "bass_smooth":    0.06,
        "breath_smooth":  0.10,   # natural_dynamic is already smoothed by ARE
        # spectrum bins smoothed harder — per-frame FFT jitter reads as colour
        # flicker when it drives the spectrum colour field.
        "spec_smooth":    0.14,
        # `glow` = the de-flickered BRIGHTNESS driver. Brightness/bloom crossing
        # the bloom threshold every frame is what reads as "blinky"; this is
        # smoothed hard so exposure/soup-brightness swell instead of strobe.
        "glow_smooth":    0.22,
        # drop surge detector (rising-edge in breath = "the drop")
        "drop_thresh":    2.6,    # breath rise rate (1/s) that counts as a drop — higher = less twitchy (relaxed swells don't trigger)
        # ABSOLUTE loudness gate: the AGC normalises a quiet track up to full
        # scale, so relaxed songs read as "loud" and move fast. Scale the drive by
        # the genuine (un-AGC'd) loudness so relaxed = calm, energetic = lively.
        "loud_ref":       0.5,    # natural-dynamic level that counts as "full energy"
        "drop_minlevel":  0.45,   # breath must be at least this high to fire
        "drop_release":   0.55,   # s — how long the shockwave envelope lasts
        "drop_turn":      1.0,    # rad — soup-flow rotation on a real drop (smaller = slower evolution)
        "snare_turn":     0.12,   # rad — tiny soup-flow rotation per snare
        "kick_turn":      0.08,   # rad — soup-flow rotation per KICK (small = slow heading evolution, orientation holds)
        "mode_fade_tau":  0.45,   # s — force eases to 0 and back on each mode switch → smooth transitions, not jumps
        # hue drift (smooth colour, no per-beat blink)
        "hue_step":       0.5,    # rad nudged forward per snare/beat
        "hue_drop":       1.1,    # rad nudged on a drop (bigger colour shift)
        "hue_tau":        0.55,   # s — heavy smoothing so the colour EASES, never snaps
        "beat_smooth":    0.05,   # softens the kick's instant attack into a quick SWELL → organic, less poppy
        # mid-peak (novelty) onset → swirl disturbance
        "mid_base_tau":   0.6,    # slow baseline the mid is compared against (novelty detection)
        "mid_peak_thresh": 0.3,   # how far mid must rise above baseline to count as a peak — higher = swirls fire LESS often (only strong mid hits)
        "mid_release":    0.55,   # s — swirl-burst envelope length (longer = a real swirl, not a flick)
        "bass_base_tau":  1.6,    # slow low-end baseline the kick is compared against
        "blow_thresh":    0.22,   # how far a kick's low-end must exceed baseline to fully blow OUT (vs gather)
        "mode_every":     48,     # advance the force mode every N kicks if no drop arrives first (long dwell = slow evolution, modes STAY)
        "mode_min_dwell": 30,     # a mode must hold at least this many kicks before a drop can switch it → it really settles before changing
        # --- PACING (for slow / atmospheric music) ---
        "trig_interval":  1,      # fire the force SURGE every Nth kick (1 = every kick; 2-4 = sparser, calmer)
        "dur_scale":      1.0,    # multiplies all envelope/hold durations (>1 = longer, more evolving/atmospheric)
        "surge_release":  0.30,   # s — base surge-envelope length (× dur_scale)
        # --- IDLE / LOW-ENERGY evolution (quiet, CHILL, or no music) ---
        # idle fades in PROPORTIONALLY as energy drops below idle_thresh — so even
        # gentle/chill music (sparse beats) keeps getting autonomous morphs/shapes
        # instead of falling into a dead zone between "loud enough to react" and
        # "silent enough to idle". Raise idle_thresh to make more music count as chill.
        "idle_thresh":    0.42,   # energy below this fades idle IN (chill music included)
        "idle_amt":       0.5,    # strength of the idle drive (gentle surges + mode shaping)
        "idle_rate":      0.07,   # Hz — slow idle pulse rate (breaths per second)
        "idle_mode_secs": 22.0,   # advance the force mode every N seconds while idle (slow — a shape holds ~20s before morphing)
        "idle_cycle_min": 0.25,   # idle fade above this advances modes + cycles (so chill, not just silent, evolves)
        # --- BREATHING ROOM (dynamics-driven minimum time between surges) ---
        "surge_cd_min":   0.22,   # s — min gap between surges at full intensity (responsive)
        "surge_cd_calm":  1.6,    # s — EXTRA gap added when calm (low energy) → space to develop, not jerky
        # --- SIGNAL LIVENESS (robustness to dropout / flatline / DC) ---
        "alive_tau":      0.5,    # s — smoothing of the raw-input "is it changing?" measure
        "alive_floor":    0.006,  # activity below this = flatlined/dead → fade audio out, go idle
        "alive_attack":   0.3,    # s — how fast the layer comes back when the signal returns
        # LMH material smoothing (vessel "what is the substance made of")
        "mid_smooth":     0.10,   # circulation responds at body-flow rate
        "high_smooth":    0.06,   # surface detail, a touch faster
        "pressure_smooth": 0.18,  # low·breath → slow tidal pressure
        "surface_cap":    0.7,    # cap high-band response (cheap if uncapped)
        # AGC: slow running-max so quiet/loud tracks both normalise
        "agc_decay":      6.0,    # s — how fast the tracked max forgets
        "agc_floor":      0.004,  # below this, treat as silence (no amplification)
        # build/release from the burst square wave
        "build_attack":   0.45,   # s — slow ramp up while burst held
        "build_release":  0.12,   # s — fast release on drop
    }


def fresh_state(n_spec=15):
    """Persistent state carried across cooks (stored on the COMP in TD)."""
    return {
        "kick": 0.0, "snare": 0.0, "hat": 0.0, "pulse": 0.0,
        "bass": 0.0, "breath": 0.0, "build": 0.0,
        # de-flickered brightness driver + drop surge state
        "glow": 0.0, "drop": 0.0, "dropdir": 0.0, "prev_breath": 0.0,
        "prev_snare": 0.0, "prev_kick": 0.0, "beat": 0.0,
        # mid-peak disturbance (swirl) — onset detection off the continuous mid band
        "midhit": 0.0, "mid_base": 0.0,
        # beat polarity: DYNAMICS-driven, continuous −1..+1 (suck-in ↔ blow-out)
        # from how hard the kick lands vs the recent low-end baseline.
        "beat_count": 0, "beatpolarity": 1.0, "bass_base": 0.0,
        # FORCE MODE: index into MODE_SEQ (resolved id in `forcemode`). Steps on
        # drops + a beat-count fallback; REST steps give occasional downtime.
        "forcemode": float(MODE_SEQ[0]), "seq_idx": 0, "last_switch": -999,
        # smooth mode transitions: force dips to 0 and eases back on each switch.
        "mode_fade": 1.0, "prev_forcemode": float(MODE_SEQ[0]),
        # random seed re-rolled each mode/effect occurrence → every instance of a
        # mode looks a bit different (orientation / size / radius / angle jitter).
        "seed": 0.5,
        # smoothed, ACCUMULATING hue — beats nudge it forward and it EASES there
        # (never snaps back), so colour drifts instead of blinking on every hit.
        "hue_accum": 0.0, "hue_smooth": 0.0,
        # gated force surge + idle evolution state
        "surge": 0.0, "trig_count": 0, "modedrive": 0.0,
        "idle_phase": 0.0, "idle_mode_timer": 0.0,
        # dynamics-driven breathing room between surges
        "time_acc": 0.0, "last_surge_t": -999.0,
        # signal-liveness: a flatlined / DC / absent input must NOT read as "loud"
        # (the AGC would normalise a constant to full scale). Track how much the
        # raw input is actually CHANGING; fade the audio layer out when it's flat.
        "prev_raw": {}, "activity": 0.0,
        # LMH material / vessel mood (low/mid/high → pressure/circulation/surface)
        "mid": 0.0, "high": 0.0,
        "pressure": 0.0, "circulation": 0.0, "surface": 0.0,
        "spec": [0.0] * n_spec,
        # AGC running maxima
        "max_bass": 0.0, "max_mid": 0.0, "max_high": 0.0,
        # ONE shared max across all spectrum bins so relative bin heights are
        # preserved (the equalizer shape) instead of each bin self-normalising
        # to full scale and flattening the spectrum.
        "max_spec": 0.0,
    }


# Canonical output channel order (TD Script CHOP appends in this order).
def output_names(n_spec=15):
    return (["kick", "snare", "hat", "pulse", "bass", "breath", "build",
             "glow", "drop", "dropdir",
             "mid", "high", "pressure", "circulation", "surface", "beat",
             "midhit", "beatpolarity", "forcemode", "surge", "modedrive", "seed", "hue"]
            + ["spec%d" % i for i in range(n_spec)])


def process(state, features, dt, params):
    """Advance one cook. Mutates `state` in place, returns a flat dict of
    modulation outputs in [0..1] (pre-depth — the TD wrapper applies depth/master).

    features: dict with any of the ARE feature keys (missing → 0). `spec` is a
              list of bin amplitudes.
    dt:       seconds since previous cook.
    """
    dt = _finite(dt, 0.0)
    if dt <= 0.0:
        dt = 1.0 / 60.0
    state["time_acc"] = state.get("time_acc", 0.0) + dt

    g = lambda k: _finite(features.get(k, 0.0), 0.0)
    dur = max(0.05, float(params.get("dur_scale", 1.0)))   # duration multiplier

    # ---- SIGNAL LIVENESS -----------------------------------------------
    # Sum how much the raw input CHANGED this cook. A flatlined / DC / absent
    # signal has ~zero change → `alive` fades to 0 (the AGC would otherwise
    # normalise a constant to full scale and read it as "loud" forever). Fast
    # attack when the signal returns, slower release so brief gaps don't kill it.
    _alive_keys = ("drums_low", "drums_mid", "drums_high", "bass", "mid", "high",
                   "natural_dynamic", "pulse_dynamic")
    _act = 0.0
    _pr = state["prev_raw"]
    for _k in _alive_keys:
        _cur = g(_k)
        _act += abs(_cur - _pr.get(_k, _cur))
        _pr[_k] = _cur
    _tau = params["alive_attack"] if _act > state["activity"] else params["alive_tau"]
    state["activity"] = smooth(state["activity"], _act, dt, _tau)
    alive = max(0.0, min(1.0, state["activity"] / max(params["alive_floor"], 1e-6)))

    # ---- transients: peak-hold envelopes (release × dur_scale) ---------
    state["kick"]  = env_follow(state["kick"],  g("drums_low"),     dt, params["kick_release"]  * dur)
    state["snare"] = env_follow(state["snare"], g("drums_mid"),     dt, params["snare_release"] * dur)
    state["hat"]   = env_follow(state["hat"],   g("drums_high"),    dt, params["hat_release"]   * dur)
    state["pulse"] = env_follow(state["pulse"], g("pulse_dynamic"), dt, params["pulse_release"] * dur)

    # ---- continuous bands: AGC-normalise then smooth -------------------
    bass_n, state["max_bass"] = agc_normalize(
        g("bass"), state["max_bass"], dt, params["agc_decay"], params["agc_floor"])
    state["bass"] = smooth(state["bass"], bass_n, dt, params["bass_smooth"])
    # slow low-end baseline → lets us tell a HARD kick from an average one.
    state["bass_base"] = smooth(state["bass_base"], state["bass"], dt, params["bass_base_tau"])

    # mid / high bands (AGC + smooth) — the "material" signals.
    mid_n, state["max_mid"] = agc_normalize(
        g("mid"), state["max_mid"], dt, params["agc_decay"], params["agc_floor"])
    state["mid"] = smooth(state["mid"], mid_n, dt, params["mid_smooth"])
    high_n, state["max_high"] = agc_normalize(
        g("high"), state["max_high"], dt, params["agc_decay"], params["agc_floor"])
    state["high"] = smooth(state["high"], high_n, dt, params["high_smooth"])

    # mid-PEAK onset (novelty): compare mid to a slow baseline; a rise above it
    # fires a swirl-burst envelope. A SECOND disturbance, distinct from the kick
    # gather — driven by the mids, so busy mid sections add organic swirls.
    state["mid_base"] = smooth(state["mid_base"], state["mid"], dt, params["mid_base_tau"])
    if (state["mid"] - state["mid_base"] > params["mid_peak_thresh"]
            and state["midhit"] < 0.4):
        state["midhit"] = 1.0
    else:
        state["midhit"] = state["midhit"] * math.exp(-dt / max(params["mid_release"], 1e-3))

    # natural_dynamic is already 0..1 + smoothed by ARE; light smooth only.
    prev_breath = state["breath"]
    state["breath"] = smooth(state["breath"], g("natural_dynamic"), dt, params["breath_smooth"])

    # ---- glow: de-flickered BRIGHTNESS driver --------------------------
    # Brightness/bloom must NOT strobe on every kick (crossing the bloom
    # threshold reads as flicker). Drive it from a HARD-smoothed blend of the
    # sustained breath + a little transient energy so it swells, never blinks.
    glow_target = max(state["breath"], 0.5 * state["kick"], 0.4 * state["hat"])
    state["glow"] = smooth(state["glow"], glow_target, dt, params["glow_smooth"])

    # `beat` = the kick shaped into an organic SWELL (softened attack) — used for
    # the on-beat motion surge so it breathes instead of popping.
    state["beat"] = smooth(state["beat"], state["kick"], dt, params["beat_smooth"])

    # ---- drop: rising-edge surge detector ("the drop") -----------------
    # A drop = a fast sustained energy jump. Detect a steep rise in breath above
    # a level; fire a 1.0 impulse, then decay over drop_release. Re-arms only
    # after the envelope falls — so one drop = one shockwave, not a stutter.
    rise = (state["breath"] - prev_breath) / dt
    if (rise > params["drop_thresh"] and state["breath"] > params["drop_minlevel"]
            and state["drop"] < 0.35):
        state["drop"] = 1.0
        state["hue_accum"] += params["hue_drop"]   # bigger colour shift on a drop
        # each drop rotates the soup-flow direction → the disturbance visibly
        # changes heading on the drop (wrapped to keep the float bounded).
        state["dropdir"] = math.fmod(state["dropdir"] + params["drop_turn"], 6.2831853)
        # and STEPS the force-mode playlist — but only if the current mode has
        # held its minimum dwell, so frequent drops can't strobe the modes faster
        # than they can be read.
        if state["beat_count"] - state["last_switch"] >= int(params["mode_min_dwell"]):
            state["seq_idx"] = (int(state["seq_idx"]) + 1) % len(MODE_SEQ)
            state["forcemode"] = float(MODE_SEQ[state["seq_idx"]])
            state["last_switch"] = state["beat_count"]
    else:
        state["drop"] = state["drop"] * math.exp(-dt / max(params["drop_release"], 1e-3))

    # snare onset → SMALL soup-flow direction nudge (subtle, frequent). Rising
    # edge of the snare envelope through 0.5 = one nudge per hit, not per frame.
    if state["snare"] > 0.5 and state["prev_snare"] <= 0.5:
        state["dropdir"] = math.fmod(state["dropdir"] + params["snare_turn"], 6.2831853)
    state["prev_snare"] = state["snare"]
    # kick onset → soup-flow direction nudge too (the kick is the reliable beat
    # when ARE's mid/high drum detectors aren't firing).
    if state["kick"] > 0.5 and state["prev_kick"] <= 0.5:
        state["beat_count"] += 1
        state["trig_count"] += 1
        # PACING: only every Nth kick is a SURGE beat — for slow/atmospheric
        # music raise trig_interval so the field isn't shoved on every hit.
        interval = max(1, int(params.get("trig_interval", 1)))
        # dynamics-driven breathing room: when calm (low energy) require a LONGER
        # gap between surges so patterns get time to develop and it never hastes;
        # when intense, the gap shrinks so it stays responsive.
        energy_now = max(state["glow"], state["breath"])
        cooldown   = params["surge_cd_min"] + (1.0 - energy_now) * params["surge_cd_calm"]
        if (state["trig_count"] % interval == 0
                and (state["time_acc"] - state["last_surge_t"]) >= cooldown):
            state["last_surge_t"] = state["time_acc"]
            state["surge"] = 1.0    # fire the force-surge envelope
            state["dropdir"] = math.fmod(state["dropdir"] + params["kick_turn"], 6.2831853)
            # DYNAMICS-driven polarity: a kick landing harder than the recent
            # low-end baseline blows OUT; average/soft gathers IN. Continuous.
            nov  = state["bass"] - state["bass_base"]
            blow = max(0.0, min(1.0, nov / max(params["blow_thresh"], 1e-3)))
            # asymmetric: full gather-IN (+1), but blow-OUT capped gentle (down to
            # ~−0.4) so it never evacuates a big pillowy negative-space hole.
            state["beatpolarity"] = 1.0 - 1.4 * blow
            state["hue_accum"] += params["hue_step"]   # drift colour forward on the beat
        # beat-count fallback: advance the force mode periodically even without
        # drops, so a steady non-dropping track still explores all modes.
        mev = max(2, int(params["mode_every"]))
        if state["beat_count"] % mev == 0:
            state["seq_idx"] = (int(state["seq_idx"]) + 1) % len(MODE_SEQ)
            state["forcemode"] = float(MODE_SEQ[state["seq_idx"]])
            state["last_switch"] = state["beat_count"]
    state["prev_kick"] = state["kick"]
    # a real drop blows OUT, but gently (−0.5) so it doesn't punch a hole.
    if state["drop"] > 0.5:
        state["beatpolarity"] = -0.5
    # hue eases toward its accumulated target → smooth colour drift, no blink.
    state["hue_smooth"] = smooth(state["hue_smooth"], state["hue_accum"], dt, params["hue_tau"])
    # surge envelope decays (length × dur_scale → longer = more atmospheric).
    state["surge"] = state["surge"] * math.exp(-dt / max(params["surge_release"] * dur, 1e-3))

    # ---- IDLE evolution: when energy is low (quiet / no music), fade in a
    # gentle autonomous drive so the field keeps breathing + slowly cycling
    # through modes/shapes instead of going dead. Fades out as audio energy rises.
    # energy gated by liveness: a flat/dead signal → energy 0 → idle fully takes
    # over (no stuck-loud AGC churn).
    energy = max(state["glow"], state["breath"], state["bass"]) * alive
    idlef  = max(0.0, min(1.0, 1.0 - energy / max(params["idle_thresh"], 1e-3)))
    state["idle_phase"] = math.fmod(state["idle_phase"] + dt * params["idle_rate"], 1.0)
    idle_pulse = idlef * params["idle_amt"] * (0.5 + 0.5 * math.sin(state["idle_phase"] * 6.2831853))
    state["idle_mode_timer"] += dt
    if idlef > params["idle_cycle_min"] and state["idle_mode_timer"] >= params["idle_mode_secs"]:
        state["idle_mode_timer"] = 0.0
        state["seq_idx"] = (int(state["seq_idx"]) + 1) % len(MODE_SEQ)
        state["forcemode"] = float(MODE_SEQ[state["seq_idx"]])

    # ---- smooth MODE TRANSITIONS: when the force mode changes, dip the drive to
    # 0 and ease it back so the field flows between modes instead of jumping.
    if state["forcemode"] != state["prev_forcemode"]:
        state["mode_fade"] = 0.0
        state["prev_forcemode"] = state["forcemode"]
        state["seed"] = _RND.random()    # fresh randomness for the new occurrence
    state["mode_fade"] = smooth(state["mode_fade"], 1.0, dt, params["mode_fade_tau"])

    # surge the force layer sees = max(audio surge, idle pulse); modedrive = the
    # sustained shaping drive = max(audio energy, idle baseline). Both eased by
    # the mode-transition fade.
    # ABSOLUTE loudness (un-AGC'd) → relaxed songs stay genuinely calm; only real
    # energy drives fast motion. breath is the smoothed natural-dynamic (0..1).
    loud = max(0.0, min(1.0, state["breath"] / max(params["loud_ref"], 1e-3))) * alive
    surge_out = max(state["surge"] * loud, idle_pulse) * state["mode_fade"]
    state["modedrive"] = max(state["glow"] * loud, idlef * params["idle_amt"] * 0.7) * state["mode_fade"]

    # ---- build / release from the burst square wave --------------------
    burst = g("burst")
    tau = params["build_attack"] if burst > state["build"] else params["build_release"]
    state["build"] = smooth(state["build"], burst, dt, tau)

    # ---- spectrum: GLOBAL AGC (shared max) + per-bin smooth ------------
    # Normalise every bin by the SAME decaying max so the spectral SHAPE
    # (relative bin heights) is preserved — that shape is the equalizer look.
    spec = features.get("spec", []) or []
    n = len(state["spec"])
    peak = 0.0
    for i in range(len(spec)):
        xv = _finite(spec[i], 0.0)
        if xv > peak:
            peak = xv
    rm = state["max_spec"] * math.exp(-dt / max(params["agc_decay"], 1e-3))
    if peak > rm:
        rm = peak
    state["max_spec"] = rm
    denom = rm if rm > params["agc_floor"] else 0.0
    for i in range(n):
        x = _finite(spec[i], 0.0) if i < len(spec) else 0.0
        sn = (x / denom) if denom > 0.0 else 0.0
        if sn > 1.0:
            sn = 1.0
        state["spec"][i] = smooth(state["spec"][i], sn, dt, params["spec_smooth"])

    # ---- vessel MOOD: "what is the trapped substance made of" ----------
    # Low = pressure/mass (bass·breath, slow tidal). Mid = circulation (body
    # flow). High = surface agitation (capped — cheap if uncapped). These drive
    # the PHYSICS of the material inside the logo vessel, never the visuals.
    state["pressure"]    = smooth(state["pressure"], state["bass"] * state["breath"],
                                  dt, params["pressure_smooth"])
    state["circulation"] = state["mid"]
    state["surface"]     = min(state["high"], params["surface_cap"])

    out = {
        "kick": state["kick"], "snare": state["snare"], "hat": state["hat"],
        "pulse": state["pulse"], "bass": state["bass"], "breath": state["breath"],
        "build": state["build"], "glow": state["glow"], "drop": state["drop"],
        "dropdir": state["dropdir"], "mid": state["mid"], "high": state["high"],
        "pressure": state["pressure"], "circulation": state["circulation"],
        "surface": state["surface"], "beat": state["beat"],
        "midhit": state["midhit"], "beatpolarity": state["beatpolarity"],
        "forcemode": state["forcemode"],
        "surge": surge_out, "modedrive": state["modedrive"],
        "seed": state["seed"],
        "hue": math.fmod(state["hue_smooth"], 6.2831853),
    }
    for i in range(n):
        out["spec%d" % i] = state["spec"][i]

    # Fade every audio-MAGNITUDE channel with signal liveness so a flatlined / DC
    # / absent input can't drive constant motion (the AGC would read a constant as
    # full scale). Control channels (forcemode/dropdir/beatpolarity) and the
    # idle-blended surge/modedrive are excluded — they're handled above.
    for k in ("kick", "snare", "hat", "pulse", "bass", "breath", "build", "glow",
              "mid", "high", "pressure", "circulation", "surface", "beat",
              "midhit", "drop"):
        out[k] *= alive
    for i in range(n):
        out["spec%d" % i] *= alive
    return out


# ---------------------------------------------------------------------------
# Self-test: synthetic signal. Run `python3 audio_logic.py`.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    p = default_params()
    st = fresh_state(4)
    dt = 1.0 / 60.0

    # NOTE: these check the internal STATE (ungated envelope/AGC logic). The
    # output dict is faded by signal-liveness (test 7e), so constant test inputs
    # would read as "dead" at the output — the math itself lives in `state`.

    # 1) A single 1-frame kick should produce a decaying envelope, not a blip.
    process(st, {"drums_low": 1.0}, dt, p)
    assert abs(st["kick"] - 1.0) < 1e-9, st["kick"]
    prev = st["kick"]
    for _ in range(5):
        process(st, {}, dt, p)  # no input → decay
        assert st["kick"] < prev, "kick env must decay"
        prev = st["kick"]
    assert prev > 0.4, "kick tail should still be visible after ~5 frames"

    # 2) AGC: a steady small bass settles near 1.0 (normalised by its own max).
    st2 = fresh_state(4)
    for _ in range(240):
        process(st2, {"bass": 0.05}, dt, p)
    assert st2["bass"] > 0.8, ("AGC should lift steady small bass toward 1", st2["bass"])

    # 3) A quiet passage after a loud one decays the running max (AGC forgets).
    for _ in range(60):
        process(st2, {"bass": 0.5}, dt, p)   # loud → max rises
    big_max = st2["max_bass"]
    for _ in range(600):                          # ~10s silence
        process(st2, {"bass": 0.0}, dt, p)
    assert st2["max_bass"] < big_max, "running max must decay during silence"
    assert st2["bass"] < 0.05, "bass should fall to ~0 in silence"

    # 4) Build ramps up slowly while burst held, releases fast.
    st3 = fresh_state(4)
    for _ in range(30):
        process(st3, {"burst": 1.0}, dt, p)
    held = st3["build"]
    assert held > 0.3, ("build should ramp while held", held)
    process(st3, {"burst": 0.0}, dt, p)
    process(st3, {"burst": 0.0}, dt, p)
    assert st3["build"] < held, "build should release when burst drops"

    # 5) Spectrum bins normalise (shared max → relative shape preserved).
    st4 = fresh_state(4)
    for _ in range(240):
        process(st4, {"spec": [0.02, 0.10, 0.0, 0.05]}, dt, p)
    assert st4["spec"][1] > st4["spec"][0] > 0.0, "louder bin should read higher"
    assert st4["spec"][2] < 0.05, "silent bin stays low"

    # 6) glow is smoother than a raw kick: a single kick must not spike glow.
    st_g = fresh_state(4)
    process(st_g, {"drums_low": 1.0}, dt, p)
    assert st_g["kick"] > 0.9 and st_g["glow"] < 0.3, \
        ("glow must not strobe with the kick", st_g["glow"])

    # 7) drop fires on a fast breath rise, then decays; dropdir advances once.
    st_d = fresh_state(4)
    for _ in range(30):                       # settle low
        process(st_d, {"natural_dynamic": 0.05}, dt, p)
    dir0 = st_d["dropdir"]
    o = None
    for _ in range(20):                       # fast sustained rise = the drop
        o = process(st_d, {"natural_dynamic": 0.9}, dt, p)
    assert st_d["drop"] > 0.3, ("drop should fire on a surge", st_d["drop"])
    assert abs(st_d["dropdir"] - dir0) > 0.5, "dropdir should rotate on the drop"
    fired_dir = st_d["dropdir"]
    for _ in range(120):                      # hold high → no re-fire, decays
        process(st_d, {"natural_dynamic": 0.9}, dt, p)
    assert st_d["drop"] < 0.2, "drop must decay while energy stays flat (no stutter)"
    assert abs(st_d["dropdir"] - fired_dir) < 1e-6, "dropdir must not advance without a new drop"

    # 7b) vessel mood: mid→circulation, high→surface (capped), low·breath→pressure.
    # (check STATE — a constant test input is "flatlined" so the output is faded.)
    st_m = fresh_state(4)
    for _ in range(240):
        process(st_m, {"mid": 0.2, "high": 0.5, "bass": 0.2,
                       "natural_dynamic": 0.8}, dt, p)
    assert st_m["circulation"] > 0.5, ("mid should drive circulation", st_m["circulation"])
    assert st_m["surface"] <= p["surface_cap"] + 1e-9, "surface must be capped"
    assert st_m["pressure"] > 0.3, ("low·breath should build pressure", st_m["pressure"])

    # 7c) PACING: trig_interval gates the surge — every Nth kick fires one.
    def surge_fires(interval, n_onsets):
        pp = dict(p); pp['trig_interval'] = interval
        pp['idle_amt'] = 0.0; pp['surge_release'] = 0.05
        st = fresh_state(4); fires = 0
        for _ in range(n_onsets):
            before = st["surge"]
            process(st, {"drums_low": 1.0, "natural_dynamic": 0.9}, dt, pp)   # onset
            if st["surge"] > 0.5 and before < 0.5:   # surge jumped (decays same cook)
                fires += 1
            for _ in range(60):
                process(st, {"natural_dynamic": 0.9}, dt, pp)                 # release
        return fires
    assert surge_fires(1, 8) == 8, surge_fires(1, 8)
    assert surge_fires(2, 8) == 4, surge_fires(2, 8)
    assert surge_fires(4, 8) == 2, surge_fires(4, 8)

    # 7d) IDLE: with NO audio, an autonomous gentle surge keeps evolving.
    st_i = fresh_state(4); mx = 0.0
    for _ in range(2500):
        o = process(st_i, {}, dt, p)
        mx = max(mx, o["surge"])
    assert mx > 0.05, ("idle should produce gentle autonomous surges", mx)
    assert o["modedrive"] > 0.05, ("idle should sustain mode shaping", o["modedrive"])

    # 7e) LIVENESS: a flatlined / DC input must FADE OUT (not read as loud via the
    # AGC) and hand over to idle. A varying input reads alive.
    import random as _r; _r.seed(1)
    st_f = fresh_state(4); o = None
    for _ in range(300):                       # varying = alive
        o = process(st_f, {"bass": 0.3 + 0.2 * _r.random(),
                           "natural_dynamic": 0.5 + 0.3 * _r.random()}, dt, p)
    assert o["bass"] > 0.2, ("varying signal should read alive", o["bass"])
    for _ in range(400):                       # now FLATLINE at constant DC
        o = process(st_f, {"bass": 0.3, "natural_dynamic": 0.5}, dt, p)
    assert o["bass"] < 0.05, ("flatlined DC must NOT read as loud", o["bass"])
    assert o["modedrive"] > 0.05, ("idle should take over on flatline", o["modedrive"])

    # 6b) NaN resilience.
    st5 = fresh_state(4)
    o = process(st5, {"drums_low": float("nan"), "bass": float("inf")}, dt, p)
    for k, v in o.items():
        assert math.isfinite(v), (k, v)

    print("OK — audio_logic: kick envelope decay, bass AGC lift + forget, "
          "build ramp/release, per-bin spectrum AGC, NaN resilience all pass.")
