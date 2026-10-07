"""HTTP client for the external logistics provider (Part 7).

`mock_services/logistics_api.py` is a local stand-in for the provider.

- Authentication: `Authorization: Bearer <api key>`. The key is a Password field, stored encrypted
  and decrypted only in memory here. It is never logged: request headers are masked before logging.
- Timeouts: separate connect and read timeouts, so a dead host fails fast while a slow but working
  API (10-20 s) still gets time to answer.
- Errors are sorted into transient (worth retrying: timeouts, connection errors, 408/425/429/5xx) and
  permanent (401, 403, 422, …: retrying won't help until someone fixes the cause).
- Logging: every HTTP attempt is an Integration Request linked to the Reno Order, with the payload,
  the outcome, the status code and the duration.
"""

import json
import time

import frappe
import requests
from frappe.utils import cint

SERVICE_NAME = "Reno Logistics"
RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}


class LogisticsError(Exception):
	pass


class LogisticsTransientError(LogisticsError):
	"""Worth retrying later: the provider was slow, unreachable or temporarily failing."""


class LogisticsPermanentError(LogisticsError):
	"""Won't succeed by retrying: bad credentials, invalid data, and so on."""


class LogisticsClient:
	def __init__(self, settings):
		self.base_url = (settings.base_url or "").rstrip("/")
		self.api_key = settings.get_password("api_key", raise_exception=False)
		self.timeout = (cint(settings.connect_timeout) or 5, cint(settings.read_timeout) or 30)

	def create_shipment(self, payload: dict, idempotency_key: str, reference_name: str) -> dict:
		return self._request("POST", "/v1/shipments", payload, idempotency_key, reference_name)

	def _request(self, method: str, path: str, payload: dict, idempotency_key: str, reference_name: str):
		url = f"{self.base_url}{path}"
		headers = {
			"Authorization": f"Bearer {self.api_key}",
			"Idempotency-Key": idempotency_key,  # lets the provider return the same result on a retry
			"Content-Type": "application/json",
			"Accept": "application/json",
		}
		log = _start_log(method, url, payload, headers, idempotency_key, reference_name)
		started = time.monotonic()

		try:
			response = requests.request(method, url, json=payload, headers=headers, timeout=self.timeout)
		except requests.Timeout:
			error = f"Timed out after {time.monotonic() - started:.1f}s (timeouts: {self.timeout})"
			_finish_log(log, "Failed", error=error)
			raise LogisticsTransientError(error) from None
		except requests.ConnectionError as e:
			error = f"Could not connect to {self.base_url}: {e.__class__.__name__}"
			_finish_log(log, "Failed", error=error)
			raise LogisticsTransientError(error) from None

		body = _json_or_text(response)
		outcome = {
			"status_code": response.status_code,
			"seconds": round(time.monotonic() - started, 2),
			"body": body,
		}
		if response.ok:
			_finish_log(log, "Completed", output=outcome)
			return body

		error = f"HTTP {response.status_code}: {json.dumps(body)[:500]}"
		_finish_log(log, "Failed", output=outcome, error=error)
		if response.status_code in RETRYABLE_STATUS_CODES:
			raise LogisticsTransientError(error)
		raise LogisticsPermanentError(error)


# ---------------------------------------------------------------------- request log


def _start_log(method, url, payload, headers, idempotency_key, reference_name):
	masked = {**headers, "Authorization": "Bearer ***"}
	# A log record isn't business data, so it's written with ignore_permissions even though the job
	# runs as the automation user. The business documents themselves are still permission-checked.
	return frappe.get_doc(
		{
			"doctype": "Integration Request",
			"integration_request_service": SERVICE_NAME,
			"request_description": f"{method} {url.split('://', 1)[-1].split('/', 1)[-1]}",
			"url": url,
			"request_id": idempotency_key,
			"request_headers": json.dumps(masked, indent=1),
			"data": json.dumps(payload, indent=1, default=str),
			"status": "Queued",
			"reference_doctype": "Reno Order",
			"reference_docname": reference_name,
		}
	).insert(ignore_permissions=True)


def _finish_log(log, status: str, output=None, error=None):
	log.db_set(
		{
			"status": status,
			"output": json.dumps(output, indent=1, default=str) if output is not None else None,
			"error": error,
		}
	)


def _json_or_text(response):
	try:
		return response.json()
	except ValueError:
		return {"raw": response.text[:500]}
