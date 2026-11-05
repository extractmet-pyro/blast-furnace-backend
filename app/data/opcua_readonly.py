"""Read-only OPC UA edge collector (runs ON-PREMISES in the OT/industrial DMZ - never on Render).

Architecture (docs/OT_SECURITY.md):
    DCS/historian OPC UA server --(read/subscribe only)--> this collector (DMZ host)
        --(outbound HTTPS, token)--> /api/v1/data/ingest  (cloud analytics, LIVE_MONITOR_ONLY)

Safety properties
  * Only `read_value()` / data-change subscriptions are used. The module contains no call that
    writes node values, calls methods or browses-and-modifies; tests/test_safety.py scans the
    source to prove it. Server-side write permissions for the collector account must ALSO be
    disabled by the site (defence in depth).
  * Security policy Basic256Sha256 + SignAndEncrypt and certificate pinning are required by
    default; credentials/certificates are read from files/environment on the edge host only.
  * The collector pushes measurements outbound; the cloud never connects into the OT network.

Status: implemented against the `asyncua` client API (optional dependency, `pip install asyncua`).
NOT tested against a live OPC UA server in this release - site FAT/SAT is required.

Usage (edge host):
    python -m app.data.opcua_readonly --map site_map.json --api https://<backend>/api/v1/data/ingest
    # env: OPCUA_URL, OPCUA_CERT, OPCUA_KEY, OPCUA_SERVER_CERT, EDGE_INGEST_TOKEN
site_map.json: [{"node_id": "ns=2;s=BF1.BLAST.FLOW", "tag": "BL_FLOW", "scale": 1.0, "offset": 0.0}, ...]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List


def build_payload(samples: List[Dict[str, object]], mapping: Dict[str, dict]) -> dict:
    """Convert raw node samples to the /data/ingest contract (pure function, unit-tested)."""
    readings = []
    for s in samples:
        m = mapping.get(str(s["node_id"]))
        if not m:
            continue
        v = s.get("value")
        val = None if v is None else float(v) * float(m.get("scale", 1.0)) + float(m.get("offset", 0.0))
        ts = s.get("ts") or datetime.now(timezone.utc)
        if isinstance(ts, datetime):
            ts = ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        readings.append({"tag": m["tag"], "value": val, "ts_utc": ts})
    return {"readings": readings}


def post(api_url: str, token: str, payload: dict, timeout: float = 10.0) -> int:
    req = urllib.request.Request(api_url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-Edge-Token": token})
    # the URL comes from the site configuration (read-only historian/edge endpoint), never from a request
    with urllib.request.urlopen(req, timeout=timeout) as r:  # nosec B310
        return r.status


async def collect_forever(map_file: str, api_url: str, period_s: float = 60.0):  # pragma: no cover - needs a server
    from asyncua import Client  # optional dependency
    mapping = {m["node_id"]: m for m in json.loads(open(map_file).read())}
    client = Client(url=os.environ["OPCUA_URL"])
    client.set_security_string(
        f"Basic256Sha256,SignAndEncrypt,{os.environ['OPCUA_CERT']},{os.environ['OPCUA_KEY']},{os.environ['OPCUA_SERVER_CERT']}")
    token = os.environ["EDGE_INGEST_TOKEN"]
    async with client:
        nodes = {nid: client.get_node(nid) for nid in mapping}
        while True:
            samples = []
            for nid, node in nodes.items():
                try:
                    dv = await node.read_data_value()          # READ ONLY
                    samples.append({"node_id": nid, "value": dv.Value.Value,
                                    "ts": dv.SourceTimestamp or dv.ServerTimestamp})
                except Exception:
                    samples.append({"node_id": nid, "value": None, "ts": None})
            payload = build_payload(samples, mapping)
            try:
                post(api_url, token, payload)
            except Exception as e:
                print("push failed (buffering is the site's responsibility):", e)
            await asyncio.sleep(period_s)


def main():  # pragma: no cover
    ap = argparse.ArgumentParser(description="Read-only OPC UA edge collector")
    ap.add_argument("--map", required=True)
    ap.add_argument("--api", required=True)
    ap.add_argument("--period", type=float, default=60.0)
    a = ap.parse_args()
    asyncio.run(collect_forever(a.map, a.api, a.period))


if __name__ == "__main__":  # pragma: no cover
    main()
