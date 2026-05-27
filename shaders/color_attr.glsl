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
uniform float uSoupbright;   // steady brightness multiplier for the soup

const vec3 kPalette[5] = vec3[](
    vec3(0.95, 0.30, 0.20),  // Lid 0 — left_wrist  (warm red)
    vec3(0.20, 0.65, 0.95),  // Lid 1 — right_wrist (cyan)
    vec3(0.95, 0.85, 0.20),  // Lid 2 — left_ankle  (yellow)
    vec3(0.55, 0.95, 0.30),  // Lid 3 — right_ankle (lime)
    vec3(0.85, 0.40, 0.95)   // Lid 4 — nose        (magenta)
);

// Soup base (ambient particles, Lid >= 5): cool dim so the soup reads as a
// quiet field that the warm movement embers pop against.
const vec3 kSoup = vec3(0.16, 0.18, 0.28);

// Embers ramp colours (kEmberHot is intentionally HDR > 1 so births bloom).
const vec3 kEmberHot = vec3(1.90, 1.55, 1.15);  // white-hot at birth
const vec3 kEmberMid = vec3(1.00, 0.42, 0.10);  // warm orange, mid-life
const vec3 kEmberOld = vec3(0.45, 0.06, 0.02);  // deep red ember, near death

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
        // ---- SOUP: steady, persistent glow (NOT subject to the embers
        // decay-to-black). Hold full brightness across life with only a brief
        // fade-in at birth and a soft fade-out near death so particles don't
        // pop. This is what makes the soup read as a thick persistent cloud
        // instead of flashing on then vanishing. A gentle warm accent shows
        // only when a flow field actually pushes the soup (speed > 0).
        float env = smoothstep(0.0, 0.05, agef) * (1.0 - smoothstep(0.80, 1.0, agef));
        float tv  = clamp(speed * uVelGain, 0.0, uMaxBlend);
        vec3  sc  = mix(kSoup, uAccent, tv);
        outc = sc * uSoupbright * env;
    } else {
        // ---- MOVEMENT: per-limb palette + velocity accent + Embers age ramp.
        int   k     = ((lid % 5) + 5) % 5;
        vec3  ident = uBase + kPalette[k];
        float tv    = clamp(speed * uVelGain, 0.0, uMaxBlend);
        vec3  col   = mix(ident, uAccent, tv);

        vec3 ageCol;
        if (agef < 0.15)      ageCol = mix(kEmberHot, col,       smoothstep(0.0, 0.15, agef));
        else if (agef < 0.60) ageCol = mix(col,       kEmberMid, smoothstep(0.15, 0.60, agef));
        else                  ageCol = mix(kEmberMid,  kEmberOld, smoothstep(0.60, 1.00, agef));
        float bright = pow(1.0 - agef, max(uAgefalloff, 0.01));  // peaks at birth
        ageCol *= bright;

        outc = mix(col, ageCol, clamp(uAgegradient, 0.0, 1.0));
    }

    // velocity bloom: push fast particles into HDR so Bloom catches them.
    // (Soup is slow, so this is ~no-op for it.)
    outc *= (1.0 + speed * uVelbloom);

    Cd[idx] = outc;
}
