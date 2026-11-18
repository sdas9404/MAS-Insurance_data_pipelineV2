"""Read back assigned capabilities through the installed SUMO 1.28.0 API."""
import json
import pyarrow.parquet as pq
import traci
import research_pipeline as p

def run():
    rows=pq.read_table(p.BASE/'datasets/driver_parameters.parquet').to_pylist()
    methods=dict(tau='getTau',speedFactor='getSpeedFactor',accel='getAccel',decel='getDecel',emergencyDecel='getEmergencyDecel',
                 apparentDecel='getApparentDecel',sigma='getImperfection',minGap='getMinGap',actionStepLength='getActionStepLength',
                 length='getLength',width='getWidth',maxSpeed='getMaxSpeed',mass='getMass',minGapLat='getMinGapLat',maxSpeedLat='getMaxSpeedLat')
    checks=[]
    try:
        traci.start([str(p.SUMO),'-n',str(p.ROOT/'network/london_filtered.net.xml'),'-r',str(p.BASE/'runs/run_001/study_types.rou.xml'),
                     '--step-length','.1','--step-method.ballistic','true','--lateral-resolution','.5','--no-step-log','true'])
        version=traci.getVersion()
        for d in rows:
            for field,method in methods.items():
                value=getattr(traci.vehicletype,method)(d['driver_id'])
                passed=abs(value-d[field])<1e-10
                checks.append(dict(driver_id=d['driver_id'],parameter=field,assigned=d[field],sumo_effective=value,passed=passed))
                assert passed,(d['driver_id'],field,value,d[field])
        traci.close()
    finally:
        try: traci.close()
        except Exception: pass
    p.table(p.BASE/'diagnostics/parameter_readback.parquet',checks)
    p.js(p.BASE/'diagnostics/parameter_readback.json',dict(sumo_version=version,checks=len(checks),all_passed=True,
         remaining_attributes='Accepted by installed SUMO parser in smoke and main runs; generic LC/JM attributes lack individual vehicletype getters.'))
    print(json.dumps(dict(checks=len(checks),all_passed=True)))

if __name__=='__main__': run()
