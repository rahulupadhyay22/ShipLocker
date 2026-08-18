import re
import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib import admin as django_admin
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User, Locker
from apps.payments.models import Payment
from . import pricing
from .admin import PersonalShopQuotationAdmin, PersonalShopQuotationAdminForm, PersonalShopRequestAdmin
from .models import (
    PersonalShopRequest, PersonalShopQuotation, PersonalShopQuotationLineItem, PersonalShopImage,
    generate_personal_shop_request_id,
)
from .views import build_timeline


def _make_locker(email):
    user = User.objects.create(email=email, is_active=True)
    return Locker.objects.create(user=user)


def _make_request(locker, status='submitted', **extra):
    return PersonalShopRequest.objects.create(
        locker=locker, request_type='custom_request', status=status,
        destination_country='USA', **extra,
    )


def _make_quotation(req, status='pending', valid_until=None, **extra):
    return PersonalShopQuotation.objects.create(
        request=req, total_amount=Decimal('100.00'), status=status,
        valid_until=valid_until or (timezone.now() + timedelta(hours=48)),
        **extra,
    )


class OwnershipScopingTests(TestCase):
    """Spec Rule: LockerOwnershipMixin on every authenticated view touching a
    PersonalShopRequest/related object — 404, not 403 or 200, for a second user."""

    def setUp(self):
        self.locker_a = _make_locker('owner-a@example.com')
        self.locker_b = _make_locker('owner-b@example.com')
        self.request_a = _make_request(self.locker_a)
        self.quotation_a = _make_quotation(self.request_a)
        self.request_a.active_quotation = self.quotation_a
        self.request_a.status = 'quotation_ready'
        self.request_a.save()
        self.client.force_login(self.locker_b.user)

    def test_foreign_locker_cannot_view_request_detail(self):
        url = reverse('personal_shop:request_detail', args=[self.request_a.pk])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_foreign_locker_cannot_view_quotation(self):
        url = reverse('personal_shop:quotation_detail', args=[self.request_a.pk])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_foreign_locker_cannot_view_payment_confirmation(self):
        url = reverse('personal_shop:payment_confirmation', args=[self.request_a.pk])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_foreign_locker_cannot_get_edit_form(self):
        # request_a is in quotation_ready with an active quotation, but ownership
        # is checked before the edit-permission check — still 404, not 403.
        url = reverse('personal_shop:request_edit', args=[self.request_a.pk])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_foreign_locker_cannot_cancel(self):
        url = reverse('personal_shop:request_cancel', args=[self.request_a.pk])
        self.assertEqual(self.client.post(url).status_code, 404)

    def test_foreign_locker_cannot_decline_quotation(self):
        url = reverse('personal_shop:quotation_decline', args=[self.request_a.pk])
        self.assertEqual(self.client.post(url).status_code, 404)

    def test_foreign_locker_cannot_post_quotation_pay(self):
        url = reverse('personal_shop:quotation_pay', args=[self.request_a.pk])
        self.assertEqual(self.client.post(url).status_code, 404)

    def test_foreign_locker_cannot_see_request_in_list(self):
        url = reverse('personal_shop:request_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(self.request_a, response.context['requests'])

    def test_owner_can_view_own_request(self):
        self.client.force_login(self.locker_a.user)
        url = reverse('personal_shop:request_detail', args=[self.request_a.pk])
        self.assertEqual(self.client.get(url).status_code, 200)


class AuthGuardTests(TestCase):
    """Unauthenticated access to protected TrunkAssist routes redirects to login."""

    def test_dashboard_requires_login(self):
        response = self.client.get(reverse('personal_shop:dashboard'))
        self.assertEqual(response.status_code, 302)

    def test_request_list_requires_login(self):
        response = self.client.get(reverse('personal_shop:request_list'))
        self.assertEqual(response.status_code, 302)


class StatusTransitionGuardTests(TestCase):
    """Spec Rules: edit locked once active_quotation is set; cancel blocked once
    purchased+; expired quotation blocks payment and self-heals status."""

    def setUp(self):
        self.locker = _make_locker('guard-test@example.com')
        self.client.force_login(self.locker.user)

    def test_edit_forbidden_once_quotation_active(self):
        # status alone is still a pre-quotation value ('searching') — isolates
        # the "active_quotation is set" clause of is_editable specifically,
        # rather than piggybacking on a non-editable status too.
        req = _make_request(self.locker, status='searching')
        quotation = _make_quotation(req)
        req.active_quotation = quotation
        req.save()

        url = reverse('personal_shop:request_edit', args=[req.pk])
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {'description': 'x'}).status_code, 403)

    def test_edit_allowed_while_pre_quotation_status(self):
        for status in ('submitted', 'reviewing', 'executive_assigned', 'searching', 'needs_info'):
            with self.subTest(status=status):
                req = _make_request(self.locker, status=status)
                url = reverse('personal_shop:request_edit', args=[req.pk])
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_edit_post_updates_request_when_pre_quotation(self):
        req = _make_request(self.locker, status='submitted')
        url = reverse('personal_shop:request_edit', args=[req.pk])
        response = self.client.post(url, {'description': 'updated description'})
        self.assertRedirects(
            response, reverse('personal_shop:request_detail', args=[req.pk])
        )
        req.refresh_from_db()
        self.assertEqual(req.type_details.get('description'), 'updated description')

    def test_cancel_blocked_once_purchased(self):
        req = _make_request(self.locker, status='purchased')
        url = reverse('personal_shop:request_cancel', args=[req.pk])
        response = self.client.post(url)
        # Spec: "return a 400 with an explanatory message rather than silently no-op"
        self.assertEqual(response.status_code, 400)
        req.refresh_from_db()
        self.assertEqual(req.status, 'purchased')

    def test_cancel_blocked_once_delivered_to_warehouse(self):
        req = _make_request(self.locker, status='delivered_to_warehouse')
        url = reverse('personal_shop:request_cancel', args=[req.pk])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 400)
        req.refresh_from_db()
        self.assertEqual(req.status, 'delivered_to_warehouse')

    def test_cancel_allowed_before_purchased(self):
        req = _make_request(self.locker, status='submitted')
        url = reverse('personal_shop:request_cancel', args=[req.pk])
        response = self.client.post(url)
        self.assertRedirects(response, reverse('personal_shop:request_detail', args=[req.pk]))
        req.refresh_from_db()
        self.assertEqual(req.status, 'cancelled')
        self.assertIsNotNone(req.cancelled_at)

    def test_quotation_view_flips_expired_status_on_load(self):
        req = _make_request(self.locker, status='quotation_ready')
        quotation = _make_quotation(req, valid_until=timezone.now() - timedelta(hours=1))
        req.active_quotation = quotation
        req.save()

        url = reverse('personal_shop:quotation_detail', args=[req.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        quotation.refresh_from_db()
        req.refresh_from_db()
        self.assertEqual(quotation.status, 'expired')
        self.assertEqual(req.status, 'quotation_expired')

    def test_expired_quotation_blocks_payment_order_and_flips_status(self):
        req = _make_request(self.locker, status='quotation_ready')
        quotation = _make_quotation(req, valid_until=timezone.now() - timedelta(hours=1))
        req.active_quotation = quotation
        req.save()

        url = reverse('personal_shop:quotation_pay', args=[req.pk])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 400)

        quotation.refresh_from_db()
        req.refresh_from_db()
        self.assertEqual(quotation.status, 'expired')
        self.assertEqual(req.status, 'quotation_expired')
        self.assertFalse(Payment.objects.filter(personal_shop_request=req).exists())


class QuotationDeclineTests(TestCase):
    """Spec DoD: 'Decline' sets quotation_declined / declined without creating a Payment."""

    def setUp(self):
        self.locker = _make_locker('decline-test@example.com')
        self.client.force_login(self.locker.user)
        self.req = _make_request(self.locker, status='quotation_ready')
        self.quotation = _make_quotation(self.req)
        self.req.active_quotation = self.quotation
        self.req.save()

    def test_decline_sets_statuses_and_creates_no_payment(self):
        url = reverse('personal_shop:quotation_decline', args=[self.req.pk])
        response = self.client.post(url)
        self.assertRedirects(response, reverse('personal_shop:request_detail', args=[self.req.pk]))

        self.quotation.refresh_from_db()
        self.req.refresh_from_db()
        self.assertEqual(self.quotation.status, 'declined')
        self.assertEqual(self.req.status, 'quotation_declined')
        self.assertFalse(Payment.objects.filter(personal_shop_request=self.req).exists())


class RequestCreationTests(TestCase):
    """Spec DoD: each of the 6 request-type forms creates a PersonalShopRequest
    with the correct request_type, correct initial status, and a display ID in
    <locker-id>-TA### format."""

    FORM_DATA = {
        'product_link': {
            'product_url': 'https://example.com/item',
            'quantity': 2, 'size': 'M', 'colour': 'Red', 'notes': 'test',
        },
        'image_search': {'description': 'a nice handbag'},
        'cart_screenshot': {'description': 'items in my cart'},
        'boutique_purchase': {
            'boutique_name': 'Test Boutique',
            'item_description': 'a silk dress',
            'preferred_size': 'M',
        },
        'local_shop_purchase': {
            'shop_name': 'Test Shop',
            'city': 'hyderabad',
            'shop_address': '123 Street, Landmark',
            'shop_phone': '9999999999',
            'item_description': 'leather shoes',
        },
        'custom_request': {'description': 'find me something rare'},
    }

    def setUp(self):
        self.locker = _make_locker('creator@example.com')
        self.client.force_login(self.locker.user)

    def test_submitting_each_request_type_creates_request_with_expected_fields(self):
        for request_type, data in self.FORM_DATA.items():
            with self.subTest(request_type=request_type):
                url = reverse('personal_shop:request_create', args=[request_type])
                response = self.client.post(url, data)

                req = PersonalShopRequest.objects.filter(
                    locker=self.locker, request_type=request_type
                ).order_by('-created_at').first()
                self.assertIsNotNone(req, f"no request created for {request_type}")
                self.assertRedirects(
                    response, reverse('personal_shop:request_detail', args=[req.pk])
                )

                expected_status = 'reviewing' if request_type == 'custom_request' else 'submitted'
                self.assertEqual(req.status, expected_status)

                self.assertIsNotNone(req.display_id)
                self.assertTrue(
                    re.match(rf'^{re.escape(self.locker.locker_id)}-TA\d{{3}}$', req.display_id),
                    f"unexpected display_id format: {req.display_id}",
                )

    def test_unknown_request_type_404s_on_get(self):
        url = reverse('personal_shop:request_create', args=['not_a_real_type'])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_unknown_request_type_404s_on_post(self):
        url = reverse('personal_shop:request_create', args=['not_a_real_type'])
        self.assertEqual(self.client.post(url, {}).status_code, 404)

    def test_invalid_form_does_not_create_request(self):
        # product_link requires product_url
        url = reverse('personal_shop:request_create', args=['product_link'])
        before = PersonalShopRequest.objects.count()
        response = self.client.post(url, {})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(PersonalShopRequest.objects.count(), before)


class RequestListFilterPaginationTests(TestCase):
    """Spec Route: My Requests list filters by status/request_type query params,
    paginate_by = 20, ownership-scoped."""

    def setUp(self):
        self.locker = _make_locker('lister@example.com')
        self.client.force_login(self.locker.user)

    def test_filters_by_status(self):
        _make_request(self.locker, status='submitted')
        target = _make_request(self.locker, status='purchased')
        url = reverse('personal_shop:request_list')
        response = self.client.get(url, {'status': 'purchased'})
        results = list(response.context['requests'])
        self.assertEqual(results, [target])

    def test_filters_by_request_type(self):
        product_link_req = PersonalShopRequest.objects.create(
            locker=self.locker, request_type='product_link', destination_country='USA',
        )
        _make_request(self.locker, status='submitted')  # custom_request type
        url = reverse('personal_shop:request_list')
        response = self.client.get(url, {'request_type': 'product_link'})
        results = list(response.context['requests'])
        self.assertEqual(results, [product_link_req])

    def test_paginated_at_20_per_page(self):
        for _ in range(25):
            _make_request(self.locker, status='submitted')
        url = reverse('personal_shop:request_list')
        response = self.client.get(url)
        self.assertTrue(response.context['is_paginated'])
        self.assertEqual(len(response.context['requests']), 20)

        response_page_2 = self.client.get(url, {'page': 2})
        self.assertEqual(len(response_page_2.context['requests']), 5)


class QuotationUniqueConstraintTests(TestCase):
    """Spec: UniqueConstraint on PersonalShopQuotation(request, status='pending')
    is the DB-level backstop against two pending quotations existing at once."""

    def setUp(self):
        self.locker = _make_locker('constraint-test@example.com')
        self.req = _make_request(self.locker, status='searching')
        _make_quotation(self.req, status='pending')

    def test_second_pending_quotation_for_same_request_raises_integrity_error(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                _make_quotation(self.req, status='pending')

    def test_second_non_pending_quotation_for_same_request_is_allowed(self):
        # declined/expired/approved rows don't collide with the partial index
        second = _make_quotation(self.req, status='declined')
        self.assertIsNotNone(second.pk)


class SequentialDisplayIdTests(TestCase):
    """Regression coverage for the first-request race fix (locks the Locker row,
    not just existing children, so a brand-new locker's first request is also
    race-safe). True concurrent-thread coverage isn't practical against the
    SQLite test database used in this suite; this locks down the generator's
    sequencing logic instead.
    """

    def test_first_and_second_request_get_distinct_sequential_ids(self):
        locker = _make_locker('sequence-test@example.com')
        first_id = generate_personal_shop_request_id(locker)
        PersonalShopRequest.objects.create(
            locker=locker, request_type='custom_request', display_id=first_id,
        )
        second_id = generate_personal_shop_request_id(locker)

        self.assertTrue(first_id.endswith('-TA001'))
        self.assertTrue(second_id.endswith('-TA002'))
        self.assertNotEqual(first_id, second_id)


def _admin_request(user):
    """Bare RequestFactory request wired up enough for ModelAdmin.message_user
    (needs a session + messages storage) and has_perm checks (needs .user)."""
    request = RequestFactory().get('/')
    request.user = user
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


class _FakeQuotationForm:
    """Stand-in for a bound ModelForm — save_related only touches .instance
    and .save_m2m(), so a full form/formset round-trip isn't needed to
    exercise the real total-amount computation in save_related."""
    def __init__(self, instance):
        self.instance = instance

    def save_m2m(self):
        pass


class SuggestedServiceFeePricingTests(TestCase):
    """Spec 10 §pricing.py — suggested (never enforced) fee table from the
    CamelTrunk service-charge PDF."""

    def test_minimum_applies_when_no_product_value_given(self):
        self.assertEqual(pricing.suggested_service_fee('product_link'), Decimal('199'))
        self.assertEqual(pricing.suggested_service_fee('image_search'), Decimal('299'))
        self.assertEqual(pricing.suggested_service_fee('cart_screenshot'), Decimal('299'))
        self.assertEqual(pricing.suggested_service_fee('boutique_purchase'), Decimal('399'))

    def test_rate_used_when_it_exceeds_minimum(self):
        # 5% of 10000 = 500, which is above the 199 minimum.
        self.assertEqual(pricing.suggested_service_fee('product_link', Decimal('10000')), Decimal('500.00'))

    def test_minimum_wins_when_rate_falls_below_it(self):
        # 5% of 1000 = 50, below the 199 minimum — minimum applies.
        self.assertEqual(pricing.suggested_service_fee('product_link', Decimal('1000')), Decimal('199'))

    def test_flat_fee_ignores_product_value(self):
        self.assertEqual(pricing.suggested_service_fee('local_shop_purchase'), Decimal('499'))
        self.assertEqual(pricing.suggested_service_fee('local_shop_purchase', Decimal('50000')), Decimal('499'))

    def test_custom_request_returns_starting_fee(self):
        self.assertEqual(pricing.suggested_service_fee('custom_request'), Decimal('499'))
        self.assertEqual(pricing.suggested_service_fee('custom_request', Decimal('99999')), Decimal('499'))

    def test_unknown_request_type_returns_none(self):
        self.assertIsNone(pricing.suggested_service_fee('not_a_real_type'))


class QuotationIsRefundableTests(TestCase):
    """Spec 10: is_refundable is a post-payment-only check — True unless the
    quotation is a research_fee/expense_advance AND work has started."""

    def setUp(self):
        self.locker = _make_locker('refundable-test@example.com')
        self.req = _make_request(self.locker, status='searching')

    def test_purchase_type_always_refundable(self):
        quotation = _make_quotation(self.req, quotation_type='purchase')
        self.assertTrue(quotation.is_refundable)
        self.req.work_started_at = timezone.now()
        self.req.save()
        self.assertTrue(quotation.is_refundable)

    def test_research_fee_refundable_before_work_started(self):
        quotation = _make_quotation(self.req, quotation_type='research_fee')
        self.assertTrue(quotation.is_refundable)

    def test_research_fee_not_refundable_after_work_started(self):
        quotation = _make_quotation(self.req, quotation_type='research_fee')
        self.req.work_started_at = timezone.now()
        self.req.save()
        self.assertFalse(quotation.is_refundable)

    def test_expense_advance_not_refundable_after_work_started(self):
        # expense_advance is only valid on boutique_purchase/local_shop_purchase,
        # not the class's default custom_request self.req — needs its own request.
        req = PersonalShopRequest.objects.create(
            locker=_make_locker('expense-advance-refundable@example.com'),
            request_type='local_shop_purchase', status='searching', destination_country='USA',
        )
        quotation = _make_quotation(req, quotation_type='expense_advance')
        req.work_started_at = timezone.now()
        req.save()
        self.assertFalse(quotation.is_refundable)


class MarkSearchingOrWorkStartedActionTests(TestCase):
    """mark_searching is a merged action: ordinary pre-quotation "searching" for
    most rows, but rows carrying an approved research_fee/expense_advance
    quotation with no work_started_at yet get the work-started stamp instead —
    never both, so an already-paid request's status is never regressed back to
    'searching'. The work-started branch stays permission-gated even though it's
    no longer a separately-hidden action."""

    def setUp(self):
        self.locker = _make_locker('work-started-test@example.com')
        self.req = _make_request(self.locker, status='paid')
        self.staff_no_perm = User.objects.create(email='staff-no-perm@example.com', is_staff=True)
        self.staff_with_perm = User.objects.create(email='staff-with-perm@example.com', is_staff=True)
        perm = Permission.objects.get(
            codename='mark_work_started',
            content_type=ContentType.objects.get_for_model(PersonalShopRequest),
        )
        self.staff_with_perm.user_permissions.add(perm)
        self.admin_instance = PersonalShopRequestAdmin(PersonalShopRequest, django_admin.site)

    def _quotation(self, quotation_type='research_fee', status='approved'):
        quotation = _make_quotation(self.req, quotation_type=quotation_type, status=status)
        self.req.active_quotation = quotation
        self.req.save()
        return quotation

    def _run(self, user):
        self.admin_instance.mark_searching(
            _admin_request(user), PersonalShopRequest.objects.filter(pk=self.req.pk)
        )

    def test_ordinary_request_gets_marked_searching(self):
        # No active_quotation at all — the common case, any of the 6 request types.
        self._run(self.staff_no_perm)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, 'searching')
        self.assertIsNotNone(self.req.searching_started_at)
        self.assertIsNone(self.req.work_started_at)

    def test_eligible_request_gets_work_started_not_searching(self):
        self._quotation()
        self._run(self.staff_with_perm)
        self.req.refresh_from_db()
        self.assertIsNotNone(self.req.work_started_at)
        self.assertEqual(self.req.work_started_by, self.staff_with_perm)
        # status must NOT regress to 'searching' — it stays 'paid'.
        self.assertEqual(self.req.status, 'paid')

    def test_eligible_request_rejected_without_permission_and_not_marked_searching_either(self):
        self._quotation()
        self._run(self.staff_no_perm)
        self.req.refresh_from_db()
        self.assertIsNone(self.req.work_started_at)
        # Denied the work-started path, but must not silently fall through to
        # the searching path either — that would still regress its status.
        self.assertEqual(self.req.status, 'paid')

    def test_purchase_type_quotation_falls_through_to_searching(self):
        self._quotation(quotation_type='purchase')
        self._run(self.staff_with_perm)
        self.req.refresh_from_db()
        self.assertIsNone(self.req.work_started_at)
        self.assertEqual(self.req.status, 'searching')

    def test_unapproved_quotation_falls_through_to_searching(self):
        self._quotation(status='pending')
        self._run(self.staff_with_perm)
        self.req.refresh_from_db()
        self.assertIsNone(self.req.work_started_at)
        self.assertEqual(self.req.status, 'searching')

    def test_idempotent_does_not_overwrite_existing_work_started_timestamp(self):
        self._quotation()
        original = timezone.now() - timedelta(days=1)
        self.req.work_started_at = original
        self.req.work_started_by = self.staff_no_perm
        self.req.save()

        self._run(self.staff_with_perm)
        self.req.refresh_from_db()
        self.assertEqual(self.req.work_started_at, original)
        self.assertEqual(self.req.work_started_by, self.staff_no_perm)
        # Must not fall through to the searching branch just because it no
        # longer matches "work_started_at is null" — status stays 'paid'.
        self.assertEqual(self.req.status, 'paid')


class QuotationTotalAmountForPurchaseTests(TestCase):
    """Purchase quotations total = subtotal (line items) + service fee +
    domestic shipping + payment gateway charge. research_fee_amount and
    travel_expense_amount are ignored even if set — they only apply to the
    other two quotation types."""

    def setUp(self):
        self.locker = _make_locker('purchase-total-test@example.com')
        self.req = _make_request(self.locker, status='searching')  # custom_request
        self.quotation = _make_quotation(
            self.req, quotation_type='purchase',
            domestic_shipping_amount=Decimal('50'), service_fee_amount=Decimal('100'),
            payment_gateway_charge=Decimal('10'),
            research_fee_amount=Decimal('999'), travel_expense_amount=Decimal('999'),
        )
        PersonalShopQuotationLineItem.objects.create(
            quotation=self.quotation, name='Item', qty=1, unit_amount=Decimal('200'),
        )

    def test_total_amount_excludes_research_and_travel_fields(self):
        admin_instance = PersonalShopQuotationAdmin(PersonalShopQuotation, django_admin.site)
        admin_instance.save_related(
            _admin_request(self.locker.user), _FakeQuotationForm(self.quotation), [], change=True,
        )
        self.quotation.refresh_from_db()
        self.assertEqual(self.quotation.subtotal, Decimal('200.00'))
        # 50 + 100 + 10 + 200 subtotal — NOT +999 +999 from the other two fields.
        self.assertEqual(self.quotation.total_amount, Decimal('360.00'))


class QuotationTotalAmountForResearchFeeTests(TestCase):
    """Research Fee quotations total = research_fee_amount only — domestic
    shipping, service fee, payment gateway charge, and line items are all
    excluded, since this is a standalone upfront fee before any purchase."""

    def setUp(self):
        self.locker = _make_locker('research-fee-total-test@example.com')
        self.req = PersonalShopRequest.objects.create(
            locker=self.locker, request_type='custom_request',
            status='searching', destination_country='USA',
        )
        self.quotation = _make_quotation(
            self.req, quotation_type='research_fee',
            domestic_shipping_amount=Decimal('50'), service_fee_amount=Decimal('100'),
            research_fee_amount=Decimal('499'), payment_gateway_charge=Decimal('10'),
        )

    def test_total_amount_is_research_fee_only(self):
        admin_instance = PersonalShopQuotationAdmin(PersonalShopQuotation, django_admin.site)
        admin_instance.save_related(
            _admin_request(self.locker.user), _FakeQuotationForm(self.quotation), [], change=True,
        )
        self.quotation.refresh_from_db()
        # NOT 50 + 100 + 499 + 10 — just the research fee.
        self.assertEqual(self.quotation.total_amount, Decimal('499'))

    def test_negative_research_fee_fails_full_clean(self):
        with self.assertRaises(ValidationError):
            _make_quotation(
                PersonalShopRequest.objects.create(
                    locker=_make_locker('negative-research-fee-test@example.com'),
                    request_type='custom_request', status='searching', destination_country='USA',
                ),
                research_fee_amount=Decimal('-499'),
            ).full_clean()


class QuotationTotalAmountForExpenseAdvanceTests(TestCase):
    """Expense Advance quotations total = travel_expense_amount only — same
    standalone-upfront-fee treatment as Research Fee."""

    def setUp(self):
        self.locker = _make_locker('expense-advance-total-test@example.com')
        self.req = PersonalShopRequest.objects.create(
            locker=self.locker, request_type='local_shop_purchase',
            status='searching', destination_country='USA',
        )
        self.quotation = _make_quotation(
            self.req, quotation_type='expense_advance',
            domestic_shipping_amount=Decimal('50'), service_fee_amount=Decimal('100'),
            travel_expense_amount=Decimal('75'), payment_gateway_charge=Decimal('10'),
        )
        PersonalShopQuotationLineItem.objects.create(
            quotation=self.quotation, name='Item', qty=1, unit_amount=Decimal('200'),
        )

    def test_total_amount_is_travel_expense_only(self):
        admin_instance = PersonalShopQuotationAdmin(PersonalShopQuotation, django_admin.site)
        admin_instance.save_related(
            _admin_request(self.locker.user), _FakeQuotationForm(self.quotation), [], change=True,
        )
        self.quotation.refresh_from_db()
        # subtotal is still computed from line items (unaffected), but
        # total_amount ignores everything except travel_expense_amount.
        self.assertEqual(self.quotation.subtotal, Decimal('200.00'))
        self.assertEqual(self.quotation.total_amount, Decimal('75'))


class SecondQuotationDoesNotRewriteTimelineTests(TestCase):
    """Regression test: a request cycling through a second quotation (spec 10's
    research-fee-then-purchase flow) must not push quotation_ready_at later than
    an already-stamped paid_at — the customer timeline renders fixed steps in a
    fixed order, so a re-stamped quotation_ready_at would show "Quotation Ready"
    dated after "Paid" even though Paid is the later step."""

    def setUp(self):
        self.locker = _make_locker('timeline-test@example.com')
        self.req = _make_request(self.locker, status='reviewing')
        self.admin_instance = PersonalShopQuotationAdmin(PersonalShopQuotation, django_admin.site)

    def _approve_quotation(self, quotation):
        self.admin_instance.save_related(
            _admin_request(self.locker.user), _FakeQuotationForm(quotation), [], change=True,
        )

    def test_quotation_ready_at_not_overwritten_by_second_quotation(self):
        first = _make_quotation(self.req, quotation_type='research_fee', status='pending')
        self._approve_quotation(first)
        self.req.refresh_from_db()
        first_quotation_ready_at = self.req.quotation_ready_at
        self.assertIsNotNone(first_quotation_ready_at)

        self.req.mark_paid()
        self.req.refresh_from_db()
        self.assertIsNotNone(self.req.paid_at)

        second = _make_quotation(self.req, quotation_type='purchase', status='pending')
        self._approve_quotation(second)
        self.req.refresh_from_db()

        self.assertEqual(self.req.quotation_ready_at, first_quotation_ready_at)
        self.assertLessEqual(self.req.quotation_ready_at, self.req.paid_at)


class TimelinePaidStepHidesWhileAwaitingNewQuotationTests(TestCase):
    """Regression test: once a second quotation cycle is underway (spec 10's
    research-fee-then-purchase / expense-advance-then-settlement flows), the
    Request Progress timeline's "Paid" step must stop reading as done — a
    customer looking at the page mid-second-round should see that a fresh
    payment is pending, not that they're already fully paid."""

    def setUp(self):
        self.req = _make_request(_make_locker('timeline-paid-step@example.com'), status='paid')
        self.req.paid_at = timezone.now()
        self.req.save()

    def test_paid_step_done_in_ordinary_paid_status(self):
        timeline = build_timeline(self.req)
        paid_step = next(s for s in timeline if s['label'] == 'Paid')
        self.assertTrue(paid_step['done'])
        self.assertIsNotNone(paid_step['timestamp'])

    def test_paid_step_not_done_while_awaiting_new_quotation(self):
        for status in ('quotation_ready', 'quotation_declined', 'quotation_expired', 'payment_pending'):
            with self.subTest(status=status):
                self.req.status = status
                timeline = build_timeline(self.req)
                paid_step = next(s for s in timeline if s['label'] == 'Paid')
                self.assertFalse(paid_step['done'])
                self.assertIsNone(paid_step['timestamp'])

    def test_paid_step_done_again_once_second_round_is_paid(self):
        self.req.status = 'quotation_ready'
        self.assertFalse(next(s for s in build_timeline(self.req) if s['label'] == 'Paid')['done'])
        self.req.status = 'paid'
        self.assertTrue(next(s for s in build_timeline(self.req) if s['label'] == 'Paid')['done'])


class QuotationTravelExpenseValidationTests(TestCase):
    """Spec 10 Gap 5 fix: a negative travel_expense_amount is rejected, not
    silently under-totaled."""

    def test_negative_travel_expense_fails_full_clean(self):
        req = _make_request(_make_locker('negative-travel-test@example.com'), status='searching')
        quotation = _make_quotation(req, travel_expense_amount=Decimal('-500'))
        with self.assertRaises(ValidationError):
            quotation.full_clean()


class QuotationTypeMatchesRequestTypeTests(TestCase):
    """research_fee is only valid on a Custom Request; expense_advance only on
    Boutique/Local Shop Purchase — staff should not be able to create a
    research_fee/expense_advance quotation on a request type the PDF never
    describes that stage for (e.g. Product Link)."""

    def _request(self, request_type):
        return PersonalShopRequest.objects.create(
            locker=_make_locker(f'{request_type}-{uuid.uuid4()}@example.com'),
            request_type=request_type, destination_country='USA',
        )

    def test_research_fee_valid_on_custom_request(self):
        req = self._request('custom_request')
        quotation = _make_quotation(req, quotation_type='research_fee')
        quotation.full_clean()  # must not raise

    def test_expense_advance_valid_on_boutique_purchase(self):
        req = self._request('boutique_purchase')
        quotation = _make_quotation(req, quotation_type='expense_advance')
        quotation.full_clean()  # must not raise

    def test_expense_advance_valid_on_local_shop_purchase(self):
        req = self._request('local_shop_purchase')
        quotation = _make_quotation(req, quotation_type='expense_advance')
        quotation.full_clean()  # must not raise

    def test_purchase_valid_on_any_request_type(self):
        for request_type in ('product_link', 'image_search', 'cart_screenshot', 'custom_request'):
            with self.subTest(request_type=request_type):
                req = self._request(request_type)
                quotation = _make_quotation(req, quotation_type='purchase')
                quotation.full_clean()  # must not raise

    def test_research_fee_invalid_on_product_link_raises_on_full_clean(self):
        # Unsaved instance — save() would raise first otherwise, this isolates
        # the clean()-layer check (the admin-form-facing path) specifically.
        req = self._request('product_link')
        quotation = PersonalShopQuotation(
            request=req, quotation_type='research_fee',
            valid_until=timezone.now() + timedelta(hours=48),
        )
        with self.assertRaises(ValidationError):
            quotation.full_clean()

    def test_research_fee_invalid_on_product_link_raises_on_save(self):
        # The hard guarantee — holds even bypassing full_clean() (shell/script edit).
        req = self._request('product_link')
        with self.assertRaises(ValidationError):
            _make_quotation(req, quotation_type='research_fee')

    def test_expense_advance_invalid_on_custom_request_raises_on_save(self):
        req = self._request('custom_request')
        with self.assertRaises(ValidationError):
            _make_quotation(req, quotation_type='expense_advance')

    def test_admin_form_rejects_mismatched_quotation_type(self):
        req = self._request('product_link')
        quotation = PersonalShopQuotation(
            request=req, quotation_type='purchase', valid_until=timezone.now() + timedelta(hours=48),
        )
        quotation.save()
        form = PersonalShopQuotationAdminForm(
            data={
                'request': req.pk, 'quotation_type': 'research_fee',
                'domestic_shipping_amount': '0', 'service_fee_amount': '0',
                'travel_expense_amount': '0', 'payment_gateway_charge': '0',
                'subtotal': '0', 'total_amount': '0',
                'valid_until': quotation.valid_until.strftime('%Y-%m-%d %H:%M:%S'),
                'status': 'pending',
            },
            instance=quotation,
        )
        self.assertFalse(form.is_valid())
        self.assertIn('quotation_type', form.errors)


class QuotationTypeLockedAfterApprovalTests(TestCase):
    """Spec 10 Gap 4 fix — two layers: the model itself refuses to save a
    quotation_type change on an approved quotation (the actual guarantee,
    holds even from the Django shell), and the admin form rejects the same
    edit earlier with a friendlier error."""

    def setUp(self):
        self.locker = _make_locker('lock-test@example.com')
        self.req = _make_request(self.locker, status='paid')

    def test_model_save_rejects_type_change_after_approval(self):
        quotation = _make_quotation(self.req, quotation_type='purchase', status='approved')
        quotation.quotation_type = 'research_fee'
        with self.assertRaises(ValidationError):
            quotation.save()

    def test_model_save_allows_type_change_while_pending(self):
        quotation = _make_quotation(self.req, quotation_type='purchase', status='pending')
        quotation.quotation_type = 'research_fee'
        quotation.save()
        quotation.refresh_from_db()
        self.assertEqual(quotation.quotation_type, 'research_fee')

    def test_admin_form_rejects_type_change_after_approval(self):
        quotation = _make_quotation(self.req, quotation_type='purchase', status='approved')
        form = PersonalShopQuotationAdminForm(
            data={'quotation_type': 'research_fee'}, instance=quotation,
        )
        form.is_valid()
        self.assertIn('quotation_type', form.errors)


class QuotationTemplateRefundabilityMessageTests(TestCase):
    """Regression test for the spec-review bug: the pre-payment refundability
    sentence must be driven by quotation_type, not is_refundable (which is
    always True before payment, since work_started_at can't be set yet)."""

    def setUp(self):
        self.locker = _make_locker('refund-message-test@example.com')
        self.client.force_login(self.locker.user)

    def _quotation_page(self, quotation_type):
        req = _make_request(self.locker, status='quotation_ready')
        quotation = _make_quotation(req, quotation_type=quotation_type, status='pending')
        req.active_quotation = quotation
        req.save()
        url = reverse('personal_shop:quotation_detail', args=[req.pk])
        return self.client.get(url)

    def test_research_fee_shows_non_refundable_warning(self):
        response = self._quotation_page('research_fee')
        self.assertContains(response, 'This fee becomes non-refundable once we begin work.')
        self.assertNotContains(response, 'Fully refundable if you decline before purchase.')

    def test_purchase_shows_fully_refundable_message(self):
        response = self._quotation_page('purchase')
        self.assertContains(response, 'Fully refundable if you decline before purchase.')
        self.assertNotContains(response, 'This fee becomes non-refundable once we begin work.')


class SuggestedFeeAdminEndpointTests(TestCase):
    """The admin-internal JSON endpoint the JS prefill hits — registered via
    PersonalShopQuotationAdmin.get_urls(), not the app's public urls.py."""

    def setUp(self):
        self.locker = _make_locker('endpoint-test@example.com')
        self.staff = User.objects.create(
            email='endpoint-staff@example.com', is_staff=True, is_superuser=True,
        )
        self.req = _make_request(self.locker, status='reviewing')  # custom_request type

    def test_returns_suggested_fee_for_known_request(self):
        self.client.force_login(self.staff)
        url = reverse('admin:personal_shop_quotation_suggested_fee', args=[self.req.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['fee'], '499.00')
        self.assertEqual(data['request_type'], 'custom_request')
        self.assertEqual(set(data['allowed_quotation_types']), {'purchase', 'research_fee'})

    def test_product_value_above_minimum_uses_percentage(self):
        # Regression: product_value was never actually passed through before,
        # so the percentage math never ran — the endpoint always silently
        # returned just the flat minimum. product_link is 5% (min ₹199);
        # 5% of 10000 = 500, above the minimum, so 500 should win.
        req = PersonalShopRequest.objects.create(
            locker=self.locker, request_type='product_link', destination_country='USA',
        )
        self.client.force_login(self.staff)
        url = reverse('admin:personal_shop_quotation_suggested_fee', args=[req.pk])
        response = self.client.get(url, {'product_value': '10000'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['fee'], '500.00')

    def test_product_value_below_minimum_still_uses_minimum(self):
        req = PersonalShopRequest.objects.create(
            locker=self.locker, request_type='product_link', destination_country='USA',
        )
        self.client.force_login(self.staff)
        url = reverse('admin:personal_shop_quotation_suggested_fee', args=[req.pk])
        response = self.client.get(url, {'product_value': '1000'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['fee'], '199.00')

    def test_invalid_product_value_falls_back_to_minimum_not_500(self):
        req = PersonalShopRequest.objects.create(
            locker=self.locker, request_type='product_link', destination_country='USA',
        )
        self.client.force_login(self.staff)
        url = reverse('admin:personal_shop_quotation_suggested_fee', args=[req.pk])
        response = self.client.get(url, {'product_value': 'not-a-number'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['fee'], '199.00')

    def test_nan_product_value_does_not_500(self):
        # Regression: Decimal('NaN') parses without raising InvalidOperation,
        # but the later rate comparison in ServiceCharge.compute() raises on
        # a NaN operand — must be rejected before it gets that far.
        req = PersonalShopRequest.objects.create(
            locker=self.locker, request_type='product_link', destination_country='USA',
        )
        self.client.force_login(self.staff)
        url = reverse('admin:personal_shop_quotation_suggested_fee', args=[req.pk])
        response = self.client.get(url, {'product_value': 'NaN'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['fee'], '199.00')

    def test_infinite_product_value_does_not_500(self):
        # Regression: Decimal('Infinity')/a huge exponent parses fine but
        # overflows during the rate multiplication — same fix as NaN above.
        req = PersonalShopRequest.objects.create(
            locker=self.locker, request_type='product_link', destination_country='USA',
        )
        self.client.force_login(self.staff)
        url = reverse('admin:personal_shop_quotation_suggested_fee', args=[req.pk])
        response = self.client.get(url, {'product_value': 'Infinity'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['fee'], '199.00')

    def test_staff_without_view_permission_gets_404(self):
        # admin_view() (get_urls) only enforces is_active/is_staff, not the
        # model-level view permission — the view itself must check it too.
        unpermitted_staff = User.objects.create(
            email='no-view-perm-staff@example.com', is_staff=True, is_superuser=False,
        )
        self.client.force_login(unpermitted_staff)
        url = reverse('admin:personal_shop_quotation_suggested_fee', args=[self.req.pk])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_allowed_quotation_types_for_boutique_purchase(self):
        req = PersonalShopRequest.objects.create(
            locker=self.locker, request_type='boutique_purchase', destination_country='USA',
        )
        self.client.force_login(self.staff)
        url = reverse('admin:personal_shop_quotation_suggested_fee', args=[req.pk])
        response = self.client.get(url)
        self.assertEqual(set(response.json()['allowed_quotation_types']), {'purchase', 'expense_advance'})

    def test_allowed_quotation_types_for_local_shop_purchase(self):
        req = PersonalShopRequest.objects.create(
            locker=self.locker, request_type='local_shop_purchase', destination_country='USA',
        )
        self.client.force_login(self.staff)
        url = reverse('admin:personal_shop_quotation_suggested_fee', args=[req.pk])
        response = self.client.get(url)
        self.assertEqual(set(response.json()['allowed_quotation_types']), {'purchase', 'expense_advance'})

    def test_allowed_quotation_types_for_product_link_is_purchase_only(self):
        req = PersonalShopRequest.objects.create(
            locker=self.locker, request_type='product_link', destination_country='USA',
        )
        self.client.force_login(self.staff)
        url = reverse('admin:personal_shop_quotation_suggested_fee', args=[req.pk])
        response = self.client.get(url)
        self.assertEqual(response.json()['allowed_quotation_types'], ['purchase'])

    def test_unknown_request_id_404s(self):
        self.client.force_login(self.staff)
        url = reverse('admin:personal_shop_quotation_suggested_fee', args=[uuid.uuid4()])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_anonymous_redirected_to_admin_login(self):
        url = reverse('admin:personal_shop_quotation_suggested_fee', args=[self.req.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)


# ---------------------------------------------------------------------------
# Spec 10 independent verification pass — written directly from
# .claude/specs/10-trunk-assist-service-charges.md's Model changes / Templates
# / Rules for implementation / Definition of done sections, without reading
# test logic out of the classes above. Duplication with the classes above is
# expected and fine; this pass exists to catch spec/implementation drift the
# earlier (implementation-derived) tests would not surface.
# ---------------------------------------------------------------------------


class Spec10VerifyQuotationTypeConstraintTests(TestCase):
    """Model changes: quotation_type default is 'purchase'; research_fee is
    only valid for custom_request; expense_advance is only valid for
    boutique_purchase/local_shop_purchase; purchase is valid for any of the
    6 request types (QUOTATION_TYPE_ALLOWED_REQUEST_TYPES / clean() / save()
    per the spec's "Model changes" and Rules sections)."""

    ALL_REQUEST_TYPES = [
        'product_link', 'image_search', 'cart_screenshot',
        'boutique_purchase', 'local_shop_purchase', 'custom_request',
    ]

    def _request(self, request_type):
        return PersonalShopRequest.objects.create(
            locker=_make_locker(f'{request_type}-{uuid.uuid4()}@example.com'),
            request_type=request_type, destination_country='USA',
        )

    def test_default_quotation_type_is_purchase(self):
        req = self._request('product_link')
        quotation = _make_quotation(req)  # no quotation_type kwarg passed
        self.assertEqual(quotation.quotation_type, 'purchase')

    def test_research_fee_valid_only_on_custom_request(self):
        for request_type in self.ALL_REQUEST_TYPES:
            with self.subTest(request_type=request_type):
                req = self._request(request_type)
                if request_type == 'custom_request':
                    quotation = _make_quotation(req, quotation_type='research_fee')
                    quotation.full_clean()  # must not raise
                    self.assertEqual(quotation.quotation_type, 'research_fee')
                else:
                    with self.assertRaises(ValidationError):
                        _make_quotation(req, quotation_type='research_fee')

    def test_expense_advance_valid_only_on_boutique_or_local_shop_purchase(self):
        for request_type in self.ALL_REQUEST_TYPES:
            with self.subTest(request_type=request_type):
                req = self._request(request_type)
                if request_type in ('boutique_purchase', 'local_shop_purchase'):
                    quotation = _make_quotation(req, quotation_type='expense_advance')
                    quotation.full_clean()  # must not raise
                    self.assertEqual(quotation.quotation_type, 'expense_advance')
                else:
                    with self.assertRaises(ValidationError):
                        _make_quotation(req, quotation_type='expense_advance')

    def test_purchase_valid_on_every_request_type(self):
        for request_type in self.ALL_REQUEST_TYPES:
            with self.subTest(request_type=request_type):
                req = self._request(request_type)
                quotation = _make_quotation(req, quotation_type='purchase')
                quotation.full_clean()  # must not raise

    def test_invalid_type_rejected_on_clean_not_just_save(self):
        # clean() is the admin-form-facing layer — should reject before an
        # unsaved instance ever reaches save().
        req = self._request('product_link')
        quotation = PersonalShopQuotation(
            request=req, quotation_type='expense_advance',
            valid_until=timezone.now() + timedelta(hours=48),
        )
        with self.assertRaises(ValidationError):
            quotation.full_clean()


class Spec10VerifyIsRefundablePropertyTests(TestCase):
    """Model changes: is_refundable is True unless quotation_type is
    research_fee/expense_advance AND request.work_started_at is set. Spec
    explicitly states this is a post-payment-only check that must never read
    False before payment, since work_started_at can't be set until the
    gating quotation is approved."""

    def test_purchase_is_always_refundable_even_if_work_started_is_set(self):
        req = _make_request(_make_locker('spec10-refundable-purchase@example.com'), status='searching')
        quotation = _make_quotation(req, quotation_type='purchase')
        self.assertTrue(quotation.is_refundable)
        req.work_started_at = timezone.now()
        req.save()
        self.assertTrue(quotation.is_refundable)

    def test_research_fee_refundable_while_pending_and_unstarted(self):
        req = _make_request(_make_locker('spec10-refundable-research-pending@example.com'), status='searching')
        quotation = _make_quotation(req, quotation_type='research_fee', status='pending')
        # Pre-payment: still pending, work never started — must read True.
        self.assertIsNone(req.work_started_at)
        self.assertTrue(quotation.is_refundable)

    def test_research_fee_not_refundable_once_work_started(self):
        req = _make_request(_make_locker('spec10-refundable-research-started@example.com'), status='paid')
        quotation = _make_quotation(req, quotation_type='research_fee', status='approved')
        req.work_started_at = timezone.now()
        req.save()
        self.assertFalse(quotation.is_refundable)

    def test_expense_advance_refundable_before_work_started(self):
        req = PersonalShopRequest.objects.create(
            locker=_make_locker('spec10-refundable-advance-pending@example.com'),
            request_type='boutique_purchase', status='searching', destination_country='USA',
        )
        quotation = _make_quotation(req, quotation_type='expense_advance', status='pending')
        self.assertTrue(quotation.is_refundable)

    def test_expense_advance_not_refundable_once_work_started(self):
        req = PersonalShopRequest.objects.create(
            locker=_make_locker('spec10-refundable-advance-started@example.com'),
            request_type='local_shop_purchase', status='paid', destination_country='USA',
        )
        quotation = _make_quotation(req, quotation_type='expense_advance', status='approved')
        req.work_started_at = timezone.now()
        req.save()
        self.assertFalse(quotation.is_refundable)


class Spec10VerifyWorkStartedStampingTests(TestCase):
    """Rules: 'mark_work_started must not be callable before the gating
    quotation's payment is captured (active_quotation.status == "approved")'.
    Files to change: the action is enabled only where
    active_quotation.quotation_type is research_fee/expense_advance and its
    status is approved; sets work_started_at (and, per the model's
    work_started_by field, the acting staff user).

    NOTE: the spec (Files to change) describes this as its own
    `mark_work_started` admin action. The current implementation folds this
    behavior into the existing `mark_searching` action instead (no standalone
    `mark_work_started` callable exists) — these tests exercise the real
    `mark_searching` entry point since that's what actually performs the
    stamping described above; see the report accompanying this test file for
    the naming discrepancy.
    """

    def setUp(self):
        self.locker = _make_locker('spec10-work-started@example.com')
        self.staff_no_perm = User.objects.create(email='spec10-staff-no-perm@example.com', is_staff=True)
        self.staff_with_perm = User.objects.create(email='spec10-staff-with-perm@example.com', is_staff=True)
        perm = Permission.objects.get(
            codename='mark_work_started',
            content_type=ContentType.objects.get_for_model(PersonalShopRequest),
        )
        self.staff_with_perm.user_permissions.add(perm)
        self.admin_instance = PersonalShopRequestAdmin(PersonalShopRequest, django_admin.site)

    def _run(self, req, user):
        self.admin_instance.mark_searching(
            _admin_request(user), PersonalShopRequest.objects.filter(pk=req.pk)
        )

    def test_stamps_work_started_when_gating_quotation_approved_and_permitted(self):
        req = _make_request(self.locker, status='paid')
        quotation = _make_quotation(req, quotation_type='research_fee', status='approved')
        req.active_quotation = quotation
        req.save()

        self._run(req, self.staff_with_perm)
        req.refresh_from_db()
        self.assertIsNotNone(req.work_started_at)
        self.assertEqual(req.work_started_by, self.staff_with_perm)

    def test_not_stamped_without_permission(self):
        req = _make_request(self.locker, status='paid')
        quotation = _make_quotation(req, quotation_type='research_fee', status='approved')
        req.active_quotation = quotation
        req.save()

        self._run(req, self.staff_no_perm)
        req.refresh_from_db()
        self.assertIsNone(req.work_started_at)

    def test_not_stamped_while_gating_quotation_is_still_pending(self):
        # Payment not yet captured — must not be callable per the Rules section.
        req = _make_request(self.locker, status='quotation_ready')
        quotation = _make_quotation(req, quotation_type='research_fee', status='pending')
        req.active_quotation = quotation
        req.save()

        self._run(req, self.staff_with_perm)
        req.refresh_from_db()
        self.assertIsNone(req.work_started_at)

    def test_not_stamped_for_purchase_type_quotation(self):
        req = _make_request(self.locker, status='paid')
        quotation = _make_quotation(req, quotation_type='purchase', status='approved')
        req.active_quotation = quotation
        req.save()

        self._run(req, self.staff_with_perm)
        req.refresh_from_db()
        self.assertIsNone(req.work_started_at)

    def test_no_active_quotation_does_not_error_and_leaves_work_started_at_unset(self):
        req = _make_request(self.locker, status='submitted')
        self._run(req, self.staff_with_perm)  # must not raise
        req.refresh_from_db()
        self.assertIsNone(req.work_started_at)

    def test_stamping_works_for_expense_advance_type_too(self):
        req = PersonalShopRequest.objects.create(
            locker=self.locker, request_type='local_shop_purchase',
            status='paid', destination_country='USA',
        )
        quotation = _make_quotation(req, quotation_type='expense_advance', status='approved')
        req.active_quotation = quotation
        req.save()

        self._run(req, self.staff_with_perm)
        req.refresh_from_db()
        self.assertIsNotNone(req.work_started_at)
        self.assertEqual(req.work_started_by, self.staff_with_perm)


class Spec10VerifyTravelExpenseValidationTests(TestCase):
    """Model changes: travel_expense_amount is a DecimalField for physical-
    visit costs; Rules/DoD imply it's a normal validated money field like the
    existing amount fields — non-negative."""

    def test_default_is_zero(self):
        req = _make_request(_make_locker('spec10-travel-default@example.com'), status='searching')
        quotation = _make_quotation(req)
        self.assertEqual(quotation.travel_expense_amount, Decimal('0'))

    def test_negative_value_rejected_on_full_clean(self):
        req = _make_request(_make_locker('spec10-travel-negative@example.com'), status='searching')
        quotation = _make_quotation(req, travel_expense_amount=Decimal('-1'))
        with self.assertRaises(ValidationError):
            quotation.full_clean()

    def test_zero_is_valid(self):
        req = _make_request(_make_locker('spec10-travel-zero@example.com'), status='searching')
        quotation = _make_quotation(req, travel_expense_amount=Decimal('0'))
        quotation.full_clean()  # must not raise

    def test_positive_value_is_valid(self):
        req = PersonalShopRequest.objects.create(
            locker=_make_locker('spec10-travel-positive@example.com'),
            request_type='local_shop_purchase', status='searching', destination_country='USA',
        )
        quotation = _make_quotation(
            req, quotation_type='expense_advance', travel_expense_amount=Decimal('850.50'),
        )
        quotation.full_clean()  # must not raise
        self.assertEqual(quotation.travel_expense_amount, Decimal('850.50'))


class Spec10VerifyQuotationTypeLockAfterApprovalTests(TestCase):
    """Rules: 'quotation_type lock after approval (both model-save guarantee
    and admin-form-level check)'."""

    def setUp(self):
        self.locker = _make_locker('spec10-lock-test@example.com')
        self.req = _make_request(self.locker, status='paid')

    def test_model_save_blocks_type_change_once_approved(self):
        quotation = _make_quotation(self.req, quotation_type='purchase', status='approved')
        quotation.quotation_type = 'research_fee'
        with self.assertRaises(ValidationError):
            quotation.save()

    def test_model_save_permits_type_change_while_still_pending(self):
        # self.req is a custom_request (see _make_request's default), so
        # research_fee is a valid type for it — this isolates the
        # approval-lock rule specifically, not the type/request-type rule.
        quotation = _make_quotation(self.req, quotation_type='purchase', status='pending')
        quotation.quotation_type = 'research_fee'
        quotation.save()  # must not raise
        quotation.refresh_from_db()
        self.assertEqual(quotation.quotation_type, 'research_fee')

    def test_admin_form_rejects_type_change_once_approved(self):
        quotation = _make_quotation(self.req, quotation_type='purchase', status='approved')
        form = PersonalShopQuotationAdminForm(
            data={'quotation_type': 'research_fee'}, instance=quotation,
        )
        form.is_valid()
        self.assertIn('quotation_type', form.errors)

    def test_admin_form_does_not_flag_quotation_type_when_change_is_same_value(self):
        quotation = _make_quotation(self.req, quotation_type='purchase', status='approved')
        form = PersonalShopQuotationAdminForm(
            data={'quotation_type': 'purchase'}, instance=quotation,
        )
        form.is_valid()
        self.assertNotIn('quotation_type', form.errors)


class Spec10VerifyPricingSuggestionTests(TestCase):
    """New apps/personal_shop/pricing.py: a pure suggestion table (no DB
    access), never used to enforce/reject a staff-entered fee. 'behavior when
    no ServiceCharge is configured' — there is no ServiceCharge model in this
    spec at all; the table simply has no entry for some request types, and
    the function returns None for those so staff must set the fee manually."""

    def test_percentage_types_use_minimum_when_no_product_value_given(self):
        self.assertEqual(pricing.suggested_service_fee('product_link'), Decimal('199'))
        self.assertEqual(pricing.suggested_service_fee('image_search'), Decimal('299'))
        self.assertEqual(pricing.suggested_service_fee('cart_screenshot'), Decimal('299'))
        self.assertEqual(pricing.suggested_service_fee('boutique_purchase'), Decimal('399'))

    def test_percentage_wins_when_it_exceeds_minimum(self):
        # product_link: 5% of 10000 = 500, above the 199 minimum.
        self.assertEqual(pricing.suggested_service_fee('product_link', Decimal('10000')), Decimal('500.00'))

    def test_minimum_wins_when_percentage_is_below_it(self):
        # product_link: 5% of 1000 = 50, below the 199 minimum.
        self.assertEqual(pricing.suggested_service_fee('product_link', Decimal('1000')), Decimal('199'))

    def test_flat_fee_type_ignores_product_value(self):
        self.assertEqual(pricing.suggested_service_fee('local_shop_purchase'), Decimal('499'))
        self.assertEqual(pricing.suggested_service_fee('local_shop_purchase', Decimal('999999')), Decimal('499'))

    def test_custom_request_has_no_formula_always_returns_starting_fee(self):
        # Spec: 'custom_request has no formula — final fee based on complexity' —
        # this is a starting-point suggestion, never derived from product_value.
        self.assertEqual(pricing.suggested_service_fee('custom_request'), Decimal('499'))
        self.assertEqual(pricing.suggested_service_fee('custom_request', Decimal('50000')), Decimal('499'))

    def test_unconfigured_request_type_returns_none_for_manual_entry(self):
        self.assertIsNone(pricing.suggested_service_fee('not_a_real_request_type'))
        self.assertIsNone(pricing.suggested_service_fee(''))

    def test_reads_from_service_charge_not_a_hardcoded_dict(self):
        # Superseded: pricing.py originally hardcoded these numbers in a pure
        # dict with zero DB access (spec's literal text). That was a
        # deliberate later product decision reversed in favor of the
        # admin-configurable ServiceCharge model (apps/content/models.py) —
        # editing a fee on the Service Charges admin page now takes effect
        # here without a deploy. This asserts the DB is actually consulted,
        # rather than a stale "no queries" purity guarantee.
        with self.assertNumQueries(1):
            pricing.suggested_service_fee('product_link', Decimal('5000'))
        with self.assertNumQueries(0):
            pricing.suggested_service_fee('unknown_type')


class Spec10VerifyTotalAmountIncludesTravelExpenseTests(TestCase):
    """Supersedes the spec's literal flat-formula text (spec Rules describes
    a single "subtotal + shipping + service_fee + gateway + travel" sum
    applied uniformly). That formula was superseded by deliberate, later
    product decisions made directly by the user in review:
    - Expense Advance totals to travel_expense_amount ALONE, not summed with
      service_fee_amount — confirmed explicitly, twice.
    - Purchase totals to subtotal + shipping + service_fee + gateway, with
      travel_expense_amount deliberately EXCLUDED — the field isn't even
      shown on a Purchase-type admin form (see the field-visibility JS in
      static/admin/js/personal_shop/admin_suggested_fee.js), since travel
      expense only applies to physical-visit Expense Advance quotations.
    The spec document itself was never rewritten after these decisions;
    these tests assert the actual, confirmed-correct behavior instead.
    """

    def _run_save_related(self, quotation, user):
        admin_instance = PersonalShopQuotationAdmin(PersonalShopQuotation, django_admin.site)
        admin_instance.save_related(
            _admin_request(user), _FakeQuotationForm(quotation), [], change=True,
        )
        quotation.refresh_from_db()
        return quotation

    def test_expense_advance_total_is_travel_expense_alone(self):
        locker = _make_locker('spec10-total-travel-advance@example.com')
        req = PersonalShopRequest.objects.create(
            locker=locker, request_type='local_shop_purchase',
            status='searching', destination_country='USA',
        )
        quotation = _make_quotation(
            req, quotation_type='expense_advance',
            domestic_shipping_amount=Decimal('0'), service_fee_amount=Decimal('499'),
            payment_gateway_charge=Decimal('0'), travel_expense_amount=Decimal('800'),
        )
        quotation = self._run_save_related(quotation, locker.user)
        # Confirmed: expense_advance totals to travel_expense_amount ALONE —
        # service_fee_amount (499) is NOT added, even though it's set here.
        self.assertEqual(quotation.total_amount, Decimal('800'))

    def test_purchase_total_excludes_travel_expense(self):
        locker = _make_locker('spec10-total-travel-purchase@example.com')
        req = _make_request(locker, status='searching')  # custom_request
        quotation = _make_quotation(
            req, quotation_type='purchase',
            domestic_shipping_amount=Decimal('50'), service_fee_amount=Decimal('100'),
            payment_gateway_charge=Decimal('10'), travel_expense_amount=Decimal('75'),
        )
        PersonalShopQuotationLineItem.objects.create(
            quotation=quotation, name='Item', qty=1, unit_amount=Decimal('200'),
        )
        quotation = self._run_save_related(quotation, locker.user)
        # Confirmed: Purchase excludes travel_expense_amount entirely —
        # subtotal(200) + domestic_shipping(50) + service_fee(100) + gateway(10) = 360, NOT 435.
        self.assertEqual(quotation.total_amount, Decimal('360'))

    def test_zero_travel_expense_leaves_existing_total_formula_unchanged(self):
        locker = _make_locker('spec10-total-travel-zero@example.com')
        req = _make_request(locker, status='searching')
        quotation = _make_quotation(
            req, quotation_type='purchase',
            domestic_shipping_amount=Decimal('50'), service_fee_amount=Decimal('100'),
            payment_gateway_charge=Decimal('10'), travel_expense_amount=Decimal('0'),
        )
        PersonalShopQuotationLineItem.objects.create(
            quotation=quotation, name='Item', qty=1, unit_amount=Decimal('200'),
        )
        quotation = self._run_save_related(quotation, locker.user)
        self.assertEqual(quotation.total_amount, Decimal('360'))


class Spec10VerifyQuotationTemplateRefundabilityTests(TestCase):
    """Templates: the pre-payment refundability sentence on quotation.html is
    driven by quotation_type alone, never by is_refundable (which the spec
    states is always True at this point, since the quotation is unpaid and
    work_started_at cannot yet be set)."""

    def setUp(self):
        self.locker = _make_locker('spec10-template-refund@example.com')
        self.client.force_login(self.locker.user)

    def _quotation_page(self, quotation_type):
        # research_fee only validates on custom_request; expense_advance only
        # on boutique_purchase/local_shop_purchase — pick a parent request_type
        # that's actually allowed for the quotation_type under test, same
        # constraint Spec10VerifyQuotationTypeConstraintTests establishes.
        if quotation_type == 'expense_advance':
            req = PersonalShopRequest.objects.create(
                locker=self.locker, request_type='local_shop_purchase',
                status='quotation_ready', destination_country='USA',
            )
        else:
            req = _make_request(self.locker, status='quotation_ready')
        quotation = _make_quotation(req, quotation_type=quotation_type, status='pending')
        req.active_quotation = quotation
        req.save()
        # Sanity check on the spec's own justification: is_refundable must
        # still read True here even for research_fee/expense_advance, since
        # nothing has been paid and work hasn't started.
        self.assertTrue(quotation.is_refundable)
        url = reverse('personal_shop:quotation_detail', args=[req.pk])
        return self.client.get(url)

    def test_purchase_shows_fully_refundable_sentence(self):
        response = self._quotation_page('purchase')
        self.assertContains(response, 'Fully refundable if you decline before purchase.')
        self.assertNotContains(response, 'This fee becomes non-refundable once we begin work.')

    def test_research_fee_shows_non_refundable_once_work_begins_sentence(self):
        # Despite quotation.is_refundable being True at this point (asserted
        # in _quotation_page), the page must show the non-refundable warning
        # — proof the template reads quotation_type, not is_refundable.
        response = self._quotation_page('research_fee')
        self.assertContains(response, 'This fee becomes non-refundable once we begin work.')
        self.assertNotContains(response, 'Fully refundable if you decline before purchase.')

    def test_expense_advance_shows_non_refundable_once_work_begins_sentence(self):
        response = self._quotation_page('expense_advance')
        self.assertContains(response, 'This fee becomes non-refundable once we begin work.')
        self.assertNotContains(response, 'Fully refundable if you decline before purchase.')

    def test_quotation_type_badge_label_matches_type(self):
        purchase_resp = self._quotation_page('purchase')
        self.assertContains(purchase_resp, 'Purchase Quotation')

        research_resp = self._quotation_page('research_fee')
        self.assertContains(research_resp, 'Research Fee')

        advance_resp = self._quotation_page('expense_advance')
        self.assertContains(advance_resp, 'Expense Advance')

    def test_travel_expense_line_shown_only_when_non_zero(self):
        req = PersonalShopRequest.objects.create(
            locker=self.locker, request_type='local_shop_purchase',
            status='quotation_ready', destination_country='USA',
        )
        quotation = _make_quotation(
            req, quotation_type='expense_advance', status='pending',
            travel_expense_amount=Decimal('850'),
        )
        req.active_quotation = quotation
        req.save()
        url = reverse('personal_shop:quotation_detail', args=[req.pk])
        response = self.client.get(url)
        self.assertContains(response, 'Travel / Local Expense')
        self.assertContains(response, '850')

    def test_travel_expense_line_hidden_when_zero(self):
        req = PersonalShopRequest.objects.create(
            locker=self.locker, request_type='local_shop_purchase',
            status='quotation_ready', destination_country='USA',
        )
        quotation = _make_quotation(
            req, quotation_type='expense_advance', status='pending',
            travel_expense_amount=Decimal('0'),
        )
        req.active_quotation = quotation
        req.save()
        url = reverse('personal_shop:quotation_detail', args=[req.pk])
        response = self.client.get(url)
        self.assertNotContains(response, 'Travel / Local Expense')


class Spec10VerifyRequestTypeEscalationHelpTextTests(TestCase):
    """Rules: staff escalate image_search/cart_screenshot to custom_request,
    or boutique_purchase to custom_request (cross-boutique sourcing), by
    editing request_type directly — 'document both cases as a staff runbook
    note in the admin's request_type help_text, not new code'."""

    def test_request_type_help_text_documents_both_escalation_paths(self):
        staff = User.objects.create(
            email='spec10-escalation-help-text@example.com', is_staff=True, is_superuser=True,
        )
        admin_instance = PersonalShopRequestAdmin(PersonalShopRequest, django_admin.site)
        request = _admin_request(staff)
        form_class = admin_instance.get_form(request, obj=None)
        help_text = form_class.base_fields['request_type'].help_text
        self.assertIn('custom_request', help_text)
        self.assertTrue('image_search' in help_text or 'cart_screenshot' in help_text)
        self.assertIn('boutique_purchase', help_text)


class Spec10VerifyResearchFeeThenPurchaseCycleTests(TestCase):
    """DoD (verbatim): 'A second quotation created afterward on the same
    request (quotation_type=purchase, real line items) becomes the new
    active_quotation and is payable through the existing pay flow,
    unaffected by the earlier research-fee quotation's history.' Exercises
    the spec's central narrative end to end: research fee quote -> pay ->
    mark work started -> follow-up purchase quote replaces active_quotation
    without disturbing the research-fee quotation's own approved history or
    the work_started_at stamp."""

    def setUp(self):
        self.locker = _make_locker('spec10-cycle@example.com')
        self.req = _make_request(self.locker, status='reviewing')  # custom_request
        self.staff = User.objects.create(email='spec10-cycle-staff@example.com', is_staff=True)
        perm = Permission.objects.get(
            codename='mark_work_started',
            content_type=ContentType.objects.get_for_model(PersonalShopRequest),
        )
        self.staff.user_permissions.add(perm)
        self.quotation_admin = PersonalShopQuotationAdmin(PersonalShopQuotation, django_admin.site)
        self.request_admin = PersonalShopRequestAdmin(PersonalShopRequest, django_admin.site)

    def _approve_quotation(self, quotation):
        self.quotation_admin.save_related(
            _admin_request(self.locker.user), _FakeQuotationForm(quotation), [], change=True,
        )

    def test_second_purchase_quotation_replaces_active_quotation_after_research_fee_cycle(self):
        # 1. Research fee quotation issued and approved -> becomes active_quotation.
        research_fee = _make_quotation(self.req, quotation_type='research_fee', status='pending')
        self._approve_quotation(research_fee)
        self.req.refresh_from_db()
        self.assertEqual(self.req.active_quotation_id, research_fee.pk)
        self.assertEqual(self.req.status, 'quotation_ready')

        # 2. Payment captured -> request paid, research fee quotation approved.
        self.req.mark_paid()
        self.req.refresh_from_db()
        research_fee.refresh_from_db()
        self.assertEqual(self.req.status, 'paid')
        self.assertEqual(research_fee.status, 'approved')

        # 3. Staff marks work started -> fee locked non-refundable, status untouched.
        self.request_admin.mark_searching(
            _admin_request(self.staff), PersonalShopRequest.objects.filter(pk=self.req.pk),
        )
        self.req.refresh_from_db()
        self.assertIsNotNone(self.req.work_started_at)
        work_started_at = self.req.work_started_at
        self.assertEqual(self.req.status, 'paid')
        self.assertFalse(research_fee.is_refundable)

        # 4. A follow-up purchase quotation with real line items is issued.
        purchase = _make_quotation(self.req, quotation_type='purchase', status='pending')
        PersonalShopQuotationLineItem.objects.create(
            quotation=purchase, name='Sourced item', qty=1, unit_amount=Decimal('1500'),
        )
        self._approve_quotation(purchase)
        self.req.refresh_from_db()
        research_fee.refresh_from_db()

        # New quotation takes over as active_quotation and is payable.
        self.assertEqual(self.req.active_quotation_id, purchase.pk)
        self.assertEqual(self.req.status, 'quotation_ready')
        self.assertEqual(purchase.status, 'pending')
        # The research-fee quotation's own history is unaffected.
        self.assertEqual(research_fee.status, 'approved')
        self.assertEqual(research_fee.quotation_type, 'research_fee')
        # work_started_at is not disturbed by the new quotation cycle.
        self.assertEqual(self.req.work_started_at, work_started_at)


class ServiceFeeHelpTextReflectsLiveServiceChargeTests(TestCase):
    """Code-review fix: the service_fee_amount admin help text used to be a
    hardcoded rate-table string that would silently go stale the moment an
    admin edited a rate on the Service Charges page. It's now built live
    from ServiceCharge — this locks in that an edit actually shows up."""

    def setUp(self):
        self.locker = _make_locker('help-text-live-test@example.com')
        self.admin_instance = PersonalShopQuotationAdmin(PersonalShopQuotation, django_admin.site)
        self.factory_request = RequestFactory().get('/')
        self.factory_request.user = User.objects.create(
            email='help-text-staff@example.com', is_staff=True, is_superuser=True,
        )

    def _help_text(self):
        form_class = self.admin_instance.get_form(self.factory_request, obj=None)
        return form_class.base_fields['service_fee_amount'].help_text

    def test_help_text_reflects_current_product_link_rate(self):
        self.assertIn('product_link 5.00% (min ₹199.00)', self._help_text())

    def test_help_text_changes_after_admin_edits_the_rate(self):
        from apps.content.models import ServiceCharge

        ServiceCharge.objects.filter(code='trunkassist_product_link').update(percentage_rate='8.00')
        self.assertIn('product_link 8.00% (min ₹199.00)', self._help_text())

    def test_help_text_omits_inactive_charges(self):
        from apps.content.models import ServiceCharge

        ServiceCharge.objects.filter(code='trunkassist_product_link').update(is_active=False)
        self.assertNotIn('product_link', self._help_text())


class PersonalShopImageStorageCleanupTests(TestCase):
    """PersonalShopImage rows must not orphan their Supabase Storage file on delete."""

    def setUp(self):
        self.locker = _make_locker('pshopimg@example.com')
        self.request = _make_request(self.locker)

    @patch('apps.accounts.services.SupabaseStorage.delete_file')
    def test_deleting_image_deletes_its_storage_file(self, mock_delete):
        image = PersonalShopImage.objects.create(
            request=self.request, image_path='personal-shop/RB-1/RB-1-TA001/photo_abc123.jpg',
        )

        image.delete()

        mock_delete.assert_called_once_with('parcel-images', 'personal-shop/RB-1/RB-1-TA001/photo_abc123.jpg')

    @patch('apps.accounts.services.SupabaseStorage.delete_file')
    def test_deleting_request_cascades_to_its_images_storage_files(self, mock_delete):
        PersonalShopImage.objects.create(request=self.request, image_path='personal-shop/RB-1/RB-1-TA001/photo_x.jpg')
        PersonalShopImage.objects.create(request=self.request, image_path='personal-shop/RB-1/RB-1-TA001/photo_y.jpg')

        self.request.delete()

        self.assertEqual(mock_delete.call_count, 2)

    @patch('apps.accounts.services.SupabaseStorage.delete_file', side_effect=Exception('storage down'))
    def test_storage_failure_does_not_block_the_db_delete(self, mock_delete):
        image = PersonalShopImage.objects.create(
            request=self.request, image_path='personal-shop/RB-1/RB-1-TA001/photo_z.jpg',
        )
        image_pk = image.pk

        image.delete()  # must not raise

        self.assertFalse(PersonalShopImage.objects.filter(pk=image_pk).exists())
