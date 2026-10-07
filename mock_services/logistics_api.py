#!/usr/bin/env python3
"""A tiny stand-in for the external logistics provider (Part 7). Standard library only.

    python3 mock_services/logistics_api.py --port 8790 --api-key demo-key \\
        --latency 10-20 --failure-rate 0.3 \\
        --webhook-url http://localhost:8100/api/method/reno_order.integrations.logistics.webhook.shipment_status \\
        --webhook-secret demo-secret

Endpoints
  POST /v1/shipments                   create a shipment (Bearer auth, Idempotency-Key honoured)
  POST /v1/shipments/<id>/advance      demo helper: scheduled → in_transit → delivered, then calls the webhook
  GET  /health
Behaviour you can switch on to exercise the client: slow responses (--latency) and random
503 errors (--failure-rate).
"""

import argparse
import hashlib
import hmac
import itertools
import json
import random
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ARGS = None
SHIPMENTS: dict[str, dict] = {}
IDEMPOTENCY: dict[str, tuple[int, dict]] = {}
IDS = itertools.count(10001)
LOCK = threading.Lock()
NEXT_STATUS = {"scheduled": "in_transit", "in_transit": "delivered"}


class Handler(BaseHTTPRequestHandler):
	def do_GET(self):
		if self.path == "/health":
			return self.reply(200, {"status": "ok"})
		self.reply(404, {"error": "not_found"})

	def do_POST(self):
		if self.headers.get("Authorization") != f"Bearer {ARGS.api_key}":
			return self.reply(401, {"error": "unauthorized"})
		try:
			body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
		except ValueError:
			return self.reply(400, {"error": "invalid_json"})

		if self.path == "/v1/shipments":
			return self.create_shipment(body)
		parts = self.path.strip("/").split("/")
		if len(parts) == 4 and parts[:2] == ["v1", "shipments"] and parts[3] == "advance":
			return self.advance(parts[2])
		self.reply(404, {"error": "not_found"})

	def create_shipment(self, body):
		key = self.headers.get("Idempotency-Key")
		with LOCK:
			if key and key in IDEMPOTENCY:  # a retry of a request we already processed
				_status, saved = IDEMPOTENCY[key]
				return self.reply(200, saved)

		missing = [f for f in ("reference", "scheduled_date", "deliver_to", "items") if not body.get(f)]
		if missing:
			return self.reply(422, {"error": "validation_failed", "missing": missing})

		time.sleep(random.uniform(*ARGS.latency))  # a slow provider
		if random.random() < ARGS.failure_rate:
			return self.reply(503, {"error": "service_unavailable"})

		with LOCK:
			shipment = {
				"shipment_id": f"SHP-{next(IDS)}",
				"reference": body["reference"],
				"status": "scheduled",
				"scheduled_date": body["scheduled_date"],
			}
			SHIPMENTS[shipment["shipment_id"]] = shipment
			if key:
				IDEMPOTENCY[key] = (201, shipment)
		self.reply(201, shipment)

	def advance(self, shipment_id):
		shipment = SHIPMENTS.get(shipment_id)
		if not shipment:
			return self.reply(404, {"error": "unknown_shipment"})
		shipment["status"] = NEXT_STATUS.get(shipment["status"], shipment["status"])
		self.reply(200, {**shipment, "webhook": send_webhook(shipment)})

	def reply(self, status, payload):
		data = json.dumps(payload).encode()
		self.send_response(status)
		self.send_header("Content-Type", "application/json")
		self.send_header("Content-Length", str(len(data)))
		self.end_headers()
		self.wfile.write(data)


def send_webhook(shipment) -> str:
	if not ARGS.webhook_url:
		return "not configured"
	body = json.dumps({"shipment_id": shipment["shipment_id"], "status": shipment["status"]}).encode()
	timestamp = str(int(time.time()))
	signature = hmac.new(
		ARGS.webhook_secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
	).hexdigest()
	request = urllib.request.Request(
		ARGS.webhook_url,
		data=body,
		headers={
			"Content-Type": "application/json",
			"X-Reno-Timestamp": timestamp,
			"X-Reno-Signature": signature,
		},
		method="POST",
	)
	try:
		with urllib.request.urlopen(request, timeout=10) as response:
			return f"HTTP {response.status}"
	except Exception as e:
		return f"failed: {e}"


def main():
	global ARGS
	parser = argparse.ArgumentParser(
		description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
	)
	parser.add_argument("--port", type=int, default=8790)
	parser.add_argument("--api-key", default="demo-key")
	parser.add_argument("--latency", default="1-2", help="seconds, e.g. 10-20")
	parser.add_argument("--failure-rate", type=float, default=0.0, help="share of requests answered with 503")
	parser.add_argument("--webhook-url", default="")
	parser.add_argument("--webhook-secret", default="demo-secret")
	ARGS = parser.parse_args()
	ARGS.latency = (
		tuple(float(x) for x in ARGS.latency.split("-"))
		if "-" in ARGS.latency
		else (float(ARGS.latency),) * 2
	)
	print(
		f"Mock logistics API on http://127.0.0.1:{ARGS.port} (latency {ARGS.latency}s, failure rate {ARGS.failure_rate})"
	)
	ThreadingHTTPServer(("127.0.0.1", ARGS.port), Handler).serve_forever()


if __name__ == "__main__":
	main()
