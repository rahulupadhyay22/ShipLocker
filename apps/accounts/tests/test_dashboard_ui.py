"""
Tests for the dashboard view (spec: .claude/specs/01-dashboard-ui.md).

DashboardView's context grew one key beyond the original UI-only spec:
`recent_shipments`, added to back the Recent Shipments table added during
mockup-parity work (see the spec's "Actual scope delivered" addendum). These
tests lock down the parts of the context contract that are testable via the
Django test client: login guard, 200 + correct template for an authenticated
user, presence/type of every context key the template binds, auto-creation
of a Locker on first visit (ownership scoping via `user.locker`), and
per-user data isolation.
"""
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User, Locker
from apps.locker.models import Parcel

EXPECTED_CONTEXT_KEYS = [
    'parcels_in_trunk',
    'incoming_count',
    'total_weight_kg',
    'storage_days_left',
    'est_shipping_cost',
    'trunk_capacity',
    'declared_value_total',
    'avg_storage_days',
    'avg_weight_per_item',
    'recent_activity',
    'recent_shipments',
    'announcements',
]


class DashboardViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email='user@example.com')
        self.other_user = User.objects.create(email='other@example.com')
        self.url = reverse('accounts:dashboard')

    # -- Auth guard --------------------------------------------------

    def test_anonymous_user_redirected_to_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('accounts:login'), response.url)

    # -- Happy path ----------------------------------------------------

    def test_authenticated_user_gets_200_and_correct_template(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'accounts/dashboard.html')

    def test_context_contains_all_expected_keys(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        for key in EXPECTED_CONTEXT_KEYS:
            self.assertIn(key, response.context, f"missing context key: {key}")

    def test_context_key_types(self):
        # Give the user a parcel so numeric aggregates aren't None/empty edge cases.
        locker = Locker.objects.create(user=self.user)
        Parcel.objects.create(
            locker=locker,
            status='approved',
            weight_kg=1.5,
            item_price=1000,
        )
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        ctx = response.context

        self.assertIsInstance(ctx['parcels_in_trunk'], int)
        self.assertIsInstance(ctx['incoming_count'], int)
        # recent_activity, recent_shipments, and announcements must be iterable (list/queryset)
        list(ctx['recent_activity'])
        list(ctx['recent_shipments'])
        list(ctx['announcements'])

    # -- Ownership scoping: locker auto-created on first visit --------

    def test_locker_auto_created_on_first_dashboard_visit(self):
        self.assertFalse(Locker.objects.filter(user=self.user).exists())
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            Locker.objects.filter(user=self.user).exists(),
            "DashboardView should scope via user.locker and create one if missing",
        )

    # -- Ownership isolation --------------------------------------------

    def test_dashboard_only_shows_requesting_users_own_locker_data(self):
        # User A has no parcels; user B has several. Regardless of exactly
        # which statuses count towards "in trunk", A's count must not be
        # inflated by B's data.
        Locker.objects.create(user=self.user)
        other_locker = Locker.objects.create(user=self.other_user)
        Parcel.objects.create(locker=other_locker, status='approved', weight_kg=3)
        Parcel.objects.create(locker=other_locker, status='approved', weight_kg=4)

        self.client.force_login(self.user)
        response = self.client.get(self.url)

        self.assertEqual(response.context['parcels_in_trunk'], 0)
