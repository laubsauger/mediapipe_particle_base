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

const int MAX_PERSONS = 4;
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

    float node = 0.0;
    // Outer loop over persons; cheap per-person early-out using nose visibility
    // saves ~75% of inner work in single-person scenes (no more 4× cost).
    for (int pid = 0; pid < MAX_PERSONS; ++pid) {
        int row_pos = 2 * pid;
        float nose_vis = texelFetch(sTD2DInputs[0], ivec2(0, row_pos), 0).z;
        if (nose_vis < 0.05) continue;

        // --- bones as wireframe-stylised capsules -------------------------
        for (int i = 0; i < NBONES; ++i) {
            vec4 ja = texelFetch(sTD2DInputs[0], ivec2(BONES[i].x, row_pos), 0);
            vec4 jb = texelFetch(sTD2DInputs[0], ivec2(BONES[i].y, row_pos), 0);
            float vis = min(ja.z, jb.z);
            if (vis < 0.05) continue;

            vec2 A = ja.xy * a;
            vec2 B = jb.xy * a;
            vec2 AB = B - A;
            float len2 = max(dot(AB, AB), 1e-8);
            float t = clamp(dot(P - A, AB) / len2, 0.0, 1.0);
            float d = distance(P, A + t * AB);

            // Animated wireframe rungs: bright bands flowing along the bone
            // (energy stream feel) + a slow pulse so bones BREATHE, not static.
            float dash  = 0.55 + 0.45 * sin(t * 36.0 - uTime * 6.0);
            float pulse = 1.0 + uFlow * 0.7 * sin(t * 10.0 - uTime * 2.5);
            float modul = mix(pulse, pulse * dash, uFlow);

            // Soft volumetric halo (the "3D tube") + tight bright SKIN at the
            // bone surface (wireframe look — visible OUTLINE at the capsule's
            // edge, hollow centre falls off naturally with d²).
            float halo_g = exp(-(d * d) / (w * w));
            float skin_g = exp(-(d * d) / (w * w * 0.25)) * (1.0 - exp(-(d * d) / (w * w * 0.04)));
            halo += vis * halo_g * modul;
            core += vis * skin_g * 1.5;            // bright wireframe skin
        }

        // --- joint nodes — sharper, more "control point" feel -------------
        for (int j = 0; j < NJOINTS; ++j) {
            vec4 jt = texelFetch(sTD2DInputs[0], ivec2(j, row_pos), 0);
            if (jt.z < 0.05) continue;
            float dj = distance(P, jt.xy * a);
            // Bright core + soft halo per joint (3D bead at each control point)
            float core_j = exp(-(dj * dj) / (w * w * 0.15));
            float halo_j = exp(-(dj * dj) / (w * w * 0.8));
            node += jt.z * (core_j * 1.3 + halo_j * 0.4);
        }
    }

    // Tint = the soup's CURRENT GLOBAL colour (time-only, no spatial term) so
    // the body reads as ONE coherent colour matching the surrounding soup mood,
    // not a per-fragment sample that ends up complementary to the bg at the
    // body's location. Same hue accumulator + evolve as the soup → harmonises.
    float phase   = fract(uTime * uSoupcyclespeed);
    vec3  soupCol = hueShift(soupPalette(phase),
                             uTime * uSoupevolve + uLogohueoffset);
    vec3  tint    = mix(uTint, soupCol, clamp(uBlend, 0.0, 1.0));

    // Composite using the SOUP TINT for every layer — body reads as the same
    // material as the field, not a white skeleton slabbed on top. The core was
    // previously vec3(1.0) which ACES'd to white and made the body feel
    // detached from the surrounding colour.
    vec3  hotTint = mix(tint, tint * 1.4 + vec3(0.10), 0.5);  // mildly hotter, still in palette
    vec3  col = tint    * (halo * 0.55 + node * 0.7)
              + hotTint * (core * 0.9  + node * 0.35);
    float lum = halo * 0.55 + core * 0.9 + node * 1.0;
    fragColor = vec4(col * uGlow, clamp(lum * uGlow, 0.0, 1.0));
}
