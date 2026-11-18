"""Reproducible observational SUMO experiment. Never writes legacy data paths."""
from __future__ import annotations
import argparse, collections, csv, hashlib, json, math, os, platform, random
import shutil, subprocess, sys, time, zipfile
from pathlib import Path
import xml.etree.ElementTree as ET
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import yaml
import traci
import traci.constants as tc

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'experiments/unlabeled_v1'
SUMO = Path(os.environ.get('SUMO_HOME', 'C:/Program Files (x86)/SUMO')) / 'bin/sumo.exe'

def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1024*1024), b''): h.update(b)
    return h.hexdigest()

def js(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, default=str), encoding='utf-8')

def table(path, rows, schema=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    t = pa.Table.from_pylist(rows, schema=schema) if rows or schema else pa.table({'run_id': pa.array([], pa.string())})
    pq.write_table(t, path, compression='zstd')
    return t

def sample(rng, s):
    kind = s['distribution']
    if kind == 'constant': return s['value']
    if kind == 'choice': return rng.choices(s['values'], weights=s['weights'])[0]
    if kind == 'mixture': return sample(rng, rng.choices(s['components'], weights=s['weights'])[0])
    lo, hi = s['bounds']
    if kind == 'beta': return lo + (hi-lo)*rng.betavariate(*s['shape'])
    if kind == 'uniform': return rng.uniform(lo, hi)
    raise ValueError(kind)

def population(cfg, n=90):
    rng = random.Random(cfg['population_seed'])
    rows = []
    for i in range(n):
        d = {k: sample(rng, v) for k, v in cfg['parameters'].items()}
        # Explicit deterministic dependency rules, also stored in YAML.
        d['emergencyDecel'] = max(d['decel'], d['emergencyDecel'])
        d['apparentDecel'] = d['decel'] * d.pop('apparentDecelRatio')
        d['lcPushyGap'] = d['minGapLat'] * (1-d['lcPushy'])
        d.update(driver_id=f'D{i:03d}', parameter_version='unlabeled_v1', population_seed=cfg['population_seed'])
        d['tau_below_action_step'] = d['tau'] < d['actionStepLength']
        rows.append(d)
    return rows

def write_xml(path, root):
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(root, space='  ')
    ET.ElementTree(root).write(path, encoding='utf-8', xml_declaration=True)

def attrs(d):
    return {k: str(v) for k,v in d.items() if k not in {'driver_id','parameter_version','population_seed','tau_below_action_step'}}

def prepare(smoke=False):
    base = BASE / 'smoke' if smoke else BASE
    marker = base/'prepared.json'
    if marker.exists():
        assert json.loads(marker.read_text())['sampling_sha256']==digest(ROOT/'config/sampling.yaml'), 'Sampling changed: use a new experiment version, never mutate completed priors'
        return base
    cfg = yaml.safe_load((ROOT/'config/sampling.yaml').read_text())
    assert cfg['n_drivers']==90 and cfg['trips_per_driver']==10
    assert set(cfg['background_per_wave'])=={'low','moderate','high'}
    assert len(cfg['run_seeds'])==len(set(cfg['run_seeds']))==4
    assert cfg['simulation_step_s']==.1 and cfg['lateral_resolution_m']>0
    assert abs(cfg['telemetry_interval_s']/.1-round(cfg['telemetry_interval_s']/.1))<1e-8
    base.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT/'config/sampling.yaml', base/'sampling.yaml')
    drivers = population(cfg, 5 if smoke else 90)
    table(base/'datasets/driver_parameters.parquet', drivers)
    source = ET.parse(ROOT/'routes/london.rou.xml').getroot()
    defs = {r.get('id'): r.get('edges') for r in source.findall('route')}
    templates = [(v.get('id'), v.find('route').get('edges') if v.find('route') is not None else defs[v.get('route')]) for v in source.findall('vehicle')]
    rng = random.Random(cfg['schedule_seed'])
    ntrips = 2 if smoke else 10
    planned = []
    for t in range(ntrips):
        for d in drivers:
            tpl = rng.choice(templates)
            planned.append(dict(driver_id=d['driver_id'], trip_id=f"{d['driver_id']}_T{t+1:02d}", trip_number=t+1,
                                depart_scheduled_s=float(t*cfg['wave_seconds']+rng.randrange(cfg['departure_window_s'])),
                                source_route_template_id=tpl[0], route_edges=tpl[1], is_background=False))
    runs, manifests, backgrounds, metadata = [], [], [], []
    for idx, (density, count) in enumerate(cfg['background_per_wave'].items()):
        for replicate, seed in enumerate(cfg['run_seeds']):
            rid = f'run_{idx*4+replicate+1:03d}' if not smoke else 'smoke_001'
            if smoke and runs: continue
            rd = base/'runs'/rid
            (rd/'raw').mkdir(parents=True, exist_ok=True)
            study = ET.Element('routes')
            for d in drivers: ET.SubElement(study,'vType',id=d['driver_id'],**attrs(d))
            rows = []
            for p in sorted(planned,key=lambda p:(p['depart_scheduled_s'],p['trip_id'])):
                row = dict(run_id=rid, **p, sim_vehicle_id=p['trip_id'], global_trip_id=f"{rid}:{p['trip_id']}")
                rows.append(row)
                # Triggered departures are released by the runner, after previous trip ends.
                v=ET.SubElement(study,'vehicle',id=p['trip_id'],type=p['driver_id'],depart='triggered',departLane='best',departSpeed='0')
                ET.SubElement(v,'route',edges=p['route_edges'])
            # Runtime adds routes/vehicles from this manifest; XML provides vTypes and reproducible reference routes.
            # Triggered vehicles would remain pending forever, so use only vTypes in runtime file.
            reference=rd/'study_planned.rou.xml'; write_xml(reference,study)
            runtime=ET.Element('routes')
            for d in drivers: ET.SubElement(runtime,'vType',id=d['driver_id'],**attrs(d))
            write_xml(rd/'study_types.rou.xml',runtime)
            bg=ET.Element('routes')
            bd=population({**cfg,'population_seed':cfg['background_parameter_seed']}, 12)
            for j,d in enumerate(bd): ET.SubElement(bg,'vType',id=f'BGTYPE{j}',**attrs(d))
            brng=random.Random(seed)
            bgrows=[]
            for t in range(ntrips):
                for j in range(10 if smoke else count):
                    tpl=brng.choice(templates); vid=f'BG_{t:02d}_{j:04d}'
                    bgrows.append(dict(run_id=rid,driver_id=None,trip_id=vid,sim_vehicle_id=vid,global_trip_id=f'{rid}:{vid}',
                                       depart_scheduled_s=float(t*cfg['wave_seconds']+brng.randrange(cfg['departure_window_s'])),
                                       route_edges=tpl[1],source_route_template_id=tpl[0],is_background=True,type_id=f'BGTYPE{j%12}'))
            for r in sorted(bgrows,key=lambda x:x['depart_scheduled_s']):
                v=ET.SubElement(bg,'vehicle',id=r['trip_id'],type=r['type_id'],depart=str(r['depart_scheduled_s']),departLane='best',departSpeed='0')
                ET.SubElement(v,'route',edges=r['route_edges'])
            write_xml(rd/'background.rou.xml',bg)
            c=ET.Element('configuration')
            sections={}
            def option(section,k,v):
                if section not in sections: sections[section]=ET.SubElement(c,section)
                ET.SubElement(sections[section],k,value=str(v))
            option('input','net-file','../../../../network/london_filtered.net.xml' if not smoke else '../../../../../network/london_filtered.net.xml')
            option('input','route-files','study_types.rou.xml,background.rou.xml')
            for key,name in [('tripinfo-output','tripinfo'),('collision-output','collisions'),('lanechange-output','lanechanges'),('statistic-output','statistics')]: option('output',key,f'raw/{name}.xml')
            option('output','tripinfo-output.write-unfinished','true')
            option('output','tripinfo-output.write-undeparted','true')
            option('output','precision',8)
            option('time','step-length',cfg['simulation_step_s'])
            option('processing','step-method.ballistic','true'); option('processing','lateral-resolution',cfg['lateral_resolution_m'])
            option('processing','collision.action','teleport'); option('processing','collision.check-junctions','true')
            option('processing','time-to-teleport',cfg['time_to_teleport_s'])
            option('random_number','seed',seed)
            option('report','no-step-log','true'); option('report','log','raw/sumo.log')
            option('report','error-log','raw/warnings.log'); option('report','duration-log.statistics','true')
            option('ssm_device','device.ssm.probability',1)
            option('ssm_device','device.ssm.measures','TTC DRAC PET PPET MDRAC')
            option('ssm_device','device.ssm.file','raw/ssm.xml')
            write_xml(rd/'simulation.sumocfg',c)
            runs.append(dict(run_id=rid,density=density,seed=seed,replicate=replicate+1,planned_study=len(rows),planned_background=len(bgrows),
                             max_simulation_s=ntrips*cfg['wave_seconds']+cfg['clearance_s'],configuration_sha256=digest(rd/'simulation.sumocfg')))
            manifests.extend(rows); backgrounds.extend(bgrows)
            metadata.extend(dict(run_id=rid,driver_id=d['driver_id'],density=density,seed=seed,parameter_version='unlabeled_v1') for d in drivers)
    for name,rows in [('trip_manifest',manifests),('background_traffic_manifest',backgrounds),('driver_run_metadata',metadata),('simulation_runs',runs)]: table(base/f'datasets/{name}.parquet',rows)
    js(marker,dict(runs=len(runs),drivers=len(drivers),study_trips=len(manifests),sampling_sha256=digest(base/'sampling.yaml')))
    return base

# Typed schemas remain useful when an event is absent.
IDENT=[('run_id',pa.string()),('driver_id',pa.string()),('trip_id',pa.string()),('sim_vehicle_id',pa.string()),('is_background',pa.bool_())]
TEL=pa.schema(IDENT+[(k,pa.float64()) for k in ['timestamp_s','x_m','y_m','lane_position_m','lateral_position_m','heading_deg','speed_mps','acceleration_mps2','jerk_mps3','distance_m','waiting_time_s','time_loss_s','lane_speed_limit_mps','allowed_speed_mps','leader_gap_m','follower_gap_m','relative_speed_mps','observed_headway_s','ttc_s','junction_distance_m','tls_distance_m']]+[(k,pa.string()) for k in ['edge_id','lane_id','edge_type','leader_id','follower_id','tls_id','tls_state']]+[('lane_index',pa.int32()),('lane_count',pa.int32()),('tls_link_index',pa.int32()),('route_index',pa.int32())])
EV=pa.schema(IDENT+[('timestamp_s',pa.float64()),('end_s',pa.float64()),('duration_s',pa.float64()),('event_type',pa.string()),('value',pa.float64()),('other_vehicle_id',pa.string()),('source',pa.string())])
TL=pa.schema(IDENT+[('timestamp_s',pa.float64()),('tls_id',pa.string()),('link_index',pa.int32()),('incoming_lane',pa.string()),('internal_lane',pa.string()),('signal_before',pa.string()),('signal_after',pa.string()),('turn_direction',pa.string()),('red_signal_entry_observed',pa.bool_()),('right_turn',pa.bool_()),('verified_legal_violation',pa.bool_()),('uncertainty',pa.string())])

class Stream:
    def __init__(self,path,schema):
        path.parent.mkdir(parents=True,exist_ok=True)
        self.writer=pq.ParquetWriter(path,schema,compression='zstd'); self.schema=schema; self.rows=[]; self.count=0
    def add(self,row):
        self.rows.append(row); self.count+=1
        if len(self.rows)>=20000: self.flush()
    def flush(self):
        if self.rows: self.writer.write_table(pa.Table.from_pylist(self.rows,schema=self.schema)); self.rows=[]
    def close(self): self.flush(); self.writer.close()

def identity(rid,vid,lookup):
    p=lookup.get(vid,{})
    return dict(run_id=rid,driver_id=p.get('driver_id'),trip_id=vid,sim_vehicle_id=vid,is_background=p.get('is_background',vid.startswith('BG_')))

def network_context():
    net=ET.parse(ROOT/'network/london_filtered.net.xml').getroot()
    lanes={}
    for e in net.findall('edge'):
        for l in e.findall('lane'): lanes[l.get('id')]=dict(limit=float(l.get('speed')),length=float(l.get('length')),edge_type=e.get('type'),lane_count=len(e.findall('lane')))
    links={}
    for c in net.findall('connection'):
        if c.get('tl'):
            key=(c.get('tl'),int(c.get('linkIndex')),f"{c.get('from')}_{c.get('fromLane')}")
            links[key]=dict(via=c.get('via'),direction=c.get('dir'))
    return lanes,links

def run_one(base,rid):
    rd=base/'runs'/rid
    assert base.resolve().is_relative_to(ROOT)
    assert rd.resolve().is_relative_to((base/'runs').resolve()), 'Run path escaped experiment directory'
    if (rd/'complete.json').exists(): return json.loads((rd/'complete.json').read_text())
    if (rd/'started.json').exists():
        # Preserve interrupted attempts before resuming this run from its beginning.
        attempt=rd/'attempts'/str(time.time_ns()); attempt.mkdir(parents=True)
        for name in ['raw','telemetry.parquet','events.parquet','traffic_light_entries.parquet','trip_summary.parquet','runtime_statistics.parquet','runtime_state.json','collector_source.py','progress.json','started.json']:
            if (rd/name).exists(): shutil.move(str(rd/name),str(attempt/name))
        (rd/'raw').mkdir()
    cfg=yaml.safe_load((base/'sampling.yaml').read_text())
    runs=pq.read_table(base/'datasets/simulation_runs.parquet').to_pylist()
    run=next(r for r in runs if r['run_id']==rid)
    plans=[r for r in pq.read_table(base/'datasets/trip_manifest.parquet').to_pylist() if r['run_id']==rid]
    bg=[r for r in pq.read_table(base/'datasets/background_traffic_manifest.parquet').to_pylist() if r['run_id']==rid]
    lookup={p['trip_id']:p for p in plans+bg}
    lanes,links=network_context()
    js(rd/'started.json',dict(started_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),seed=run['seed']))
    shutil.copy2(Path(__file__),rd/'collector_source.py')
    tel=Stream(rd/'telemetry.parquet',TEL); events=Stream(rd/'events.parquet',EV); tl=Stream(rd/'traffic_light_entries.parquet',TL)
    fields={'speed_mps':tc.VAR_SPEED,'acceleration_mps2':tc.VAR_ACCELERATION,'edge_id':tc.VAR_ROAD_ID,'lane_id':tc.VAR_LANE_ID,
            'lane_position_m':tc.VAR_LANEPOSITION,'lateral_position_m':tc.VAR_LANEPOSITION_LAT,'heading_deg':tc.VAR_ANGLE,
            'distance_m':tc.VAR_DISTANCE,'waiting_time_s':tc.VAR_WAITING_TIME,'time_loss_s':tc.VAR_TIMELOSS,'allowed_speed_mps':tc.VAR_ALLOWED_SPEED,
            'lane_index':tc.VAR_LANE_INDEX,'route_index':tc.VAR_ROUTE_INDEX}
    variables=list(fields.values())+[tc.VAR_POSITION,tc.VAR_NEXT_TLS,tc.VAR_LEADER,tc.VAR_FOLLOWER]
    queue=sorted(plans,key=lambda p:(p['depart_scheduled_s'],p['trip_id']))
    active_driver={}; previous={}; prev_tel={}; episodes={}; lifecycle={}; stats=[]; mingaps={}; teleporting=set()
    start=time.monotonic(); step=0; max_active=0; total_active=0; nsteps=0; overlap=0
    def emit(vid,t,kind,value=None,other=None,end=None):
        events.add(dict(**identity(rid,vid,lookup),timestamp_s=t,end_s=end,duration_s=None if end is None else end-t,event_type=kind,value=value,other_vehicle_id=other,source='TraCI_observed'))
    oldcwd=Path.cwd()
    try:
        os.chdir(rd)
        traci.start([str(SUMO),'-c','simulation.sumocfg'],stdout=open(rd/'raw/process_stdout.log','w'))
        while step*cfg['simulation_step_s'] < run['max_simulation_s']:
            now=traci.simulation.getTime()
            retained=[]
            for p in queue:
                if p['depart_scheduled_s']>now or p['driver_id'] in active_driver:
                    retained.append(p); continue
                vid=p['trip_id']; routeid='route_'+vid
                traci.route.add(routeid,p['route_edges'].split())
                traci.vehicle.add(vid,routeid,typeID=p['driver_id'],depart='now',departLane='best',departSpeed='0')
                active_driver[p['driver_id']]=vid
                lifecycle.setdefault(vid,{})['released_s']=now
            queue=retained
            traci.simulationStep(); step+=1; t=traci.simulation.getTime()
            for vid in traci.simulation.getDepartedIDList():
                lifecycle.setdefault(vid,{})['depart_actual_s']=t
                traci.vehicle.subscribe(vid,variables,parameters={tc.VAR_LEADER:('d',150.),tc.VAR_FOLLOWER:('d',150.)})
                mingaps[vid]=traci.vehicle.getMinGap(vid)
                emit(vid,t,'departure')
            teleported=set(traci.simulation.getStartingTeleportIDList())
            teleporting.update(teleported)
            for vid in teleported:
                lifecycle.setdefault(vid,{})['teleports']=lifecycle.setdefault(vid,{}).get('teleports',0)+1
                emit(vid,t,'teleport_start')
            for vid in traci.simulation.getEndingTeleportIDList():
                teleporting.discard(vid); emit(vid,t,'teleport_end')
            for vid in traci.simulation.getCollidingVehiclesIDList():
                lifecycle.setdefault(vid,{})['collision_observed']=True
            arrived=traci.simulation.getArrivedIDList()
            for vid in arrived:
                lifecycle.setdefault(vid,{})['arrival_actual_s']=t; emit(vid,t,'arrival')
                d=lookup.get(vid,{}).get('driver_id')
                if d and active_driver.get(d)==vid: active_driver.pop(d)
            results=traci.vehicle.getAllSubscriptionResults()
            counts=collections.Counter(lookup[v]['driver_id'] for v in results if v in lookup and not lookup[v]['is_background'])
            overlap+=sum(max(0,n-1) for n in counts.values())
            if any(n>1 for n in counts.values()): raise RuntimeError('Simultaneous study trips for one driver')
            count=len(results); max_active=max(max_active,count); total_active+=count; nsteps+=1
            sample_now=step % round(cfg['telemetry_interval_s']/cfg['simulation_step_s'])==0
            sample_speeds=[]; waiting=0
            for vid,s in results.items():
                if not s or s.get(tc.VAR_SPEED,-1)<0: continue
                lane=s[tc.VAR_LANE_ID]; speed=s[tc.VAR_SPEED]; acc=s[tc.VAR_ACCELERATION]
                context=lanes.get(lane,{}); nxt=s.get(tc.VAR_NEXT_TLS,()); old=previous.get(vid)
                if old and old['lane']!=lane and lane.startswith(':') and old['tls']:
                    signal=old['tls'][0]; key=(signal[0],signal[1],old['lane']); link=links.get(key,{})
                    # Require the actual controlled via lane, not merely an approaching red signal.
                    if link.get('via')==lane:
                        state_after=traci.trafficlight.getRedYellowGreenState(signal[0])[signal[1]]
                        red=signal[3] in ('r','R') and state_after in ('r','R')
                        tl.add(dict(**identity(rid,vid,lookup),timestamp_s=t,tls_id=signal[0],link_index=signal[1],incoming_lane=old['lane'],
                                    internal_lane=lane,signal_before=signal[3],signal_after=state_after,turn_direction=link.get('direction'),
                                    red_signal_entry_observed=red,right_turn=link.get('direction')=='r',verified_legal_violation=None,
                                    uncertainty='stop-line crossing bracketed by 0.1s; legal turn permissions unverified'))
                leader=s.get(tc.VAR_LEADER); leader_id=leader[0] if leader and leader[0] else None
                gap=None; rel=None; headway=None; ttc=None
                if leader_id and leader_id in results:
                    # TraCI leader gap excludes ego minGap. Recover physical bumper gap.
                    gap=float(leader[1])+mingaps[vid]
                    rel=speed-results[leader_id].get(tc.VAR_SPEED,speed)
                    if speed>cfg['events']['headway_min_speed_mps']: headway=gap/speed
                    if rel>0 and gap>=0: ttc=gap/rel
                flags={'harsh_acceleration':acc>=cfg['events']['harsh_acceleration_mps2'],
                       'hard_braking':acc<=-cfg['events']['hard_braking_mps2'],
                       'simulator_limit_exceedance':speed>context.get('limit',math.inf)+cfg['events']['speed_excess_mps'],
                       'short_following_gap':headway is not None and headway<cfg['events']['short_headway_s'],
                       'ttc_near_miss':ttc is not None and ttc<cfg['events']['near_miss_ttc_s']}
                for kind,on in flags.items():
                    key=(vid,kind)
                    if on and key not in episodes:
                        episodes[key]=(t,ttc if kind=='ttc_near_miss' else headway if kind=='short_following_gap' else speed if kind=='simulator_limit_exceedance' else acc,leader_id)
                    if not on and key in episodes:
                        st,val,other=episodes.pop(key); emit(vid,st,kind,val,other,t)
                if old and old['speed']>0.1 and speed<=0.1: emit(vid,t,'stop',speed)
                if old and old['speed']<=0.1 and speed>0.1: emit(vid,t,'go',speed)
                previous[vid]=dict(lane=lane,tls=nxt,speed=speed)
                lifecycle.setdefault(vid,{})['last_seen_s']=t
                if not sample_now: continue
                r={k:s.get(v) for k,v in fields.items()}; x,y=s[tc.VAR_POSITION]
                last=prev_tel.get(vid)
                follower=s.get(tc.VAR_FOLLOWER,('',-1))
                fid=follower[0] or None
                fgap=follower[1]+mingaps[fid] if fid and fid in results else None
                row=dict(**identity(rid,vid,lookup),**r,timestamp_s=t,x_m=x,y_m=y,
                         jerk_mps3=(acc-last[1])/(t-last[0]) if last else None,lane_speed_limit_mps=context.get('limit'),
                         edge_type=context.get('edge_type'),lane_count=context.get('lane_count'),junction_distance_m=max(0,context.get('length',0)-s[tc.VAR_LANEPOSITION]),
                         leader_id=leader_id,leader_gap_m=gap,follower_id=fid,follower_gap_m=fgap,relative_speed_mps=rel,observed_headway_s=headway,ttc_s=ttc,
                         tls_id=nxt[0][0] if nxt else None,tls_link_index=nxt[0][1] if nxt else None,tls_distance_m=nxt[0][2] if nxt else None,tls_state=nxt[0][3] if nxt else None)
                tel.add(row); prev_tel[vid]=(t,acc)
                sample_speeds.append(speed); waiting+=speed<0.1
            gone=set(previous)-set(results)
            for vid in gone:
                previous.pop(vid,None); prev_tel.pop(vid,None)
                for key in [k for k in episodes if k[0]==vid]:
                    st,val,other=episodes.pop(key); emit(vid,st,key[1],val,other,t)
                d=lookup.get(vid,{}).get('driver_id')
                if d and active_driver.get(d)==vid and vid not in teleporting:
                    # An unexpected disappearance is accounted as removal; no overlapping episode.
                    active_driver.pop(d); lifecycle.setdefault(vid,{})['disappearance_s']=t
            if step%100==0:
                stats.append(dict(run_id=rid,timestamp_s=t,active_vehicles=count,pending_insertions=len(traci.simulation.getPendingVehicles()),
                                  waiting_vehicles=waiting if sample_now else None,mean_speed_mps=float(np.mean(sample_speeds)) if sample_speeds else None))
            if step%1000==0:
                js(rd/'progress.json',dict(run_id=rid,simulation_s=t,telemetry_rows=tel.count,arrivals=sum('arrival_actual_s' in v for v in lifecycle.values()),remaining_study=len(queue),wall_s=time.monotonic()-start))
            if not queue and traci.simulation.getMinExpectedNumber()==0: break
        for (vid,kind),(st,val,other) in episodes.items(): emit(vid,st,kind,val,other,t)
        version=traci.getVersion()
        traci.close()
    finally:
        tel.close(); events.close(); tl.close(); os.chdir(oldcwd)
        try: traci.close()
        except Exception: pass
    info={e.get('id'):e.attrib for e in ET.parse(rd/'raw/tripinfo.xml').getroot().findall('tripinfo')}
    js(rd/'runtime_state.json',dict(lifecycle=lifecycle,simulation_end_s=t,version=version,max_active=max_active,total_active=total_active,nsteps=nsteps,wall_s=time.monotonic()-start))
    table(rd/'runtime_statistics.parquet',stats)
    summaries=[]
    for p in plans+bg:
        vid=p['trip_id']; life=lifecycle.get(vid,{}); ti=info.get(vid,{})
        inserted='depart_actual_s' in life; arrived='arrival_actual_s' in life
        status='arrived' if arrived else 'non_inserted' if not inserted else 'collision_related_removal' if life.get('collision_observed') and life.get('disappearance_s') else 'removed' if life.get('disappearance_s') else 'incomplete'
        summaries.append(dict(**identity(rid,vid,lookup),global_trip_id=p['global_trip_id'],depart_scheduled_s=p['depart_scheduled_s'],
                              **{**life,'teleports':life.get('teleports',0)},inserted=inserted,completed=arrived,final_status=status,
                              departure_delay_s=life.get('depart_actual_s',p['depart_scheduled_s'])-p['depart_scheduled_s'] if inserted else None,
                              duration_s=float(ti['duration']) if ti else None,distance_m=float(ti['routeLength']) if ti else None,
                              time_loss_s=float(ti['timeLoss']) if ti else None,waiting_time_s=float(ti['waitingTime']) if ti else None))
    # Schema inference from union keys avoids losing rare outcome fields from later rows.
    keys=set().union(*(r.keys() for r in summaries)); summaries=[{k:r.get(k) for k in sorted(keys)} for r in summaries]
    table(rd/'trip_summary.parquet',summaries); table(rd/'runtime_statistics.parquet',stats)
    study=[s for s in summaries if not s['is_background']]
    out=dict(**run,successful=True,study_driver_count=len({r['driver_id'] for r in study}),study_manifest_count=len(study),
             inserted=sum(s['inserted'] for s in study),completed=sum(s['completed'] for s in study),teleports=sum(s['teleports'] for s in study),
             statuses=dict(collections.Counter(s['final_status'] for s in study)),telemetry_rows=tel.count,event_rows=events.count,tls_entry_rows=tl.count,
             max_concurrent=max_active,mean_concurrent=total_active/nsteps,wall_s=time.monotonic()-start,simulation_end_s=t,sumo_version=version,
             no_overlap_violations=overlap)
    assert len(study)==run['planned_study']
    js(rd/'complete.json',out)
    print(json.dumps(out),flush=True)
    return out

def main():
    p=argparse.ArgumentParser(); p.add_argument('command',choices=['prepare','smoke','run','summarize','package']); p.add_argument('--run-id'); a=p.parse_args()
    if a.command=='prepare': print(prepare())
    elif a.command=='smoke': run_one(prepare(True),'smoke_001')
    elif a.command=='run':
        base=prepare()
        for r in pq.read_table(base/'datasets/simulation_runs.parquet').to_pylist():
            if a.run_id is None or a.run_id==r['run_id']: run_one(base,r['run_id'])
    elif a.command=='summarize':
        from research_handoff import summarize
        summarize(BASE)
    elif a.command=='package':
        from research_handoff import package
        package(BASE)

if __name__=='__main__': main()
