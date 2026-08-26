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
    """Premium lockers get real money already saved (standard - actual,
    same as calculate_premium_savings_breakdown()) — using standard * rate
    here would overcount rows backfilled with standard == actual (no real
    discount ever applied, see personal_shop 0007 / shipments 0008). Free
    lockers get the hypothetical standard * rate, since they have no actual
    discount to subtract.

    Assumes the four sources summed here (quotation service fee, shipment
    shipping cost, batch storage charges, consolidation) are the complete
    set of increment sources record_premium_savings() has ever applied —
    if a new discount category is added upstream without a matching branch
    here, this backfill silently under-counts it. This is an overwrite
    (.update(premium_savings_amount=amount)), not an increment, so it also
    assumes no other process is concurrently calling record_premium_savings()
    for the same locker while this migration runs; run during a maintenance
    window / before traffic resumes on a fresh deploy, not against a live
    database taking payments."""
    Locker = apps.get_model('accounts', 'Locker')
    PersonalShopQuotation = apps.get_model('personal_shop', 'PersonalShopQuotation')
    Shipment = apps.get_model('shipments', 'Shipment')
    BatchCharge = apps.get_model('payments', 'BatchCharge')

    zero = Decimal('0.00')

    def discount(standard, actual):
        return max(zero, (standard or zero) - (actual or zero))

    for locker in Locker.objects.all():
        quotation_totals = PersonalShopQuotation.objects.filter(
            request__locker=locker, quotation_type='purchase', status='approved',
        ).aggregate(standard=Sum('service_fee_standard_amount'), actual=Sum('service_fee_amount'))
        shipment_totals = Shipment.objects.filter(
            user__locker=locker, payment_status='paid',
        ).aggregate(
            standard=Sum('shipping_cost_standard'), actual=Sum('shipping_cost'),
            consolidation_standard=Sum('consolidation_fee_standard'), consolidation_actual=Sum('consolidation_fee'),
        )
        batch_totals = BatchCharge.objects.filter(
            batch__locker=locker, status='paid',
        ).aggregate(standard=Sum('amount_standard'), actual=Sum('amount'))

        if locker.plan_type == 'paid':
            amount = (
                discount(quotation_totals['standard'], quotation_totals['actual'])
                + discount(shipment_totals['standard'], shipment_totals['actual'])
                + discount(batch_totals['standard'], batch_totals['actual'])
                + discount(shipment_totals['consolidation_standard'], shipment_totals['consolidation_actual'])
            ).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        else:
            amount = (
                (quotation_totals['standard'] or zero) * SERVICE_FEE_RATE
                + (shipment_totals['standard'] or zero) * SHIPPING_RATE
                + (batch_totals['standard'] or zero) * STORAGE_RATE
                # 100% off, not a rate — consolidation is fully waived for Premium.
                + (shipment_totals['consolidation_standard'] or zero)
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
