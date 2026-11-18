"""Native-output derivation, validation, summaries and full handoff packaging."""
from __future__ import annotations
import collections, csv, hashlib, json, math, platform, re, shutil, subprocess, sys, time, zipfile
from pathlib import Path
import xml.etree.ElementTree as ET
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import research_pipeline as p
from research_dictionary import define

def numeric(v):
    if v in (None,'NA','None','nan','-1.#IND'): return None
    try:
        f=float(v); return f if math.isfinite(f) else None
    except (ValueError,TypeError): return None

def csv_table(path,rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    pd.DataFrame(rows).to_csv(path,index=False)

def markdown_table(frame):
    if frame.empty: return '(No completed runs)'
    f=frame.copy().fillna('NA')
    return '\n'.join(['| '+' | '.join(map(str,f.columns))+' |','|'+'|'.join(['---']*len(f.columns))+'|']+['| '+' | '.join(str(v) for v in row)+' |' for row in f.itertuples(index=False,name=None)])

def artifact_purpose(arc):
    if 'vehicle_telemetry/' in arc: return 'Full sampled trajectories for study and background vehicles; run_id encoded by partition'
    if '/baseline/' in arc or arc.startswith('data/'): return 'Preserved LC2013 foundation/diagnostic artifact: '+Path(arc).name
    if '/attempts/' in arc or '/diagnostics/' in arc or '/smoke' in arc: return 'Engineering, calibration, or audit evidence excluded from main observations: '+Path(arc).name
    names={'driver_parameters':'Fixed 90-driver sampled inputs and vehicle capabilities',
        'driver_effective_parameters':'Installed-SUMO chosen speed-factor audit, two insertions per driver',
        'driver_run_metadata':'1080 planned driver/run records joined to observed exposure and outcomes',
        'trip_manifest':'10800 frozen planned study episodes with stable routes and schedules',
        'background_traffic_manifest':'Separate planned background journeys and vehicle-type keys',
        'background_vehicle_parameters':'12 separately sampled background vehicle-type parameter sets',
        'trip_summary':'Every planned study/background trip and its final observed status, including non-insertions',
        'driving_events':'Observed motion episodes, lifecycle events and explicitly named native/proxy events',
        'lane_changes':'Native lane-change records with surrounding gaps, reason and raw attributes',
        'collisions':'Separate native SUMO collision detector records, both participants attributed',
        'traffic_light_entries':'Matched controlled stop-line crossings, red-entry flag and unverified legal status',
        'ssm_conflicts_raw':'All native ego/foe SSM reports and original XML, retaining code 111',
        'ssm_encounters_deduplicated':'Unordered-pair interval encounter components with lossless raw-record references',
        'simulation_runs':'12 frozen density/seed/input configuration records',
        'run_statistics':'Measured run load, outcomes, safety counts, quality flags and input bundle hashes',
        'per_driver_run_features':'Driver/run descriptive features and exposure-normalized rates',
        'per_driver_features':'90 persistent-driver descriptive aggregates across completed runs',
        'simulator_warnings':'Structured categories and complete text of native SUMO warnings'}
    if '/datasets/' in arc and Path(arc).stem in names: return names[Path(arc).stem]
    if arc.startswith('scripts/'): return 'Reproduction/collection/validation source: '+Path(arc).name
    if arc.startswith('tests/'): return 'Executable regression or integrity test: '+Path(arc).name
    if arc.startswith('network/'): return 'Preserved original geospatial simulation input: '+Path(arc).name
    if '/raw/' in arc: return 'Original SUMO safety/lifecycle output: '+Path(arc).name
    if arc.endswith('.sumocfg'): return 'Generated or preserved SUMO configuration; use Python runner for main study insertion'
    if arc.endswith('.rou.xml'): return 'Explicit vehicle types, planned routes, or background demand input'
    return 'Experiment documentation, summary, configuration or validation artifact: '+Path(arc).name

def typed(rows,fields):
    # Explicit schemas for empty tables; union of all fields for nonempty data.
    if not rows: return pa.Table.from_pylist([],schema=pa.schema(fields))
    keys=sorted(set().union(*(r.keys() for r in rows)))
    data=pa.Table.from_pylist([{k:r.get(k) for k in keys} for r in rows])
    declared={f.name:f.type for f in pa.schema(fields)}
    schema=pa.schema([pa.field(f.name,declared.get(f.name,f.type)) for f in data.schema])
    return data.cast(schema)

def save(path,rows,fields):
    path.parent.mkdir(parents=True,exist_ok=True)
    pq.write_table(typed(rows,fields),path,compression='zstd')

def deduplicate(raw):
    groups=collections.defaultdict(list)
    for r in raw: groups[(r['run_id'],*sorted((r['ego'],r['foe'])))].append(r)
    out=[]
    for (rid,a,b),records in sorted(groups.items()):
        clusters=[]
        for r in sorted(records,key=lambda x:x['begin_s']):
            if clusters and r['begin_s']<=max(x['end_s'] for x in clusters[-1])+.2: clusters[-1].append(r)
            else: clusters.append([r])
        for j,c in enumerate(clusters):
            eid=f'{rid}:{a}:{b}:{j}'
            for r in c: r['encounter_id']=eid
            d=dict(run_id=rid,encounter_id=eid,vehicle_a=a,vehicle_b=b,begin_s=min(r['begin_s'] for r in c),end_s=max(r['end_s'] for r in c),
                   raw_record_count=len(c),raw_record_ids=[r['raw_record_id'] for r in c],has_type_111=any(r['has_type_111'] for r in c),
                   confirmed_collision=False)
            d['junction_conflict_proxy']=any(r.get('junction_conflict_proxy',False) for r in c)
            for metric in ['minTTC','maxDRAC','PET','minPPET','maxMDRAC']:
                vals=[r.get(metric+'_value') for r in c if r.get(metric+'_value') is not None]
                d[metric+'_value']=(max(vals) if metric.startswith('max') else min(vals)) if vals else None
            out.append(d)
    return out

def native(rd,rid,lookup):
    lane=[]; collisions=[]; ssm=[]
    def participant(vid,prefix):
        d=p.identity(rid,vid,lookup)
        return {prefix+k:v for k,v in d.items() if k!='run_id'}
    for i,e in enumerate(ET.parse(rd/'raw/lanechanges.xml').getroot()):
        vid=e.get('id'); a=e.attrib
        d=dict(**p.identity(rid,vid,lookup),native_tag=e.tag,raw_attributes_json=json.dumps(a,sort_keys=True))
        for k,v in a.items(): d[k]=numeric(v) if k in ['time','dir','speed','pos','leaderGap','leaderSecureGap','leaderSpeed','followerGap','followerSecureGap','followerSpeed','origLeaderGap','origLeaderSecureGap','origLeaderSpeed','latGap'] else v
        d['unsafe_gap_proxy']=any(numeric(a.get(g)) is not None and numeric(a.get(s)) is not None and numeric(a[g])<numeric(a[s]) for g,s in [('leaderGap','leaderSecureGap'),('followerGap','followerSecureGap')])
        lane.append(d)
    for i,e in enumerate(ET.parse(rd/'raw/collisions.xml').getroot().findall('collision')):
        a=e.attrib
        d=dict(run_id=rid,collision_id=f'{rid}:C{i:06d}',raw_attributes_json=json.dumps(a),collision_action='teleport',
               detector='SUMO default minGap-factor=1 plus junction overlap; not necessarily physical contact',
               **participant(a.get('collider'),'collider_'),**participant(a.get('victim'),'victim_'))
        d.update({k:numeric(v) if k in ['time','pos','colliderSpeed','victimSpeed'] else v for k,v in a.items()})
        collisions.append(d)
    for i,e in enumerate(ET.parse(rd/'raw/ssm.xml').getroot().findall('conflict')):
        a=e.attrib
        d=dict(run_id=rid,raw_record_id=f'{rid}:S{i:07d}',ego=a['ego'],foe=a['foe'],begin_s=float(a['begin']),end_s=float(a['end']),
               raw_xml=ET.tostring(e,encoding='unicode'),**participant(a['ego'],'ego_'),**participant(a['foe'],'foe_'))
        types=[]
        for m in e:
            if m.get('value') is not None: d[f'{m.tag}_value_raw']=m.get('value')
            for k,v in m.attrib.items():
                value=(None if v in ('NA','None','') else v) if k=='position' else numeric(v)
                d[f'{m.tag}_{k}']=int(value) if k=='type' and value is not None else value
            if numeric(m.get('type')) is not None: types.append(int(float(m.get('type'))))
        d['has_type_111']=111 in types
        d['junction_conflict_proxy']=any(t in {6,7,10,11,12,13,14,15,16,17,19} for t in types)
        ssm.append(d)
    return lane,collisions,ssm

def telemetry_summary(path,lookup,interval):
    accum={}; validation=dict(rows=0,unknown_vehicle_rows=0,invalid_motion_rows=0,duplicate_observations=0,missing_counts={})
    last_t={}
    for batch in pq.ParquetFile(path).iter_batches(batch_size=65536):
        df=batch.to_pandas(); validation['rows']+=len(df)
        for col,n in df.isna().sum().items(): validation['missing_counts'][col]=validation['missing_counts'].get(col,0)+int(n)
        validation['unknown_vehicle_rows']+=int((~df.sim_vehicle_id.isin(lookup)).sum())
        validation['invalid_motion_rows']+=int(((df.speed_mps<0)|(~np.isfinite(df.x_m))|(~np.isfinite(df.y_m))).sum())
        for vid,g in df.groupby('sim_vehicle_id',sort=False):
            t=g.timestamp_s.to_numpy()
            validation['duplicate_observations']+=int(np.sum(np.diff(t)<=0)) + int(vid in last_t and t[0]<=last_t[vid])
            last_t[vid]=t[-1]
        for did,g in df[~df.is_background].groupby('driver_id'):
            a=accum.setdefault(did,dict(n=0,speed_sum=0.,speed_sumsq=0.,speed_max=0.,accel_min=0.,accel_max=0.,
                         speed_excess_s=0.,speed_excess_m=0.,tailgate_s=0.,stopped_s=0.,headway=[],ttc=[],acceleration=[],waiting_max=0.,speed=[]))
            s=g.speed_mps.to_numpy(); a['n']+=len(g); a['speed_sum']+=float(s.sum()); a['speed_sumsq']+=float((s*s).sum()); a['speed_max']=max(a['speed_max'],float(s.max()))
            a['accel_min']=min(a['accel_min'],float(g.acceleration_mps2.min())); a['accel_max']=max(a['accel_max'],float(g.acceleration_mps2.max()))
            exc=g.speed_mps>g.lane_speed_limit_mps+.5
            a['speed_excess_s']+=int(exc.sum())*interval; a['speed_excess_m']+=float(g.loc[exc,'speed_mps'].sum())*interval
            a['tailgate_s']+=int((g.observed_headway_s<1.).sum())*interval; a['stopped_s']+=int((g.speed_mps<.1).sum())*interval
            a['waiting_max']=max(a['waiting_max'],float(g.waiting_time_s.max()))
            for k,col in [('headway','observed_headway_s'),('ttc','ttc_s'),('acceleration','acceleration_mps2'),('speed','speed_mps')]: a[k].append(g[col].dropna().to_numpy())
    out=[]
    for did,a in accum.items():
        d=dict(driver_id=did,telemetry_rows=a['n'],sampled_exposure_s=a['n']*interval,speed_mean_mps=a['speed_sum']/a['n'],
               speed_std_mps=math.sqrt(max(0,a['speed_sumsq']/a['n']-(a['speed_sum']/a['n'])**2)),speed_max_mps=a['speed_max'],
               observed_acceleration_min_mps2=a['accel_min'],observed_acceleration_max_mps2=a['accel_max'],
               speed_limit_exceedance_s=a['speed_excess_s'],speed_limit_exceedance_m=a['speed_excess_m'],
               short_headway_exposure_s=a['tailgate_s'],stopped_exposure_s=a['stopped_s'],max_continuous_waiting_s=a['waiting_max'])
        for k in ['headway','ttc','acceleration','speed']:
            vals=np.concatenate(a[k]); d[k+'_defined_samples']=len(vals); d[k+'_missing_fraction']=1-len(vals)/a['n']
            d[k+'_mean']=float(vals.mean()) if len(vals) else None
            d[k+'_std']=float(vals.std()) if len(vals) else None
            d[k+'_min']=float(vals.min()) if len(vals) else None
            d[k+'_max']=float(vals.max()) if len(vals) else None
            for q in [.05,.25,.5,.75,.95]: d[f'{k}_p{int(q*100):02d}']=float(np.quantile(vals,q)) if len(vals) else None
        out.append(d)
    validation['sampled_vehicle_count']=len(last_t)
    validation['sampled_study_driver_count']=len(accum)
    return out,validation

def summarize(base):
    ds=base/'datasets'; summaries=base/'summaries'; summaries.mkdir(exist_ok=True)
    runs=pq.read_table(ds/'simulation_runs.parquet').to_pylist()
    plan=pq.read_table(ds/'trip_manifest.parquet').to_pylist(); bg=pq.read_table(ds/'background_traffic_manifest.parquet').to_pylist()
    lookup_by_run=collections.defaultdict(dict)
    for row in plan+bg: lookup_by_run[row['run_id']][row['trip_id']]=row
    all_lanes=[]; all_collisions=[]; raw_ssm=[]; trips=[]; runstats=[]; features=[]; qualities=[]; all_events=[]; all_tl=[]; warnings=[]
    import yaml
    cfg=yaml.safe_load((base/'sampling.yaml').read_text())
    _,controlled_links=p.network_context()
    for run in runs:
        rid=run['run_id']; rd=base/'runs'/rid
        if not (rd/'complete.json').exists(): continue
        print('Summarizing',rid,flush=True)
        done=json.loads((rd/'complete.json').read_text()); lookup=lookup_by_run[rid]
        l,c,s=native(rd,rid,lookup); all_lanes+=l; all_collisions+=c; raw_ssm+=s
        tr=pq.read_table(rd/'trip_summary.parquet').to_pylist()
        ev=pq.read_table(rd/'events.parquet').to_pylist(); all_events+=ev
        tls=pq.read_table(rd/'traffic_light_entries.parquet').to_pylist(); all_tl+=tls
        warning_counts=collections.Counter(); teleport_reasons=collections.defaultdict(list)
        for line in (rd/'raw/warnings.log').read_text(encoding='utf-8',errors='replace').splitlines():
            if not line.startswith(('Warning:','Error:')): continue
            category='ssm_geometry' if 'bad internal lane geometry' in line else 'ssm_collision_report' if 'SSM device' in line and 'detected collision' in line else 'teleport_start' if 'Teleporting vehicle' in line else 'teleport_end' if 'ends teleporting' in line else 'emergency_braking' if 'emergency braking' in line else 'parameter_compatibility' if 'action step length' in line else 'other'
            ts=re.search(r'(?:time=|at time=)([\d.]+)',line)
            warnings.append(dict(run_id=rid,category=category,timestamp_s=float(ts.group(1).rstrip('.')) if ts else None,raw_message=line))
            warning_counts[category]+=1
            reason=re.search(r"Teleporting vehicle '([^']+)'; (.*?)(?:, lane=|$)",line)
            if reason: teleport_reasons[reason.group(1)].append(reason.group(2))
        native_trips={e.get('id'):e.attrib for e in ET.parse(rd/'raw/tripinfo.xml').getroot().findall('tripinfo')}
        for r in tr:
            r['teleport_reason_messages']=teleport_reasons.get(r['sim_vehicle_id'],[])
            r['native_vaporized']=native_trips.get(r['sim_vehicle_id'],{}).get('vaporized')
            r['native_departure_s']=numeric(native_trips.get(r['sim_vehicle_id'],{}).get('depart'))
            r['native_arrival_s']=numeric(native_trips.get(r['sim_vehicle_id'],{}).get('arrival'))
            r['native_tripinfo_json']=json.dumps(native_trips.get(r['sim_vehicle_id'],{}),sort_keys=True)
            if not r['inserted']:
                # SUMO undeparted TripInfo placeholders are not measured travel outcomes.
                for field in ['duration_s','distance_m','waiting_time_s','time_loss_s']: r[field]=None
            r['removal_or_incomplete_reason']=None if r['completed'] else 'not_released_before_horizon' if not r['inserted'] and r.get('released_s') is None and not r['is_background'] else 'SUMO_pending_insertion_at_horizon' if not r['inserted'] else r['native_vaporized'] or 'not_arrived_by_horizon; inspect lifecycle and raw warnings'
        trips+=tr
        f,q=telemetry_summary(rd/'telemetry.parquet',lookup,cfg['telemetry_interval_s'])
        observed={d['driver_id'] for d in f}
        template=f[0] if f else {'telemetry_rows':0,'sampled_exposure_s':0.,'speed_mean_mps':None,'speed_std_mps':None,'speed_max_mps':None,
                 'speed_limit_exceedance_s':0.,'speed_limit_exceedance_m':0.,'short_headway_exposure_s':0.,'stopped_exposure_s':0.,'max_continuous_waiting_s':None}
        for did in sorted({r['driver_id'] for r in lookup.values() if not r['is_background']}-observed):
            d={k:None for k in template}; d['driver_id']=did
            for k in ['telemetry_rows','sampled_exposure_s','speed_limit_exceedance_s','speed_limit_exceedance_m','short_headway_exposure_s','stopped_exposure_s']: d[k]=0
            for metric in ['headway','ttc','acceleration','speed']:
                d[metric+'_defined_samples']=0; d[metric+'_missing_fraction']=1.
                for statistic in ['mean','std','min','max','p05','p25','p50','p75','p95']: d[metric+'_'+statistic]=None
            f.append(d)
        intervals=collections.defaultdict(list)
        for r in tr:
            if not r['is_background'] and r.get('depart_actual_s') is not None:
                intervals[r['driver_id']].append((r['depart_actual_s'],r.get('arrival_actual_s') or r.get('last_seen_s') or done['simulation_end_s']))
        overlap=0
        for ints in intervals.values():
            seq=sorted(ints)
            overlap+=sum(a[1]>b[0]+1e-8 for a,b in zip(seq,seq[1:]))
        q.update(run_id=rid,actual_overlap_violations=overlap,warning_counts=dict(warning_counts),study_driver_count=len({r['driver_id'] for r in tr if not r['is_background']}),
                 study_trip_count=sum(not r['is_background'] for r in tr),invalid_safety_references=sum(x not in lookup for r in s for x in [r['ego'],r['foe']]))
        q['invalid_collision_references']=sum(x not in lookup for r in c for x in [r.get('collider'),r.get('victim')])
        q['invalid_lanechange_references']=sum(r['sim_vehicle_id'] not in lookup for r in l)
        q['invalid_signal_entry_references']=sum(r['sim_vehicle_id'] not in lookup for r in tls)
        q['invalid_signal_derivations']=sum(controlled_links.get((r['tls_id'],r['link_index'],r['incoming_lane']),{}).get('via')!=r['internal_lane'] or
                  r['red_signal_entry_observed']!=(r['signal_before'] in ('r','R') and r['signal_after'] in ('r','R')) or r['verified_legal_violation'] is not None for r in tls)
        q['invalid_driving_event_references']=sum(r['sim_vehicle_id'] not in lookup or (r.get('other_vehicle_id') is not None and r['other_vehicle_id'] not in lookup) for r in ev)
        assert all(q[k]==0 for k in ['invalid_collision_references','invalid_lanechange_references','invalid_signal_entry_references','invalid_signal_derivations','invalid_driving_event_references'])
        assert overlap==0 and q['unknown_vehicle_rows']==0 and q['invalid_safety_references']==0 and q['duplicate_observations']==0
        assert q['study_driver_count']==90 and q['study_trip_count']==900
        q['passed']=True; qualities.append(q); p.js(rd/'validation.json',q)
        for d in f:
            did=d['driver_id']; d.update(run_id=rid,density=run['density'],seed=run['seed'])
            mytr=[x for x in tr if x['driver_id']==did]; myev=[x for x in ev if x['driver_id']==did]
            counts=collections.Counter(x['event_type'] for x in myev)
            for event in ['harsh_acceleration','hard_braking','simulator_limit_exceedance','short_following_gap','ttc_near_miss','stop','go']:
                d[event+'_episodes']=counts[event]
            d.update(planned_trips=len(mytr),inserted_trips=sum(x['inserted'] for x in mytr),completed_trips=sum(x['completed'] for x in mytr),
                     incomplete_trips=sum(x['final_status']=='incomplete' for x in mytr),non_inserted_trips=sum(not x['inserted'] for x in mytr),
                     removed_trips=sum(x['final_status'] in ('removed','collision_related_removal') for x in mytr),not_completed_trips=sum(not x['completed'] for x in mytr),
                     teleports=sum(x['teleports'] for x in mytr),teleported_trips=sum(x['teleports']>0 for x in mytr),
                     distance_m=sum(x['distance_m'] or 0 for x in mytr),travel_duration_s=sum(x['duration_s'] or 0 for x in mytr),
                     waiting_time_s=sum(x['waiting_time_s'] or 0 for x in mytr),time_loss_s=sum(x['time_loss_s'] or 0 for x in mytr),
                     lane_changes=sum(x['driver_id']==did and x['native_tag']=='change' for x in l),
                     unsafe_merge_gap_proxy_count=sum(x['driver_id']==did and x['unsafe_gap_proxy'] and x['native_tag']=='change' for x in l),
                     red_signal_entries=sum(x['driver_id']==did and x['red_signal_entry_observed'] for x in tls),
                     collision_involvements=sum(did in (x.get('collider_driver_id'),x.get('victim_driver_id')) for x in c),
                     junction_ssm_raw_reports=sum(did==x.get('ego_driver_id') and x['junction_conflict_proxy'] for x in s))
            for metric in ['lane_changes','hard_braking_episodes','harsh_acceleration_episodes','ttc_near_miss_episodes','red_signal_entries','collision_involvements']:
                d[metric+'_per_100km']=d[metric]*100000/d['distance_m'] if d['distance_m']>0 else None
                d[metric+'_per_hour']=d[metric]*3600/d['travel_duration_s'] if d['travel_duration_s']>0 else None
            features.append(d)
        study=[x for x in tr if not x['is_background']]; background=[x for x in tr if x['is_background']]
        runtime=pq.read_table(rd/'runtime_statistics.parquet').to_pandas()
        runstats.append(dict(**done,background_inserted=sum(x['inserted'] for x in background),background_completed=sum(x['completed'] for x in background),
                             background_teleports=sum(x['teleports'] for x in background),background_non_inserted=sum(not x['inserted'] for x in background),background_inserted_not_completed=sum(x['inserted'] and not x['completed'] for x in background),
                             study_teleported_trips=sum(x['teleports']>0 for x in study),background_teleported_trips=sum(x['teleports']>0 for x in background),
                             non_inserted=sum(not x['inserted'] for x in study),incomplete=sum(x['final_status']=='incomplete' for x in study),
                             removed=sum(x['final_status'] in ('removed','collision_related_removal') for x in study),not_completed=sum(not x['completed'] for x in study),
                             collision_records=len(c),lane_changes=sum(x['native_tag']=='change' for x in l),ssm_raw_records=len(s),ssm_type111_records=sum(x['has_type_111'] for x in s),
                             signal_entries=len(tls),red_signal_entries=sum(x['red_signal_entry_observed'] for x in tls),
                             mean_waiting_time_s=float(np.mean([x['waiting_time_s'] for x in study if x['waiting_time_s'] is not None])),
                             max_waiting_time_s=max((x['waiting_time_s'] or 0 for x in study),default=0),
                             mean_departure_delay_s=float(np.mean([x['departure_delay_s'] for x in study if x['departure_delay_s'] is not None])),
                             throughput_arrivals_per_hour=sum(x['completed'] for x in tr)*3600/done['simulation_end_s'],max_sampled_pending_insertions=int(runtime.pending_insertions.max()),
                             max_sampled_queue_vehicles=int(runtime.waiting_vehicles.max()),queue_sampling_interval_s=10.,actual_overlap_violations=overlap,
                             ssm_geometry_warning_count=warning_counts['ssm_geometry'],emergency_braking_warning_count=warning_counts['emergency_braking'],warning_counts_json=json.dumps(warning_counts)))
        rf=[d for d in features if d['run_id']==rid]; n=sum(d['telemetry_rows'] for d in rf)
        mean=sum(d['speed_mean_mps']*d['telemetry_rows'] for d in rf if d['telemetry_rows'])/n if n else None
        runstats[-1].update(study_speed_mean_mps=mean,study_speed_std_mps=math.sqrt(max(0,sum((d['speed_std_mps']**2+d['speed_mean_mps']**2)*d['telemetry_rows'] for d in rf if d['telemetry_rows'])/n-mean**2)) if n else None,
                           study_speed_max_mps=max((d['speed_max_mps'] for d in rf if d['telemetry_rows']),default=None),study_distance_m=sum(d['distance_m'] for d in rf),
                           study_speed_limit_exceedance_s=sum(d['speed_limit_exceedance_s'] for d in rf),
                           study_speed_limit_exceedance_m=sum(d['speed_limit_exceedance_m'] for d in rf),study_short_headway_exposure_s=sum(d['short_headway_exposure_s'] for d in rf),
                           study_hard_braking_episodes=sum(d['hard_braking_episodes'] for d in rf),study_harsh_acceleration_episodes=sum(d['harsh_acceleration_episodes'] for d in rf),
                           study_lane_changes=sum(d['lane_changes'] for d in rf),study_red_signal_entries=sum(d['red_signal_entries'] for d in rf),
                           study_collision_involvements=sum(d['collision_involvements'] for d in rf),study_ttc_near_miss_episodes=sum(d['ttc_near_miss_episodes'] for d in rf))
        runstats[-1].update(simulation_step_s=cfg['simulation_step_s'],telemetry_interval_s=cfg['telemetry_interval_s'],collision_action='teleport',collision_mingap_factor_configured=-1.,collision_mingap_factor_effective=1.,junction_collision_check=True,lane_change_model='SL2015',ballistic_integration=True,lateral_resolution_m=cfg['lateral_resolution_m'])
        inputs={name:p.digest(path) for name,path in {'simulation.sumocfg':rd/'simulation.sumocfg','study_types.rou.xml':rd/'study_types.rou.xml','study_planned.rou.xml':rd/'study_planned.rou.xml','background.rou.xml':rd/'background.rou.xml','network':p.ROOT/'network/london_filtered.net.xml','sampling.yaml':base/'sampling.yaml'}.items()}
        runstats[-1]['input_hashes_json']=json.dumps(inputs,sort_keys=True)
        runstats[-1]['input_bundle_sha256']=hashlib.sha256(json.dumps(inputs,sort_keys=True).encode()).hexdigest()
    encounters=deduplicate(raw_ssm)
    assert sum(e['raw_record_count'] for e in encounters)==len(raw_ssm), 'Deduplication lost raw reports'
    # Confirmation only from the separate detector, matched by pair and overlapping time window.
    for e in encounters:
        e['collision_log_matches']=[c['collision_id'] for c in all_collisions if c['run_id']==e['run_id'] and set([c.get('collider'),c.get('victim')])==set([e['vehicle_a'],e['vehicle_b']]) and e['begin_s']-.2<=c['time']<=e['end_s']+.2]
        e['confirmed_collision']=bool(e['collision_log_matches'])
        for side in ['a','b']:
            ident=p.identity(e['run_id'],e['vehicle_'+side],lookup_by_run[e['run_id']])
            e.update({f'{side}_{k}':v for k,v in ident.items() if k!='run_id'})
    def native_event(rid,vid,t,kind,value=None,other=None,source='SUMO_native'):
        all_events.append(dict(**p.identity(rid,vid,lookup_by_run[rid]),timestamp_s=t,end_s=None,duration_s=None,event_type=kind,value=value,other_vehicle_id=other,source=source))
    params={r['driver_id']:r for r in pq.read_table(ds/'driver_parameters.parquet').to_pylist()}
    for lane in all_lanes:
        if lane['native_tag']!='change': continue
        rid=lane['run_id']; vid=lane['sim_vehicle_id']; t=lane['time']
        native_event(rid,vid,t,'lane_change',lane.get('dir'),source='SUMO_lanechange')
        if lane['unsafe_gap_proxy']: native_event(rid,vid,t,'unsafe_merge_gap_proxy',source='SUMO_lanechange_secure_gap_comparison')
        lat=lane.get('latGap'); desired=params.get(lane['driver_id'],{}).get('minGapLat')
        if lat is not None and desired is not None and lat<desired:
            native_event(rid,vid,t,'reduced_lateral_clearance_proxy',lat,source='SUMO_lanechange_latGap')
    for c in all_collisions:
        for participant,other in [('collider','victim'),('victim','collider')]:
            native_event(c['run_id'],c[participant],c['time'],'sumo_collision_involvement',other=c[other],source='SUMO_collision_output')
    for entry in all_tl:
        if entry['red_signal_entry_observed']:
            native_event(entry['run_id'],entry['sim_vehicle_id'],entry['timestamp_s'],'red_signal_entry_observed',source='TraCI_matched_stop_line_crossing')
    for e in encounters:
        for side,other in [('a','b'),('b','a')]:
            vid=e['vehicle_'+side]
            if e['junction_conflict_proxy']:
                native_event(e['run_id'],vid,e['begin_s'],'junction_ssm_conflict_proxy',e.get('minTTC_value'),e['vehicle_'+other],source='SUMO_SSM_deduplicated')
    for r in runstats:
        r['ssm_deduplicated_encounters']=sum(e['run_id']==r['run_id'] for e in encounters)
        r['statuses_json']=json.dumps(r.pop('statuses')); r['sumo_version']=str(r['sumo_version'])
        r['driving_event_rows_including_native']=sum(e['run_id']==r['run_id'] for e in all_events)
    # Deduplicated SSM attribution includes both participants, with background left distinguishable.
    for d in features:
        ee=[e for e in encounters if e['run_id']==d['run_id'] and d['driver_id'] in (e.get('a_driver_id'),e.get('b_driver_id'))]
        d['ssm_encounter_involvements']=len(ee)
        for metric in ['minTTC','maxDRAC','PET','minPPET','maxMDRAC']:
            vals=np.array([e[metric+'_value'] for e in ee if e[metric+'_value'] is not None],dtype=float)
            d[metric+'_ssm_defined_encounters']=len(vals); d[metric+'_ssm_missing_encounters']=len(ee)-len(vals)
            d[metric+'_ssm_mean']=float(vals.mean()) if len(vals) else None
            for q in [.05,.5,.95]: d[f'{metric}_ssm_p{int(100*q):02d}']=float(np.quantile(vals,q)) if len(vals) else None
    for name,rows,fields in [('trip_summary',trips,p.IDENT+[('final_status',pa.string()),('removal_or_incomplete_reason',pa.string()),('native_vaporized',pa.string()),('teleport_reason_messages',pa.list_(pa.string())),('native_departure_s',pa.float64()),('native_arrival_s',pa.float64())]),('driving_events',all_events,list(p.EV)),
                             ('traffic_light_entries',all_tl,list(p.TL)),('lane_changes',all_lanes,p.IDENT+[('time',pa.float64()),('reason',pa.string())]),
                             ('collisions',all_collisions,[('run_id',pa.string()),('collision_id',pa.string()),('collider',pa.string()),('victim',pa.string()),('time',pa.float64())]),
                             ('ssm_conflicts_raw',raw_ssm,[('run_id',pa.string()),('ego',pa.string()),('foe',pa.string()),('has_type_111',pa.bool_())]),
                             ('ssm_encounters_deduplicated',encounters,[('run_id',pa.string()),('encounter_id',pa.string()),('raw_record_count',pa.int64())]),
                             ('run_statistics',runstats,[('run_id',pa.string())]),('per_driver_run_features',features,[('run_id',pa.string()),('driver_id',pa.string())]),
                             ('simulator_warnings',warnings,[('run_id',pa.string()),('category',pa.string()),('timestamp_s',pa.float64()),('raw_message',pa.string())])]:
        save(ds/f'{name}.parquet',rows,fields)
    csv_table(summaries/'run_statistics.csv',runstats); csv_table(summaries/'per_driver_run_features.csv',features)
    event_counts=collections.Counter((e['run_id'],e['is_background'],e['event_type']) for e in all_events)
    csv_table(summaries/'event_counts.csv',[dict(run_id=rid,is_background=bg,event_type=ev,count=n) for (rid,bg,ev),n in sorted(event_counts.items())])
    expected_events=['harsh_acceleration','hard_braking','simulator_limit_exceedance','short_following_gap','ttc_near_miss','stop','go','lane_change','unsafe_merge_gap_proxy','reduced_lateral_clearance_proxy','junction_ssm_conflict_proxy','red_signal_entry_observed','sumo_collision_involvement']
    csv_table(summaries/'behavior_observation_summary.csv',[dict(run_id=r['run_id'],is_background=bg,event_type=kind,count=event_counts[(r['run_id'],bg,kind)],absence_note='No episodes recorded under declared detector/threshold' if event_counts[(r['run_id'],bg,kind)]==0 else '') for r in runstats for bg in [False,True] for kind in expected_events])
    bgtypes=[]
    if runs:
        tree=ET.parse(base/'runs'/runs[0]['run_id']/'background.rou.xml').getroot()
        for v in tree.findall('vType'):
            r=dict(v.attrib); r['type_id']=r.pop('id')
            bgtypes.append({k:numeric(value) if numeric(value) is not None else value for k,value in r.items()})
        save(ds/'background_vehicle_parameters.parquet',bgtypes,[('type_id',pa.string())])
    ff=pd.DataFrame(features)
    if not ff.empty:
        additive=['planned_trips','inserted_trips','completed_trips','incomplete_trips','non_inserted_trips','removed_trips','not_completed_trips','teleports','teleported_trips','distance_m','travel_duration_s','waiting_time_s','time_loss_s','sampled_exposure_s','telemetry_rows','speed_limit_exceedance_s','speed_limit_exceedance_m','short_headway_exposure_s','stopped_exposure_s','lane_changes','red_signal_entries','collision_involvements','junction_ssm_raw_reports']+[c for c in ff if c.endswith('_episodes')]
        ag=ff.groupby('driver_id')[additive].sum()
        ag['speed_mean_mps']=(ff.speed_mean_mps*ff.telemetry_rows).groupby(ff.driver_id).sum()/ag.telemetry_rows
        ag['speed_std_mps']=np.sqrt(np.maximum(0,((ff.speed_std_mps**2+ff.speed_mean_mps**2)*ff.telemetry_rows).groupby(ff.driver_id).sum()/ag.telemetry_rows-ag.speed_mean_mps**2))
        ag['speed_max_mps']=ff.groupby('driver_id').speed_max_mps.max()
        ag['observed_acceleration_min_mps2']=ff.groupby('driver_id').observed_acceleration_min_mps2.min()
        ag['observed_acceleration_max_mps2']=ff.groupby('driver_id').observed_acceleration_max_mps2.max()
        for metric in ['headway','ttc']:
            total=ff.groupby('driver_id')[metric+'_defined_samples'].sum()
            mean=(ff[metric+'_mean']*ff[metric+'_defined_samples']).groupby(ff.driver_id).sum()/total.replace(0,np.nan)
            ag[metric+'_defined_samples']=total; ag[metric+'_missing_fraction']=1-total/ag.telemetry_rows
            ag[metric+'_mean']=mean
            ag[metric+'_std']=np.sqrt(np.maximum(0,((ff[metric+'_std']**2+ff[metric+'_mean']**2)*ff[metric+'_defined_samples']).groupby(ff.driver_id).sum()/total.replace(0,np.nan)-mean**2))
            ag[metric+'_min']=ff.groupby('driver_id')[metric+'_min'].min(); ag[metric+'_max']=ff.groupby('driver_id')[metric+'_max'].max()
        for did in ag.index:
            ee=[e for e in encounters if did in (e.get('a_driver_id'),e.get('b_driver_id'))]
            ag.loc[did,'ssm_encounter_involvements']=len(ee)
            for metric in ['minTTC','maxDRAC','PET','minPPET','maxMDRAC']:
                vals=np.array([e[metric+'_value'] for e in ee if e[metric+'_value'] is not None],dtype=float)
                ag.loc[did,metric+'_ssm_defined_encounters']=len(vals); ag.loc[did,metric+'_ssm_missing_encounters']=len(ee)-len(vals)
                ag.loc[did,metric+'_ssm_mean']=float(vals.mean()) if len(vals) else np.nan
                for q in [.05,.5,.95]: ag.loc[did,f'{metric}_ssm_p{int(100*q):02d}']=float(np.quantile(vals,q)) if len(vals) else np.nan
        for metric in ['lane_changes','hard_braking_episodes','harsh_acceleration_episodes','ttc_near_miss_episodes','red_signal_entries','collision_involvements']:
            ag[metric+'_per_100km']=ag[metric]*100000/ag.distance_m.replace(0,np.nan)
            ag[metric+'_per_hour']=ag[metric]*3600/ag.travel_duration_s.replace(0,np.nan)
        ag=ag.reset_index(); ag.to_csv(summaries/'per_driver_features.csv',index=False); pq.write_table(pa.Table.from_pandas(ag,preserve_index=False),ds/'per_driver_features.parquet',compression='zstd')
        variation=[]
        for metric in ['speed_mean_mps','speed_std_mps','speed_limit_exceedance_s','headway_mean','ttc_min','hard_braking_episodes_per_100km','lane_changes_per_100km','short_headway_exposure_s','red_signal_entries','collision_involvements']:
            vals=ag[metric].dropna()
            variation.append(dict(metric=metric,drivers_with_defined_metric=len(vals),mean=float(vals.mean()) if len(vals) else None,std_between_drivers=float(vals.std()) if len(vals)>1 else None,
                                  minimum=float(vals.min()) if len(vals) else None,p05=float(vals.quantile(.05)) if len(vals) else None,median=float(vals.median()) if len(vals) else None,
                                  p95=float(vals.quantile(.95)) if len(vals) else None,maximum=float(vals.max()) if len(vals) else None))
        csv_table(summaries/'between_driver_variation.csv',variation)
        # Metadata joins preserve all 1,080 planned driver/run rows.
        m=pq.read_table(ds/'driver_run_metadata.parquet').to_pandas()[['run_id','driver_id','density','seed','parameter_version']]
        m=m.merge(ff.drop(columns=['density','seed']),how='left',on=['run_id','driver_id'])
        pq.write_table(pa.Table.from_pandas(m,preserve_index=False),ds/'driver_run_metadata.parquet',compression='zstd')
    metric_stats=[]
    for metric in ['minTTC','maxDRAC','PET','minPPET','maxMDRAC']:
        vals=np.array([e[metric+'_value'] for e in encounters if e.get(metric+'_value') is not None],dtype=float)
        raw_vals=[r.get(metric+'_value_raw') for r in raw_ssm]
        nonfinite=sum(v not in (None,'NA','None','nan') and numeric(v) is None for v in raw_vals)
        metric_stats.append(dict(metric=metric,total_encounters=len(encounters),defined=len(vals),missing=len(encounters)-len(vals),negative_values=int((vals<0).sum()),raw_nonfinite_values=nonfinite,
                                 mean=float(vals.mean()) if len(vals) else None,minimum=float(vals.min()) if len(vals) else None,
                                 p05=float(np.quantile(vals,.05)) if len(vals) else None,median=float(np.median(vals)) if len(vals) else None,p95=float(np.quantile(vals,.95)) if len(vals) else None))
    csv_table(summaries/'ssm_measure_distributions.csv',metric_stats)
    if runstats:
        rdf=pd.DataFrame(runstats)
        density=rdf.groupby('density',sort=False).agg(successful_runs=('run_id','count'),mean_concurrent_vehicles=('mean_concurrent','mean'),maximum_concurrent_vehicles=('max_concurrent','max'),
                    mean_study_speed_mps=('study_speed_mean_mps','mean'),mean_study_waiting_s=('mean_waiting_time_s','mean'),collision_records=('collision_records','sum'),study_teleports=('teleports','sum'),background_teleports=('background_teleports','sum'),red_signal_entries=('red_signal_entries','sum')).reset_index()
        density.to_csv(summaries/'density_summary.csv',index=False)
    overall=dict(successful_main_runs=len(runstats),expected_main_runs=12,planned_study_trips=len(plan),observed_study_completed=sum(r['completed'] for r in runstats),
                 total_telemetry_rows=sum(r['telemetry_rows'] for r in runstats),driving_event_records=len(all_events),lane_change_records=sum(x['native_tag']=='change' for x in all_lanes),
                 collision_records=len(all_collisions),ssm_raw_records=len(raw_ssm),ssm_deduplicated_encounters=len(encounters),
                 ssm_type111_records=sum(x['has_type_111'] for x in raw_ssm),signal_entries=len(all_tl),red_signal_entries=sum(x['red_signal_entry_observed'] for x in all_tl),
                 type111_without_collision_log=sum(e['has_type_111'] and not e['confirmed_collision'] for e in encounters),complete=len(runstats)==12)
    overall.update(observed_study_inserted=sum(r['inserted'] for r in runstats),study_teleports=sum(r['teleports'] for r in runstats),
                   background_teleports=sum(r['background_teleports'] for r in runstats),observed_background_completed=sum(r['background_completed'] for r in runstats),
                   study_red_signal_entries=sum(r['study_red_signal_entries'] for r in runstats),study_collision_involvements=sum(r['study_collision_involvements'] for r in runstats),
                   geometry_warning_count=sum(r['ssm_geometry_warning_count'] for r in runstats))
    overall['study_non_inserted']=sum(r['non_inserted'] for r in runstats)
    overall['study_inserted_not_completed']=sum(r['inserted']-r['completed'] for r in runstats)
    overall['study_planned_outcomes_accounted']=sum(r['study_manifest_count'] for r in runstats)
    overall['study_teleported_trips']=sum(r['study_teleported_trips'] for r in runstats)
    overall['study_removed_trips']=sum(r['removed'] for r in runstats)
    p.js(base/'STATUS.json',overall); p.js(base/'quality_report.json',dict(overall=overall,runs=qualities))
    p.js(base/'ssm_quality_flags.json',dict(metrics=metric_stats,interpretation='Negative predicted clearance/MDRAC and very large surrogates are preserved native outputs, not clipped or converted into risk labels. Review geometry warnings and model validity before severity interpretation.',
        encounters_with_ttc_over_one_hour=sum(e.get('minTTC_value') is not None and e['minTTC_value']>3600 for e in encounters)))
    csv_table(summaries/'overall_statistics.csv',[overall])
    write_docs(base,overall,runstats,metric_stats)
    print(json.dumps(overall),flush=True)
    return overall

def write_docs(base,overall,runs,ssm_stats):
    ds=base/'datasets'
    lines=['# SE4457 synthetic observational traffic experiment','','```json\n'+json.dumps(overall,indent=2)+'\n```','',
           'This dataset describes synthetic simulator observations, not real drivers, claims, actuarial outcomes, or validated human accident frequencies. No personas, scores, insurance premiums, or classification targets were generated.',
           '', '## Design and reproducibility','',
           '90 fixed study identities, ten independent route episodes per run, twelve runs: low/moderate/high background demand with four seeds each. Each driver has the same ten explicit routes and scheduled departure times in all runs. Spatial continuity between successive trips is not assumed. The runner releases each trip after its schedule and after the previous trip has ended; resulting delays are observations.',
           'The SUMO route input contains study vTypes and separate background routes. Study routes and departures are in trip_manifest.parquet and study_planned.rou.xml. Running SUMO alone does not release study vehicles: use the Python runner. It uses TraCI route.add and vehicle.add with standard insertion checks, never speedMode/laneChangeMode overrides.',
           'Simulation uses SUMO 1.28.0, Krauss, SL2015, ballistic integration, 0.1-second steps, 0.5 m sublanes. Telemetry is sampled at 0.5 seconds; events and stop-line crossing logic run every 0.1 seconds. Background demand is 450/1350/2700 planned journeys per run; study demand is always 900. Background receives 12 fixed, separately sampled vTypes. All background vehicles carry SSM devices and have telemetry.',
           '', 'From the repository root on Windows:', '', '```powershell',
           'python -m pip install -r requirements-lock.txt', 'python -m pytest -q tests',
           'python scripts/research_pipeline.py prepare','python scripts/research_pipeline.py smoke',
           'python scripts/research_pipeline.py run', 'python scripts/research_pipeline.py summarize','python scripts/research_pipeline.py package','```',
           'Set SUMO_HOME to the installed SUMO 1.28.0 directory. Completed runs are skipped. Interrupted run attempts are archived and restarted from simulation time zero, preserving previous files. This is a run-level resume, not a simulator-state checkpoint. To reproduce from scratch, use a fresh copy and omit experiments/unlabeled_v1 except baseline/.',
           '', '## Measured run results','',markdown_table(pd.DataFrame(runs)[[c for c in ['run_id','density','seed','inserted','completed','teleports','telemetry_rows','lane_changes','collision_records','ssm_raw_records','red_signal_entries','max_concurrent'] if c in pd.DataFrame(runs)]]),
           '', '## Sampling methodology','',
           'sampling.yaml is the complete distribution specification. The generator uses Python random.Random with independent population, background-parameter, schedule, and per-run seeds. Bounded beta distributions concentrate mass away from extremes. tau has a 94% central / 6% short-gap mixture; red-entry willingness and foe-ignore settings have small point-mass/continuous mixtures. Mixtures are unnamed parameter priors and do not preassign events. The realized 90-driver cohort may differ from nominal mixture weights.',
           'All independent draws follow YAML insertion order. emergencyDecel is max(sample, decel); apparentDecel is decel times a sampled ratio; lcPushyGap is the equivalent encoding minGapLat*(1-lcPushy). lcPushyGap is not an additional independent mechanism. speedDev=0 retains each sampled speedFactor. The installed parser rejected negative lcImpatience; the accepted configuration bounds it at [0,0.6]. The rejected smoke is preserved separately.',
           'Driver characteristics: speedFactor, tau, sigma, minGap, startupDelay, actionStepLength, lane-changing/sublane/junction settings and impatience. Vehicle characteristics: accel, decel, emergencyDecel, apparentDecel, dimensions, mass, maxSpeed, maxSpeedLat, passenger category and HBEFA3/PC_G_EU6 emissions class. Capability inputs are not observed acceleration. These priors and their independence assumptions are exploratory, not calibrated.',
           '', '## Events, formulas and interpretation','',
           'driving_events records episodes. Consecutive true simulation steps are coalesced, with onset, end and duration; value is the onset value, not the peak. Harsh acceleration >=2.5 m/s²; hard braking <=-4 m/s²; simulator limit exceedance speed > lane limit+0.5 m/s; short gap physical bumper gap/speed <1 s when speed>2 m/s; following TTC gap/(ego speed-leader speed) <1.5 s when closing speed>0. Stops/go events cross 0.1 m/s. Departure, arrival and teleport events are lifecycle observations. These definitions are descriptive thresholds, not insurance risk rules.',
           'TraCI leader gap excludes ego minGap; physical gap is returned gap+ego minGap. Follower physical gap adds follower minGap. Headway is the instantaneous net bumper gap divided by ego speed, not configured tau and not a detector passage interval. TTC is undefined when no observed leader or no positive closing speed. Jerk is the finite difference of observed acceleration at consecutive telemetry samples; the first sample is null. A leader not present in the current subscription set has null derived metrics.',
           'A traffic-light entry requires an external-lane to internal-lane transition matching the network connection via lane and controlling TLS link. Red-signal entry requires red on both sides of that 0.1-second interval. Boundary phase changes are not counted as confirmed red. Right-turn direction is retained. verified_legal_violation is always null because legal permissions and turn-on-red semantics were not independently verified. Approaching red is not an entry. Entries without internal-lane matching are not inferred.',
           'Native lane-change rows retain raw attributes and surrounding actual/secure gaps. unsafe_gap_proxy compares actual target gaps to SUMO secure gaps; it is not a verified unsafe human merge. Lateral position telemetry supports separate analysis of lane encroachment; no geometrically validated side-encroachment event label is asserted. SSM junction_conflict_proxy uses raw extrema types 6,7,10–17,19; it is not a proven failure to yield.',
           'SSM raw is one row per native conflict, with all measure attributes and raw XML, preserving both ego/foe reports and code 111. Native TTC/DRAC/PET/PPET/MDRAC are taken directly from SUMO; time/position/type correspond to the measure extreme. They are not recomputed with the simple following TTC formula for junctions. Deduplication groups an unordered participant pair in a run into connected overlapping time intervals with 0.2 s tolerance. Raw IDs map back to every constituent report. Minima are combined by min, maxima by max; separate encounters with a gap >0.2 s stay separate. Transitive overlap can merge a long sequence of interactions.',
           'Collision-log matches require the same unordered pair and a detector time within encounter interval ±0.2 s. Code 111 alone never confirms a collision. Collision action is teleport. SUMO default collision.mingap-factor=1 detects longitudinal gap violations and junction overlap; collision-output is not proof of physical contact or damage. Teleport counts include congestion and collision teleports; raw warning logs distinguish causes, while lifecycle records preserve timing. Collision-related disappearance classification is conservative and does not claim a simulator reason code when unavailable.',
           'SSM default thresholds are TTC 3 s, DRAC 3 m/s², PET 2 s, PPET 2 s, MDRAC 3.4 m/s²; range 50 m, extra tracking 5 s, MDRAC reaction time 1 s. Only threshold-qualified native conflicts are logged, not every interaction. See official SUMO documentation: https://sumo.dlr.de/docs/Simulation/Output/SSM_Device.html and https://sumo.dlr.de/docs/Simulation/Safety.html .',
           '', '## Measurement and limitations','',
           'All trajectory floats retain native double precision; SUMO XML is configured to eight decimal places. Parquet uses ZSTD. Null means unavailable/undefined/not applicable; zero is an observed or configured zero. Heading is SUMO degrees clockwise from north, positions are projected network metres (see network location metadata), speed m/s, distance/gap m, acceleration m/s², jerk m/s³, time s, mass kg. Native XML strings remain in raw_attributes_json/raw_xml.',
           'Summary exposure uses sampled observations ×0.5 s; speed-excess distance is a rectangular integral speed×0.5 s. This can miss short episodes or approximate trip endpoints. Events use 0.1-second logic. Rates per 100 km use TripInfo routeLength, including any teleport progress; rates per hour use TripInfo duration. Incomplete-trip denominators need care. Telemetry speed statistics are time-sampled, not trip-weighted. Per-run driver quantiles are exact for sampled values; aggregate-driver means/standard deviations are weighted by telemetry rows. Headway/TTC missingness is conditional on interacting traffic.',
           'Network OSM provenance and lane speed limits have not been legally audited. Exceedances refer to simulator metadata, not real-world legal speeding. Vehicle mass and emissions class are metadata for the default Krauss dynamics, not a calibrated fleet model. Lane count includes the current edge; junction proximity is distance to the end of the current lane, not Euclidean distance to a junction centre.',
           'Only passenger motor vehicles are modeled. No pedestrians/cyclists, measured demographic attributes, human perception errors, weather or real claims are supplied. No demographic table was requested. Density is a demand treatment; observed concurrency/queues measure realized density. Seeds repeat across density conditions as common random numbers, but differing vehicle populations consume random streams differently. Statistical train/test splits in later work must group by persistent driver ID and should test unseen routes and seeds.',
           'The old LC2013 diagnostic is retained under baseline/: 90 completed trips, 82 lane changes, 108 raw SSM records, zero collision-output records; D083_T01/D087_T01 have reciprocal code-111 reports. Baseline statistics.xml is empty and is not evidence of full-run completion. New experiment data are separate.',
           '', '## SSM distributions and missingness','',markdown_table(pd.DataFrame(ssm_stats)),
           '', 'See STATUS.json, quality_report.json, per-run validation.json, summaries/*.csv, and handoff_manifest.csv. Full telemetry is retained in datasets/vehicle_telemetry/run_id=.../. A ZIP is complete only when STATUS.json states complete=true.']
    (base/'README.md').write_text('\n\n'.join(lines),encoding='utf-8')
    additions=[]
    cohort=pq.read_table(ds/'driver_parameters.parquet').to_pandas()
    templates=pq.read_table(ds/'trip_manifest.parquet').to_pandas()
    distinct=templates[templates.run_id=='run_001'].groupby('driver_id').source_route_template_id.nunique()
    additions+=['## Realised cohort and reference joins',
        f"Of 90 study drivers, {int((cohort.tau<=.65).sum())} have tau <=0.65 s, {int(cohort.tau_below_action_step.sum())} have tau below their action interval, {int((cohort.jmDriveAfterRedTime>0).sum())} have a positive red-entry willingness setting, and {int((cohort.jmIgnoreFoeProb>0).sum())} have nonzero foe-ignore probability. These are inputs, not observed infractions. Ten route slots are stable across all runs; distinct template counts per driver are {distinct.value_counts().to_dict()}. Repeated templates are retained and not counted as independent route diversity.",
        'Telemetry static inputs are normalized into driver_parameters (join on driver_id). Background types are in background_vehicle_parameters, joined through background_traffic_manifest type_id. Each driver retains one vehicle capability set, so driver/vehicle effects are confounded in this design. run_id is stored losslessly in the Hive partition folder rather than duplicated in canonical telemetry files. Use pyarrow.dataset.dataset(path, format="parquet", partitioning="hive") or pandas.read_parquet(path) for all partitions. Per-run runtime queue snapshots occur every 10 seconds; sampled queue maxima may miss between-snapshot peaks. Main mean/max concurrency is measured every simulation step.',
        'The first main attempt completed SUMO but failed during summary writing on a duplicate teleport key. Its full raw/telemetry outputs are preserved under run_001/attempts and excluded from main tables. The corrected same-seed replay matched all 1,350 native TripInfo records exactly (see replay_equivalence.json). The subscription optimisation also matched every smoke telemetry value. Parameter read-back passed 1,350 installed-SUMO checks. See diagnostics/parameter_readback.json and test_results.xml.',
        'The isolated following-brake probe in diagnostics/following_brake_probe is excluded from main counts: tau=0.35 s with a 1 s action interval produced four collision-log records for each of two seeds, while tau=1.5 s produced none. Planned leader stops, ordinary insertion checks, and standard safety logic were used. This illustrates model sensitivity; it does not estimate human crash probability.',
        'Unavailable or unverified features: legal signal-violation classification, calibrated human blind spots, real-world speed-limit verification, a certified failure-to-yield label, continuous geometric lateral-encroachment duration, claims damage/severity, and demographic effects. Native lateral-gap and junction-conflict proxies remain available separately. Network geometry warnings identify incomplete SSM coverage; raw simulator warnings are preserved and counted.']
    additions+=['## Installed-model precision and collision defaults',
        'A separate 180-insertion attribute probe (two sequential short episodes per driver) confirmed stable effective speed factors with speedDev=0. SUMO precision.random defaults to four decimal places, so per-vehicle speedFactor differs from the full-precision assigned vType input by at most approximately 0.00005. driver_effective_parameters.parquet retains both values; no main input was revised. Telemetry itself retains full double precision.',
        'The saved installed defaults are in diagnostics/sumo_defaults.xml. collision.mingap-factor is configured by its default -1, which delegates to the car-following model; the Krauss inherited effective value is 1. Longitudinal records can therefore be minGap infringements, while junction checks use geometric overlap. This is confirmed in [SUMO 1.28.0 MSCFModel source](https://github.com/eclipse-sumo/sumo/blob/v1_28_0/src/microsim/cfmodels/MSCFModel.cpp) and [MSLane collision checking](https://github.com/eclipse-sumo/sumo/blob/v1_28_0/src/microsim/MSLane.cpp). A collision-output record must not be interpreted as a human injury/damage outcome.',
        'The ZIP mirrors the runnable repository layout. Active datasets are under experiments/unlabeled_v1/datasets/. Root README/report/dictionary files are convenience copies. Legacy root data and baseline/ copies are explicitly separate from main observations. Polygon files are retained for visualisation; they do not affect headless vehicle dynamics.']
    additions+=['## Native surrogate formulas and validity',
        'For physical following gap g and positive closing speed Δv: TTC=g/Δv, DRAC=Δv²/(2g), and MDRAC=Δv/[2(TTC−PRT)], with native PRT=1 s. PET is follower conflict-area entry time minus leader exit time; PPET uses projected entry/exit timing. Junction TTC/DRAC use native conflict-area geometry and case handling rather than the simple following formula. These are descriptions of native measures, not recomputations of exported extrema. See the [SUMO SSM specification](https://sumo.dlr.de/docs/Simulation/Output/SSM_Device.html).',
        'Native negative PET/PPET/MDRAC and very large extrema are retained, including geometrically questionable projections. They must not automatically become risk severity. ssm_quality_flags.json reports negative/nonfinite values and long-TTC tails. MDRAC and merging/crossing geometry have documented model limitations. Numeric NA/nonfinite outputs map to null while *_value_raw and raw_xml preserve the original strings. The cohort distributions are conditional on native conflict logging thresholds and interaction opportunity, not population crash-rate estimates.']
    additions+=['## Read telemetry in bounded batches',
        'The complete telemetry dataset is large. Select columns and runs when exploring it; the ZIP never substitutes a sample for the full observations.',
        '```python\nimport pyarrow.dataset as ds\ntraffic = ds.dataset("experiments/unlabeled_v1/datasets/vehicle_telemetry",\n                     format="parquet", partitioning="hive")\nscanner = traffic.scanner(columns=["run_id", "driver_id", "trip_id", "timestamp_s", "speed_mps"],\n                          filter=ds.field("run_id") == "run_001", batch_size=65536)\nfor batch in scanner.to_batches():\n    frame = batch.to_pandas()\n    # Analyse or aggregate this batch; avoid retaining all batches in memory.\n```']
    recovery=base/'diagnostics/execution_recovery.json'
    if recovery.exists():
        additions+=['## Execution recovery',
          'High-density runs 010 and 012 encountered native SUMO bad-allocation failures under parallel execution. Their attempts, including partial telemetry and potentially truncated native XML, are preserved under their attempts/ folders and excluded from main tables. The identical frozen seeds/inputs were replayed sequentially. diagnostics/recovered_prefix_validation.json compares every completed TripInfo record from those failed prefixes with the successful replay. The concurrency helper now defaults to one worker to bound native SSM memory. Only successful, validated per-run outputs enter canonical datasets.']
    for file,title in [('density_summary.csv','Observed density treatments'),('between_driver_variation.csv','Observed variation across the 90 persistent study identities'),('behavior_observation_summary.csv','Behavior detector coverage')]:
        path=base/'summaries'/file
        if path.exists():
            frame=pd.read_csv(path)
            if file=='behavior_observation_summary.csv':
                frame=frame.groupby(['is_background','event_type'],dropna=False)['count'].sum().reset_index()
            additions+=['## '+title,markdown_table(frame.round(5))]
    (base/'README.md').write_text((base/'README.md').read_text(encoding='utf-8')+'\n\n'+'\n\n'.join(additions),encoding='utf-8')
    shutil.copy2(base/'README.md',base/'research_report.md')
    (base/'known_limitations.md').write_text('''# Known limitations

- Synthetic passenger-vehicle observations are not real actuarial, injury, damage, or claims outcomes. Priors are exploratory and uncalibrated; no human risk classes or premiums are assigned.
- Driver and vehicle capability are paired permanently. Route and density exposure also affect observed behaviour; these effects must not be treated as innate human personality.
- The fixed 7,800-second horizon censors trips. Pending insertions, driver-availability gates, and unfinished journeys are retained. A completed experiment run does not mean all 900 study trips arrived. Actual departures can be delayed relative to the controlled schedule.
- SSM geometry warnings imply incomplete conflict coverage. Code 111 is retained but is not interchangeable with collision-output. The default Krauss collision threshold can detect minGap infringement rather than physical contact; no damage severity is modeled.
- Red-signal entries are matched stop-line crossings bracketed at 0.1 seconds. Legal turn permissions remain unverified; verified_legal_violation is null. Very short internal lanes and phase-boundary crossings may be missed.
- OSM/network speed limits are not legally audited. Reported speeding is only an exceedance of simulator lane metadata.
- Telemetry is sampled every 0.5 seconds; event logic runs every 0.1 seconds. Single-step acceleration/braking threshold excursions count as episodes without a minimum duration filter. Thresholds do not define human unsafe behaviour. Queue maxima are from 10-second snapshots.
- Exposure integrals approximate endpoints. TripInfo distance can include teleport progress. Compare rates with censoring and density in view. Headway/TTC nulls depend on interaction opportunities; configured tau is not observed headway.
- Ten independent route slots per driver are retained across runs, not a spatially continuous itinerary. Ten drivers repeat a template once. There are 90 independent study identities, not 10,800 independent people.
- SUMO rounds per-vehicle speed factors to four decimal places, despite full-precision vType inputs; effective values were audited separately. No pedestrians, cyclists, weather, validated blind spots, demographics, or verified failure-to-yield labels are present.
- Calibration, smoke tests, and engineering attempts are preserved in separate folders and excluded from main observational totals. Group later evaluation splits by driver and assess unseen seeds/routes. See the full report and quality JSON for measured counts and warnings.
''',encoding='utf-8')
    dictionary=[]
    for path in sorted(ds.rglob('*.parquet')):
        for f in pq.ParquetFile(path).schema_arrow:
            unit,definition=define(f.name)
            dictionary.append(dict(table=path.relative_to(ds).as_posix(),column=f.name,type=str(f.type),unit=unit,definition=definition,null_convention='unavailable, undefined, or not applicable'))
    csv_table(base/'data_dictionary.csv',dictionary)

DEFINITIONS={'driver_id':'Persistent study identity D000–D089; null for background', 'trip_id':'Episode vehicle ID; globally unique together with run_id',
             'run_id':'Experiment run ID; separate from persistent driver attributes','is_background':'True for non-study demand',
             'accel':'Static vehicle acceleration capability; NOT observed acceleration', 'decel':'Static comfortable braking capability; NOT observed deceleration',
             'acceleration_mps2':'Observed TraCI acceleration','tau':'Static desired following time parameter; NOT measured headway',
             'observed_headway_s':'Physical net bumper gap / ego speed, only for speed>2 m/s',
             'ttc_s':'Observed following gap / positive closing speed; null otherwise',
             'verified_legal_violation':'Always null: legal permissions not independently verified',
             'confirmed_collision':'Matched separate SUMO collision log; does not certify damage or physical contact'}

def export_telemetry(base):
    # Canonical partitioned telemetry, retained in full. Copies preserve run checkpoint files.
    for done in sorted((base/'runs').glob('*/complete.json')):
        rid=done.parent.name; target=base/f'datasets/vehicle_telemetry/run_id={rid}/part-00000.parquet'
        target.parent.mkdir(parents=True,exist_ok=True)
        original=pq.ParquetFile(done.parent/'telemetry.parquet')
        def preserve_export_attempt(path):
            archive=base/'diagnostics/telemetry_export_attempts'/rid/f'{time.time_ns()}.incomplete'
            archive.parent.mkdir(parents=True,exist_ok=True)
            assert path.resolve().is_relative_to(base.resolve()) and archive.resolve().is_relative_to(base.resolve())
            shutil.move(str(path),str(archive))
        if target.exists():
            try:
                existing=pq.ParquetFile(target)
                assert existing.metadata.num_rows==original.metadata.num_rows
                assert {'previous_sample_speed_mps','trip_elapsed_s','is_stopped'}<=set(existing.schema_arrow.names)
            except Exception: preserve_export_attempt(target)
        if not target.exists():
            pending=target.with_name('.part-00000.pending')
            if pending.exists(): preserve_export_attempt(pending)
            # run_id is encoded losslessly in the Hive partition path. Avoid a duplicate
            # in-file field that conflicts with pyarrow.read_table Hive inference.
            schema=pa.schema([f for f in original.schema_arrow if f.name!='run_id']+[pa.field('previous_sample_speed_mps',pa.float64()),pa.field('trip_elapsed_s',pa.float64()),pa.field('is_stopped',pa.bool_())])
            departures={r['sim_vehicle_id']:r.get('depart_actual_s') for r in pq.read_table(done.parent/'trip_summary.parquet').to_pylist()}
            last_speed={}
            with pq.ParquetWriter(pending,schema,compression='zstd') as writer:
                for batch in original.iter_batches():
                    source=pa.Table.from_batches([batch]).drop(['run_id'])
                    tiny=source.select(['sim_vehicle_id','speed_mps','timestamp_s']).to_pandas()
                    previous=tiny.groupby('sim_vehicle_id',sort=False).speed_mps.shift()
                    first=~tiny.sim_vehicle_id.duplicated()
                    previous.loc[first]=tiny.loc[first,'sim_vehicle_id'].map(last_speed)
                    last_speed.update(tiny.groupby('sim_vehicle_id',sort=False).speed_mps.last().to_dict())
                    elapsed=tiny.timestamp_s-tiny.sim_vehicle_id.map(departures)
                    source=source.append_column('previous_sample_speed_mps',pa.array(previous,type=pa.float64(),from_pandas=True))
                    source=source.append_column('trip_elapsed_s',pa.array(elapsed,type=pa.float64(),from_pandas=True))
                    source=source.append_column('is_stopped',pa.array(tiny.speed_mps<.1,type=pa.bool_()))
                    writer.write_table(source)
            pending.replace(target)
            # Full ordinary-reader check also validates Hive run_id inference.
            read=pq.read_table(target,columns=['run_id','trip_id','previous_sample_speed_mps','trip_elapsed_s','is_stopped'])
            assert read.num_rows==original.metadata.num_rows
            assert set(read['run_id'].to_pylist())=={rid}
            print('Exported telemetry',rid,read.num_rows,flush=True)

def completed_tripinfo_prefix(path):
    rows={}; truncated=False
    try:
        for _,e in ET.iterparse(path,events=['end']):
            if e.tag=='tripinfo' and float(e.get('arrival','-1'))>=0: rows[e.get('id')]=dict(e.attrib)
    except ET.ParseError: truncated=True
    return rows,truncated

def validate_recovered_prefixes(base):
    records=[]
    for rid in ['run_010','run_012']:
        rd=base/'runs'/rid
        if not (rd/'complete.json').exists(): continue
        current={e.get('id'):e.attrib for e in ET.parse(rd/'raw/tripinfo.xml').getroot().findall('tripinfo')}
        for attempt in sorted((rd/'attempts').glob('*')):
            path=attempt/'raw/tripinfo.xml'
            if not path.exists(): continue
            earlier,truncated=completed_tripinfo_prefix(path)
            identical=all(current.get(vid)==row for vid,row in earlier.items())
            assert identical,f'Recovered {rid} did not match preserved completed-trip prefix'
            records.append(dict(run_id=rid,attempt=str(attempt.relative_to(base)),completed_prefix_trips=len(earlier),all_prefix_tripinfo_identical=identical,failed_xml_truncated=truncated))
    p.js(base/'diagnostics/recovered_prefix_validation.json',dict(comparisons=records,no_frozen_input_changes=True))
    return records

def package(base):
    overall=summarize(base)
    assert overall['complete'], 'Full handoff requires all 12 main runs; see STATUS.json and resume unfinished runs'
    validate_recovered_prefixes(base)
    export_telemetry(base)
    from validate_handoff import validate
    validate(base)
    # Refresh dictionary including all partitions.
    write_docs(base,overall,pq.read_table(base/'datasets/run_statistics.parquet').to_pylist(),pd.read_csv(base/'summaries/ssm_measure_distributions.csv').to_dict('records'))
    versions=dict(python=sys.version,platform=platform.platform(),sumo=subprocess.run([str(p.SUMO),'--version'],capture_output=True,text=True).stdout)
    p.js(base/'versions.json',versions)
    lock=subprocess.run([sys.executable,'-m','pip','freeze'],capture_output=True,text=True,check=True).stdout
    (base/'requirements-lock.txt').write_text(lock,encoding='utf-8')
    p.js(base/'packaging_plan.json',dict(main_runs_expected=12,main_runs_completed=overall['successful_main_runs'],
          main_telemetry_rows=overall['total_telemetry_rows'],full_telemetry_included=True,
          partition_key='run_id stored in Hive folder path; other columns in each Parquet file',
          manifest_row_count_convention='Data row count for Parquet/CSV; null for non-tabular files. Manifest omits itself to avoid circular hash.',
          zip_validation='Final CRC, SHA-256, and extracted full Parquet read results are outside ZIP in deliverables/STATUS.json and experiments/unlabeled_v1/zip_validation.json.'))
    files={}
    for path in base.rglob('*'):
        if not path.is_file() or '__pycache__' in path.parts: continue
        rel=path.relative_to(base)
        if rel.parts[0] in {'extracted_validation'} or path.name in {'handoff_manifest.csv','zip_validation.json'}: continue
        if path.name=='telemetry.parquet' and rel.parts[0]=='runs' and len(rel.parts)==3: continue # full canonical partition included
        files['experiments/unlabeled_v1/'+rel.as_posix()]=path
    for folder in ['scripts','tests','network','routes','config','data/manifest','data/raw']:
        for path in (p.ROOT/folder).rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts: files[path.relative_to(p.ROOT).as_posix()]=path
    for name in ['requirements.txt','pytest.ini','run_001.sumocfg']:
        files[name]=p.ROOT/name
    # Root documentation aliases keep the package approachable while preserving
    # the real workspace paths needed by source code and relative SUMO inputs.
    for name in ['README.md','research_report.md','known_limitations.md','data_dictionary.csv','requirements-lock.txt','STATUS.json']:
        files[name]=base/name
    manifest=[]
    for arc,path in sorted(files.items()):
        count=pq.ParquetFile(path).metadata.num_rows if path.suffix=='.parquet' else None
        if path.suffix=='.csv':
            with path.open(encoding='utf-8-sig',newline='') as f: count=max(0,sum(1 for row in csv.reader(f))-1)
        purpose=artifact_purpose(arc)
        manifest.append(dict(path=arc,purpose=purpose,row_count=count,size_bytes=path.stat().st_size,sha256=p.digest(path)))
    csv_table(base/'handoff_manifest.csv',manifest); files['handoff_manifest.csv']=base/'handoff_manifest.csv'
    dest=p.ROOT/'deliverables/SE4457_PhD_handoff.zip'
    if dest.exists():
        archived=dest.with_name(f'SE4457_PhD_handoff_previous_{time.time_ns()}.zip'); shutil.move(dest,archived)
    with zipfile.ZipFile(dest,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6,allowZip64=True) as z:
        for arc,path in sorted(files.items()): z.write(path,arc)
    extracted=base/'extracted_validation'; extracted.mkdir(exist_ok=True)
    parquet_count=0; parquet_rows=0
    with zipfile.ZipFile(dest) as z:
        assert z.testzip() is None
        # Preserve a runnable extracted workspace and fully read every Parquet in bounded batches.
        z.extractall(extracted)
        expected={r['path']:r for r in manifest}
        for name in z.namelist():
            if name=='handoff_manifest.csv': continue
            path=extracted/name
            if name.endswith('.parquet'):
                pf=pq.ParquetFile(path)
                file_rows=0
                for batch in pf.iter_batches(): parquet_rows+=batch.num_rows; file_rows+=batch.num_rows
                assert file_rows==expected[name]['row_count'],name
                parquet_count+=1
            assert p.digest(path)==expected[name]['sha256'],name
        # All relative SUMO input references must resolve after extraction.
        config_references=0
        for config in (extracted/'experiments/unlabeled_v1/runs').glob('*/simulation.sumocfg'):
            tree=ET.parse(config).getroot()
            for key in ['net-file','route-files']:
                for value in tree.find('input/'+key).get('value').split(','):
                    assert (config.parent/value).is_file(),str(config)+': '+value
                    config_references+=1
    check=dict(zip_path=str(dest),zip_sha256=p.digest(dest),zip_size_bytes=dest.stat().st_size,expected_extracted_bytes=sum(r['size_bytes'] for r in manifest)+(base/'handoff_manifest.csv').stat().st_size,
               crc_passed=True,all_member_hashes_passed=True,extracted_parquet_files_fully_read=parquet_count,extracted_parquet_rows_read=parquet_rows,
               extracted_configuration_references_verified=config_references,all_telemetry_included=True,**overall)
    p.js(base/'zip_validation.json',check); p.js(p.ROOT/'deliverables/STATUS.json',check)
    print(json.dumps(check),flush=True)

if __name__=='__main__':
    package(p.BASE) if '--package' in sys.argv else summarize(p.BASE)
