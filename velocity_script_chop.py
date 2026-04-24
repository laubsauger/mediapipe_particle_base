# velocity_script_chop.py
# =======================
# Script CHOP callback for the velocity_controller. Thin wrapper around
# velocity_logic — reads landmark channels, maintains per-landmark state
# across cooks (in parent COMP storage), emits per-landmark output channels.
#
# Same convention as painting_script_chop: parameters live on the enclosing
# Base COMP, nothing on the Script CHOP itself.
#
# Required siblings inside the Base COMP:
#   - Text DAT `velocity_logic` (contents = velocity_logic.py)
#   - A Script CHOP that references this DAT as its Callbacks DAT
#   - A downstream Lag CHOP doing the smoothing (lag time = parent.Blendtime)
#
# Input channel contract (rename upstream via Select CHOP / Rename CHOP to
# match — these are MediaPipe's standard pose channel names):
#   <L>:x, <L>:y                (required, 0..1 source space)
#   <L>:visible                 (optional, 0..1 confidence from MediaPipe)
# for each landmark in parent.par.Landmarks (comma-separated).
#
# Default landmarks: left_wrist, right_wrist, left_ankle, right_ankle, nose
#
# Output channel contract (pre-Lag):
#   <L>:x   <L>:y                pass-through position
#   <L>:vx  <L>:vy               smoothed velocity, 1/s in 0..1 space
#   <L>:speed                    |v|
#   <L>:accel                    smoothed |a|
#   <L>:emit                     0..1 emission rate (speed / Speedscale)
#   <L>:burst                    0..1 burst envelope
#   <L>:visible                  0 / 1
#   total_motion                 sum of speeds across landmarks
#   total_burst                  sum of burst envelopes
#   frame_dt                     seconds since last cook (diagnostic)

STORAGE_KEY = 'velocity_state'
STORAGE_TIME_KEY = 'velocity_last_t'


def _find_chan(scriptOp, name):
    for cin in scriptOp.inputs:
        if cin is None:
            continue
        c = cin[name]
        if c is not None:
            return c
    return None


def _read(scriptOp, name, default=0.0):
    c = _find_chan(scriptOp, name)
    if c is None:
        return default
    return float(c[0])


def _landmark_list(par_value):
    """Parse parent.par.Landmarks (comma/space separated) into a tuple."""
    if par_value is None:
        return None
    items = [s.strip() for s in str(par_value).replace(',', ' ').split()]
    items = [s for s in items if s]
    return tuple(items) if items else None


def onCook(scriptOp):
    scriptOp.clear()
    if scriptOp.isTimeSlice:
        scriptOp.isTimeSlice = False

    logic = mod.velocity_logic
    comp = parent()
    par = comp.par

    # ---- Landmark set ----------------------------------------------------
    # Optional parent par 'Landmarks' lets an experiment fork override without
    # editing code. Falls back to the module default.
    landmarks = _landmark_list(getattr(par, 'Landmarks', None) and par.Landmarks.eval()) \
                or logic.LANDMARKS

    # ---- State (survives cook-to-cook, cleared on reload) ----------------
    state = comp.fetch(STORAGE_KEY, None)
    if state is None or set(state.keys()) != set(landmarks):
        # Landmarks changed (or first cook) — rebuild.
        state = logic.new_state(landmarks)
    else:
        # Same landmarks, but the inner schema may have grown since this
        # session last stored it (e.g. `last_good_x` added after an update).
        # ensure_schema backfills any missing keys with defaults.
        logic.ensure_schema(state, landmarks)
    comp.store(STORAGE_KEY, state)

    # ---- dt from absTime -------------------------------------------------
    now = absTime.seconds
    last_t = comp.fetch(STORAGE_TIME_KEY, None)
    if last_t is None:
        dt = 1.0 / max(me.time.rate, 1.0)  # seed with a nominal frame time
    else:
        dt = max(0.0, now - last_t)
        # Cap runaway dt after pauses/reloads so the first frame doesn't
        # produce a huge spurious acceleration.
        if dt > 0.25:
            dt = 1.0 / max(me.time.rate, 1.0)
            logic.reset_state(state)
    comp.store(STORAGE_TIME_KEY, now)

    # ---- Parameters ------------------------------------------------------
    vis_thresh = par.Visibilitythreshold.eval()
    # Trustthreshold is stricter — only frames at or above this confidence
    # update last_good and run velocity math. Between the two thresholds
    # the landmark is "visible but not trusted" (pinned to last_good, but
    # emit still fires). Clamp trust >= gate so the zones don't invert.
    trust_thresh = par.Trustthreshold.eval() if hasattr(par, 'Trustthreshold') else max(vis_thresh, 0.75)
    if trust_thresh < vis_thresh:
        trust_thresh = vis_thresh

    params = {
        "velocity_smooth": par.Velocitysmooth.eval(),
        "accel_smooth":    par.Accelsmooth.eval(),
        "speed_scale":     par.Speedscale.eval(),
        "accel_threshold": par.Accelthreshold.eval(),
        "accel_scale":     par.Accelscale.eval(),
        "burst_decay":     par.Burstdecay.eval(),
        "max_jump":        par.Maxjump.eval() if hasattr(par, 'Maxjump') else 0.3,
    }

    # ---- Build samples dict ---------------------------------------------
    samples = {}
    for lm in landmarks:
        xch = _find_chan(scriptOp, f'{lm}:x')
        ych = _find_chan(scriptOp, f'{lm}:y')
        if xch is None or ych is None:
            # Missing position channel -> treat as invisible but still decay.
            samples[lm] = (0.0, 0.0, False, False)
            continue
        x = float(xch[0])
        y = float(ych[0])
        vch = _find_chan(scriptOp, f'{lm}:visible')
        # :visible is MediaPipe's 0..1 confidence. Two zones:
        #   visible = confidence >= vis_thresh   (output gate)
        #   trusted = confidence >= trust_thresh (commit last_good & velocity)
        # If the channel isn't present, assume fully visible and trusted.
        if vch is not None:
            v = float(vch[0])
            samples[lm] = (x, y, v >= vis_thresh, v >= trust_thresh)
        else:
            samples[lm] = (x, y, True, True)

    # ---- Update logic ----------------------------------------------------
    per_landmark, globals_out = logic.update(state, samples, dt, params)

    # ---- Emit channels ---------------------------------------------------
    scriptOp.numSamples = 1
    scriptOp.rate = me.time.rate

    for lm in landmarks:
        o = per_landmark[lm]
        for suffix in logic.PER_LANDMARK_CHANS:
            scriptOp.appendChan(f'{lm}:{suffix}')[0] = o[suffix]

    for g in logic.GLOBAL_CHANS:
        scriptOp.appendChan(g)[0] = globals_out[g]

    return
