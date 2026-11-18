"""Small deterministic fixture; requires the project's requirements installed."""
import subprocess
import sys
from pathlib import Path
import xml.etree.ElementTree as ET

import pyarrow.parquet as pq
import yaml


def test_scenario_generator(tmp_path):
    root = Path(__file__).resolve().parents[1]
    (tmp_path/'network').mkdir()
    (tmp_path/'routes').mkdir()
    (tmp_path/'config').mkdir()
    net = ET.Element('net')
    for e in ('e1', 'e2'):
        edge = ET.SubElement(net, 'edge', {'id': e, 'from': 'j1', 'to': 'j2'})
        ET.SubElement(edge, 'lane', {'id': f'{e}_0', 'speed': '13.89', 'length': '100'})
    ET.ElementTree(net).write(tmp_path/'network/london_filtered.net.xml', encoding='utf-8')
    (tmp_path/'network/london.poly.xml').write_text('<additional/>')
    routes = ET.Element('routes')
    for i in range(3):
        vehicle = ET.SubElement(routes, 'vehicle', {'id': str(i), 'depart': str(i * 10)})
        ET.SubElement(vehicle, 'route', {'edges': 'e1'})
    ET.ElementTree(routes).write(tmp_path/'routes/london.rou.xml', encoding='utf-8')
    config = yaml.safe_load((root/'experiments/unlabeled_v1/baseline/config/sampling.yaml').read_text())
    config.update({'n_drivers': 6, 'trips_per_driver': 2})
    (tmp_path/'config/sampling.yaml').write_text(yaml.safe_dump(config))
    result = subprocess.run([sys.executable, str(root/'experiments/unlabeled_v1/baseline/scripts/generate_unlabeled.py'), '--root', str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    generated = ET.parse(tmp_path/'routes/run_001_900.rou.xml').getroot()
    ids = [x.attrib['id'] for x in generated.findall('vehicle')]
    assert len(ids) == len(set(ids)) == 12
    assert len(generated.findall('vType')) == 6
    params = pq.read_table(tmp_path/'data/manifest/run_001_driver_parameters.parquet').to_pylist()
    trips = pq.read_table(tmp_path/'data/manifest/run_001_trip_manifest.parquet').to_pylist()
    assert len(params) == 6
    assert len(trips) == 12
    assert all(sum(r['driver_id'] == d['driver_id'] for r in trips) == 2 for d in params)
    assert all('persona' not in row and 'risk_score' not in row for row in params)
