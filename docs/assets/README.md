# Screenshot provenance

These unchanged PNGs were captured and visually inspected on October 14, 2026 from the
verified release HTTPS preview at source commit `84be4c6`. API responses use genuine saved
NOAA/ERA5 frames and saved operational evidence. No screenshot was generated, retouched or
populated with invented records. The [manifest](manifest.json) records source artifacts,
SHA-256, size, port, cutoff, viewport and exact source commit; ignored originals stay local.

| Image | Scope |
| --- | --- |
| [Los Angeles](historical-los-angeles.png) | Historical replay at January 1, 2024, 12:30 UTC; 99 fresh reporting vessels, 124 saved observations. |
| [Oakland](historical-oakland.png) | Separate regional dataset, same cutoff; 75 fresh reporting vessels, 126 saved observations. |
| [Mobile](historical-mobile.png) | Los Angeles historical replay on a 390-pixel viewport. |
| [Pipeline](pipeline.png) | Actual source/stage diagram, historical-only dbt branch and finite scheduler; selected Prefect detail. |

Basemap is off. Packaged map images use local geometry without third-party tiles. The verifier's
separate test-only raster, error projections and live fixtures are **not public screenshots**.
The pipeline image is an element screenshot; no edited crop or fabricated metrics were added.

Historical vessel reports: NOAA OCM / MarineCadastre.gov 2024 AIS, [CC0 data citation](https://github.com/ocm-marinecadastre/ais-vessel-traffic/blob/main/data/ais-broadcast-points-2024-readme.md).
Weather shown in the three map images: [Open-Meteo](https://open-meteo.com/) and Copernicus C3S / ERA5,
transformed UTC selections under [CC BY 4.0 and applicable API terms](https://open-meteo.com/en/licence).
Zone polygons are PortWatch approximations, not reproduced official maps.

No raw AISStream data, live vessel screenshot, account dashboard, credential, workstation path
or prepared export is packaged. Public API data is sampled separately in the
[readiness audit](../evidence/phase5-public-readiness.json). [Source audit](../SOURCE_AUDIT.md) ·
[Phase 4 verification and limits](../evidence/phase4-workspace.json).
