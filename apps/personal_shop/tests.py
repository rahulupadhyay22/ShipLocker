import re
from datetime import timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User, Locker
from apps.payments.models import Payment
from .models import PersonalShopRequest, PersonalShopQuotation, generate_personal_shop_request_id


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
