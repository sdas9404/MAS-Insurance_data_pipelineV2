# Execution checkpoint and handoff completion

The full objective is twelve **unlabeled** SUMO 1.28.0 runs, not a classifier or risk-score pipeline. The frozen manifests contain 90 persistent study drivers and 900 planned trips per run (10,800 total). Background demand is separate. Do not change completed priors, routes, schedules, or raw observations.

The original LC2013 files are preserved in `baseline/` and checked by tests. The safety diagnostic has 90 arrivals, 82 lane changes, 108 raw SSM reports, zero collision-output records, and the D083/D087 code-111 discrepancy.

Ten runs completed before recovery: 001–009 and 011. Runs 010 and 012 failed under concurrent native memory pressure (`bad allocation`). The original attempts are retained, with readable partial Parquet. A sequential `python scripts/research_pipeline.py run` resumes only unfinished runs, archiving their previous attempts. Check OS processes and completion markers before launching anything else. Native memory demand is substantial; the suite helper now defaults to one worker. Changing worker count does not change frozen inputs.

`scripts/experiment_status.py` reports current markers and updates `LIVE_STATUS.json` and `deliverables/EXECUTION_STATUS.json`. An unfinished marker may mean running or interrupted; it does not prove a live process. `progress.json` is updated every 100 simulated seconds.

Remaining completion steps:

1. Let sequential replays finish and confirm twelve `runs/*/complete.json` files. Do not rerun finished seeds.
2. Run release tests (`python -m pytest -q tests ...`); nine tests most recently passed. Pytest fixtures require execution outside this environment's restrictive sandbox.
3. Run `python scripts/research_pipeline.py package`. It recomputes all observations, checks each run's actual driver overlap and references, validates preserved failed-trip prefixes against replays, exports all partitioned telemetry, validates 10,800 study plus 18,000 background trip summaries, writes reports/dictionary/manifest, builds the single ZIP, checks CRC and every member hash, extracts everything and fully reads every included Parquet, and checks relative configuration references. It refuses a full handoff before all twelve runs exist.
4. Run the included tests from `extracted_validation/tests` using the original virtual environment and a separate pytest basetemp. Add the real extracted-test result to the outside ZIP validation/status JSON. Do not claim these checks passed before executing them.
5. Final response must link `deliverables/SE4457_PhD_handoff.zip`, give actual successful-run/arrival/telemetry/event counts, confirm all twelve 90-driver/900-planned-trip validations, list summaries/report, and state warnings/unverified legal/actuarial meanings.

Main data is only under `datasets/`; baseline, smoke, calibration, and attempts are excluded from primary totals. Runs may reach the fixed 7,800-second horizon with non-inserted and incomplete trips. Preserve every planned outcome. Null legal-violation flags remain unverified; red-signal crossings and SSM type 111 are not legal or physical-crash labels. Native surrogate outliers remain raw and flagged. Effective SUMO speedFactor rounding was audited separately. No demographics, premiums, personas, or risk scores are generated.

The ZIP mirrors workspace paths (`experiments/unlabeled_v1/...`) so its scripts, tests, and relative SUMO input paths remain runnable after extraction. Full telemetry is retained; do not substitute samples to reduce size. `deliverables/STATUS.json` and `zip_validation.json` are the final integrity proof outside the ZIP.
