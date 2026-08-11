# GST Invoice Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a shipment's payment is marked `paid`, automatically generate a GST-compliant invoice PDF, upload it to Supabase, and record it so the existing "Download Invoice" Quick Action keeps working.

**Architecture:** A thin `post_save` signal on `Shipment` (in `apps/shipments/signals.py`) detects a real transition into `payment_status='paid'` and calls a single entry point, `InvoiceService.generate_for_shipment(shipment, paid_at)` in `apps/payments/services.py`. That orchestrator calls four isolated, independently-testable steps — snapshot charges, calculate GST (pure function in `apps/payments/tax.py`), generate a PDF (`reportlab`), upload to Supabase — then does exactly one DB write (`Invoice` + `ShipmentDocument`, in one transaction) after both PDF steps have already succeeded.

**Tech Stack:** Django 5.2, `reportlab` (new dependency, pure-Python PDF generation, no system libs), existing Supabase Storage via `apps.locker.utils.upload_shipment_document`.

## Global Constraints

- Django ORM only, no raw SQL.
- `Invoice.shipment` uses `on_delete=models.PROTECT` — never cascade-delete a financial record.
- PDF generation + Supabase upload happen strictly before any DB write; the DB write (`Invoice` + `ShipmentDocument`) is one `transaction.atomic()` block.
- `calculate_gst` is a pure function — no DB access, no I/O — so it's testable without a database.
- Reuse `apps.locker.utils.upload_shipment_document` for the Supabase upload — do not reimplement upload logic.
- Compare shipment/company state values normalized (`.strip().upper()`) before deciding CGST/SGST vs IGST — never raw `==` on free-text state fields.
- `generate_invoice_number()` lives in `apps/payments/services.py`, not on the `Invoice` model — keep `apps/payments/models.py` free of business logic, same reasoning the spec applied throughout.
- Signal handler wraps the `InvoiceService` call in `try/except` and logs via `logging.getLogger(__name__)` (the logger already used in `apps/shipments/signals.py`) — a PDF/upload failure must never raise out of the payment-verification request.
- Financial/security-relevant log lines go through `logging.getLogger('security')` in `apps/payments/services.py` (already the logger used in that file).
- Full spec: `docs/superpowers/specs/2026-08-05-gst-invoice-generation-design.md`.

---

## Task 1: `reportlab` dependency

**Files:**
- Modify: `requirements.txt`

**Interfaces:**
- Produces: `reportlab` importable as `from reportlab.lib.pagesizes import A4` etc., used by Task 6.

- [ ] **Step 1: Add the dependency**

Add this line to `requirements.txt` (after `pillow==12.3.0`, alphabetically doesn't matter here — the file isn't sorted):

```
reportlab==4.2.5
```

- [ ] **Step 2: Install it**

Run: `pip install reportlab==4.2.5`
Expected: `Successfully installed reportlab-4.2.5` (or similar; reportlab has no system-level deps, so this should never require apt/brew packages)

- [ ] **Step 3: Verify import**

Run: `python -c "from reportlab.lib.pagesizes import A4; from reportlab.platypus import SimpleDocTemplate, Table; print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "chore: add reportlab for GST invoice PDF generation"
```

---

## Task 2: `AppSettings` GST fields

**Files:**
- Modify: `apps/notifications/models.py`
- Modify: `apps/notifications/admin.py`
- Create: `apps/notifications/migrations/00XX_appsettings_gst_fields.py` (auto-generated — number picks up from whatever's already in that directory)

**Interfaces:**
- Produces: `AppSettings.company_legal_name`, `.company_gstin`, `.company_pan`, `.company_registered_address`, `.company_state`, `.gst_rate_percent` (Decimal, default `18.00`) — all consumed by `apps/payments/tax.py` (Task 4) and `apps/payments/services.py` (Task 6) via `AppSettings.get_settings()`.

- [ ] **Step 1: Add the fields to the model**

In `apps/notifications/models.py`, find this block (around line 216-235):

```python
    # ===========================
    # SUPABASE STORAGE
    # ===========================
    supabase_url = models.CharField(
        max_length=200,
        blank=True,
        help_text="Supabase Project URL"
    )
    supabase_anon_key = models.CharField(
        max_length=500,
        blank=True,
        help_text="Supabase Anon/Public Key"
    )
    supabase_service_role_key = models.CharField(
        max_length=500,
        blank=True,
        help_text="Supabase Service Role Key (for server-side operations)"
    )
    
    # ===========================
    # METADATA
    # ===========================
    updated_at = models.DateTimeField(auto_now=True)
```

Insert a new section between the Supabase block and the Metadata block:

```python
    # ===========================
    # SUPABASE STORAGE
    # ===========================
    supabase_url = models.CharField(
        max_length=200,
        blank=True,
        help_text="Supabase Project URL"
    )
    supabase_anon_key = models.CharField(
        max_length=500,
        blank=True,
        help_text="Supabase Anon/Public Key"
    )
    supabase_service_role_key = models.CharField(
        max_length=500,
        blank=True,
        help_text="Supabase Service Role Key (for server-side operations)"
    )

    # ===========================
    # GST / INVOICE DETAILS
    # ===========================
    company_legal_name = models.CharField(
        max_length=255,
        blank=True,
        help_text="Registered legal business name shown on GST invoices"
    )
    company_gstin = models.CharField(
        max_length=20,
        blank=True,
        help_text="Your company's GSTIN (e.g., 36AAAAA0000A1Z5)"
    )
    company_pan = models.CharField(
        max_length=10,
        blank=True,
        help_text="Your company's PAN"
    )
    company_registered_address = models.TextField(
        blank=True,
        help_text="Registered business address shown on GST invoices"
    )
    company_state = models.CharField(
        max_length=100,
        blank=True,
        help_text="Your company's registered state (e.g., Telangana) — compared against the shipment's delivery state to decide CGST+SGST vs IGST"
    )
    gst_rate_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=18.00,
        help_text="GST rate applied to domestic shipment invoices (e.g., 18.00 for 18%)"
    )

    # ===========================
    # METADATA
    # ===========================
    updated_at = models.DateTimeField(auto_now=True)
```

- [ ] **Step 2: Generate the migration**

Run: `python manage.py makemigrations notifications`
Expected: `Migrations for 'notifications': apps\notifications\migrations\00XX_appsettings_company_legal_name_and_more.py` (or similar auto-generated name) listing the 6 new fields as `+ Add field ...`

- [ ] **Step 3: Apply it**

Run: `python manage.py migrate notifications`
Expected: `Applying notifications.00XX_..._and_more... OK`

- [ ] **Step 4: Expose the fields in admin**

In `apps/notifications/admin.py`, find the `fieldsets` tuple and this block:

```python
        ('☁️ Supabase Storage', {
            'fields': (
                'supabase_url',
                'supabase_anon_key',
                'supabase_service_role_key',
            ),
            'description': 'Supabase configuration for file storage',
            'classes': ('collapse',),
        }),
        ('ℹ️ Status', {
            'fields': ('integration_dashboard', 'updated_at',),
        }),
```

Insert a new fieldset between them:

```python
        ('☁️ Supabase Storage', {
            'fields': (
                'supabase_url',
                'supabase_anon_key',
                'supabase_service_role_key',
            ),
            'description': 'Supabase configuration for file storage',
            'classes': ('collapse',),
        }),
        ('🧾 GST / Invoice Details', {
            'fields': (
                'company_legal_name',
                'company_gstin',
                'company_pan',
                'company_registered_address',
                'company_state',
                'gst_rate_percent',
            ),
            'description': 'Shown on every generated GST invoice. company_state is compared against each domestic shipment\'s delivery state to decide CGST+SGST vs IGST.',
        }),
        ('ℹ️ Status', {
            'fields': ('integration_dashboard', 'updated_at',),
        }),
```

- [ ] **Step 5: Verify in admin**

Run: `python manage.py runserver`, visit `/manage-rb-panel/notifications/appsettings/1/change/`, confirm the new "🧾 GST / Invoice Details" section appears with all 6 fields, fill in test values (e.g. `company_legal_name="CamelTrunk Logistics Pvt Ltd"`, `company_gstin="36AAAAA0000A1Z5"`, `company_state="Telangana"`, `gst_rate_percent=18.00`), save, reload, confirm values persisted.

- [ ] **Step 6: Commit**

```bash
git add apps/notifications/models.py apps/notifications/admin.py apps/notifications/migrations/
git commit -m "feat: add company GST fields to AppSettings"
```

---

## Task 3: `Invoice` model

**Files:**
- Modify: `apps/payments/models.py`
- Modify: `apps/payments/admin.py`
- Create: `apps/payments/migrations/00XX_invoice.py` (auto-generated)

**Interfaces:**
- Consumes: nothing new (uses existing `Shipment` via string reference `'shipments.Shipment'`).
- Produces: `Invoice` model importable as `from apps.payments.models import Invoice`, with all fields listed below. Consumed by `apps/payments/services.py` (Task 6).

- [ ] **Step 1: Add the model**

Append to the end of `apps/payments/models.py`:

```python
class Invoice(models.Model):
    """GST invoice generated once a shipment's payment is marked paid.

    Every field that could change later on the Shipment/User/Payment is
    snapshotted here at generation time, so this record stays a correct
    historical document no matter what happens to the shipment afterward.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shipment = models.OneToOneField(
        Shipment, on_delete=models.PROTECT, related_name='invoice'
    )
    invoice_number = models.CharField(max_length=30, unique=True, db_index=True)
    invoice_date = models.DateTimeField(
        help_text="The payment's paid_at timestamp, not whenever the PDF happened to be generated"
    )

    # Snapshotted customer details
    customer_name = models.CharField(max_length=255)
    customer_email = models.EmailField(blank=True)
    billing_address = models.TextField()
    customer_gstin = models.CharField(max_length=20, blank=True)

    # Snapshotted payment reference
    payment_reference = models.CharField(max_length=100, blank=True)
    payment_method = models.CharField(max_length=50, blank=True)
    amount_paid = models.DecimalField(max_digits=10, decimal_places=2)

    # Snapshotted charges
    shipping_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    storage_fee_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    consolidation_fee_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    taxable_amount = models.DecimalField(max_digits=10, decimal_places=2)

    # GST breakdown
    is_zero_rated = models.BooleanField(default=False)
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    cgst_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    sgst_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    igst_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)

    pdf_document_url = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Invoice'
        verbose_name_plural = 'Invoices'
        ordering = ['-invoice_date']

    def __str__(self):
        return self.invoice_number
```

- [ ] **Step 2: Generate the migration**

Run: `python manage.py makemigrations payments`
Expected: `Migrations for 'payments': apps\payments\migrations\00XX_invoice.py` with a single `CreateModel` operation for `Invoice`

- [ ] **Step 3: Apply it**

Run: `python manage.py migrate payments`
Expected: `Applying payments.00XX_invoice... OK`

- [ ] **Step 4: Register in admin**

Add to `apps/payments/admin.py`, after the existing imports (`from .models import Payment, StorageFee` becomes `from .models import Payment, StorageFee, Invoice`), and append this class at the end of the file:

```python
@admin.register(Invoice)
class InvoiceAdmin(ModelAdmin):
    list_display = ['invoice_number', 'shipment', 'customer_name', 'invoice_date', 'total_amount', 'is_zero_rated']
    list_filter = ['is_zero_rated', 'invoice_date']
    search_fields = ['invoice_number', 'shipment__display_id', 'customer_name', 'customer_email']
    raw_id_fields = ['shipment']
    readonly_fields = [
        'shipment', 'invoice_number', 'invoice_date',
        'customer_name', 'customer_email', 'billing_address', 'customer_gstin',
        'payment_reference', 'payment_method', 'amount_paid',
        'shipping_amount', 'storage_fee_amount', 'consolidation_fee_amount', 'taxable_amount',
        'is_zero_rated', 'gst_rate', 'cgst_amount', 'sgst_amount', 'igst_amount', 'total_amount',
        'pdf_document_url', 'created_at',
    ]
    date_hierarchy = 'invoice_date'
    list_per_page = 25

    def download_link(self, obj):
        if not obj.pdf_document_url:
            return '-'
        from apps.locker.utils import get_signed_shipment_doc_url
        try:
            signed_url = get_signed_shipment_doc_url(obj.pdf_document_url)
            return format_html('<a href="{}" target="_blank">📄 Download PDF</a>', signed_url)
        except Exception:
            return 'Unavailable'
    download_link.short_description = 'PDF'

    fieldsets = (
        ('Invoice', {
            'fields': ('shipment', 'invoice_number', 'invoice_date', 'download_link')
        }),
        ('Customer', {
            'fields': ('customer_name', 'customer_email', 'billing_address', 'customer_gstin')
        }),
        ('Payment', {
            'fields': ('payment_reference', 'payment_method', 'amount_paid')
        }),
        ('Charges', {
            'fields': ('shipping_amount', 'storage_fee_amount', 'consolidation_fee_amount', 'taxable_amount')
        }),
        ('GST', {
            'fields': ('is_zero_rated', 'gst_rate', 'cgst_amount', 'sgst_amount', 'igst_amount', 'total_amount')
        }),
    )
    readonly_fields = readonly_fields + ['download_link']

    def has_add_permission(self, request):
        return False  # Invoices are only created by InvoiceService

    def has_delete_permission(self, request, obj=None):
        return False  # PROTECT on the FK already blocks this; admin shouldn't offer it either
```

- [ ] **Step 5: Verify**

Run: `python manage.py check`
Expected: `System check identified no issues (0 silenced).`

- [ ] **Step 6: Commit**

```bash
git add apps/payments/models.py apps/payments/admin.py apps/payments/migrations/
git commit -m "feat: add Invoice model for GST invoice records"
```

---

## Task 4: `calculate_gst` (pure function)

**Files:**
- Create: `apps/payments/tax.py`
- Test: `apps/payments/tests.py`

**Interfaces:**
- Consumes: a `shipment`-like object with `.shipment_type` and `.state`; a `taxable_amount` (Decimal); a `settings`-like object with `.gst_rate_percent` and `.company_state` (duck-typed — the real caller passes an `AppSettings` instance, but tests can pass a simple namespace).
- Produces: `calculate_gst(shipment, taxable_amount, settings) -> dict` with keys `is_zero_rated` (bool), `gst_rate` (Decimal), `cgst_amount` (Decimal), `sgst_amount` (Decimal), `igst_amount` (Decimal), `total_amount` (Decimal). Consumed by `apps/payments/services.py` (Task 6).

- [ ] **Step 1: Write the failing tests**

Create `apps/payments/tests.py`:

```python
from decimal import Decimal
from types import SimpleNamespace

from django.test import TestCase

from apps.payments.tax import calculate_gst


def _settings(company_state='Telangana', gst_rate_percent=Decimal('18.00')):
    return SimpleNamespace(company_state=company_state, gst_rate_percent=gst_rate_percent)


def _shipment(shipment_type='domestic', state='Telangana'):
    return SimpleNamespace(shipment_type=shipment_type, state=state)


class CalculateGstTests(TestCase):
    def test_international_is_zero_rated(self):
        result = calculate_gst(_shipment(shipment_type='international'), Decimal('1000.00'), _settings())
        self.assertTrue(result['is_zero_rated'])
        self.assertEqual(result['gst_rate'], Decimal('0.00'))
        self.assertEqual(result['cgst_amount'], Decimal('0.00'))
        self.assertEqual(result['sgst_amount'], Decimal('0.00'))
        self.assertEqual(result['igst_amount'], Decimal('0.00'))
        self.assertEqual(result['total_amount'], Decimal('1000.00'))

    def test_domestic_same_state_splits_cgst_sgst(self):
        result = calculate_gst(
            _shipment(shipment_type='domestic', state='Telangana'),
            Decimal('1000.00'),
            _settings(company_state='Telangana', gst_rate_percent=Decimal('18.00')),
        )
        self.assertFalse(result['is_zero_rated'])
        self.assertEqual(result['cgst_amount'], Decimal('90.00'))
        self.assertEqual(result['sgst_amount'], Decimal('90.00'))
        self.assertEqual(result['igst_amount'], Decimal('0.00'))
        self.assertEqual(result['total_amount'], Decimal('1180.00'))

    def test_domestic_same_state_case_and_whitespace_insensitive(self):
        result = calculate_gst(
            _shipment(shipment_type='domestic', state='  telangana  '),
            Decimal('1000.00'),
            _settings(company_state='TELANGANA', gst_rate_percent=Decimal('18.00')),
        )
        self.assertEqual(result['cgst_amount'], Decimal('90.00'))
        self.assertEqual(result['sgst_amount'], Decimal('90.00'))
        self.assertEqual(result['igst_amount'], Decimal('0.00'))

    def test_domestic_different_state_uses_igst(self):
        result = calculate_gst(
            _shipment(shipment_type='domestic', state='Maharashtra'),
            Decimal('1000.00'),
            _settings(company_state='Telangana', gst_rate_percent=Decimal('18.00')),
        )
        self.assertEqual(result['cgst_amount'], Decimal('0.00'))
        self.assertEqual(result['sgst_amount'], Decimal('0.00'))
        self.assertEqual(result['igst_amount'], Decimal('180.00'))
        self.assertEqual(result['total_amount'], Decimal('1180.00'))
```

- [ ] **Step 2: Run to verify it fails**

Run: `python manage.py test apps.payments.tests.CalculateGstTests -v 2`
Expected: `ModuleNotFoundError: No module named 'apps.payments.tax'` (or `ImportError`)

- [ ] **Step 3: Implement `apps/payments/tax.py`**

```python
"""Pure GST calculation — no DB access, no I/O. Isolated so future tax rule
changes (new states, new rates, exemptions) only touch this file."""

from decimal import Decimal, ROUND_HALF_UP


def _normalize_state(value):
    return (value or '').strip().upper()


def calculate_gst(shipment, taxable_amount, settings):
    """Compute the GST breakdown for a shipment's taxable amount.

    International shipments are zero-rated (export of service). Domestic
    shipments get CGST+SGST if the shipment's delivery state matches the
    company's registered state (both compared normalized), otherwise IGST.
    """
    taxable_amount = Decimal(str(taxable_amount))

    if shipment.shipment_type != 'domestic':
        return {
            'is_zero_rated': True,
            'gst_rate': Decimal('0.00'),
            'cgst_amount': Decimal('0.00'),
            'sgst_amount': Decimal('0.00'),
            'igst_amount': Decimal('0.00'),
            'total_amount': taxable_amount,
        }

    gst_rate = Decimal(str(settings.gst_rate_percent or 0))
    same_state = _normalize_state(shipment.state) == _normalize_state(settings.company_state)

    cgst_amount = Decimal('0.00')
    sgst_amount = Decimal('0.00')
    igst_amount = Decimal('0.00')

    if same_state:
        half_rate = gst_rate / 2
        cgst_amount = (taxable_amount * half_rate / 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        sgst_amount = (taxable_amount * half_rate / 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    else:
        igst_amount = (taxable_amount * gst_rate / 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    total_amount = (taxable_amount + cgst_amount + sgst_amount + igst_amount).quantize(
        Decimal('0.01'), rounding=ROUND_HALF_UP
    )

    return {
        'is_zero_rated': False,
        'gst_rate': gst_rate,
        'cgst_amount': cgst_amount,
        'sgst_amount': sgst_amount,
        'igst_amount': igst_amount,
        'total_amount': total_amount,
    }
```

- [ ] **Step 4: Run to verify it passes**

Run: `python manage.py test apps.payments.tests.CalculateGstTests -v 2`
Expected: `Ran 4 tests in ...s\n\nOK`

- [ ] **Step 5: Commit**

```bash
git add apps/payments/tax.py apps/payments/tests.py
git commit -m "feat: add pure GST calculation function with tests"
```

---

## Task 5: `generate_invoice_number` (FY-aware, race-safe)

**Files:**
- Modify: `apps/payments/services.py`
- Modify: `apps/payments/tests.py`

**Interfaces:**
- Consumes: `Invoice` model (Task 3).
- Produces: `generate_invoice_number(invoice_date) -> str`, format `INV/<FY>/<0001>` where FY spans Apr 1 – Mar 31 (e.g. `INV/2026-27/0001`). Consumed by `apps/payments/services.py`'s `InvoiceService.generate_for_shipment` (Task 6).

- [ ] **Step 1: Write the failing tests**

Add to `apps/payments/tests.py` (append; keep the existing `CalculateGstTests` class above):

```python
from datetime import datetime

from django.utils import timezone

from apps.payments.services import generate_invoice_number
from apps.payments.models import Invoice
from apps.accounts.models import User
from apps.shipments.models import Shipment


def _make_shipment(user):
    return Shipment.objects.create(
        user=user, shipment_type='domestic', status='declaration_pending',
        recipient_name='Test Recipient', recipient_phone='9999999999',
        address_line1='Addr', city='Hyderabad', state='Telangana',
        postal_code='500001', country='India',
    )


class GenerateInvoiceNumberTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email='invoice-number-test@example.com', is_active=True)

    def test_sequential_within_same_financial_year(self):
        first_date = timezone.make_aware(datetime(2026, 6, 1))
        second_date = timezone.make_aware(datetime(2026, 9, 1))

        first_number = generate_invoice_number(first_date)
        Invoice.objects.create(
            shipment=_make_shipment(self.user), invoice_number=first_number,
            invoice_date=first_date, customer_name='A', billing_address='addr',
            amount_paid=Decimal('100.00'), taxable_amount=Decimal('100.00'), total_amount=Decimal('100.00'),
        )
        second_number = generate_invoice_number(second_date)

        self.assertEqual(first_number, 'INV/2026-27/0001')
        self.assertEqual(second_number, 'INV/2026-27/0002')

    def test_new_financial_year_resets_prefix(self):
        fy_2026_date = timezone.make_aware(datetime(2026, 6, 1))
        fy_2027_date = timezone.make_aware(datetime(2027, 5, 1))  # FY 2027-28, since FY starts Apr 1

        fy_2026_number = generate_invoice_number(fy_2026_date)
        Invoice.objects.create(
            shipment=_make_shipment(self.user), invoice_number=fy_2026_number,
            invoice_date=fy_2026_date, customer_name='A', billing_address='addr',
            amount_paid=Decimal('100.00'), taxable_amount=Decimal('100.00'), total_amount=Decimal('100.00'),
        )
        fy_2027_number = generate_invoice_number(fy_2027_date)

        self.assertEqual(fy_2026_number, 'INV/2026-27/0001')
        self.assertEqual(fy_2027_number, 'INV/2027-28/0001')

    def test_march_is_still_previous_financial_year(self):
        march_date = timezone.make_aware(datetime(2027, 3, 15))
        number = generate_invoice_number(march_date)
        self.assertEqual(number, 'INV/2026-27/0001')
```

Add `from decimal import Decimal` at the top of `apps/payments/tests.py` if not already there from Task 4 (it already is, from `CalculateGstTests` — no duplicate import needed, just confirm it's present once at the top of the file).

- [ ] **Step 2: Run to verify it fails**

Run: `python manage.py test apps.payments.tests.GenerateInvoiceNumberTests -v 2`
Expected: `ImportError: cannot import name 'generate_invoice_number' from 'apps.payments.services'`

- [ ] **Step 3: Implement `generate_invoice_number` in `apps/payments/services.py`**

Add `from django.db import transaction` to the imports at the top of `apps/payments/services.py` (it currently only imports `hmac, hashlib, logging`, `Decimal`). Then append this to the end of the file:

```python
def _financial_year_label(invoice_date):
    """FY runs Apr 1 - Mar 31. E.g. any date in Apr 2026-Mar 2027 -> '2026-27'."""
    year = invoice_date.year
    if invoice_date.month >= 4:
        return f"{year}-{str(year + 1)[-2:]}"
    return f"{year - 1}-{str(year)[-2:]}"


def generate_invoice_number(invoice_date):
    """Sequential invoice number within a financial year: INV/2026-27/0001.
    Race-safe via select_for_update, same pattern as generate_shipment_id
    in apps/shipments/models.py."""
    from .models import Invoice

    prefix = f"INV/{_financial_year_label(invoice_date)}/"

    with transaction.atomic():
        last = (
            Invoice.objects
            .select_for_update()
            .filter(invoice_number__startswith=prefix)
            .order_by('-invoice_number')
            .first()
        )
        if last:
            try:
                num = int(last.invoice_number.rsplit('/', 1)[1]) + 1
            except (ValueError, IndexError):
                num = Invoice.objects.filter(invoice_number__startswith=prefix).count() + 1
        else:
            num = 1

    return f"{prefix}{num:04d}"
```

- [ ] **Step 4: Run to verify it passes**

Run: `python manage.py test apps.payments.tests.GenerateInvoiceNumberTests -v 2`
Expected: `Ran 3 tests in ...s\n\nOK`

- [ ] **Step 5: Commit**

```bash
git add apps/payments/services.py apps/payments/tests.py
git commit -m "feat: add FY-aware sequential invoice numbering"
```

---

## Task 6: `InvoiceService` orchestrator

**Files:**
- Modify: `apps/payments/services.py`
- Modify: `apps/payments/tests.py`

**Interfaces:**
- Consumes: `calculate_gst` (Task 4), `generate_invoice_number` (Task 5), `Invoice` model (Task 3), `apps.shipments.views._payment_summary(shipment)` (already exists — returns `{'shipping_amount', 'storage_fee_total', 'consolidation_fee', ...}`), `apps.locker.utils.upload_shipment_document(file, locker_id, shipment_display_id, doc_type)` and `get_user_locker_id(user)` (already exist), `apps.notifications.models.AppSettings.get_settings()` (already exists), `apps.shipments.models.ShipmentDocument` (already exists).
- Produces: `InvoiceService.generate_for_shipment(shipment, paid_at=None) -> Invoice`. Consumed by the signal (Task 7) and the manual admin action (Task 8).

- [ ] **Step 1: Write the failing tests**

Add to `apps/payments/tests.py`:

```python
from unittest.mock import patch

from apps.notifications.models import AppSettings
from apps.payments.models import Payment
from apps.shipments.models import ShipmentDocument
from apps.payments.services import InvoiceService


class InvoiceServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email='invoice-service-test@example.com', is_active=True)
        settings = AppSettings.get_settings()
        settings.company_legal_name = 'CamelTrunk Logistics Pvt Ltd'
        settings.company_gstin = '36AAAAA0000A1Z5'
        settings.company_pan = 'AAAAA0000A'
        settings.company_registered_address = 'Hyderabad, Telangana, India'
        settings.company_state = 'Telangana'
        settings.gst_rate_percent = Decimal('18.00')
        settings.save()

        self.shipment = _make_shipment(self.user)
        self.shipment.shipping_cost = Decimal('1000.00')
        self.shipment.payment_status = 'paid'
        self.shipment.save()

        Payment.objects.create(
            user=self.user, shipment=self.shipment, amount=Decimal('1000.00'),
            payment_method='razorpay', status='captured',
            razorpay_payment_id='pay_test123', paid_at=timezone.now(),
        )

    def test_generate_for_shipment_creates_invoice_and_document(self):
        invoice = InvoiceService.generate_for_shipment(self.shipment, paid_at=timezone.now())

        self.assertIsNotNone(invoice)
        self.assertEqual(invoice.shipment, self.shipment)
        self.assertTrue(invoice.invoice_number.startswith('INV/'))
        self.assertEqual(invoice.customer_name, 'Test Recipient')
        self.assertEqual(invoice.payment_reference, 'pay_test123')
        self.assertEqual(invoice.shipping_amount, Decimal('1000.00'))
        self.assertEqual(invoice.cgst_amount, Decimal('90.00'))
        self.assertEqual(invoice.sgst_amount, Decimal('90.00'))
        self.assertTrue(invoice.pdf_document_url)

        doc = ShipmentDocument.objects.get(shipment=self.shipment, document_type='invoice')
        self.assertEqual(doc.document_url, invoice.pdf_document_url)

    def test_generate_for_shipment_is_idempotent(self):
        first = InvoiceService.generate_for_shipment(self.shipment, paid_at=timezone.now())
        second = InvoiceService.generate_for_shipment(self.shipment, paid_at=timezone.now())

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Invoice.objects.filter(shipment=self.shipment).count(), 1)
        self.assertEqual(ShipmentDocument.objects.filter(shipment=self.shipment, document_type='invoice').count(), 1)

    def test_upload_failure_leaves_no_partial_record(self):
        with patch('apps.payments.services.InvoiceService.upload_pdf', side_effect=Exception('Supabase timeout')):
            with self.assertRaises(Exception):
                InvoiceService.generate_for_shipment(self.shipment, paid_at=timezone.now())

        self.assertEqual(Invoice.objects.filter(shipment=self.shipment).count(), 0)
        self.assertEqual(ShipmentDocument.objects.filter(shipment=self.shipment, document_type='invoice').count(), 0)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python manage.py test apps.payments.tests.InvoiceServiceTests -v 2`
Expected: `ImportError: cannot import name 'InvoiceService' from 'apps.payments.services'`

- [ ] **Step 3: Implement `InvoiceService` in `apps/payments/services.py`**

Append to the end of `apps/payments/services.py`:

```python
def build_charge_snapshot(shipment):
    """{'shipping_amount', 'storage_fee_amount', 'consolidation_fee_amount'}
    reusing the exact same totals already shown on the shipment detail page."""
    from apps.shipments.views import _payment_summary

    summary = _payment_summary(shipment)
    return {
        'shipping_amount': summary['shipping_amount'],
        'storage_fee_amount': summary['storage_fee_total'],
        'consolidation_fee_amount': summary['consolidation_fee'],
    }


def build_customer_snapshot(shipment):
    address_lines = [shipment.address_line1]
    if shipment.address_line2:
        address_lines.append(shipment.address_line2)
    address_lines.append(f"{shipment.city}, {shipment.state} {shipment.postal_code}")
    address_lines.append(shipment.country)
    return {
        'customer_name': shipment.recipient_name,
        'customer_email': shipment.recipient_email,
        'billing_address': '\n'.join(address_lines),
    }


class InvoiceService:
    """Single entry point for GST invoice generation. All the real work
    (snapshot, tax calc, PDF, upload, DB write) is coordinated from here —
    the calling signal/admin-action stays a one-line call into this class."""

    @staticmethod
    def generate_pdf(context):
        import io
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.units import mm
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=20 * mm, bottomMargin=20 * mm)
        styles = getSampleStyleSheet()
        story = []

        story.append(Paragraph(context['company_legal_name'] or 'CamelTrunk', styles['Title']))
        story.append(Paragraph((context['company_registered_address'] or '').replace('\n', '<br/>'), styles['Normal']))
        story.append(Paragraph(
            f"GSTIN: {context['company_gstin'] or '-'} | PAN: {context['company_pan'] or '-'}",
            styles['Normal'],
        ))
        story.append(Spacer(1, 10 * mm))

        story.append(Paragraph(f"Invoice Number: {context['invoice_number']}", styles['Heading2']))
        story.append(Paragraph(f"Invoice Date: {context['invoice_date'].strftime('%d %b %Y')}", styles['Normal']))
        story.append(Spacer(1, 5 * mm))

        story.append(Paragraph('Bill To:', styles['Heading3']))
        story.append(Paragraph(context['customer_name'], styles['Normal']))
        story.append(Paragraph(context['billing_address'].replace('\n', '<br/>'), styles['Normal']))
        if context.get('customer_gstin'):
            story.append(Paragraph(f"GSTIN: {context['customer_gstin']}", styles['Normal']))
        story.append(Spacer(1, 8 * mm))

        rows = [['Description', 'Amount (INR)']]
        rows.append(['Shipping Charges', f"{context['shipping_amount']:.2f}"])
        if context['storage_fee_amount'] > 0:
            rows.append(['Storage Fee', f"{context['storage_fee_amount']:.2f}"])
        if context['consolidation_fee_amount'] > 0:
            rows.append(['Consolidation Fee', f"{context['consolidation_fee_amount']:.2f}"])
        rows.append(['Taxable Amount', f"{context['taxable_amount']:.2f}"])

        if context['is_zero_rated']:
            rows.append(['GST', 'Export of Service — Zero Rated (0%)'])
        else:
            if context['cgst_amount'] > 0:
                half_rate = context['gst_rate'] / 2
                rows.append([f"CGST @ {half_rate}%", f"{context['cgst_amount']:.2f}"])
                rows.append([f"SGST @ {half_rate}%", f"{context['sgst_amount']:.2f}"])
            if context['igst_amount'] > 0:
                rows.append([f"IGST @ {context['gst_rate']}%", f"{context['igst_amount']:.2f}"])

        rows.append(['Total Amount', f"{context['total_amount']:.2f}"])

        table = Table(rows, colWidths=[120 * mm, 50 * mm])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#003746')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('LINEABOVE', (0, -1), (-1, -1), 1, colors.black),
        ]))
        story.append(table)
        story.append(Spacer(1, 8 * mm))

        if context.get('payment_reference'):
            story.append(Paragraph(
                f"Paid via {context.get('payment_method') or 'online payment'} — Reference: {context['payment_reference']}",
                styles['Normal'],
            ))

        doc.build(story)
        return buffer.getvalue()

    @staticmethod
    def upload_pdf(pdf_bytes, shipment):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from apps.locker.utils import upload_shipment_document, get_user_locker_id

        locker_id = get_user_locker_id(shipment.user)
        filename = f"invoice_{shipment.display_id}.pdf"
        uploaded_file = SimpleUploadedFile(filename, pdf_bytes, content_type='application/pdf')
        return upload_shipment_document(uploaded_file, locker_id, shipment.display_id, 'invoice')

    @staticmethod
    def generate_for_shipment(shipment, paid_at=None):
        from django.utils import timezone
        from django.db import transaction
        from .models import Invoice, Payment
        from apps.shipments.models import ShipmentDocument
        from apps.notifications.models import AppSettings

        existing = Invoice.objects.filter(shipment=shipment).first()
        if existing:
            logger.info(f"Invoice already exists for shipment {shipment.pk} ({existing.invoice_number}), skipping")
            return existing

        invoice_date = paid_at or timezone.now()
        settings = AppSettings.get_settings()

        charges = build_charge_snapshot(shipment)
        taxable_amount = (
            charges['shipping_amount'] + charges['storage_fee_amount'] + charges['consolidation_fee_amount']
        )
        gst = calculate_gst(shipment, taxable_amount, settings)
        customer = build_customer_snapshot(shipment)

        payment = Payment.objects.filter(
            shipment=shipment, status='captured'
        ).order_by('-paid_at').first()

        invoice_number = generate_invoice_number(invoice_date)

        pdf_context = {
            'company_legal_name': settings.company_legal_name,
            'company_registered_address': settings.company_registered_address,
            'company_gstin': settings.company_gstin,
            'company_pan': settings.company_pan,
            'invoice_number': invoice_number,
            'invoice_date': invoice_date,
            'customer_gstin': '',
            **customer,
            **charges,
            'taxable_amount': taxable_amount,
            **gst,
            'payment_reference': payment.razorpay_payment_id if payment else '',
            'payment_method': payment.get_payment_method_display() if payment else '',
        }

        pdf_bytes = InvoiceService.generate_pdf(pdf_context)
        pdf_path = InvoiceService.upload_pdf(pdf_bytes, shipment)

        with transaction.atomic():
            invoice = Invoice.objects.create(
                shipment=shipment,
                invoice_number=invoice_number,
                invoice_date=invoice_date,
                customer_name=customer['customer_name'],
                customer_email=customer['customer_email'],
                billing_address=customer['billing_address'],
                customer_gstin='',
                payment_reference=payment.razorpay_payment_id if payment else '',
                payment_method=payment.get_payment_method_display() if payment else '',
                amount_paid=payment.amount if payment else gst['total_amount'],
                shipping_amount=charges['shipping_amount'],
                storage_fee_amount=charges['storage_fee_amount'],
                consolidation_fee_amount=charges['consolidation_fee_amount'],
                taxable_amount=taxable_amount,
                is_zero_rated=gst['is_zero_rated'],
                gst_rate=gst['gst_rate'],
                cgst_amount=gst['cgst_amount'],
                sgst_amount=gst['sgst_amount'],
                igst_amount=gst['igst_amount'],
                total_amount=gst['total_amount'],
                pdf_document_url=pdf_path,
            )
            ShipmentDocument.objects.create(
                shipment=shipment,
                document_type='invoice',
                document_url=pdf_path,
            )

        logger.info(f"Invoice {invoice_number} generated for shipment {shipment.pk}")
        return invoice
```

- [ ] **Step 4: Run to verify it passes**

Run: `python manage.py test apps.payments.tests.InvoiceServiceTests -v 2`
Expected: `Ran 3 tests in ...s\n\nOK`

Note on `test_upload_failure_leaves_no_partial_record`: this patches `upload_pdf` to raise, which happens *before* the `transaction.atomic()` block in `generate_for_shipment` — so no DB write is ever attempted, confirming the ordering the spec requires (PDF steps must fully succeed before any DB write).

- [ ] **Step 5: Run the full payments test suite**

Run: `python manage.py test apps.payments -v 2`
Expected: `Ran 11 tests in ...s\n\nOK` (4 from Task 4 + 3 from Task 5 + 3 from Task 6, adjust count if any test above changed)

- [ ] **Step 6: Commit**

```bash
git add apps/payments/services.py apps/payments/tests.py
git commit -m "feat: add InvoiceService orchestrator (PDF generation, upload, DB write)"
```

---

## Task 7: Signal wiring (`payment_status` → `paid`)

**Files:**
- Modify: `apps/shipments/signals.py`
- Modify: `apps/payments/tests.py`

**Interfaces:**
- Consumes: `InvoiceService.generate_for_shipment` (Task 6).
- Produces: an automatic invoice whenever `Shipment.payment_status` transitions into `'paid'` via a normal `.save()` call — exercised end-to-end by the real `VerifyPaymentView`/`RazorpayWebhookView` flows without any changes to those views.

- [ ] **Step 1: Write the failing test**

Add to `apps/payments/tests.py` (uses the `Invoice`, `AppSettings`, `Payment`, `patch`, `timezone`, `Decimal`, and `_make_shipment` names already imported/defined earlier in this same file from Tasks 4-6 — no new imports needed):

```python
class ShipmentPaidSignalTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email='signal-test@example.com', is_active=True)
        settings = AppSettings.get_settings()
        settings.company_state = 'Telangana'
        settings.gst_rate_percent = Decimal('18.00')
        settings.save()

        self.shipment = _make_shipment(self.user)
        self.shipment.shipping_cost = Decimal('500.00')
        self.shipment.save()

    def test_marking_shipment_paid_generates_invoice(self):
        self.assertEqual(Invoice.objects.filter(shipment=self.shipment).count(), 0)

        self.shipment.payment_status = 'paid'
        self.shipment.paid_at = timezone.now()
        self.shipment.save()

        self.assertEqual(Invoice.objects.filter(shipment=self.shipment).count(), 1)

    def test_saving_an_already_paid_shipment_again_does_not_duplicate(self):
        self.shipment.payment_status = 'paid'
        self.shipment.paid_at = timezone.now()
        self.shipment.save()
        self.assertEqual(Invoice.objects.filter(shipment=self.shipment).count(), 1)

        # Unrelated field change while already paid — must not regenerate
        self.shipment.admin_notes = 'unrelated edit'
        self.shipment.save()
        self.assertEqual(Invoice.objects.filter(shipment=self.shipment).count(), 1)

    def test_invoice_generation_failure_does_not_raise_out_of_save(self):
        with patch('apps.payments.services.InvoiceService.upload_pdf', side_effect=Exception('Supabase down')):
            self.shipment.payment_status = 'paid'
            self.shipment.paid_at = timezone.now()
            self.shipment.save()  # must not raise

        self.assertEqual(Invoice.objects.filter(shipment=self.shipment).count(), 0)
```

Remove the placeholder `from apps.payments.models import Invoice as InvoiceModel` line above before saving — it was only there to flag that `Invoice` must already be imported at the top of the test file from Task 5/6 (it is, via `from apps.payments.models import Invoice`). Do not add a second import of the same name.

- [ ] **Step 2: Run to verify it fails**

Run: `python manage.py test apps.payments.tests.ShipmentPaidSignalTests -v 2`
Expected: first test FAILs with `AssertionError: 0 != 1` (no signal wired up yet, so no invoice gets created)

- [ ] **Step 3: Wire the signal**

In `apps/shipments/signals.py`, the file currently ends with the `sync_tracking_on_number_change` function (around line 113-114). Add this to the end of the file:

```python
# Track original payment_status before save (same shape as _original_tracking_numbers above)
_original_payment_status = {}


@receiver(pre_save, sender=Shipment)
def store_original_payment_status(sender, instance, **kwargs):
    """Store the original payment_status before save to detect a real transition into 'paid'."""
    if instance.pk:
        try:
            _original_payment_status[instance.pk] = Shipment.objects.get(pk=instance.pk).payment_status
        except Shipment.DoesNotExist:
            _original_payment_status[instance.pk] = ''
    else:
        _original_payment_status[instance.pk] = ''


@receiver(post_save, sender=Shipment)
def generate_invoice_on_paid(sender, instance, created, **kwargs):
    """Generate the GST invoice once a shipment's payment_status transitions
    into 'paid'. Deliberately thin — all the real work is InvoiceService's
    job. A failure here must never raise out of the payment-verification
    request; it's logged loudly and a manual admin action exists as backstop."""
    was = _original_payment_status.pop(instance.pk, '')

    if created or was == 'paid' or instance.payment_status != 'paid':
        return

    from apps.payments.services import InvoiceService

    try:
        InvoiceService.generate_for_shipment(instance, paid_at=instance.paid_at or timezone.now())
    except Exception:
        logger.exception(f"Invoice generation failed for shipment {instance.pk}")
```

- [ ] **Step 4: Run to verify the first two tests pass, and check the third**

Run: `python manage.py test apps.payments.tests.ShipmentPaidSignalTests -v 2`
Expected: `Ran 3 tests in ...s\n\nOK` — all three pass, including `test_invoice_generation_failure_does_not_raise_out_of_save` (the `try/except` in the signal catches the patched exception, logs it, and `self.shipment.save()` completes normally).

- [ ] **Step 5: Run the full test suite once more**

Run: `python manage.py test apps.payments -v 2`
Expected: `Ran 14 tests in ...s\n\nOK`

- [ ] **Step 6: Manual smoke test against the real payment flow**

Run: `python manage.py runserver`. Using an existing test shipment with `shipping_cost` set and `payment_status='unpaid'`, either:
- go through the real Razorpay test-mode checkout on the shipment detail page's "Pay Now" button, or
- in `/manage-rb-panel/`, open the shipment and manually set `payment_status` to `paid` + save (this alone is enough to trigger the signal, since the signal doesn't care which code path set the field)

Then confirm: a new row appears in `/manage-rb-panel/payments/invoice/`, and the shipment detail page's Quick Actions "Download Invoice" link now works and opens a PDF showing the correct company details, line items, and GST/zero-rated section.

- [ ] **Step 7: Commit**

```bash
git add apps/shipments/signals.py apps/payments/tests.py
git commit -m "feat: auto-generate GST invoice when shipment payment is marked paid"
```

---

## Task 8: Manual "Generate Invoice" admin backstop

**Files:**
- Modify: `apps/shipments/admin.py`

**Interfaces:**
- Consumes: `InvoiceService.generate_for_shipment` (Task 6).
- Produces: a `generate_invoice` admin action on `ShipmentAdmin`, usable from the shipment list's bulk-action dropdown.

- [ ] **Step 1: Add the action**

In `apps/shipments/admin.py`, find:

```python
    actions = [
        'approve_declaration', 'mark_packing', 'mark_dispatched', 'mark_delivered',
        'add_storage_fees'
    ]
```

Change it to:

```python
    actions = [
        'approve_declaration', 'mark_packing', 'mark_dispatched', 'mark_delivered',
        'add_storage_fees', 'generate_invoice'
    ]
```

Then add this method next to the other `@admin.action` methods on `ShipmentAdmin` (e.g. right after `mark_delivered`):

```python
    @admin.action(description='🧾 Generate Invoice')
    def generate_invoice(self, request, queryset):
        from apps.payments.services import InvoiceService

        generated = 0
        skipped = 0
        for shipment in queryset:
            if shipment.payment_status != 'paid':
                skipped += 1
                continue
            try:
                InvoiceService.generate_for_shipment(shipment, paid_at=shipment.paid_at)
                generated += 1
            except Exception:
                logger.exception(f"Manual invoice generation failed for shipment {shipment.pk}")
                skipped += 1

        message = f'{generated} invoice(s) generated (or already existed).'
        if skipped:
            message += f' {skipped} skipped — either not paid yet, or generation failed (check logs).'
        self.message_user(request, message)
```

- [ ] **Step 2: Verify**

Run: `python manage.py check`
Expected: `System check identified no issues (0 silenced).`

Then run: `python manage.py runserver`, visit `/manage-rb-panel/shipments/shipment/`, select a paid shipment that already has an invoice (from Task 7's manual test), run the "🧾 Generate Invoice" action on it, confirm the message says it was generated/already existed and no duplicate `Invoice` row appears in `/manage-rb-panel/payments/invoice/`.

- [ ] **Step 3: Commit**

```bash
git add apps/shipments/admin.py
git commit -m "feat: add manual Generate Invoice admin action as a backstop"
```

---

## Task 9: Final verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `python manage.py test apps.payments -v 2`
Expected: all tests from Tasks 4-7 pass (`Ran 14 tests ... OK`)

- [ ] **Step 2: Run Django system checks**

Run: `python manage.py check`
Expected: `System check identified no issues (0 silenced).`

- [ ] **Step 3: Confirm every Definition of Done item from the spec**

Walk through `docs/superpowers/specs/2026-08-05-gst-invoice-generation-design.md`'s Definition of Done checklist and check off each item against what Tasks 1-8 built. Every item should already be satisfied by this point; if any isn't, that's a gap to fix before calling this done.

- [ ] **Step 4: Update the spec's checkboxes**

Edit `docs/superpowers/specs/2026-08-05-gst-invoice-generation-design.md`, changing each `- [ ]` under Definition of Done to `- [x]` once manually confirmed.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/specs/2026-08-05-gst-invoice-generation-design.md
git commit -m "docs: mark GST invoice generation spec complete"
```
