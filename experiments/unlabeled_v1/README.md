# SE4457 synthetic observational traffic experiment



```json
{
  "successful_main_runs": 12,
  "expected_main_runs": 12,
  "planned_study_trips": 10800,
  "observed_study_completed": 9646,
  "total_telemetry_rows": 30860769,
  "driving_event_records": 1121900,
  "lane_change_records": 21879,
  "collision_records": 389,
  "ssm_raw_records": 158183,
  "ssm_deduplicated_encounters": 79328,
  "ssm_type111_records": 49245,
  "signal_entries": 99195,
  "red_signal_entries": 152,
  "type111_without_collision_log": 24973,
  "complete": true,
  "observed_study_inserted": 9982,
  "study_teleports": 42,
  "background_teleports": 485,
  "observed_background_completed": 17166,
  "study_red_signal_entries": 66,
  "study_collision_involvements": 112,
  "geometry_warning_count": 2444,
  "study_non_inserted": 818,
  "study_inserted_not_completed": 336,
  "study_planned_outcomes_accounted": 10800,
  "study_teleported_trips": 40,
  "study_removed_trips": 0
}
```



This dataset describes synthetic simulator observations, not real drivers, claims, actuarial outcomes, or validated human accident frequencies. No personas, scores, insurance premiums, or classification targets were generated.



## Design and reproducibility



90 fixed study identities, ten independent route episodes per run, twelve runs: low/moderate/high background demand with four seeds each. Each driver has the same ten explicit routes and scheduled departure times in all runs. Spatial continuity between successive trips is not assumed. The runner releases each trip after its schedule and after the previous trip has ended; resulting delays are observations.

The SUMO route input contains study vTypes and separate background routes. Study routes and departures are in trip_manifest.parquet and study_planned.rou.xml. Running SUMO alone does not release study vehicles: use the Python runner. It uses TraCI route.add and vehicle.add with standard insertion checks, never speedMode/laneChangeMode overrides.

Simulation uses SUMO 1.28.0, Krauss, SL2015, ballistic integration, 0.1-second steps, 0.5 m sublanes. Telemetry is sampled at 0.5 seconds; events and stop-line crossing logic run every 0.1 seconds. Background demand is 450/1350/2700 planned journeys per run; study demand is always 900. Background receives 12 fixed, separately sampled vTypes. All background vehicles carry SSM devices and have telemetry.



From the repository root on Windows:



```powershell

python -m pip install -r requirements-lock.txt

python -m pytest -q tests

python scripts/research_pipeline.py prepare

python scripts/research_pipeline.py smoke

python scripts/research_pipeline.py run

python scripts/research_pipeline.py summarize

python scripts/research_pipeline.py package

```

Set SUMO_HOME to the installed SUMO 1.28.0 directory. Completed runs are skipped. Interrupted run attempts are archived and restarted from simulation time zero, preserving previous files. This is a run-level resume, not a simulator-state checkpoint. To reproduce from scratch, use a fresh copy and omit experiments/unlabeled_v1 except baseline/.



## Measured run results



| run_id | density | seed | inserted | completed | teleports | telemetry_rows | lane_changes | collision_records | ssm_raw_records | red_signal_entries | max_concurrent |
|---|---|---|---|---|---|---|---|---|---|---|---|
| run_001 | low | 1103 | 900 | 900 | 0 | 667752 | 993 | 3 | 4197 | 7 | 117 |
| run_002 | low | 2207 | 900 | 900 | 1 | 650857 | 994 | 4 | 3979 | 8 | 111 |
| run_003 | low | 3301 | 900 | 900 | 0 | 657832 | 1042 | 1 | 4181 | 13 | 115 |
| run_004 | low | 4409 | 900 | 900 | 0 | 676836 | 1075 | 8 | 4041 | 6 | 118 |
| run_005 | moderate | 1103 | 868 | 851 | 0 | 1656891 | 1718 | 16 | 9674 | 13 | 214 |
| run_006 | moderate | 2207 | 880 | 866 | 3 | 1569824 | 1778 | 14 | 10038 | 12 | 216 |
| run_007 | moderate | 3301 | 874 | 856 | 2 | 1876426 | 1683 | 19 | 10338 | 18 | 251 |
| run_008 | moderate | 4409 | 884 | 873 | 3 | 1829243 | 1865 | 28 | 10015 | 12 | 242 |
| run_009 | high | 1103 | 728 | 660 | 9 | 5194754 | 2650 | 81 | 24706 | 10 | 620 |
| run_010 | high | 2207 | 749 | 683 | 6 | 5097995 | 2722 | 70 | 25048 | 17 | 593 |
| run_011 | high | 3301 | 708 | 637 | 8 | 5509192 | 2518 | 79 | 25749 | 22 | 626 |
| run_012 | high | 4409 | 691 | 620 | 10 | 5473167 | 2841 | 66 | 26217 | 14 | 641 |



## Sampling methodology



sampling.yaml is the complete distribution specification. The generator uses Python random.Random with independent population, background-parameter, schedule, and per-run seeds. Bounded beta distributions concentrate mass away from extremes. tau has a 94% central / 6% short-gap mixture; red-entry willingness and foe-ignore settings have small point-mass/continuous mixtures. Mixtures are unnamed parameter priors and do not preassign events. The realized 90-driver cohort may differ from nominal mixture weights.

All independent draws follow YAML insertion order. emergencyDecel is max(sample, decel); apparentDecel is decel times a sampled ratio; lcPushyGap is the equivalent encoding minGapLat*(1-lcPushy). lcPushyGap is not an additional independent mechanism. speedDev=0 retains each sampled speedFactor. The installed parser rejected negative lcImpatience; the accepted configuration bounds it at [0,0.6]. The rejected smoke is preserved separately.

Driver characteristics: speedFactor, tau, sigma, minGap, startupDelay, actionStepLength, lane-changing/sublane/junction settings and impatience. Vehicle characteristics: accel, decel, emergencyDecel, apparentDecel, dimensions, mass, maxSpeed, maxSpeedLat, passenger category and HBEFA3/PC_G_EU6 emissions class. Capability inputs are not observed acceleration. These priors and their independence assumptions are exploratory, not calibrated.



## Events, formulas and interpretation



driving_events records episodes. Consecutive true simulation steps are coalesced, with onset, end and duration; value is the onset value, not the peak. Harsh acceleration >=2.5 m/s²; hard braking <=-4 m/s²; simulator limit exceedance speed > lane limit+0.5 m/s; short gap physical bumper gap/speed <1 s when speed>2 m/s; following TTC gap/(ego speed-leader speed) <1.5 s when closing speed>0. Stops/go events cross 0.1 m/s. Departure, arrival and teleport events are lifecycle observations. These definitions are descriptive thresholds, not insurance risk rules.

TraCI leader gap excludes ego minGap; physical gap is returned gap+ego minGap. Follower physical gap adds follower minGap. Headway is the instantaneous net bumper gap divided by ego speed, not configured tau and not a detector passage interval. TTC is undefined when no observed leader or no positive closing speed. Jerk is the finite difference of observed acceleration at consecutive telemetry samples; the first sample is null. A leader not present in the current subscription set has null derived metrics.

A traffic-light entry requires an external-lane to internal-lane transition matching the network connection via lane and controlling TLS link. Red-signal entry requires red on both sides of that 0.1-second interval. Boundary phase changes are not counted as confirmed red. Right-turn direction is retained. verified_legal_violation is always null because legal permissions and turn-on-red semantics were not independently verified. Approaching red is not an entry. Entries without internal-lane matching are not inferred.

Native lane-change rows retain raw attributes and surrounding actual/secure gaps. unsafe_gap_proxy compares actual target gaps to SUMO secure gaps; it is not a verified unsafe human merge. Lateral position telemetry supports separate analysis of lane encroachment; no geometrically validated side-encroachment event label is asserted. SSM junction_conflict_proxy uses raw extrema types 6,7,10–17,19; it is not a proven failure to yield.

SSM raw is one row per native conflict, with all measure attributes and raw XML, preserving both ego/foe reports and code 111. Native TTC/DRAC/PET/PPET/MDRAC are taken directly from SUMO; time/position/type correspond to the measure extreme. They are not recomputed with the simple following TTC formula for junctions. Deduplication groups an unordered participant pair in a run into connected overlapping time intervals with 0.2 s tolerance. Raw IDs map back to every constituent report. Minima are combined by min, maxima by max; separate encounters with a gap >0.2 s stay separate. Transitive overlap can merge a long sequence of interactions.

Collision-log matches require the same unordered pair and a detector time within encounter interval ±0.2 s. Code 111 alone never confirms a collision. Collision action is teleport. SUMO default collision.mingap-factor=1 detects longitudinal gap violations and junction overlap; collision-output is not proof of physical contact or damage. Teleport counts include congestion and collision teleports; raw warning logs distinguish causes, while lifecycle records preserve timing. Collision-related disappearance classification is conservative and does not claim a simulator reason code when unavailable.

SSM default thresholds are TTC 3 s, DRAC 3 m/s², PET 2 s, PPET 2 s, MDRAC 3.4 m/s²; range 50 m, extra tracking 5 s, MDRAC reaction time 1 s. Only threshold-qualified native conflicts are logged, not every interaction. See official SUMO documentation: https://sumo.dlr.de/docs/Simulation/Output/SSM_Device.html and https://sumo.dlr.de/docs/Simulation/Safety.html .



## Measurement and limitations



All trajectory floats retain native double precision; SUMO XML is configured to eight decimal places. Parquet uses ZSTD. Null means unavailable/undefined/not applicable; zero is an observed or configured zero. Heading is SUMO degrees clockwise from north, positions are projected network metres (see network location metadata), speed m/s, distance/gap m, acceleration m/s², jerk m/s³, time s, mass kg. Native XML strings remain in raw_attributes_json/raw_xml.

Summary exposure uses sampled observations ×0.5 s; speed-excess distance is a rectangular integral speed×0.5 s. This can miss short episodes or approximate trip endpoints. Events use 0.1-second logic. Rates per 100 km use TripInfo routeLength, including any teleport progress; rates per hour use TripInfo duration. Incomplete-trip denominators need care. Telemetry speed statistics are time-sampled, not trip-weighted. Per-run driver quantiles are exact for sampled values; aggregate-driver means/standard deviations are weighted by telemetry rows. Headway/TTC missingness is conditional on interacting traffic.

Network OSM provenance and lane speed limits have not been legally audited. Exceedances refer to simulator metadata, not real-world legal speeding. Vehicle mass and emissions class are metadata for the default Krauss dynamics, not a calibrated fleet model. Lane count includes the current edge; junction proximity is distance to the end of the current lane, not Euclidean distance to a junction centre.

Only passenger motor vehicles are modeled. No pedestrians/cyclists, measured demographic attributes, human perception errors, weather or real claims are supplied. No demographic table was requested. Density is a demand treatment; observed concurrency/queues measure realized density. Seeds repeat across density conditions as common random numbers, but differing vehicle populations consume random streams differently. Statistical train/test splits in later work must group by persistent driver ID and should test unseen routes and seeds.

The old LC2013 diagnostic is retained under baseline/: 90 completed trips, 82 lane changes, 108 raw SSM records, zero collision-output records; D083_T01/D087_T01 have reciprocal code-111 reports. Baseline statistics.xml is empty and is not evidence of full-run completion. New experiment data are separate.



## SSM distributions and missingness



| metric | total_encounters | defined | missing | negative_values | raw_nonfinite_values | mean | minimum | p05 | median | p95 |
|---|---|---|---|---|---|---|---|---|---|---|
| minTTC | 79328 | 73535 | 5793 | 0 | 0 | 736.4655368169657 | 0.0 | 0.0 | 2.35745936 | 45.63632255200002 |
| maxDRAC | 79328 | 77469 | 1859 | 0 | 0 | 11.73200280988666 | 0.0 | 0.000665952 | 2.23152696 | 3.8169997499999986 |
| PET | 79328 | 4258 | 75070 | 25 | 0 | 4.1503765245984034 | -8.36928189 | 0.8018336775000001 | 1.86277325 | 7.889307913999995 |
| minPPET | 79328 | 31849 | 47479 | 22739 | 0 | -39.46557119691798 | -478256.76005245 | -373.80109957799993 | -0.94851801 | 3.2089896959999815 |
| maxMDRAC | 79328 | 74972 | 4356 | 29 | 0 | 57.72407173934081 | -10.69571158 | 0.00022117000000000016 | 3.664120455 | 31.16355515399999 |



See STATUS.json, quality_report.json, per-run validation.json, summaries/*.csv, and handoff_manifest.csv. Full telemetry is retained in datasets/vehicle_telemetry/run_id=.../. A ZIP is complete only when STATUS.json states complete=true.

## Realised cohort and reference joins

Of 90 study drivers, 2 have tau <=0.65 s, 0 have tau below their action interval, 8 have a positive red-entry willingness setting, and 9 have nonzero foe-ignore probability. These are inputs, not observed infractions. Ten route slots are stable across all runs; distinct template counts per driver are {10: 80, 9: 10}. Repeated templates are retained and not counted as independent route diversity.

Telemetry static inputs are normalized into driver_parameters (join on driver_id). Background types are in background_vehicle_parameters, joined through background_traffic_manifest type_id. Each driver retains one vehicle capability set, so driver/vehicle effects are confounded in this design. run_id is stored losslessly in the Hive partition folder rather than duplicated in canonical telemetry files. Use pyarrow.dataset.dataset(path, format="parquet", partitioning="hive") or pandas.read_parquet(path) for all partitions. Per-run runtime queue snapshots occur every 10 seconds; sampled queue maxima may miss between-snapshot peaks. Main mean/max concurrency is measured every simulation step.

The first main attempt completed SUMO but failed during summary writing on a duplicate teleport key. Its full raw/telemetry outputs are preserved under run_001/attempts and excluded from main tables. The corrected same-seed replay matched all 1,350 native TripInfo records exactly (see replay_equivalence.json). The subscription optimisation also matched every smoke telemetry value. Parameter read-back passed 1,350 installed-SUMO checks. See diagnostics/parameter_readback.json and test_results.xml.

The isolated following-brake probe in diagnostics/following_brake_probe is excluded from main counts: tau=0.35 s with a 1 s action interval produced four collision-log records for each of two seeds, while tau=1.5 s produced none. Planned leader stops, ordinary insertion checks, and standard safety logic were used. This illustrates model sensitivity; it does not estimate human crash probability.

Unavailable or unverified features: legal signal-violation classification, calibrated human blind spots, real-world speed-limit verification, a certified failure-to-yield label, continuous geometric lateral-encroachment duration, claims damage/severity, and demographic effects. Native lateral-gap and junction-conflict proxies remain available separately. Network geometry warnings identify incomplete SSM coverage; raw simulator warnings are preserved and counted.

## Installed-model precision and collision defaults

A separate 180-insertion attribute probe (two sequential short episodes per driver) confirmed stable effective speed factors with speedDev=0. SUMO precision.random defaults to four decimal places, so per-vehicle speedFactor differs from the full-precision assigned vType input by at most approximately 0.00005. driver_effective_parameters.parquet retains both values; no main input was revised. Telemetry itself retains full double precision.

The saved installed defaults are in diagnostics/sumo_defaults.xml. collision.mingap-factor is configured by its default -1, which delegates to the car-following model; the Krauss inherited effective value is 1. Longitudinal records can therefore be minGap infringements, while junction checks use geometric overlap. This is confirmed in [SUMO 1.28.0 MSCFModel source](https://github.com/eclipse-sumo/sumo/blob/v1_28_0/src/microsim/cfmodels/MSCFModel.cpp) and [MSLane collision checking](https://github.com/eclipse-sumo/sumo/blob/v1_28_0/src/microsim/MSLane.cpp). A collision-output record must not be interpreted as a human injury/damage outcome.

The ZIP mirrors the runnable repository layout. Active datasets are under experiments/unlabeled_v1/datasets/. Root README/report/dictionary files are convenience copies. Legacy root data and baseline/ copies are explicitly separate from main observations. Polygon files are retained for visualisation; they do not affect headless vehicle dynamics.

## Native surrogate formulas and validity

For physical following gap g and positive closing speed Δv: TTC=g/Δv, DRAC=Δv²/(2g), and MDRAC=Δv/[2(TTC−PRT)], with native PRT=1 s. PET is follower conflict-area entry time minus leader exit time; PPET uses projected entry/exit timing. Junction TTC/DRAC use native conflict-area geometry and case handling rather than the simple following formula. These are descriptions of native measures, not recomputations of exported extrema. See the [SUMO SSM specification](https://sumo.dlr.de/docs/Simulation/Output/SSM_Device.html).

Native negative PET/PPET/MDRAC and very large extrema are retained, including geometrically questionable projections. They must not automatically become risk severity. ssm_quality_flags.json reports negative/nonfinite values and long-TTC tails. MDRAC and merging/crossing geometry have documented model limitations. Numeric NA/nonfinite outputs map to null while *_value_raw and raw_xml preserve the original strings. The cohort distributions are conditional on native conflict logging thresholds and interaction opportunity, not population crash-rate estimates.

## Read telemetry in bounded batches

The complete telemetry dataset is large. Select columns and runs when exploring it; the ZIP never substitutes a sample for the full observations.

```python
import pyarrow.dataset as ds
traffic = ds.dataset("experiments/unlabeled_v1/datasets/vehicle_telemetry",
                     format="parquet", partitioning="hive")
scanner = traffic.scanner(columns=["run_id", "driver_id", "trip_id", "timestamp_s", "speed_mps"],
                          filter=ds.field("run_id") == "run_001", batch_size=65536)
for batch in scanner.to_batches():
    frame = batch.to_pandas()
    # Analyse or aggregate this batch; avoid retaining all batches in memory.
```

## Execution recovery

High-density runs 010 and 012 encountered native SUMO bad-allocation failures under parallel execution. Their attempts, including partial telemetry and potentially truncated native XML, are preserved under their attempts/ folders and excluded from main tables. The identical frozen seeds/inputs were replayed sequentially. diagnostics/recovered_prefix_validation.json compares every completed TripInfo record from those failed prefixes with the successful replay. The concurrency helper now defaults to one worker to bound native SSM memory. Only successful, validated per-run outputs enter canonical datasets.

## Observed density treatments

| density | successful_runs | mean_concurrent_vehicles | maximum_concurrent_vehicles | mean_study_speed_mps | mean_study_waiting_s | collision_records | study_teleports | background_teleports | red_signal_entries |
|---|---|---|---|---|---|---|---|---|---|
| low | 4 | 52.53561 | 118 | 9.45575 | 68.24719 | 16 | 1 | 15 | 34 |
| moderate | 4 | 111.18655 | 251 | 5.91324 | 174.3268 | 77 | 8 | 69 | 55 |
| high | 4 | 342.9204 | 641 | 3.04405 | 430.51563 | 296 | 33 | 401 | 63 |

## Observed variation across the 90 persistent study identities

| metric | drivers_with_defined_metric | mean | std_between_drivers | minimum | p05 | median | p95 | maximum |
|---|---|---|---|---|---|---|---|---|
| speed_mean_mps | 90 | 5.48911 | 1.42778 | 3.23128 | 3.78046 | 5.21064 | 7.87835 | 10.41677 |
| speed_std_mps | 90 | 7.02696 | 0.50735 | 5.7157 | 6.17322 | 7.00742 | 7.81639 | 8.38004 |
| speed_limit_exceedance_s | 90 | 5039.67222 | 4693.02417 | 0.0 | 0.0 | 5951.5 | 11596.9 | 13051.5 |
| headway_mean | 90 | 7.03293 | 1.33374 | 4.27791 | 4.89964 | 6.91629 | 9.11124 | 11.67473 |
| ttc_min | 90 | 0.39862 | 0.34001 | 0.00311 | 0.03962 | 0.3008 | 1.17166 | 1.33212 |
| hard_braking_episodes_per_100km | 90 | 213.99108 | 103.99975 | 0.0 | 0.39619 | 241.02761 | 324.58298 | 424.78454 |
| lane_changes_per_100km | 90 | 32.36414 | 17.47622 | 5.78972 | 11.49936 | 29.3764 | 68.03755 | 86.96771 |
| short_headway_exposure_s | 90 | 102.45556 | 430.01188 | 1.0 | 2.225 | 24.5 | 128.375 | 3107.5 |
| red_signal_entries | 90 | 0.73333 | 2.72236 | 0.0 | 0.0 | 0.0 | 7.1 | 17.0 |
| collision_involvements | 90 | 1.24444 | 1.63742 | 0.0 | 0.0 | 1.0 | 3.55 | 11.0 |

## Behavior detector coverage

| is_background | event_type | count |
|---|---|---|
| False | go | 68554 |
| False | hard_braking | 49843 |
| False | harsh_acceleration | 47551 |
| False | junction_ssm_conflict_proxy | 16674 |
| False | lane_change | 7560 |
| False | red_signal_entry_observed | 66 |
| False | reduced_lateral_clearance_proxy | 34 |
| False | short_following_gap | 2273 |
| False | simulator_limit_exceedance | 70391 |
| False | stop | 58764 |
| False | sumo_collision_involvement | 112 |
| False | ttc_near_miss | 6866 |
| False | unsafe_merge_gap_proxy | 219 |
| True | go | 177790 |
| True | hard_braking | 96148 |
| True | harsh_acceleration | 58483 |
| True | junction_ssm_conflict_proxy | 42346 |
| True | lane_change | 14319 |
| True | red_signal_entry_observed | 86 |
| True | reduced_lateral_clearance_proxy | 0 |
| True | short_following_gap | 6707 |
| True | simulator_limit_exceedance | 164620 |
| True | stop | 160316 |
| True | sumo_collision_involvement | 666 |
| True | ttc_near_miss | 15309 |
| True | unsafe_merge_gap_proxy | 407 |