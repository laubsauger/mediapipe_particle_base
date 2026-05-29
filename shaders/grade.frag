// grade.frag
// =========
// GLSL TOP pixel shader — cinematic color grade + tone map for the particle
// render's post chain. Takes the HDR bloomed/streaked image and maps it to a
// graded display-range image. Pure fragment shader sampling sTD2DInputs[0]
// (safe — no compute-POP samplers).
//
// Pipeline placement:  … bloom/streaks → grade → lens_finish → out2
//
// Uniforms (Vectors/Scalars page, bound to parent().par.* via the Look page):
//   uExposure   (float) pre-tonemap HDR gain
//   uContrast   (float) contrast around mid grey
//   uSaturation (float) 0 = greyscale, 1 = neutral, >1 = punchy
//   uLift       (vec3)  shadow lift (adds in dark regions)
//   uGamma      (vec3)  midtone gamma per channel
//   uGain       (vec3)  highlight gain per channel
//   uTint       (vec3)  overall multiply tint

uniform float uExposure;
uniform float uContrast;
uniform float uSaturation;
uniform vec3  uLift;
uniform vec3  uGamma;
uniform vec3  uGain;
uniform vec3  uTint;
uniform float uEnable;   // 0 = passthrough (grade off)

out vec4 fragColor;

// ACES filmic approximation (Narkowicz) — HDR → display, filmic rolloff.
vec3 aces(vec3 x)
{
    const float a = 2.51, b = 0.03, c = 2.43, d = 0.59, e = 0.14;
    return clamp((x * (a * x + b)) / (x * (c * x + d) + e), 0.0, 1.0);
}

void main()
{
    vec4 src = texture(sTD2DInputs[0], vUV.st);
    if (uEnable < 0.5) { fragColor = src; return; }
    vec3 c = src.rgb * max(uExposure, 0.0);

    c = aces(c);                                  // tone map HDR → [0,1]

    // lift / gamma / gain (standard color-grade controls)
    c = pow(max(c, 0.0), 1.0 / max(uGamma, vec3(1e-3)));
    c = c * uGain + uLift * (1.0 - c);

    // contrast around mid grey
    c = (c - 0.5) * uContrast + 0.5;

    // saturation
    float luma = dot(c, vec3(0.299, 0.587, 0.114));
    c = mix(vec3(luma), c, uSaturation);

    c *= uTint;

    fragColor = vec4(clamp(c, 0.0, 1.0), src.a);
}
