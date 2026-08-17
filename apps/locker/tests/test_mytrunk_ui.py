"""Tests for MyTrunkView (locker:my_locker) per spec 02-mytrunk-ui.

Spec is UI-only (stat tiles, toolbar, card layout) except for one server-side
behavior change: the "all" tab was dropped and the default `active_tab` when
no `?tab=` is given is now `ready_to_ship` (was `all`). These tests cover that
default-tab change plus the pre-existing (unchanged) view behaviors the spec
says must not regress: auth guard, locker-scoped ownership, tab filtering,
tab counts, per-item context fields, pagination, and empty state.

NOT covered here (out of scope, not server-testable via Django's test
client): client-side search/filter/sort/view-toggle JS, localStorage
persistence, "Select All"/"Ship Selected" checkbox JS behavior.
"""
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User, Locker
from apps.locker.models import Parcel, ReturnRequest, DiscardRequest
from apps.locker.services.batch_billing import get_open_batch


class MyTrunkViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='trunk@example.com')
        self.locker = Locker.objects.create(user=self.user)

        self.other_user = User.objects.create_user(email='other@example.com')
        self.other_locker = Locker.objects.create(user=self.other_user)

        self.url = reverse('locker:my_locker')

    def _make_parcel(self, locker, status, item_name):
        return Parcel.objects.create(
            locker=locker, status=status, item_name=item_name, weight_kg='1.50',
        )

    # 1. Auth guard
    def test_anonymous_get_redirects_to_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('accounts:login'), response.url)

    # 2. Ownership / isolation
    def test_items_scoped_to_own_locker_only(self):
        self._make_parcel(self.locker, 'approved', 'My Sneakers')
        self._make_parcel(self.other_locker, 'approved', 'Other Users Watch')

        self.client.force_login(self.user)
        response = self.client.get(self.url, {'tab': 'ready_to_ship'})

        titles = [i['title'] for i in response.context['items']]
        self.assertIn('My Sneakers', titles)
        self.assertNotIn('Other Users Watch', titles)
        self.assertNotContains(response, 'Other Users Watch')

    # 3. Default tab behavior
    def test_no_tab_param_defaults_to_ready_to_ship(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.context['active_tab'], 'ready_to_ship')

    def test_invalid_tab_param_falls_back_to_ready_to_ship(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url, {'tab': 'all'})
        self.assertEqual(response.context['active_tab'], 'ready_to_ship')

        response = self.client.get(self.url, {'tab': 'bogus_tab'})
        self.assertEqual(response.context['active_tab'], 'ready_to_ship')

    # 4. Tab filtering
    def test_tab_filtering_returns_only_matching_kind(self):
        self._make_parcel(self.locker, 'action_required', 'Action Item')
        self._make_parcel(self.locker, 'approved', 'Ready Item')
        returned_parcel = self._make_parcel(self.locker, 'return_requested', 'Return Item')
        ReturnRequest.objects.create(parcel=returned_parcel, reason='wrong size')
        discarded_parcel = self._make_parcel(self.locker, 'discard_requested', 'Discard Item')
        DiscardRequest.objects.create(parcel=discarded_parcel, reason='damaged')

        self.client.force_login(self.user)

        cases = {
            'action_required': ['Action Item'],
            'ready_to_ship': ['Ready Item'],
            'returns': ['Return Item'],
            'discards': ['Discard Item'],
        }
        for tab, expected_titles in cases.items():
            with self.subTest(tab=tab):
                response = self.client.get(self.url, {'tab': tab})
                self.assertEqual(response.context['active_tab'], tab)
                titles = [i['title'] for i in response.context['items']]
                self.assertEqual(sorted(titles), sorted(expected_titles))
                for kind_item in response.context['items']:
                    self.assertEqual(kind_item['kind'], tab)

    # 5. Tab counts
    def test_tab_counts_match_db_state(self):
        self._make_parcel(self.locker, 'action_required', 'AR 1')
        self._make_parcel(self.locker, 'action_required', 'AR 2')
        self._make_parcel(self.locker, 'approved', 'Ready 1')

        r1 = self._make_parcel(self.locker, 'return_requested', 'Ret 1')
        ReturnRequest.objects.create(parcel=r1, reason='r')
        r2 = self._make_parcel(self.locker, 'return_requested', 'Ret 2')
        rr2 = ReturnRequest.objects.create(parcel=r2, reason='r')
        rr2.status = 'completed'
        rr2.save()

        d1 = self._make_parcel(self.locker, 'discard_requested', 'Disc 1')
        DiscardRequest.objects.create(parcel=d1, reason='d')
        d2 = self._make_parcel(self.locker, 'discard_requested', 'Disc 2')
        dr2 = DiscardRequest.objects.create(parcel=d2, reason='d')
        dr2.status = 'discarded'
        dr2.save()

        self.client.force_login(self.user)
        response = self.client.get(self.url)

        self.assertEqual(response.context['action_count'], 2)
        self.assertEqual(response.context['ready_count'], 1)
        # completed return requests are excluded from the count
        self.assertEqual(response.context['return_count'], 1)
        # discarded discard requests are excluded from the count
        self.assertEqual(response.context['discard_count'], 1)

    # 6. Card data fields
    def test_item_context_fields_present_and_correct(self):
        parcel = self._make_parcel(self.locker, 'approved', 'Ready Widget')
        self.client.force_login(self.user)

        response = self.client.get(self.url, {'tab': 'ready_to_ship'})
        items = response.context['items']
        self.assertEqual(len(items), 1)
        item = items[0]

        self.assertIn('weight_kg', item)
        self.assertIn('date', item)
        self.assertIn('days_left', item)
        self.assertIn('status_label', item)
        self.assertIn('status_class', item)
        self.assertIn('pk', item)

        parcel.refresh_from_db()
        # Storage is billed per Trunk ID (Batch) now, not per parcel — assert
        # against the locker's open batch, mirroring MyTrunkView's own calc.
        batch = get_open_batch(self.locker)
        expected_days_left = max(0, (batch.free_storage_end_date - timezone.localdate()).days)
        self.assertEqual(item['days_left'], expected_days_left)
        self.assertEqual(item['pk'], parcel.pk)
        self.assertEqual(item['status_label'], 'Ready to Ship')

    # 7. Pagination
    def test_pagination_when_tab_has_more_than_20_items(self):
        for n in range(25):
            self._make_parcel(self.locker, 'approved', f'Item {n}')

        self.client.force_login(self.user)
        response = self.client.get(self.url, {'tab': 'ready_to_ship'})

        self.assertEqual(len(response.context['items']), 20)
        page_obj = response.context['page_obj']
        self.assertTrue(page_obj.has_next())
        self.assertEqual(page_obj.paginator.count, 25)

        response_page2 = self.client.get(self.url, {'tab': 'ready_to_ship', 'page': 2})
        self.assertEqual(len(response_page2.context['items']), 5)

    # 8. Empty state
    def test_empty_tab_returns_200_with_no_items(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url, {'tab': 'discards'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context['items']), [])
        self.assertContains(response, 'No Items Here')
