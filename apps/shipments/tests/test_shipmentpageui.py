"""
Tests for the Shipments list page redesign (spec: .claude/specs/03-shipmentpageui.md).

Covers, per spec:
- login required on /shipments/
- per-user ownership isolation (never see another user's shipments)
- ShipmentsListView returns ALL statuses (not just active)
- ShipmentStatsMixin aggregate counts (total/in_transit/customs/delivered)
- Shipment.stage property (4-step list, or None for returned/cancelled)
- pagination with >20 shipments
- empty state (200, not error)
- legacy active/delivered/closed routes still resolve/respond
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User, Locker
from apps.locker.models import Parcel
from apps.shipments.models import Shipment


def make_shipment(user, status='in_transit', **extra):
    defaults = dict(
        user=user,
        shipment_type='international',
        status=status,
        recipient_name='Jane Doe',
        recipient_phone='9999999999',
        address_line1='1 Test St',
        city='Testville',
        state='TS',
        postal_code='000000',
        country='USA',
    )
    defaults.update(extra)
    return Shipment.objects.create(**defaults)


class ShipmentsListViewAuthAndOwnershipTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email='owner@example.com')
        self.other_user = User.objects.create(email='other@example.com')

    def test_anonymous_user_redirected_to_login(self):
        response = self.client.get(reverse('shipments:list'))
        self.assertRedirects(
            response,
            f"/accounts/login/?next={reverse('shipments:list')}",
            fetch_redirect_response=False,
        )

    def test_user_never_sees_another_users_shipments(self):
        mine = make_shipment(self.user, status='delivered')
        theirs = make_shipment(self.other_user, status='delivered')

        self.client.force_login(self.user)
        response = self.client.get(reverse('shipments:list'))

        shipment_ids = {s.id for s in response.context['shipments']}
        self.assertIn(mine.id, shipment_ids)
        self.assertNotIn(theirs.id, shipment_ids)


class ShipmentsListViewQuerysetTests(TestCase):
    """Confirms the list is unified across all statuses, not active-only."""

    def setUp(self):
        self.user = User.objects.create(email='user@example.com')
        self.client.force_login(self.user)

    def test_list_view_returns_all_statuses_not_just_active(self):
        statuses = [
            'draft', 'declaration_pending', 'pending_payment', 'packing',
            'dispatched', 'in_transit', 'customs', 'out_for_delivery',
            'delivered', 'returned', 'cancelled',
        ]
        for status in statuses:
            make_shipment(self.user, status=status)

        response = self.client.get(reverse('shipments:list'))
        returned_statuses = {s.status for s in response.context['shipments']}
        self.assertEqual(returned_statuses, set(statuses))

    def test_empty_state_returns_200(self):
        response = self.client.get(reverse('shipments:list'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['shipments']), 0)


class ShipmentStatsMixinTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email='stats@example.com')
        self.client.force_login(self.user)

    def test_stat_tile_counts_match_known_status_mix(self):
        make_shipment(self.user, status='draft')
        make_shipment(self.user, status='in_transit')
        make_shipment(self.user, status='in_transit')
        make_shipment(self.user, status='out_for_delivery')
        make_shipment(self.user, status='customs')
        make_shipment(self.user, status='delivered')
        make_shipment(self.user, status='delivered')
        make_shipment(self.user, status='delivered')
        make_shipment(self.user, status='cancelled')

        response = self.client.get(reverse('shipments:list'))

        self.assertEqual(response.context['total_count'], 9)
        self.assertEqual(response.context['in_transit_count'], 3)
        self.assertEqual(response.context['customs_count'], 1)
        self.assertEqual(response.context['delivered_count'], 3)

    def test_stat_counts_scoped_to_requesting_user_only(self):
        other_user = User.objects.create(email='other-stats@example.com')
        make_shipment(other_user, status='delivered')
        make_shipment(other_user, status='delivered')

        make_shipment(self.user, status='delivered')

        response = self.client.get(reverse('shipments:list'))
        self.assertEqual(response.context['total_count'], 1)
        self.assertEqual(response.context['delivered_count'], 1)


class ShipmentStagePropertyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email='stage@example.com')

    def _stage_by_key(self, shipment):
        return {step['key']: step['complete'] for step in shipment.stage}

    def test_stage_none_for_returned(self):
        shipment = make_shipment(self.user, status='returned')
        self.assertIsNone(shipment.stage)

    def test_stage_none_for_cancelled(self):
        shipment = make_shipment(self.user, status='cancelled')
        self.assertIsNone(shipment.stage)

    def test_stage_for_customs_status_completes_first_three_only(self):
        shipment = make_shipment(self.user, status='customs')
        flags = self._stage_by_key(shipment)
        self.assertTrue(flags['picked_up'])
        self.assertTrue(flags['dispatched'])
        self.assertTrue(flags['customs'])
        self.assertFalse(flags['delivered'])

    def test_stage_for_delivered_status_completes_all_four(self):
        shipment = make_shipment(self.user, status='delivered')
        flags = self._stage_by_key(shipment)
        self.assertTrue(flags['picked_up'])
        self.assertTrue(flags['dispatched'])
        self.assertTrue(flags['customs'])
        self.assertTrue(flags['delivered'])

    def test_stage_for_packing_status_only_picked_up_complete(self):
        shipment = make_shipment(self.user, status='packing')
        flags = self._stage_by_key(shipment)
        self.assertTrue(flags['picked_up'])
        self.assertFalse(flags['dispatched'])
        self.assertFalse(flags['customs'])
        self.assertFalse(flags['delivered'])

    def test_stage_returns_four_steps_for_normal_status(self):
        shipment = make_shipment(self.user, status='in_transit')
        self.assertEqual(len(shipment.stage), 4)

    def test_stage_for_dispatched_status_second_step_complete(self):
        """NOTE: discriminator between spec text and models.py's actual rule.

        The spec file (03-shipmentpageui.md, line 21) defines the "In Transit"
        stage as complete for status in (in_transit, customs, out_for_delivery,
        delivered) -- NOT including 'dispatched'. But models.py's implemented
        `stage` property (and the task brief's own restated 4-step list of
        Picked Up/Dispatched/Customs/Delivered) treats a 'dispatched' shipment
        as having its second step already complete. This test asserts the
        *implemented* behavior (second step complete for status='dispatched')
        since that's what apps/shipments/models.py actually does today. If the
        spec's stricter "In Transit" definition is the intended contract, this
        assertion should flip to assertFalse -- flagging for reconciliation.
        """
        shipment = make_shipment(self.user, status='dispatched')
        flags = self._stage_by_key(shipment)
        self.assertTrue(flags['picked_up'])
        self.assertTrue(flags['dispatched'])
        self.assertFalse(flags['customs'])
        self.assertFalse(flags['delivered'])


class ShipmentsListPaginationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email='pagination@example.com')
        self.client.force_login(self.user)

    def test_pagination_with_more_than_20_shipments_across_statuses(self):
        statuses = ['in_transit', 'delivered', 'customs', 'packing']
        for i in range(25):
            make_shipment(self.user, status=statuses[i % len(statuses)])

        response = self.client.get(reverse('shipments:list'))
        self.assertTrue(response.context['is_paginated'])
        self.assertEqual(len(response.context['shipments']), 20)

        response_page_2 = self.client.get(reverse('shipments:list'), {'page': 2})
        self.assertEqual(len(response_page_2.context['shipments']), 5)


class LegacyShipmentRoutesTests(TestCase):
    """Spec: active/delivered/closed routes are kept working (harmless legacy)."""

    def setUp(self):
        self.user = User.objects.create(email='legacy@example.com')
        self.client.force_login(self.user)

    def test_active_route_still_responds(self):
        response = self.client.get(reverse('shipments:active'))
        self.assertEqual(response.status_code, 200)

    def test_delivered_route_still_responds(self):
        response = self.client.get(reverse('shipments:delivered'))
        self.assertEqual(response.status_code, 200)

    def test_closed_route_still_responds(self):
        response = self.client.get(reverse('shipments:closed'))
        self.assertEqual(response.status_code, 200)


class ApproveDeclarationMethodTests(TestCase):
    """Shipment.approve_declaration(): declaration_pending -> pending_payment
    (or straight to packing if already paid). Not gated on shipping_cost --
    the customer sets that later by choosing a shipping speed."""

    def setUp(self):
        self.user = User.objects.create(email='approve@example.com')

    def test_noop_when_status_is_not_declaration_pending(self):
        shipment = make_shipment(self.user, status='packing', shipping_cost=Decimal('100.00'))
        result = shipment.approve_declaration()
        shipment.refresh_from_db()
        self.assertFalse(result)
        self.assertEqual(shipment.status, 'packing')

    def test_unset_shipping_cost_does_not_block_approval(self):
        """shipping_cost is set later by the customer, not at approval time,
        so approve_declaration() must not gate on it being present."""
        shipment = make_shipment(
            self.user, status='declaration_pending',
            shipping_cost=None, payment_status='unpaid',
        )
        result = shipment.approve_declaration()
        shipment.refresh_from_db()
        self.assertTrue(result)
        self.assertEqual(shipment.status, 'pending_payment')

    def test_zero_shipping_cost_is_a_valid_price(self):
        """A legitimately free/promo shipment (cost=0.00) must still be approvable."""
        shipment = make_shipment(
            self.user, status='declaration_pending',
            shipping_cost=Decimal('0.00'), payment_status='unpaid',
        )
        result = shipment.approve_declaration()
        shipment.refresh_from_db()
        self.assertTrue(result)
        self.assertEqual(shipment.status, 'pending_payment')

    def test_moves_to_pending_payment_when_unpaid(self):
        shipment = make_shipment(
            self.user, status='declaration_pending',
            shipping_cost=Decimal('2000.00'), payment_status='unpaid',
        )
        result = shipment.approve_declaration()
        shipment.refresh_from_db()
        self.assertTrue(result)
        self.assertEqual(shipment.status, 'pending_payment')

    def test_moves_straight_to_packing_when_already_paid(self):
        shipment = make_shipment(
            self.user, status='declaration_pending',
            shipping_cost=Decimal('2000.00'), payment_status='paid',
        )
        result = shipment.approve_declaration()
        shipment.refresh_from_db()
        self.assertTrue(result)
        self.assertEqual(shipment.status, 'packing')


class AdvanceAfterPaymentMethodTests(TestCase):
    """Shipment.advance_after_payment(): pending_payment -> packing once paid."""

    def setUp(self):
        self.user = User.objects.create(email='advance@example.com')

    def test_advances_pending_payment_to_packing(self):
        shipment = make_shipment(self.user, status='pending_payment')
        result = shipment.advance_after_payment()
        self.assertTrue(result)
        self.assertEqual(shipment.status, 'packing')

    def test_noop_for_other_statuses(self):
        for status in ('declaration_pending', 'packing', 'in_transit', 'delivered'):
            shipment = make_shipment(self.user, status=status)
            result = shipment.advance_after_payment()
            self.assertFalse(result, f"expected no-op for status={status}")
            self.assertEqual(shipment.status, status)

    def test_does_not_persist_by_itself(self):
        """Per its docstring, advance_after_payment only mutates in memory --
        callers must save. Confirms the DB row is untouched until saved."""
        shipment = make_shipment(self.user, status='pending_payment')
        shipment.advance_after_payment()
        from_db = Shipment.objects.get(pk=shipment.pk)
        self.assertEqual(from_db.status, 'pending_payment')


class ShipmentBadgeClassTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email='badge@example.com')

    def test_delivered_maps_to_status_approved(self):
        shipment = make_shipment(self.user, status='delivered')
        self.assertEqual(shipment.badge_class, 'status-approved')

    def test_returned_maps_to_status_returned(self):
        shipment = make_shipment(self.user, status='returned')
        self.assertEqual(shipment.badge_class, 'status-returned')

    def test_cancelled_maps_to_status_action(self):
        shipment = make_shipment(self.user, status='cancelled')
        self.assertEqual(shipment.badge_class, 'status-action')

    def test_other_statuses_map_to_status_pending(self):
        for status in ('draft', 'declaration_pending', 'pending_payment', 'packing', 'in_transit'):
            shipment = make_shipment(self.user, status=status)
            self.assertEqual(shipment.badge_class, 'status-pending')


class CreateShipmentPreselectedParcelsTests(TestCase):
    """CreateShipmentView.get: 'Ship Selected' passes ?parcels=<id> to narrow
    the list to just the chosen parcels (not all ready-to-ship items)."""

    def setUp(self):
        self.user = User.objects.create(email='preselect@example.com')
        self.locker = Locker.objects.create(user=self.user)
        self.parcel_a = Parcel.objects.create(
            locker=self.locker, status='approved', item_name='Outfit A',
            weight_kg=Decimal('1.0'), item_price=Decimal('500'), item_currency='INR',
            category='clothing',
        )
        self.parcel_b = Parcel.objects.create(
            locker=self.locker, status='approved', item_name='Outfit B',
            weight_kg=Decimal('1.0'), item_price=Decimal('500'), item_currency='INR',
            category='clothing',
        )
        self.client.force_login(self.user)

    def test_no_query_param_shows_all_approved_parcels(self):
        response = self.client.get(reverse('shipments:create'))
        content = response.content.decode()
        self.assertIn('Outfit A', content)
        self.assertIn('Outfit B', content)

    def test_query_param_narrows_to_only_that_parcel(self):
        response = self.client.get(reverse('shipments:create'), {'parcels': str(self.parcel_a.id)})
        content = response.content.decode()
        self.assertIn('Outfit A', content)
        self.assertNotIn('Outfit B', content)

    def test_malformed_uuid_in_query_param_does_not_500(self):
        """Regression guard: a non-UUID value in ?parcels= must be ignored,
        not raise an unhandled ValidationError from id__in=..."""
        response = self.client.get(reverse('shipments:create'), {'parcels': 'not-a-uuid'})
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('Outfit A', content)
        self.assertIn('Outfit B', content)

    def test_another_users_parcel_id_is_ignored(self):
        other_user = User.objects.create(email='other-preselect@example.com')
        other_locker = Locker.objects.create(user=other_user)
        other_parcel = Parcel.objects.create(
            locker=other_locker, status='approved', item_name='Not Mine',
            weight_kg=Decimal('1.0'), item_price=Decimal('500'), item_currency='INR',
            category='clothing',
        )
        response = self.client.get(reverse('shipments:create'), {'parcels': str(other_parcel.id)})
        content = response.content.decode()
        self.assertNotIn('Not Mine', content)
        self.assertNotIn('Outfit A', content)
        self.assertNotIn('Outfit B', content)
