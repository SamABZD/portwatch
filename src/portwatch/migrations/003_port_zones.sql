-- Versioned analytical approximations; never official port or berth boundaries.
CREATE TABLE geofence_versions (
    port_id text NOT NULL,
    version text NOT NULL,
    content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    authored_on date NOT NULL,
    approximate boolean NOT NULL CHECK (approximate),
    uncertainty text NOT NULL CHECK (length(uncertainty) > 0),
    validity jsonb NOT NULL,
    reference jsonb NOT NULL,
    document jsonb NOT NULL,
    loaded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (port_id, version)
);

CREATE TABLE port_zones (
    port_id text NOT NULL,
    version text NOT NULL,
    zone_id text NOT NULL,
    name text NOT NULL,
    zone_type text NOT NULL CHECK (zone_type IN ('terminal','harbor','waiting_candidate')),
    attribution_scope text NOT NULL CHECK (attribution_scope IN ('port','region')),
    priority integer NOT NULL CHECK (priority IN (10,20,30)),
    geometry public.geometry(MultiPolygon,4326) NOT NULL,
    reference jsonb NOT NULL,
    validity jsonb NOT NULL,
    PRIMARY KEY (port_id, version, zone_id),
    FOREIGN KEY (port_id, version) REFERENCES geofence_versions(port_id, version),
    CHECK (public.ST_IsValid(geometry) AND NOT public.ST_IsEmpty(geometry)),
    CHECK ((zone_type = 'terminal' AND priority = 10 AND attribution_scope = 'port')
        OR (zone_type = 'harbor' AND priority = 20 AND attribution_scope = 'port')
        OR (zone_type = 'waiting_candidate' AND priority = 30 AND attribution_scope = 'region'))
);
CREATE INDEX port_zones_spatial_idx ON port_zones USING gist(geometry);

CREATE FUNCTION reject_geofence_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Geofences are immutable: load a new version instead';
END;
$$;
CREATE TRIGGER geofence_versions_immutable BEFORE UPDATE OR DELETE ON geofence_versions
    FOR EACH ROW EXECUTE FUNCTION reject_geofence_change();
CREATE TRIGGER port_zones_immutable BEFORE UPDATE OR DELETE ON port_zones
    FOR EACH ROW EXECUTE FUNCTION reject_geofence_change();

CREATE TABLE position_zone_assignments (
    position_id bigint NOT NULL REFERENCES vessel_positions(id),
    port_id text NOT NULL,
    geofence_version text NOT NULL,
    assignment_version text NOT NULL,
    state text NOT NULL CHECK (state IN ('inside','outside')),
    primary_zone_id text,
    membership_count integer NOT NULL CHECK (membership_count >= 0),
    PRIMARY KEY (position_id, port_id, geofence_version, assignment_version),
    FOREIGN KEY (port_id, geofence_version) REFERENCES geofence_versions(port_id, version),
    FOREIGN KEY (port_id, geofence_version, primary_zone_id)
        REFERENCES port_zones(port_id, version, zone_id),
    CHECK ((state = 'outside' AND primary_zone_id IS NULL AND membership_count = 0)
        OR (state = 'inside' AND primary_zone_id IS NOT NULL AND membership_count > 0))
);
CREATE INDEX position_zone_assignments_version_idx
    ON position_zone_assignments(port_id, geofence_version, assignment_version, position_id);
CREATE INDEX position_zone_assignments_primary_idx
    ON position_zone_assignments(port_id, geofence_version, primary_zone_id);

CREATE TABLE position_zone_memberships (
    position_id bigint NOT NULL,
    port_id text NOT NULL,
    geofence_version text NOT NULL,
    assignment_version text NOT NULL,
    zone_id text NOT NULL,
    PRIMARY KEY (position_id, port_id, geofence_version, assignment_version, zone_id),
    FOREIGN KEY (position_id, port_id, geofence_version, assignment_version)
        REFERENCES position_zone_assignments(position_id, port_id, geofence_version, assignment_version),
    FOREIGN KEY (port_id, geofence_version, zone_id) REFERENCES port_zones(port_id, version, zone_id)
);
CREATE INDEX position_zone_memberships_zone_idx
    ON position_zone_memberships(port_id, geofence_version, zone_id);
