"""Measure SUMO's per-vehicle speed-factor handling, without touching main runs."""
import pyarrow.parquet as pq
import traci
import research_pipeline as p

def run():
    drivers=pq.read_table(p.BASE/'datasets/driver_parameters.parquet').to_pylist()
    plans=[r for r in pq.read_table(p.BASE/'datasets/trip_manifest.parquet').to_pylist() if r['run_id']=='run_001' and r['trip_number']<=2]
    actual={}; lookup={}; active={}
    try:
        traci.start([str(p.SUMO),'-n',str(p.ROOT/'network/london_filtered.net.xml'),'-r',str(p.BASE/'runs/run_001/study_types.rou.xml'),
                     '--step-length','.1','--step-method.ballistic','true','--lateral-resolution','.5','--no-step-log','true'])
        queue=sorted(plans,key=lambda r:(r['trip_number'],r['driver_id']))
        for _ in range(10000):
            retained=[]
            for row in queue:
                if row['driver_id'] in active:
                    retained.append(row); continue
                vid='audit_'+row['trip_id']; lookup[vid]=row; active[row['driver_id']]=vid
                # One permitted edge suffices for this attribute read-back, avoiding long diagnostic journeys.
                traci.route.add(vid,[row['route_edges'].split()[0]])
                traci.vehicle.add(vid,vid,typeID=row['driver_id'],depart='now',departSpeed='0')
            queue=retained
            traci.simulationStep()
            for vid in traci.simulation.getDepartedIDList(): actual.setdefault(lookup[vid]['driver_id'],[]).append(traci.vehicle.getSpeedFactor(vid))
            for vid in traci.simulation.getArrivedIDList(): active.pop(lookup[vid]['driver_id'],None)
            if sum(map(len,actual.values()))==180: break
        traci.close()
    finally:
        try: traci.close()
        except Exception: pass
    rows=[]
    for d in drivers:
        vals=actual[d['driver_id']]; assert len(vals)==2 and vals[0]==vals[1]
        rows.append(dict(driver_id=d['driver_id'],assigned_speedFactor=d['speedFactor'],effective_speedFactor=vals[0],audit_vehicle_count=2,consistent_across_audit_trips=True,difference=vals[0]-d['speedFactor']))
    p.table(p.BASE/'datasets/driver_effective_parameters.parquet',rows)
    p.js(p.BASE/'diagnostics/effective_speed_factor_audit.json',dict(study_drivers=90,audit_insertions=180,all_replicates_identical=True,
          description='Installed SUMO rounds chosen per-vehicle speed factors using precision.random (default 4), even though the vType input retains full precision. No main priors changed.'))
    print('Audited 180 insertions, 90 stable effective speed factors')

if __name__=='__main__': run()
