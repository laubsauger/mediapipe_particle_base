// bounds_reflect.glsl
// ===================
// GLSL POP program — reflects particles off the inside of an axis-aligned
// 3D box. For each particle:
//   1. If P crosses a wall, clamp P back to the wall.
//   2. If Partvel points outward on that axis, flip it * uBounce
//      (0 = dead stop, 1 = perfectly elastic).
//
// Place as the LAST op in the force chain, immediately before the
// Null POP referenced by Particle POP's "Target Particles Update POP".
// That way particle positions and velocities are reflected just before
// feeding back into the next integration cook.
//
// -------------------------------------------------------------------------
// IMPORTANT: attribute access syntax
// -------------------------------------------------------------------------
// The GLSL POP's per-point attribute read/write API varies by TD build
// (some use `inPointAttribs.P`-style members, some use helper functions,
// some require declaring input/output attribute buffers on the op's
// parameter pages). I've written the LOGIC explicitly below — you'll need
// to adapt the four attribute access lines to your build's actual API.
// Reference the GLSL POP docs (Help ▸ Operator Snippets or
// docs.derivative.ca/GLSL_POP) for exact syntax in your version.
//
// Concretely, the lines you need to replace are marked `/* READ P */`
// etc. Everything between them (the reflection math) is standard GLSL
// and won't change.
//
// -------------------------------------------------------------------------
// Uniforms
// -------------------------------------------------------------------------
// Declare these on the GLSL POP's Vectors / Scalars page and bind to
// parent pars:
//   uBoxMin   vec3    box min corner in PARTICLE space (0..1 MediaPipe,
//                     not render/stretched space). Default (0, 0, -0.5)
//   uBoxMax   vec3    box max corner. Default (1, 1, +0.5)
//   uBounce   float   0 = particles stop at walls, 1 = elastic bounce.
//                     0.3–0.6 feels like water against a pool wall.
//   uMargin   float   small inset from walls so the visual clamp happens
//                     just before particles would clip the wall.
//                     Default 0.0 (hard clamp at exact wall).

uniform vec3  uBoxMin;
uniform vec3  uBoxMax;
uniform float uBounce;
uniform float uMargin;

// -------------------------------------------------------------------------
// main — reflection math (framework-agnostic)
// -------------------------------------------------------------------------
void main() {
    // /* READ P */   — replace with your build's point-attribute read for P.
    vec3 P = vec3(0.0);        // <-- read P from the input point

    // /* READ Partvel */ — replace with your build's read for Partvel.
    vec3 v = vec3(0.0);        // <-- read Partvel from the input point

    vec3 boxMin = uBoxMin + vec3(uMargin);
    vec3 boxMax = uBoxMax - vec3(uMargin);

    // Hard clamp + velocity reflection, per axis.
    for (int i = 0; i < 3; ++i) {
        if (P[i] < boxMin[i]) {
            P[i] = boxMin[i];
            if (v[i] < 0.0) {
                v[i] = -v[i] * uBounce;
            }
        } else if (P[i] > boxMax[i]) {
            P[i] = boxMax[i];
            if (v[i] > 0.0) {
                v[i] = -v[i] * uBounce;
            }
        }
    }

    // /* WRITE P */        — replace with your build's point-attribute write for P.
    // /* WRITE Partvel */  — replace with your build's write for Partvel.
    //
    // Conceptually:
    //   outPoint.P       = P;
    //   outPoint.Partvel = v;
}
