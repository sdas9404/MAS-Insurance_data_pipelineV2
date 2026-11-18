"""Human-readable checkpoint inventory; never equate planned trips with arrivals."""
import json
import pyarrow.parquet as pq
import research_pipeline as p

def status():
    rows=[]
    for run in pq.read_table(p.BASE/'datasets/simulation_runs.parquet').to_pylist():
        rd=p.BASE/'runs'/run['run_id']
        row=dict(run_id=run['run_id'],density=run['density'],seed=run['seed'],planned_study_trips=900)
        if (rd/'complete.json').exists():
            d=json.loads((rd/'complete.json').read_text())
            row.update(state='validated' if (rd/'validation.json').exists() else 'execution_finished_awaiting_validation',inserted=d['inserted'],arrived=d['completed'],telemetry_rows=d['telemetry_rows'],simulation_s=d['simulation_end_s'])
        elif (rd/'progress.json').exists():
            d=json.loads((rd/'progress.json').read_text())
            row.update(state='started_not_finalized; may be running or interrupted',simulation_s=d['simulation_s'],telemetry_rows=d['telemetry_rows'])
        else: row['state']='not_started'
        rows.append(row)
    out=dict(expected_main_runs=12,executions_finished=sum('arrived' in r for r in rows),validated_runs=sum(r['state']=='validated' for r in rows),
             planned_study_trips=10800,arrivals_in_finished_runs=sum(r.get('arrived',0) for r in rows),runs=rows,
             resume_command='python scripts/research_pipeline.py run; completed runs are skipped and interrupted attempts are archived')
    p.js(p.BASE/'LIVE_STATUS.json',out); p.js(p.ROOT/'deliverables/EXECUTION_STATUS.json',out)
    for r in rows: print(r['run_id'],r['density'],r['state'],r.get('simulation_s'),r.get('arrived',''))
    return out

if __name__=='__main__': status()
