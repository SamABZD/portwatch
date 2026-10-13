# Cost controls and release gate

Phase 1 completed locally on October 13, 2026 (Asia/Beirut). The completion work must add
no recurring running cost. Existing Railway operation is metered and is not free hosting.
Detailed billing evidence and the service cost table are held outside the Git checkout;
account identifiers, billing responses and saved source/API fixtures are not published here.

## Verified baseline and limits

- Railway currently has exactly two PortWatch services, one active deployment and one
  running instance per service. The backend has a 1 vCPU / 2 GB ceiling; PostGIS has a
  0.5 vCPU / 1 GB ceiling. Each persistent volume remains 5000 MB. No resource was added,
  resized, replicated, restarted or deployed during this phase.
- Both ports' saved health responses reported one connected collector per port, recent
  heartbeats and available backups. These are application reports, not a complete host
  process census: Railway SSH access was unavailable without registering another key.
  Local Python collectors were not running and were not started.
- The actual Railway workspace compute usage limit was absent. The separate Hobby agent
  limit does not cap service compute. CPU/RAM ceilings and storage reserves are resource
  controls, not monetary caps. Other projects share this workspace.
- Vercel billing, plan, usage allowance and spend controls could not be inspected: there
  was no usable local CLI authentication and browser access failed. Do not infer Hobby,
  a zero bill, a configured limit or verified cost neutrality from the public deployment.
- A historical five-minute compute estimate is not a spending target. The new baseline
  uses existing provider time series for a comparable hour; it is an extrapolation of
  observed compute, not a full-month bill or the provider's billing-cycle forecast.

[Railway's usage controls](https://docs.railway.com/pricing/cost-control) apply across a
workspace. A hard compute limit can take all its workloads offline; an email alert does
not stop them. The [Hobby subscription](https://docs.railway.com/pricing/plans) includes
usage credit, so do not add the full usage estimate to the subscription fee again.
[Vercel Spend Management](https://vercel.com/docs/spend-management) also has plan and team
scope, with delayed checks and an explicit production-pause action. No account-wide
limit was changed, and no numerical cap is proposed without an owner-selected budget.

## Request reductions

The dashboard's catalog, frame and health requests and the pipeline status poll now share
one visibility-aware request lifecycle. It performs no polling while hidden, aborts pending
work, and refreshes each active resource once when the tab returns. Successful catalogs
unsubscribe until the port, revision or explicit retry changes. Periodic requests never overlap;
an abort-ignoring loader must settle before a queued resume refresh. Effect cleanup rejects
late results after port, mode, cutoff, freshness, revision, retry or route changes.

Visible live frames, health and pipeline status keep their existing 30-second cadence.
Historical frames and catalogs have no periodic timer. Replay playback stops advancing
while hidden. Manual retries still work. The Next.js proxy combines the caller's abort
signal with its existing 20-second deadline; actual upstream cancellation depends on the
hosting platform forwarding the disconnect and the backend's handling of cancellation.

No shared response cache was added. Port/mode/cutoff/freshness/trail/revision identities remain
in the existing backend cache contract. The standalone lineage document keeps its static-view
query and `no-store` behavior. News/weather acquisition remains backend scheduled work;
page navigation and public refresh controls only read saved data.

Before this change, code scheduled up to four dashboard API reads per minute or two pipeline
reads per minute in a hidden tab, subject to browser throttling. The local regression observed
zero requests during each 90-second hidden interval. Dollar savings depend on traffic and
cannot be inferred from these request counts. No hosted before/after reduction is claimed.

## Existing controls and remaining growth risks

Preserve the singleton runtime volume lock, one collector per configured port, one replica
per service, zero backend deployment overlap, per-port mutation locks and per-deployment
concurrency of one. Prefect's runner limit remains three. Finite live processing stays at
one minute; weather and news remain hourly and staggered. Provider cooldowns, `Retry-After`,
bounded HTTP bodies and the 1 GiB disk reserve remain intact.

Existing hourly maintenance bounds diagnostic logs and terminal Prefect metadata. Daily
logical backups retain two copies per database on the existing backend volume, with checksums,
a 512 MiB file bound, ten-minute timeout and disk reserve. Original observations and business
audit rows are preserved. Second-volume backups still share the provider failure domain.

The audit found scaling risks that need measured, integrity-preserving work before any
dataset expansion:

- Minute cleaning directly verifies retained live bytes before reusing completed results.
  The in-process five-minute checksum cache does not persist across Prefect flow processes.
- Publication fingerprints scan all retained normalized live positions. Visit recomputation
  can span their complete history; a seven-day output window does not bound that compute.
- The API checksum LRU has 8192 entries. An isolated synthetic 8193-entry ordered rescan
  demonstrated cache thrashing. The saved cloud health snapshot reported 1897 complete live
  batches across both ports; this is not an inventory of every retained raw file.
- Frozen historical data is backed up daily despite unchanged business content. Reusing
  a verified archive needs a separate backup-health and restore design.
- Source retention has no automatic deletion policy. Provider disk time series and volume
  inventory reported different used-size values, so they do not establish a capacity deadline.
  Get an actual filesystem measurement before projecting remaining collection days.

Do not weaken explicit audits or writer checksums to hide these costs. Measure stage time,
retained bytes/rows and unchanged-versus-new work before introducing persisted verification
receipts or incremental affected-vessel recomputation. No SQL, schema, schedules or backend
processing behavior changed in this phase.

## Lower-cost operation, stop and rollback

The existing checksummed regional historical exports were verified locally for both ports
with database access pointed at closed localhost port 9 and the AIS key unset. Catalog,
historical frame and health returned 200; health reported `database=not_used`; live reads
returned 422 with `prepared_mode_has_no_live_connection`. This confirms a useful genuine
replay path. The Python prepared server still consumes compute when hosted; it is not a
free static site. A reviewed static read adapter would be separate work if fully static
hosting becomes necessary.

For an optional live pause at a later authorized rollout, set only the PortWatch backend's
`PORTWATCH_CLOUD_LIVE=0`; retain bootstrap and both databases/volumes. The existing shutdown
path sends the owned runtime a stop request, waits for finalization and then exits it. Verify
readiness and both historical catalogs before relying on this mode. Display source status as
paused/stale, never live. This can remove collectors, Prefect and scheduled acquisition, but
the API, database and retained storage remain billable. It has not been applied or priced as
a measured reduction.

If disk pressure approaches the reserve, stop collection before capacity exhaustion. Keep
verified exports and off-cloud backup copies. Stop only PortWatch's owned deployment if a
complete compute stop is needed; that also takes its current API/demo offline. Retained
volumes and earlier usage can continue to bill. Do not delete source bytes or volumes.

The frontend rollback source is the pre-phase commit `7d34eed`. The backend was not changed;
its existing deployment remains the rollback reference recorded in the private audit.
At release, save the tested frontend revision and deployment URL before replacing production.
Reverting the polling patch restores the former requests, so rollback may increase usage.

## Verification and rollout gate

Commands run from the repository root unless stated otherwise:

```powershell
# From web/.
node --test scripts/visible-polling.test.mjs
npm run typecheck
npm run build

# From the repository root; pass privately saved baseline responses.
.\.venv\Scripts\python.exe -m scripts.verify_polling_browser --help
.\.venv\Scripts\python.exe -m scripts.verify_polling_browser --fixtures <private-baseline-directory>
.\.venv\Scripts\ruff.exe check scripts/verify_polling_browser.py
.\.venv\Scripts\ruff.exe format --check scripts/verify_polling_browser.py
git diff --check
```

Eleven deterministic request-lifecycle tests and seven browser scenarios passed. The browser
checks cover both ports, both modes, both routes, hidden playback, manual refresh, cancellation,
slow requests and failure recovery. Saved genuine responses and synthetic response/timing
controls stayed isolated from analytical storage; external tiles and providers were blocked.
Desktop/mobile captures and the JSON result remain in ignored `artifacts/phase1/`.
The prepared-mode probes also passed. This is targeted Phase 1 verification; complete release
validation, clean-clone reproduction and hosted deployment belong to later phases.

Production recurring delta from Phase 1 is zero because nothing was deployed or reconfigured.
The tested sustained-hidden sessions reduce requests; frequent short hide/resume cycles can
add refreshes. Overall query and monetary deltas at real traffic, and one-time rollout usage,
remain unresolved. Bounded billing/API reads may themselves
incur a small amount of existing metered usage; they are not advertised as free. Before rollout,
inspect Vercel's actual plan/usage/controls, compare the same measurement window and traffic basis,
and account for rollout runtime/storage/egress. A comparable positive delta blocks rollout.
No paid integration, account change, extra port, historical backfill, dbt schedule or CI enablement
was added. Phase 2 has not started.
