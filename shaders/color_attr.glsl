// color_attr.glsl
// ===============
// GLSL POP compute shader. Derives a per-particle Cd (color) attribute
// from PartVel and the per-particle landmark id, so the geometryCOMP
// instancing can use a stable, always-visible color rather than feeding
// raw PartVel (which goes to ~0 with damping → black particles).
//
// Cd composition:
//   palette[id]                 — per-limb base hue
//   + abs(PartVel) * uVelGain   — speed-driven brightness boost
//   + uBase                     — never fully black
//
// Pipeline placement:
//   particle1 → color_attr → render_null
//
// On the GLSL POP:
//   Attribute Class       : Point
//   Output Attributes     : ""        (we don't modify existing attrs)
//   Create Attributes [0] : name=custom, customname=Cd, type=float, comps=3
//   Initialize Output     : On
//   Vectors 1 page:
//     uBase    (vec3)
//   Vectors 2 page:
//     uVelGain (float)

uniform vec3  uBase;
uniform float uVelGain;

// Per-landmark palette (hand-picked, 5 entries — extend if Landmarks
// list grows). Wraps with mod when id exceeds the table length.
const vec3 kPalette[5] = vec3[](
    vec3(0.95, 0.30, 0.20),  // left_wrist  — warm red
    vec3(0.20, 0.65, 0.95),  // right_wrist — cyan
    vec3(0.95, 0.85, 0.20),  // left_ankle  — yellow
    vec3(0.55, 0.95, 0.30),  // right_ankle — lime
    vec3(0.85, 0.40, 0.95)   // nose        — magenta
);

void main()
{
    uint idx = TDIndex();
    if (idx >= TDNumElements()) return;

    vec3  vel = TDIn_PartVel().xyz;
    // PartId is a particle POP built-in. We use it to rotate through the
    // limb palette deterministically. Not perfect (a particle's color
    // doesn't follow its source landmark), but stable per-particle and
    // gives visible variety without needing the source `id` attr (which
    // collides with a reserved keyword in GLSL).
    uint  pid = uint(TDIn_PartId());
    int   pal_n = 5;
    vec3  base = kPalette[(pid % uint(pal_n))];

    vec3 c = uBase + base + abs(vel) * uVelGain;
    c = clamp(c, vec3(0.0), vec3(1.0));

    Cd[idx] = c;
}
