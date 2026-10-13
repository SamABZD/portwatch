# Approximate analytical zones

`geo/los_angeles.geojson` defines six manually authored polygons under immutable version
`la-approx-v1`. These are analytical vicinity approximations, not official port boundaries,
navigation charts, individual berth outlines, or evidence of a port call. The harbor and terminal
polygons include land and adjacent water; terminal coverage is partial.

## Evidence and review

Authoring/review date: 2026-10-12. The PortWatch implementation reviewed the official
[Port of Los Angeles terminal schematic (2023-0035)](https://kentico.portoflosangeles.org/getmedia/07e1377d-b452-4ecb-a629-9a0c69410805/pola_terminals_map)
visually, through its [terminal map entry point](https://portoflosangeles.org/business/terminals/terminal-map).
The schematic identifies the relative locations of WBCT, Yusen/Everport, Fenix, and Pier 400.
Coordinates were authored as coarse vicinity approximations rather than extracted as surveyed
boundaries. Saved 2024-01-01 NOAA AIS points were overlaid for plausibility. No other day's
operational layout or official anchorage boundary is claimed.

The collection filter remains `[-118.34,33.66,-118.225,33.80]`, in longitude/latitude order.
It decides which records were collected. The smaller analytical polygons give those observations
approximate memberships. A point can be inside the collection filter and outside every zone.

The conservative eastern limit keeps polygons west of the collection edge. The reviewed Long
Beach Pier F reference `[-118.209,33.754]` is outside all zones and approximately 1,575 meters
from the nearest port-scoped polygon, measured with PostGIS geography. This is a checked reference
point, not proof of the complete jurisdictional boundary. The offshore candidate polygon is
**region scoped**. It must not become Los Angeles waiting time without subsequent visit evidence.

Metadata preserves references, uncertainty, authoring/review dates, and unverified operational
validity. `effective_from` and `effective_to` remain null. Applying the polygons to the frozen
2024 replay is an explicit analytical approximation. Unknown effective dates do not mean that
the polygons are confirmed for every date.

## Commands

Run from the PortWatch project root:

```powershell
.\.venv\Scripts\python.exe -m portwatch db migrate
.\.venv\Scripts\python.exe -m portwatch geo load --port los_angeles
.\.venv\Scripts\python.exe -m portwatch transform zones --port los_angeles --demo
.\.venv\Scripts\python.exe scripts/verify_zones.py
```

`geo load --file <path>` validates an alternate reviewed file. A content change requires a new
version. Loading an identical canonical document reuses the existing version. Coordinate rings
must be explicitly closed; finite 2D longitude/latitude coordinates must fit the reviewed
collection envelope. Empty, self-intersecting, swapped, duplicate-ID and unexpected reference
memberships are rejected. No automatic repair or `ST_MakeValid` is used.

`transform zones --demo --version <loaded-version>` can reproduce an older stored version after
the configured file changes. With no version argument, the configured file's checksum must match
the loaded version. The command selects the frozen source, dataset, cleaning version, and UTC
interval; live data remains separate. No provider request or new acquisition is needed.

## Schema and assignment rules

Migration 003 adds `geofence_versions`, `port_zones`, `position_zone_assignments` and
`position_zone_memberships`. Earlier migrations are unchanged. Stored polygons are valid,
nonempty MultiPolygons with SRID 4326. Geofence catalog/zone updates and deletes are refused by
database triggers. A new version coexists with prior versions and their assignments.

Each assignment is keyed by position ID, port, geofence version and algorithm `covers-v1`.
Position IDs already identify an observation and cleaning version. Memberships reference both
the assignment and the exact zone version. Every selected position receives either an inside
summary plus all matching memberships, or an explicit outside summary with no primary zone.
Assignments are additive; reruns insert only previously unassigned positions. Original positions
and source lineage are untouched.

The [ST_Covers predicate](https://postgis.net/docs/ST_Covers.html) includes the polygon boundary.
Primary display priority is terminal (10), harbor (20), then regional waiting candidate (30).
Equal-priority matches choose the lexically smallest zone ID. This primary choice does not remove
other memberships and does not assert berth occupancy, a visit, or waiting.

Both position and zone geometries have GiST indexes. A representative 62,157-position join used
`vessel_positions_spatial_idx` and returned 72,237 memberships in about 84 ms on the initial
verified run. That measurement is for the join, not total load time or a general performance
guarantee. The six-zone table appropriately used a sequential scan.

The first bulk membership insertion timed out because the new assignment table had zero-row
statistics and foreign-key probes chose a broad port/version index. Transaction rollback left
no partial assignments. Refreshing table/temp statistics before dependent bulk inserts made the
full CLI load finish in approximately 10 seconds, including two CLI startups. A separately
started verifier using the old code was canceled at its own membership statement and rolled back.

For areas and distances, cast to PostGIS geography: `ST_Area(geometry::geography)` returns square
meters and [ST_Distance](https://postgis.net/docs/ST_Distance.html) returns meters. Geometry in
EPSG:4326 has angular units and must not be treated as meters.

## Measured coverage

For 62,157 real clean historical positions:

| Result | Count |
| --- | ---: |
| Inside one or more zones | 61,712 |
| Outside all zones | 445 |
| Total memberships | 72,237 |
| Positions with multiple memberships | 10,525 |
| Terminal primary assignment | 10,304 |
| Harbor primary assignment | 50,668 |
| Regional candidate primary assignment | 740 |

The West Basin vicinity has zero observed memberships on this saved day. This is a valid observed
zero, not a failed source or proof that the real terminal was empty. All numbers describe receiver
observations in a bounded dataset, not vessel or terminal occupancy counts.

The verifier checks **every** membership independently with Shapely, compares repeated assignment
hashes, and checks the original clean business hash before/after. Both replays have zero membership
mismatches and zero new assignments. Original clean hash remains
`355cc069362fd84e7aa27530647bae9f8dbbcbdb0156e9585dfea9b91a8122d4`.
The assignment hash is `be40f26d8b7786456f876e7b969c00201527888b1fc3417ddab34f5fab7740a4`.

`artifacts/step06/verification.json` includes counts, scope, hashes, geography area/distance checks
and the actual query plan. `artifacts/step06/los-angeles-zones.png` shows all polygons over 4,782
sampled real observations and reviewed reference points. The plot was visually inspected.
