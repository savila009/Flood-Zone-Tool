#!/usr/bin/env python3
"""Small static server + /api/check using Nominatim + FEMA NFHL (no extra deps)."""

import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "3000"))
PUBLIC_DIR = os.path.dirname(os.path.abspath(__file__))

NOMINATIM = "https://nominatim.openstreetmap.org/search"
FEMA_QUERY_ENDPOINTS = [
    "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query",
    "https://services5.arcgis.com/7weheFjxuNkGGiZi/arcgis/rest/services/USA_Flood_Hazard_Areas_view/FeatureServer/0/query",
]


def is_in_sfha(features):
    return any(f.get("attributes", {}).get("SFHA_TF") == "T" for f in features or [])


def http_get_json(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=PUBLIC_DIR, **kwargs)

    def log_message(self, fmt, *args):
        print(fmt % args)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/check":
            self._api_check(parsed)
            return
        super().do_GET()

    def _api_check(self, parsed):
        qs = urllib.parse.parse_qs(parsed.query)
        raw = (qs.get("address") or [""])[0].strip()
        if not raw:
            self._json(400, {"ok": False, "error": "Enter an address."})
            return

        try:
            geo_url = NOMINATIM + "?" + urllib.parse.urlencode(
                {
                    "format": "json",
                    "limit": "1",
                    "countrycodes": "us",
                    "q": raw,
                }
            )
            geo_data = http_get_json(
                geo_url,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "FloodZoneCheck/1.0 (local tool)",
                },
            )
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            self._json(502, {"ok": False, "error": f"Geocoding failed: {e}"})
            return

        if not geo_data:
            self._json(
                200,
                {
                    "ok": True,
                    "geocoded": False,
                    "answer": None,
                    "message": "That address could not be located. Try adding city and state.",
                },
            )
            return

        place = geo_data[0]
        try:
            lat = float(place["lat"])
            lon = float(place["lon"])
        except (KeyError, TypeError, ValueError):
            self._json(
                200,
                {
                    "ok": True,
                    "geocoded": False,
                    "answer": None,
                    "message": "Invalid coordinates returned for that address.",
                },
            )
            return

        fema_params = urllib.parse.urlencode(
            {
                "geometry": f"{lon},{lat}",
                "geometryType": "esriGeometryPoint",
                "inSR": "4326",
                "spatialRel": "esriSpatialRelIntersects",
                "outFields": "FLD_ZONE,SFHA_TF,ZONE_SUBTY",
                "returnGeometry": "false",
                "f": "json",
            }
        )
        fema_data = None
        endpoint_errors = []
        for endpoint in FEMA_QUERY_ENDPOINTS:
            fema_url = endpoint + "?" + fema_params
            try:
                data = http_get_json(fema_url)
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
                endpoint_errors.append(f"{endpoint}: {e}")
                continue
            if isinstance(data, dict) and data.get("error"):
                endpoint_errors.append(f"{endpoint}: {data['error']}")
                continue
            fema_data = data
            break

        if fema_data is None:
            self._json(
                502,
                {
                    "ok": False,
                    "error": "FEMA flood map service unavailable from all endpoints.",
                    "details": endpoint_errors,
                },
            )
            return

        features = fema_data.get("features") or []
        display_name = place.get("display_name", "")

        if not features:
            self._json(
                200,
                {
                    "ok": True,
                    "geocoded": True,
                    "displayName": display_name,
                    "lat": lat,
                    "lon": lon,
                    "inSfha": None,
                    "answer": None,
                    "zones": [],
                    "message": (
                        "No NFHL flood hazard polygon at this coordinate. The map may not "
                        "cover this area yet, or there is a data gap—this is not a clean “No.” "
                        "Try the official FEMA Map Service Center if you need certainty."
                    ),
                },
            )
            return

        in_sfha = is_in_sfha(features)
        zones = list(
            {f["attributes"]["FLD_ZONE"] for f in features if f.get("attributes", {}).get("FLD_ZONE")}
        )

        self._json(
            200,
            {
                "ok": True,
                "geocoded": True,
                "displayName": display_name,
                "lat": lat,
                "lon": lon,
                "inSfha": in_sfha,
                "answer": "Yes" if in_sfha else "No",
                "zones": zones,
                "message": (
                    "FEMA’s National Flood Hazard Layer marks this location in a Special "
                    "Flood Hazard Area (high-risk / 1% annual-chance floodplain)."
                    if in_sfha
                    else "FEMA’s National Flood Hazard Layer shows this location outside the "
                    "Special Flood Hazard Area for the effective map (e.g. Zone X or similar)."
                ),
            },
        )

    def _json(self, status, body):
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _make_server():
    """Prefer IPv6 dual-stack (::) so http://localhost works when it resolves to ::1."""

    class DualStackV6(ThreadingHTTPServer):
        address_family = socket.AF_INET6

        def server_bind(self):
            try:
                self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            except OSError:
                pass
            super().server_bind()

    try:
        return DualStackV6(("::", PORT), Handler)
    except OSError:
        return ThreadingHTTPServer(("127.0.0.1", PORT), Handler)


def main():
    server = _make_server()
    host = server.server_address[0]
    if ":" in str(host) and not str(host).startswith("::ffff:"):
        print(f"Serving — open http://127.0.0.1:{PORT}/ or http://localhost:{PORT}/")
    else:
        print(f"Serving — open http://127.0.0.1:{PORT}/")
    server.serve_forever()


if __name__ == "__main__":
    main()
