"""Create 90 stable, unlabeled driver parameter sets and 10 route episodes per driver.

Produces a ready-to-run SUMO route/config pair, CSV manifests, and Parquet manifests.
Input routes must use explicit <vehicle> with a nested <route edges=.../> or a
reference to a global <route id=... edges=.../>. The original files are unmodified.
"""
import argparse
import csv
import json
import random
from pathlib import Path
import xml.etree.ElementTree as ET

import pyarrow as pa
import pyarrow.parquet as pq
import yaml

from validate_inputs import validate


def sample_uniform(rng, lo_hi, digits=4):
    return round(rng.uniform(*map(float, lo_hi)), digits)


def sample_driver(rng, cfg):
    bounds = cfg['bounds']
    d = {k: sample_uniform(rng, b) for k, b in bounds.items()}
    action_step = rng.choice([0.2, 0.4, 0.6, 0.8, 1.0])
    d['actionStepLength'] = action_step
    # The low-tail branch permits occasional difficult safety interactions without
    # defining any discrete named driver group or preassigning crash outcomes.
    if rng.random() < cfg['small_headway_tail_probability']:
        d['tau'] = round(rng.uniform(0.28, 0.65), 4)
    else:
        d['tau'] = round(rng.uniform(max(0.75, action_step + 0.15), 2.4), 4)
    decel = d['decel']
    d['emergencyDecel'] = round(max(decel + rng.uniform(1.0, 2.2), 6.0), 4)
    d['apparentDecel'] = round(max(2.5, decel * rng.uniform(0.8, 1.06)), 4)
    # -1 disables deliberate red-light violation, not zero.
    d['jmDriveAfterRedTime'] = round(rng.uniform(1, 5.0), 4) if rng.random() < cfg['red_light_noncompliance_probability'] else -1.0
    d['jmDriveRedSpeed'] = None
    if d['jmDriveAfterRedTime'] > 0:
        d['jmDriveRedSpeed'] = round(rng.uniform(5, 11), 4)
    d['jmIgnoreFoeProb'] = round(rng.uniform(0.004, 0.04), 4) if rng.random() < cfg['junction_priority_error_probability'] else 0.0
    d['jmIgnoreFoeSpeed'] = None
    if d['jmIgnoreFoeProb'] > 0:
        d['jmIgnoreFoeSpeed'] = 12.0
    # speedDev=0 prevents a second random speed factor being drawn for each trip.
    d['speedDev'] = 0
    d['vClass'] = 'passenger'
    d['carFollowModel'] = 'Krauss'
    d['laneChangeModel'] = 'LC2013'
    # Deliberately omit lcPushy: it is inert without SUMO's sublane model.
    return d


def route_templates(path):
    root = ET.parse(path).getroot()
    defs = {r.get('id'): r.get('edges', '') for r in root.findall('route')}
    out = []
    for v in root.findall('vehicle'):
        r = v.find('route')
        edges = r.get('edges') if r is not None else defs.get(v.get('route'))
        if not edges:
            raise ValueError(f"Missing explicit edges for {v.get('id')}")
        out.append({'template_id': v.get('id'), 'edges': edges})
    return out


def write_csv(path, rows):
    if not rows:
        raise ValueError('No records to write')
    with open(path, 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)


def main(root):
    cfg = yaml.safe_load((root/'config/sampling.yaml').read_text(encoding='utf-8'))
    run_id = str(cfg['run_id'])
    n, trips = int(cfg['n_drivers']), int(cfg['trips_per_driver'])
    validate(root/'network/london_filtered.net.xml', root/'routes/london.rou.xml', root/'network/london.poly.xml')
    templates = route_templates(root/'routes/london.rou.xml')
    rng = random.Random(int(cfg['seed']))
    drivers = []
    for i in range(n):
        driver_id = f'D{i:03d}'
        drivers.append({'run_id': run_id, 'driver_id': driver_id, **sample_driver(rng, cfg)})

    # Balanced reuse: with original 300 templates and 900 trips, each template
    # appears three times. Otherwise sample approximately evenly.
    need = n * trips
    order = [templates[i % len(templates)] for i in range(need)]
    rng.shuffle(order)
    root_routes = ET.Element('routes')
    for driver in drivers:
        attrs = {'id': driver['driver_id']}
        attrs.update({k: str(v) for k, v in driver.items() if k not in ('run_id', 'driver_id') and v is not None})
        ET.SubElement(root_routes, 'vType', attrs)
    records = []
    for t in range(trips):
        for i, d in enumerate(drivers):
            trip_id = f"{d['driver_id']}_T{t+1:02d}"
            tpl = order[t*n+i]
            departure = t * int(cfg['wave_seconds']) + rng.randrange(int(cfg['wave_departure_window_seconds']))
            v = ET.SubElement(root_routes, 'vehicle', {
                'id': trip_id, 'type': d['driver_id'], 'depart': str(departure),
                'departLane': 'best', 'departSpeed': 'max',
            })
            ET.SubElement(v, 'route', {'edges': tpl['edges']})
            records.append({'run_id': run_id, 'driver_id': d['driver_id'],
                            'trip_id': trip_id, 'trip_number': t+1,
                            'depart_scheduled_s': float(departure),
                            'source_route_template_id': tpl['template_id'],
                            'route_first_edge': tpl['edges'].split()[0],
                            'route_last_edge': tpl['edges'].split()[-1]})
    # SUMO requires vehicles sorted by departure time, irrespective of persona.
    children = list(root_routes)
    type_elems = [x for x in children if x.tag == 'vType']
    vehicles = sorted([x for x in children if x.tag == 'vehicle'], key=lambda x: (float(x.get('depart')), x.get('id')))
    root_routes[:] = type_elems + vehicles

    (root/'data/manifest').mkdir(parents=True, exist_ok=True)
    (root/f'data/raw/{run_id}').mkdir(parents=True, exist_ok=True)
    (root/'routes').mkdir(parents=True, exist_ok=True)
    route_path = root/f'routes/{run_id}_900.rou.xml'
    ET.indent(root_routes, space='  ')
    ET.ElementTree(root_routes).write(route_path, encoding='utf-8', xml_declaration=True)
    write_csv(root/f'data/manifest/{run_id}_driver_parameters.csv', drivers)
    write_csv(root/f'data/manifest/{run_id}_trips.csv', records)
    pq.write_table(pa.Table.from_pylist(drivers), root/f'data/manifest/{run_id}_driver_parameters.parquet')
    pq.write_table(pa.Table.from_pylist(records), root/f'data/manifest/{run_id}_trip_manifest.parquet')

    simcfg = ET.Element('configuration')
    inp = ET.SubElement(simcfg, 'input')
    ET.SubElement(inp, 'net-file', {'value': 'network/london_filtered.net.xml'})
    ET.SubElement(inp, 'route-files', {'value': f'routes/{run_id}_900.rou.xml'})
    ET.SubElement(inp, 'additional-files', {'value': 'network/london.poly.xml'})
    out = ET.SubElement(simcfg, 'output')
    for key, fname in [('tripinfo-output','tripinfo.xml'),('collision-output','collisions.xml'),('lanechange-output','lanechanges.xml')]:
        ET.SubElement(out, key, {'value': f'data/raw/{run_id}/{fname}'})
    ET.SubElement(out, 'tripinfo-output.write-unfinished', {'value': 'true'})
    time = ET.SubElement(simcfg, 'time')
    ET.SubElement(time, 'begin', {'value':'0'})
    end = (trips - 1) * int(cfg['wave_seconds']) + int(cfg['wave_seconds'])
    ET.SubElement(time, 'end', {'value':str(end)})
    ET.SubElement(time, 'step-length', {'value':str(cfg['simulation_step_seconds'])})
    proc = ET.SubElement(simcfg, 'processing')
    ET.SubElement(proc, 'collision.action', {'value': 'warn'})
    ET.SubElement(proc, 'collision.check-junctions', {'value': 'true'})
    report = ET.SubElement(simcfg, 'report')
    ET.SubElement(report, 'duration-log.statistics', {'value': 'true'})
    ET.indent(simcfg, space='  ')
    ET.ElementTree(simcfg).write(root/f'{run_id}.sumocfg', encoding='utf-8', xml_declaration=True)
    (root/'data/manifest'/f'{run_id}_run.json').write_text(json.dumps({
        'run_id':run_id,'seed':cfg['seed'],'driver_count':n,'planned_trips':need,
        'route_template_count':len(templates),'trip_episodes_are_independent':True,
        'no_personas_or_risk_labels':True,'simulated_step_s':cfg['simulation_step_seconds']}, indent=2))
    print(f'Generated {n} unique unlabeled drivers and {need} planned trips')
    print(f'Route file: {route_path}')
    print(f'Config: {root / (run_id+".sumocfg")}')
    print(f'Next: sumo -c .\\{run_id}.sumocfg --verbose')

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', default='.')
    main(Path(parser.parse_args().root).resolve())
