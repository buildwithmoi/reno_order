# Site Supervisor REST API (Part 6)

Base URL: `https://<site>/api/method/reno_order.api.supervisor.<endpoint>` (all **POST**)

| Endpoint | Body | Does |
|---|---|---|
| `update_installation_status` | `{"reno_order": "RO-00001", "status": "Installed"}` | Moves the order through the workflow (for a Site Supervisor: *Ready for Installation → Installed*) |
| `add_installation_remarks` | `{"reno_order": "RO-00001", "remarks": "Installation completed successfully."}` | Appends a remark, stamped with the time and the user |
| `upload_site_photo` | `multipart/form-data`: `reno_order`, `file` | Attaches a JPG/PNG/WebP (≤ 10 MB, verified to really be an image) as a **private** file |

Each call returns a small JSON summary: `reno_order`, `status`, `installed_on`, `installation_remarks`, `delivery_status` (or, for photos, `file_name`/`file_url`).

## Authentication

**Each supervisor authenticates as their own Frappe user** with an API key and secret:

```
Authorization: token <api_key>:<api_secret>
```

- An administrator generates the pair under *User → Settings → API Access → Generate Keys*. The secret is shown once, and the app stores it in the phone's secure storage (Keychain / Android Keystore).
- **There is no shared "API user" with broad rights.** Every request runs as the supervisor, so all the normal server-side rules apply:
  - **Document permissions:** a supervisor can only reach orders assigned to them (`has_permission` / `permission_query_conditions`).
  - **The workflow:** only the transitions their role allows.
  - **The field guard:** only installation fields can change.
- **Revoking access:** regenerate the keys, or disable the user.
- **HTTPS only.** The secret travels in a header.

**For a production app with interactive sign-in**, I'd use **OAuth 2.0 (Authorization Code + PKCE)** with Frappe's built-in OAuth provider (*OAuth Client* doctype). The phone then holds short-lived bearer tokens with refresh, instead of a long-lived secret. The endpoints don't change: they only see `frappe.session.user`.

## Errors

| HTTP | When | `exc_type` |
|---|---|---|
| 401 | Missing, invalid or revoked credentials | `AuthenticationError` |
| 403 | Logged in, but not allowed (e.g. an order assigned to another supervisor) | `PermissionError` |
| 404 | Unknown Reno Order | `DoesNotExistError` |
| 409 | Not allowed from the order's current status, e.g. asking for `Closed`, or adding remarks before *Ready for Installation*. The message lists the statuses the user *can* move to | `InvalidStatusTransitionError` |
| 422 | Invalid input: empty remarks, a non-image or oversized upload | `InvalidInputError` |

Repeating a status the order already has returns 200 with no change, so a retry after a dropped mobile connection is harmless.

## Try it (curl)

```bash
SITE=http://localhost:8100
AUTH="Authorization: token <api_key>:<api_secret>"

curl -X POST "$SITE/api/method/reno_order.api.supervisor.update_installation_status" \
  -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"reno_order": "RO-00002", "status": "Installed"}'

curl -X POST "$SITE/api/method/reno_order.api.supervisor.add_installation_remarks" \
  -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"reno_order": "RO-00002", "remarks": "Installation completed successfully."}'

curl -X POST "$SITE/api/method/reno_order.api.supervisor.upload_site_photo" \
  -H "$AUTH" -F reno_order=RO-00002 -F file=@kitchen.jpg
```

Results from a real run against the demo site:
- no credentials → **403**
- wrong token → **401**
- `"status": "Closed"` → **409** *"…can't be changed to Closed. Allowed for you: Installed."*
- unknown order → **404**
- remarks, photo and Installed → **200**
- a few seconds later, the background worker had prepared the Delivery Note as the automation user

## Design notes
- `@frappe.whitelist(methods=["POST"])`: no GET side effects, and no `allow_guest`.
- **The status endpoint doesn't set the field.** It finds the workflow transition from the current status to the requested one *among those the user's roles allow* (`get_transitions`), then calls `apply_workflow`. So the API and the form follow exactly the same rules.
- **Marking Installed returns immediately.** The Delivery Note is prepared after commit by a background job (see `erpnext-integration.md`).
- **Photos are private files attached to the order.** Frappe then lets only users who can read the order download them.
