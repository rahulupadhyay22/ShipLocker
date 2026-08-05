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
