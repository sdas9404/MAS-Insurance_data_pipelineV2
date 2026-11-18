import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from research_handoff import deduplicate
from research_handoff import native

def record(i,a,b,start,end,ttc=2,code=False):
    return dict(run_id='r',ego=a,foe=b,begin_s=start,end_s=end,raw_record_id=i,has_type_111=code,minTTC_value=ttc)

def test_dedup_reciprocals_and_separate_episodes():
    raw=[record('1','a','b',1,4,0,True),record('2','b','a',1.1,4.1,1),record('3','a','b',5,6)]
    out=deduplicate(raw)
    assert len(out)==2
    assert out[0]['raw_record_count']==2 and out[0]['has_type_111']
    assert not out[0]['confirmed_collision']
    assert out[0]['minTTC_value']==0
    assert raw[0]['encounter_id']==raw[1]['encounter_id']!=raw[2]['encounter_id']

def test_dedup_keeps_runs_separate_and_handles_missing_measures():
    a=record('1','a','b',1,4); b=record('2','a','b',1,4); b['run_id']='r2'
    out=deduplicate([a,b])
    assert len(out)==2 and all(r['PET_value'] is None for r in out)

def test_native_ssm_background_attribution_without_collision(tmp_path):
    (tmp_path/'raw').mkdir()
    (tmp_path/'raw/lanechanges.xml').write_text('<lanechanges/>')
    (tmp_path/'raw/collisions.xml').write_text('<collisions/>')
    (tmp_path/'raw/ssm.xml').write_text('<SSMLog><conflict begin="1" end="2" ego="D000_T01" foe="BG_00"><minTTC time="1.5" type="111" value="0" position="1,2"/><PET time="NA" type="NA" value="NA" position="NA"/></conflict></SSMLog>')
    _,collisions,ssm=native(tmp_path,'run_001',{'D000_T01':dict(driver_id='D000',is_background=False),'BG_00':dict(driver_id=None,is_background=True)})
    assert not collisions and ssm[0]['has_type_111']
    assert ssm[0]['foe_is_background'] and ssm[0]['ego_driver_id']=='D000'
    assert ssm[0]['PET_position'] is None and ssm[0]['PET_value'] is None
    assert ssm[0]['PET_value_raw']=='NA' and 'value="NA"' in ssm[0]['raw_xml']
    assert not deduplicate(ssm)[0]['confirmed_collision']
