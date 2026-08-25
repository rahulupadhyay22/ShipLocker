# Backfill premium_savings_amount (spec 11a) from existing paid/approved
# history, so the switch from live aggregate queries to a denormalized
# counter doesn't silently zero out savings customers already had.
from decimal import Decimal, ROUND_HALF_UP

from django.db import migrations
from django.db.models import Sum


SERVICE_FEE_RATE = Decimal('0.25')
SHIPPING_RATE = Decimal('0.05')
STORAGE_RATE = Decimal('0.20')


def backfill_premium_savings_amount(apps, schema_editor):
    """Same formula for every locker regardless of current plan_type: real
    discount already applied (Premium) and the hypothetical (Free) are both
    standard_amount * rate — see Locker.record_premium_savings' docstring
    for why that's the same number either way."""
    Locker = apps.get_model('accounts', 'Locker')
    PersonalShopQuotation = apps.get_model('personal_shop', 'PersonalShopQuotation')
    Shipment = apps.get_model('shipments', 'Shipment')
    BatchCharge = apps.get_model('payments', 'BatchCharge')

    zero = Decimal('0.00')

    for locker in Locker.objects.all():
        quotation_standard = PersonalShopQuotation.objects.filter(
            request__locker=locker, quotation_type='purchase', status='approved',
        ).aggregate(total=Sum('service_fee_standard_amount'))['total'] or zero
        shipment_standard = Shipment.objects.filter(
            user__locker=locker, payment_status='paid',
        ).aggregate(total=Sum('shipping_cost_standard'))['total'] or zero
        batch_standard = BatchCharge.objects.filter(
            batch__locker=locker, status='paid',
        ).aggregate(total=Sum('amount_standard'))['total'] or zero

        amount = (
            quotation_standard * SERVICE_FEE_RATE
            + shipment_standard * SHIPPING_RATE
            + batch_standard * STORAGE_RATE
        ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

        if amount != zero:
            Locker.objects.filter(pk=locker.pk).update(premium_savings_amount=amount)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0006_locker_premium_savings_amount'),
        ('personal_shop', '0007_backfill_service_fee_standard_amount'),
        ('shipments', '0008_shipment_consolidation_fee_standard_and_more'),
        ('payments', '0007_batchcharge_amount_standard'),
    ]

    operations = [
        migrations.RunPython(backfill_premium_savings_amount, noop_reverse),
    ]
