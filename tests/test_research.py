import sys
from pathlib import Path
from collections import Counter
import yaml
import pyarrow.parquet as pq
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import research_pipeline as p

def test_population():
    c=yaml.safe_load((p.ROOT/'config/sampling.yaml').read_text()); a=p.population(c)
    assert a==p.population(c)
    assert len({d['driver_id'] for d in a})==90
    assert len({d['tau'] for d in a})==90
    for d in a:
        assert d['emergencyDecel']>=d['decel']
        assert abs(d['actionStepLength']/0.1-round(d['actionStepLength']/0.1))<1e-8
        assert 0<=d['lcPushyGap']<=d['minGapLat']
        assert not {'persona','risk_score','premium','risk_label'} & d.keys()
        for k,s in c['parameters'].items():
            if k in d and 'bounds' in s: assert s['bounds'][0]<=d[k]<=s['bounds'][1]

def test_design():
    b=p.prepare(); plans=pq.read_table(b/'datasets/trip_manifest.parquet').to_pylist()
    assert len(plans)==10800
    assert set(Counter(r['run_id'] for r in plans).values())=={900}
    assert set(Counter((r['run_id'],r['driver_id']) for r in plans).values())=={10}
    assert len({r['global_trip_id'] for r in plans})==10800
    for d in {r['driver_id'] for r in plans}:
        assert len({(r['trip_id'],r['route_edges'],r['depart_scheduled_s']) for r in plans if r['driver_id']==d})==10

def test_empty_schema(tmp_path):
    p.table(tmp_path/'events.parquet',[],p.EV)
    assert pq.read_table(tmp_path/'events.parquet').schema==p.EV

def test_generated_xml_matches_frozen_cohort_and_load():
    import xml.etree.ElementTree as E
    drivers=pq.read_table(p.BASE/'datasets/driver_parameters.parquet').to_pylist()
    for run in pq.read_table(p.BASE/'datasets/simulation_runs.parquet').to_pylist():
        rd=p.BASE/'runs'/run['run_id']
        types={v.get('id'):v.attrib for v in E.parse(rd/'study_types.rou.xml').getroot().findall('vType')}
        assert len(types)==90
        for d in drivers:
            expected={'id':d['driver_id'],**p.attrs(d)}
            actual=types[d['driver_id']]
            assert set(actual)==set(expected)
            for k,v in expected.items():
                try: assert float(actual[k])==float(v)
                except ValueError: assert actual[k]==v
        assert len(E.parse(rd/'study_planned.rou.xml').getroot().findall('vehicle'))==900
        background=E.parse(rd/'background.rou.xml').getroot().findall('vehicle')
        assert len(background)==run['planned_background']
        assert all(v.get('id').startswith('BG_') for v in background)

def test_baseline():
    import json
    changed={'config/sampling.yaml','scripts/generate_unlabeled.py','tests/test_foundation.py','README.md'}
    for r in json.loads((p.BASE/'baseline/inventory.json').read_text()):
        if r['path'] not in changed: assert p.digest(p.ROOT/r['path'])==r['sha256']
