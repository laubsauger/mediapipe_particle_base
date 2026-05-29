// lens_finish.frag
// ================
// GLSL TOP pixel shader — final "shot through a lens" finish: chromatic
// aberration + vignette + film grain. Runs on the graded display-range image,
// last before out2. Pure fragment shader sampling sTD2DInputs[0] (safe).
//
// Pipeline placement:  … grade → lens_finish → out2
//
// Uniforms (bound to parent().par.* via the Look page):
//   uVignette (float) 0 = none, 1 = strong edge darkening
//   uCA       (float) chromatic aberration amount (radial RGB split)
//   uGrain    (float) film grain strength
//   uTime     (float) absTime.seconds — animates the grain

uniform float uVignette;
uniform float uCA;
uniform float uGrain;
uniform float uTime;
uniform float uEnable;   // 0 = passthrough (lens finish off)

out vec4 fragColor;

float hash(vec2 p)
{
    return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453);
}

void main()
{
    vec2 uv = vUV.st;
    if (uEnable < 0.5) { fragColor = texture(sTD2DInputs[0], uv); return; }
    vec2 d  = uv - 0.5;

    // chromatic aberration — split R/B radially outward from centre.
    vec2 off = d * uCA;
    float r = texture(sTD2DInputs[0], uv + off).r;
    float g = texture(sTD2DInputs[0], uv).g;
    float b = texture(sTD2DInputs[0], uv - off).b;
    float a = texture(sTD2DInputs[0], uv).a;
    vec3 c = vec3(r, g, b);

    // vignette — smooth darkening toward the corners.
    float vig = smoothstep(0.85, 0.25, length(d) * 1.41421);
    c *= mix(1.0, vig, clamp(uVignette, 0.0, 1.0));

    // film grain — animated per-pixel noise, subtle.
    float gr = (hash(uv * vec2(1920.0, 1080.0) + fract(uTime) * 137.0) - 0.5) * uGrain;
    c += gr;

    fragColor = vec4(c, a);
}
