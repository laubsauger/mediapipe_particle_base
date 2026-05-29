// body_field.frag
// ===============
// GLSL TOP — turns the performer's skeleton into a 2D force field so the whole
// body (not just 5 debug points) parts and drags the particle soup. Fragment
// shader sampling its input texture (body_tex) — crash-safe (no GLSL-POP
// sampler).
//
// Input 0 = body_tex (NJOINTS×2 RGBA32F, packed by body_tex_script.py):
//   row v=0 (texel y=0): (x, y, visible, 1)   joint position (MediaPipe-UV) + vis
//   row v=1 (texel y=1): (vx, vy, 0, 1)        joint velocity (UV/sec)
//
// Output (sampled by body_force Lookup POP at each particle's Puv):
//   RG = push.xy   — unit-ish direction AWAY from the nearest bone × falloff
//                    (repel: particles part around limbs)
//   BA = drag.xy   — bone velocity × falloff (advect: particles flow with motion)
//
// Bones are hardcoded MediaPipe pose edges as pack-index pairs — MUST match
// body_logic.BONES. Distance is aspect-corrected (x×uAspect) so the falloff
// radius (uBodyradius) is round in world units, not stretched by the 16:9 box.

out vec4 fragColor;

uniform float uBodyradius;   // bone influence radius (world-y units)
uniform float uAspect;       // box aspect = boxWidth/boxHeight (16/9)

const int NBONES = 14;
const ivec2 BONES[NBONES] = ivec2[](
    ivec2(1,2),                          // shoulders
    ivec2(1,3), ivec2(3,5),              // left arm
    ivec2(2,4), ivec2(4,6),              // right arm
    ivec2(1,7), ivec2(2,8),              // torso sides
    ivec2(7,8),                          // hips
    ivec2(7,9), ivec2(9,11),             // left leg
    ivec2(8,10), ivec2(10,12),           // right leg
    ivec2(0,1), ivec2(0,2)               // head/neck cross
);

void main()
{
    vec2 p = vUV.st;
    vec2 a = vec2(uAspect, 1.0);   // metric scale: stretch x so distance is round

    vec2 push = vec2(0.0);
    vec2 drag = vec2(0.0);
    float r = max(uBodyradius, 1e-4);

    for (int i = 0; i < NBONES; ++i) {
        int ia = BONES[i].x;
        int ib = BONES[i].y;
        vec4 ja = texelFetch(sTD2DInputs[0], ivec2(ia, 0), 0);  // pos+vis
        vec4 jb = texelFetch(sTD2DInputs[0], ivec2(ib, 0), 0);
        float vis = min(ja.z, jb.z);
        if (vis < 0.01) continue;

        // closest point on segment A→B to p, measured in aspect-corrected space
        vec2 A = ja.xy * a;
        vec2 B = jb.xy * a;
        vec2 P = p * a;
        vec2 AB = B - A;
        float len2 = max(dot(AB, AB), 1e-8);
        float t = clamp(dot(P - A, AB) / len2, 0.0, 1.0);
        vec2 C = A + t * AB;            // closest point (aspect space)
        float dist = length(P - C);

        float fall = (1.0 - smoothstep(0.0, r, dist)) * vis;
        if (fall <= 0.0) continue;

        // push: away from the bone (back in unscaled uv direction)
        vec2 away = (P - C);
        away = (length(away) > 1e-5) ? normalize(away) / a : vec2(0.0);
        push += away * fall;

        // drag: blend of the two endpoints' velocities by t
        vec2 va = texelFetch(sTD2DInputs[0], ivec2(ia, 1), 0).xy;
        vec2 vb = texelFetch(sTD2DInputs[0], ivec2(ib, 1), 0).xy;
        drag += mix(va, vb, t) * fall;
    }

    fragColor = vec4(push, drag);
}
