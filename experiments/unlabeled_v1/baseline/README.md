# SE4457 — Unlabeled SUMO Driver Data Pipeline (Foundation)

**Purpose:** 90 stable driver identities × 10 independent trip episodes = 900 SUMO journeys per run. No persona names, risk labels, premiums, or classification rules are generated.

This is a **Stage 1 foundation**, not a finished telemetry collector. `driver_parameters.parquet` contains the exact input parameters; observational telemetry and collision/event Parquets are implemented in Stage 2 by Codex after smoke-test validation.

## Place the original files

- `network/london_filtered.net.xml`
- `network/london.poly.xml`
- `routes/london.rou.xml` (original 300-vehicle, *routed* XML)

## Setup on Windows

```powershell
cd C:\Users\suman\MAS-Insurance_data_pipeline
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python .\scripts\validate_inputs.py
python .\scripts\generate_unlabeled.py
sumo -c .\run_001.sumocfg --duration-log.statistics true --verbose
```

## Expected Stage 1 outputs

- `routes/run_001_900.rou.xml`: 90 driver vTypes; 900 departures
- `run_001.sumocfg`: new configuration; original `london.sumocfg` untouched
- `data/manifest/run_001_driver_parameters.parquet`: 90 rows
- `data/manifest/run_001_trip_manifest.parquet`: 900 rows
- `data/raw/run_001/tripinfo.xml`: observed trip outcomes
- `data/raw/run_001/collisions.xml`: reported physical collision events
- `data/raw/run_001/lanechanges.xml`: reported lane-change events

## Research-design notes

1. **Ten trips ≠ one continuous itinerary.** This foundation uses 10 independent, spatially unlinked trip episodes per driver. Their routes share a stable driver's parameters, but trips may start at a different position from the preceding destination. The departure waves reduce overlap but do not mathematically guarantee it; validate actual departure and arrival intervals. If continuity matters, implement itinerary linkage in Stage 2.
2. Input sampling distributions *must* be specified to run any reproducible simulation. They are not driver labels or risk targets.
3. Demographic metadata such as age or years licensed can be generated independently later. They are synthetic and should not be treated as actual causal determinants of risky driving.
4. Some SUMO parameters only influence behaviour with specific models. `lcPushy` is not enabled in this LC2013 baseline because it requires a sublane model. Do not collect inert parameters as active influences.
5. The `speedDev=0` parameter avoids a second, per-trip speedFactor draw; the driver retains their sampled `speedFactor` across all ten journeys.
6. There is NO guarantee of collisions, red-light violations, or a particular class balance. Observe counts and use documented stress scenarios if event rarity makes the dataset unusable; never silently manufacture collisions or labels.
7. 900 records are **not 900 independent drivers**. Later model train/test splitting should group by driver ID, and preferably test on novel seeds and routes to avoid leakage.
8. `collision.action=warn` documents collisions but does not necessarily physically resolve them; verify semantics and inspect the resulting trajectory plausibility. Teleports and unfinished trips should not be dropped from analysis.
9. Keep risk-label or clustering rules out of the simulation config and create them only in a later analysis phase, based on your collaborator's protocol.

## Follow-up work

Read `docs/CODEX_STAGE2.md` for a concrete Codex build brief for the full TraCI/Parquet collector.

## Unit tests

```powershell
python -m pytest -q
```

**Status of this starter's validation:** The source files pass Python syntax compilation. The generator was exercised with a temporary 300-template fixture, checking that it produces 900 unique trip IDs, 90 stable vehicle types, 10 trips per driver, and departure-sorted route XML. A real SUMO run and actual PyArrow Parquet write must still be tested on your Windows machine with the three original inputs; this environment did not have the SUMO executable or `pyarrow` installed.
