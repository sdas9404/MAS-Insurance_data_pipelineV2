"""Validate input availability and basic route/network XML constraints."""
import argparse
from pathlib import Path
import xml.etree.ElementTree as ET
from collections import Counter


def validate(net_path: Path, route_path: Path, poly_path: Path) -> dict:
    for p in (net_path, route_path, poly_path):
        if not p.is_file():
            raise FileNotFoundError(f"Missing required file: {p}")
    net = ET.parse(net_path).getroot()
    routes = ET.parse(route_path).getroot()
    polygons = ET.parse(poly_path).getroot()
    if net.tag != "net" or routes.tag != "routes":
        raise ValueError("Expected SUMO <net> and <routes> roots")
    edges = {e.get("id") for e in net.findall("edge") if e.get("function") != "internal"}
    valid_routes = {r.get("id"): r.get("edges", "").split() for r in routes.findall("route")}
    ids, lengths = [], []
    for v in routes.findall("vehicle"):
        vid = v.get("id")
        ids.append(vid)
        nested = v.find("route")
        eids = nested.get("edges", "").split() if nested is not None else valid_routes.get(v.get("route"), [])
        if not eids:
            raise ValueError(f"No route edges for vehicle {vid}; only explicit routed vehicles supported")
        invalid = [e for e in eids if e not in edges]
        if invalid:
            raise ValueError(f"Vehicle {vid} refers to absent edge(s): {invalid[:5]}")
        lengths.append(len(eids))
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate template vehicle IDs")
    if not ids:
        raise ValueError("No <vehicle> route templates found")
    return {
        "network_road_edges": len(edges), "input_route_templates": len(ids),
        "min_route_edges": min(lengths), "max_route_edges": max(lengths),
        "polygon_root": polygons.tag,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    a = parser.parse_args()
    root = Path(a.root)
    print(validate(root/'network/london_filtered.net.xml', root/'routes/london.rou.xml', root/'network/london.poly.xml'))
