"""Separate following/braking mechanism probe. Never part of main observations."""
from pathlib import Path
import json, subprocess, xml.etree.ElementTree as ET
import research_pipeline as p

def run():
    base=p.BASE/'diagnostics/following_brake_probe'
    if (base/'results.json').exists(): return
    base.mkdir(parents=True,exist_ok=True)
    nodes=ET.Element('nodes')
    for nid,x in [('a',0),('b',1200)]: ET.SubElement(nodes,'node',id=nid,x=str(x),y='0',type='priority')
    edges=ET.Element('edges'); ET.SubElement(edges,'edge',id='road',**{'from':'a','to':'b','numLanes':'1','speed':'20'})
    p.write_xml(base/'nodes.xml',nodes); p.write_xml(base/'edges.xml',edges)
    subprocess.run([str(p.SUMO.parent/'netconvert.exe'),'-n',str(base/'nodes.xml'),'-e',str(base/'edges.xml'),'-o',str(base/'network.net.xml')],check=True,capture_output=True)
    results=[]
    for tau in [.35,1.5]:
        for seed in [1103,2207]:
            rd=base/f'tau_{tau}_seed_{seed}'; rd.mkdir(exist_ok=True)
            r=ET.Element('routes')
            ET.SubElement(r,'vType',id='lead',carFollowModel='Krauss',laneChangeModel='SL2015',accel='2',decel='6',emergencyDecel='8',apparentDecel='4',tau='1.5',actionStepLength='0.5',sigma='0.3')
            ET.SubElement(r,'vType',id='follow',carFollowModel='Krauss',laneChangeModel='SL2015',accel='2.5',decel='4.5',emergencyDecel='7',tau=str(tau),actionStepLength='1',sigma='0.3')
            for i in range(5):
                v=ET.SubElement(r,'vehicle',id=f'lead_{i}',type='lead',depart=str(i*80),departSpeed='15'); ET.SubElement(v,'route',edges='road'); ET.SubElement(v,'stop',lane='road_0',endPos='700',duration='8')
                v=ET.SubElement(r,'vehicle',id=f'follow_{i}',type='follow',depart=str(i*80+2),departSpeed='15'); ET.SubElement(v,'route',edges='road')
            p.write_xml(rd/'routes.rou.xml',r)
            cmd=[str(p.SUMO),'-n',str(base/'network.net.xml'),'-r',str(rd/'routes.rou.xml'),'--step-length','.1','--step-method.ballistic','true','--lateral-resolution','.5','--seed',str(seed),'--end','500','--collision.action','teleport','--collision.check-junctions','true','--collision-output',str(rd/'collisions.xml'),'--tripinfo-output',str(rd/'tripinfo.xml'),'--device.ssm.probability','1','--device.ssm.measures','TTC DRAC PET PPET MDRAC','--device.ssm.file',str(rd/'ssm.xml'),'--no-step-log','true','--log',str(rd/'sumo.log'),'--error-log',str(rd/'warnings.log')]
            result=subprocess.run(cmd,capture_output=True,text=True)
            (rd/'console.log').write_text(result.stdout+result.stderr)
            assert result.returncode==0,result.stderr
            conflicts=ET.parse(rd/'ssm.xml').getroot().findall('conflict')
            vals=[float(e.find('minTTC').get('value')) for e in conflicts if e.find('minTTC') is not None and e.find('minTTC').get('value')!='NA']
            results.append(dict(tau=tau,seed=seed,completed=len(ET.parse(rd/'tripinfo.xml').getroot().findall('tripinfo')),collisions=len(ET.parse(rd/'collisions.xml').getroot().findall('collision')),ssm_reports=len(conflicts),min_ttc_s=min(vals) if vals else None))
    p.js(base/'results.json',dict(excluded_from_main_dataset=True,purpose='Compare low tau below actionStepLength with longer tau during planned leader stops; no speed override or disabled safety checks.',results=results))
    (base/'README.md').write_text('Separate controlled mechanism probe. Leaders have planned eight-second stops at 700 m. Followers use tau 0.35 or 1.5 s with 1 s action steps. Two seeds, five repetitions each. No external speed forcing or disabled safety checks. These are calibration observations excluded from all main Parquet tables and event totals. Zero collisions is a valid outcome. Reproduce: python scripts/run_calibration.py\n')
    print(json.dumps(results))

if __name__=='__main__': run()
