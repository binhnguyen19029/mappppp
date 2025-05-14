#!/usr/bin/env python3
import argparse
import osmium
import sys
import csv
import time
import os
import json
from shapely.geometry import Point, LineString
from rtree import index
import psycopg2
import psycopg2.extras
from geopy.distance import geodesic

# --- Target highway types ---
TARGET_HIGHWAY_TYPES = {
    'motorway', 'trunk', 'primary', 'secondary', 'tertiary',
    'unclassified', 'residential',
    'motorway_link', 'trunk_link', 'primary_link', 'secondary_link', 'tertiary_link',
    'living_street', 'service', 'road'
}
QUERY_RADIUS_METERS = 50

# Globals for index & cache
way_data_cache = {}
p = index.Property()
spatial_idx = index.Index(properties=p)
indexed_way_count = 0


def parse_args():
    parser = argparse.ArgumentParser(
        description="Process one CSV chunk against Vietnam OSM PBF"
    )
    parser.add_argument(
        "--osm-pbf", required=True,
        help="Path to vietnam-latest.osm.pbf"
    )
    parser.add_argument(
        "--input-csv", required=True,
        help="Path to CSV chunk, e.g., csv_chunks/vietnam_part_01.csv"
    )
    parser.add_argument(
        "--db-url", required=True,
        help="DATABASE_URL for PostgreSQL"
    )
    parser.add_argument(
        "--commit-interval", type=int, default=1000,
        help="Rows per commit (default: 1000)"
    )
    return parser.parse_args()


def calculate_segment_length(coords_lat_lon):
    total_length = 0.0
    for i in range(len(coords_lat_lon) - 1):
        segment = geodesic(coords_lat_lon[i], coords_lat_lon[i+1]).meters
        total_length += segment
    return total_length


class IndexBuilderHandler(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()

    def way(self, w):
        global indexed_way_count, way_data_cache
        highway = w.tags.get('highway')
        if highway not in TARGET_HIGHWAY_TYPES or len(w.nodes) < 2:
            return

        # collect coords and bbox
        coords = []
        mins = [float('inf'), float('inf')]
        maxs = [float('-inf'), float('-inf')]
        try:
            for n in w.nodes:
                if not n.location.valid():
                    return
                lat, lon = n.location.lat, n.location.lon
                coords.append((lat, lon))
                mins[0], mins[1] = min(mins[0], lat), min(mins[1], lon)
                maxs[0], maxs[1] = max(maxs[0], lat), max(maxs[1], lon)
        except osmium.InvalidLocationError:
            return

        if len(coords) < 2:
            return

        length = calculate_segment_length(coords)
        way_data_cache[w.id] = {
            'geometry': coords,
            'segment_length_meters': length,
            **{tag: w.tags.get(tag) for tag in ['name','maxspeed','lanes','oneway','surface','ref','lit','bridge','tunnel','access','service']},
            'highway': highway
        }
        bbox = (mins[1], mins[0], maxs[1], maxs[0])
        spatial_idx.insert(indexed_way_count, bbox, obj=w.id)
        indexed_way_count += 1
        if indexed_way_count % 5000 == 0:
            print(f"Indexed {indexed_way_count} ways", file=sys.stderr)


def find_closest_way(lat, lon, radius_m=QUERY_RADIUS_METERS):
    r_deg = radius_m/111000 * 1.5
    bbox = (lon-r_deg, lat-r_deg, lon+r_deg, lat+r_deg)
    candidates = list(spatial_idx.intersection(bbox, objects=True))
    if not candidates:
        return None
    point = Point(lon, lat)
    best = None
    mind = float('inf')
    for item in candidates:
        data = way_data_cache.get(item.object)
        if not data:
            continue
        line = LineString([(pt[1],pt[0]) for pt in data['geometry']])
        d = point.distance(line)
        if d < mind:
            mind, best = d, item.object
    if best is None:
        return None
    result = way_data_cache[best]
    # compute exact geodesic to nearest node
    dmin = float('inf')
    for pt in result['geometry']:
        d = geodesic((lat, lon), pt).meters
        if d < dmin:
            dmin = d
    result['distance_to_input_point_meters'] = dmin
    result['way_id'] = best
    return result


if __name__ == '__main__':
    args = parse_args()
    OSM_FILE_PATH = args.osm_pbf
    CSV_INPUT_PATH = args.input_csv
    DATABASE_URL = args.db_url
    COMMIT_INTERVAL = args.commit_interval

    # --- Build index ---
    print("Building spatial index...")
    handler = IndexBuilderHandler()
    handler.apply_file(OSM_FILE_PATH, locations=True)
    print(f"Indexed {indexed_way_count} ways")

    # --- Process CSV ---
    print(f"Processing CSV: {CSV_INPUT_PATH}")
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    cur = conn.cursor()
    insert_sql = """
    INSERT INTO road_segment_results (
      input_latitude,input_longitude,input_source_identifier,
      found_osm_way_id,geometry_coords,road_name,highway_type,
      maxspeed,lanes,oneway,surface,ref,lit,bridge,tunnel,access,service,
      distance_to_input_point_meters,query_radius_used,segment_length_meters
    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s);
    """

    with open(CSV_INPUT_PATH, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for i,row in enumerate(reader, start=1):
            try:
                lat, lon = float(row['latitude']), float(row['longitude'])
            except:
                continue
            data = find_closest_way(lat, lon)
            if not data:
                continue
            geom_json = json.dumps(data['geometry'])
            tup = (
                lat, lon, i,
                data['way_id'], geom_json, data.get('name'), data.get('highway'),
                data.get('maxspeed'), data.get('lanes'), data.get('oneway'), data.get('surface'),
                data.get('ref'), data.get('lit'), data.get('bridge'), data.get('tunnel'),
                data.get('access'), data.get('service'), data['distance_to_input_point_meters'],
                float(QUERY_RADIUS_METERS), data['segment_length_meters']
            )
            cur.execute(insert_sql, tup)
            if i % COMMIT_INTERVAL == 0:
                conn.commit()
                print(f"Committed {i} rows")
    conn.commit()
    cur.close()
    conn.close()
    print("Done processing chunk.")
