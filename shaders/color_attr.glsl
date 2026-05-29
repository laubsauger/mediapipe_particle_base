// color_attr.glsl
// ===============
// GLSL POP compute shader. Writes the per-particle Cd (color) attribute the
// geo1 instancer binds to instance RGB. Three layers, composited per particle:
//
//   1. Identity     — per-limb palette (Lid 0..4) for movement particles, or a
//                     cool neutral "soup" base for ambient particles (Lid >= 5,
//                     the sentinel emitted by ambient_chop_script).
//   2. Velocity accent — faster particles blend toward a warm accent (capped,
//                     no clamp blowout, no flicker).
//   3. Embers age ramp — over each particle's life (PartAge / PartLifeSpan)
//                     it goes white-hot at birth → identity/warm → ember
//                     orange → deep red → dark, with a brightness envelope that
//                     peaks at birth and fades to ~0 at death. Blended in by
//                     uAgegradient (0 = flat, 1 = full embers).
//
// Finally a velocity HDR boost (uVelbloom) lifts fast particles above 1.0 so
// the downstream Bloom TOP (render1 is 16-bit float) blooms them — "velocity
// bloom". Young/hot particles are already HDR (kEmberHot > 1), so they glow too.
//
// Pipeline placement:  particle1 → color_attr → render_null
//
// On the GLSL POP:
//   Attribute Class       : Point
//   Output Attributes     : ""   (Cd is a Create-Attribute, not an input attr)
//   Create Attributes [0] : name=custom, customname=Cd, type=float, comps=3
//   Initialize Output     : On
//   Vectors (bound to parent().par.*):
//     uBase        (vec3)  ← never-fully-black ambient floor
//     uVelGain     (float) ← speed → blend toward accent
//     uAccent      (vec3)  ← warm target color at speed
//     uMaxBlend    (float) ← cap on velocity-accent blend [0..1]
//     uAgegradient (float) ← Agegradient  (0 = flat, 1 = full embers)
//     uAgefalloff  (float) ← Agefalloff   (brightness fade exponent)
//     uVelbloom    (float) ← Velbloom     (speed → HDR brightness boost)
//
// Reads per-particle PartAge + PartLifeSpan (exact per-particle life), PartVel,
// Lid. No uLifeRef needed — normalisation is per-particle.

uniform vec3  uBase;
uniform float uVelGain;
uniform vec3  uAccent;
uniform float uMaxBlend;
uniform float uAgegradient;
uniform float uAgefalloff;
uniform float uVelbloom;
uniform float uSoupbright;      // steady brightness multiplier for the soup
uniform float uTime;            // absTime.seconds, for the soup color cycle
uniform float uSoupcyclespeed;  // how fast the soup population cycles the ramp
uniform float uSoupspeedref;    // soup speed mapped to "fast" (velocity look)
uniform float uSoupvelbloom;    // fast-soup brightness/bloom boost
uniform float uSoupcolorscale;  // spatial frequency of the color gradient (bands across the box)
uniform float uDepthdim;        // how much to dim particles toward the back (fake DoF / depth)
uniform float uLogobright;      // extra brightness for soup sitting on the logo mask
uniform float uLogoamt;         // 0..1 standby fade (op('logo_amt')['amt']); gates logo
uniform float uVelref;          // movement speed mapped to full hot/bloom (slow stays dim)
uniform float uSoupevolve;      // hue-rotation speed of the soup palette over time (evolving color)
uniform float uLogotrans;       // 0..1 logo-swap shockwave: fades the logo glow out then back
uniform float uLogoburstcolor;  // swap-time glow-up amount (HDR flare through Bloom)
uniform float uLogohueoffset;   // PERSISTENT hue offset (radians) — accumulates per swap, holds

// Soup palette + ember colours come from COMP color pars (uniforms) so PRESETS
// can recolor the whole look. No TOP sampler (that crashes a GLSL POP).
uniform vec3 uSoupA;
uniform vec3 uSoupB;
uniform vec3 uSoupC;
uniform vec3 uEmberHot;   // white-hot at birth (keep HDR > 1 so births bloom)
uniform vec3 uEmberMid;   // mid-life
uniform vec3 uEmberOld;   // near-death ember

// Rotate a color's hue by angle `a` (radians) around the luminance axis. Used
// to make the soup palette EVOLVE through the spectrum over time, instead of
// just sweeping the same fixed A/B/C gradient.
vec3 hueShift(vec3 c, float a)
{
    const vec3 k = vec3(0.57735026919);   // normalize(vec3(1))
    float cosA = cos(a);
    return c * cosA + cross(k, c) * sin(a) + k * dot(k, c) * (1.0 - cosA);
}

// Cyclic 3-stop gradient over phase t (A→B→C→A, wraps seamlessly). Smooth,
// art-directable, preset-driven.
vec3 soupPalette(float t)
{
    t = fract(t);
    if (t < 0.3333)      return mix(uSoupA, uSoupB, t * 3.0);
    else if (t < 0.6667) return mix(uSoupB, uSoupC, (t - 0.3333) * 3.0);
    else                 return mix(uSoupC, uSoupA, (t - 0.6667) * 3.0);
}

const vec3 kPalette[5] = vec3[](
    vec3(0.95, 0.30, 0.20),  // Lid 0 — left_wrist  (warm red)
    vec3(0.20, 0.65, 0.95),  // Lid 1 — right_wrist (cyan)
    vec3(0.95, 0.85, 0.20),  // Lid 2 — left_ankle  (yellow)
    vec3(0.55, 0.95, 0.30),  // Lid 3 — right_ankle (lime)
    vec3(0.85, 0.40, 0.95)   // Lid 4 — nose        (magenta)
);

// (soup base + ember colours are now uniforms uSoupA/B/C + uEmberHot/Mid/Old)

void main()
{
    uint idx = TDIndex();
    if (idx >= TDNumElements()) return;

    vec3  vel   = TDIn_PartVel().xyz;
    int   lid   = int(TDIn_Lid());
    float age   = TDIn_PartAge();
    float life  = TDIn_PartLifeSpan();
    float speed = length(vel);
    float agef  = clamp(age / max(life, 1e-3), 0.0, 1.0);

    vec3 outc;

    if (lid >= 5) {
        // ---- SOUP: a living, color-cycling, persistent cloud. NOT subject to
        // the embers decay-to-black. The idle (no-pose) state is meant to be
        // beautiful on its own; pose interaction enhances it.
        //
        // env: brief birth fade-in + soft death fade-out so particles don't pop
        //   (full brightness in between => reads as a thick persistent cloud).
        float env = smoothstep(0.0, 0.05, agef) * (1.0 - smoothstep(0.80, 1.0, agef));
        // colour: a SPATIAL gradient swept over time. Phase comes from the
        //   particle's POSITION projected onto a direction (low frequency =>
        //   broad smooth color bands across the volume), drifting with time.
        //   Position-based (not per-particle) so neighbours share color => the
        //   field reads as gradients sweeping across it, not salt-and-pepper noise.
        vec3  p     = TDIn_P().xyz;
        float phase = fract(dot(p.xy, vec2(0.6, 0.8)) * uSoupcolorscale
                            + uTime * uSoupcyclespeed);
        vec3  rampC = soupPalette(phase);
        // evolve the palette hue over time so the soup colour drifts through
        // the spectrum (continuous). PLUS a PERSISTENT per-swap hue offset that
        // ramps IN SYNC with the field morph and HOLDS afterward — each logo
        // swap shifts the colour to a new baseline and stays there (no bounce).
        rampC = hueShift(rampC, uTime * uSoupevolve + uLogohueoffset);
        // velocity response: faster soup (turbulence peaks, or a flow-field
        //   shove from a limb) gets brighter and can bloom — so slow vs fast
        //   particles read differently and pose interaction "pops".
        float sf     = clamp(speed / max(uSoupspeedref, 1e-4), 0.0, 1.0);
        float bright = uSoupbright * (1.0 + sf * uSoupvelbloom);
        // logo brighten (standby): soup particles that have drifted onto the
        // logo's bright mask glow harder, so the shape reads boldly out of the
        // cloud. .w = luma mask from c_logo_lookup; uLogoamt fades with Logomode.
        // fade the logo glow out during a swap (1 - trans) so the image-cut +
        // brightness mismatch between the two logos is hidden in the shockwave.
        bright += TDIn_logodata().w * uLogobright * uLogoamt * (1.0 - uLogotrans);
        // glow up during the swap (HDR → Bloom catches it). No hue change here —
        // colour shifts happen via uLogohueoffset (persistent, see above).
        bright *= (1.0 + uLogotrans * uLogoburstcolor * 2.0);
        // depth cue (fake DoF): particles toward the back of the box (−z) are
        //   dimmer, so the field has depth instead of a flat even mess.
        //   z range ≈ [-0.15, +0.15]; +z is nearer the camera.
        float dn     = clamp((p.z + 0.15) / 0.30, 0.0, 1.0);   // 0 back, 1 front
        float depthf = mix(1.0 - uDepthdim, 1.0, dn);
        // per-particle value variation so not every ball is identical brightness.
        float h      = fract(sin(float(TDIn_PartId()) * 12.9898) * 43758.5453);
        float pvar   = 0.65 + 0.35 * h;
        outc = rampC * bright * env * depthf * pvar;
    } else {
        // ---- MOVEMENT: per-limb palette + velocity accent + Embers age ramp.
        // Speed is normalised to uVelref. Movement PartVel is small (~0.01..0.13
        // box-units), so a small ref (~0.08) maps a brisk swipe to "full". The
        // whole intensity scales with mv so SLOW emission stays dim and colored
        // instead of blowing out to white — the old code flashed the HDR
        // uEmberHot at *every* birth regardless of speed.
        float mv    = clamp(speed / max(uVelref, 1e-4), 0.0, 1.0);
        int   k     = ((lid % 5) + 5) % 5;
        vec3  ident = uBase + kPalette[k];
        vec3  col   = mix(ident, uAccent, clamp(mv, 0.0, uMaxBlend));

        // Birth flash GATED BY SPEED: at mv≈0 the particle is born at its (LDR,
        // sub-1.0) identity color → no bloom, no white. Only fast particles
        // (mv→1) flash the HDR ember-hot color that the Bloom TOP catches.
        vec3 hot = mix(col, uEmberHot, mv);
        vec3 ageCol;
        if (agef < 0.15)      ageCol = mix(hot, col,       smoothstep(0.0, 0.15, agef));
        else if (agef < 0.60) ageCol = mix(col, uEmberMid, smoothstep(0.15, 0.60, agef));
        else                  ageCol = mix(uEmberMid, uEmberOld, smoothstep(0.60, 1.00, agef));
        float bright = pow(1.0 - agef, max(uAgefalloff, 0.01));  // peaks at birth
        ageCol *= bright;

        // Movement brightness also scales with speed so a dense slow emission
        // can't additively sum to a white wash: dim births, dim cloud.
        outc = mix(col, ageCol, clamp(uAgegradient, 0.0, 1.0)) * (0.25 + 0.75 * mv);
    }

    // velocity bloom: push fast particles into HDR so Bloom catches them.
    // (Soup is slow, so this is ~no-op for it.)
    outc *= (1.0 + speed * uVelbloom);

    Cd[idx] = outc;
}
