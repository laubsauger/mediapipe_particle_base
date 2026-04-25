// bounds_reflect.glsl
// ===================
// GLSL POP program — reflects particles off the inside of an axis-aligned
// 3D box. For each particle:
//   1. If P crosses a wall, clamp P back to the wall.
//   2. If Partvel points outward on that axis, flip it * uBounce
//      (0 = dead stop, 1 = perfectly elastic).
//
// Place as the LAST op in the force chain, immediately before the Null POP
// referenced by Particle POP's "Target Particles Update POP".
//
// -------------------------------------------------------------------------
// Setup in TD
// -------------------------------------------------------------------------
// On the GLSL POP:
//   - Attribute Class: Point
//   - Output Attributes page: add P and Partvel (selects them for writing;
//     with "Initialize Output Attributes" On the defaults copy input→output,
//     so we only need to overwrite when reflection fires).
//   - Vectors 1 page: declare uBoxMin (vec3), uBoxMax (vec3) and bind to
//     (parent().par.Boundsminx, Boundsminy, Boundsminz) etc.
//   - Scalars 1 page: declare uBounce and uMargin, bind to
//     parent().par.Boundsbounce / Boundsmargin.
//
// TD's GLSL POP runtime provides:
//   - TDIndex()          : current element index (0..TDNumElements()-1)
//   - TDNumElements()    : total number of elements in the pass
//   - TDIn_<AttribName>() : reads the named input point attribute
// Outputs are written to the output attribute buffers that are auto-
// allocated from the "Output Attributes" list — in TD the convention is
// to assign to the generated output variables. Exact output-write syntax
// varies slightly by build; recent 2024/2025 builds use the naming
// pattern below. If your compiler complains about the writes, open the
// GLSL POP's "Shader Info" DAT (right-click ▸ View Compiled Shader) and
// look for the generated output declarations — swap the write lines to
// match.

// --- Uniforms --------------------------------------------------------------
uniform vec3  uBoxMin;
uniform vec3  uBoxMax;
uniform float uBounce;
uniform float uMargin;

// --- Main ------------------------------------------------------------------
void main()
{
    int id = TDIndex();
    if (id >= TDNumElements()) return;

    // Read current point state. TD helpers use the attribute name.
    vec3 P = TDIn_P().xyz;
    vec3 V = TDIn_Partvel().xyz;

    vec3 boxMin = uBoxMin + vec3(uMargin);
    vec3 boxMax = uBoxMax - vec3(uMargin);

    // Per-axis hard clamp + velocity flip on outward motion.
    // Loop unrolled for clarity — some older GLSL compilers are picky
    // about dynamic array indexing on swizzled components.
    if (P.x < boxMin.x) { P.x = boxMin.x; if (V.x < 0.0) V.x = -V.x * uBounce; }
    else if (P.x > boxMax.x) { P.x = boxMax.x; if (V.x > 0.0) V.x = -V.x * uBounce; }

    if (P.y < boxMin.y) { P.y = boxMin.y; if (V.y < 0.0) V.y = -V.y * uBounce; }
    else if (P.y > boxMax.y) { P.y = boxMax.y; if (V.y > 0.0) V.y = -V.y * uBounce; }

    if (P.z < boxMin.z) { P.z = boxMin.z; if (V.z < 0.0) V.z = -V.z * uBounce; }
    else if (P.z > boxMax.z) { P.z = boxMax.z; if (V.z > 0.0) V.z = -V.z * uBounce; }

    // Write back. TD auto-generates output buffer bindings for each
    // attribute listed on the Output Attributes page; the generated
    // write variable follows the attribute name.
    TDOutAttrib_P      = vec4(P, 1.0);
    TDOutAttrib_Partvel = vec4(V, 0.0);
}
