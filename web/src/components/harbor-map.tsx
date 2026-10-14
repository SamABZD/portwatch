"use client";

import { useEffect, useRef, useState } from "react";
import type { GeoJSONSource, Map as LibreMap } from "maplibre-gl";
import type { FeatureCollection } from "geojson";
import type { Dataset, Frame, Vessel, Zone } from "@/lib/types";

type Props = {
  dataset: Dataset;
  frame: Frame | null;
  selected: string | null;
  onSelect: (mmsi: string) => void;
  overlays: boolean;
  trails: boolean;
  stale: boolean;
  basemap: boolean;
};

function hasPosition(vessel: Vessel) {
  return typeof vessel.longitude === "number" && Number.isFinite(vessel.longitude)
    && typeof vessel.latitude === "number" && Number.isFinite(vessel.latitude);
}

function sources(props: Props) {
  const vessels = (props.frame?.vessels || []).filter(
    (v) => hasPosition(v) && (props.stale || !v.stale),
  );
  return {
    points: {
      type: "FeatureCollection" as const,
      features: vessels.map((v) => ({
        type: "Feature" as const,
        geometry: {
          type: "Point" as const,
          coordinates: [v.longitude!, v.latitude!],
        },
        properties: { mmsi: v.mmsi, stale: v.stale },
      })),
    },
    trails: {
      type: "FeatureCollection" as const,
      features: (props.frame?.trails || [])
        .filter((t) => props.stale || !t.stale)
        .map((t) => ({
          type: "Feature" as const,
          geometry: {
            type: "LineString" as const,
            coordinates: t.points.map((p) => [p[0], p[1]]),
          },
          properties: { mmsi: t.mmsi },
        })),
    },
  };
}

export default function HarborMap(props: Props) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<LibreMap | null>(null);
  const current = useRef(props);
  current.current = props;
  const [renderer, setRenderer] = useState<"loading" | "tiles" | "vector">(
    "loading",
  );
  const [failure, setFailure] = useState<"tiles" | "renderer" | null>(null);
  const [viewport, setViewport] = useState({ width: 720, height: 440 });

  useEffect(() => {
    const element = container.current;
    if (!element) return;
    const measure = () => {
      const width = element.clientWidth;
      const height = element.clientHeight;
      if (width <= 0 || height <= 0) return;
      setViewport(previous => previous.width === width && previous.height === height
        ? previous : { width, height });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    let cancelled = false;
    if (!props.basemap) {
      setFailure(null);
      setRenderer("vector");
      return;
    }
    setFailure(null);
    setRenderer("loading");
    import("maplibre-gl")
      .then((maplibre) => {
        if (cancelled || !container.current) return;
        try {
          maplibre.setWorkerUrl(
            new URL(
              "/vendor/maplibre/maplibre-gl-worker.mjs",
              window.location.origin,
            ).href,
          );
          const instance = new maplibre.Map({
            container: container.current,
            center: [
              (props.dataset.bounds[0] + props.dataset.bounds[2]) / 2,
              (props.dataset.bounds[1] + props.dataset.bounds[3]) / 2,
            ],
            zoom: 12,
            cooperativeGestures: true,
            attributionControl: { compact: true },
            style: {
              version: 8,
              sources: {
                osm: {
                  type: "raster",
                  tileSize: 256,
                  tiles: [
                    process.env.NEXT_PUBLIC_MAP_TILE_URL ||
                      "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
                  ],
                  attribution:
                    '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',
                },
              },
              layers: [
                {
                  id: "basemap",
                  type: "raster",
                  source: "osm",
                  paint: {
                    "raster-saturation": -0.8,
                    "raster-contrast": -0.12,
                  },
                },
              ],
            },
          });
          map.current = instance;
          instance.addControl(
            new maplibre.NavigationControl({ showCompass: false }),
            "bottom-left",
          );
          instance.fitBounds(
            [
              [props.dataset.bounds[0], props.dataset.bounds[1]],
              [props.dataset.bounds[2], props.dataset.bounds[3]],
            ],
            { padding: { top: 60, right: 28, bottom: 84, left: 52 }, duration: 0 },
          );
          instance.on("error", (event) => {
            if (cancelled || map.current !== instance) return;
            setFailure("sourceId" in event && event.sourceId === "osm" ? "tiles" : "renderer");
            setRenderer("vector");
            map.current = null;
            instance.remove();
          });
          instance.on("load", () => {
            if (cancelled || map.current !== instance) return;
            const data = sources(current.current);
            instance.addSource("zones", {
              type: "geojson",
              data: props.dataset.zones as FeatureCollection,
            });
            instance.addLayer({
              id: "zone-fill",
              type: "fill",
              source: "zones",
              paint: {
                "fill-color": [
                  "match",
                  ["get", "zone_type"],
                  "waiting_candidate",
                  "#b97732",
                  "terminal",
                  "#557b81",
                  "#4d7d8f",
                ],
                "fill-opacity": 0.12,
              },
            });
            instance.addLayer({
              id: "zone-line",
              type: "line",
              source: "zones",
              paint: {
                "line-color": "#667f88",
                "line-width": 1.2,
                "line-dasharray": [4, 3],
              },
            });
            instance.addSource("trails", {
              type: "geojson",
              data: data.trails,
            });
            instance.addLayer({
              id: "trail-lines",
              type: "line",
              source: "trails",
              paint: {
                "line-color": "#377e83",
                "line-opacity": 0.5,
                "line-width": 1.4,
              },
            });
            instance.addSource("vessels", {
              type: "geojson",
              data: data.points,
            });
            instance.addLayer({
              id: "vessel-points",
              type: "circle",
              source: "vessels",
              paint: {
                "circle-radius": 5,
                "circle-color": [
                  "case",
                  ["get", "stale"],
                  "#8a959c",
                  "#08767d",
                ],
                "circle-stroke-color": "#ffffff",
                "circle-stroke-width": 1.6,
              },
            });
            instance.on("click", "vessel-points", (event) => {
              const mmsi = event.features?.[0]?.properties?.mmsi;
              if (mmsi) current.current.onSelect(String(mmsi));
            });
            instance.on("mouseenter", "vessel-points", () => {
              instance.getCanvas().style.cursor = "pointer";
            });
            instance.on("mouseleave", "vessel-points", () => {
              instance.getCanvas().style.cursor = "";
            });
            setRenderer("tiles");
          });
        } catch {
          map.current?.remove();
          map.current = null;
          setFailure("renderer");
          setRenderer("vector");
        }
      })
      .catch(() => {
        if (!cancelled) {
          setFailure("renderer");
          setRenderer("vector");
        }
      });
    return () => {
      cancelled = true;
      map.current?.remove();
      map.current = null;
    };
  }, [props.basemap, props.dataset.dataset_id, props.dataset.geofence_version]);

  useEffect(() => {
    const instance = map.current;
    if (renderer !== "tiles" || !instance?.getSource("vessels")) return;
    const data = sources(props);
    (instance.getSource("vessels") as GeoJSONSource).setData(data.points);
    (instance.getSource("trails") as GeoJSONSource).setData(data.trails);
    for (const id of ["zone-fill", "zone-line"])
      instance.setLayoutProperty(
        id,
        "visibility",
        props.overlays ? "visible" : "none",
      );
    instance.setLayoutProperty(
      "trail-lines",
      "visibility",
      props.trails ? "visible" : "none",
    );
    instance.setPaintProperty("vessel-points", "circle-radius", [
      "case",
      ["==", ["get", "mmsi"], props.selected || ""],
      8,
      5,
    ]);
    instance.setPaintProperty("vessel-points", "circle-stroke-color", [
      "case",
      ["==", ["get", "mmsi"], props.selected || ""],
      "#9e421a",
      "#ffffff",
    ]);
  }, [props, renderer]);

  return (
    <div className="map-shell">
    <div className="map-stage" aria-label="Vessel map">
      <div
        ref={container}
        className="tile-map"
        style={{ visibility: renderer === "tiles" ? "visible" : "hidden" }}
      />
      {renderer !== "tiles" && <VectorMap {...props} viewport={viewport} />}
      <div className="map-caption" role="status">
        <span className="map-north">↑ N</span>
        <span>
          {renderer === "tiles"
            ? props.dataset.port_name
            : failure === "tiles"
              ? "Basemap tiles unavailable · local geometry active"
              : failure === "renderer"
                ? "Map renderer unavailable · local geometry active"
                : renderer === "loading"
                  ? "Loading basemap · local geometry active"
                  : "Basemap off · local geometry active"}
        </span>
      </div>
      {props.frame && props.frame.vessels.length === 0 && (
        <div className="map-empty">
          <strong>
            {props.dataset.mode === "historical"
              ? "No observations at this cutoff"
              : "No saved reports in this period"}
          </strong>
          <span>
            {props.dataset.mode === "historical"
              ? "Move forward in the replay to see actual reports."
              : "Source health remains separate from traffic. Collection runs independently."}
          </span>
        </div>
      )}
      {props.frame &&
        props.frame.vessels.length > 0 &&
        !props.stale &&
        props.frame.vessels.every((v) => v.stale) && (
          <div className="map-empty">
            <strong>No fresh observations</strong>
            <span>
              Saved reports are outside the {props.frame.staleness_minutes}
              -minute cutoff. Turn on Stale to inspect last known locations.
            </span>
          </div>
        )}
    </div>
    <div className="map-footer">
      <div className="map-legend">
        <span>
          <i className="legend-point" />
          Fresh observation
        </span>
        <span>
          <i className="legend-point stale-dot" />
          {props.stale ? "Stale observation" : "Stale points hidden"}
        </span>
        <span>
          Gaps &gt;{" "}
          {Math.round((props.frame?.max_trail_gap_seconds || 900) / 60)}m break
          trails
        </span>
      </div>
      <div className="map-credit">
        AIS: NOAA OCM / AISStream · Zones: PortWatch approximations
        {renderer === "tiles" && (
          <>
            {" "}
            ·{" "}
            <a href="https://www.openstreetmap.org/copyright">
              © OpenStreetMap contributors
            </a>
          </>
        )}
      </div>
    </div>
    </div>
  );
}

function VectorMap(props: Props & { viewport: { width: number; height: number } }) {
  const { width, height } = props.viewport;
  const [west, south, east, north] = props.dataset.bounds;
  const merc = (lat: number) =>
    Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360));
  const w = (west * Math.PI) / 180,
    e = (east * Math.PI) / 180;
  const s = merc(south),
    n = merc(north);
  const top = 64, bottom = 24, side = 24;
  const scale = Math.min(
    Math.max(1, width - side * 2) / (e - w),
    Math.max(1, height - top - bottom) / (n - s),
  );
  const project = (lon: number, lat: number) => [
    width / 2 + ((lon * Math.PI) / 180 - (w + e) / 2) * scale,
    top + (height - top - bottom) / 2 - (merc(lat) - (s + n) / 2) * scale,
  ];
  const path = (zone: Zone) => {
    const polys =
      zone.geometry.type === "Polygon"
        ? [zone.geometry.coordinates as number[][][]]
        : (zone.geometry.coordinates as number[][][][]);
    return polys
      .flatMap((poly) =>
        poly.map(
          (ring) =>
            ring
              .map(
                ([lon, lat], index) =>
                  `${index ? "L" : "M"}${project(lon, lat).join(",")}`,
              )
              .join(" ") + "Z",
        ),
      )
      .join(" ");
  };
  const vessels = (props.frame?.vessels || []).filter(
    (v) => hasPosition(v) && (props.stale || !v.stale),
  );
  return (
    <svg
      className="vector-map"
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label="Local geographic map of actual observations and approximate zones"
    >
      <defs>
        <pattern
          id="map-grid"
          width="60"
          height="55"
          patternUnits="userSpaceOnUse"
        >
          <path
            d="M60 0H0V55"
            fill="none"
            stroke="currentColor"
            strokeWidth="0.6"
          />
        </pattern>
      </defs>
      <rect
        width={width}
        height={height}
        fill="url(#map-grid)"
        className="map-grid"
      />
      {props.overlays &&
        props.dataset.zones.features.map((zone) => (
          <path
            key={zone.properties.zone_id}
            d={path(zone)}
            className={`zone-path ${zone.properties.zone_type}`}
            fillRule="evenodd"
          >
            <title>{zone.properties.name} · approximate boundary</title>
          </path>
        ))}
      {props.trails &&
        (props.frame?.trails || [])
          .filter((t) => props.stale || !t.stale)
          .map((trail, index) => (
            <polyline
              key={`${trail.mmsi}-${index}`}
              points={trail.points
                .map(([lon, lat]) => project(lon, lat).join(","))
                .join(" ")}
              className={`trail-path ${props.selected === trail.mmsi ? "selected-trail" : ""}`}
            />
          ))}
      {vessels.map((v: Vessel) => {
        const [x, y] = project(v.longitude!, v.latitude!);
        return (
          <circle
            key={v.mmsi}
            cx={x}
            cy={y}
            r={props.selected === v.mmsi ? 6 : 3.8}
            className={`vessel-point ${v.stale ? "stale-point" : ""} ${props.selected === v.mmsi ? "selected-point" : ""}`}
            onClick={() => props.onSelect(v.mmsi)}
          >
            <title>
              MMSI {v.mmsi} · {v.stale ? "stale" : "fresh"}
            </title>
          </circle>
        );
      })}
      <text x="24" y={height - 10} className="coordinate-label">
        Local Mercator projection · observed paths only
      </text>
    </svg>
  );
}
