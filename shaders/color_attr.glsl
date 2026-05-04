// color_attr.glsl
// ===============
// GLSL POP compute shader. Derives a per-particle Cd (color) attribute
// from the per-particle source landmark (Lid) plus a tasteful speed
// brightness boost. Stable per-limb so all particles emitted by the same
// limb share a color identity.
//
// Why Lid (not `id`)? GLSL reserves several short names; `id` collides
// with builtin/keyword usage in some compiler paths. We rename the POP
// attribute upstream to `Lid` (limb id) and access it via TDIn_Lid().
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
//     uBase    (vec3)   ← never-fully-black floor
//     uVelGain (float)  ← speed → extra brightness

uniform vec3  uBase;
uniform float uVelGain;

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

    // Speed-driven brightness lift (additive, clamped). Keeps stable hue.
    float speed = length(vel);
    vec3 c = uBase + base + vec3(speed * uVelGain);
    c = clamp(c, vec3(0.0), vec3(1.0));

    Cd[idx] = c;
}
