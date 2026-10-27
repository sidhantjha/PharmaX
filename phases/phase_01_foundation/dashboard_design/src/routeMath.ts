import type { GeoPath } from './GlobeScene';

export type Viewport = { width: number; height: number };
export type RouteView = { lat: number; lng: number; altitude: number };

function minimalLongitudeBounds(values: number[]) {
  const sorted = values.map(value => ((value % 360) + 360) % 360).sort((a, b) => a - b);
  if (sorted.length < 2) return { start: sorted[0] ?? 0, span: 0 };
  let largestGap = -1;
  let start = sorted[0];
  for (let i = 0; i < sorted.length; i++) {
    const current = sorted[i];
    const next = i === sorted.length - 1 ? sorted[0] + 360 : sorted[i + 1];
    if (next - current > largestGap) {
      largestGap = next - current;
      start = next % 360;
    }
  }
  return { start, span: 360 - largestGap };
}

export function fitRouteView(path: GeoPath, viewport: Viewport, extraPoints: [number, number][] = []): RouteView {
  const coords = [...path.coords, ...extraPoints].filter(([lng, lat]) => Number.isFinite(lng) && Number.isFinite(lat));
  if (!coords.length) return { lat: 0, lng: 0, altitude: 1.5 };
  const lats = coords.map(([, lat]) => lat);
  const { start, span: lonSpan } = minimalLongitudeBounds(coords.map(([lng]) => lng));
  const latMin = Math.min(...lats);
  const latMax = Math.max(...lats);
  const lat = (latMin + latMax) / 2;
  const lng = ((start + lonSpan / 2 + 540) % 360) - 180;
  const latRadius = Math.max(0.025, Math.abs(latMax - latMin) * Math.PI / 360);
  const lonRadius = Math.max(0.025, Math.abs(lonSpan) * Math.PI / 360 * Math.max(0.2, Math.cos(lat * Math.PI / 180)));
  const fov = 45 * Math.PI / 180;
  const aspect = Math.max(0.45, viewport.width / Math.max(1, viewport.height));
  const padding = 0.78;
  const verticalDistance = Math.cos(latRadius) + Math.sin(latRadius) / (Math.tan(fov / 2) * padding);
  const horizontalDistance = Math.cos(lonRadius) + Math.sin(lonRadius) / (Math.tan(fov / 2) * aspect * padding);
  const distance = Math.max(verticalDistance, horizontalDistance, 1.12);
  return { lat, lng, altitude: Math.max(0.12, Math.min(2.85, distance - 1)) };
}

export function focusRouteView(path:GeoPath,viewport:Viewport,point:[number,number],maxAltitude=.82):RouteView {
  const fit=fitRouteView(path,viewport,[point]);
  return {...fit,lat:point[1],lng:point[0],altitude:Math.min(fit.altitude,maxAltitude)};
}

export function pointAlongPath(coords: number[][], progress: number): { lng: number; lat: number; nextLng: number; nextLat: number } {
  const points = coords.map(([lng, lat]) => ({ lng, lat }));
  if (points.length < 2) {
    const p = points[0] ?? { lng: 0, lat: 0 };
    return { ...p, nextLng: p.lng + 0.01, nextLat: p.lat };
  }
  const distances = points.slice(1).map((p, i) => Math.hypot((p.lng - points[i].lng) * Math.cos(p.lat * Math.PI / 180), p.lat - points[i].lat));
  const total = distances.reduce((sum, value) => sum + value, 0);
  let remaining = (Math.max(0, Math.min(0.999999, progress)) * total);
  for (let i = 0; i < distances.length; i++) {
    if (remaining <= distances[i]) {
      const t = distances[i] ? remaining / distances[i] : 0;
      const a = points[i]; const b = points[i + 1];
      return { lng: a.lng + (b.lng - a.lng) * t, lat: a.lat + (b.lat - a.lat) * t, nextLng: b.lng, nextLat: b.lat };
    }
    remaining -= distances[i];
  }
  const last = points[points.length - 1]; const prev = points[points.length - 2];
  return { ...last, nextLng: last.lng + (last.lng - prev.lng), nextLat: last.lat + (last.lat - prev.lat) };
}
