# Third-party integration and background processing (Parts 7 + 8)

**Scenario:** when a Reno Order becomes **Ready for Installation**, the kitchen components must be delivered to site. The app books that delivery with an external **logistics provider**, and the provider reports progress back through a **webhook**. The provider is mocked by `mock_services/logistics_api.py`, a standard-library HTTP server that can be slow (`--latency 10-20`) and flaky (`--failure-rate 0.3`).

```
Reno Order ── Ready for Installation ──► queue job (after commit, long queue, job_id)
                                              │  claim: Queued/Failed → Booking (commit)
                                              ▼
                                 POST /v1/shipments  (Bearer key, Idempotency-Key, timeouts)
                                   │ 201 → Booked + shipment id      │ timeout / 5xx → retry with backoff
                                   ▼                                  ▼ still failing → Failed + next retry (hourly job)
Provider ── webhook (HMAC signed) ──► In Transit / Delivered
```

| File | Role |
|---|---|
| `reno_order/integrations/logistics/client.py` | HTTP client: auth, timeouts, error classification, request log |
| `reno_order/integrations/logistics/booking.py` | Queueing, the job, retries, recovery |
| `reno_order/integrations/logistics/webhook.py` | Signed status callbacks |
| `Reno Logistics Settings` (single) | URL, encrypted credentials, timeouts, max attempts |

## Part 7: what the integration demonstrates
| Requirement | How |
|---|---|
| **Authentication** | Outbound: `Authorization: Bearer <api key>`. Inbound webhook: **HMAC-SHA256** of `"<timestamp>.<raw body>"` with a shared secret, compared in constant time (`hmac.compare_digest`) |
| **Request / response handling** | The payload is built from the order (reference, date, delivery address, contact phone, **stock items only**). The `shipment_id` from the response is stored on the order. The webhook maps provider statuses (`scheduled / in_transit / delivered`) to ours |
| **Error handling** | Errors are classified. **Transient:** timeouts, connection errors, 408/425/429/5xx. **Permanent:** 401, 403, 422, …. The order shows the status and last error, and Error Log gets an entry |
| **Logging** | **One Integration Request per HTTP attempt**, linked to the Reno Order: payload, masked headers, status code, duration, response or error. Webhook calls are logged too (`is_remote_request`) |
| **Retry strategy** | In the job: up to 3 tries with exponential backoff + jitter (~2 s, ~4 s). After that: `Failed` with `next_retry` at 5 → 10 → 20 … min (max 2 h), picked up by an hourly job, up to *Max Booking Attempts*. Permanent errors aren't retried automatically; **Retry Delivery Booking** on the form re-queues after a fix |
| **Timeout handling** | `timeout=(connect, read)`, default (5 s, 30 s). A dead host fails in 5 s, and a slow but healthy provider (10–20 s) still gets through. Timeouts count as transient |
| **Secure credential storage** | API key and webhook secret are **Password fields**: encrypted at rest with the site's `encryption_key`, read with `get_password()` only when needed, **masked in logs** (tested), never in code or the repo. The settings are System Manager only |

**Idempotency:** each booking request sends `Idempotency-Key: reno-order:<name>:delivery`. If our read timeout fires *after* the provider has created the shipment, the retry gets the **same** shipment back instead of a second one. The mock implements this.

**Replay protection (webhook):** messages older than 5 minutes are rejected. Unsigned or forged calls get **401**, and nothing changes.

## Part 8: background processing
| Requirement | How |
|---|---|
| **Background job creation** | `on_update_after_submit` → `queue_shipment_booking()` → `frappe.enqueue(..., enqueue_after_commit=True)`. The save returns at once, and the job only exists if the status change really committed |
| **Queue selection** | **`long`** (job timeout 300 s): one call can take 20 s, and in-job retries can add more. `short`/`default` are for quick work like the Delivery Note job (`default`). Keeping slow jobs on `long` means they never block quick ones |
| **Failure handling** | The error is caught and classified, and the order records `Failed`, the attempt count, the next retry time and the error message. An Error Log entry and a failed Integration Request are written. The job itself ends cleanly, so the failure record is committed |
| **Retry strategy** | Backoff inside the job, then scheduled retries (hourly job), then a manual retry button (see Part 7) |
| **Logging** | Integration Request per attempt + Error Log on failure + status fields on the order (visible in the form headline and list filters) |
| **Protection against duplicate processing** | ① `job_id` + `deduplicate` stop RQ queuing a second job for the same order while one is waiting or running. ② An **atomic claim**: `UPDATE … SET logistics_status='Booking' WHERE status IN ('Queued','Failed') AND reference IS NULL`; only one worker gets `rowcount = 1`. ③ An already-booked order is skipped. ④ The provider-side **Idempotency-Key** covers "we timed out but they succeeded" |

**Why the claim instead of a row lock:** the job commits the claim straight away and calls the provider **without holding any database lock**. Holding `SELECT … FOR UPDATE` across a 20-second network call would make anyone saving that order wait, and possibly hit lock-wait timeouts.

**Crash recovery:** if a worker dies mid-call, the order stays in `Booking`. The hourly job treats a claim older than 15 minutes as failed and re-queues it. The Idempotency-Key makes that retry safe.

## Measured run (demo site, mock at 10–20 s latency, 30% failures)
```
'Mark Ready for Installation' save took 0.07s → logistics_status=Queued
  +0s   Booking | -         | attempt 1
  +32s  Booked  | SHP-10001 | attempt 1

Integration Request log for RO-00003
  Failed     POST v1/shipments   HTTP 503 service_unavailable   11.99 s
  Failed     POST v1/shipments   HTTP 503 service_unavailable   10.93 s
  Completed  POST v1/shipments   201                            10.91 s

courier → in_transit | webhook HTTP 200   →  Reno Order: In Transit
courier → delivered  | webhook HTTP 200   →  Reno Order: Delivered
forged webhook (wrong signature)          →  HTTP 401, status unchanged
```
The user waited **0.07 s**. The provider took **~34 s** across three attempts in the background.

## Run it yourself
```bash
python3 mock_services/logistics_api.py --port 8790 --api-key demo-key --latency 10-20 --failure-rate 0.3 \
  --webhook-url http://localhost:8000/api/method/reno_order.integrations.logistics.webhook.shipment_status \
  --webhook-secret demo-secret
# Reno Logistics Settings: Enabled, API Base URL http://127.0.0.1:8790, API Key demo-key, Webhook Signing Secret demo-secret
# Move an order to Ready for Installation, then watch Logistics → Delivery Booking, and Integration Request.
curl -X POST -H "Authorization: Bearer demo-key" http://127.0.0.1:8790/v1/shipments/SHP-10001/advance   # sends the webhook
```
