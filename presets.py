"""
presets.py
==========

Look presets for `velocity_controller`. Each preset is a bundle of custom-par
values (palette, post-FX, motion, density) that defines a distinct aesthetic.
Pure Python, no TD imports — self-testable: `python3 presets.py`.

Applied via `apply_preset.py` (a Parameter Execute DAT) when the COMP's
`Applypreset` pulse fires (or `Preset` menu changes):

    import presets; presets.apply(parent(), parent().par.Preset.eval())

Values are float, or (r, g, b) tuples for RGB pars — `apply()` writes the
`<name>r/g/b` component pars for tuples.

SCOPE — LOOK ONLY. Presets set palette + post-FX (bloom / streak / grade /
lens) + trail length. They DELIBERATELY do NOT touch the soup/particle PHYSICS
(`Soupmaxspeed`, `Soupturb*`, `Curlgain`, `Ambientrate`/`points`, `Particlesize`,
`Fieldforce`, `Spawnvelscale`, clustering, bounds, damping, …). Those were tuned
by hand over many iterations; a mood switch must never silently undo that. The
four moods differ purely through color + glow + grade, which is plenty. (If you
want mood-driven physics later, add the keys back here — but make it an explicit,
documented choice, not a surprise.)

Tuning note: keep soup colors' peak channel below `Bloomthreshold` (so the
calm soup doesn't bloom); `Emberhot` is intentionally HDR (> 1) so movement
births bloom. `Feedbackfade` is trail length — keep < ~0.8 to avoid additive
trail blow-out.
"""

# Each preset shares the same key set so switching is a clean crossfade of values.
# LOOK-ONLY (see SCOPE above): colors + post-FX + trail length. No physics.
PRESETS = {
    # Deep space — cool blues/violets/magenta, soft glowing dust, calm.
    'Cosmic': {
        'Soupcola': (0.10, 0.30, 0.72), 'Soupcolb': (0.42, 0.18, 0.82),
        'Soupcolc': (0.22, 0.30, 0.88),
        'Emberhot': (1.5, 1.6, 2.4), 'Embermid': (0.35, 0.30, 1.5),
        'Emberold': (0.08, 0.12, 0.45),
        'Bloomstrength': 1.2, 'Bloomthreshold': 1.05, 'Feedbackfade': 0.55,
        'Streakenable': 1.0, 'Streakintensity': 0.85, 'Streaklength': 150.0,
        'Soupsetspeed': 0.04,
        'Exposure': 1.0, 'Contrast': 1.05, 'Saturation': 1.15,
        'Tint': (0.95, 0.98, 1.12),
        'Vignette': 0.22, 'Chromab': 0.0025, 'Grain': 0.03,
    },
    # Fire — white-hot cores → orange → red embers, sparky, energetic.
    'Ember': {
        'Soupcola': (0.55, 0.16, 0.04), 'Soupcolb': (0.75, 0.35, 0.06),
        'Soupcolc': (0.40, 0.08, 0.10),
        'Emberhot': (2.4, 1.8, 1.0), 'Embermid': (1.4, 0.5, 0.08),
        'Emberold': (0.5, 0.05, 0.02),
        'Bloomstrength': 1.6, 'Bloomthreshold': 0.95, 'Feedbackfade': 0.7,
        'Streakenable': 1.0, 'Streakintensity': 0.8, 'Streaklength': 110.0,
        'Soupsetspeed': 0.05,
        'Exposure': 1.05, 'Contrast': 1.1, 'Saturation': 1.25,
        'Tint': (1.12, 0.96, 0.85),
        'Vignette': 0.5, 'Chromab': 0.003, 'Grain': 0.04,
    },
    # Ink / fluid — near-monochrome, painterly, smoky, high-contrast, calm.
    'Ink': {
        'Soupcola': (0.16, 0.20, 0.26), 'Soupcolb': (0.30, 0.36, 0.42),
        'Soupcolc': (0.10, 0.14, 0.20),
        'Emberhot': (1.8, 1.9, 2.0), 'Embermid': (0.6, 0.65, 0.7),
        'Emberold': (0.12, 0.13, 0.16),
        'Bloomstrength': 0.7, 'Bloomthreshold': 1.3, 'Feedbackfade': 0.78,
        'Streakenable': 0.0, 'Streakintensity': 0.0, 'Streaklength': 80.0,
        'Soupsetspeed': 0.0,
        'Exposure': 1.0, 'Contrast': 1.25, 'Saturation': 0.35,
        'Tint': (1.0, 1.0, 1.02),
        'Vignette': 0.55, 'Chromab': 0.0015, 'Grain': 0.05,
    },
    # Neon / cyber — saturated electric cyan/magenta/lime, punchy, strong glow.
    'Neon': {
        'Soupcola': (0.05, 0.65, 0.75), 'Soupcolb': (0.70, 0.10, 0.65),
        'Soupcolc': (0.30, 0.22, 0.82),
        'Emberhot': (2.2, 2.2, 2.4), 'Embermid': (0.30, 0.55, 1.5),
        'Emberold': (0.5, 0.05, 0.5),
        'Bloomstrength': 1.8, 'Bloomthreshold': 0.9, 'Feedbackfade': 0.68,
        'Streakenable': 1.0, 'Streakintensity': 1.1, 'Streaklength': 180.0,
        'Soupsetspeed': 0.06,
        'Exposure': 1.05, 'Contrast': 1.15, 'Saturation': 1.5,
        'Tint': (1.0, 1.0, 1.0),
        'Vignette': 0.4, 'Chromab': 0.006, 'Grain': 0.025,
    },
}

NAMES = list(PRESETS.keys())


def apply(comp, name):
    """Set every par in PRESETS[name] on `comp`. Tuples → r/g/b component pars.
    Missing pars are skipped (so the preset survives a partial install)."""
    bundle = PRESETS.get(name)
    if not bundle:
        return 0
    applied = 0
    for key, val in bundle.items():
        if isinstance(val, (tuple, list)):
            for letter, v in zip('rgb', val):
                p = getattr(comp.par, key + letter, None)
                if p is not None:
                    p.val = v
                    applied += 1
        else:
            p = getattr(comp.par, key, None)
            if p is not None:
                p.val = val
                applied += 1
    return applied


if __name__ == '__main__':
    # All presets share the same key set (clean switching).
    keysets = [frozenset(p) for p in PRESETS.values()]
    assert len(set(keysets)) == 1, "presets have differing key sets: " + \
        repr([sorted(k) for k in keysets])
    # RGB keys are 3-tuples; scalars are numbers.
    rgb_keys = {'Soupcola', 'Soupcolb', 'Soupcolc', 'Emberhot', 'Embermid',
                'Emberold', 'Tint'}
    for name, bundle in PRESETS.items():
        for k, v in bundle.items():
            if k in rgb_keys:
                assert isinstance(v, tuple) and len(v) == 3, (name, k, v)
            else:
                assert isinstance(v, (int, float)), (name, k, v)

    # apply() against a fake comp mirrors par writes.
    class _P:
        def __init__(self): self.val = None
    class _Pars:
        def __init__(self): self._d = {}
        def __getattr__(self, n):
            # only used by getattr(comp.par, name, None)
            return self._d.get(n)
    class _Comp:
        def __init__(self):
            self.par = _Pars()
            # pre-create every par a preset might set
            for b in PRESETS.values():
                for k, v in b.items():
                    if isinstance(v, tuple):
                        for letter in 'rgb':
                            self.par._d[k + letter] = _P()
                    else:
                        self.par._d[k] = _P()
    c = _Comp()
    n = apply(c, 'Cosmic')
    assert n > 0
    assert c.par._d['Soupcolar'].val == 0.10
    assert c.par._d['Saturation'].val == 1.15
    print("OK — presets: %d presets, %d pars each, apply() set %d pars."
          % (len(PRESETS), len(next(iter(PRESETS.values()))), n))
