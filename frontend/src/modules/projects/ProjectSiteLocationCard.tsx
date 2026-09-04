import { useEffect, useState } from "react";
import { Crosshair, ExternalLink, LoaderCircle, MapPin, Navigation } from "lucide-react";
import { api } from "../../api/client";
import "./project-site-location.css";

type SiteLocation = {
  configured: boolean;
  latitude: number | null;
  longitude: number | null;
  label: string | null;
  radius_m: number | null;
  accuracy_m: number | null;
};

type CurrentPosition = {
  latitude: number;
  longitude: number;
  accuracy: number;
};

const DEFAULT_RADIUS_M = 250;

export function distanceMeters(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const earthRadiusM = 6_371_000;
  const radians = (degrees: number) => degrees * Math.PI / 180;
  const latitudeDelta = radians(lat2 - lat1);
  const longitudeDelta = radians(lon2 - lon1);
  const a = Math.sin(latitudeDelta / 2) ** 2
    + Math.cos(radians(lat1)) * Math.cos(radians(lat2)) * Math.sin(longitudeDelta / 2) ** 2;
  return earthRadiusM * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function formatDistance(metres: number): string {
  if (metres < 1_000) return `${Math.round(metres)} м`;
  return `${(metres / 1_000).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} км`;
}

function geolocationError(error: GeolocationPositionError): string {
  if (error.code === error.PERMISSION_DENIED) return "Доступ к геопозиции запрещён. Разрешите его для этого сайта и повторите.";
  if (error.code === error.POSITION_UNAVAILABLE) return "Не удалось определить геопозицию. Проверьте GPS и сеть.";
  if (error.code === error.TIMEOUT) return "GPS не ответил вовремя. Попробуйте ещё раз на открытом месте.";
  return "Не удалось определить геопозицию.";
}

function getCurrentPosition(): Promise<CurrentPosition> {
  return new Promise((resolve, reject) => {
    if (!navigator.geolocation) {
      reject(new Error("Геопозиция не поддерживается этим браузером."));
      return;
    }
    navigator.geolocation.getCurrentPosition(
      ({ coords }) => resolve({ latitude: coords.latitude, longitude: coords.longitude, accuracy: coords.accuracy }),
      (error) => reject(new Error(geolocationError(error))),
      { enableHighAccuracy: true, timeout: 15_000, maximumAge: 30_000 },
    );
  });
}

export function ProjectSiteLocationCard({ projectId }: { projectId: number }) {
  const [site, setSite] = useState<SiteLocation | null>(null);
  const [label, setLabel] = useState("");
  const [radiusM, setRadiusM] = useState(DEFAULT_RADIUS_M);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    setMessage("");
    void api<SiteLocation>(`/projects/${projectId}/site-location`)
      .then((result) => {
        if (!active) return;
        setSite(result);
        setLabel(result.label || "");
        setRadiusM(result.radius_m || DEFAULT_RADIUS_M);
      })
      .catch((reason: Error) => active && setError(reason.message))
      .finally(() => active && setLoading(false));
    return () => { active = false; };
  }, [projectId]);

  const unsupported = typeof navigator !== "undefined" && !navigator.geolocation;

  async function checkDistance() {
    if (!site?.configured || site.latitude == null || site.longitude == null) return;
    setWorking(true);
    setError("");
    setMessage("");
    try {
      const current = await getCurrentPosition();
      const metres = distanceMeters(current.latitude, current.longitude, site.latitude, site.longitude);
      const inside = metres <= (site.radius_m || DEFAULT_RADIUS_M);
      setMessage(inside
        ? `Вы на объекте · ${formatDistance(metres)} от точки по прямой`
        : `До объекта ${formatDistance(metres)} по прямой`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось определить геопозицию.");
    } finally {
      setWorking(false);
    }
  }

  async function saveCurrentAsSite() {
    setWorking(true);
    setError("");
    setMessage("");
    try {
      const current = await getCurrentPosition();
      const result = await api<SiteLocation>(`/projects/${projectId}/site-location`, {
        method: "PUT",
        body: JSON.stringify({
          latitude: current.latitude,
          longitude: current.longitude,
          label: label.trim() || null,
          radius_m: radiusM,
          accuracy_m: current.accuracy,
        }),
      });
      setSite(result);
      setLabel(result.label || "");
      setRadiusM(result.radius_m || DEFAULT_RADIUS_M);
      setMessage("Точка объекта сохранена.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось сохранить точку объекта.");
    } finally {
      setWorking(false);
    }
  }

  const mapUrl = site?.configured && site.latitude != null && site.longitude != null
    ? `https://www.openstreetmap.org/?mlat=${site.latitude}&mlon=${site.longitude}#map=17/${site.latitude}/${site.longitude}`
    : "";

  return <section className="card site-location-card" aria-labelledby="site-location-title">
    <div className="site-location-icon"><MapPin aria-hidden="true" /></div>
    <div className="site-location-main">
      <div className="site-location-heading">
        <div><span className="today-eyebrow">GPS ОБЪЕКТА</span><h2 id="site-location-title">{site?.configured ? site.label || "Точка объекта" : "Местоположение объекта"}</h2></div>
        {site?.configured && <span className="site-location-radius">радиус {site.radius_m || DEFAULT_RADIUS_M} м</span>}
      </div>

      {loading && <p className="site-location-muted"><LoaderCircle className="spin" /> Загружаем точку объекта…</p>}

      {!loading && !site?.configured && <div className="site-location-setup">
        <p>Сохраните текущее место как центр стройплощадки. Доступно менеджеру проекта.</p>
        <div className="site-location-fields">
          <label>Название места<input value={label} maxLength={500} placeholder="Например, стройплощадка" onChange={(event) => setLabel(event.target.value)} /></label>
          <label>Радиус объекта<select value={radiusM} onChange={(event) => setRadiusM(Number(event.target.value))}><option value={100}>100 м</option><option value={250}>250 м</option><option value={500}>500 м</option><option value={1000}>1 км</option></select></label>
        </div>
        <button type="button" className="site-location-primary" disabled={working || unsupported} onClick={() => void saveCurrentAsSite()}><Crosshair />{working ? "Определяем…" : "Установить объект по текущему месту"}</button>
      </div>}

      {!loading && site?.configured && <div className="site-location-actions">
        <button type="button" className="site-location-primary" disabled={working || unsupported} onClick={() => void checkDistance()}><Navigation />{working ? "Определяем…" : "Проверить моё местоположение"}</button>
        <button type="button" className="site-location-secondary" disabled={working || unsupported} onClick={() => void saveCurrentAsSite()}><Crosshair />Обновить точку текущим местом</button>
        <a href={mapUrl} target="_blank" rel="noreferrer">Открыть на карте <ExternalLink /></a>
      </div>}

      {unsupported && !loading && <p className="site-location-error" role="alert">Геопозиция не поддерживается этим браузером.</p>}
      {error && <p className="site-location-error" role="alert">{error}</p>}
      {message && <p className="site-location-result" aria-live="polite">{message}</p>}
      {!loading && <small className="site-location-privacy">GPS запрашивается только после нажатия. При проверке расстояния ваша позиция не сохраняется.</small>}
    </div>
  </section>;
}
