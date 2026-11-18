"""Create the explicit versioned sampler configuration, preserving baseline first."""
from pathlib import Path
import yaml
root=Path(__file__).resolve().parents[1]
assert (root/'experiments/unlabeled_v1/baseline/config/sampling.yaml').exists()
assert not (root/'experiments/unlabeled_v1/prepared.json').exists(), 'Experiment already frozen; revise priors only in a new version'
cfg=dict(version='unlabeled_v1',population_seed=20261117,schedule_seed=20261118,background_parameter_seed=20261119,
         run_seeds=[1103,2207,3301,4409],n_drivers=90,trips_per_driver=10,simulation_step_s=0.1,telemetry_interval_s=0.5,
         lateral_resolution_m=0.5,wave_seconds=600,departure_window_s=240,clearance_s=1800,time_to_teleport_s=300,
         background_per_wave=dict(low=45,moderate=135,high=270),
         dependencies=dict(emergencyDecel='max(sampled emergencyDecel, decel)',apparentDecel='decel * apparentDecelRatio',
                           lcPushyGap='minGapLat * (1-lcPushy); equivalent alternative encoding, not an independent draw',
                           tau='6% low-tail draw intentionally permits tau < actionStepLength; may reduce safety',
                           all_others='independent conditional on constants; no latent classes'),
         events=dict(harsh_acceleration_mps2=2.5,hard_braking_mps2=4.0,speed_excess_mps=0.5,short_headway_s=1.0,headway_min_speed_mps=2.0,near_miss_ttc_s=1.5))
spec='''speedFactor .77 1.33 5 5
accel 1.2 3.5 3 3
decel 3.2 5.8 3 3
emergencyDecel 6 9 3 3
apparentDecelRatio .8 1.06 3 3
sigma .05 .9 2 4
minGap .9 4 3 3
startupDelay 0 1.5 2 4
lcStrategic .4 2.5 3 4
lcCooperative .05 1 4 2
lcSpeedGain .25 3 3 4
lcKeepRight .3 2.5 3 3
lcAssertive .75 2 2 5
lcSigma 0 .45 2 5
minGapLat .25 .8 3 3
lcPushy 0 .6 1.5 6
lcImpatience 0 .6 2 4
lcAccelLat .5 1.8 3 3
lcSublane .5 1.5 3 3
maxSpeedLat .5 1.2 3 3
impatience 0 .8 2 5
jmDriveRedSpeed 3 8 3 3
jmTimegapMinor .7 1.5 3 3
length 3.7 5.3 3 3
width 1.65 2.1 3 3
mass 1000 2300 3 4
maxSpeed 33.3 50 3 3'''
params={}
def beta(lo,hi,a,b): return dict(distribution='beta',bounds=[lo,hi],shape=[a,b])
def const(v): return dict(distribution='constant',value=v)
for line in spec.splitlines():
    name,*values=line.split(); params[name]=beta(*map(float,values))
params['tau']=dict(distribution='mixture',weights=[.94,.06],components=[beta(.85,2.4,3,4),beta(.28,.65,2,2)])
params['actionStepLength']=dict(distribution='choice',values=[.2,.4,.6,.8,1.0],weights=[.1,.25,.35,.2,.1])
params['jmDriveAfterRedTime']=dict(distribution='mixture',weights=[.94,.06],components=[const(-1),beta(.2,2,2,4)])
params['jmIgnoreFoeProb']=dict(distribution='mixture',weights=[.95,.05],components=[const(0),beta(.002,.025,2,5)])
params.update({k:const(v) for k,v in dict(lcMaxSpeedLatStanding=0,jmIgnoreFoeSpeed=10,emissionClass='HBEFA3/PC_G_EU6',
              vClass='passenger',speedDev=0,carFollowModel='Krauss',laneChangeModel='SL2015').items()})
cfg['parameters']=params
(root/'config/sampling.yaml').write_text('# Exploratory synthetic priors; not calibrated population statistics. No labels.\n'+yaml.safe_dump(cfg,sort_keys=False))
