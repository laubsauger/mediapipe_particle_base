# audio_react_chop.py
# ===================
# Script CHOP callback. Turns the merged ARE (Audio Reactive Engine v1.2)
# feature channels into normalised MODULATION signals the particle render binds
# to. Synced to the Callbacks DAT of the Script CHOP `audio_react` inside
# velocity_controller.
#
# Wiring
# ------
#   ARE outs (drums + global_dyn + lmh + red_nat_fft)  →  merge CHOP
#   `audio_features`  →  input0 of this Script CHOP.
# Reading input0 (audio CHOPs are time-sliced → always cooking) gives this op a
# per-cook cook dependency, like emitters_chop_script reading lag1.
#
# Output channels (all 0..1, ALREADY multiplied by the master Audioreact so a
# single par kills the whole layer; PER-MAPPING depth lives in the consumer
# expressions as `base + chan·Audio<x>`):
#   kick snare hat pulse bass breath build  + spec0..spec(N-1)
#
# Pure logic lives in `audio_logic.py` (Text DAT `audio_logic`, `mod.audio_logic`).
# Self-test the math with `python3 audio_logic.py`.

N_SPEC = 15  # red_nat_fft channel count


def _par(name, default):
    p = getattr(parent().par, name, None)
    if p is None:
        return default
    try:
        return p.eval()
    except Exception:
        return default


def _chan(src, name, default=0.0):
    if src is None:
        return default
    c = src[name]
    if c is None or len(c) == 0:
        return default
    try:
        v = float(c[0])
    except (TypeError, ValueError):
        return default
    import math as _m
    return v if _m.isfinite(v) else default


def onCook(scriptOp):
    scriptOp.clear()
    if scriptOp.isTimeSlice:
        scriptOp.isTimeSlice = False

    src = scriptOp.inputs[0] if scriptOp.inputs else None

    al = mod.audio_logic

    # Reduced-FFT channel names are red_nat_fft_chan10 .. red_nat_fft_chan114
    # (ARE's quirky numbering); resolve whatever spectrum channels are present
    # in input order so we don't hardcode the odd suffixes.
    spec = []
    if src is not None:
        spec_chans = [c for c in src.chans() if c.name.startswith('red_nat_fft')]
        spec = [float(c[0]) for c in spec_chans[:N_SPEC]]

    features = {
        'drums_low':       _chan(src, 'drums_low'),
        'drums_mid':       _chan(src, 'drums_mid'),
        'drums_high':      _chan(src, 'drums_high'),
        'burst':           _chan(src, 'burst'),
        'pulse_dynamic':   _chan(src, 'pulse_dynamic'),
        'natural_dynamic': _chan(src, 'natural_dynamic'),
        'bass':            _chan(src, 'bass'),
        'mid':             _chan(src, 'mid'),
        'high':            _chan(src, 'high'),
        'spec':            spec,
    }

    # Persistent state across cooks (envelopes + AGC running maxima).
    # Rebuild if absent, if the spectrum width changed, OR if the schema gained
    # new keys (e.g. after a code reload) — otherwise process() KeyErrors on a
    # stale-shape dict carried over from the old module.
    state = parent().fetch('_audio_state', None)
    n_spec = len(spec) if spec else N_SPEC
    template = al.fresh_state(n_spec)
    if (state is None or len(state.get('spec', [])) != n_spec
            or any(k not in state for k in template)):
        state = template

    params = al.default_params()
    # Expose the most useful envelope/smoothing times as COMP pars (optional).
    params['kick_release']  = float(_par('Audiokickrelease',  params['kick_release']))
    params['hat_release']   = float(_par('Audiohatrelease',   params['hat_release']))
    params['breath_smooth'] = float(_par('Audiobreathsmooth', params['breath_smooth']))
    params['build_attack']  = float(_par('Audiobuildattack',  params['build_attack']))

    dt = 1.0 / max(1e-6, me.time.rate)
    out = al.process(state, features, dt, params)
    parent().store('_audio_state', state)

    # Master gate: 0 = layer fully off (outputs all 0 → all consumers fall back
    # to their hand-tuned base values).
    master = float(_par('Audioreact', 1.0))

    names = al.output_names(n_spec)
    scriptOp.numSamples = 1
    scriptOp.rate = me.time.rate
    for nm in names:
        ch = scriptOp.appendChan(nm)
        ch[0] = float(out.get(nm, 0.0)) * master
    return
