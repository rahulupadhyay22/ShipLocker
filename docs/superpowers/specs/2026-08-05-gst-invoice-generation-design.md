# Design: GST Invoice Generation on Payment

## Overview

When a shipment's payment is marked `paid` (via `VerifyPaymentView` or `RazorpayWebhookView` in `apps/payments/views.py`), automatically generate a GST-compliant invoice PDF, upload it to Supabase Storage, and record it so the existing "Download Invoice" Quick Action on the shipment detail page (`apps/shipments/views.py` `ShipmentDetailView`, `templates/shipments/detail.html`) picks it up with zero template changes.

No GST/invoice concept exists in the codebase today (verified via grep — only marketing copy in `templates/content/duties.html`). No PDF library is installed. This is new, isolated functionality layered on top of the existing `Shipment`, `ShippingRate`/`service_type`, `consolidation_fee`, and `_payment_summary()` work already shipped.

## Decisions (confirmed with user)

- **GST scope**: domestic shipments (`shipment.shipment_type == 'domestic'`) get real GST (CGST+SGST or IGST). International shipments still get an invoice PDF, but 0% / "Export of Service — Zero Rated".
- **PDF library**: `reportlab` (new dependency) — pure Python, no system libs to install on Railway/Render or Windows dev.
- **Company GST details**: new fields on `apps.notifications.models.AppSettings` (admin-editable singleton, same place as `site_name`/`warehouse_address`).
- **Invoice numbering**: sequential per financial year (Apr 1 – Mar 31), e.g. `INV/2026-27/0001`. Race-safe generation, same pattern as `generate_shipment_id`/`generate_shipment_doc_id` in `apps/shipments/models.py` (`select_for_update` on the last invoice of the current FY).
- **CGST/SGST vs IGST**: full split. Compare `AppSettings.company_state` to `shipment.state` — same state → CGST+SGST (half the configured rate each); different state → IGST (full rate).
- **Taxable amount** = `shipping_amount + storage_fee_amount + consolidation_fee_amount` (all service charges billed by ShipLocker for this shipment). GST (or the zero-rated note) applies to that sum, not to each line individually.

## Architecture (revised after review)

A signal-triggered call into a single service — **not** business logic inside the signal itself.

```
Shipment.payment_status → 'paid'
        │  (post_save signal, apps/shipments/signals.py — one line, thin)
        ▼
InvoiceService.generate_for_shipment(shipment, paid_at)   [apps/payments/services.py]
        │
        ├─ 1. build_charge_snapshot(shipment)              → {shipping, storage, consolidation}
        ├─ 2. calculate_gst(shipment, snapshot, settings)   → apps/payments/tax.py (isolated)
        ├─ 3. generate_pdf(context)                         → reportlab, returns bytes
        ├─ 4. upload_pdf(bytes, shipment)                   → reuses upload_shipment_document, returns storage path
        └─ 5. transaction.atomic(): create Invoice + ShipmentDocument together
```

Steps 3–4 happen **before** any DB write. Step 5 is the only DB mutation, wrapped in one transaction, so we never end up with an `Invoice` row pointing at a PDF that doesn't exist, or vice versa. If the DB write in step 5 fails after a successful upload, the result is an orphaned Supabase file (acceptable — matches standard practice for non-transactional external storage) but never an inconsistent DB record.

### Idempotency

`generate_for_shipment` first checks `Invoice.objects.filter(shipment=shipment).exists()`. If found, it logs and returns the existing invoice — no regeneration. This feature does not support regenerating/correcting an invoice; a future correction is a Credit/Debit Note, explicitly out of scope here (not designed against, but not built).

### Signal (thin, matches existing pattern)

`apps/shipments/signals.py` already has a `pre_save`/`post_save` pair that snapshots a field before save and compares after save to detect a real transition (used today for `tracking_number` changes). Add the same shape for `payment_status`:

```python
_original_payment_status = {}

@receiver(pre_save, sender=Shipment)
def _store_original_payment_status(sender, instance, **kwargs):
    if instance.pk:
        try:
            _original_payment_status[instance.pk] = Shipment.objects.get(pk=instance.pk).payment_status
        except Shipment.DoesNotExist:
            _original_payment_status[instance.pk] = ''

@receiver(post_save, sender=Shipment)
def _generate_invoice_on_paid(sender, instance, created, **kwargs):
    was = _original_payment_status.pop(instance.pk, '')
    if not created and was != 'paid' and instance.payment_status == 'paid':
        from apps.payments.services import InvoiceService
        try:
            InvoiceService.generate_for_shipment(instance, paid_at=instance.paid_at or timezone.now())
        except Exception:
            logger.exception(f"Invoice generation failed for shipment {instance.pk}")
```

The `try/except` matters: this runs *after* the shipment/payment save already committed (post_save). A PDF/Supabase failure must not raise out of the payment-verification request and must not be silently swallowed — it's logged loudly, and a manual admin action (below) exists as a backstop.

### Manual backstop

Add a `generate_invoice` admin action on `ShipmentAdmin` (`apps/shipments/admin.py`), same pattern as the existing `approve_declaration`/`mark_dispatched` actions, calling the identical `InvoiceService.generate_for_shipment`. Lets staff retry if the automatic path failed (e.g. transient Supabase error) without needing shell access.

## Model changes

**`apps/notifications/models.py` `AppSettings`** — new fields:
- `company_legal_name` (CharField)
- `company_gstin` (CharField)
- `company_pan` (CharField)
- `company_registered_address` (TextField)
- `company_state` (CharField) — compared against `shipment.state` for CGST/SGST vs IGST
- `gst_rate_percent` (DecimalField, default `18.00`)

**New model `Invoice`** in `apps/payments/models.py` (payments app already owns `Payment`/`StorageFee`, financial documents belong here):
```python
class Invoice(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shipment = models.OneToOneField('shipments.Shipment', on_delete=models.PROTECT, related_name='invoice')
    invoice_number = models.CharField(max_length=30, unique=True, db_index=True)
    invoice_date = models.DateTimeField()  # the payment's paid_at, NOT generation time

    # Snapshotted charges — independent of whatever the shipment looks like later
    shipping_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    storage_fee_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    consolidation_fee_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    taxable_amount = models.DecimalField(max_digits=10, decimal_places=2)

    is_zero_rated = models.BooleanField(default=False)  # international / export of service
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    cgst_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    sgst_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    igst_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)

    pdf_document_url = models.CharField(max_length=500)  # Supabase Storage path — Invoice owns this directly
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-invoice_date']

    def __str__(self):
        return self.invoice_number
```
`on_delete=models.PROTECT` — an issued invoice must never disappear via a shipment cascade-delete (accounting record).

**`ShipmentDocument`** — unchanged model. `InvoiceService` creates one row (`document_type='invoice'`, `document_url=<same path as Invoice.pdf_document_url>`) purely so the existing Quick Actions "Download Invoice" link keeps working. `Invoice` remains the source of truth; this row is a UI convenience pointer, not authoritative.

**Migrations**: one in `notifications` (AppSettings fields), one in `payments` (new `Invoice` model).

## Files touched

- `apps/notifications/models.py` — AppSettings fields
- `apps/notifications/admin.py` — expose new fields (check current registration before editing)
- `apps/payments/models.py` — `Invoice` model + `generate_invoice_number()` helper (FY-aware, `select_for_update`, same shape as `generate_shipment_id`)
- `apps/payments/tax.py` (new) — `calculate_gst(shipment, taxable_amount, settings)` pure function
- `apps/payments/services.py` — `InvoiceService` class/functions: `build_charge_snapshot`, `generate_pdf`, `upload_pdf`, `generate_for_shipment` (orchestrator with the idempotency check)
- `apps/payments/admin.py` — register `Invoice` (read-only list, links to PDF) for visibility/audit
- `apps/shipments/signals.py` — thin `payment_status` transition detector + call into `InvoiceService`
- `apps/shipments/admin.py` — `generate_invoice` manual action on `ShipmentAdmin`
- `requirements.txt` — add `reportlab`

## Files to create

- `apps/payments/tax.py`
- `apps/notifications/migrations/00XX_appsettings_gst_fields.py` (auto-generated)
- `apps/payments/migrations/00XX_invoice.py` (auto-generated)

## Rules for implementation

- Django ORM only, no raw SQL
- `Invoice.on_delete=models.PROTECT` on the shipment FK — never cascade-delete a financial record
- PDF generation and Supabase upload happen strictly before any DB write (steps 3–4 before step 5's transaction)
- Signal handler wraps `InvoiceService` call in `try/except`, logs via the existing logger, never raises into the payment-verification request/response cycle
- `calculate_gst` stays a pure function with no I/O — testable without DB/network
- Reuse `upload_shipment_document` (apps/locker/utils.py) for the actual Supabase upload — don't reimplement
- Log invoice generation (success/failure) through the `security` logger, consistent with other financial state changes in this codebase

## Out of scope (explicitly)

- Invoice regeneration/versioning — corrections are a future Credit/Debit Note feature, not built now
- Multi-currency GST (assume INR throughout, matches the rest of the app)
- GST for `international` shipments beyond the zero-rated note — no reverse-charge/export-specific compliance logic beyond what's stated

## Definition of done

- [ ] Migrations for `notifications` and `payments` apps apply cleanly
- [ ] Admin can fill in company GSTIN/PAN/legal name/registered address/state/GST rate on AppSettings
- [ ] Marking a **domestic** shipment's payment as paid (via the real Razorpay verify flow, not just admin) generates an `Invoice` row + PDF, with correct CGST+SGST split when `shipment.state == company_state`, correct IGST when different
- [ ] Marking an **international** shipment's payment as paid generates an invoice PDF showing 0% / zero-rated, no CGST/SGST/IGST amounts
- [ ] The shipment detail page's "Download Invoice" Quick Action links to the generated PDF (no template changes needed)
- [ ] Marking the same shipment paid twice (e.g. duplicate signal fire) does not create a second `Invoice` or duplicate `ShipmentDocument`
- [ ] Invoice numbers are sequential within a financial year and don't collide under concurrent generation (verify via the same race-safety approach as `generate_shipment_id`)
- [ ] `invoice_date` reflects the actual payment time, not whenever the PDF happened to be generated
- [ ] The manual "Generate Invoice" admin action successfully backfills an invoice for an already-paid shipment that has none
- [ ] Deleting a shipment with an invoice is blocked/protected at the DB level (`on_delete=PROTECT`)
