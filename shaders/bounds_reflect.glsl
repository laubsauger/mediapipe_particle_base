// bounds_reflect.glsl
// ===================
// GLSL POP compute shader. Integrates external force (fieldforce + NoiseCurl)
// into PartVel, applies per-cook velocity damping, then reflects particles
// off the inside of an axis-aligned 3D box.
//
// Why we do force integration here: TD's Particle POP does NOT automatically
// apply a feedback `PartForce` attribute to `PartVel`. The integrator only
// handles position (P += PartVel * dt) and built-in initial conditions
// (initvelocity / damping / mass). Any custom force field has to be folded
// into PartVel manually before the feedback target reads it. We do that here.
//
// Pipeline placement:
//   add_to_force → bounds_reflect → force_null
//
// On the GLSL POP:
//   Attribute Class      : Point
//   Output Attributes    : "PartVel P"   (P required for the hard position
//                          clamp; without it particles overshoot/escape the box)
//   Initialize Output    : On
//   Vectors 1 page:
//     uBoxMin     (vec3)  ← (parent().par.Boundsminx, Boundsminy, Boundsminz)
//     uBoxMax     (vec3)  ← (parent().par.Boundsmaxx, Boundsmaxy, Boundsmaxz)
//   Vectors 2 page:
//     uBounce     (float) ← parent().par.Boundsbounce
//     uMargin     (float) ← parent().par.Boundsmargin
//   Vectors 3 page (NEW — add via Vectors par on the GLSL POP):
//     uForceScale    (float) ← parent().par.Forcescale  (per-cook force gain;
//                                try 0.02 — multiplies the curved force when
//                                added to PartVel; smaller = gentler push)
//     uDamping       (float) ← parent().par.Velocitydamping (0..1; fraction
//                                of velocity REMOVED per cook; 0=keep,
//                                1=stop. Set Particle POP's own damping to 0
//                                to avoid stacking.)
//     uMaxSpeed      (float) ← parent().par.Maxspeed (clamp on |vel|; try 8)
//     uForceDeadzone (float) ← parent().par.Forcedeadzone (raw |force|
//                                magnitude below which the particle gets
//                                NO push at all; try 5 — silences the slow
//                                drift caused by field persistence at rest)
//     uForceRef      (float) ← parent().par.Forceref (reference |force| at
//                                which the curved response hits its full
//                                magnitude; try 80 — anything above maps to
//                                full uForceRef)
//     uForceGamma    (float) ← parent().par.Forcegamma (response curvature;
//                                1.0 = linear, 2.0 = squared (gentler at
//                                small motion, snappier at big motion).)

uniform vec3  uBoxMin;
uniform vec3  uBoxMax;
uniform float uBounce;
uniform float uMargin;
uniform float uForceScale;
uniform float uDamping;
uniform float uMaxSpeed;
uniform float uForceDeadzone;
uniform float uForceRef;
uniform float uForceGamma;
uniform float uSoupturb;     // gentle base curl drift for ambient soup (Lid>=5)
uniform float uSoupmaxspeed; // hard cap on idle soup speed (calm soup)
uniform float uSoupturb2;    // strength of the broad/slow second curl layer
uniform float uSouplayermix; // fraction of soup on layer B (broad) vs A (fine)
uniform float uMaskattract;  // soup pull toward the logo shape (gradient force)
uniform float uMaskamt;      // 0..1 standby fade (op('logo_amt')['amt']); gates logo
uniform float uMasktrap;     // velocity damping ON the logo mask (sticks soup → fills shape)
uniform float uMaskvigor;    // liveliness of contained particles (0=static decal, 1=churning vessel)
uniform float uMasktrans;    // 0..1 visual swap bump (unused here; color_attr uses it)
uniform float uMaskpush;     // outward push-back strength during the swap shockwave
uniform float uMaskmorph;    // 0..1 mid-swap (cross-dissolve): releases the trap so particles flow to new homes
uniform float uBodypush;     // repel strength: particles parted by the skeleton
uniform float uBodydrag;     // advect strength: particles dragged along limb motion
uniform float uSoupfieldgain;// soup-only fraction of the RAW (pre-deadzone)
                             // field force — lets depth/pose flow reach the soup
uniform float uWallrepel;    // soft inward push strength near each bounds wall
uniform float uWallband;     // distance from the wall over which the push ramps
uniform float uDroprepel;    // audio: radial outward SHOCKWAVE from box centre on big drops ("the music breathes the particles")
uniform float uSoupdir;      // audio: soup-flow direction rotation (radians), stepped on each drop so the disturbance changes heading
uniform float uResonance;    // audio: SEGMENTED-LOGO regional resonance — each logo region churns with its mapped reduced-FFT bin (vessel = resonant body)
uniform float uSurface;      // audio: HIGH-band surface agitation — fine fizz concentrated at the logo silhouette edge
uniform float uBeatpol;      // audio: beat polarity (+1 = gather/suck-in, −1 = blow-out) — varies per beat so contractions aren't all the same
uniform float uMidswirl;     // audio: MID-peak swirl burst — a rotational (tangential) disturbance, distinct from the kick gather
uniform float uForcemode;    // audio: which beat FORCE MODE (0 rest,1 gather,2 vortex,3 waveform,4 current,5 fold) — cycles on drops, holds a min dwell
uniform float uModesustain;  // audio: CONTINUOUS energy-scaled drive (not just the beat pulse) so shaping modes (waveform/fold/current) actually build up and read
// uSpectrum[] is auto-declared by TD from the bound CHOP (15 samples) — do NOT
// declare it here (redeclaration). It holds the normalised reduced-FFT bins.

// yaw (around Z) + pitch (around X) rotation — used to tumble the shape
// attractors into different orientations as uSoupdir advances.
mat3 rotYP(float a, float b)
{
    float ca = cos(a), sa = sin(a), cb = cos(b), sb = sin(b);
    mat3 Rz = mat3(ca, -sa, 0.0,  sa, ca, 0.0,  0.0, 0.0, 1.0);
    mat3 Rx = mat3(1.0, 0.0, 0.0,  0.0, cb, -sb,  0.0, sb, cb);
    return Rz * Rx;
}

void main()
{
    uint id = TDIndex();
    if (id >= TDNumElements()) return;

    vec3 pos   = TDIn_P().xyz;
    vec3 vel   = TDIn_PartVel().xyz;
    vec3 force = TDIn_PartForce().xyz;
    int  lid   = int(TDIn_Lid());
    vec3 curl  = TDIn_NoiseCurl().xyz;   // fine layer (curl_noise)
    vec3 curl2 = TDIn_NoiseCurl2().xyz;  // broad/slow layer (curl_noise2)
    vec4 logo  = TDIn_maskdata();        // .xy = ∇luma (attractor dir), .w = mask
    vec4 body  = TDIn_bodyforce();       // .xy = push (repel dir), .zw = drag (limb vel)
    // Depth is now a FLOW FIELD contribution merged into the per-particle
    // `fieldforce` upstream (depth_field GLSL TOP → field_mix Composite TOP),
    // not a separate wall-repel attribute. Nothing to read here.

    // NaN/Inf guard. NaN P fed into instancing transforms or texture lookups
    // can crash the Vulkan device outright. Clamp to zero here so a single
    // bad cook can't poison the simulation.
    if (any(isnan(vel))   || any(isinf(vel)))   vel   = vec3(0.0);
    if (any(isnan(force)) || any(isinf(force))) force = vec3(0.0);
    if (any(isnan(pos))   || any(isinf(pos))) {
        // Corrupt position: park at box centre, dead stop. Write P too so a
        // NaN can't persist in the fed-back position.
        pos = (uBoxMin + uBoxMax) * 0.5;
        vel = vec3(0.0);
        P[id]       = pos;
        PartVel[id] = vel;
        return;
    }

    // ---- Nonlinear force response -----------------------------------------
    // Field persistence (Fielddecay) keeps a residue of force around even
    // when the performer is nearly still — multiplied by a linear ForceScale
    // that adds up to "violent push at rest". Apply a deadzone + gamma curve
    // so small magnitudes get squashed to ~0 and only the high-end of motion
    // produces a strong kick.
    //
    //   t   = clamp((|f| - deadzone) / (ref - deadzone), 0, 1)
    //   t   = pow(t, gamma)              gamma > 1 = gentler-at-small
    //   |f'| = t * ref                   reshape magnitude
    //   f'  = (f / |f|) * |f'|           preserve direction
    // RAW force snapshot BEFORE the deadzone curve. The deadzone (tuned for
    // strong movement-emitter splats) crushes anything below ~uForceDeadzone
    // to zero — that's correct for movement particles (kills rest-drift) but
    // it also kills any contribution from the depth-driven flow field which
    // is small per-pixel. The soup branch below applies the RAW force at a
    // small gain so the soup actually responds to depth/pose-driven flow.
    vec3 force_raw = force;

    float fmag = length(force);
    if (fmag > 1e-4 && uForceRef > uForceDeadzone) {
        float t = clamp((fmag - uForceDeadzone)
                        / (uForceRef - uForceDeadzone), 0.0, 1.0);
        t = pow(t, max(uForceGamma, 0.001));
        force = (force / fmag) * (t * uForceRef);
    } else {
        force = vec3(0.0);
    }

    // ---- Force integration -------------------------------------------------
    // PartVel += curved_force * uForceScale  (treat uForceScale as dt * gain)
    // Then per-cook damping: vel *= (1 - uDamping). uDamping=0 keeps all,
    // uDamping=1 zeroes velocity each cook.
    vel += force * uForceScale;

    // Soup-specific: apply the RAW field force (pre-deadzone) at a small gain
    // so depth/pose flow reaches the ambient soup. Movement particles already
    // got the curved force above — the deadzone is calibrated for them.
    if (lid >= 5) {
        vel += force_raw * uForceScale * uSoupfieldgain;
    }

    // Body field: the performer's skeleton parts the soup (push = away from the
    // nearest bone) and drags it along limb motion (drag = bone velocity).
    // Applies to ALL particles. Per-joint visibility already gated the field, so
    // off-frame / low-confidence joints contribute nothing (no phantom pushes).
    vel += vec3(body.xy, 0.0) * uBodypush + vec3(body.zw, 0.0) * uBodydrag;

    // Gentle base turbulence for the ambient soup only (Lid>=5). Curl is
    // otherwise crushed by the deadzone/gamma curve above (tuned for strong
    // movement forces), so apply it DIRECTLY here for soup, scaled by the
    // (small) uSoupturb — this is the idle swirl. Keep it low; terminal drift
    // ≈ |curl|·uSoupturb / uDamping.
    if (lid >= 5) {
        // Two interleaved flow layers: each soup particle follows the broad
        // slow curl (B) or the fine curl (A) by a stable PartId hash, so the
        // field reads as layered structure rather than one uniform drift.
        // Curl is CALMED as the logo fades in (uMaskamt→1) so the turbulence
        // stops fighting the attractor and the shape can actually complete.
        float h = fract(sin(float(TDIn_PartId()) * 91.17) * 43758.5453);
        float curlcalm = 1.0 - 0.85 * uMaskamt;
        // Audio: rotate the soup-flow direction (uSoupdir, stepped per drop) so
        // the disturbance visibly changes heading on each drop.
        float cs = cos(uSoupdir), sd = sin(uSoupdir);
        vec3 curlR  = vec3(curl.x  * cs - curl.y  * sd, curl.x  * sd + curl.y  * cs, curl.z);
        vec3 curl2R = vec3(curl2.x * cs - curl2.y * sd, curl2.x * sd + curl2.y * cs, curl2.z);
        if (h < uSouplayermix) vel += curl2R * uSoupturb2 * curlcalm;
        else                   vel += curlR  * uSoupturb  * curlcalm;
    }

    vel *= max(0.0, 1.0 - uDamping);

    // Speed clamp. Without this, an emitter staring straight at a particle
    // for a few cooks can compound force into runaway velocity. Cap so the
    // bounds-reflect step doesn't have to work miracles.
    float spd = length(vel);
    if (spd > uMaxSpeed) vel *= (uMaxSpeed / spd);

    // Soup-specific HARD speed cap. The ambient soup should drift gently when
    // undisturbed; whatever residual velocity it picks up (curl, field tail,
    // integration), this caps its idle speed so it never reads as fast/turbulent.
    // A limb's flow field can still shove it — it's just capped at uSoupmaxspeed.
    if (lid >= 5) {
        // Idle calm cap on the curl drift (free soup stays gentle).
        float ss = length(vel);
        if (ss > uSoupmaxspeed) vel *= (uSoupmaxspeed / ss);

        // ---- Logo as a 3D VESSEL ------------------------------------------
        // `inside` is high for particles sitting on the bright shape.
        float inside = clamp(logo.w * uMaskamt, 0.0, 1.0);

        // 1. Attract from afar + soft edge WALL. Applied AFTER the calm cap so
        //    it isn't throttled. The gradient is ~0 inside the bright plateau
        //    (contents move freely) and strong at the edges (escaping particles
        //    get pushed back in) → the shape behaves like a container.
        //    SWAP = positional morph + explosion AT THE SAME TIME:
        //    • attract stays ON while the logo_cycle cross-dissolves the two
        //      logo images → the gradient field smoothly morphs old→new, so
        //      particles migrate to their new homes (real position lerp).
        //    • a simultaneous OUTWARD PUSH pulse (uMaskmorph) blasts them off
        //      the old shape mid-swap. Push fades with morph, attract gathers
        //      them onto the NEW shape. So they explode AND reform together —
        //      not in series.
        vel.xy += logo.xy * uMaskattract * uMaskamt;
        vel.xy -= logo.xy * uMaskpush    * uMaskamt * uMaskmorph;

        // 2. Keep the CONTENTS ALIVE: inject extra (un-capped) 3D curl swirl
        //    ONLY inside the shape, so contained particles tumble like a filled
        //    vessel instead of freezing on the mask. uMaskvigor = liveliness;
        //    curl is 3D so they also drift in z → reads as a 3D vessel.
        vel += curl * (uSoupturb * 5.0 * inside * uMaskvigor);

        // ---- SEGMENTED LOGO: regional FFT resonance -----------------------
        // Split the vessel into a grid of regions; each maps to one reduced-FFT
        // bin (uSpectrum). The trapped material in each region churns with that
        // frequency band's energy, so different parts of the logo resonate with
        // different parts of the music — the shape reads as a resonant body with
        // internal "organs", not a uniform fill. Gated to inside the vessel.
        if (uResonance > 0.0 && inside > 0.0) {
            vec3 nrm = (pos - uBoxMin) / max(uBoxMax - uBoxMin, vec3(1e-3));
            int rx = int(clamp(nrm.x, 0.0, 0.999) * 5.0);   // 0..4 columns
            int ry = int(clamp(nrm.y, 0.0, 0.999) * 3.0);   // 0..2 rows
            float band = uSpectrum[ry * 5 + rx];            // 0..14 → bin
            vel += curl * (band * uResonance * inside);
        }

        // ---- SURFACE agitation (high band) --------------------------------
        // Fine fizz concentrated at the silhouette edge (inside·(1-inside) peaks
        // at the boundary) so thin strokes shimmer and the edge feels porous,
        // without disturbing the calm interior fill.
        if (uSurface > 0.0) {
            float edge = inside * (1.0 - inside) * 4.0;     // peaks at the silhouette
            vel += curl * (uSurface * (inside * 0.25 + edge));
        }

        // 3. Gentle settle (NOT a freeze) so speed doesn't run away — weakened
        //    by vigor so the swirl persists (vigor 0 → sticky/static like a
        //    decal; vigor 1 → lively churn inside the shape). RELEASED during a
        //    swap (1 - trans) so particles are free to blow outward.
        // trap released during the morph so particles flow freely to their new
        // homes, returns (×1) once settled.
        vel.xy *= (1.0 - inside * uMasktrap * (1.0 - 0.7 * uMaskvigor)
                        * (1.0 - uMaskmorph));

        // 4. Overall logo-speed limit, with headroom for the vessel swirl so
        //    step 2 isn't immediately clamped away.
        float lcap = uSoupmaxspeed + uMaskamt * (uMaskattract * 0.5
                     + uSoupturb * 5.0 * inside * uMaskvigor);
        float ls = length(vel);
        if (ls > lcap) vel *= (lcap / ls);
    }

    // ---- Edge damping ------------------------------------------------------
    // Particles drift to the bounds because outward forces (depth ∇d pointing
    // away from body, curl at periphery, etc.) decay to ~0 in empty areas
    // near walls — particles coast there and stop. The previous "soft repel"
    // approach just shifted the accumulation band inward and shrank the
    // usable area. Better: scrub the velocity itself in a band near each
    // wall. Particles approaching a wall lose energy rapidly so they never
    // reach it; particles in the middle are untouched.
    {
        // INWARD repel force near each wall. Killing velocity here (the old
        // approach) made particles STOP at the wall → they piled into a bright
        // rectangular "frame" (cheap-looking). Instead push them back toward the
        // interior BEFORE they reach the wall, so nothing accumulates at the edge.
        vec3 lo = pos - uBoxMin;     // distance to the min walls (>0 inside)
        vec3 hi = uBoxMax - pos;     // distance to the max walls
        float band = max(uWallband, 1e-4);
        // ramp 0 (deep inside) → 1 (at the wall), quadratic so the push stays
        // gentle in the band and firm right at the edge.
        vec3 pmin = clamp(1.0 - lo / band, 0.0, 1.0); pmin *= pmin;
        vec3 pmax = clamp(1.0 - hi / band, 0.0, 1.0); pmax *= pmax;
        // +inward near a min wall, −inward near a max wall.
        vec3 inward = pmin - pmax;
        vel += inward * uWallrepel * 0.2;

        // Round the rectangular silhouette toward an ELLIPSE: a gentle extra
        // inward pull only beyond an inscribed ellipse (corners), so the mass
        // reads as a soft oval — no hard rectangle to see rotating. Corners only,
        // so the bulk of the frame still fills.
        vec3 cen3  = (uBoxMin + uBoxMax) * 0.5;
        vec3 half3 = (uBoxMax - uBoxMin) * 0.5;
        vec2 nq    = (pos.xy - cen3.xy) / half3.xy;     // 1.0 = on the inscribed ellipse
        float over = clamp((length(nq) - 0.9) / 0.2, 0.0, 1.0);
        vel.xy -= ((pos.xy - cen3.xy) / (length(pos.xy - cen3.xy) + 1e-4))
                  * over * uWallrepel * 0.5;
    }

    // ---- AUDIO BEAT surge (organic) ----------------------------------------
    // On the beat, amplify the swirly CURL flow rather than blasting radially —
    // each particle moves along its own noise direction (which animates over
    // time), so the surge is organic and NEVER the same direction twice. A small
    // radial term keeps a gentle "breathe out" feel. Applied AFTER the speed caps
    // so it reads; wall clamp below contains it. No-op when uDroprepel is 0.
    if (uDroprepel > 0.0 || uModesustain > 0.0) {
        vec3 cen   = (uBoxMin + uBoxMax) * 0.5;
        vec3 bhalf = (uBoxMax - uBoxMin) * 0.5;
        int  fmode = int(uForcemode + 0.5);
        // accent = the rhythmic beat pulse; sustain = continuous energy-scaled
        // drive so a mode keeps shaping the field over its whole dwell (not just
        // a flicker on each kick). drive = both.
        float accent = uDroprepel;
        float drive  = uDroprepel + uModesustain;

        if (fmode == 1) {
            // GATHER — pull toward an ORBITING focal point (angle stepped per
            // beat, two harmonics so it wanders); polarity flips suck-IN vs
            // blow-OUT. Curl overlay keeps it organic, not a clean implosion.
            float a1 = uSoupdir;
            float a2 = uSoupdir * 1.7 + 1.3;
            vec2 off = (vec2(cos(a1), sin(a1)) * 0.7 + vec2(cos(a2), sin(a2)) * 0.3);
            vec2 foc = cen.xy + off * (uBoxMax.x - uBoxMin.x) * 0.28;
            vec2 pull = (foc - pos.xy) / (length(foc - pos.xy) + 1e-4);
            // mostly beat-pulsed (+ a little sustain) so it doesn't collapse the
            // whole cloud onto the point; the focal point also keeps moving.
            vel.xy += pull * (accent * 0.45 + uModesustain * 0.2) * uBeatpol;
            vel    += (curl * 0.65 + curl2 * 0.25) * drive;
        } else if (fmode == 2) {
            // VORTEX — soft localized swirl (tangential, radial falloff so it's
            // not a rigid box rotation). Sustained so it keeps turning, polarity
            // flips the spin.
            vec2  r    = pos.xy - cen.xy;
            float rn   = length(r / bhalf.xy);
            float fall = smoothstep(0.0, 0.35, rn) * (1.0 - smoothstep(0.65, 1.05, rn));
            vec2  tang = vec2(-r.y, r.x) / (length(r) + 1e-4);
            // smooth one-way swirl + curl for organic break-up (no in/out radial
            // ripple — that read as a back-and-forth wobble).
            vel.xy += tang * uBeatpol * drive * fall;
            vel    += curl * 0.6 * drive;
        } else if (fmode == 3) {
            // WAVEFORM — the 15 reduced-FFT bins define a height curve across X;
            // pull each particle toward it so the soup arranges into the
            // spectrum's SHAPE (audio waveform sculpted from particles). A little
            // curl keeps it from collapsing to a dead line.
            vec3  nrm = (pos - uBoxMin) / max(uBoxMax - uBoxMin, vec3(1e-3));
            int   b   = int(clamp(nrm.x, 0.0, 0.999) * 15.0);
            float amp = uSpectrum[b];
            float ty  = uBoxMin.y + (0.25 + amp * 0.6) * (uBoxMax.y - uBoxMin.y);
            vel.y += (ty - pos.y) * drive * 1.5;          // SUSTAINED → the shape forms
            vel   += curl * 0.3 * drive;
        } else if (fmode == 4) {
            // CURRENT — an organic drifting wind: flows along uSoupdir but its
            // strength waves sinusoidally across the perpendicular axis (+ curl),
            // so it reads as flowing ribbons/currents, not a rigid translation.
            vec2  wind = vec2(cos(uSoupdir), sin(uSoupdir));
            float w    = sin(dot(pos.xy - cen.xy, vec2(-wind.y, wind.x)) * 4.5);
            vel.xy += wind * drive * (0.5 + 0.4 * w);
            vel    += (curl * 0.5 + curl2 * 0.2) * drive;
        } else if (fmode == 5) {
            // FOLD — counter-flow shear about the orbiting axis: the two sides
            // stream in opposite directions and FOLD into each other (taffy-like),
            // softened so the seam isn't a hard line. SUSTAINED so the fold develops.
            vec2  ax   = vec2(cos(uSoupdir), sin(uSoupdir));
            float side = dot(pos.xy - cen.xy, vec2(-ax.y, ax.x)) / bhalf.y;
            vel.xy += ax * tanh(side * 2.0) * drive * 0.7;
            vel    += (curl * 0.5 + curl2 * 0.2) * drive;
        } else if (fmode >= 6) {
            // SHAPE ATTRACTORS — particles briefly ASSUME an abstract 3D shape,
            // tumbled into a different orientation by uSoupdir. Pull each particle
            // toward the nearest point on the shape's surface (sustained drive →
            // the form coalesces during the dwell, then releases on mode switch).
            mat3 R    = rotYP(uSoupdir, uSoupdir * 0.5 + 0.7);   // orientation
            vec3 s    = bhalf * 0.7;                              // shape half-extents (fit box)
            vec3 pl   = transpose(R) * (pos - cen);              // particle in shape-local space
            if (fmode == 9) {
                // TUNNEL — pull onto a cylinder WALL (radial in the local XY) and
                // FLOW along the local axis (Z) so particles stream through a
                // corridor. With a wide Z this reads as flying down a tunnel; the
                // flow direction flips with polarity (toward / away from camera).
                float Rt   = s.x * 0.55;
                vec2  wall = pl.xy / (length(pl.xy) + 1e-4) * Rt;
                vec3  lf   = vec3((wall - pl.xy) * 1.0, uBeatpol * 0.6);  // radial pull + axial flow
                vel += R * lf * drive;
                vel += curl * 0.2 * drive;
            } else {
                vec3 tl;                                          // target in shape-local space
                if (fmode == 6) {
                    // SPHERE/ELLIPSOID shell — points pushed onto the surface radius.
                    tl = normalize(pl + vec3(1e-4)) * s;
                } else if (fmode == 7) {
                    // TORUS — big ring radius in the local XY plane, circular tube.
                    float Rt = s.x * 0.7, rt = s.x * 0.32;
                    vec2  c2 = pl.xy / (length(pl.xy) + 1e-4) * Rt;
                    vec3  q  = pl - vec3(c2, 0.0);
                    tl = vec3(c2, 0.0) + normalize(q + vec3(1e-4)) * rt;
                } else {
                    // SHEET — flatten onto the rotating local Z=0 plane → a tilting
                    // plane of particles (clear 3D-orientation read).
                    tl = vec3(pl.xy, 0.0);
                }
                vec3 tgt = R * tl + cen;
                vel += (tgt - pos) * drive * 1.4;
                vel += curl * 0.25 * drive;                      // keep it alive, not a frozen shell
            }
        } else {
            // REST (mode 0 / default) — downtime. Faint curl breath only, so the
            // field goes calm between active modes.
            vel += curl * 0.12 * drive;
        }
    }

    // ---- MID-PEAK swirl ----------------------------------------------------
    // A rotational burst tangential to the centre on mid peaks — a SECOND, more
    // organic disturbance distinct from the kick's radial gather. Spin direction
    // flips with uSoupdir so successive swirls don't all rotate the same way.
    if (uMidswirl > 0.0) {
        vec3 cen2  = (uBoxMin + uBoxMax) * 0.5;
        vec3 half2 = (uBoxMax - uBoxMin) * 0.5;
        vec2 r     = pos.xy - cen2.xy;
        // radial falloff: a soft ring (peaks mid-radius, →0 at centre AND edge)
        // so the swirl is a localized VORTEX, not a rigid rotation of the whole
        // rectangle. Kills the "rotating rectangle" read.
        float rn   = length(r / half2.xy);
        float fall = smoothstep(0.0, 0.35, rn) * (1.0 - smoothstep(0.65, 1.05, rn));
        vec2 tang  = vec2(-r.y, r.x) / (length(r) + 1e-4);
        float spin = mod(uSoupdir, 6.2831853) > 3.14159265 ? 1.0 : -1.0;
        // tangential swirl + curl for organic break-up (dropped the in/out radial
        // ripple — it read as a back-and-forth wobble).
        vel.xy += tang * spin * uMidswirl * fall;
        vel    += curl * (uMidswirl * fall * 0.6);
    }

    // ---- Wall reflection + hard position clamp -----------------------------
    // Reflecting velocity ALONE is not enough to contain particles: the flip
    // lags one integration step, so a fast particle (or one shoved outward by
    // an edge-of-box field/curl force) overshoots the wall and visibly sits
    // OUTSIDE the box before the flip drags it back — and persistent outward
    // force can let it escape entirely. So we also clamp the position to the
    // wall here and write P back. Because this POP feeds force_null →
    // particle1's "Target Particles Update POP", the clamped P becomes the
    // base position the Particle POP integrates from next cook — the particle
    // deflects off the wall instead of teleporting through it.
    //
    // Requires `P` in the GLSL POP's Output Attributes (alongside PartVel).
    vec3 boxMin = uBoxMin + vec3(uMargin);
    vec3 boxMax = uBoxMax - vec3(uMargin);

    if (pos.x < boxMin.x) { pos.x = boxMin.x; if (vel.x < 0.0) vel.x = -vel.x * uBounce; }
    else if (pos.x > boxMax.x) { pos.x = boxMax.x; if (vel.x > 0.0) vel.x = -vel.x * uBounce; }

    if (pos.y < boxMin.y) { pos.y = boxMin.y; if (vel.y < 0.0) vel.y = -vel.y * uBounce; }
    else if (pos.y > boxMax.y) { pos.y = boxMax.y; if (vel.y > 0.0) vel.y = -vel.y * uBounce; }

    if (pos.z < boxMin.z) { pos.z = boxMin.z; if (vel.z < 0.0) vel.z = -vel.z * uBounce; }
    else if (pos.z > boxMax.z) { pos.z = boxMax.z; if (vel.z > 0.0) vel.z = -vel.z * uBounce; }

    P[id]       = pos;
    PartVel[id] = vel;
}
