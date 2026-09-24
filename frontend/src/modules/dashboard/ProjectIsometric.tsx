import { useEffect, useRef, useState } from "react";

type Vertex = [number, number, number];
type Face = { vertices: Vertex[]; tone: number; accent: boolean };
const faces: Face[] = [];
function box(x: number, y: number, width: number, depth: number, height: number, base = 0, accent = false) {
  const p = (dx: number, dy: number, z: number): Vertex => [x + dx, y + dy, base + z];
  faces.push(
    { vertices: [p(0, 0, height), p(width, 0, height), p(width, depth, height), p(0, depth, height)], tone: 2, accent },
    { vertices: [p(0, 0, 0), p(width, 0, 0), p(width, 0, height), p(0, 0, height)], tone: 0, accent },
    { vertices: [p(width, 0, 0), p(width, depth, 0), p(width, depth, height), p(width, 0, height)], tone: 1, accent },
    { vertices: [p(width, depth, 0), p(0, depth, 0), p(0, depth, height), p(width, depth, height)], tone: 0, accent },
    { vertices: [p(0, depth, 0), p(0, 0, 0), p(0, 0, height), p(0, depth, height)], tone: 1, accent },
  );
}
// Illustrative industrial geometry, not a BIM model or live sensor data.
box(-150, -110, 300, 220, 8, -8);
box(-125, -75, 78, 48, 35);
box(-106, -66, 22, 25, 132, 0, true);
box(-72, -66, 22, 25, 151);
box(-113, -72, 36, 37, 5, 131, true);
box(-79, -72, 36, 37, 5, 150);
box(-25, -82, 71, 48, 31);
box(72, -85, 60, 49, 26);
for (const x of [78, 98, 118]) {
  box(x, -75, 7, 9, 116);
  for (const z of [48, 70, 92, 113]) box(x - 2, -77, 11, 13, 2, z);
}
box(-125, -10, 102, 66, 43);
for (let x = -120; x < -25; x += 9) box(x, 57, 3, 3, 40);
for (const x of [-113, -88, -63, -38]) box(x, 4, 15, 24, 9, 43);
box(9, -12, 60, 59, 61);
box(19, 0, 38, 31, 11, 61);
box(94, -7, 36, 42, 43);
for (let x = -124; x < 122; x += 30) {
  box(x, 75, 15, 20, 23);
  box(x + 5, 60, 4, 17, 4, 19);
  box(x + 5, 58, 4, 4, 23);
}
box(-130, 64, 268, 4, 4, 10);
box(134, -80, 4, 148, 4, 10);
for (let y = -100; y < 100; y += 25) box(143, y, 3, 3, 13);
for (let x = -140; x < 140; x += 25) box(x, 103, 3, 3, 13);

export function ProjectIsometric({ onDocuments, onSchedule, onFinance }: {
  onDocuments: () => void; onSchedule: () => void; onFinance: () => void;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const angle = useRef(.7);
  const drag = useRef<number | null>(null);
  const [rotating, setRotating] = useState(() => typeof window.matchMedia === "function" && !window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  useEffect(() => {
    if (typeof CanvasRenderingContext2D === "undefined") return;
    const element = canvas.current;
    const ctx = element?.getContext("2d");
    if (!element || !ctx) return;
    let frame = 0;
    let last = 0;
    const draw = (now: number) => {
      frame = requestAnimationFrame(draw);
      if (document.hidden || now - last < 33) return;
      const delta = last ? Math.min(now - last, 100) : 0;
      last = now;
      const width = element.clientWidth, height = element.clientHeight;
      if (!width || !height) return;
      if (rotating && drag.current === null) angle.current += delta * .00016;
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      if (element.width !== Math.round(width * ratio) || element.height !== Math.round(height * ratio)) {
        element.width = Math.round(width * ratio); element.height = Math.round(height * ratio);
      }
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      ctx.clearRect(0, 0, width, height);
      const scale = Math.min(width / 460, height / 325);
      const cosine = Math.cos(angle.current), sine = Math.sin(angle.current);
      const project = ([x, y, z]: Vertex) => {
        const horizontal = x * cosine - y * sine, depth = x * sine + y * cosine;
        return { x: width / 2 + horizontal * scale, y: height * .66 + (depth * .47 - z * .88) * scale, depth: depth * .88 + z * .47 };
      };
      const halo = ctx.createRadialGradient(width / 2, height * .61, 0, width / 2, height * .61, width * .48);
      halo.addColorStop(0, "#147bc830"); halo.addColorStop(1, "#07172b00");
      ctx.fillStyle = halo; ctx.fillRect(0, 0, width, height);
      const sorted = faces.map(face => ({ ...face, points: face.vertices.map(project) }))
        .sort((a, b) => a.points.reduce((sum, p) => sum + p.depth, 0) / 4 - b.points.reduce((sum, p) => sum + p.depth, 0) / 4);
      for (const face of sorted) {
        ctx.beginPath(); face.points.forEach((p, i) => i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)); ctx.closePath();
        ctx.fillStyle = (face.accent ? ["#1676b7", "#249fda", "#9befff"] : ["#1d4364", "#386d92", "#a0cfe4"])[face.tone];
        ctx.strokeStyle = face.accent ? "#72e6ff" : "#70b1d1";
        ctx.lineWidth = .6; ctx.fill(); ctx.stroke();
      }
    };
    frame = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(frame);
  }, [rotating]);
  return <figure className="project-isometric" aria-label="Вращающаяся иллюстрация промышленного объекта">
    <div className="plant-scene-label"><span>ОБЪЕКТ / ОБЗОР</span><button type="button" aria-pressed={rotating} onClick={() => setRotating(!rotating)}>{rotating ? "Остановить вращение" : "Включить вращение"}</button></div>
    <canvas ref={canvas} aria-label="Объёмная модель промышленного комплекса" tabIndex={0}
      onPointerDown={(event) => { drag.current = event.clientX; event.currentTarget.setPointerCapture(event.pointerId); }}
      onPointerMove={(event) => { if (drag.current !== null) { angle.current += (event.clientX - drag.current) * .009; drag.current = event.clientX; } }}
      onPointerUp={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }} onLostPointerCapture={() => { drag.current = null; }}
      onKeyDown={(event) => { if (event.key === "ArrowLeft" || event.key === "ArrowRight") { event.preventDefault(); setRotating(false); angle.current += event.key === "ArrowLeft" ? -.15 : .15; } }} />
    <nav className="plant-scene-actions" aria-label="Разделы объекта">
      <button type="button" onClick={onDocuments}>Документы ↗</button><button type="button" onClick={onSchedule}>ГПР ↗</button><button type="button" onClick={onFinance}>ДДС ↗</button>
    </nav>
    <figcaption>Иллюстрация · поворот мышью или стрелками</figcaption>
  </figure>;
}
