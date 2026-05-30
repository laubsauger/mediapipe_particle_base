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
#   <L>:z                       (optional, MediaPipe depth — hip-centered,
#                                negative = toward camera. Missing -> 0.)
#   <L>:visible                 (optional, 0..1 confidence from MediaPipe)
# for each landmark in parent.par.Landmarks (comma-separated).
#
# Default landmarks: left_wrist, right_wrist, left_ankle, right_ankle, nose
#
# Output channel contract (pre-Lag):
#   <L>:x   <L>:y   <L>:z        pass-through position (3D)
#   <L>:vx  <L>:vy  <L>:vz       smoothed velocity, 1/s in MediaPipe-space
#   <L>:speed                    3D magnitude sqrt(vx²+vy²+vz²)
#   <L>:accel                    smoothed |a|
#   <L>:emit                     0..1 emission rate (speed / Speedscale)
#   <L>:burst                    0..1 burst envelope
#   <L>:visible                  0 / 1
#   total_motion                 sum of speeds across landmarks
#   total_burst                  sum of burst envelopes
#   frame_dt                     seconds since last cook (diagnostic)

STORAGE_KEY = 'velocity_state'
STORAGE_TIME_KEY = 'velocity_last_t'


import math


def _find_chan(scriptOp, name):
    for cin in scriptOp.inputs:
        if cin is None:
            continue
        c = cin[name]
        if c is not None:
            return c
    return None


def _finite(v, default):
    """Coerce v to a finite float, substituting `default` for NaN/Inf.
    MediaPipe occasionally emits NaN (invisible joints, tracker restart);
    we reject them at the boundary so nothing NaN ever enters the logic
    or the Lag CHOP."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def _read(scriptOp, name, default=0.0):
    c = _find_chan(scriptOp, name)
    if c is None:
        return default
    return _finite(c[0], default)


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
    try:
        bl = mod.body_logic        # for MAX_PERSONS
        persons = bl.MAX_PERSONS
    except Exception:
        persons = 1                # single-person fallback if body_logic missing
    comp = parent()
    par = comp.par

    # ---- Landmark set ----------------------------------------------------
    landmarks = _landmark_list(getattr(par, 'Landmarks', None) and par.Landmarks.eval()) \
                or logic.LANDMARKS

    # ---- State (nested {person: {lm: state}}; survives cook-to-cook) ----
    state = comp.fetch(STORAGE_KEY, None)
    if (not isinstance(state, dict)
            or set(state.keys()) != set(range(persons))
            or any(set(ps.keys()) != set(landmarks)
                   for ps in state.values() if isinstance(ps, dict))):
        state = logic.new_state(landmarks, persons=persons)
    else:
        logic.ensure_schema(state, landmarks, persons=persons)
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
        "settle_frames":   int(par.Settleframes.eval()) if hasattr(par, 'Settleframes') else 5,
        "z_speed_weight":  par.Zspeedweight.eval() if hasattr(par, 'Zspeedweight') else 0.35,
        # Aspect = box width/height (16:9). MediaPipe normalises x by image
        # WIDTH and y by HEIGHT, so equal physical motion gives a smaller vx
        # than vy → a vertical bias in velocity/emission. Scaling vx by aspect
        # makes the velocity isotropic (horizontal motion reads as strong as
        # vertical). 1.0 = no correction.
        "aspect": ((par.Boundsmaxx.eval() / par.Boundsmaxy.eval())
                   if (hasattr(par, 'Boundsmaxx') and hasattr(par, 'Boundsmaxy')
                       and par.Boundsmaxy.eval()) else 1.0),
    }

    # ---- Build NESTED multi-person samples directly from in_pose --------
    # Channel-name resolution (per-person prefix + legacy fallback for p=0)
    # is centralised in body_logic — see per_person_chans / per_person_vis_chans.
    try:
        bl = mod.body_logic
        name_to_mp = {}
        try:
            import sys as _sys
            if project.folder not in _sys.path:
                _sys.path.append(project.folder)
            from adapters import contract as _ct
            name_to_mp = _ct.INDEX_OF
        except Exception:
            pass
    except Exception:
        bl = None
        name_to_mp = {}
    ip = op('in_pose')

    _SENTINEL = object()
    def _read(names):
        if ip is None or bl is None:
            return _SENTINEL
        # body_logic.read_first returns default when nothing matches; we want
        # to distinguish "no channel found" (treat as fully-visible/trusted)
        # from "channel present, value 0".
        for nm in names:
            try:
                c = ip[nm]
            except Exception:
                continue
            if c is None:
                continue
            return _finite(c[0], 0.0)
        return _SENTINEL

    samples = {}
    for p in range(persons):
        person_samples = {}
        for lm in landmarks:
            mp_idx = name_to_mp.get(lm)
            x = _read(bl.per_person_chans(p, lm, 'x') if bl else [])
            y = _read(bl.per_person_chans(p, lm, 'y') if bl else [])
            z = _read(bl.per_person_chans(p, lm, 'z') if bl else [])
            v_raw = _read(bl.per_person_vis_chans(p, mp_idx, lm) if (bl and mp_idx is not None) else [])
            x = x if x is not _SENTINEL else 0.0
            y = y if y is not _SENTINEL else 0.0
            z = z if z is not _SENTINEL else 0.0
            if v_raw is _SENTINEL:
                # No visibility channel for this person → person not tracked.
                # Default to NOT visible (was True — caused phantom persons 1..3
                # to read visible=1 in lag1 and pollute the emitter texture).
                person_samples[lm] = (x, y, z, False, False)
            else:
                person_samples[lm] = (x, y, z,
                                      v_raw >= vis_thresh,
                                      v_raw >= trust_thresh)
        samples[p] = person_samples

    # ---- Update logic (handles both flat + nested) -----------------------
    per_landmark, globals_out = logic.update(state, samples, dt, params)

    # ---- Emit per-person `p<P>:<lm>:<suffix>` + globals -----------------
    scriptOp.numSamples = 1
    scriptOp.rate = me.time.rate

    for p in range(persons):
        for lm in landmarks:
            o = per_landmark.get('p%d:%s' % (p, lm))
            if o is None:
                continue
            for suffix in logic.PER_LANDMARK_CHANS:
                scriptOp.appendChan('p%d:%s:%s' % (p, lm, suffix))[0] = o[suffix]

    for g in logic.GLOBAL_CHANS:
        scriptOp.appendChan(g)[0] = globals_out[g]
    return
