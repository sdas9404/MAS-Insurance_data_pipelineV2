# Codex Stage 2 — 12-run unlabeled SUMO research pipeline and PhD handoff

## Authority and objective
This replaces the earlier single-run Stage 2 brief. Inspect the existing repository before changing files. Treat the requirements in this document and the latest user-approved implementation prompt as current; identify conflicting legacy configuration and document changes. Do not overwrite original network, polygon, route templates, earlier LC2013 baseline, existing raw outputs, or previous manifests.

Produce a reproducible multi-agent driving dataset for later research on driver behaviour and possible insurance risk models. **No persona labels, prior risk classes, supervised target labels, premiums, or insurance scores**. Simulated driver parameters are *inputs*, actual driving and safety events are *observations*. Do not derive or assign outcome labels simply from the parameters used to generate behaviour.

## 1. Core experimental design
- **12 main runs**, `run_001` through `run_012`.
- **90 study drivers per run**, with persistent IDs `D000`–`D089` and the *same underlying sampled driver parameters* across runs unless a change is explicitly versioned and explained.
- **10 scheduled trips per study driver per run** = **900 planned study trips/run**, **10,800 planned study trips total**, and **120 planned trips per study driver** across 12 runs.
- Distinguish `run_id`, `driver_id`, `trip_id`, `sim_vehicle_id`, and `is_background`. The globally unique trip key is `(run_id, trip_id)` or a fully qualified global ID; never collapse repeated trips into a single vehicle record.
- Prefer the same ten planned OD route templates and study-driver departure schedule across comparable runs; check vehicle is not assigned concurrent trips. Keep an explicit record of whether each trip was inserted, arrived, teleported, collision-removed, or never inserted. Don't equate planned trips with successful trips.
- Use **three controlled traffic-density conditions**, e.g. low / moderate / high, with **four independently seeded runs per condition**. These are *experimental exposure conditions*, not driver personas. Record scenario, traffic load, seed and run-level density/concurrency statistics. If the full implementation uses different density groups, explain and document actual settings.
- Generate background traffic using separate route files/IDs; never count background vehicles among the 90 study drivers or their 900 trips. Preserve identifiers for study/background interactions.
- Retain the baseline 90-trip LC2013 diagnostic separately: 90 arrivals, 82 lane changes, 108 SSM records, zero collision-output records. Do not assume 108 distinct near misses or that SSM type 111 is a collision confirmed by the separate collision detector.

## 2. Unlabeled, reproducible input sampling
- Update `config/sampling.yaml` **and its generator**, not just generated route XML.
- Explicitly define the distribution type, center/spread or shape, bounds, mixing weights, seed, and dependencies for **every sampled parameter**, including **`tau`**. No hidden hard-coded `tau` distributions.
- Distinguish longitudinal behaviour and following preference (`speedFactor`, `tau`, `minGap`, `sigma`, `startupDelay`, `actionStepLength`) from vehicle capability (`accel`, `decel`, `emergencyDecel`, `apparentDecel`, `maxSpeed`, `length`, `width`, other supported vehicle properties), lane change (`lcStrategic`, `lcCooperative`, `lcSpeedGain`, `lcKeepRight`, `lcAssertive`, `lcSigma`), lateral/sublane (`minGapLat`, `lcPushy`, `lcPushyGap`, `lcImpatience`, `lcAccelLat`, others confirmed for SL2015), and junction behaviour (`impatience`, `jmDriveAfterRedTime`, `jmDriveRedSpeed`, `jmIgnoreFoeProb`, `jmIgnoreFoeSpeed`).
- Verify support, semantics and ranges in the **installed SUMO 1.28.0**, rather than silently passing unsupported or inert options. If necessary, keep incompatible features in separately labelled scenario experiments.
- Prior range examples from the existing sampler (e.g. speedFactor 0.77–1.33; accel 1.2–3.5; decel 3.2–5.8; sigma 0.05–0.9; minGap 0.9–4.0) are **exploratory assumptions, not calibrated population statistics**. Add configurable `tau` and other parameters; document rationale, thresholds, and limitations. Do not sample all parameters independently/uniformly if that generates implausible combinations; implement configurable bounded/mixed distributions and constraints.
- Enforce parameter compatibility, such as `emergencyDecel >= decel`, appropriate perceived braking parameters, action intervals that are integer multiples of the simulator step, and physically reasonable vehicle dimensions. Mark intentionally extreme or `tau < actionStepLength` configurations as *experimental conditions/metadata*, not risk-class labels. Never silently correct generated driver attributes without recording changes.
- Keep individually sampled static parameters stable over the driver's trips and runs; save exact assigned values, population seed, configuration hash, generator version and validity flags.
- The generator can sample small documented tails enabling short-headway following, unsafe gap acceptance, red-light entry and priority mistakes. These input probabilities are **not actual collision probabilities or real-world prevalence estimates**. Do not guarantee a particular driver crashes or commit a specific infraction.
- Separate synthetic age/years licensed and other demographics if requested, with independent transparent sampling and explicit `synthetic_unvalidated` provenance. Don't imply a real-world relationship to risk absent evidence.

## 3. SUMO runtime and collision/interaction diversity
- Standard main-run simulation step: **0.1 seconds**. Telemetry can be sampled at **0.2 seconds** (configurable), but run safety-event logic at the simulation-step cadence where needed; label sampling/derivative intervals accurately. Use appropriate ballistic integration and action-step compatibility checks.
- Retain LC2013 baseline. For main runs, implement and smoke-test **SL2015** with a positive `--lateral-resolution` using newly generated, separate configs and vTypes; do not mix SL2015 with explicit `laneChangeModel=LC2013`.
- Use SUMO collision detector with junction checking, SSM device, lane-change output, TripInfo, and raw simulator logs. Record collision action and consequences (continued/teleported/removed).
- Add background traffic so the 90 study drivers have realistic opportunities to interact. Measure traffic occupancy and exposure; do not simply multiply 900 focal trips or concentrate all cars on one road to force incidents. Consider distinct moderate and high demand while preserving the focal cohort.
- Capture both ordinary/safe and safety-critical driving: observed speeding, tight gaps, harsh acceleration/braking, lane changes, lateral encroachments, red-signal entries, priority conflicts, near misses and possible collisions. Actual presence and rate are empirical outputs; **never fabricate, inject, or require a nonzero crash count in all 12 main runs**. If a detector needs validation, create *clearly separated* incident-injection tests excluded from the observational handoff/modeling set.
- Note that unsafe lane-change parameterization is not automatically a valid human-blind-spot model. Validate semantics and document gaps between simulator behaviour and real driving.
- Validate lane speed-limit metadata before calling a speed exceedance a legal violation. Keep “exceeds simulator lane limit” distinct from verified legal speeding.

## 4. Observed telemetry and events
Collect as broadly as technically useful and supported, avoiding unsupported or misleading metrics. Use TraCI subscriptions and suitable native SUMO outputs. Raw and derived data must be distinguishable, units explicit, nulls meaningful, and source/provenance recorded.

### Driver/vehicle static manifest
Exact `speedFactor`, **`tau`**, `accel`, `decel`, `emergencyDecel`, `apparentDecel`, `sigma`, `minGap`, `actionStepLength`, all supported lane-change/junction/sublane settings, `length`, `width`, `maxSpeed`, physical parameters, vType, seeds, and configuration hashes. These are static *inputs*, not observed actual braking/actual headway.

### Per-timestep telemetry
`run_id`, `driver_id`, `trip_id`, SUMO vehicle ID, `is_background`, `sim_time_s`, x/y, road edge, lane and index, lane position, lateral position where available, heading, speed, actual acceleration, previous speed, derived jerk, distance, travel time, waiting time and time loss, road class, lane maximum speed, allowed speed, moving/stopped status, leader/follower IDs, physical gap, relative speed, *observed* time headway, TTC when defined, nearby signal/link state and distance, route progress, local context, and additional reliable SUMO observable fields. Keep unit conventions and downsampling documented; mark unavailable/not-applicable data null rather than zero.

### Safety/events
- Lane changes: source native output, reason, target/surrounding gaps, directions and any lateral movement telemetry.
- Collisions: preserve SUMO collision XML and attributes; join both participants to study/background identity, run and trip; include collision response action.
- SSM: preserve raw ego/foe records with `minTTC`, `maxDRAC`, `PET`, `PPET`, `MDRAC`, conflict code, timestamp and location; provide deduplicated encounter-level view separately. Reciprocal ego/foe records are not distinct physical encounters. SSM code 111 must **not** automatically become `confirmed_collision=true`; preserve detector disagreement.
- Red signals: detect crossing of the applicable controlled stop line at red, verify controlling link, movement and turn rules. Store `red_signal_entry_observed` separately from `verified_legal_violation`; do not infer violation solely from `getNextTLS` or a red signal while approaching.
- Derive observed speeding episodes, acceleration/deceleration events, harsh braking, near-miss measures and tailgating using declared thresholds, time/distance exposure, formulas and provenance. Record event onset/offset, relevant vehicle IDs, and exact thresholds. Preserve original raw observations.

## 5. Required Parquet handoff tables
1. `driver_parameters.parquet`: **90 rows** for fixed cohort with unique `driver_id` and complete static sampled parameters, generation metadata.
2. `driver_run_metadata.parquet`: **1,080 rows** (`run_id`, `driver_id`) and per-run realized config/exposure/outcomes (no labels).
3. `trip_manifest.parquet`: **10,800 planned study-trip rows** (background trips have their own manifest), with scheduled and observed departure/arrival, final status and outcome reason.
4. `trip_summary.parquet`: study trip-level metrics and final status for **all 10,800 planned trips**, including incompletes (nullable unobserved outcomes); background trip summaries separately tagged.
5. `vehicle_telemetry/`: partitioned Parquet by run, streaming writes; include background observations as necessary for interactions, documented inclusion policy.
6. `driving_events.parquet`, `lane_changes.parquet`, `collisions.parquet`, `traffic_light_entries.parquet`: accurate native/derived events with source/uncertainty.
7. `ssm_conflicts_raw.parquet` and `ssm_encounters_deduplicated.parquet`: different purposes, both required.
8. `simulation_runs.parquet`, `run_statistics.parquet`, `per_driver_features.parquet`: seeds, conditions, actual counts, travel/traffic metrics, and measured driver exposure-normalized outcomes.
9. `quality_report.json`, `schema_data_dictionary.md`/CSV, `handoff_manifest.csv` with row counts, hashes and definitions.

Prefer typed, compressed PyArrow/Parquet and bounded-memory streaming (ParquetWriter, row groups). Maintain stable schemas across runs, null/NaN conventions and time/units. Store native raw files separately per run to support reprocessing.

## 6. Research summary and integrity checks
- Compute per-run and overall planned/inserted/completed/teleported/removed/incomplete counts; 90 drivers and 900 *planned* study trips per run; 12 runs overall.
- Report per-driver exposure-normalized speed, observed headway, acceleration/deceleration, harsh brakes, lane changes, red-signal entries, TTC/DRAC/PET, collision involvement and near-miss rates. Include spread/percentiles, denominators and missingness. Distinguish number of unique drivers from number of trip observations.
- Check simulator parameters versus manifest, no overlapping active trips for a driver, route validity, no duplicate `(run_id, trip_id, sim_time_s)` records, accurate identifiers, plausible speeds and positions, correct collision/SSM counting and trip outcome integrity.
- Summarize significant warnings (such as `tau < actionStepLength`), detector disagreement, and network speed-limit uncertainty. Do not silently treat zero recorded collisions as a pipeline failure.
- Keep eventual ML evaluation split by `driver_id`, especially since driver parameters are reused across 12 runs. The simulation does not validate real insurance claims frequency or causal age effects.

## 7. Execution plan
1. Audit existing repository and scripts, establish baseline without overwriting.
2. Update YAML sampling + generator, add `tau`, test distributions and unit constraints.
3. Implement controlled 12-run scenario generator, route/manifests, background traffic, SL2015 tests.
4. Run a **small smoke test (5 drivers × 2 trips)**, then one full **90 × 10** experiment. Check actual telemetry and safety output before scaling.
5. Run remaining experiments with reproducible seed allocation and checkpoints/resume. Never rerun or overwrite previously verified runs without explicit archival/versioning.
6. Build/execute automated validation, aggregate observed statistics and produce research report.
7. Package and validate research handoff ZIP.

If a full run cannot be completed, leave honest checkpoint/status logs and describe exactly what is incomplete.

## 8. Final PhD collaborator handoff ZIP
Create **`deliverables/SE4457_PhD_handoff.zip`** containing:
- Clear `README.md` and usage examples (Pandas/PyArrow), methodology and research report, limitations, data dictionary and event definitions.
- **All** relevant Parquet datasets, aggregated CSV/Parquet statistics and raw SUMO collision/SSM/lane-change/TripInfo files, not only sample extracts.
- Original network/poly/base route templates, generated 12-run route/config/seed inputs, `sampling.yaml` and source scripts/test suite.
- Exact SUMO 1.28.0 confirmation, Python/dependency versions or lockfile, file hashes, reproducibility steps and quality reports.
- File manifest (path, description, row count for table data, bytes, SHA-256), ZIP integrity check, and post-extraction Parquet-read test.
- Clear distinction between 10,800 planned trips and actual successful arrivals, and between simulator crash logs, SSM flags and inferred events.

Do not omit large telemetry tables merely to make the ZIP small. If computational, disk or execution limits prevent completion, report the limitation; do not describe an incomplete ZIP as a complete handoff. End with actual run counts, driver/trip validations, observed event counts and final absolute ZIP path.
