// Photoreal finale: the string leaves Carol's building as one city light on the real Earth,
// spreads city to city, then a Starship carries it from Starbase to Mars.
// Imagery (all NASA, public domain): Blue Marble NG (July), Black Marble 2016 city lights,
// Blue Marble clouds, NASA 3D Resources Mars, NASA/Goddard SVS Deep Star Maps 2020.
import React, { useLayoutEffect, useMemo } from "react";
import { useLoader, useThree } from "@react-three/fiber";
import { ThreeCanvas } from "@remotion/three";
import { staticFile } from "remotion";
import * as THREE from "three";
import { ramp } from "./lib";

// ---------- timeline (seconds) ----------

export const SPACE = {
  in: 23.15, // Earth starts to show through the shrinking buildings
  arcs: 23.8, // first string leaves Prague
  launch: 24.95, // Starship lifts off from Starbase
  land: 27.35, // Starship touches down on Mars
};

// ---------- geometry helpers ----------

type V = THREE.Vector3;
const v3 = (x: number, y: number, z: number) => new THREE.Vector3(x, y, z);
const RAD = Math.PI / 180;

// Unit vector for (lat, lon) in the Earth mesh's local frame (matches SphereGeometry's UVs).
const geo = (lat: number, lon: number): V => {
  const phi = (lon + 180) * RAD;
  return v3(-Math.cos(phi) * Math.cos(lat * RAD), Math.sin(lat * RAD), Math.sin(phi) * Math.cos(lat * RAD));
};

const CITY: Record<string, [number, number]> = {
  prague: [50.08, 14.42],
  nyc: [40.71, -74.0],
  sf: [37.77, -122.42],
  starbase: [25.99, -97.16],
  saoPaulo: [-23.55, -46.63],
  lagos: [6.52, 3.38],
  nairobi: [-1.29, 36.82],
  capeTown: [-33.92, 18.42],
  mumbai: [19.08, 72.88],
  moscow: [55.76, 37.62],
  reykjavik: [64.15, -21.94],
  tokyo: [35.68, 139.69],
  singapore: [1.35, 103.82],
};
// [from, to, start offset after SPACE.arcs]
const ARCS: [string, string, number][] = [
  ["prague", "nyc", 0.0],
  ["prague", "lagos", 0.12],
  ["prague", "mumbai", 0.22],
  ["prague", "moscow", 0.3],
  ["prague", "saoPaulo", 0.4],
  ["prague", "nairobi", 0.5],
  ["prague", "starbase", 0.58],
  ["prague", "reykjavik", 0.66],
  ["nyc", "sf", 0.78],
  ["nyc", "saoPaulo", 0.86],
  ["lagos", "capeTown", 0.92],
  ["mumbai", "singapore", 0.98],
  ["mumbai", "tokyo", 1.06],
  ["nairobi", "capeTown", 1.12],
];
const ARC_DRAW = 0.5; // seconds to draw one string

const EARTH_YAW = -(90 + CITY.prague[1]) * RAD; // Prague faces +Z at t = SPACE.in
const earthYaw = (t: number) => EARTH_YAW + (t - SPACE.in) * 1.2 * RAD;
const SUN = v3(-1, 0.22, 0.1).normalize(); // Europe at dusk: Americas in daylight, Asia lit up
const MARS = v3(7.6, 1.3, -4.2);
const MARS_R = 0.72;

const toWorld = (local: V, t: number) => local.clone().applyAxisAngle(v3(0, 1, 0), earthYaw(t));

const STRING = new THREE.Color("#ffc46b");

// ---------- camera ----------

type Pose = { t: number; target: V; dir: V; dist: number };
const PRAGUE_UP = toWorld(geo(...CITY.prague), SPACE.in);
const POSES: Pose[] = [
  { t: SPACE.in - 0.2, target: v3(0, 0, 0), dir: PRAGUE_UP.clone(), dist: 1.3 },
  { t: SPACE.in + 0.35, target: v3(0, 0, 0), dir: PRAGUE_UP.clone(), dist: 1.55 },
  { t: 25.05, target: v3(-0.15, 0.1, 0), dir: v3(-0.3, 0.52, 0.8).normalize(), dist: 4.3 },
  { t: 26.5, target: v3(2.4, 0.45, -1.3), dir: v3(0.05, 0.26, 1).normalize(), dist: 11.0 },
  { t: 28.3, target: v3(3.5, 0.6, -1.9), dir: v3(0.08, 0.2, 1).normalize(), dist: 13.4 },
];
// eased per leg but never fully stops at a key (same shape as the 2D zoom)
const legEase = (x: number) => 0.3 * x + 0.7 * x * x * (3 - 2 * x);
const cameraAt = (t: number) => {
  const k = POSES.findIndex((p) => p.t >= t);
  if (k <= 0) return POSES[k === 0 ? 0 : POSES.length - 1];
  const a = POSES[k - 1];
  const b = POSES[k];
  const e = legEase((t - a.t) / (b.t - a.t));
  const qa = new THREE.Quaternion();
  const q = new THREE.Quaternion().setFromUnitVectors(a.dir, b.dir);
  const dir = a.dir.clone().applyQuaternion(qa.slerp(q, e));
  return { t, target: a.target.clone().lerp(b.target, e), dir, dist: a.dist * Math.pow(b.dist / a.dist, e) };
};

const Camera: React.FC<{ t: number }> = ({ t }) => {
  const camera = useThree((s) => s.camera);
  useLayoutEffect(() => {
    const c = cameraAt(t);
    camera.position.copy(c.target).addScaledVector(c.dir, c.dist);
    camera.up.set(0, 1, 0);
    camera.lookAt(c.target);
    camera.updateMatrixWorld();
  }, [t, camera]);
  return null;
};

// ---------- textures ----------

// useLoader suspends, and <ThreeCanvas> holds the frame (delayRender) until the image is in
const useTex = (file: string, srgb = true) => {
  const tex = useLoader(THREE.TextureLoader, staticFile(file));
  tex.colorSpace = srgb ? THREE.SRGBColorSpace : THREE.NoColorSpace;
  tex.anisotropy = 8;
  return tex;
};

const glowTexture = (() => {
  let tex: THREE.Texture | null = null;
  return () => {
    if (tex) return tex;
    const c = document.createElement("canvas");
    c.width = c.height = 128;
    const g = c.getContext("2d")!;
    const r = g.createRadialGradient(64, 64, 0, 64, 64, 64);
    r.addColorStop(0, "rgba(255,255,255,1)");
    r.addColorStop(0.18, "rgba(255,255,255,0.55)");
    r.addColorStop(0.45, "rgba(255,255,255,0.12)");
    r.addColorStop(1, "rgba(255,255,255,0)");
    g.fillStyle = r;
    g.fillRect(0, 0, 128, 128);
    tex = new THREE.CanvasTexture(c);
    return tex;
  };
})();

const Glow: React.FC<{ position: V; size: number; color: THREE.Color | string; opacity: number }> = ({ position, size, color, opacity }) =>
  opacity <= 0.001 ? null : (
    <sprite position={position} scale={[size, size, 1]}>
      <spriteMaterial map={glowTexture()} color={color} transparent opacity={opacity} blending={THREE.AdditiveBlending} depthWrite={false} />
    </sprite>
  );

// ---------- Earth ----------

const VERT = /* glsl */ `
varying vec2 vUv; varying vec3 vN; varying vec3 vP;
void main() {
  vUv = uv;
  vN = normalize(mat3(modelMatrix) * normal);
  vec4 wp = modelMatrix * vec4(position, 1.0);
  vP = wp.xyz;
  gl_Position = projectionMatrix * viewMatrix * wp;
}`;

const EARTH_FRAG = /* glsl */ `
uniform sampler2D dayMap; uniform sampler2D nightMap; uniform sampler2D cloudMap;
uniform vec3 sunDir; uniform float cloudShift; uniform float fade;
varying vec2 vUv; varying vec3 vN; varying vec3 vP;
void main() {
  vec3 n = normalize(vN);
  vec3 v = normalize(cameraPosition - vP);
  float ndl = dot(n, sunDir);
  vec3 day = texture2D(dayMap, vUv).rgb;
  float cl = texture2D(cloudMap, vUv + vec2(cloudShift, 0.0)).r;
  float water = smoothstep(0.004, 0.03, day.b - day.r);
  vec3 surf = mix(day, vec3(0.92), cl * 0.92);
  float dayAmt = smoothstep(-0.10, 0.20, ndl);
  vec3 col = surf * (max(ndl, 0.0) * 1.5 + 0.004);
  vec3 h = normalize(sunDir + v);
  col += vec3(1.0, 0.9, 0.75) * pow(max(dot(n, h), 0.0), 70.0) * water * (1.0 - cl) * 0.55 * dayAmt;
  vec3 night = texture2D(nightMap, vUv).rgb;
  float lum = max(night.r, night.g);
  vec3 city = vec3(1.0, 0.72, 0.38) * smoothstep(0.03, 0.5, lum) * 3.2;
  col += (city + night * 0.12) * (1.0 - dayAmt) * (1.0 - cl * 0.7);
  float term = exp(-pow(ndl / 0.14, 2.0));
  col *= mix(vec3(1.0), vec3(1.35, 0.72, 0.5), term * 0.55);
  float fr = pow(1.0 - max(dot(n, v), 0.0), 2.4);
  col += vec3(0.25, 0.5, 1.0) * fr * smoothstep(-0.3, 0.45, ndl) * 0.9;
  gl_FragColor = vec4(col * fade, 1.0);
  #include <colorspace_fragment>
}`;

// back-face shell: soft glow hugging the limb, brightest on the sunlit side
const ATMOS_FRAG = /* glsl */ `
uniform vec3 sunDir; uniform vec3 tint; uniform float strength; uniform float edge;
varying vec2 vUv; varying vec3 vN; varying vec3 vP;
void main() {
  vec3 n = normalize(vN);
  vec3 v = normalize(cameraPosition - vP);
  float i = pow(clamp(-dot(n, v) / edge, 0.0, 1.0), 3.0);
  float lit = smoothstep(-0.35, 0.5, dot(n, sunDir));
  gl_FragColor = vec4(tint * i * (0.15 + 0.85 * lit) * strength, 1.0);
  #include <colorspace_fragment>
}`;

const Earth: React.FC<{ t: number; fade: number }> = ({ t, fade }) => {
  const day = useTex("space/earth_day.jpg");
  const night = useTex("space/earth_night.jpg");
  const clouds = useTex("space/earth_clouds.jpg", false);
  const mat = useMemo(
    () =>
      new THREE.ShaderMaterial({
        vertexShader: VERT,
        fragmentShader: EARTH_FRAG,
        uniforms: {
          dayMap: { value: day },
          nightMap: { value: night },
          cloudMap: { value: clouds },
          sunDir: { value: SUN },
          cloudShift: { value: 0 },
          fade: { value: 1 },
        },
      }),
    [day, night, clouds],
  );
  mat.uniforms.cloudShift.value = (t - SPACE.in) * 0.0015;
  mat.uniforms.fade.value = fade;
  const atmos = useMemo(
    () =>
      new THREE.ShaderMaterial({
        vertexShader: VERT,
        fragmentShader: ATMOS_FRAG,
        uniforms: { sunDir: { value: SUN }, tint: { value: new THREE.Color(0.35, 0.62, 1.0) }, strength: { value: 1 }, edge: { value: 0.3 } },
        side: THREE.BackSide,
        blending: THREE.AdditiveBlending,
        transparent: true,
        depthWrite: false,
      }),
    [],
  );
  atmos.uniforms.strength.value = 0.9 * fade;
  return (
    <group>
      <mesh rotation={[0, earthYaw(t), 0]} material={mat}>
        <sphereGeometry args={[1, 160, 120]} />
      </mesh>
      <mesh material={atmos}>
        <sphereGeometry args={[1.05, 96, 72]} />
      </mesh>
    </group>
  );
};

// ---------- strings ----------

const STRING_FRAG = /* glsl */ `
uniform float progress; uniform float time; uniform vec3 color; uniform float opacity; uniform float back;
varying vec2 vUv; varying vec3 vN; varying vec3 vP;
void main() {
  if (vUv.x > progress) discard;
  float head = smoothstep(progress - 0.06, progress, vUv.x) * (1.0 - step(0.999, progress));
  float leg = mod(time, 2.0);
  float p = back > 0.5 ? 1.0 - fract(time) : (leg < 1.0 ? leg : 2.0 - leg);
  float pulse = exp(-pow((vUv.x - p) / 0.035, 2.0));
  vec3 c = color * (0.55 + 2.2 * head + 2.4 * pulse);
  gl_FragColor = vec4(c * opacity, 1.0);
  #include <colorspace_fragment>
}`;

const stringMaterial = () =>
  new THREE.ShaderMaterial({
    vertexShader: VERT,
    fragmentShader: STRING_FRAG,
    uniforms: { progress: { value: 0 }, time: { value: 0 }, color: { value: STRING }, opacity: { value: 1 }, back: { value: 0 } },
    blending: THREE.AdditiveBlending,
    transparent: true,
    depthWrite: false,
  });

// Great-circle arc lifted off the surface, in Earth-local coordinates.
const arcCurve = (a: V, b: V) => {
  const angle = a.angleTo(b);
  const lift = 0.04 + 0.22 * (angle / Math.PI);
  const q = new THREE.Quaternion();
  const pts = Array.from({ length: 49 }, (_, i) => {
    const s = i / 48;
    const qs = q.clone().setFromUnitVectors(a, b);
    const p = a.clone().applyQuaternion(new THREE.Quaternion().slerp(qs, s));
    return p.multiplyScalar(1.004 + lift * Math.sin(Math.PI * s));
  });
  return new THREE.CatmullRomCurve3(pts);
};

const Arc: React.FC<{ from: string; to: string; start: number; t: number }> = ({ from, to, start, t }) => {
  const { geom, mat, end } = useMemo(() => {
    const a = geo(...CITY[from]);
    const b = geo(...CITY[to]);
    const curve = arcCurve(a, b);
    return { geom: new THREE.TubeGeometry(curve, 96, 0.003, 6, false), mat: stringMaterial(), end: b.multiplyScalar(1.006) };
  }, [from, to]);
  const p = ramp(t, start, start + ARC_DRAW);
  if (p <= 0) return null;
  mat.uniforms.progress.value = p;
  mat.uniforms.time.value = (t - start) / 1.4 + start;
  const hit = ramp(t, start + ARC_DRAW - 0.05, start + ARC_DRAW + 0.1);
  const flash = hit * (1 - ramp(t, start + ARC_DRAW + 0.1, start + ARC_DRAW + 0.7));
  return (
    <>
      <mesh geometry={geom} material={mat} />
      <Glow position={end} size={0.07 + 0.22 * flash} color={STRING} opacity={0.6 * hit + 0.6 * flash} />
    </>
  );
};

// ---------- Starship ----------

// Unit-length Starship pointing +Y, engines at y = 0. Stainless body, black heat-shield tiles
// on the belly (-Z half), four flaps, six Raptors.
const Starship: React.FC<{ env: THREE.Texture | null; burn: number; t: number }> = ({ env, burn, t }) => {
  const parts = useMemo(() => {
    const steel = new THREE.MeshStandardMaterial({ color: "#d2d5da", metalness: 0.9, roughness: 0.3, envMapIntensity: 1.3 });
    const tiles = new THREE.MeshStandardMaterial({ color: "#16171a", metalness: 0.1, roughness: 0.85 });
    const raptor = new THREE.MeshStandardMaterial({ color: "#3a3b3f", metalness: 0.8, roughness: 0.45, side: THREE.DoubleSide });
    const R = 0.09;
    const noseProfile = Array.from({ length: 24 }, (_, i) => {
      const s = i / 23;
      return new THREE.Vector2(R * Math.pow(Math.max(1 - Math.pow(s, 2.3), 0), 0.62), 0.62 + 0.38 * s);
    });
    const plume = new THREE.ShaderMaterial({
      vertexShader: /* glsl */ `varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
      fragmentShader: /* glsl */ `
        uniform float burn; uniform float flicker; varying vec2 vUv;
        void main() {
          float along = vUv.y;                // 1 at the nozzle, 0 at the tail
          float core = pow(along, 2.2);
          float edge = 1.0 - abs(fract(vUv.x * 2.0) - 0.5) * 2.0;
          vec3 c = mix(vec3(0.35, 0.55, 1.0), vec3(1.0, 0.85, 0.6), core);
          gl_FragColor = vec4(c * core * (0.4 + 0.6 * edge) * burn * flicker * 1.6, 1.0);
          #include <colorspace_fragment>
        }`,
      uniforms: { burn: { value: 0 }, flicker: { value: 1 } },
      blending: THREE.AdditiveBlending,
      transparent: true,
      depthWrite: false,
      side: THREE.DoubleSide,
    });
    return { steel, tiles, raptor, R, noseProfile, plume };
  }, []);
  const { steel, tiles, raptor, R, noseProfile, plume } = parts;
  steel.envMap = env;
  plume.uniforms.burn.value = burn;
  plume.uniforms.flicker.value = 0.85 + 0.15 * Math.sin(t * 91) * Math.sin(t * 37);
  const flap = (y: number, h: number, w: number, side: 1 | -1) => (
    <mesh position={[side * (R + w / 2 - 0.004), y, -0.02]} material={tiles}>
      <boxGeometry args={[w, h, 0.012]} />
    </mesh>
  );
  const raptors = [0, 1, 2, 3, 4, 5].map((i) => {
    const inner = i < 3;
    const a = (i * 120 + (inner ? 0 : 60)) * RAD;
    const r = inner ? 0.028 : 0.062;
    return (
      <mesh key={i} position={[Math.cos(a) * r, -0.018, Math.sin(a) * r]} material={raptor}>
        <cylinderGeometry args={[0.009, inner ? 0.018 : 0.024, 0.04, 20, 1, true]} />
      </mesh>
    );
  });
  return (
    <group>
      <mesh position={[0, 0.31, 0]} material={steel}>
        <cylinderGeometry args={[R, R, 0.62, 48]} />
      </mesh>
      <mesh position={[0, 0.31, 0]} material={tiles}>
        <cylinderGeometry args={[R * 1.004, R * 1.004, 0.62, 48, 1, true, Math.PI / 2, Math.PI]} />
      </mesh>
      <mesh material={steel}>
        <latheGeometry args={[noseProfile, 48]} />
      </mesh>
      <mesh material={tiles} scale={[1.006, 1, 1.006]}>
        <latheGeometry args={[noseProfile, 48, Math.PI / 2, Math.PI]} />
      </mesh>
      {flap(0.8, 0.12, 0.05, 1)}
      {flap(0.8, 0.12, 0.05, -1)}
      {flap(0.1, 0.17, 0.075, 1)}
      {flap(0.1, 0.17, 0.075, -1)}
      <mesh position={[0, 0.001, 0]} rotation={[Math.PI / 2, 0, 0]} material={raptor}>
        <circleGeometry args={[R, 48]} />
      </mesh>
      {raptors}
      {burn > 0.01 && (
        <mesh position={[0, -0.36, 0]} material={plume}>
          <cylinderGeometry args={[0.07, 0.3, 0.64, 32, 1, true]} />
        </mesh>
      )}
    </group>
  );
};

// Soft space-like reflection environment for the steel: black sky, blue Earth-glow below, the Sun.
const useSpaceEnv = () => {
  const gl = useThree((s) => s.gl);
  return useMemo(() => {
    const scene = new THREE.Scene();
    const sky = new THREE.Mesh(
      new THREE.SphereGeometry(10, 32, 16),
      new THREE.ShaderMaterial({
        side: THREE.BackSide,
        uniforms: { sun: { value: SUN } },
        vertexShader: /* glsl */ `varying vec3 vD; void main(){ vD = normalize(position); gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
        fragmentShader: /* glsl */ `
          uniform vec3 sun; varying vec3 vD;
          void main() {
            vec3 d = normalize(vD);
            vec3 c = vec3(0.02, 0.025, 0.035) + vec3(0.12, 0.22, 0.4) * smoothstep(0.1, -0.7, d.y);
            c += vec3(1.0, 0.95, 0.88) * pow(max(dot(d, sun), 0.0), 60.0) * 14.0;
            c += vec3(0.5, 0.45, 0.4) * pow(max(dot(d, sun), 0.0), 4.0) * 0.5;
            gl_FragColor = vec4(c, 1.0);
          }`,
      }),
    );
    scene.add(sky);
    const pmrem = new THREE.PMREMGenerator(gl);
    const rt = pmrem.fromScene(scene, 0.02);
    pmrem.dispose();
    return rt.texture;
  }, [gl]);
};

// Launch from Starbase, big arc over Earth, landing flip onto Mars.
const shipPath = (() => {
  const base = toWorld(geo(...CITY.starbase), SPACE.launch);
  const site = v3(-1, 0.55, 0.75).normalize();
  const land = MARS.clone().addScaledVector(site, MARS_R);
  const curve = new THREE.CubicBezierCurve3(
    base.clone().multiplyScalar(1.0),
    base.clone().multiplyScalar(2.2).add(v3(0.6, 0.9, 0.6)),
    MARS.clone().addScaledVector(site, 2.6).add(v3(0, 0.9, 0)),
    land,
  );
  return { curve, base, site, land };
})();

const Ship: React.FC<{ t: number; env: THREE.Texture | null }> = ({ t, env }) => {
  const camera = useThree((s) => s.camera);
  const { curve, site } = shipPath;
  const trail = useMemo(() => ({ geom: new THREE.TubeGeometry(curve, 240, 0.012, 6, false), mat: stringMaterial() }), [curve]);
  if (t < SPACE.launch) return null;
  // slow off the pad, fast cruise, braking into Mars
  const x = (t - SPACE.launch) / (SPACE.land - SPACE.launch);
  const p = x >= 1 ? 1 : x < 0.18 ? 0.5 * Math.pow(x / 0.18, 2) * 0.1 : 0.05 + 0.95 * legEase((x - 0.18) / 0.82);
  const pos = curve.getPointAt(Math.min(p, 1));
  const tan = curve.getTangentAt(Math.min(p, 0.999));
  // landing flip: from nose-first to tail-down over the last stretch
  const flip = ramp(t, SPACE.land - 0.55, SPACE.land - 0.1);
  const fwd = new THREE.Quaternion().setFromUnitVectors(v3(0, 1, 0), tan);
  const upright = new THREE.Quaternion().setFromUnitVectors(v3(0, 1, 0), site);
  const q = fwd.slerp(upright, flip);
  // keep the ship readable on screen: size tracks camera distance, smaller as it reaches Mars
  const dist = camera.position.distanceTo(pos);
  const size = Math.min(0.055 * dist, 0.6) * (1 - 0.2 * ramp(t, SPACE.land - 0.9, SPACE.land));
  const lift = ramp(t, SPACE.launch, SPACE.launch + 0.25);
  const burn = x < 0 ? 0 : Math.max(1 - ramp(t, SPACE.launch + 0.9, SPACE.launch + 1.3) * 0.75, flip) * (1 - ramp(t, SPACE.land, SPACE.land + 0.08));
  const hover = pos.clone().addScaledVector(site, size * 0.05 * (1 - ramp(t, SPACE.land - 0.2, SPACE.land)));
  trail.mat.uniforms.progress.value = Math.max(p - 0.004, 0);
  trail.mat.uniforms.time.value = 0;
  trail.mat.uniforms.opacity.value = 0.95;
  const nozzle = pos.clone().add(v3(0, -1, 0).applyQuaternion(q).multiplyScalar(size * 0.08));
  return (
    <>
      <mesh geometry={trail.geom} material={trail.mat} />
      <group position={hover} quaternion={q} scale={size * (0.6 + 0.4 * lift)}>
        <Starship env={env} burn={burn} t={t} />
      </group>
      <Glow position={nozzle} size={size * 0.9} color="#ffd9a8" opacity={0.85 * burn} />
    </>
  );
};

// Once the ship lands, a reply runs back down the string from Mars to Earth.
const Reply: React.FC<{ t: number }> = ({ t }) => {
  const { curve } = shipPath;
  const pulse = useMemo(() => ({ geom: new THREE.TubeGeometry(curve, 240, 0.02, 6, false), mat: stringMaterial() }), [curve]);
  if (t < SPACE.land + 0.05) return null;
  const k = ramp(t, SPACE.land + 0.05, SPACE.land + 0.75);
  pulse.mat.uniforms.progress.value = 1;
  pulse.mat.uniforms.back.value = 1;
  pulse.mat.uniforms.time.value = k * 0.98;
  pulse.mat.uniforms.opacity.value = 0.7 * (1 - ramp(k, 0.85, 1));
  return <mesh geometry={pulse.geom} material={pulse.mat} />;
};

// ---------- Mars ----------

const Mars: React.FC<{ t: number }> = ({ t }) => {
  const map = useTex("space/mars.jpg");
  const atmos = useMemo(
    () =>
      new THREE.ShaderMaterial({
        vertexShader: VERT,
        fragmentShader: ATMOS_FRAG,
        uniforms: { sunDir: { value: SUN }, tint: { value: new THREE.Color(1.0, 0.55, 0.35) }, strength: { value: 0.6 }, edge: { value: 0.3 } },
        side: THREE.BackSide,
        blending: THREE.AdditiveBlending,
        transparent: true,
        depthWrite: false,
      }),
    [],
  );
  const landed = ramp(t, SPACE.land, SPACE.land + 0.2);
  const ring = ramp(t, SPACE.land, SPACE.land + 0.6);
  const site = shipPath.land.clone().addScaledVector(shipPath.site, 0.01);
  return (
    <group>
      <mesh position={MARS} rotation={[0.12, -1.9 + (t - 24) * 0.02, 0]}>
        <sphereGeometry args={[MARS_R, 128, 96]} />
        <meshStandardMaterial map={map} roughness={1} metalness={0} />
      </mesh>
      <mesh position={MARS} material={atmos}>
        <sphereGeometry args={[MARS_R * 1.05, 64, 48]} />
      </mesh>
      <Glow position={site} size={0.2 + 1.6 * ring} color={STRING} opacity={landed * (1 - ring) * 1.4} />
      <Glow position={site} size={0.3} color={STRING} opacity={landed} />
    </group>
  );
};

// ---------- scene ----------

const Scene: React.FC<{ t: number }> = ({ t }) => {
  const env = useSpaceEnv();
  const fade = ramp(t, SPACE.in, SPACE.in + 0.45);
  const hub = toWorld(geo(...CITY.prague), t).multiplyScalar(1.006);
  const hubGlow = ramp(t, SPACE.in, SPACE.in + 0.3);
  const marsIn = ramp(t, 25.2, 26.0);
  return (
    <>
      <Camera t={t} />
      <ambientLight intensity={0.03} />
      <directionalLight position={SUN.clone().multiplyScalar(50)} intensity={3.2} />
      <Earth t={t} fade={fade} />
      <group rotation={[0, earthYaw(t), 0]}>
        {ARCS.map(([a, b, dt]) => (
          <Arc key={a + b} from={a} to={b} start={SPACE.arcs + dt} t={t} />
        ))}
      </group>
      <Glow position={hub} size={0.12 + 0.1 * (1 - hubGlow)} color={STRING} opacity={hubGlow} />
      {marsIn > 0 && <Mars t={t} />}
      <Ship t={t} env={env} />
      <Reply t={t} />
    </>
  );
};

export const Space: React.FC<{ t: number }> = ({ t }) => (
  <ThreeCanvas
    width={1920}
    height={1080}
    style={{ position: "absolute", inset: 0 }}
    camera={{ fov: 35, near: 0.01, far: 400, position: [0, 0, 5] }}
    gl={{ antialias: true, alpha: true, preserveDrawingBuffer: true }}
    flat
  >
    <Scene t={t} />
  </ThreeCanvas>
);
