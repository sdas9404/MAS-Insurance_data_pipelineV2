"""Validate final relational counts and primary dataset schemas before packaging."""
from collections import Counter
import json
import pyarrow as pa
import pyarrow.parquet as pq
import research_pipeline as p

def validate(base=p.BASE):
    ds=base/'datasets'
    drivers=pq.read_table(ds/'driver_parameters.parquet').to_pylist()
    assert len(drivers)==len({r['driver_id'] for r in drivers})==90
    assert not {'persona','risk_score','risk_label','premium'} & set(drivers[0])
    metadata=pq.read_table(ds/'driver_run_metadata.parquet',columns=['run_id','driver_id']).to_pylist()
    assert len(metadata)==len({(r['run_id'],r['driver_id']) for r in metadata})==1080
    plan=pq.read_table(ds/'trip_manifest.parquet').to_pylist()
    assert len(plan)==len({r['global_trip_id'] for r in plan})==10800
    assert set(Counter((r['run_id'],r['driver_id']) for r in plan).values())=={10}
    assert set(Counter(r['run_id'] for r in plan).values())=={900}
    assert len({r['run_id'] for r in plan})==12
    driver_ids={r['driver_id'] for r in drivers}; run_ids={r['run_id'] for r in plan}
    assert {(r['run_id'],r['driver_id']) for r in metadata}=={(rid,did) for rid in run_ids for did in driver_ids}
    assert all(r['driver_id'] in driver_ids and r['global_trip_id']==r['run_id']+':'+r['trip_id'] for r in plan)
    summary=pq.read_table(ds/'trip_summary.parquet').to_pylist()
    study=[r for r in summary if not r['is_background']]
    background=[r for r in summary if r['is_background']]
    assert len(study)==10800 and len(background)==18000
    assert len(summary)==len({r['global_trip_id'] for r in summary})==28800
    assert {r['global_trip_id'] for r in study}=={r['global_trip_id'] for r in plan}
    bgplan=pq.read_table(ds/'background_traffic_manifest.parquet',columns=['global_trip_id','type_id']).to_pylist()
    bgtypes={r['type_id'] for r in pq.read_table(ds/'background_vehicle_parameters.parquet',columns=['type_id']).to_pylist()}
    assert len(bgtypes)==12 and len(bgplan)==18000
    assert {r['global_trip_id'] for r in bgplan}=={r['global_trip_id'] for r in background}
    assert all(r['type_id'] in bgtypes for r in bgplan)
    for r in summary:
        assert r['is_background']==(r['driver_id'] is None)
        assert not r['completed'] or r['inserted']
        assert r['inserted']==(r.get('depart_actual_s') is not None)
        assert r['completed']==(r.get('arrival_actual_s') is not None)
        if not r['inserted']: assert r['duration_s'] is None and r['distance_m'] is None
    features=pq.read_table(ds/'per_driver_features.parquet',columns=['driver_id','planned_trips']).to_pylist()
    assert len(features)==90 and all(r['planned_trips']==120 for r in features)
    assert {r['driver_id'] for r in features}==driver_ids
    dr=pq.read_table(ds/'per_driver_run_features.parquet',columns=['run_id','driver_id','planned_trips','completed_trips','incomplete_trips','non_inserted_trips','removed_trips']).to_pylist()
    assert len(dr)==1080 and all(r['planned_trips']==10 for r in dr)
    assert all(r['completed_trips']+r['incomplete_trips']+r['non_inserted_trips']+r['removed_trips']==10 for r in dr)
    refs={(r['run_id'],r['trip_id']) for r in summary}
    for batch in pq.ParquetFile(ds/'collisions.parquet').iter_batches(columns=['run_id','collider','victim']):
        assert all((r['run_id'],r['collider']) in refs and (r['run_id'],r['victim']) in refs for r in batch.to_pylist())
    for name,columns in [('driving_events',['run_id','trip_id']),('traffic_light_entries',['run_id','trip_id']),('lane_changes',['run_id','trip_id'])]:
        for batch in pq.ParquetFile(ds/f'{name}.parquet').iter_batches(columns=columns):
            assert all((r['run_id'],r['trip_id']) in refs for r in batch.to_pylist())
    native=pq.read_table(ds/'ssm_conflicts_raw.parquet',columns=['run_id','raw_record_id','ego','foe','encounter_id']).to_pylist()
    encounters=pq.read_table(ds/'ssm_encounters_deduplicated.parquet',columns=['encounter_id','raw_record_ids','raw_record_count','confirmed_collision','collision_log_matches']).to_pylist()
    assert len(native)==len({r['raw_record_id'] for r in native})
    assert all((r['run_id'],r['ego']) in refs and (r['run_id'],r['foe']) in refs for r in native)
    ids=[i for e in encounters for i in e['raw_record_ids']]
    assert len(ids)==len(set(ids))==len(native)
    assert set(ids)=={r['raw_record_id'] for r in native}
    assert all(e['raw_record_count']==len(e['raw_record_ids']) and e['confirmed_collision']==bool(e['collision_log_matches']) for e in encounters)
    signal=pq.read_table(ds/'traffic_light_entries.parquet',columns=['verified_legal_violation'])
    assert signal.schema.field('verified_legal_violation').type==pa.bool_()
    assert signal['verified_legal_violation'].null_count==signal.num_rows
    runs=pq.read_table(ds/'run_statistics.parquet').to_pylist()
    assert len(runs)==12
    assert all(r['study_driver_count']==90 and r['study_manifest_count']==900 for r in runs)
    telemetry=0
    for r in runs:
        rd=base/'runs'/r['run_id']
        quality=json.loads((rd/'validation.json').read_text())
        assert quality['passed'] and quality['actual_overlap_violations']==0
        file=ds/f"vehicle_telemetry/run_id={r['run_id']}/part-00000.parquet"
        pf=pq.ParquetFile(file)
        assert pf.metadata.num_rows==r['telemetry_rows']
        assert pf.schema_arrow.field('timestamp_s').type==pa.float64()
        assert pf.schema_arrow.field('driver_id').type==pa.string()
        assert pf.schema_arrow.field('is_background').type==pa.bool_()
        telemetry+=pf.metadata.num_rows
    report=dict(passed=True,main_runs=12,driver_parameters_rows=90,driver_run_metadata_rows=1080,planned_study_trips=10800,
                trip_summary_study_rows=len(study),trip_summary_background_rows=len(background),telemetry_rows=telemetry,
                observed_study_inserted=sum(r['inserted'] for r in study),observed_study_arrived=sum(r['completed'] for r in study),
                observed_study_non_inserted=sum(not r['inserted'] for r in study),observed_study_inserted_not_arrived=sum(r['inserted'] and not r['completed'] for r in study),
                all_12_run_driver_trip_counts_passed=True,all_foreign_keys_checked=True,deduplication_lossless=True,no_observed_driver_overlap=True)
    p.js(base/'handoff_validation.json',report)
    print(json.dumps(report),flush=True)
    return report

if __name__=='__main__': validate()
