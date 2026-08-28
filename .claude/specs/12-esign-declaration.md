# Spec: E-Sign Customs + CamelTrunk Declaration

## Overview

Replace the download → print → sign → scan → upload PDF step in shipment creation with an in-app e-signature. The user reviews their parcel contents, value, and destination (auto-populated from existing `Parcel`/`Shipment` data), selects a declaration purpose, reads the CamelTrunk Customer Declaration & Authorization text, types their full name (pre-filled from `request.user.full_name` as a convenience, editable, not validated against anything), and clicks "Sign & Authorize Shipment." Identity is bound the same way any clickwrap agreement binds it: the authenticated session (`request.user`) + timestamp + IP address, not a name match — KYC is being removed from this app, so there is no verified name left to compare against. The server records a signature audit trail (typed name, timestamp, IP, declaration text version), generates a combined Customs Declaration + Authorization PDF with reportlab (same pattern as `InvoiceService.generate_pdf`), and uploads it to Supabase Storage as the existing `ShipmentDocument(document_type='customs')` record — so `DeclarationApprovalAdmin` needs zero changes.

**Legal note (flagged, not resolved here):** whether a typed-name + session/IP/timestamp clickwrap satisfies India's IT Act (Information Technology Act, 2000, incl. the Second Schedule / electronic signature provisions) for a customs declaration specifically — as opposed to a general ToS/consent flow — should be reviewed alongside the ToS/Privacy Policy legal review already queued. This spec does not make a legal-sufficiency determination; it implements the same mechanism most shipping/logistics SaaS use, pending that review.

The `declaration_file` upload field and its `<input type="file">` are removed from `create.html`.

## Depends on

- `apps.locker.models.Parcel` (`item_name`, `item_price`, `item_currency`, `category`, `customs_description`, `weight_kg` — already collected during parcel approval, no new fields needed there)
- `apps.accounts.models.User.full_name` (pre-fill convenience only, not validated against)
- `apps.locker.utils.upload_shipment_document` / `get_user_locker_id` (existing Supabase upload helper, reused as-is)
- `indiabox.validators` (existing validators, no new ones needed — no file upload to validate anymore)
- `indiabox.middleware._get_client_ip` pattern (`HTTP_X_FORWARDED_FOR` first entry, falling back to `REMOTE_ADDR`) — already implemented twice in `indiabox/middleware.py`; reuse the same logic for IP capture here rather than a third copy (extract to a shared helper if a third call site appears, not before)

## App(s) touched

- `apps/shipments` (model, view, template)

## Routes

No new routes. `ShipmentCreateView.post` (`POST /shipments/create/`) changes behavior: it now accepts `declaration_purpose`, `signature_name`, and `signature_agree` POST fields instead of a `declaration_file` upload. Same view, same ownership (`LoginRequiredMixin`, parcels filtered by `request.user.locker`).

## Model changes

Add to `Shipment` (`apps/shipments/models.py`):

```python
DECLARATION_PURPOSE_CHOICES = [
    ('gift', 'Gift'),
    ('sale', 'Sale'),
    ('sample', 'Commercial Sample'),
    ('return', 'Return'),
    ('other', 'Other'),
]

declaration_purpose = models.CharField(max_length=20, choices=DECLARATION_PURPOSE_CHOICES, blank=True)
declaration_signed_name = models.CharField(max_length=255, blank=True)
declaration_signed_at = models.DateTimeField(null=True, blank=True)
declaration_signed_ip = models.GenericIPAddressField(null=True, blank=True)
declaration_version = models.CharField(max_length=20, blank=True)
```

One migration. No changes to `ShipmentDocument`, `Parcel`, `KYCDocument`, or `AppSettings` — the generated PDF still lands as a normal `ShipmentDocument(document_type='customs')` row, which is all `DeclarationApprovalAdmin` and `approve_declaration()` already key off.

`declaration_version` is a hardcoded constant in code (e.g. `DECLARATION_TEXT_VERSION = 'v1'` next to the declaration text), stamped onto the shipment at signing time — gives an audit trail if the legal wording changes later, no admin-editable config needed for a v1.

## Templates

Modify:
- `templates/shipments/create.html` — replace the "Download, sign, and upload" block (lines ~363–383) with: itemized parcel table (reads from already-selected parcels via JS, same data source the page already uses to build the parcel selection UI), declaration purpose radio group, collapsible declaration text block, "I have read and agree" checkbox, typed full-name input (prefilled from `request.user.full_name`, editable — free-text attestation, not validated), "Sign & Authorize Shipment" submit button that disables itself on click (prevent double-submit via double-click or slow network retry). Remove `<input type="file" name="declaration_file">`.

Create: none — no new template files.

Static assets: none. `static/documents/Customer_Declaration_Authorization.pdf` stays on disk but is no longer linked from `create.html`; leave the file in place (harmless, and the customs-help page doesn't reference it either) rather than deleting an asset that isn't part of this change.

## Files to change

- `apps/shipments/models.py` — add declaration fields + `DECLARATION_TEXT_VERSION` constant + declaration text constant
- `apps/shipments/views.py` — `ShipmentCreateView.post`: drop `declaration_file` handling, add signature field capture (name non-blank check, IP via `X-Forwarded-For`, `select_for_update()` on parcels), call new PDF generation
- `apps/payments/services.py` or a new `apps/shipments/services.py` — add `DeclarationService.generate_pdf(shipment, parcels)` / `.upload_pdf(...)` following `InvoiceService`'s exact pattern (new module preferred — see Rules)
- `templates/shipments/create.html` — replace upload block with e-sign block
- `apps/shipments/migrations/000X_shipment_declaration_fields.py` — generated via `makemigrations`

## Files to create

- `apps/shipments/services.py` (if `DeclarationService` doesn't fit naturally into `apps/payments/services.py`, which is payment/invoice-scoped) — houses `DeclarationService.generate_pdf` / `.upload_pdf`, mirroring `InvoiceService`

## New dependencies

"No new dependencies." (`reportlab==4.2.5` already in `requirements.txt`, already used for invoice PDFs.)

## Rules for implementation

- Use Django ORM only
- No raw SQL unless absolutely necessary
- Ownership: `ShipmentCreateView` already scopes parcels to `request.user.locker`; no ownership mixin change needed since this is a `post()`-only creation view, not a `DetailView`
- Security logging through the `security` logger (`logging.getLogger('security')`, matching `apps/shipments/views.py` convention) — log signature events (name typed, timestamp, IP, shipment id) same as other POST actions in this file
- No name-match validation. `signature_name` is a required-non-blank free-text field (`request.POST.get('signature_name', '').strip()` — reject only if empty). It is an attestation, not an identity check; the actual identity binding is `request.user` (authenticated session) + `declaration_signed_at` + `declaration_signed_ip`, exactly how clickwrap agreements work elsewhere on the web. Do not add a KYC/full_name comparison — KYC is being removed from this app.
- IP capture: reuse the exact pattern already in `indiabox/middleware.py` (`_get_client_ip`) — `request.META.get('HTTP_X_FORWARDED_FOR')`, first entry (`.split(',')[0].strip()`), falling back to `request.META.get('REMOTE_ADDR')`. Do not use `REMOTE_ADDR` alone — on Railway/Render the app sits behind a reverse proxy, so `REMOTE_ADDR` would record the proxy's IP, not the client's. Before implementation, confirm Railway/Render actually populate `X-Forwarded-For` on requests to this app (check request logs or a `render.yaml`/`railway.toml` proxy note); the codebase already relies on this same header for rate limiting, which is a strong signal it's already verified to work in this deployment, but confirm rather than assume.
- Double-submission protection:
  - Client-side: the "Sign & Authorize Shipment" submit button disables itself immediately on click (prevents double-click and accidental resubmit).
  - Server-side: lock the parcels being consumed with `select_for_update()` inside the `transaction.atomic()` block, same as `ApproveParcelView.post` in `apps/locker/views.py` locks the parcel being approved. Two concurrent submits both querying `Parcel.objects.filter(id__in=parcel_ids, locker=locker, status='approved')` race on the same rows; `select_for_update()` serializes them, and the loser's re-check naturally fails (`parcel.status` is already `'shipped'` from the winner) and re-renders with "Invalid parcel selection" instead of creating a second shipment. This mirrors the existing status-guard pattern in this codebase rather than introducing a new "recent submission" cache/dedup mechanism.
- CSRF: standard Django form POST, no separate signature-widget JS library — a checkbox + text input is sufficient per the user's explicit ask ("like now we do esign on websites" = clickwrap, not a canvas/drawn signature)
- `declaration_signed_name`, `declaration_signed_at`, `declaration_signed_ip`, `declaration_purpose`, `declaration_version` are set exactly once, at shipment creation time in `ShipmentCreateView.post`. No other code path (admin action, signal, management command, API) may modify these fields afterward — they are a point-in-time signature record, not editable shipment metadata.
- Declaration text lives as a Python constant (not `AppSettings`) since it's versioned legal text reviewed before each change, not a runtime-editable admin field — bump `DECLARATION_TEXT_VERSION` whenever the wording changes
- Upload files to Supabase Storage via `upload_shipment_document`, unchanged path/bucket convention (`shipment/{locker_id}/customs_{display_id}_{hash}.pdf`)
- Templates extend `templates/base.html`, CSS variables from `static/css/main.css` only — match the existing `create.html` styling (`.modern-alert`, `.form-group`, `.btn` classes already used on that page)
- `validate_file_upload` import in `views.py` becomes dead for this path — remove the now-unused `declaration_file` validation branch, don't leave a dead `if False:`-style stub
- Transaction: signature capture + PDF generation + `ShipmentDocument` creation + `Shipment` creation stay inside the existing `with transaction.atomic():` block in `ShipmentCreateView.post`; if PDF generation raises, the whole shipment creation must roll back (unlike today's code, which only `logger.error`s and continues on upload failure — a failed e-sign PDF means the declaration was never actually recorded, so this must be a hard failure, not a soft one)

## Definition of done

- [ ] Creating a shipment with 1+ parcels shows an itemized table of item name, category, description, value, weight for each selected parcel, with a summed total
- [ ] Declaration purpose (Gift/Sale/Commercial Sample/Return/Other) is a required radio selection
- [ ] The CamelTrunk Customer Declaration & Authorization text is visible/expandable on the page before signing
- [ ] Submitting without checking "I agree" is rejected with a form error, no shipment created
- [ ] Submitting with a blank `signature_name` is rejected with a form error, no shipment created
- [ ] Submitting a `signature_name` that differs from `request.user.full_name` (e.g. a nickname or family member's name) is accepted — no name-match validation exists
- [ ] Submitting the form twice quickly (double-click, or two tabs) for the same parcels creates exactly one `Shipment`; the second attempt fails with "Invalid parcel selection" rather than creating a duplicate
- [ ] Submitting with agreement checked and a non-blank name creates the `Shipment` with `declaration_purpose`, `declaration_signed_name`, `declaration_signed_at` (server time), `declaration_signed_ip` (real client IP via `X-Forwarded-For`, not the proxy's), `declaration_version` populated
- [ ] A `ShipmentDocument(document_type='customs')` is created pointing at a generated PDF containing: shipment/recipient details, itemized parcel table, declaration purpose, full declaration text, signed name, and timestamp
- [ ] The PDF opens correctly from `DeclarationApprovalAdmin`'s "View Declaration" link with no admin-side code changes
- [ ] No `declaration_file` file input remains anywhere in `create.html`
- [ ] `static/documents/Customer_Declaration_Authorization.pdf` link is removed from `create.html`
- [ ] If PDF generation/upload fails, the entire shipment creation rolls back and the user sees an error (no orphaned `declaration_pending` shipment without a document)
