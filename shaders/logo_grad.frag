// logo_grad.frag
// =============
// GLSL TOP — turns the logo image (null_logo) into a force + mask field for the
// particle soup. Fragment shader sampling sTD2DInputs[0] (safe).
//
//   RGB = ∇(luma)  — gradient of the logo luminance. Points toward brighter
//                    pixels, so used as an ATTRACTOR force (particles climb
//                    toward the logo shape).
//   A   = luma     — the logo mask (used to brighten particles sitting on it).
//
// A native Lookup Texture POP samples THIS at each particle's Puv → writes a
// `logodata` attribute (no GLSL-POP sampler, which would crash). Input 0 =
// null_logo (1280×720, matches the box aspect).

out vec4 fragColor;

float luma(vec2 uv)
{
    return dot(texture(sTD2DInputs[0], uv).rgb, vec3(0.299, 0.587, 0.114));
}

void main()
{
    vec2 uv = vUV.st;
    vec2 px = 1.0 / vec2(textureSize(sTD2DInputs[0], 0));
    // wider sample step → smoother gradient (fills toward bright regions, not
    // just one-pixel edges), so particles cluster into the shape, not a hairline.
    vec2 s = px * 3.0;
    float l   = luma(uv);
    float gx  = luma(uv + vec2(s.x, 0.0)) - luma(uv - vec2(s.x, 0.0));
    float gy  = luma(uv + vec2(0.0, s.y)) - luma(uv - vec2(0.0, s.y));
    fragColor = vec4(gx, gy, 0.0, l);
}
