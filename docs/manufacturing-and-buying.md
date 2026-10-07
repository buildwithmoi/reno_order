# Manufacturing and buying (Parts 4 and 5)

Both scenarios use **standard ERPNext** documents and settings. The app adds only one thing: a `validate` hook that keeps the `reno_order` reference on documents ERPNext creates without its mapper (`erpnext_flow/links.py → inherit_reno_order`). On the Reno Order form, the **Connections** tab lists every Sales Order, Delivery Note, Sales Invoice, Work Order, Material Request and Purchase Order for that order.

**Demo data** (standard masters only, safe to run more than once):
```bash
bench --site <site> execute reno_order.setup.demo.setup_demo_data
```
It creates:
- the workstations, operations and cabinet BOM below
- opening stock for the raw materials and countertops (a *Stock Reconciliation → Opening Stock* against Temporary Opening)
- two hardware suppliers
- the Company's default WIP / finished-goods warehouses and **Default Operating Cost Account**

The **hinge set has no stock on purpose**, so it drives Part 5.

The figures below come from running both flows on a fresh site (2 cabinets, 4 hinge sets).

## Part 4: manufacturing the Kitchen Cabinet

### Set-up (masters)
| Master | Kitchen Cabinet (`KC-CAB-01`) |
|---|---|
| **Raw materials** (per cabinet) | Plywood ×3 @ 250 · Laminate ×2 @ 120 · Adhesive ×1 @ 60 · Hardware kit ×1 @ 40 = **1,090** |
| **Workstations** (hour rate from cost components) | Cutting Station 50/h (wages 30 + electricity 20) · Assembly Bench 40/h (wages 35 + rent 5) · Finishing Booth 45/h (wages 30 + consumables 15) |
| **Operations** (planned time per cabinet) | Cutting 60 min → Assembly 90 min → Finishing 45 min = **143.75** |
| **BOM** `BOM-KC-CAB-01-001` | *With Operations*, default BOM, **1,233.75 per cabinet** |
| **Company** | Default WIP warehouse *Work In Progress*, default finished goods *Stores*, **Default Operating Cost Account** *Manufacturing Overheads* (without it, ERPNext can't post the Manufacture entry) |

### The flow
```
Reno Order (Confirmed) → Sales Order (submitted)
   └─ Sales Order → Create → Work Order        (dialog: BOM + qty; one Work Order per manufactured line)
        Work Order: submit                      → 3 Job Cards, one per operation, in sequence
        Work Order: Start                       → Stock Entry "Material Transfer for Manufacture"  Stores → WIP
        Job Cards: time logs, complete          → Cutting, then Assembly, then Finishing (ERPNext enforces the order)
        Work Order: Finish                      → Stock Entry "Manufacture": raw materials out of WIP, cabinets into Stores
   └─ Delivery Note (Part 3) delivers the cabinets from Stores
```

### How the Reno Order connects to manufacturing
- The Work Order keeps ERPNext's own **`sales_order` + `sales_order_item`** link. That drives the Sales Order's "produced" quantities and reservations.
- **`reno_order`** sits on the Work Order header. ERPNext's *Create → Work Order* doesn't use the mapper, so the field would arrive empty. The hook copies it from the Sales Order.
- For many orders at once, a **Production Plan** (*Get Sales Orders*) creates the Work Orders. Each still carries its `sales_order`, so the same hook fills `reno_order`.
- Nothing in manufacturing itself is customized: BOM costing, job cards, backflush and valuation are all standard.

### Stock and accounting impact (measured: 2 cabinets)
| Step | Stock | Accounting (GL) |
|---|---|---|
| Work Order submitted | Plans 2 cabinets (*planned qty*) and reserves raw materials for production (*projected qty* falls) | None |
| **Material Transfer for Manufacture** | Raw materials move Stores → Work In Progress | Nothing net here, because both warehouses use the same *Stock In Hand* account. Separate warehouse accounts would show Dr WIP / Cr Stores |
| Job Cards | None. They record **actual time** per operation (Cutting 2 h, Assembly 3 h, Finishing 1.5 h) | None |
| **Manufacture** | Raw materials out of WIP (value 2,180); 2 cabinets into Stores at **1,233.75 each** (2,467.50) | Raw materials 2,180 and finished goods 2,467.50 both post to *Stock In Hand*, so the net entry is **Dr Stock In Hand 287.50 / Cr Manufacturing Overheads 287.50**: the labour and overhead absorbed into inventory |

The 287.50 operating cost uses the **job cards' actual time** × workstation rates: Cutting 2 h × 50 = 100, Assembly 3 h × 40 = 120, Finishing 1.5 h × 45 = 67.50. A job that took longer would make the cabinets cost more.

## Part 5: buying out-of-stock hinges

**Scenario:** a Reno Order line has 4 × *Soft-close Hinge Set* (`HW-HINGE-SET`), and the stock level is 0.

### The flow
```
Sales Order (submitted, reserves 4 hinges)
   └─ Create → Material Request (Purchase)          keep the hinge row; the cabinet is made, not bought
        └─ Create → Request for Quotation           two suppliers
             └─ Create → Supplier Quotation ×2      Accra Hardware 95, Tema Fittings 88
                  (Supplier Quotation Comparison report to choose)
                  └─ Create → Purchase Order        from the cheaper quotation (88)
                       └─ Create → Purchase Receipt     goods arrive
                            └─ Create → Purchase Invoice    supplier bills
```
Other standard ways to raise the Material Request:
- **Auto re-order:** set the item's re-order level, plus *Stock Settings → Auto Material Request*.
- **Production Plan → Get Raw Materials for Purchase:** when the hardware is a BOM component.

### Where stock and accounting impact happen (measured: 4 hinges @ 88)
| Document | Stock effect | Accounting effect |
|---|---|---|
| Sales Order | *Reserved* qty 4 → projected −4 (shortage visible) | None |
| Material Request | *Requested* qty 4 (projected back to 0) | None |
| RFQ, Supplier Quotation | None | None |
| Purchase Order | *Ordered* qty 4 (replaces the requested qty) | None: it's a commitment (budget checks can apply) |
| **Purchase Receipt** | **Actual qty +4**, valued at 88 (stock ledger entry) | **Dr Stock In Hand 352 / Cr Stock Received But Not Billed 352** |
| **Purchase Invoice** | None (stock was received by the receipt) | **Dr Stock Received But Not Billed 352 / Cr Creditors 352**: the supplier is now owed |
| Payment Entry (later) | None | Dr Creditors / Cr Bank |

- *Stock Received But Not Billed* is the clearing account between receipt and invoice. Its balance is goods received but not yet invoiced.
- If the invoice rate differs from the receipt rate, *Buying Settings → Set Landed Cost Based on Purchase Invoice Rate* decides what happens:
  - **On:** the received stock is revalued to the invoice rate (a repost of the receipt's valuation).
  - **Off:** the stock keeps the receipt rate, and the difference stays in the accounts.
- A Purchase Invoice with *Update Stock* skips the receipt and does both jobs at once (Dr Stock / Cr Creditors).

### How the Reno Order reference survives procurement
| Step | `reno_order` | How |
|---|---|---|
| Sales Order → Material Request | ✓ | ERPNext's mapper copies same-named fields |
| Material Request → RFQ → Supplier Quotation | not on these documents | An RFQ often gathers many requests for many suppliers, so a single header reference would be misleading |
| Supplier Quotation → Purchase Order | ✓ | The PO arrives blank. The hook reads `material_request` on the PO rows and takes that request's `reno_order` |
| Purchase Order → Receipt → Invoice | traced through rows | Each receipt and invoice row links its Purchase Order (and Material Request) |
| One PO for several Reno Orders | left blank, deliberately | The header can't name one order. Each row still traces back through its own Material Request |

## Tests (`reno_order/tests/test_manufacturing_buying.py`)
| Test | Asserts |
|---|---|
| `test_work_order_from_sales_order_gets_the_reno_order` | *Create → Work Order* (`make_work_orders`) gets the Reno Order |
| `test_material_request_from_sales_order_carries_the_reno_order` | The mapper carries it |
| `test_purchase_order_from_supplier_quotation_recovers_the_reno_order` | The full MR → RFQ → SQ → PO chain: blank after mapping, restored on save |
| `test_purchase_order_for_several_reno_orders_is_left_blank` | A consolidated PO isn't given a wrong reference |
| `test_an_explicit_reno_order_is_kept` | A value set by the user is never overwritten |
