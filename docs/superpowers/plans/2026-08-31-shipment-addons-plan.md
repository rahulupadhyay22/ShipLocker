# Shipment Add-on Services + Auto Shipment-Type Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the manual International/Domestic radio in the Create Shipment wizard with country-derived `shipment_type`, add four opt-in paid add-ons (Insurance, Extra Photos, Priority Packing, Gift Wrapping) priced via the existing `ServiceCharge` table, and fix a confirmed pre-existing bug where `consolidation_fee` is shown to customers as owed but never actually charged.

**Architecture:** A new `ShipmentAddon` child table (one row per purchased add-on, price locked in at creation) plumbed through the existing fee-computation/payment/invoice pipeline that `consolidation_fee` already established — `ServiceCharge.compute()` for pricing, `_payment_summary()` for the customer-facing total, `CreatePaymentOrderView` for the actual Razorpay charge, `InvoiceService` for the GST snapshot. The consolidation-fee bug fix lands first, as its own commit, since later tasks touch the same line to wire in add-ons.

**Tech Stack:** Django (no DRF), Django's own `TestCase`/test client (no pytest configured), vanilla JS in `<script>` blocks (no frontend build step), Razorpay via `apps/payments/services.py`.

**Spec:** `docs/superpowers/specs/2026-08-31-shipment-addons-design.md` — this plan implements it task-by-task; read both.

## Global Constraints

- Never trust a client-submitted add-on price — always recompute server-side from the locked `ServiceCharge` row (spec: "Rules for implementation").
- `ShipmentAddon` rows are created inside the same `transaction.atomic()` block as the `Shipment`, using the already `select_for_update()`-locked `parcels` queryset.
- Add-ons get **no** Premium-plan discount (opt-in extras, not baseline service — confirmed decision).
- Use CSS variables already in `static/css/main.css` — no hardcoded hex values in new template sections.
- Reuse `ServiceCharge.compute()` for all four add-on price computations — never reimplement percentage/floor math.
- The consolidation-fee billing-gap fix must be its own commit, separate from the add-ons feature commits, with a message documenting the historical bug (confirmed real, not intentional — see spec).
- Test runner on this machine: `.venv\Scripts\python.exe manage.py test <app_label>` (Windows venv; adjust to your own `python`/`manage.py test` invocation if different).

---

## Task 1: Fix the consolidation-fee billing gap (own commit)

**Files:**
- Modify: `apps/payments/views.py:157-256` (`CreatePaymentOrderView.post`)
- Test: `apps/payments/tests.py`

**Interfaces:**
- Consumes: `Shipment.consolidation_fee` (existing field), `Shipment.shipping_cost`, `_get_pending_batch_charges_for_locker(locker)` (existing helper, same file)
- Produces: nothing new consumed by later tasks — `total_due` still ends up in the same `Payment.amount`/Razorpay order shape later tasks build on

- [ ] **Step 1: Write the failing test**

Add to `apps/payments/tests.py` (mirrors the `_enable_razorpay` mocking pattern already used elsewhere in that file and in `apps/locker/tests/test_return_service_charge.py`):

```python
class CreatePaymentOrderConsolidationFeeTests(TestCase):
    """Regression test for the consolidation_fee billing gap: total_due
    must include consolidation_fee, not just shipping + pending storage."""

    def setUp(self):
        self.user = User.objects.create(email='consolidation-bug@example.com', is_active=True)
        self.locker = Locker.objects.create(user=self.user, plan_type='free')
        self.shipment = Shipment.objects.create(
            user=self.user,
            shipment_type='international',
            status='pending_payment',
            recipient_name='Jane Doe',
            address_line1='1 Test Street',
            city='Testville',
            state='Test State',
            postal_code='12345',
            country='United States',
            shipping_cost=Decimal('800.00'),
            consolidation_fee=Decimal('300.00'),
            currency='INR',
        )
        self.client.force_login(self.user)
        self.url = reverse('payments:create_order', kwargs={'shipment_pk': self.shipment.pk})

    def test_total_due_includes_consolidation_fee(self):
        p1, p2, p3 = _enable_razorpay('order_consolidation_1')
        with p1, p2, p3:
            response = self.client.post(self.url)

        self.assertEqual(response.status_code, 200)
        data = response.json()
        # 800 shipping + 300 consolidation = 1100.00 -> 110000 paise
        self.assertEqual(data['amount'], 110000)

        payment = Payment.objects.get(shipment=self.shipment)
        self.assertEqual(payment.amount, Decimal('1100.00'))
```

Add the needed imports at the top of `apps/payments/tests.py` if not already present in that file: `from decimal import Decimal`, `from django.test import TestCase`, `from django.urls import reverse`, `from apps.accounts.models import User, Locker`, `from apps.shipments.models import Shipment`, `from .models import Payment`. Check the existing `_enable_razorpay` helper signature already defined in this file before adding a second one — reuse it, don't duplicate.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe manage.py test apps.payments.tests.CreatePaymentOrderConsolidationFeeTests -v 2`
Expected: FAIL — `data['amount']` is `80000` (only shipping), not `110000`.

- [ ] **Step 3: Fix `CreatePaymentOrderView.post`**

In `apps/payments/views.py`, find:
```python
        shipping_due = shipment.shipping_cost if shipment.payment_status != 'paid' else Decimal('0.00')
```
Change the `total_due` line just below it (currently `total_due = (shipping_due + pending_storage_total).quantize(Decimal('0.01'))`) to:
```python
        consolidation_due = (shipment.consolidation_fee or Decimal('0.00')) if shipment.payment_status != 'paid' else Decimal('0.00')
        total_due = (shipping_due + consolidation_due + pending_storage_total).quantize(Decimal('0.01'))
```
Then find the `description_parts` block just below and add a consolidation entry, consistent with the existing `shipping`/`storage` entries:
```python
        description_parts = []
        if shipping_due > 0:
            description_parts.append('shipping')
        if consolidation_due > 0:
            description_parts.append('consolidation')
        if pending_storage_total > 0:
            description_parts.append('storage')
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe manage.py test apps.payments.tests.CreatePaymentOrderConsolidationFeeTests -v 2`
Expected: PASS

- [ ] **Step 5: Run the full payments test suite to check for regressions**

Run: `.venv\Scripts\python.exe manage.py test apps.payments -v 2`
Expected: all PASS (any pre-existing test asserting the old, lower `total_due` for a shipment with a non-zero `consolidation_fee` needs updating — check output for failures and fix the assertion to the corrected amount, not revert the fix).

- [ ] **Step 6: Commit — own commit, separate from the add-ons feature**

```bash
git add apps/payments/views.py apps/payments/tests.py
git commit -m "$(cat <<'EOF'
fix: CreatePaymentOrderView never actually charged consolidation_fee

total_due only ever summed shipping_due + pending_storage_total, so the
consolidation fee shown to customers as owed on the shipment detail page
(_payment_summary()'s shipment_amount_due) was never included in the
actual Razorpay charge amount. Confirmed via git history: this line has
never included consolidation_fee since the field was introduced. Premium
customers were unaffected (consolidation is waived to 0 for them) --
Free-plan customers on multi-item shipments were undercharged.

No automatic billing reconciliation is performed here. Determining
whether any already-paid Free-plan shipments were underbilled requires
querying production data directly.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Seed the four add-on `ServiceCharge` rows

**Files:**
- Modify: `apps/content/models.py` (`KNOWN_SERVICE_CHARGE_CODES`)
- Create: `apps/content/migrations/0013_seed_addon_service_charges.py`
- Test: `apps/content/tests.py` (create if it doesn't exist — check first with `Glob` for `apps/content/tests*.py`)

**Interfaces:**
- Produces: four active `ServiceCharge` rows with codes `addon_insurance`, `addon_extra_photos`, `addon_priority_packing`, `addon_gift_wrapping`, readable via `apps.content.services.get_service_charge(code)` — Task 4 depends on these codes existing exactly as spelled here.

- [ ] **Step 1: Add the four codes to `KNOWN_SERVICE_CHARGE_CODES`**

In `apps/content/models.py`, extend the existing list:
```python
KNOWN_SERVICE_CHARGE_CODES = [
    ('trunkassist_product_link', 'TrunkAssist – Product Link'),
    ('trunkassist_image_search', 'TrunkAssist – Image Search'),
    ('trunkassist_cart_screenshot', 'TrunkAssist – Cart Screenshot'),
    ('trunkassist_boutique_purchase', 'TrunkAssist – Boutique Purchase'),
    ('trunkassist_local_shop_purchase', 'TrunkAssist – Local Shop Purchase'),
    ('trunkassist_custom_request', 'TrunkAssist – Custom Request'),
    ('consolidation_fee', 'Consolidation Fee'),
    ('return_service_charge', 'Return Service Charge'),
    ('addon_insurance', 'Add-on: Insurance'),
    ('addon_extra_photos', 'Add-on: Extra Photos'),
    ('addon_priority_packing', 'Add-on: Priority Packing'),
    ('addon_gift_wrapping', 'Add-on: Gift Wrapping'),
]
```

- [ ] **Step 2: Write the seed migration**

Create `apps/content/migrations/0013_seed_addon_service_charges.py` (same shape as `0010_seed_service_charge_codes.py` and `0012_seed_return_service_charge.py`):
```python
from django.db import migrations


ADDON_CHARGES = [
    {
        'code': 'addon_insurance', 'name': 'Add-on: Insurance',
        'description': 'Optional coverage for the full declared value of your shipment against loss or damage in transit. 2% of declared value, minimum ₹99.',
        'charge_type': 'percentage', 'percentage_rate': '2.00', 'amount': '99.00',
    },
    {
        'code': 'addon_extra_photos', 'name': 'Add-on: Extra Photos',
        'description': 'Extra photos of your items before packing, beyond the standard intake set. Flat ₹149.',
        'charge_type': 'flat', 'percentage_rate': None, 'amount': '149.00',
    },
    {
        'code': 'addon_priority_packing', 'name': 'Add-on: Priority Packing',
        'description': 'Your shipment jumps the warehouse packing queue. Flat ₹299.',
        'charge_type': 'flat', 'percentage_rate': None, 'amount': '299.00',
    },
    {
        'code': 'addon_gift_wrapping', 'name': 'Add-on: Gift Wrapping',
        'description': 'Your shipment is gift-wrapped before it ships. Flat ₹99.',
        'charge_type': 'flat', 'percentage_rate': None, 'amount': '99.00',
    },
]


def seed_charges(apps, schema_editor):
    ServiceCharge = apps.get_model('content', 'ServiceCharge')
    for entry in ADDON_CHARGES:
        ServiceCharge.objects.get_or_create(
            code=entry['code'],
            defaults={
                'name': entry['name'],
                'description': entry['description'],
                'charge_type': entry['charge_type'],
                'percentage_rate': entry['percentage_rate'],
                'amount': entry['amount'],
                'currency': 'INR',
                'is_active': True,
            },
        )


def unseed_charges(apps, schema_editor):
    ServiceCharge = apps.get_model('content', 'ServiceCharge')
    codes = [entry['code'] for entry in ADDON_CHARGES]
    ServiceCharge.objects.filter(code__in=codes).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('content', '0012_seed_return_service_charge'),
    ]

    operations = [
        migrations.RunPython(seed_charges, unseed_charges),
    ]
```

- [ ] **Step 3: Apply the migration**

Run: `.venv\Scripts\python.exe manage.py migrate content`
Expected: `Applying content.0013_seed_addon_service_charges... OK`

- [ ] **Step 4: Write a test confirming the seed**

Check first whether `apps/content/tests.py` exists (`Glob` for `apps/content/tests*.py` / `apps/content/tests/`). If it doesn't exist, create `apps/content/tests.py`. Add:
```python
from decimal import Decimal
from django.test import TestCase
from apps.content.services import get_service_charge


class AddonServiceChargeSeedTests(TestCase):
    def test_all_four_addon_charges_are_seeded_and_active(self):
        insurance = get_service_charge('addon_insurance')
        self.assertIsNotNone(insurance)
        self.assertEqual(insurance.charge_type, 'percentage')
        self.assertEqual(insurance.percentage_rate, Decimal('2.00'))
        self.assertEqual(insurance.amount, Decimal('99.00'))

        for code, expected_amount in [
            ('addon_extra_photos', Decimal('149.00')),
            ('addon_priority_packing', Decimal('299.00')),
            ('addon_gift_wrapping', Decimal('99.00')),
        ]:
            charge = get_service_charge(code)
            self.assertIsNotNone(charge, f"{code} should be seeded")
            self.assertEqual(charge.charge_type, 'flat')
            self.assertEqual(charge.amount, expected_amount)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv\Scripts\python.exe manage.py test apps.content.tests.AddonServiceChargeSeedTests -v 2`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add apps/content/models.py apps/content/migrations/0013_seed_addon_service_charges.py apps/content/tests.py
git commit -m "$(cat <<'EOF'
feat: seed ServiceCharge rows for four shipment add-ons

addon_insurance (2% of declared value, min ₹99), addon_extra_photos
(₹149), addon_priority_packing (₹299), addon_gift_wrapping (₹99) --
starting points, admin-editable in /manage-rb-panel/ with no deploy
needed. Part of the shipment add-ons feature (spec:
docs/superpowers/specs/2026-08-31-shipment-addons-design.md).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `ShipmentAddon` model, migration, and staff-facing admin inline

**Files:**
- Modify: `apps/shipments/models.py`
- Create: `apps/shipments/migrations/0010_shipmentaddon.py` (via `makemigrations`)
- Modify: `apps/shipments/admin.py`
- Test: `apps/shipments/tests/test_shipment_addons.py` (new file — this is also where Task 5's and Task 6's tests will live)

**Interfaces:**
- Produces: `ShipmentAddon` model with `ADDON_CHOICES = [('insurance', ...), ('extra_photos', ...), ('priority_packing', ...), ('gift_wrapping', ...)]`, fields `shipment` (FK to `Shipment`, `related_name='addons'`), `code` (choices), `amount` (Decimal), `created_at`. `unique_together = ['shipment', 'code']`. Tasks 4-6 all import and use this model.

- [ ] **Step 1: Write the failing test**

Create `apps/shipments/tests/test_shipment_addons.py`:
```python
"""Tests for shipment add-ons (spec:
docs/superpowers/specs/2026-08-31-shipment-addons-design.md)."""
from decimal import Decimal
from django.db import IntegrityError
from django.test import TestCase

from apps.accounts.models import User, Locker
from apps.shipments.models import Shipment, ShipmentAddon


def make_shipment(user):
    return Shipment.objects.create(
        user=user,
        shipment_type='international',
        status='pending_payment',
        recipient_name='Jane Doe',
        address_line1='1 Test Street',
        city='Testville',
        state='Test State',
        postal_code='12345',
        country='United States',
        currency='INR',
    )


class ShipmentAddonModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email='addon-model@example.com', is_active=True)
        Locker.objects.create(user=self.user)
        self.shipment = make_shipment(self.user)

    def test_creates_addon_with_valid_code(self):
        addon = ShipmentAddon.objects.create(
            shipment=self.shipment, code='insurance', amount=Decimal('99.00'),
        )
        self.assertEqual(self.shipment.addons.count(), 1)
        self.assertEqual(addon.amount, Decimal('99.00'))

    def test_duplicate_code_on_same_shipment_rejected(self):
        ShipmentAddon.objects.create(
            shipment=self.shipment, code='gift_wrapping', amount=Decimal('99.00'),
        )
        with self.assertRaises(IntegrityError):
            ShipmentAddon.objects.create(
                shipment=self.shipment, code='gift_wrapping', amount=Decimal('99.00'),
            )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe manage.py test apps.shipments.tests.test_shipment_addons -v 2`
Expected: FAIL — `ImportError: cannot import name 'ShipmentAddon'`

- [ ] **Step 3: Add the model**

In `apps/shipments/models.py`, add after the `ShipmentItem` class (which already establishes the `Shipment`-linking pattern in this file):
```python
class ShipmentAddon(models.Model):
    """Opt-in paid add-on service purchased at shipment creation (Insurance,
    Extra Photos, Priority Packing, Gift Wrapping). amount is locked in at
    creation time, same rationale as Shipment.consolidation_fee -- an admin
    changing the ServiceCharge rate later doesn't retroactively change what
    an existing shipment owes. No Premium-plan discount applies to add-ons
    (opt-in extras, not baseline service)."""

    ADDON_CHOICES = [
        ('insurance', 'Insurance'),
        ('extra_photos', 'Extra Photos'),
        ('priority_packing', 'Priority Packing'),
        ('gift_wrapping', 'Gift Wrapping'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name='addons')
    code = models.CharField(max_length=20, choices=ADDON_CHOICES)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Shipment Add-on'
        verbose_name_plural = 'Shipment Add-ons'
        unique_together = ['shipment', 'code']

    def __str__(self):
        return f"{self.get_code_display()} — {self.shipment.display_id}"
```
Check the top of `apps/shipments/models.py` for `import uuid` and `from django.db import models` — both are already used by `ShipmentItem`/`Shipment`, no new imports needed.

- [ ] **Step 4: Generate and apply the migration**

Run: `.venv\Scripts\python.exe manage.py makemigrations shipments`
Expected: creates `apps/shipments/migrations/0010_shipmentaddon.py`. Open it and confirm it only adds the `ShipmentAddon` model (no unexpected changes to other fields — if `makemigrations` prompts about unrelated changes, stop and investigate before proceeding).

Run: `.venv\Scripts\python.exe manage.py migrate shipments`
Expected: `Applying shipments.0010_shipmentaddon... OK`

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv\Scripts\python.exe manage.py test apps.shipments.tests.test_shipment_addons -v 2`
Expected: PASS (both tests)

- [ ] **Step 6: Add the staff-facing admin inline**

In `apps/shipments/admin.py`, add near `ShipmentItemInline` (same file, same pattern):
```python
class ShipmentAddonInline(TabularInline):
    model = ShipmentAddon
    extra = 0
    readonly_fields = ['code', 'amount', 'created_at']

    def has_add_permission(self, request, obj=None):
        return False
```
Import `ShipmentAddon` in the existing `from .models import ...` line at the top of the file, and add `ShipmentAddonInline` to `ShipmentAdmin.inlines`:
```python
    inlines = [ShipmentItemInline, ShipmentAddonInline, ShipmentDocumentInline, TrackingEventInline]
```

- [ ] **Step 7: Manual admin check**

Run: `.venv\Scripts\python.exe manage.py runserver`, log into `/manage-rb-panel/` as a superuser, open any existing `Shipment`, confirm a read-only "Shipment Add-ons" inline section appears (empty, since none exist yet) with no errors.

- [ ] **Step 8: Commit**

```bash
git add apps/shipments/models.py apps/shipments/migrations/0010_shipmentaddon.py apps/shipments/admin.py apps/shipments/tests/test_shipment_addons.py
git commit -m "$(cat <<'EOF'
feat: add ShipmentAddon model and staff-facing admin inline

Child table for opt-in paid add-ons (Insurance, Extra Photos, Priority
Packing, Gift Wrapping) purchased at shipment creation, price locked in
at creation time. ShipmentAddonInline on ShipmentAdmin so warehouse staff
can see what a customer purchased and needs fulfilling.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Pricing helpers — `_compute_addon_amount` and `get_addon_options`

**Files:**
- Modify: `apps/payments/services.py`
- Test: `apps/payments/tests.py`

**Interfaces:**
- Consumes: `apps.content.services.get_service_charge(code)` (existing), `ServiceCharge.compute(product_value=None)` (existing), `apps.locker.models.Parcel.item_price` (existing field)
- Produces:
  - `ADDON_SERVICE_CHARGE_CODES: dict[str, str]` mapping `ShipmentAddon.ADDON_CHOICES` codes to their `ServiceCharge` codes (`'insurance' -> 'addon_insurance'`, etc.)
  - `_compute_addon_amount(addon_code: str, parcels=None) -> Decimal | None` — `None` means "not offered" (no active `ServiceCharge`)
  - `get_addon_options() -> list[dict]` — each dict has keys `code`, `label`, `description`, `charge_type`, `rate` (float or `None`), `floor_or_amount` (float)

  Task 5 imports and calls both `_compute_addon_amount` and `get_addon_options` directly by these names.

- [ ] **Step 1: Write the failing tests**

Add to `apps/payments/tests.py`:
```python
class ComputeAddonAmountTests(TestCase):
    def setUp(self):
        # addon_* ServiceCharge rows are seeded by
        # apps/content/migrations/0013_seed_addon_service_charges.py.
        self.user = User.objects.create(email='addon-pricing@example.com', is_active=True)
        self.locker = Locker.objects.create(user=self.user)
        self.parcel_a = Parcel.objects.create(
            locker=self.locker, item_name='Watch', status='approved', item_price=Decimal('4000.00'),
        )
        self.parcel_b = Parcel.objects.create(
            locker=self.locker, item_name='Bag', status='approved', item_price=Decimal('1000.00'),
        )

    def test_insurance_percentage_above_floor(self):
        from apps.payments.services import _compute_addon_amount
        # sum = 5000, 2% = 100.00, above the 99.00 floor
        amount = _compute_addon_amount('insurance', [self.parcel_a, self.parcel_b])
        self.assertEqual(amount, Decimal('100.00'))

    def test_insurance_percentage_below_floor_uses_floor(self):
        from apps.payments.services import _compute_addon_amount
        cheap_parcel = Parcel.objects.create(
            locker=self.locker, item_name='Pen', status='approved', item_price=Decimal('500.00'),
        )
        # 500 * 2% = 10.00, below the 99.00 floor
        amount = _compute_addon_amount('insurance', [cheap_parcel])
        self.assertEqual(amount, Decimal('99.00'))

    def test_insurance_with_no_parcels_uses_floor(self):
        from apps.payments.services import _compute_addon_amount
        amount = _compute_addon_amount('insurance', [])
        self.assertEqual(amount, Decimal('99.00'))

    def test_flat_addon_returns_configured_amount(self):
        from apps.payments.services import _compute_addon_amount
        self.assertEqual(_compute_addon_amount('gift_wrapping'), Decimal('99.00'))
        self.assertEqual(_compute_addon_amount('extra_photos'), Decimal('149.00'))
        self.assertEqual(_compute_addon_amount('priority_packing'), Decimal('299.00'))

    def test_unconfigured_addon_returns_none(self):
        from apps.content.models import ServiceCharge
        from apps.payments.services import _compute_addon_amount
        ServiceCharge.objects.filter(code='addon_gift_wrapping').update(is_active=False)
        from apps.content.services import invalidate_service_charge_cache
        invalidate_service_charge_cache('addon_gift_wrapping')  # .update() bypasses the save signal
        self.assertIsNone(_compute_addon_amount('gift_wrapping'))


class GetAddonOptionsTests(TestCase):
    def test_returns_all_four_configured_addons(self):
        from apps.payments.services import get_addon_options
        options = get_addon_options()
        codes = {opt['code'] for opt in options}
        self.assertEqual(codes, {'insurance', 'extra_photos', 'priority_packing', 'gift_wrapping'})

    def test_excludes_addon_with_no_active_service_charge(self):
        from apps.content.models import ServiceCharge
        from apps.content.services import invalidate_service_charge_cache
        from apps.payments.services import get_addon_options

        ServiceCharge.objects.filter(code='addon_priority_packing').update(is_active=False)
        invalidate_service_charge_cache('addon_priority_packing')

        options = get_addon_options()
        codes = {opt['code'] for opt in options}
        self.assertNotIn('priority_packing', codes)
        self.assertEqual(len(options), 3)

    def test_insurance_option_carries_rate_and_floor(self):
        from apps.payments.services import get_addon_options
        options = {opt['code']: opt for opt in get_addon_options()}
        insurance = options['insurance']
        self.assertEqual(insurance['charge_type'], 'percentage')
        self.assertEqual(insurance['rate'], 2.0)
        self.assertEqual(insurance['floor_or_amount'], 99.0)

    def test_flat_option_has_no_rate(self):
        from apps.payments.services import get_addon_options
        options = {opt['code']: opt for opt in get_addon_options()}
        self.assertIsNone(options['gift_wrapping']['rate'])
        self.assertEqual(options['gift_wrapping']['floor_or_amount'], 99.0)
```
Confirm `from apps.locker.models import Parcel` is already imported at the top of `apps/payments/tests.py` (it's used by other test classes in that file per the codebase's existing patterns); add it if not.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe manage.py test apps.payments.tests.ComputeAddonAmountTests apps.payments.tests.GetAddonOptionsTests -v 2`
Expected: FAIL — `ImportError: cannot import name '_compute_addon_amount'`

- [ ] **Step 3: Implement the helpers**

In `apps/payments/services.py`, add after `_get_consolidation_fee_amount` (which already establishes the "look up a `ServiceCharge` by code, compute an amount" pattern in this exact file):
```python
ADDON_SERVICE_CHARGE_CODES = {
    'insurance': 'addon_insurance',
    'extra_photos': 'addon_extra_photos',
    'priority_packing': 'addon_priority_packing',
    'gift_wrapping': 'addon_gift_wrapping',
}

ADDON_LABELS = {
    'insurance': ('Insurance', "Protect your shipment's full declared value against loss or damage."),
    'extra_photos': ('Extra Photos', 'Extra photos of your items before packing, beyond the standard intake set.'),
    'priority_packing': ('Priority Packing', 'Jump the queue — your shipment gets packed first.'),
    'gift_wrapping': ('Gift Wrapping', 'Have your shipment gift-wrapped before it ships.'),
}


def _compute_addon_amount(addon_code, parcels=None) -> Decimal | None:
    """Resolve the price for a shipment add-on from its ServiceCharge row.
    Returns None if the ServiceCharge is missing/inactive -- unlike
    consolidation_fee's "missing means free" convention, an unpriced add-on
    should not be offered at all (these are optional upsells, not
    mandatory fees). parcels is only used for 'insurance' (percentage of
    declared value); ignored for the three flat add-ons."""
    from apps.content.services import get_service_charge

    charge_code = ADDON_SERVICE_CHARGE_CODES.get(addon_code)
    if not charge_code:
        return None
    charge = get_service_charge(charge_code)
    if not charge:
        return None
    if addon_code == 'insurance':
        declared_value = sum((p.item_price or Decimal('0')) for p in (parcels or []))
        return Decimal(str(charge.compute(declared_value))).quantize(Decimal('0.01'))
    return Decimal(str(charge.compute())).quantize(Decimal('0.01'))


def get_addon_options():
    """List of {code, label, description, charge_type, rate, floor_or_amount}
    for every add-on with an active ServiceCharge configured -- single
    source of truth for both the step-3 checkbox list (CreateShipmentView.get)
    and add-on creation validation (CreateShipmentView.post), so the two
    can't drift out of sync with each other. rate is None for flat charges."""
    from apps.content.services import get_service_charge

    options = []
    for code, charge_code in ADDON_SERVICE_CHARGE_CODES.items():
        charge = get_service_charge(charge_code)
        if not charge:
            continue
        label, description = ADDON_LABELS[code]
        options.append({
            'code': code,
            'label': label,
            'description': description,
            'charge_type': charge.charge_type,
            'rate': float(charge.percentage_rate) if charge.charge_type == 'percentage' else None,
            'floor_or_amount': float(charge.amount),
        })
    return options
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe manage.py test apps.payments.tests.ComputeAddonAmountTests apps.payments.tests.GetAddonOptionsTests -v 2`
Expected: PASS (all 9 tests)

- [ ] **Step 5: Commit**

```bash
git add apps/payments/services.py apps/payments/tests.py
git commit -m "$(cat <<'EOF'
feat: add _compute_addon_amount and get_addon_options pricing helpers

Single source of truth for shipment add-on pricing, reusing
ServiceCharge.compute() -- an add-on with no active ServiceCharge
configured is treated as "not offered" (returns None / excluded from
get_addon_options()), unlike consolidation_fee's "missing means free"
convention, since these are optional upsells rather than mandatory fees.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Shipment-type derivation + add-on creation in `CreateShipmentView`

**Files:**
- Modify: `apps/shipments/views.py` (`CreateShipmentView.get`, `CreateShipmentView.post`)
- Test: `apps/shipments/tests/test_shipment_addons.py`

**Interfaces:**
- Consumes: `get_addon_options()`, `_compute_addon_amount(code, parcels)` (Task 4), `ShipmentAddon` (Task 3)
- Produces: `CreateShipmentView.get` context gains `addon_options` (list of dicts) and `addon_options_json` (JSON string); each parcel object in `parcels` gains `.item_price` already as a model field (no change needed — it's read directly in the template). `shipment_type` on a created `Shipment` is now always derived from `country`, never from POST.

- [ ] **Step 1: Write the failing tests**

Add to `apps/shipments/tests/test_shipment_addons.py` (extends the file from Task 3):
```python
from unittest.mock import patch
from django.urls import reverse

from apps.locker.models import Parcel
from apps.shipments.models import ShipmentAddon


def make_parcel(locker, **extra):
    defaults = dict(
        locker=locker, status='approved', item_name='Test Item',
        item_price=Decimal('2000.00'), category='electronics',
        customs_description='A test item', weight_kg=Decimal('1.0'),
    )
    defaults.update(extra)
    return Parcel.objects.create(**defaults)


class CreateShipmentAddonsAndTypeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email='create-addons@example.com', full_name='Rahul Signer', is_active=True)
        self.locker = Locker.objects.create(user=self.user)
        self.parcel = make_parcel(self.locker)
        self.client.force_login(self.user)
        self.url = reverse('shipments:create')

        self.generate_pdf_patcher = patch(
            'apps.shipments.services.declaration_service.DeclarationService.generate_pdf',
            return_value=b'%PDF-fake-bytes',
        )
        self.upload_pdf_patcher = patch(
            'apps.shipments.services.declaration_service.DeclarationService.upload_pdf',
            return_value='shipment/RB-00001/customs_fake.pdf',
        )
        self.generate_pdf_patcher.start()
        self.upload_pdf_patcher.start()
        self.addCleanup(self.generate_pdf_patcher.stop)
        self.addCleanup(self.upload_pdf_patcher.stop)

    def _valid_data(self, **overrides):
        data = {
            'parcels': [str(self.parcel.id)],
            'declaration_purpose': 'gift',
            'signature_agree': 'on',
            'signature_name': 'Rahul Signer',
            'recipient_name': 'Jane Doe',
            'recipient_phone': '9999999999',
            'recipient_email': 'jane@example.com',
            'address_line1': '1 Test Street',
            'address_line2': '',
            'city': 'Testville',
            'state': 'Test State',
            'postal_code': '123456',
            'country': 'United States',
        }
        data.update(overrides)
        return data

    def test_get_context_includes_addon_options(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        codes = {opt['code'] for opt in response.context['addon_options']}
        self.assertEqual(codes, {'insurance', 'extra_photos', 'priority_packing', 'gift_wrapping'})

    def test_india_country_derives_domestic(self):
        response = self.client.post(self.url, self._valid_data(country='India'))
        self.assertEqual(response.status_code, 302)
        shipment = Shipment.objects.get()
        self.assertEqual(shipment.shipment_type, 'domestic')

    def test_non_india_country_derives_international(self):
        response = self.client.post(self.url, self._valid_data(country='United States'))
        self.assertEqual(response.status_code, 302)
        shipment = Shipment.objects.get()
        self.assertEqual(shipment.shipment_type, 'international')

    def test_stray_shipment_type_post_field_is_ignored(self):
        # A client-supplied shipment_type must never override the
        # country-derived value.
        response = self.client.post(self.url, self._valid_data(country='India', shipment_type='international'))
        self.assertEqual(response.status_code, 302)
        shipment = Shipment.objects.get()
        self.assertEqual(shipment.shipment_type, 'domestic')

    def test_selected_addons_create_shipmentaddon_rows_with_server_computed_amounts(self):
        response = self.client.post(self.url, self._valid_data(addons=['gift_wrapping', 'priority_packing']))
        self.assertEqual(response.status_code, 302)
        shipment = Shipment.objects.get()
        addons = {a.code: a.amount for a in shipment.addons.all()}
        self.assertEqual(addons, {'gift_wrapping': Decimal('99.00'), 'priority_packing': Decimal('299.00')})

    def test_client_supplied_addon_amount_is_ignored(self):
        # Only 'addons' (the code list) is a real form field; there is no
        # amount field for the client to tamper with in the first place --
        # this test documents/locks in that the server always recomputes.
        response = self.client.post(self.url, self._valid_data(addons=['insurance']))
        self.assertEqual(response.status_code, 302)
        shipment = Shipment.objects.get()
        addon = shipment.addons.get(code='insurance')
        # self.parcel has item_price=2000.00 -> 2% = 40.00, below the 99.00 floor
        self.assertEqual(addon.amount, Decimal('99.00'))

    def test_unknown_addon_code_is_ignored(self):
        response = self.client.post(self.url, self._valid_data(addons=['not_a_real_addon']))
        self.assertEqual(response.status_code, 302)
        shipment = Shipment.objects.get()
        self.assertEqual(shipment.addons.count(), 0)

    def test_unconfigured_addon_creates_no_row_even_if_requested(self):
        from apps.content.models import ServiceCharge
        from apps.content.services import invalidate_service_charge_cache
        ServiceCharge.objects.filter(code='addon_extra_photos').update(is_active=False)
        invalidate_service_charge_cache('addon_extra_photos')

        response = self.client.post(self.url, self._valid_data(addons=['extra_photos']))
        self.assertEqual(response.status_code, 302)
        shipment = Shipment.objects.get()
        self.assertEqual(shipment.addons.count(), 0)

    def test_no_addons_selected_creates_no_rows(self):
        response = self.client.post(self.url, self._valid_data())
        self.assertEqual(response.status_code, 302)
        shipment = Shipment.objects.get()
        self.assertEqual(shipment.addons.count(), 0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe manage.py test apps.shipments.tests.test_shipment_addons.CreateShipmentAddonsAndTypeTests -v 2`
Expected: FAIL — `addon_options` missing from context; `shipment_type` still comes from the (now-removed-from-form-data-but-still-read) POST field so India/domestic tests fail.

- [ ] **Step 3: Wire `addon_options` into `CreateShipmentView.get`**

In `apps/shipments/views.py`, inside `CreateShipmentView.get`, add near the existing `from apps.payments.services import _get_consolidation_fee_amount, _lookup_consolidation_fee_standard` line:
```python
        from apps.payments.services import get_addon_options
        import json
```
(Check first whether `json` is already imported at the top of the file — it is not, per the existing `import logging` / `import uuid` block, so add `import json` there instead of function-locally, matching the file's top-level import style for stdlib modules.)

Add to the `return render(...)` context dict:
```python
            'addon_options': get_addon_options(),
            'addon_options_json': json.dumps(get_addon_options()),
```

- [ ] **Step 4: Derive `shipment_type` from country and create `ShipmentAddon` rows in `CreateShipmentView.post`**

In `apps/shipments/views.py`, inside `CreateShipmentView.post`, delete:
```python
        shipment_type = request.POST.get('shipment_type', 'international')
```
and delete the block just below it:
```python
        if shipment_type not in dict(Shipment.TYPE_CHOICES):
            messages.error(request, 'Invalid shipment type.')
            return redirect('shipments:create')
```

After `address_data = validate_address({...})` succeeds (later in the same method), add:
```python
        shipment_type = 'domestic' if address_data['country'].strip().upper() == 'INDIA' else 'international'
```

Inside the `with transaction.atomic():` block, after the `Shipment.objects.create(...)` call that currently uses `shipment_type=shipment_type` (already correct — it references the local variable, no change needed there), add, right after the `shipment` variable is created and before the "Optionally save as default address" block:
```python
                from .models import ShipmentAddon
                from apps.payments.services import _compute_addon_amount

                requested_addons = set(request.POST.getlist('addons'))
                valid_addon_codes = dict(ShipmentAddon.ADDON_CHOICES).keys()
                for addon_code in requested_addons & valid_addon_codes:
                    amount = _compute_addon_amount(addon_code, parcels)
                    if amount is not None:
                        ShipmentAddon.objects.create(shipment=shipment, code=addon_code, amount=amount)
                        logger.info(
                            f"Add-on '{addon_code}' (₹{amount}) added to shipment {shipment.display_id} "
                            f"by user {request.user.id}"
                        )
```
This reuses the `parcels` list already built earlier in `post` from the `select_for_update()`-locked queryset — check the exact variable name in the existing code (it's `parcels`, built a few lines above the `Shipment.objects.create(...)` call) before writing this, so the insurance computation sums `item_price` from the same locked rows, not a fresh unlocked query.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe manage.py test apps.shipments.tests.test_shipment_addons.CreateShipmentAddonsAndTypeTests -v 2`
Expected: PASS (all 9 tests)

- [ ] **Step 6: Run the full shipments test suite to check for regressions**

Run: `.venv\Scripts\python.exe manage.py test apps.shipments -v 2`
Expected: all PASS. In particular check `apps/shipments/tests/test_esign_declaration.py` — it POSTs a `shipment_type: 'international'` field directly (now inert) alongside `country: 'United States'`; since `'UNITED STATES'.strip().upper() != 'INDIA'`, the derived value is still `'international'`, so no change to that test file should be needed. Confirm this by reading the actual test run output, not by assumption.

- [ ] **Step 7: Commit**

```bash
git add apps/shipments/views.py apps/shipments/tests/test_shipment_addons.py
git commit -m "$(cat <<'EOF'
feat: derive shipment_type from country, create ShipmentAddon rows on create

shipment_type is no longer trusted from a client-supplied POST field --
it's derived from the validated delivery country (India -> domestic,
anything else -> international), matching _match_shipping_zone's existing
country.strip().upper() convention. Selected add-ons are validated against
ShipmentAddon.ADDON_CHOICES and priced server-side via
_compute_addon_amount, never trusting a client-supplied amount (there
isn't one to trust in the first place -- only the code list is a real
form field).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Payment wiring — `_payment_summary`, `CreatePaymentOrderView`, GST invoice, equivalence test

**Files:**
- Modify: `apps/shipments/views.py` (`_payment_summary`)
- Modify: `apps/payments/views.py` (`CreatePaymentOrderView.post`)
- Modify: `apps/payments/models.py` (`Invoice`)
- Create: `apps/payments/migrations/0011_invoice_addons_amount.py` (via `makemigrations`)
- Modify: `apps/payments/services.py` (`build_charge_snapshot`, `InvoiceService.generate_pdf`, `InvoiceService.generate_for_shipment`)
- Test: `apps/shipments/tests/test_shipment_addons.py`, `apps/payments/tests.py`

**Interfaces:**
- Consumes: `Shipment.addons` (Task 3's `related_name`), `_compute_addon_amount`/`get_addon_options` (Task 4)
- Produces: `_payment_summary(shipment)` dict gains key `'addons_amount'`; `unpaid_charges` and `shipment_amount_due`/`shipment_total_amount` now include it. `build_charge_snapshot(shipment)` dict gains `'addons_amount'`. `Invoice.addons_amount` field.

- [ ] **Step 1: Write the failing tests**

Add to `apps/shipments/tests/test_shipment_addons.py`:
```python
from apps.shipments.views import _payment_summary


class PaymentSummaryAddonsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email='payment-summary-addons@example.com', is_active=True)
        Locker.objects.create(user=self.user, plan_type='free')
        self.shipment = make_shipment(self.user)
        self.shipment.shipping_cost = Decimal('800.00')
        self.shipment.consolidation_fee = Decimal('300.00')
        self.shipment.payment_status = 'unpaid'
        self.shipment.save()
        ShipmentAddon.objects.create(shipment=self.shipment, code='gift_wrapping', amount=Decimal('99.00'))
        ShipmentAddon.objects.create(shipment=self.shipment, code='priority_packing', amount=Decimal('299.00'))

    def test_addons_amount_included_in_unpaid_charges_and_amount_due(self):
        summary = _payment_summary(self.shipment)
        self.assertEqual(summary['addons_amount'], Decimal('398.00'))
        # 800 shipping + 300 consolidation + 398 addons = 1498.00
        self.assertEqual(summary['shipment_amount_due'], Decimal('1498.00'))
```

Add to `apps/payments/tests.py`:
```python
class CreatePaymentOrderAddonsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email='addons-checkout@example.com', is_active=True)
        Locker.objects.create(user=self.user, plan_type='free')
        self.shipment = Shipment.objects.create(
            user=self.user, shipment_type='international', status='pending_payment',
            recipient_name='Jane Doe', address_line1='1 Test Street', city='Testville',
            state='Test State', postal_code='12345', country='United States',
            shipping_cost=Decimal('800.00'), consolidation_fee=Decimal('300.00'), currency='INR',
        )
        ShipmentAddon.objects.create(shipment=self.shipment, code='gift_wrapping', amount=Decimal('99.00'))
        self.client.force_login(self.user)
        self.url = reverse('payments:create_order', kwargs={'shipment_pk': self.shipment.pk})

    def test_total_due_includes_addons(self):
        p1, p2, p3 = _enable_razorpay('order_addons_1')
        with p1, p2, p3:
            response = self.client.post(self.url)
        self.assertEqual(response.status_code, 200)
        # 800 + 300 + 99 = 1199.00 -> 119900 paise
        self.assertEqual(response.json()['amount'], 119900)

    def test_displayed_total_equals_charged_total_with_every_fee_present(self):
        """Regression guard for the consolidation_fee billing gap: proves
        the two independent calculations (display math vs. charge math)
        agree, not just that each one's own formula looks right in
        isolation -- that's what let them drift apart the first time."""
        from django.utils import timezone
        from apps.shipments.views import _payment_summary
        from apps.locker.models import Batch
        from apps.payments.models import BatchCharge

        # Batch lives in apps.locker.models (the storage-billing unit), not
        # apps.payments.models -- BatchCharge (the per-day line item) is the
        # one that lives in payments. Required fields per apps/locker/models.py:
        # plan_type_at_creation, quota_year, first_parcel_received_date.
        batch = Batch.objects.create(
            locker=self.shipment.user.locker,
            plan_type_at_creation='free',
            quota_year=timezone.now().year,
            first_parcel_received_date=timezone.now().date(),
        )
        BatchCharge.objects.create(
            batch=batch, charge_date=timezone.now().date(),
            parcel_count_snapshot=1, amount=Decimal('50.00'), status='pending',
        )

        displayed = _payment_summary(self.shipment)['shipment_amount_due']

        p1, p2, p3 = _enable_razorpay('order_addons_2')
        with p1, p2, p3:
            response = self.client.post(self.url)
        charged = Decimal(str(response.json()['amount'])) / 100

        self.assertEqual(displayed, charged)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe manage.py test apps.shipments.tests.test_shipment_addons.PaymentSummaryAddonsTests apps.payments.tests.CreatePaymentOrderAddonsTests -v 2`
Expected: FAIL — `KeyError: 'addons_amount'`, and `total_due` mismatches.

- [ ] **Step 3: Update `_payment_summary`**

In `apps/shipments/views.py`, `_payment_summary(shipment)`:
```python
    shipping_amount = Decimal(str(shipment.shipping_cost or 0))
    consolidation_fee = Decimal(str(shipment.consolidation_fee or 0))
    addons_amount = shipment.addons.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    storage_total = pending_total + paid_total
    unpaid_charges = shipping_amount + consolidation_fee + addons_amount
```
(`Sum` is already imported in this function's body via `from django.db.models import Sum` — check and reuse, don't re-import.) Add `'addons_amount': addons_amount,` to the returned dict, alongside the existing `'consolidation_fee': consolidation_fee,` line.

- [ ] **Step 4: Update `CreatePaymentOrderView.post`**

In `apps/payments/views.py`, extend the `consolidation_due` line from Task 1 to also include add-ons:
```python
        addons_total = shipment.addons.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        consolidation_due = ((shipment.consolidation_fee or Decimal('0.00')) + addons_total) if shipment.payment_status != 'paid' else Decimal('0.00')
```
Check whether `Sum` is already imported at the top of `apps/payments/views.py` (it's used elsewhere in this same view for `pending_storage_total`) — reuse it. Extend `description_parts`:
```python
        if addons_total > 0:
            description_parts.append('add-ons')
```

- [ ] **Step 5: Add `Invoice.addons_amount` and update the GST snapshot**

In `apps/payments/models.py`, add to `Invoice` right after `consolidation_fee_amount`:
```python
    addons_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
```

Run: `.venv\Scripts\python.exe manage.py makemigrations payments`
Expected: creates `apps/payments/migrations/0011_invoice_addons_amount.py` with only this one field addition.

Run: `.venv\Scripts\python.exe manage.py migrate payments`

In `apps/payments/services.py`, update `build_charge_snapshot`:
```python
    return {
        'shipping_amount': summary['shipping_amount'],
        'storage_fee_amount': summary['storage_fee_paid'],
        'consolidation_fee_amount': summary['consolidation_fee'],
        'addons_amount': summary['addons_amount'],
    }
```
Update `InvoiceService.generate_for_shipment`'s `taxable_amount` line:
```python
        taxable_amount = (
            charges['shipping_amount'] + charges['storage_fee_amount']
            + charges['consolidation_fee_amount'] + charges['addons_amount']
        )
```
and add `addons_amount=charges['addons_amount'],` to the `Invoice.objects.create(...)` call, alongside the existing `consolidation_fee_amount=charges['consolidation_fee_amount'],` line.

In `InvoiceService.generate_pdf`, add a row for add-ons alongside the existing consolidation-fee row:
```python
        if context['consolidation_fee_amount'] > 0:
            rows.append(['Consolidation Fee', f"{context['consolidation_fee_amount']:.2f}"])
        if context['addons_amount'] > 0:
            rows.append(['Add-ons', f"{context['addons_amount']:.2f}"])
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe manage.py test apps.shipments.tests.test_shipment_addons.PaymentSummaryAddonsTests apps.payments.tests.CreatePaymentOrderAddonsTests -v 2`
Expected: PASS (all 3 tests, including the equivalence test)

- [ ] **Step 7: Run the full payments and shipments suites to check for regressions**

Run: `.venv\Scripts\python.exe manage.py test apps.payments apps.shipments -v 2`
Expected: all PASS — pay particular attention to any existing invoice-generation test (`apps.payments.tax`, GST tests) that asserts a specific `taxable_amount`/`total_amount` for a shipment; these should be unaffected since `addons_amount` defaults to `0` for any shipment with no add-ons, but confirm from actual output.

- [ ] **Step 8: Commit**

```bash
git add apps/shipments/views.py apps/payments/views.py apps/payments/models.py apps/payments/migrations/0011_invoice_addons_amount.py apps/payments/services.py apps/shipments/tests/test_shipment_addons.py apps/payments/tests.py
git commit -m "$(cat <<'EOF'
feat: wire add-on totals into payment amount, GST invoice, and display

_payment_summary(), CreatePaymentOrderView, and the GST invoice snapshot
all now include addons_amount alongside shipping/consolidation/storage.
Includes a regression test proving the customer-facing displayed total
and the actual Razorpay charge total are the same number end-to-end (not
just that each formula looks correct in isolation) -- the guard against
this exact class of bug (the consolidation_fee gap fixed earlier)
recurring for add-ons or anything added later.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Wizard UI — step 1/3 changes, JS, sidebar

**Files:**
- Modify: `templates/shipments/create.html`
- Modify: `templates/shipments/_create_summary_sidebar.html`
- Test: `apps/shipments/tests/test_shipment_addons.py` (render-only checks — full client-side JS behavior needs a manual browser check, called out below)

**Interfaces:**
- Consumes: `addon_options` / `addon_options_json` (Task 5's context), each parcel's existing `.item_price` field
- Produces: no new Python interfaces — this task is templates/JS only

- [ ] **Step 1: Write a render-smoke test first**

Add to `apps/shipments/tests/test_shipment_addons.py`:
```python
class CreateShipmentTemplateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email='create-template@example.com', is_active=True)
        self.locker = Locker.objects.create(user=self.user)
        self.parcel = make_parcel(self.locker)
        self.client.force_login(self.user)

    def test_no_shipment_type_radio_rendered(self):
        response = self.client.get(reverse('shipments:create'))
        self.assertNotContains(response, 'name="shipment_type"')

    def test_addon_checkboxes_rendered_for_each_configured_addon(self):
        response = self.client.get(reverse('shipments:create'))
        for code in ('insurance', 'extra_photos', 'priority_packing', 'gift_wrapping'):
            self.assertContains(response, f'value="{code}"')

    def test_parcel_card_carries_item_price_data_attribute(self):
        response = self.client.get(reverse('shipments:create'))
        self.assertContains(response, 'data-item-price=')

    def test_hidden_addon_not_rendered(self):
        from apps.content.models import ServiceCharge
        from apps.content.services import invalidate_service_charge_cache
        ServiceCharge.objects.filter(code='addon_insurance').update(is_active=False)
        invalidate_service_charge_cache('addon_insurance')

        response = self.client.get(reverse('shipments:create'))
        self.assertNotContains(response, 'value="insurance"')
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe manage.py test apps.shipments.tests.test_shipment_addons.CreateShipmentTemplateTests -v 2`
Expected: FAIL — `shipment_type` radio still present, no `addon` checkboxes, no `data-item-price`.

- [ ] **Step 3: Step 1 — add `data-item-price` and reword the insurance copy**

In `templates/shipments/create.html`, find the `.parcel-select-card` `<label>` (around line 66) and add the attribute next to the existing `data-billable-weight`:
```html
                            <label class="parcel-select-card" data-weight="{{ parcel.weight_kg|default:'0' }}"
                                data-billable-weight="{{ parcel.billable_weight|default:'0' }}"
                                data-item-price="{{ parcel.item_price|default:'0' }}">
```
Find the "Your items are 100% insured" box (around line 139-145) and reword it:
```html
                        <div class="modern-alert" style="background: var(--primary-light); border: 1px solid var(--primary); align-items: flex-start; margin-top: 1.5rem;">
                            <div style="color: var(--primary);"><svg width="20" height="20" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6l7-3z"/></svg></div>
                            <div>
                                <strong>Basic protection included</strong>
                                <p style="margin-top: 0.25rem; font-size: 0.85rem;">Every shipment includes basic protection — add Insurance in the next step for full coverage of your declared value.</p>
                            </div>
                        </div>
```

- [ ] **Step 4: Step 3 — remove the radio group, add the type badge and add-on checkboxes**

Replace the `<div class="radio-group">...</div>` block (lines 279-312, the `shipment_type` radios) with:
```html
                <div class="modern-alert" id="shipmentTypeBadge" style="background: var(--surface-50); border: 1px solid var(--surface-200); align-items: center;">
                    <strong id="shipmentTypeBadgeText">Add a delivery address to see shipment type</strong>
                </div>

                <h4 style="font-size: 1rem; font-weight: 600; margin: 1.5rem 0 1rem;">Add-on Services</h4>
                <div class="radio-group" id="addonOptions">
                    {% for opt in addon_options %}
                    <label class="radio-card">
                        <input type="checkbox" name="addons" value="{{ opt.code }}" class="addon-checkbox" data-addon-code="{{ opt.code }}" onchange="updateAddonPrices()">
                        <span class="rc-content">
                            <div>
                                <strong>{{ opt.label }}</strong>
                                <span>{{ opt.description }}</span>
                                <span class="addon-price" data-addon-price="{{ opt.code }}">—</span>
                            </div>
                        </span>
                    </label>
                    {% endfor %}
                </div>
```

- [ ] **Step 5: JS — badge, add-on pricing, remove the old recap logic**

In the `<script>` block, add the `ADDON_OPTIONS` constant next to the existing `ZONES_DATA`/`CONSOLIDATION_FEE` constants:
```javascript
    const ADDON_OPTIONS = JSON.parse('{{ addon_options_json|escapejs }}');
```

Add two new functions (place near `updateShippingEstimate`):
```javascript
    function updateShipmentTypeBadge() {
        const form = document.getElementById('shipmentForm');
        const badgeText = document.getElementById('shipmentTypeBadgeText');
        if (!badgeText) return;
        const country = form.country.value.trim();
        if (!country) {
            badgeText.textContent = 'Add a delivery address to see shipment type';
            return;
        }
        badgeText.textContent = country.toUpperCase() === 'INDIA'
            ? '🚚 Domestic Shipment (within India)'
            : '🌍 International Shipment';
    }

    function updateAddonPrices() {
        let declaredValue = 0;
        document.querySelectorAll('.parcel-select-card').forEach(function (card) {
            const cb = card.querySelector('.parcel-checkbox');
            if (cb.checked) {
                declaredValue += parseFloat(card.dataset.itemPrice) || 0;
            }
        });

        ADDON_OPTIONS.forEach(function (opt) {
            const priceEl = document.querySelector('.addon-price[data-addon-price="' + opt.code + '"]');
            if (!priceEl) return;
            let price;
            if (opt.charge_type === 'percentage') {
                price = Math.max(declaredValue * (opt.rate / 100), opt.floor_or_amount);
            } else {
                price = opt.floor_or_amount;
            }
            priceEl.textContent = fmtMoney(price);
        });
    }
```

Call both from `updateSummary()` (so add-on prices and the badge stay in sync with item/country changes) — add these two lines at the end of `updateSummary()`, just before its closing brace:
```javascript
        updateShipmentTypeBadge();
        updateAddonPrices();
```
Add a call to `updateShipmentTypeBadge()` inside the existing `country`'s `onchange="updateSummary()"` handler — no change needed there since `updateSummary()` now calls it internally.

In `updateReviewRecap()`, replace the block that reads the now-deleted `shipment_type` radio:
```javascript
        if (serviceEl) {
            const country = form.country.value.trim();
            serviceEl.textContent = country
                ? (country.toUpperCase() === 'INDIA' ? 'Domestic (Within India)' : 'International')
                : '—';
        }
```

- [ ] **Step 6: Sidebar — reword copy, add the add-ons line item**

In `templates/shipments/_create_summary_sidebar.html`, reword the "100% insured" box the same way as step 1's (Step 3 above), and add a new fee row after the existing `.js-consolidation-fee-row`:
```html
        <div class="summary-fees-row js-addons-fee-row" hidden>
            <span class="summary-stat-label">Add-ons</span>
            <span class="summary-stat-value js-sum-addons-fee">INR 0.00</span>
        </div>
```
Back in `create.html`'s `updateSummary()`, add logic to populate and show/hide this row (near the existing `.js-consolidation-fee-row` handling):
```javascript
        let addonsTotal = 0;
        document.querySelectorAll('.addon-checkbox:checked').forEach(function (cb) {
            const opt = ADDON_OPTIONS.find(function (o) { return o.code === cb.dataset.addonCode; });
            if (!opt) return;
            addonsTotal += opt.charge_type === 'percentage'
                ? Math.max(0, opt.floor_or_amount) // recomputed below once declaredValue is known
                : opt.floor_or_amount;
        });
```
This needs `declaredValue` computed before it — since `updateAddonPrices()` already computes `declaredValue` and per-addon prices into the DOM, simplify by summing the already-rendered `.addon-price` text instead of recomputing in two places. Replace the block above with, placed *after* the call to `updateAddonPrices()` inside `updateSummary()`:
```javascript
        let addonsTotal = 0;
        document.querySelectorAll('.addon-checkbox:checked').forEach(function (cb) {
            const priceEl = document.querySelector('.addon-price[data-addon-price="' + cb.dataset.addonCode + '"]');
            if (priceEl) addonsTotal += parseFloat(priceEl.textContent.replace(/[^0-9.]/g, '')) || 0;
        });
        document.querySelectorAll('.js-addons-fee-row').forEach(function (el) { el.hidden = addonsTotal <= 0; });
        document.querySelectorAll('.js-sum-addons-fee').forEach(function (el) { el.textContent = fmtMoney(addonsTotal); });
```
Fold `addonsTotal` into the existing `otherFees` calculation at the bottom of `updateSummary()`:
```javascript
        const otherFees = (showStorageFee ? STORAGE_FEE_PENDING : 0) + (count >= 1 ? CONSOLIDATION_FEE : 0) + addonsTotal;
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe manage.py test apps.shipments.tests.test_shipment_addons.CreateShipmentTemplateTests -v 2`
Expected: PASS (all 4 tests)

- [ ] **Step 8: Manual browser check (JS behavior isn't covered by Django TestCase)**

Run: `.venv\Scripts\python.exe manage.py runserver`, log in as a user with at least one approved parcel with `item_price` set, go through Create Shipment:
- Step 1: check an item, confirm the sidebar's insurance box reads the reworded copy, not "100% insured."
- Step 2: enter an address with country "India" — proceed to step 3, confirm the badge reads "🚚 Domestic Shipment (within India)."
- Go back, change country to e.g. "United States," proceed to step 3 again, confirm the badge updates to "🌍 International Shipment."
- Check the Insurance checkbox, confirm its price reflects `max(declared_value * 2%, 99)` for the checked items; go back to step 1, check an additional high-value item, return to step 3, confirm Insurance's price recomputed live.
- Check Gift Wrapping and Priority Packing, confirm the sidebar's "Add-ons" row appears with the correct sum and the "Estimated Total" includes it.
- Submit; confirm the created shipment has the right `shipment_type` and the right `ShipmentAddon` rows (check via `/manage-rb-panel/` using Task 3's inline).

- [ ] **Step 9: Commit**

```bash
git add templates/shipments/create.html templates/shipments/_create_summary_sidebar.html apps/shipments/tests/test_shipment_addons.py
git commit -m "$(cat <<'EOF'
feat: wizard UI for auto shipment-type badge and add-on checkboxes

Step 3's manual International/Domestic radio is replaced with a
read-only badge derived client-side from the step-2 country field. New
Add-on Services card with four checkboxes, live-priced from currently
checked step-1 items (Insurance) or a flat amount (the other three).
"100% insured" copy on step 1 and the summary sidebar reworded since
Insurance is now a paid add-on rather than a blanket included guarantee.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Post-creation visibility on the shipment detail page

**Files:**
- Modify: `templates/shipments/detail.html`
- Modify: `apps/shipments/views.py` (`ShipmentDetailView.get_context_data` — confirm exact class name by reading the file; it already calls `_payment_summary` for its context, per Task 6)
- Test: `apps/shipments/tests/test_shipment_addons.py`

**Interfaces:**
- Consumes: `shipment.addons.all()` (Task 3's `related_name`), `_payment_summary`'s `addons_amount` (Task 6)

- [ ] **Step 1: Write the failing tests**

Add to `apps/shipments/tests/test_shipment_addons.py`:
```python
class ShipmentDetailAddonsVisibilityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email='detail-addons@example.com', is_active=True)
        Locker.objects.create(user=self.user)
        self.shipment = make_shipment(self.user)
        self.client.force_login(self.user)
        self.url = reverse('shipments:detail', kwargs={'pk': self.shipment.pk})

    def test_no_addons_section_hidden(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Gift Wrapping')

    def test_purchased_addons_are_listed(self):
        ShipmentAddon.objects.create(shipment=self.shipment, code='gift_wrapping', amount=Decimal('99.00'))
        ShipmentAddon.objects.create(shipment=self.shipment, code='insurance', amount=Decimal('120.00'))
        response = self.client.get(self.url)
        self.assertContains(response, 'Gift Wrapping')
        self.assertContains(response, 'Insurance')
        self.assertContains(response, '99.00')
        self.assertContains(response, '120.00')
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe manage.py test apps.shipments.tests.test_shipment_addons.ShipmentDetailAddonsVisibilityTests -v 2`
Expected: FAIL — no add-ons section rendered at all yet.

- [ ] **Step 3: Add the add-ons section to `detail.html`**

In `templates/shipments/detail.html`, inside the Payment Summary card, add a row after the existing `#sd-consolidation-fee-row` block (before `.sd-info-total`):
```html
            {% with shipment_addons=shipment.addons.all %}
            {% if shipment_addons %}
            <div class="sd-info-item" id="sd-addons-fee-row">
                <span class="sd-info-label">Add-ons</span>
                <span class="sd-info-value">
                    {% for addon in shipment_addons %}
                    {{ addon.get_code_display }}: {{ shipment.currency }} {{ addon.amount }}{% if not forloop.last %}, {% endif %}
                    {% endfor %}
                </span>
            </div>
            {% endif %}
            {% endwith %}
```
Also reword the "100% Insured" stat in the Shipment Information card (around line 264-266). Compute the boolean in the view rather than in template logic — in `apps/shipments/views.py`, `ShipmentDetailView.get_context_data` (it already calls `_payment_summary` for its context, per Task 6), add:
```python
        context['has_insurance_addon'] = shipment.addons.filter(code='insurance').exists()
```
then in the template:
```html
                <div class="sd-stat">
                    <span class="sd-info-label">Insurance</span>
                    <span class="sd-stat-value">
                        {% if has_insurance_addon %}<span class="sd-insured">Insured</span>{% else %}Basic protection included{% endif %}
                    </span>
                </div>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe manage.py test apps.shipments.tests.test_shipment_addons.ShipmentDetailAddonsVisibilityTests -v 2`
Expected: PASS

- [ ] **Step 5: Run the full shipments and payments suites one final time**

Run: `.venv\Scripts\python.exe manage.py test apps.shipments apps.payments apps.content apps.accounts apps.locker -v 2`
Expected: all PASS. This is the final full-suite check across every app touched by this plan.

- [ ] **Step 6: Commit**

```bash
git add templates/shipments/detail.html apps/shipments/views.py apps/shipments/tests/test_shipment_addons.py
git commit -m "$(cat <<'EOF'
feat: show purchased add-ons on the shipment detail page

Payment Summary card lists any ShipmentAddon rows with their amounts; the
Shipment Information card's "100% Insured" claim is replaced with an
accurate insured/basic-protection state based on whether the Insurance
add-on was actually purchased. Completes the shipment add-ons feature
(spec: docs/superpowers/specs/2026-08-31-shipment-addons-design.md).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```
