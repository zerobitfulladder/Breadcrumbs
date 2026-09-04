import { useEffect, useRef, useState } from "react";
import L from "leaflet";
import { api } from "../api";
import type { MapPoint } from "../api";
import "leaflet/dist/leaflet.css";
import "./AffiliationMap.css";

interface Props {
  /** library: everyone. author / paper: just that subject's affiliations. */
  scope: "library" | "author" | "paper";
  id?: number | null;
  refreshKey?: number;
}

// OpenStreetMap's own tiles: no account, no key, no quota to trip. CARTO's
// basemaps look better but meter their free tier and start demanding a key,
// which is not a dependency worth having in a local tool. The dark look is
// recreated with a CSS filter instead.
const TILES = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const ATTRIBUTION = "&copy; OpenStreetMap contributors";

/**
 * One Leaflet map over a set of points.
 *
 * Its own component so the inline map and the expanded one are the same code
 * with a different container; Leaflet needs a real instance per element, and
 * duplicating the marker logic would let the two drift apart.
 */
function MapCanvas({
  points,
  interactive,
  className,
}: {
  points: MapPoint[];
  interactive: boolean;
  className: string;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const layerRef = useRef<L.LayerGroup | null>(null);

  useEffect(() => {
    if (!hostRef.current || mapRef.current) return;
    const map = L.map(hostRef.current, {
      zoomControl: false,
      attributionControl: false,
      worldCopyJump: true,
      // Dragging works on both maps. Only the wheel is held back on the small
      // one, so scrolling over it still scrolls the sidebar rather than
      // zooming — the two gestures do not conflict, the wheel does.
      scrollWheelZoom: interactive,
      doubleClickZoom: true,
    }).setView([25, 0], 1);

    L.tileLayer(TILES, { attribution: ATTRIBUTION, maxZoom: 19 }).addTo(map);
    L.control.zoom({ position: "bottomright" }).addTo(map);
    L.control.attribution({ position: "bottomleft", prefix: false }).addTo(map);
    layerRef.current = L.layerGroup().addTo(map);
    mapRef.current = map;

    return () => {
      map.remove();
      mapRef.current = null;
      layerRef.current = null;
    };
  }, [interactive]);

  useEffect(() => {
    const map = mapRef.current;
    const layer = layerRef.current;
    if (!map || !layer) return;

    layer.clearLayers();
    if (!points.length) return;

    const maxAuthors = Math.max(...points.map((p) => p.author_count));
    for (const point of points) {
      // A marker is one institution however many authors it holds; size shows
      // how many, so duplicates stay merged without losing that information.
      const share = maxAuthors > 1 ? point.author_count / maxAuthors : 1;
      const marker = L.circleMarker([point.lat, point.lon], {
        radius: (interactive ? 6 : 5) + share * (interactive ? 10 : 7),
        weight: 2,
        color: point.current ? "#10b981" : "#4f8ef7",
        fillColor: point.current ? "#10b981" : "#4f8ef7",
        fillOpacity: 0.35,
      });
      marker.bindTooltip(
        `<strong>${point.name}</strong>` +
          (point.city ? `<br>${point.city}` : "") +
          (point.country_code ? ` (${point.country_code})` : "") +
          `<br>${point.authors.map((a) => a.name).join(", ")}` +
          (point.current ? "<br><em>most recent</em>" : ""),
        { direction: "top", opacity: 0.95 },
      );
      marker.addTo(layer);
    }

    const bounds = L.latLngBounds(points.map((p) => [p.lat, p.lon] as [number, number]));
    map.fitBounds(bounds, {
      padding: interactive ? [60, 60] : [26, 26],
      maxZoom: points.length === 1 ? 6 : 10,
    });
    // The container is often revealed or resized after mount, so the map has
    // to be told its size changed or it renders into a stale box.
    window.setTimeout(() => map.invalidateSize(), 60);
  }, [points, interactive]);

  return <div className={className} ref={hostRef} />;
}

/** Where the people behind these papers work. */
export default function AffiliationMap({ scope, id = null, refreshKey = 0 }: Props) {
  const [points, setPoints] = useState<MapPoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [unplaced, setUnplaced] = useState(0);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .affiliationMap(scope, id)
      .then((d) => {
        if (cancelled) return;
        setPoints(d.points);
        setUnplaced(d.unplaced);
      })
      .catch(() => !cancelled && setPoints([]))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [scope, id, refreshKey]);

  useEffect(() => {
    if (!expanded) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setExpanded(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [expanded]);

  const title =
    scope === "library"
      ? "Where the library comes from"
      : scope === "author"
        ? "Their affiliations"
        : "Author affiliations";
  const hasCurrent = scope === "author" && points.some((p) => p.current);

  return (
    <div className="am-root">
      <div className="am-head">
        <h4>{title}</h4>
        <span className="am-count">
          {loading ? "…" : `${points.length} place${points.length === 1 ? "" : "s"}`}
        </span>
        <button
          className="am-expand"
          onClick={() => setExpanded(true)}
          disabled={!points.length}
          title="Open a larger map"
          aria-label="Open a larger map"
        >
          ⤢
        </button>
      </div>

      <MapCanvas points={points} interactive={false} className="am-canvas" />

      {!loading && !points.length && (
        <p className="am-empty">
          No affiliations with a known location
          {unplaced
            ? `, and ${unplaced} institution${unplaced === 1 ? "" : "s"} could not be placed`
            : ""}
          .
        </p>
      )}
      {hasCurrent && (
        <div className="am-legend">
          <span className="am-dot is-current" /> most recent
          <span className="am-dot" /> earlier
        </div>
      )}

      {expanded && (
        <div
          className="am-backdrop"
          onPointerDown={() => setExpanded(false)}
          role="dialog"
          aria-modal="true"
          aria-label={title}
        >
          <div className="am-modal" onPointerDown={(e) => e.stopPropagation()}>
            <div className="am-modal-bar">
              <strong>{title}</strong>
              <span className="am-count">
                {points.length} place{points.length === 1 ? "" : "s"}
                {unplaced ? ` · ${unplaced} unplaced` : ""}
              </span>
              {hasCurrent && (
                <span className="am-legend">
                  <span className="am-dot is-current" /> most recent
                  <span className="am-dot" /> earlier
                </span>
              )}
              <button className="am-close" onClick={() => setExpanded(false)}>
                Close
              </button>
            </div>
            <MapCanvas points={points} interactive className="am-modal-canvas" />
          </div>
        </div>
      )}
    </div>
  );
}
