// velocity_field.frag
// ===================
// GLSL TOP pixel shader. Splats N emitter points (wrists/ankles/nose) into a
// 2D RG16F velocity field that a POP Force node samples. Each emitter paints
// a radial gaussian whose RG encodes (vx, vy) scaled by its emit envelope,
// with an additional burst push.
//
// Single-input — this writes the INSTANTANEOUS field only. Persistence
// (decay / trails of force in the air) is implemented OUTSIDE the shader
// with a Feedback TOP + Level TOP chain. That keeps the shader compilable
// with one input, matches the screen-space smear pattern used elsewhere,
// and lets you tune `Fielddecay` without recompiling.
//
// Input 0 (sTD2DInputs[0]): emitters texture, size = (N, 2), RGBA32F.
//   Row 0 (v = 0.25 after pixel-center): (x,  y,  vx, vy)   for each landmark
//   Row 1 (v = 0.75 after pixel-center): (emit, burst, visible, speed)
//
// Output: RGBA16F.  RG = instantaneous velocity field contribution.
//                   B  = total gaussian weight (handy debug).
//                   A  = 1.
//
// Uniforms — set via the GLSL TOP's Vectors 1 page:
//   uNumEmitters   (float, treated as int)  count of landmarks in input 0
//   uRadius        (float)                  gaussian sigma in UV space (0..1)
//   uForceGain     (float)                  scales (vx,vy) before splatting
//   uBurstGain     (float)                  extra push along (vx,vy) from burst
//
// (Fielddecay is no longer a uniform — it drives the external Level TOP
//  multiplier. See velocity_controller_setup.md.)

uniform float uNumEmitters;
uniform float uRadius;
uniform float uForceGain;
uniform float uBurstGain;

out vec4 fragColor;

void main()
{
    vec2 p = vUV.st;
    int n = int(uNumEmitters + 0.5);
    float inv_two_r2 = 1.0 / (2.0 * max(uRadius * uRadius, 1e-6));

    vec2 v = vec2(0.0);
    float w_total = 0.0;

    // Loop cap at 64 so the shader stays compilable on strict drivers.
    // Bump this if you ever track >64 landmarks.
    for (int i = 0; i < 64; ++i) {
        if (i >= n) break;
        float u = (float(i) + 0.5) / float(n);
        vec4 r0 = texture(sTD2DInputs[0], vec2(u, 0.25));
        vec4 r1 = texture(sTD2DInputs[0], vec2(u, 0.75));

        float visible = r1.b;
        if (visible < 0.5) continue;

        vec2  pos   = r0.xy;
        vec2  vel   = r0.zw;
        float emit  = r1.r;
        float burst = r1.g;

        // Gaussian kernel centered on emitter.
        vec2 d = p - pos;
        float w = exp(-dot(d, d) * inv_two_r2);

        // Emitter contribution: base velocity * emit, plus extra burst push.
        float gain = uForceGain * (emit + uBurstGain * burst);
        v += vel * gain * w;
        w_total += w;
    }

    fragColor = vec4(v, w_total, 1.0);
}
