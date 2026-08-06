"""Razorpay payment verification and order creation service."""

import hmac
import hashlib
import logging
from decimal import Decimal
from django.db import transaction
from .tax import calculate_gst
from indiabox.circuit_breaker import CircuitBreaker, CircuitOpenError

logger = logging.getLogger('security')

# Checkout critical path: every gunicorn thread blocked on a slow Razorpay
# call is a thread unavailable to unrelated pages, so fail fast once it's
# clearly degraded rather than let requests queue up behind it.
_breaker = CircuitBreaker('razorpay', fail_threshold=5, reset_timeout=60, max_concurrency=4)


class RazorpayService:
    """Service for Razorpay payment operations with signature verification."""

    def __init__(self):
        self._settings = None

    @property
    def settings(self):
        if self._settings is None:
            from apps.notifications.models import AppSettings
            self._settings = AppSettings.load()
        return self._settings

    @property
    def key_id(self):
        return self.settings.razorpay_key_id if self.settings else ''

    @property
    def key_secret(self):
        return self.settings.razorpay_key_secret if self.settings else ''

    @property
    def is_enabled(self):
        return (
            self.settings.razorpay_enabled if self.settings else False
        ) and self.key_id and self.key_secret

    def verify_payment_signature(self, order_id: str, payment_id: str, signature: str) -> bool:
        """
        Verify Razorpay payment signature to prevent spoofing.

        The signature is an HMAC-SHA256 hash of 'order_id|payment_id'
        using the Razorpay key_secret.

        Args:
            order_id: Razorpay order ID (order_xxxxx)
            payment_id: Razorpay payment ID (pay_xxxxx)
            signature: Razorpay signature from checkout response

        Returns:
            True if signature is valid, False otherwise
        """
        if not self.key_secret:
            logger.error("Razorpay key_secret not configured — cannot verify signature")
            return False

        message = f"{order_id}|{payment_id}"
        expected_signature = hmac.new(
            self.key_secret.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

        is_valid = hmac.compare_digest(expected_signature, signature)

        if not is_valid:
            logger.warning(
                f"Razorpay signature mismatch: order={order_id}, payment={payment_id}"
            )
        else:
            logger.info(
                f"Razorpay payment verified: order={order_id}, payment={payment_id}"
            )

        return is_valid

    def create_order(self, amount_paise: int, currency: str = 'INR', receipt: str = '', notes: dict = None):
        """
        Create a Razorpay order via API.

        Args:
            amount_paise: Amount in paise (e.g., 50000 = ₹500)
            currency: Currency code (default INR)
            receipt: Internal receipt/reference ID
            notes: Optional metadata dict

        Returns:
            Razorpay order dict or None on error
        """
        import requests

        if not self.is_enabled:
            logger.warning("Razorpay not enabled or credentials missing")
            return None

        url = 'https://api.razorpay.com/v1/orders'
        payload = {
            'amount': amount_paise,
            'currency': currency,
            'receipt': receipt,
        }
        if notes:
            payload['notes'] = notes

        try:
            with _breaker.call():
                response = requests.post(
                    url,
                    json=payload,
                    auth=(self.key_id, self.key_secret),
                    timeout=8,
                )
                response.raise_for_status()
                order = response.json()
            logger.info(f"Razorpay order created: {order.get('id')}")
            return order
        except CircuitOpenError as e:
            logger.error(f"Razorpay order creation skipped: {e}")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"Razorpay order creation failed: {e}")
            return None

    def verify_webhook_signature(self, body: bytes, signature: str, webhook_secret: str) -> bool:
        """
        Verify Razorpay webhook signature.

        Args:
            body: Raw request body bytes
            signature: X-Razorpay-Signature header value
            webhook_secret: Your webhook secret from Razorpay dashboard

        Returns:
            True if valid
        """
        expected = hmac.new(
            webhook_secret.encode('utf-8'),
            body,
            hashlib.sha256
        ).hexdigest()

        is_valid = hmac.compare_digest(expected, signature)
        if not is_valid:
            logger.warning("Razorpay webhook signature mismatch")
        return is_valid


def _get_daily_storage_fee_amount() -> Decimal:
    """Resolve daily storage fee amount from active service charges.

    Looks for an active ServiceCharge containing both "storage" and "day" in the name,
    then falls back to any active storage charge. If not configured, uses ₹50/day.
    """
    from django.db.models import Q
    from apps.content.models import ServiceCharge

    named_daily_charge = ServiceCharge.objects.filter(
        Q(name__icontains='storage') & Q(name__icontains='day'),
        is_active=True,
    ).order_by('updated_at').first()
    if named_daily_charge:
        return Decimal(str(named_daily_charge.amount))

    generic_storage_charge = ServiceCharge.objects.filter(
        is_active=True,
        name__icontains='storage',
    ).order_by('updated_at').first()
    if generic_storage_charge:
        return Decimal(str(generic_storage_charge.amount))

    return Decimal('50.00')


def _get_consolidation_fee_amount() -> Decimal:
    """Resolve the consolidation fee from active service charges.

    Looks for an active ServiceCharge with "consolidat" in the name (matches
    "Consolidation"). Not configured -> 0 (fee simply isn't shown/charged).
    """
    from apps.content.models import ServiceCharge

    charge = ServiceCharge.objects.filter(
        is_active=True,
        name__icontains='consolidat',
    ).order_by('updated_at').first()
    if charge:
        return Decimal(str(charge.amount))

    return Decimal('0.00')


def ensure_storage_fee_for_parcel(parcel):
    """Create or update pending StorageFee automatically after 30 free days."""
    from .models import StorageFee

    if not parcel or not parcel.received_at:
        return None

    overdue_days = max(0, parcel.storage_days - 30)
    if overdue_days <= 0:
        return None

    daily_fee = _get_daily_storage_fee_amount()
    total_fee = daily_fee * Decimal(overdue_days)

    storage_fee = StorageFee.objects.filter(
        parcel=parcel,
        status='pending',
    ).order_by('-created_at').first()

    if storage_fee:
        updated = False
        if storage_fee.days_overdue != overdue_days:
            storage_fee.days_overdue = overdue_days
            updated = True
        if storage_fee.fee_amount != total_fee:
            storage_fee.fee_amount = total_fee
            updated = True
        if updated:
            storage_fee.save(update_fields=['days_overdue', 'fee_amount'])
        return storage_fee

    # Do not auto-create another fee if one is already paid/waived.
    historical_fee = StorageFee.objects.filter(parcel=parcel).exists()
    if historical_fee:
        return None

    return StorageFee.objects.create(
        parcel=parcel,
        fee_amount=total_fee,
        days_overdue=overdue_days,
        status='pending',
    )


def _financial_year_label(invoice_date):
    """FY runs Apr 1 - Mar 31. E.g. any date in Apr 2026-Mar 2027 -> '2026-27'."""
    year = invoice_date.year
    if invoice_date.month >= 4:
        return f"{year}-{str(year + 1)[-2:]}"
    return f"{year - 1}-{str(year)[-2:]}"


def generate_invoice_number(invoice_date):
    """Sequential invoice number within a financial year: INV/2026-27/0001.
    Race-safe via select_for_update, same pattern as generate_shipment_id
    in apps/shipments/models.py."""
    from .models import Invoice

    prefix = f"INV/{_financial_year_label(invoice_date)}/"

    with transaction.atomic():
        last = (
            Invoice.objects
            .select_for_update()
            .filter(invoice_number__startswith=prefix)
            .order_by('-invoice_number')
            .first()
        )
        if last:
            try:
                num = int(last.invoice_number.rsplit('/', 1)[1]) + 1
            except (ValueError, IndexError):
                num = Invoice.objects.filter(invoice_number__startswith=prefix).count() + 1
        else:
            num = 1

    return f"{prefix}{num:04d}"


def build_charge_snapshot(shipment):
    """{'shipping_amount', 'storage_fee_amount', 'consolidation_fee_amount'}
    reusing the exact same totals already shown on the shipment detail page."""
    from apps.shipments.views import _payment_summary

    summary = _payment_summary(shipment)
    return {
        'shipping_amount': summary['shipping_amount'],
        'storage_fee_amount': summary['storage_fee_total'],
        'consolidation_fee_amount': summary['consolidation_fee'],
    }


def build_customer_snapshot(shipment):
    address_lines = [shipment.address_line1]
    if shipment.address_line2:
        address_lines.append(shipment.address_line2)
    address_lines.append(f"{shipment.city}, {shipment.state} {shipment.postal_code}")
    address_lines.append(shipment.country)
    return {
        'customer_name': shipment.recipient_name,
        'customer_email': shipment.recipient_email,
        'billing_address': '\n'.join(address_lines),
    }


class InvoiceService:
    """Single entry point for GST invoice generation. All the real work
    (snapshot, tax calc, PDF, upload, DB write) is coordinated from here —
    the calling signal/admin-action stays a one-line call into this class."""

    @staticmethod
    def generate_pdf(context):
        import io
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.units import mm
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=20 * mm, bottomMargin=20 * mm)
        styles = getSampleStyleSheet()
        story = []

        story.append(Paragraph(context['company_legal_name'] or 'ShipLocker', styles['Title']))
        story.append(Paragraph((context['company_registered_address'] or '').replace('\n', '<br/>'), styles['Normal']))
        story.append(Paragraph(
            f"GSTIN: {context['company_gstin'] or '-'} | PAN: {context['company_pan'] or '-'}",
            styles['Normal'],
        ))
        story.append(Spacer(1, 10 * mm))

        story.append(Paragraph(f"Invoice Number: {context['invoice_number']}", styles['Heading2']))
        story.append(Paragraph(f"Invoice Date: {context['invoice_date'].strftime('%d %b %Y')}", styles['Normal']))
        story.append(Spacer(1, 5 * mm))

        story.append(Paragraph('Bill To:', styles['Heading3']))
        story.append(Paragraph(context['customer_name'], styles['Normal']))
        story.append(Paragraph(context['billing_address'].replace('\n', '<br/>'), styles['Normal']))
        if context.get('customer_gstin'):
            story.append(Paragraph(f"GSTIN: {context['customer_gstin']}", styles['Normal']))
        story.append(Spacer(1, 8 * mm))

        rows = [['Description', 'Amount (INR)']]
        rows.append(['Shipping Charges', f"{context['shipping_amount']:.2f}"])
        if context['storage_fee_amount'] > 0:
            rows.append(['Storage Fee', f"{context['storage_fee_amount']:.2f}"])
        if context['consolidation_fee_amount'] > 0:
            rows.append(['Consolidation Fee', f"{context['consolidation_fee_amount']:.2f}"])
        rows.append(['Taxable Amount', f"{context['taxable_amount']:.2f}"])

        if context['is_zero_rated']:
            rows.append(['GST', 'Export of Service — Zero Rated (0%)'])
        else:
            if context['cgst_amount'] > 0:
                half_rate = context['gst_rate'] / 2
                rows.append([f"CGST @ {half_rate}%", f"{context['cgst_amount']:.2f}"])
                rows.append([f"SGST @ {half_rate}%", f"{context['sgst_amount']:.2f}"])
            if context['igst_amount'] > 0:
                rows.append([f"IGST @ {context['gst_rate']}%", f"{context['igst_amount']:.2f}"])

        rows.append(['Total Amount', f"{context['total_amount']:.2f}"])

        table = Table(rows, colWidths=[120 * mm, 50 * mm])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#003746')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('LINEABOVE', (0, -1), (-1, -1), 1, colors.black),
        ]))
        story.append(table)
        story.append(Spacer(1, 8 * mm))

        if context.get('payment_reference'):
            story.append(Paragraph(
                f"Paid via {context.get('payment_method') or 'online payment'} — Reference: {context['payment_reference']}",
                styles['Normal'],
            ))

        doc.build(story)
        return buffer.getvalue()

    @staticmethod
    def upload_pdf(pdf_bytes, shipment):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from apps.locker.utils import upload_shipment_document, get_user_locker_id

        locker_id = get_user_locker_id(shipment.user)
        filename = f"invoice_{shipment.display_id}.pdf"
        uploaded_file = SimpleUploadedFile(filename, pdf_bytes, content_type='application/pdf')
        return upload_shipment_document(uploaded_file, locker_id, shipment.display_id, 'invoice')

    @staticmethod
    def generate_for_shipment(shipment, paid_at=None):
        from django.utils import timezone
        from .models import Invoice, Payment
        from apps.shipments.models import ShipmentDocument
        from apps.notifications.models import AppSettings

        existing = Invoice.objects.filter(shipment=shipment).first()
        if existing:
            logger.info(f"Invoice already exists for shipment {shipment.pk} ({existing.invoice_number}), skipping")
            return existing

        invoice_date = paid_at or timezone.now()
        settings = AppSettings.get_settings()

        charges = build_charge_snapshot(shipment)
        taxable_amount = (
            charges['shipping_amount'] + charges['storage_fee_amount'] + charges['consolidation_fee_amount']
        )
        gst = calculate_gst(shipment, taxable_amount, settings)
        customer = build_customer_snapshot(shipment)

        payment = Payment.objects.filter(
            shipment=shipment, status='captured'
        ).order_by('-paid_at').first()

        invoice_number = generate_invoice_number(invoice_date)

        pdf_context = {
            'company_legal_name': settings.company_legal_name,
            'company_registered_address': settings.company_registered_address,
            'company_gstin': settings.company_gstin,
            'company_pan': settings.company_pan,
            'invoice_number': invoice_number,
            'invoice_date': invoice_date,
            'customer_gstin': '',
            **customer,
            **charges,
            'taxable_amount': taxable_amount,
            **gst,
            'payment_reference': payment.razorpay_payment_id if payment else '',
            'payment_method': payment.get_payment_method_display() if payment else '',
        }

        pdf_bytes = InvoiceService.generate_pdf(pdf_context)
        pdf_path = InvoiceService.upload_pdf(pdf_bytes, shipment)

        with transaction.atomic():
            invoice = Invoice.objects.create(
                shipment=shipment,
                invoice_number=invoice_number,
                invoice_date=invoice_date,
                customer_name=customer['customer_name'],
                customer_email=customer['customer_email'],
                billing_address=customer['billing_address'],
                customer_gstin='',
                payment_reference=payment.razorpay_payment_id if payment else '',
                payment_method=payment.get_payment_method_display() if payment else '',
                amount_paid=payment.amount if payment else gst['total_amount'],
                shipping_amount=charges['shipping_amount'],
                storage_fee_amount=charges['storage_fee_amount'],
                consolidation_fee_amount=charges['consolidation_fee_amount'],
                taxable_amount=taxable_amount,
                is_zero_rated=gst['is_zero_rated'],
                gst_rate=gst['gst_rate'],
                cgst_amount=gst['cgst_amount'],
                sgst_amount=gst['sgst_amount'],
                igst_amount=gst['igst_amount'],
                total_amount=gst['total_amount'],
                pdf_document_url=pdf_path,
            )
            ShipmentDocument.objects.create(
                shipment=shipment,
                document_type='invoice',
                document_url=pdf_path,
            )

        logger.info(f"Invoice {invoice_number} generated for shipment {shipment.pk}")
        return invoice
