// color_attr.glsl
// ===============
// GLSL POP compute shader. Derives a per-particle Cd (color) attribute
// from the per-particle source landmark (Lid). At higher velocity the
// color subtly shifts toward a warm accent (capped blend, no clamp
// blowout) so motion reads in color space without flicker — instead of
// just lifting RGB which saturates to white and looks janky.
//
// Pipeline placement:
//   particle1 → color_attr → render_null
//
// On the GLSL POP:
//   Attribute Class       : Point
//   Output Attributes     : ""
//   Create Attributes [0] : name=custom, customname=Cd, type=float, comps=3
//   Initialize Output     : On
//   Vectors:
//     uBase    (vec3)   ← never-fully-black ambient floor
//     uVelGain (float)  ← speed → blend toward accent
//     uAccent  (vec3)   ← target color at full speed
//     uMaxBlend(float)  ← cap on blend amount [0..1] (e.g. 0.4)

uniform vec3  uBase;
uniform float uVelGain;
uniform vec3  uAccent;
uniform float uMaxBlend;

const vec3 kPalette[5] = vec3[](
    vec3(0.95, 0.30, 0.20),  // Lid 0 — left_wrist  (warm red)
    vec3(0.20, 0.65, 0.95),  // Lid 1 — right_wrist (cyan)
    vec3(0.95, 0.85, 0.20),  // Lid 2 — left_ankle  (yellow)
    vec3(0.55, 0.95, 0.30),  // Lid 3 — right_ankle (lime)
    vec3(0.85, 0.40, 0.95)   // Lid 4 — nose        (magenta)
);

void main()
{
    uint idx = TDIndex();
    if (idx >= TDNumElements()) return;

    vec3  vel = TDIn_PartVel().xyz;
    int   lid = int(TDIn_Lid());
    int   pal_n = 5;
    int   k = ((lid % pal_n) + pal_n) % pal_n;
    vec3  base = kPalette[k];

    // Smooth, capped speed → blend toward accent. No clamp-to-1
    // blowout, no flicker. The palette identity always dominates.
    float speed = length(vel);
    float t = clamp(speed * uVelGain, 0.0, uMaxBlend);
    vec3  cold = uBase + base;
    vec3  c    = mix(cold, uAccent, t);

    Cd[idx] = c;
}
