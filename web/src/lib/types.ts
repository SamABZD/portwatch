export type Mode = "historical" | "live";
export type Zone = {
  type: "Feature";
  properties: {
    zone_id: string;
    name: string;
    zone_type: string;
    uncertainty: string;
  };
  geometry: {
    type: "Polygon" | "MultiPolygon";
    coordinates: number[][][] | number[][][][];
  };
};
export type Dataset = {
  mode: Mode;
  port_id: string;
  port_timezone?: string;
  available: boolean;
  reason?: string;
  port_name: string;
  source_id: string;
  dataset_id: string;
  start_utc: string;
  end_utc: string;
  revision: string;
  analytical_version: string;
  geofence_version: string;
  clean_version: string;
  bounds: number[];
  observation_count: number;
  latest_observation_utc: string | null;
  zones: { type: "FeatureCollection"; features: Zone[] };
};
export type Vessel = {
  reported_profile?: {name?: string; imo?: string; call_sign?: string; length_m?: number; beam_m?: number; draught_m?: number; destination?: string; reported_eta?: string; reported_type?: number};
  heading_degrees?: number | null;
  navigation_status?: number | null;
  profile_available_at_utc?: string | null;
  mmsi: string;
  longitude: number | null;
  latitude: number | null;
  speed_knots: number | null;
  course_degrees: number | null;
  vessel_type: number | null;
  last_observation_utc: string;
  observation_age_seconds: number;
  stale: boolean;
  zone: string;
  waiting: string;
  visit_status: string;
  partial_reasons: string[];
  waiting_history: { qualified_episodes: number; associated_episodes: number };
  source_id: string;
  timestamp_quality: string;
  ambiguous_position: boolean;
  quality_flags: string[];
};
export type Frame = {
  cutoff_utc: string;
  dataset: Dataset;
  prefix_observations: number;
  cache_hit: boolean;
  staleness_minutes: number;
  trail_minutes: number;
  max_trail_gap_seconds: number;
  vessels: Vessel[];
  trails: {
    mmsi: string;
    points: [number, number, string][];
    stale: boolean;
  }[];
  metrics: {
    seen_vessels: number;
    fresh_vessels: number | null;
    fresh_port_occupancy: number | null;
    candidate_waiting: number | null;
    completed_dwell_sample_count: number;
    completed_dwell_median_seconds: number | null;
    partial_visit_count: number;
    zone_counts: Record<string, number> | null;
  };
  chart: {
    hour_utc: string;
    hour_end_utc: string;
    observed_vessels: number | null;
    observations: number | null;
  }[];
  weather: {
    valid_time_utc: string;
    wind_speed_mps: number | null;
    gusts_mps: number | null;
    preceding_hour_precipitation_mm: number | null;
  } | null;
  news: {
    title: string;
    url: string;
    domain: string;
    publication_time_utc: string | null;
    first_seen_time_utc: string | null;
  }[];
  news_status: string;
};
export type Health = {
  overall: string;
  checked_at_utc: string;
  database: { status: string };
  collector: {
    status: string;
    last_observed_ais_message_utc?: string;
    observation_state?: string;
  };
  historical: {
    status: string;
    last_clean_commit_utc: string | null;
    last_mart_publication_utc?: string | null;
  };
  live: {
    status: string;
    last_clean_commit_utc?: string | null;
    last_mart_publication_utc?: string | null;
    observation_age_seconds?: number | null;
  };
  context: Record<
    string,
    { status: string; last_http_status: number | null; source_id?: string; using_fallback?: boolean; fallback_label?: string }
  > | null;
  issues: string[];
};
