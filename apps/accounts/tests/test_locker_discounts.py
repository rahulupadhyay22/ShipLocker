"""Tests for Locker discount methods and LockerAdmin plan-toggle wiring."""

from decimal import Decimal
from unittest.mock import patch, MagicMock

from django.test import TestCase
from django.contrib.admin.sites import AdminSite
from django.test.client import RequestFactory

from apps.accounts.models import User, Locker
from apps.accounts.admin import LockerAdmin


class LockerDiscountTests(TestCase):
    """Test discount calculation methods on Locker model."""

    def setUp(self):
        self.user = User.objects.create(email='discount-test@example.com', is_active=True)
        self.free_locker = Locker.objects.create(user=self.user, plan_type='free')

    def test_is_premium_returns_false_for_free_plan(self):
        self.assertFalse(self.free_locker.is_premium)

    def test_is_premium_returns_true_for_paid_plan(self):
        paid_locker = Locker.objects.create(
            user=User.objects.create(email='paid@example.com', is_active=True),
            plan_type='paid'
        )
        self.assertTrue(paid_locker.is_premium)

    def test_apply_service_fee_discount_free_plan_no_discount(self):
        """Free plan should return amount unchanged with zero discount."""
        amount = Decimal('500.00')
        discounted, discount = self.free_locker.apply_service_fee_discount(amount)
        self.assertEqual(discounted, Decimal('500.00'))
        self.assertEqual(discount, Decimal('0.00'))

    def test_apply_service_fee_discount_paid_plan_25_percent(self):
        """Paid plan should return 75% of amount with 25% discount."""
        paid_locker = Locker.objects.create(
            user=User.objects.create(email='paid1@example.com', is_active=True),
            plan_type='paid'
        )
        amount = Decimal('500.00')
        discounted, discount = paid_locker.apply_service_fee_discount(amount)
        self.assertEqual(discounted, Decimal('375.00'))
        self.assertEqual(discount, Decimal('125.00'))

    def test_apply_service_fee_discount_rounding_half_up(self):
        """Test rounding: 50.02 * 0.25 = 12.505, rounds up to 12.51."""
        paid_locker = Locker.objects.create(
            user=User.objects.create(email='paid2@example.com', is_active=True),
            plan_type='paid'
        )
        amount = Decimal('50.02')
        discounted, discount = paid_locker.apply_service_fee_discount(amount)
        # 50.02 * 0.25 = 12.505, rounds to 12.51
        self.assertEqual(discount, Decimal('12.51'))
        self.assertEqual(discounted, Decimal('37.51'))

    def test_apply_service_fee_discount_none_amount(self):
        """None amount should return None and zero discount."""
        paid_locker = Locker.objects.create(
            user=User.objects.create(email='paid3@example.com', is_active=True),
            plan_type='paid'
        )
        discounted, discount = paid_locker.apply_service_fee_discount(None)
        self.assertIsNone(discounted)
        self.assertEqual(discount, Decimal('0.00'))

    def test_apply_shipping_discount_free_plan_no_discount(self):
        """Free plan should return amount unchanged with zero discount."""
        amount = Decimal('500.00')
        discounted, discount = self.free_locker.apply_shipping_discount(amount)
        self.assertEqual(discounted, Decimal('500.00'))
        self.assertEqual(discount, Decimal('0.00'))

    def test_apply_shipping_discount_paid_plan_5_percent(self):
        """Paid plan should return 95% of amount with 5% discount."""
        paid_locker = Locker.objects.create(
            user=User.objects.create(email='paid4@example.com', is_active=True),
            plan_type='paid'
        )
        amount = Decimal('500.00')
        discounted, discount = paid_locker.apply_shipping_discount(amount)
        self.assertEqual(discounted, Decimal('475.00'))
        self.assertEqual(discount, Decimal('25.00'))

    def test_apply_shipping_discount_rounding_half_up(self):
        """Test rounding: 250.30 * 0.05 = 12.515, rounds up to 12.52."""
        paid_locker = Locker.objects.create(
            user=User.objects.create(email='paid5@example.com', is_active=True),
            plan_type='paid'
        )
        amount = Decimal('250.30')
        discounted, discount = paid_locker.apply_shipping_discount(amount)
        # 250.30 * 0.05 = 12.515, rounds to 12.52
        self.assertEqual(discount, Decimal('12.52'))
        self.assertEqual(discounted, Decimal('237.78'))

    def test_apply_shipping_discount_none_amount(self):
        """None amount should return None and zero discount."""
        paid_locker = Locker.objects.create(
            user=User.objects.create(email='paid6@example.com', is_active=True),
            plan_type='paid'
        )
        discounted, discount = paid_locker.apply_shipping_discount(None)
        self.assertIsNone(discounted)
        self.assertEqual(discount, Decimal('0.00'))

    def test_apply_storage_discount_free_plan_no_discount(self):
        """Free plan should return amount unchanged with zero discount."""
        amount = Decimal('100.00')
        discounted, discount = self.free_locker.apply_storage_discount(amount)
        self.assertEqual(discounted, Decimal('100.00'))
        self.assertEqual(discount, Decimal('0.00'))

    def test_apply_storage_discount_paid_plan_20_percent(self):
        """Paid plan should return 80% of amount with 20% discount."""
        paid_locker = Locker.objects.create(
            user=User.objects.create(email='paid8@example.com', is_active=True),
            plan_type='paid'
        )
        amount = Decimal('100.00')
        discounted, discount = paid_locker.apply_storage_discount(amount)
        self.assertEqual(discounted, Decimal('80.00'))
        self.assertEqual(discount, Decimal('20.00'))

    def test_apply_storage_discount_none_amount(self):
        """None amount should return None and zero discount."""
        paid_locker = Locker.objects.create(
            user=User.objects.create(email='paid9@example.com', is_active=True),
            plan_type='paid'
        )
        discounted, discount = paid_locker.apply_storage_discount(None)
        self.assertIsNone(discounted)
        self.assertEqual(discount, Decimal('0.00'))

    def test_premium_free_service_returns_plan_status(self):
        """premium_free_service should return True for paid, False for free."""
        self.assertFalse(self.free_locker.premium_free_service())

        paid_locker = Locker.objects.create(
            user=User.objects.create(email='paid7@example.com', is_active=True),
            plan_type='paid'
        )
        self.assertTrue(paid_locker.premium_free_service())


class LockerAdminSaveModelTests(TestCase):
    """Test LockerAdmin.save_model plan change triggering apply_upgrade/apply_downgrade."""

    def setUp(self):
        self.factory = RequestFactory()
        self.admin_site = AdminSite()
        self.locker_admin = LockerAdmin(Locker, self.admin_site)
        self.user = User.objects.create(email='admin-test@example.com', is_active=True)

    def test_save_model_new_locker_does_not_call_upgrade(self):
        """Creating a new locker (change=False) should not call apply_upgrade."""
        request = self.factory.get('/admin/')
        request.user = self.user

        locker = Locker(user=self.user, plan_type='paid')

        with patch('apps.locker.services.batch_billing.apply_upgrade') as mock_upgrade:
            self.locker_admin.save_model(request, locker, None, change=False)
            # Should not call apply_upgrade for new object (change=False)
            mock_upgrade.assert_not_called()

    def test_save_model_free_to_paid_calls_apply_upgrade(self):
        """Changing plan from free to paid should call apply_upgrade."""
        request = self.factory.get('/admin/')
        request.user = self.user

        # Create a locker with free plan
        locker = Locker.objects.create(user=self.user, plan_type='free')

        # Change to paid in memory
        locker.plan_type = 'paid'

        with patch('apps.locker.services.batch_billing.apply_upgrade') as mock_upgrade:
            with patch('apps.locker.services.batch_billing.get_open_batch', return_value=None):
                self.locker_admin.save_model(request, locker, None, change=True)
                # Should call apply_upgrade once with correct arguments
                mock_upgrade.assert_called_once()
                call_args = mock_upgrade.call_args
                self.assertEqual(call_args[0][0], locker)  # locker object
                # call_args[0][1] is today (date object, can't compare directly)
                self.assertIsNone(call_args[1]['active_batch'])

    def test_save_model_paid_to_free_calls_apply_downgrade(self):
        """Changing plan from paid to free should call apply_downgrade."""
        request = self.factory.get('/admin/')
        request.user = self.user

        # Create a locker with paid plan
        locker = Locker.objects.create(user=self.user, plan_type='paid')

        # Change to free in memory
        locker.plan_type = 'free'

        with patch('apps.locker.services.batch_billing.apply_downgrade') as mock_downgrade:
            self.locker_admin.save_model(request, locker, None, change=True)
            # Should call apply_downgrade once
            mock_downgrade.assert_called_once()
            call_args = mock_downgrade.call_args
            self.assertEqual(call_args[0][0], locker)  # locker object

    def test_save_model_same_plan_does_not_call_upgrade_or_downgrade(self):
        """Saving without changing plan should not call apply_upgrade or apply_downgrade."""
        request = self.factory.get('/admin/')
        request.user = self.user

        # Create a locker with free plan
        locker = Locker.objects.create(user=self.user, plan_type='free')

        # Save without changing plan_type
        with patch('apps.locker.services.batch_billing.apply_upgrade') as mock_upgrade:
            with patch('apps.locker.services.batch_billing.apply_downgrade') as mock_downgrade:
                self.locker_admin.save_model(request, locker, None, change=True)
                # Should not call either function
                mock_upgrade.assert_not_called()
                mock_downgrade.assert_not_called()

    def test_save_model_passes_active_batch_to_apply_upgrade(self):
        """apply_upgrade should receive the active_batch from get_open_batch."""
        request = self.factory.get('/admin/')
        request.user = self.user

        locker = Locker.objects.create(user=self.user, plan_type='free')
        locker.plan_type = 'paid'

        mock_batch = MagicMock()
        with patch('apps.locker.services.batch_billing.apply_upgrade') as mock_upgrade:
            with patch('apps.locker.services.batch_billing.get_open_batch', return_value=mock_batch):
                self.locker_admin.save_model(request, locker, None, change=True)
                # Should pass the mock_batch as active_batch
                mock_upgrade.assert_called_once()
                call_args = mock_upgrade.call_args
                self.assertEqual(call_args[1]['active_batch'], mock_batch)

    def test_save_model_persists_locker_to_database(self):
        """save_model should actually persist the locker to the database."""
        request = self.factory.get('/admin/')
        request.user = self.user

        locker = Locker.objects.create(user=self.user, plan_type='free')
        original_id = locker.id

        locker.plan_type = 'paid'
        with patch('apps.locker.services.batch_billing.apply_upgrade'):
            with patch('apps.locker.services.batch_billing.get_open_batch', return_value=None):
                self.locker_admin.save_model(request, locker, None, change=True)

        # Verify locker was saved to database
        saved_locker = Locker.objects.get(id=original_id)
        self.assertEqual(saved_locker.plan_type, 'paid')
