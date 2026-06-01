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
uniform float uMaskbright;      // extra brightness for soup sitting on the logo mask
uniform float uMaskamt;         // 0..1 standby fade (op('logo_amt')['amt']); gates logo
uniform float uVelref;          // movement speed mapped to full hot/bloom (slow stays dim)
uniform float uSoupevolve;      // hue-rotation speed of the soup palette over time (evolving color)
uniform float uMasktrans;       // 0..1 logo-swap shockwave: fades the logo glow out then back
uniform float uMaskburstcolor;  // swap-time glow-up amount (HDR flare through Bloom)
uniform float uMaskhueoffset;   // PERSISTENT hue offset (radians) — accumulates per swap, holds
uniform float uPersonhuestep;   // hue (rad) added per PERSON index so each body wears a distinct tint
uniform float uMaskcharge;      // "charged-in-vessel" look — inside particles brighter + hue-shifted, softened mask
uniform float uSoupgradrot;     // slow rotation of the soup gradient direction (rad/sec) — alive feel
uniform float uClusterscale;    // cosmic-web filament noise scale
uniform float uClusterboost;    // brightness boost on filament peaks (galaxy-cluster look)
uniform float uClustergamma;    // contrast of the filament structure (higher = sharper filaments)

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
    // Smooth 3-stop cyclic palette — smoothstep within each segment so colour
    // transitions ease in/out instead of flicking linearly between stops.
    t = fract(t);
    float u = t * 3.0;                  // 0..3 over A→B→C→A
    if (t < 0.3333)      return mix(uSoupA, uSoupB, smoothstep(0.0, 1.0, u));
    else if (t < 0.6667) return mix(uSoupB, uSoupC, smoothstep(0.0, 1.0, u - 1.0));
    else                 return mix(uSoupC, uSoupA, smoothstep(0.0, 1.0, u - 2.0));
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

    if (lid >= 100) {
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
        // Gradient direction ROTATES slowly over time + flows laterally — colour
        // bands sweep + spin, system feels alive without user input.
        float gang  = uTime * uSoupgradrot;
        vec2  gdir  = vec2(cos(gang), sin(gang));
        vec2  gflow = vec2(sin(uTime * 0.07), cos(uTime * 0.053)) * 0.4;
        float phase = fract(dot(p.xy + gflow, gdir) * uSoupcolorscale
                            + uTime * uSoupcyclespeed);
        vec3  rampC = soupPalette(phase);
        // evolve the palette hue over time so the soup colour drifts through
        // the spectrum (continuous). PLUS a PERSISTENT per-swap hue offset that
        // ramps IN SYNC with the field morph and HOLDS afterward — each logo
        // swap shifts the colour to a new baseline and stays there (no bounce).
        rampC = hueShift(rampC, uTime * uSoupevolve + uMaskhueoffset);
        // velocity response: faster soup (turbulence peaks, or a flow-field
        //   shove from a limb) gets brighter and can bloom — so slow vs fast
        //   particles read differently and pose interaction "pops".
        float sf     = clamp(speed / max(uSoupspeedref, 1e-4), 0.0, 1.0);
        float bright = uSoupbright * (1.0 + sf * uSoupvelbloom);
        // logo brighten (standby): soup particles that have drifted onto the
        // logo's bright mask glow harder, so the shape reads boldly out of the
        // cloud. .w = luma mask from c_logo_lookup; uMaskamt fades with Logomode.
        // fade the logo glow out during a swap (1 - trans) so the image-cut +
        // brightness mismatch between the two logos is hidden in the shockwave.
        bright += TDIn_maskdata().w * uMaskbright * uMaskamt * (1.0 - uMasktrans);
        // glow up during the swap (HDR → Bloom catches it). No hue change here —
        // colour shifts happen via uMaskhueoffset (persistent, see above).
        bright *= (1.0 + uMasktrans * uMaskburstcolor * 2.0);
        // depth cue (fake DoF): particles toward the back of the box (−z) are
        //   dimmer, so the field has depth instead of a flat even mess.
        //   z range ≈ [-0.15, +0.15]; +z is nearer the camera.
        float dn     = clamp((p.z + 0.15) / 0.30, 0.0, 1.0);   // 0 back, 1 front
        float depthf = mix(1.0 - uDepthdim, 1.0, dn);
        // per-particle value variation so not every ball is identical brightness.
        float h      = fract(sin(float(TDIn_PartId()) * 12.9898) * 43758.5453);
        float pvar   = 0.65 + 0.35 * h;
        outc = rampC * bright * env * depthf * pvar;

        // COSMIC-WEB CLUSTERS: cheap 3D filament noise → particles whose
        // position lands on a "filament" (zero-crossing of the sum of sines)
        // get an extra brightness boost. Creates a galaxy-cluster organic
        // structure across the soup — bright filaments with voids between.
        if (uClusterboost > 0.0) {
            vec3 ps = p * uClusterscale + vec3(0.0, 0.0, uTime * 0.05);
            float n = sin(ps.x * 1.7 + ps.y * 1.3 + ps.z * 0.9 + uTime * 0.10)
                    * sin(ps.y * 2.3 + ps.z * 1.7 + uTime * 0.07)
                    * sin(ps.x * 3.1 - ps.z * 2.4 + uTime * 0.13);
            float fil = pow(clamp(1.0 - abs(n), 0.0, 1.0), max(uClustergamma, 0.1));
            // Filaments belong to the BACKGROUND only — fade them inside the
            // vessel mask so they don't visually fight the logo-fill or the
            // body-emitted movement region (which sits where the user is).
            float bgmask = 1.0 - 0.85 * TDIn_maskdata().w * uMaskamt;
            outc *= 1.0 + fil * uClusterboost * bgmask;
        }

        // VESSEL CHARGE: particles whose position falls on the logo mask are
        // the "contents" of the vessel — visually distinguish them from the bg
        // soup with extra brightness and a slight hue offset. Mask is softened
        // via smoothstep so there's no hard outline bleeding into the output;
        // contribution fades smoothly toward the mask edges.
        float inside = smoothstep(0.10, 0.55, TDIn_maskdata().w) * uMaskamt;
        if (inside > 0.0 && uMaskcharge > 0.0) {
            outc = hueShift(outc, 0.5 * inside * uMaskcharge);
            outc *= 1.0 + inside * uMaskcharge * 1.3;
        }
    } else {
        // ---- MOVEMENT: per-LIMB palette + per-PERSON hue + Embers age ramp.
        // Lid encodes BOTH person and limb: Lid = person*5 + limb_index. Each
        // person wears the same 5-colour limb palette but rotated by
        // person * uPersonhuestep so the bodies are visually distinct in
        // multi-person scenes.
        float mv    = clamp(speed / max(uVelref, 1e-4), 0.0, 1.0);
        int   limb  = ((lid % 5) + 5) % 5;
        int   pid   = lid / 5;                              // 0..MAX_PERSONS-1
        vec3  ident = uBase + kPalette[limb];
        ident       = hueShift(ident, float(pid) * uPersonhuestep);
        vec3  col   = mix(ident, uAccent, clamp(mv, 0.0, uMaxBlend));

        // Birth glow-up: just a mild HDR multiplier on the limb COLOUR. Keep
        // the boost modest so ACES tonemap doesn't desaturate it to white, and
        // so the trail feedback loop (which keeps re-blooming bright pixels)
        // doesn't accumulate to white over many frames.
        vec3 hot = col * (1.0 + 0.5 * mv);
        // Very brief HOT IMPULSE at birth (~0.015 of life ≈ 30-100 ms). Reads
        // as a sharp spark/pop instead of a drawn-out flash, then settles to
        // colour identity for the bulk of life.
        vec3 ageCol;
        if (agef < 0.015)     ageCol = mix(hot, col,       smoothstep(0.0, 0.015, agef));
        else if (agef < 0.60) ageCol = mix(col, uEmberMid, smoothstep(0.015, 0.60, agef));
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
