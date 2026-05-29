// body_viz.frag
// =============
// GLSL TOP — an elegant glowing render of the performer's skeleton, in the SAME
// space as the particle field (reads the body_tex joint texture, like
// body_field), so it sits exactly where the displacement happens. Additive HDR
// output → composited into the render chain before Bloom, so it glows/blooms
// like the particles. Replaces MediaPipe's debug circles with our own look.
//
// Look: each BONE is a soft "capsule" (thin bright core + wide soft halo =
// volume), each JOINT a brighter node, plus a gentle energy pulse flowing along
// the limbs over time. Per-joint visibility gates everything (off-frame joints
// draw nothing). Aspect-corrected so widths are round, not stretched by 16:9.
//
// Input 0 = body_tex (NJOINTS×2): row0 (y=0) = (x,y,vis,1), row1 (y=1) = vel.
// BONES must match body_logic.BONES.

out vec4 fragColor;

uniform float uAspect;     // box aspect (16/9) for round widths
uniform float uTime;       // absTime.seconds — flow animation
uniform float uWidth;      // bone capsule core half-width (uv-ish, world-y)
uniform float uGlow;       // overall intensity (HDR; >1 core blooms)
uniform vec3  uTint;       // fallback halo color (used at uBlend=0)
uniform float uFlow;       // 0..1 strength of the energy pulse along limbs
// Soup-palette uniforms so the body color BLENDS with the field + evolves with
// it (instead of a fixed tint that clashes). Mirror color_attr's soup colour.
uniform vec3  uSoupA;
uniform vec3  uSoupB;
uniform vec3  uSoupC;
uniform float uSoupcolorscale;
uniform float uSoupcyclespeed;
uniform float uSoupevolve;
uniform float uBlend;      // 0 = fixed uTint, 1 = soup's current evolving colour
uniform float uLogohueoffset;   // PERSISTENT hue offset (radians), same accum as soup

vec3 soupPalette(float t)
{
    t = fract(t);
    if (t < 0.3333)      return mix(uSoupA, uSoupB, t * 3.0);
    else if (t < 0.6667) return mix(uSoupB, uSoupC, (t - 0.3333) * 3.0);
    else                 return mix(uSoupC, uSoupA, (t - 0.6667) * 3.0);
}

vec3 hueShift(vec3 c, float a)
{
    const vec3 k = vec3(0.57735026919);
    float cosA = cos(a);
    return c * cosA + cross(k, c) * sin(a) + k * dot(k, c) * (1.0 - cosA);
}

const int NJOINTS = 13;
const int NBONES  = 14;
const ivec2 BONES[NBONES] = ivec2[](
    ivec2(1,2),
    ivec2(1,3), ivec2(3,5),
    ivec2(2,4), ivec2(4,6),
    ivec2(1,7), ivec2(2,8),
    ivec2(7,8),
    ivec2(7,9), ivec2(9,11),
    ivec2(8,10), ivec2(10,12),
    ivec2(0,1), ivec2(0,2)
);

void main()
{
    vec2 p = vUV.st;
    vec2 a = vec2(uAspect, 1.0);
    vec2 P = p * a;

    float core = 0.0;   // tight bright spine
    float halo = 0.0;   // wide soft volume
    float w = max(uWidth, 1e-4);

    // --- bones as soft capsules -------------------------------------------
    for (int i = 0; i < NBONES; ++i) {
        vec4 ja = texelFetch(sTD2DInputs[0], ivec2(BONES[i].x, 0), 0);
        vec4 jb = texelFetch(sTD2DInputs[0], ivec2(BONES[i].y, 0), 0);
        float vis = min(ja.z, jb.z);
        if (vis < 0.05) continue;

        vec2 A = ja.xy * a;
        vec2 B = jb.xy * a;
        vec2 AB = B - A;
        float len2 = max(dot(AB, AB), 1e-8);
        float t = clamp(dot(P - A, AB) / len2, 0.0, 1.0);
        float d = distance(P, A + t * AB);

        // energy pulse travelling along the bone (t = 0..1 from A to B)
        float pulse = 1.0 + uFlow * 0.8 * sin(t * 18.0 - uTime * 3.0);

        halo += vis * exp(-(d * d) / (w * w)) * pulse;
        core += vis * exp(-(d * d) / (w * w * 0.10));   // tight core
    }

    // --- joints as glowing nodes ------------------------------------------
    float node = 0.0;
    for (int j = 0; j < NJOINTS; ++j) {
        vec4 jt = texelFetch(sTD2DInputs[0], ivec2(j, 0), 0);
        if (jt.z < 0.05) continue;
        float d = distance(P, jt.xy * a);
        node += jt.z * exp(-(d * d) / (w * w * 0.6));
    }

    // Tint = the soup's CURRENT GLOBAL colour (time-only, no spatial term) so
    // the body reads as ONE coherent colour matching the surrounding soup mood,
    // not a per-fragment sample that ends up complementary to the bg at the
    // body's location. Same hue accumulator + evolve as the soup → harmonises.
    float phase   = fract(uTime * uSoupcyclespeed);
    vec3  soupCol = hueShift(soupPalette(phase),
                             uTime * uSoupevolve + uLogohueoffset);
    vec3  tint    = mix(uTint, soupCol, clamp(uBlend, 0.0, 1.0));

    // composite: colored soft volume + whiter HDR core + bright nodes
    vec3  col = tint * (halo * 0.6 + node * 0.9)
              + vec3(1.0) * (core * 1.4 + node * 0.5);
    float lum = halo * 0.6 + core * 1.4 + node * 1.1;
    fragColor = vec4(col * uGlow, clamp(lum * uGlow, 0.0, 1.0));
}
