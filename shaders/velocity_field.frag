// velocity_field.frag
// ===================
// GLSL TOP pixel shader. Splats N emitter points (wrists/ankles/nose) into a
// 2D force field that a POP Force node samples. Output is RGBA — RGB holds
// the 3D velocity vector (vx, vy, vz) so Force POP can push particles in
// full 3D, and A holds the total gaussian weight for debugging.
//
// Single-input — persistence lives OUTSIDE in a Feedback TOP → Level TOP
// chain so the shader stays compilable with one input and Fielddecay is a
// plain TOP parameter.
//
// Input 0 (sTD2DInputs[0]): emitters texture from emitters_tex_script.py
//   size = (N, 2), RGBA32F.
//   Row 0 (v = 0.25): (x, y, z, visible)
//   Row 1 (v = 0.75): (vx, vy, vz, force_gain)
//     force_gain = (emit + Burstgain * burst) * visible  (pre-computed
//     in Python so the shader doesn't need burst/visible separately)
//
// Output: RGBA16F.  RGB = 3D velocity contribution.  A = total gaussian
//                   weight (debug viz of field coverage).
//
// Uniforms — Vectors 1 page:
//   uNumEmitters  (float, treated as int)  count of landmarks
//   uRadius       (float)  base gaussian sigma in UV space (0..1)
//   uForceGain    (float)  overall scalar on emitter contribution
//   uZGain        (float)  how strongly limb depth scales splat size.
//                          0 = flat (no depth scaling). Positive values:
//                          negative z (toward camera) = bigger splat,
//                          positive z (away) = smaller.
//                          size_mult = clamp(1 - z * uZGain, 0.2, 3.0).
//   uVelStretch   (float)  0..~2. How much to stretch the gaussian along
//                          the emitter's velocity direction. 0 = round,
//                          1 = meaningfully elongated when speed is high.
//                          Lets fast-moving limbs throw a longer force
//                          cone ahead of them, "flinging" particles
//                          further in the direction of motion.
//   uStretchSpeedRef (float) reference speed (1/s in UV) above which
//                          full uVelStretch is applied. Below it, stretch
//                          scales linearly with speed.

uniform float uNumEmitters;
uniform float uRadius;
uniform float uForceGain;
uniform float uZGain;
uniform float uVelStretch;
uniform float uStretchSpeedRef;
uniform float uZForceWeight;

out vec4 fragColor;

void main()
{
    vec2 p = vUV.st;
    int n = int(uNumEmitters + 0.5);

    vec3  v_acc    = vec3(0.0);
    float w_total  = 0.0;

    // Loop cap at 64 so the shader stays compilable on strict drivers.
    for (int i = 0; i < 64; ++i) {
        if (i >= n) break;
        float u = (float(i) + 0.5) / float(n);
        vec4 r0 = texture(sTD2DInputs[0], vec2(u, 0.25));
        vec4 r1 = texture(sTD2DInputs[0], vec2(u, 0.75));

        float visible = r0.w;
        if (visible < 0.5) continue;

        vec2  pos2    = r0.xy;
        float z       = r0.z;
        vec3  vel     = r1.xyz;              // full 3D velocity
        // Damp the z component of the splatted velocity by uZForceWeight.
        // Monocular-depth noise from MediaPipe makes vz spurious during
        // pure horizontal motion — without this, particles drift in z on
        // every wave/swing. Matches the spawn-side Zforceweight knob.
        vel.z *= uZForceWeight;
        float gain    = r1.w * uForceGain;
        if (gain <= 0.0) continue;

        // ---- z-scaled radius. Negative z = closer = bigger splat. ----
        // Upper clamp tightened (1.8 vs the raw 3.0 the linear formula can
        // hit) so very-close limbs don't blow up the kernel into half the
        // frame — MediaPipe's z can go well past -0.5 when someone leans in.
        // Lower clamp at 0.25 keeps far-limbs visible rather than collapsing.
        float size_mult = clamp(1.0 - z * uZGain, 0.25, 1.8);
        float r_base    = max(uRadius * size_mult, 1e-4);

        // ---- Velocity-aligned anisotropic gaussian ----
        // Rotate the sample delta into a frame where the primary axis is
        // along vel.xy, then apply a longer radius along that axis scaled
        // by how fast the limb is moving (capped at uStretchSpeedRef).
        vec2 vel2 = vel.xy;
        float vmag2 = length(vel2);
        vec2 d = p - pos2;

        float r_para = r_base;
        float r_perp = r_base;
        if (uVelStretch > 0.0 && vmag2 > 1e-4) {
            vec2 vdir = vel2 / vmag2;
            // Project d onto velocity axis and its perpendicular.
            float d_para =  dot(d, vdir);
            float d_perp = -d.x * vdir.y + d.y * vdir.x;  // 2D cross
            // Stretch factor — ramps from 1 to (1 + uVelStretch) as speed
            // approaches uStretchSpeedRef, then clamps.
            float t = clamp(vmag2 / max(uStretchSpeedRef, 1e-4), 0.0, 1.0);
            r_para = r_base * (1.0 + uVelStretch * t);
            // Keep r_perp at base so the kernel is only elongated forward.
            d = vec2(d_para, d_perp);
        }

        float inv_two_r2_para = 1.0 / (2.0 * r_para * r_para);
        float inv_two_r2_perp = 1.0 / (2.0 * r_perp * r_perp);
        float w = exp(-( d.x * d.x * inv_two_r2_para
                       + d.y * d.y * inv_two_r2_perp ));

        v_acc   += vel * gain * w;
        w_total += w;
    }

    fragColor = vec4(v_acc, w_total);
}
