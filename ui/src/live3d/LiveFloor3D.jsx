import React, { Component, useEffect, useMemo, useRef, useState } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { Edges, Html, Line } from "@react-three/drei";
import * as THREE from "three";
import { activeAt, layoutFloor, strongestEffect } from "./timeline.js";

const STATE_COLOR = { o: "#78afe8", d: "#f3c15f", c: "#b89de8", r: "#79c995", "?": "#d8dde5" };
const WALL = "#d7e0e8";

function FixedCamera() {
  const { camera, size } = useThree();
  useEffect(() => {
    camera.zoom = Math.max(28, Math.min(42, size.width / 25));
    camera.lookAt(0, 0, 0);
    camera.updateProjectionMatrix();
  }, [camera, size.width, size.height]);
  return null;
}

function useReducedMotion() {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    const media = matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(media.matches);
    update();
    media.addEventListener?.("change", update);
    return () => media.removeEventListener?.("change", update);
  }, []);
  return reduced;
}

function Wall({ position, size }) {
  return (
    <mesh position={position} castShadow receiveShadow>
      <boxGeometry args={size} />
      <meshStandardMaterial color={WALL} transparent opacity={0.38} roughness={0.7} depthWrite={false} />
      <Edges color="#91a4b7" threshold={15} />
    </mesh>
  );
}

function RoomFloor({ item, state }) {
  const material = useRef();
  const target = useMemo(() => new THREE.Color(STATE_COLOR[state] || STATE_COLOR["?"]), [state]);
  useFrame((_, delta) => {
    if (!material.current) return;
    const step = Math.min(1, delta * 5);
    material.current.color.lerp(target, step);
    material.current.emissive.lerp(target, step);
  });
  return (
    <group>
      <mesh position={[item.x, 0.06, item.z]} receiveShadow>
        <boxGeometry args={[item.width - 0.08, 0.12, item.depth - 0.08]} />
        <meshStandardMaterial ref={material} color={target} emissive={target} emissiveIntensity={0.08} roughness={0.82} />
      </mesh>
      <Html position={[item.x, 0.135, item.z]} rotation={[-Math.PI / 2, 0, 0]} transform center style={{ pointerEvents: "none" }}>
        <span className="room-label-3d">{item.room.id.slice(1)}</span>
      </Html>
    </group>
  );
}

function FloorModel({ rooms, states, slot, model }) {
  const wallHeight = 1.7;
  const thickness = 0.09;
  const door = 0.86;
  const half = model.totalDepth / 2;
  const sideWalls = [];
  for (const item of Object.values(model.bounds)) {
    const innerZ = item.sign * model.corridor / 2;
    const segment = Math.max(0.08, (item.width - door) / 2);
    sideWalls.push(
      <React.Fragment key={`inner-${item.room.id}`}>
        <Wall position={[item.x - (door + segment) / 2, wallHeight / 2, innerZ]} size={[segment, wallHeight, thickness]} />
        <Wall position={[item.x + (door + segment) / 2, wallHeight / 2, innerZ]} size={[segment, wallHeight, thickness]} />
      </React.Fragment>,
    );
  }
  const cuts = [...new Set(Object.values(model.bounds).flatMap((x) => [x.x - x.width / 2, x.x + x.width / 2]))];
  return (
    <group>
      <mesh position={[0, -0.035, 0]} receiveShadow>
        <boxGeometry args={[model.length + 0.3, 0.07, model.totalDepth + 0.3]} />
        <meshStandardMaterial color="#f1f3f6" roughness={0.95} />
      </mesh>
      <mesh position={[0, 0.015, 0]} receiveShadow>
        <boxGeometry args={[model.length, 0.04, model.corridor]} />
        <meshStandardMaterial color="#e9edf1" roughness={0.92} />
      </mesh>
      {Object.values(model.bounds).map((item) => <RoomFloor key={item.room.id} item={item} state={(states?.[item.room.id] || "")[slot] || "?"} />)}
      <Wall position={[0, wallHeight / 2, -half]} size={[model.length, wallHeight, thickness]} />
      <Wall position={[0, wallHeight / 2, half]} size={[model.length, wallHeight, thickness]} />
      <Wall position={[-model.length / 2, wallHeight / 2, 0]} size={[thickness, wallHeight, model.totalDepth]} />
      <Wall position={[model.length / 2, wallHeight / 2, 0]} size={[thickness, wallHeight, model.totalDepth]} />
      {cuts.slice(1, -1).flatMap((x) => [
        <Wall key={`n-${x}`} position={[x, wallHeight / 2, -(model.corridor + model.depth) / 2]} size={[thickness, wallHeight, model.depth]} />,
        <Wall key={`s-${x}`} position={[x, wallHeight / 2, (model.corridor + model.depth) / 2]} size={[thickness, wallHeight, model.depth]} />,
      ])}
      {sideWalls}
    </group>
  );
}

function Actor({ actor, target, origin, reduced }) {
  const ref = useRef();
  const born = useRef(performance.now());
  const curve = useMemo(() => new THREE.CatmullRomCurve3([
    new THREE.Vector3(...origin),
    new THREE.Vector3(target.door[0], 0.34, 0),
    new THREE.Vector3(...target.door),
    new THREE.Vector3(...target.task),
  ]), [origin, target]);
  useFrame(() => {
    if (!ref.current) return;
    const p = reduced ? 1 : Math.min(1, (performance.now() - born.current) / 3600);
    ref.current.position.copy(curve.getPoint(p));
  });
  const robot = actor.role === "robot";
  const color = robot ? "#1677ff" : "#e65252";
  return (
    <>
      <Line points={curve.getPoints(28)} color={color} lineWidth={1.4} transparent opacity={0.3} dashed dashSize={0.22} gapSize={0.14} />
      <group ref={ref}>
        <mesh castShadow>
          {robot ? <cylinderGeometry args={[0.24, 0.28, 0.18, 24]} /> : <sphereGeometry args={[0.24, 24, 16]} />}
          <meshStandardMaterial color={color} emissive={robot ? "#0d4fa8" : "#8d2525"} emissiveIntensity={0.32} />
        </mesh>
        <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.18, 0]}>
          <ringGeometry args={[0.3, 0.38, robot ? 24 : 4]} />
          <meshBasicMaterial color={color} transparent opacity={0.55} side={THREE.DoubleSide} />
        </mesh>
      </group>
    </>
  );
}

function Effect({ effect, target, model, emphasized, reduced }) {
  const ref = useRef();
  const born = useRef(performance.now());
  const base = target?.task || [0, 0.2, 0];
  useFrame(({ clock }) => {
    if (!ref.current || reduced) return;
    const age = (performance.now() - born.current) / 1000;
    const pulse = emphasized && age < 3 ? 1 + Math.sin(clock.elapsedTime * 7) * 0.16 : 1 + Math.sin(clock.elapsedTime * 2) * 0.04;
    ref.current.scale.setScalar(pulse);
  });
  if (effect.kind === "sensor") {
    return (
      <mesh ref={ref} position={[0, 0.16, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <planeGeometry args={[model.length, model.totalDepth, 18, 10]} />
        <meshBasicMaterial color="#7958bd" wireframe transparent opacity={emphasized ? 0.18 : 0.09} />
      </mesh>
    );
  }
  const color = effect.kind === "water" ? "#19a9e5" : effect.kind === "heating" ? "#ef743f" : "#e09a00";
  return (
    <group ref={ref} position={base}>
      {effect.kind === "water" && [0, 1, 2].map((n) => (
        <mesh key={n} rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.02 + n * 0.015, 0]} scale={0.65 + n * 0.35}>
          <ringGeometry args={[0.25, 0.31, 32]} />
          <meshBasicMaterial color={color} transparent opacity={(emphasized ? 0.8 : 0.45) / (n + 1)} side={THREE.DoubleSide} />
        </mesh>
      ))}
      {effect.kind === "heating" && (
        <mesh position={[0, 0.35, 0]}>
          <sphereGeometry args={[0.72, 24, 16]} />
          <meshBasicMaterial color={color} transparent opacity={emphasized ? 0.19 : 0.1} depthWrite={false} />
        </mesh>
      )}
      {effect.kind === "turnaround" && (
        <Line points={[
          [-target.width / 2 + 0.12, -0.2, -target.depth / 2 + 0.12],
          [target.width / 2 - 0.12, -0.2, -target.depth / 2 + 0.12],
          [target.width / 2 - 0.12, -0.2, target.depth / 2 - 0.12],
          [-target.width / 2 + 0.12, -0.2, target.depth / 2 - 0.12],
          [-target.width / 2 + 0.12, -0.2, -target.depth / 2 + 0.12],
        ]} color={color} lineWidth={emphasized ? 2.8 : 1.6} transparent opacity={emphasized ? 0.8 : 0.38} />
      )}
      {effect.warning_active && (
        <Html position={[0, 1.3, 0]} center style={{ pointerEvents: "none" }}>
          <span className={`incident-pin ${effect.priority === "critical" ? "critical" : "approval"}`}>!</span>
        </Html>
      )}
    </group>
  );
}

function Scene({ rooms, states, slot, actors, effects, now, reduced, recentActors }) {
  const model = useMemo(() => layoutFloor(rooms), [rooms]);
  const activeActors = actors.filter((x) => recentActors.has(x.id) || activeAt(x, now, "depart_utc", "task_end_utc"));
  const activeEffects = effects.filter((x) => activeAt(x, now));
  const strongest = strongestEffect(activeEffects);
  return (
    <>
      <ambientLight intensity={1.8} />
      <directionalLight position={[6, 13, 9]} intensity={2.4} castShadow shadow-mapSize={[1024, 1024]} />
      <FixedCamera />
      <FloorModel rooms={rooms} states={states} slot={slot} model={model} />
      {activeActors.map((a) => model.bounds[a.room_id] && <Actor key={a.id} actor={a} target={model.bounds[a.room_id]} origin={model.origin} reduced={reduced} />)}
      {activeEffects.map((e) => <Effect key={e.id} effect={e} target={model.bounds[e.room_id]} model={model} emphasized={e.id === strongest?.id} reduced={reduced} />)}
    </>
  );
}

class SceneBoundary extends Component {
  constructor(props) { super(props); this.state = { failed: false }; }
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error) { this.props.onFailure?.(error); }
  render() { return this.state.failed ? this.props.fallback : this.props.children; }
}

export function webGLAvailable() {
  try {
    const canvas = document.createElement("canvas");
    return Boolean(canvas.getContext("webgl2") || canvas.getContext("webgl"));
  } catch { return false; }
}

export default function LiveFloor3D({ rooms, states, slot, actors = [], effects = [], now, fallback, onFailure }) {
  const reduced = useReducedMotion();
  const available = useMemo(webGLAvailable, []);
  const previousNow = useRef(now);
  const actorTimers = useRef([]);
  const [recentActors, setRecentActors] = useState(() => new Set());
  useEffect(() => () => actorTimers.current.forEach(clearTimeout), []);
  useEffect(() => {
    const previous = previousNow.current;
    previousNow.current = now;
    if (now < previous) {
      setRecentActors(new Set());
      return undefined;
    }
    const fresh = actors.filter((actor) => {
      const depart = Date.parse(actor.depart_utc);
      return depart > previous && depart <= now;
    });
    if (!fresh.length) return undefined;
    setRecentActors((current) => new Set([...current, ...fresh.map((x) => x.id)]));
    const ids = new Set(fresh.map((x) => x.id));
    actorTimers.current.push(setTimeout(() => setRecentActors((current) => new Set([...current].filter((id) => !ids.has(id)))), 4200));
    return undefined;
  }, [actors, now]);
  useEffect(() => { if (!available) onFailure?.(new Error("WebGL unavailable")); }, [available, onFailure]);
  if (!available) return fallback;
  return (
    <SceneBoundary fallback={fallback} onFailure={onFailure}>
      <div className="live3d" role="img" aria-label={`3D floor with ${rooms.length} rooms`}>
        <Canvas orthographic shadows dpr={[1, 1.75]} camera={{ position: [14, 14, 17], zoom: 34, near: 0.1, far: 100 }}
          onCreated={({ camera }) => camera.lookAt(0, 0, 0)} gl={{ alpha: true, antialias: true }}>
          <Scene rooms={rooms} states={states} slot={slot} actors={actors} effects={effects} now={now} reduced={reduced} recentActors={recentActors} />
        </Canvas>
      </div>
    </SceneBoundary>
  );
}

