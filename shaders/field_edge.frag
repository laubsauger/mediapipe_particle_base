// field_edge.frag
// ===============
// GLSL TOP. Smoothly fades the combined flow field (field_mix) to zero at the
// texture borders before it is sampled as a force (field_out → field_sample).
//
// Why: the field had a HARD pixel→transparent edge at the texture rectangle.
// Sampled as a force at the box walls, that discontinuity read as a strong
// gradient that piled particles up along the edges (clumping) and showed as a
// glowing border. A smooth border falloff removes the contrast so particles
// near the walls feel no spurious edge force.
//
// Input 0: field_mix (combined velocity/flow field, RGBA).
// Output : same, multiplied by a smooth rectangular border mask.
//
// Uniform (Vectors page):
//   uEdgeFade (float) ← parent().par.Fieldedgefade  — border width in UV
//                       (0 = hard edge / off, ~0.06 = gentle 6% feather).

uniform float uEdgeFade;

out vec4 fragColor;

void main()
{
    vec4 c = texture(sTD2DInputs[0], vUV.st);
    vec2 p = vUV.st;
    float e = max(uEdgeFade, 1e-4);
    float m = smoothstep(0.0, e, p.x) * smoothstep(0.0, e, 1.0 - p.x)
            * smoothstep(0.0, e, p.y) * smoothstep(0.0, e, 1.0 - p.y);
    fragColor = c * m;
}
