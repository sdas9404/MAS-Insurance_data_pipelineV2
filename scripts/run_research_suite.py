"""Resume remaining main runs; sequential by default to bound native SSM memory."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import argparse, subprocess, sys, json, time, shutil
import pyarrow.parquet as pq
import research_pipeline as p

def execute(rid):
    rd=p.BASE/'runs'/rid
    if (rd/'runner_console.log').exists():
        archive=rd/'console_history'/f'{time.time_ns()}.log'
        archive.parent.mkdir(exist_ok=True)
        assert archive.resolve().is_relative_to(p.BASE.resolve())
        shutil.move(str(rd/'runner_console.log'),str(archive))
    with (rd/'runner_console.log').open('w') as log:
        result=subprocess.run([sys.executable,str(p.ROOT/'scripts/research_pipeline.py'),'run','--run-id',rid],cwd=p.ROOT,stdout=log,stderr=subprocess.STDOUT)
    if result.returncode: raise RuntimeError(f'{rid} failed: see {rd}/runner_console.log')
    return json.loads((rd/'complete.json').read_text())

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--workers',type=int,default=1); args=parser.parse_args()
    assert args.workers>=1
    assert (p.BASE/'runs/run_001/validation.json').exists(), 'Validate the first full run before scaling'
    runs=[r['run_id'] for r in pq.read_table(p.BASE/'datasets/simulation_runs.parquet').to_pylist() if not (p.BASE/'runs'/r['run_id']/'complete.json').exists()]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures={pool.submit(execute,r):r for r in runs}
        for done in as_completed(futures): print(json.dumps(done.result()),flush=True)
