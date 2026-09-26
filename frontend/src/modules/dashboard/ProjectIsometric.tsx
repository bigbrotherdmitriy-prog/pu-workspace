import { useEffect, useRef, useState } from "react";
import * as THREE from "three";

type Props = { onDocuments: () => void; onSchedule: () => void; onFinance: () => void };
const ASSETS = `${import.meta.env.BASE_URL}assets/future-light/`;
const TURNTABLE_WEBM = `${ASSETS}data-center-turntable.webm`;
const TURNTABLE_MP4 = `${ASSETS}data-center-turntable.mp4`;
const POSTER_ASSET = `${ASSETS}data-center-turntable-poster.jpg`;

// Видео «вертушка» снято на чёрном фоне. Шейдер убирает чёрный, оставляя сам объект с мягким краем,
// и добавляет неоновую подсветку контура (голубой слева, оранжевый справа).
const vertexShader = `varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }`;
const fragmentShader = `
  uniform sampler2D map; uniform float time; varying vec2 vUv;
  void main(){
    vec4 c = texture2D(map, vUv);
    float lum = max(max(c.r, c.g), c.b);
    float alpha = smoothstep(0.035, 0.16, lum);
    vec3 rim = mix(vec3(0.0, 0.86, 1.0), vec3(1.0, 0.42, 0.08), vUv.x);
    float edge = alpha * (1.0 - smoothstep(0.16, 0.42, lum));
    vec3 col = c.rgb * 1.08 + rim * edge * 0.55;
    gl_FragColor = vec4(col, alpha);
  }`;

export function ProjectIsometric({ onSchedule, onFinance }: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const [fallback, setFallback] = useState(false);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    // jsdom has a canvas element but no WebGL implementation. Use the poster in
    // unit tests; real browsers still exercise the video shader path.
    if (navigator.userAgent.includes("jsdom")) {
      setFallback(true);
      return;
    }

    const video = document.createElement("video");
    video.src = video.canPlayType('video/webm; codecs="vp9"') ? TURNTABLE_WEBM : TURNTABLE_MP4;
    video.poster = POSTER_ASSET;
    video.muted = true;
    video.loop = true;
    video.playsInline = true;
    video.preload = "auto";
    // Вращение в 2 раза медленнее.
    video.playbackRate = 0.5;
    video.defaultPlaybackRate = 0.5;
    video.crossOrigin = "anonymous";
    video.setAttribute("playsinline", "");

    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true, powerPreference: "high-performance" });
    } catch {
      setFallback(true);
      return;
    }
    renderer.setClearColor(0, 0);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    host.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    const camera = new THREE.OrthographicCamera(-0.5, 0.5, 0.5, -0.5, 0.1, 10);
    camera.position.z = 2;
    const texture = new THREE.VideoTexture(video);
    texture.colorSpace = THREE.SRGBColorSpace;
    const material = new THREE.ShaderMaterial({ uniforms: { map: { value: texture }, time: { value: 0 } }, vertexShader, fragmentShader, transparent: true });
    const plane = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), material);
    scene.add(plane);

    const resize = () => {
      const width = Math.max(host.clientWidth, 1), height = Math.max(host.clientHeight, 1);
      renderer.setSize(width, height, false);
      // Сохраняем пропорции видео 16:9 внутри блока.
      const videoAspect = 16 / 9, boxAspect = width / height;
      // Кадр вписывается целиком (contain) — края модели не обрезаются.
      plane.scale.set(boxAspect > videoAspect ? videoAspect / boxAspect : 1, boxAspect > videoAspect ? 1 : boxAspect / videoAspect, 1);
      plane.position.y = 0;
    };
    const observer = new ResizeObserver(resize);
    observer.observe(host);
    resize();

    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const start = () => { if (!reducedMotion.matches) video.play().catch(() => undefined); };
    video.addEventListener("loadeddata", () => { video.playbackRate = 0.5; start(); }, { once: true });
    video.addEventListener("error", () => setFallback(true), { once: true });

    // Поворот пальцем/мышью: перетаскивание прокручивает «вертушку», после отпускания вращение продолжается.
    let dragging = false, lastX = 0;
    const down = (event: PointerEvent) => { dragging = true; lastX = event.clientX; video.pause(); host.setPointerCapture(event.pointerId); };
    const move = (event: PointerEvent) => {
      if (!dragging || !video.duration) return;
      const dx = event.clientX - lastX; lastX = event.clientX;
      const next = (video.currentTime - dx * (video.duration / Math.max(host.clientWidth, 1)) * 1.2) % video.duration;
      video.currentTime = next < 0 ? next + video.duration : next;
    };
    const up = (event: PointerEvent) => { if (!dragging) return; dragging = false; host.releasePointerCapture(event.pointerId); start(); };
    host.addEventListener("pointerdown", down);
    host.addEventListener("pointermove", move);
    host.addEventListener("pointerup", up);
    host.addEventListener("pointercancel", up);

    let frame = 0;
    const animate = () => {
      renderer.render(scene, camera);
      if (video.readyState >= 2) renderer.domElement.dataset.frameReady = "true";
      frame = requestAnimationFrame(animate);
    };
    animate();

    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      host.removeEventListener("pointerdown", down);
      host.removeEventListener("pointermove", move);
      host.removeEventListener("pointerup", up);
      host.removeEventListener("pointercancel", up);
      video.pause();
      video.removeAttribute("src");
      video.load();
      texture.dispose();
      material.dispose();
      plane.geometry.dispose();
      renderer.dispose();
      renderer.domElement.remove();
    };
  }, []);

  return <figure className="project-isometric" aria-label="Интерактивная модель центра обработки данных">
    <div className="fl-model-glow" aria-hidden="true" />
    <div className="future-twin-model-wrap future-twin-webgl" ref={hostRef} role="img" aria-label="Вращающаяся модель центра обработки данных">
      {fallback && <img className="future-twin-model" src={POSTER_ASSET} alt="Модель центра обработки данных" />}
    </div>
    <nav className="plant-scene-actions" aria-label="Разделы объекта">
      <button type="button" onClick={onSchedule}><i aria-hidden="true" />ГПР</button>
      <button type="button" onClick={onFinance}><i aria-hidden="true" />ДДС</button>
    </nav>
  </figure>;
}
