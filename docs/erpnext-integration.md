# ERPNext integration: order to cash (Part 3)

```
Reno Order ──(Create Sales Order)──► Sales Order ──► Delivery Note ──► Sales Invoice
   ▲   reno_order link field on every document; reverse links on the Reno Order   │
   └───────────────────────────────────────────────────────────────────────────────┘
```

## How the documents are connected
- A **`reno_order` link field** is added to Sales Order, Delivery Note and Sales Invoice (plus Work Order, Material Request and Purchase Order) by `setup/install.py`.
- The field is deliberately **not `no_copy`**. ERPNext's own mappers (`make_delivery_note`, `make_sales_invoice`) copy same-named fields, so the reference travels SO → DN → SI with **no custom mapping code**.
- **Reverse links** (`sales_order`, `delivery_note`, `sales_invoice` on the Reno Order) are kept by doc_events in `erpnext_flow/links.py`:
  - set on `after_insert`
  - cleared on `on_cancel` / `on_trash`
  - returns and credit notes are ignored, so they never replace the original link

  These fields are read-only for users, and the server restores them on every save.
- Standard ERPNext still owns the business logic: pricing, taxes, stock, accounting. The custom app only orchestrates and links.

## Where stock is reserved
- **Sales Order submit** increases `reserved_qty` in the item's Bin, so **projected qty** drops. This is a *soft* reservation: planning (reorder levels, Material Requests, the production plan) sees the demand, but it doesn't stop another Delivery Note from using the stock.
- For a *hard* reservation, enable **Stock Settings → Enable Stock Reservation** and tick **Reserve Stock** on the Sales Order. ERPNext then creates **Stock Reservation Entries** on submit, which block that quantity for this order until it's delivered.
- The Reno Order itself reserves nothing. It's a planning and workflow document.

## Where stock is actually reduced
- On **Delivery Note submit**: Stock Ledger Entries are posted, Bin `actual_qty` falls, and the reservation is released.
- If a Sales Invoice is made with **Update Stock** instead of a Delivery Note, the stock moves on the invoice. This app uses the Delivery Note route, because installation is a physical hand-over.
- A Delivery Note **draft** moves no stock. That's why the automation prepares a draft by default (`Reno Settings → Submit Delivery Note Automatically` is off). The warehouse checks it and submits.

## When accounting entries are created
| Document | GL entries |
|---|---|
| Reno Order, Sales Order | none |
| Delivery Note (submit) | with perpetual inventory: **Dr Cost of Goods Sold, Cr Stock In Hand** (warehouse account), at valuation rate |
| Sales Invoice (submit) | **Dr Debtors**; **Cr Sales/Income**, plus **Cr tax accounts** if taxes apply. The Reno discount carries over as the Sales Order's additional discount, so it reduces the income |
| Payment Entry (later) | Dr Bank/Cash, Cr Debtors |

## How cancellation behaves
- **Cancel in reverse order: Sales Invoice → Delivery Note → Sales Order → Reno Order.** Frappe refuses to cancel a document while a **submitted** document links to it (`LinkExistsError`). For example, a Reno Order can't be cancelled while its Sales Order is submitted (tested).
- **Cancelling a Delivery Note** reverses its stock and GL entries, and cancelling a Sales Invoice reverses its GL entries. Our hooks clear the Reno Order's link, so a corrected document can be created (tested).
- **The workflow allows Cancel only before Installed.** Once the kitchen is installed, the goods are with the customer, so reversals go through **returns and credit notes**. They keep a proper audit trail, and they don't overwrite the Reno Order's links.

## How duplicate downstream documents are prevented
| Document | Protection |
|---|---|
| Sales Order | `create_sales_order` takes a **row lock** (`SELECT … FOR UPDATE`) on the Reno Order, then checks for an active SO. A **Sales Order `validate` hook** enforces "one active SO per Reno Order" for SOs made any other way (UI, API, copy) |
| Delivery Note (automation) | queued **after commit**, **deduplicated by job id** in RQ, and the job **locks the order and reuses an existing DN** (idempotent, tested by running it twice) |
| Delivery Note / Sales Invoice (manual) | ERPNext's own **over-delivery / over-billing checks** against the Sales Order's `delivered_qty` / `billed_qty` (Stock / Accounts Settings allowances) stop delivering or billing more than was ordered |

## The automation on "Installed" (Part 2)
When the workflow moves an order to **Installed**, `on_update_after_submit` calls `queue_delivery_note()`:
1. It sets `delivery_status = Queued`, and `frappe.enqueue(..., enqueue_after_commit=True, job_id=..., deduplicate=True)`. The user's save returns immediately.
2. The worker job (`erpnext_flow/delivery.py`) runs as the configured **Automation User**, a service account, instead of the Site Supervisor, who has no stock permissions. **Permission checks stay on.**
3. It locks the order, reuses or creates the Delivery Note with ERPNext's `make_delivery_note`, and records `Prepared`.
4. **On failure** (e.g. the Sales Order isn't submitted yet):
   - a **savepoint rollback** undoes any half-done work
   - the order records `Failed`, the attempt count and the error message, and an Error Log entry is written
   - an **hourly job** retries up to 3 times, and **Retry** is available on the form afterwards

The demo video walks through this flow end to end.
