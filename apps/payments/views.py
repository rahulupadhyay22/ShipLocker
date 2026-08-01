"""Payment views with Razorpay integration and signature verification."""

import json
import logging
from datetime import timedelta
from decimal import Decimal
from django.http import JsonResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.contrib.auth.mixins import LoginRequiredMixin
from django.utils import timezone
from django.db import transaction
from django.db.models import Sum

from .models import Payment, StorageFee
from .services import RazorpayService
from apps.shipments.models import Shipment

logger = logging.getLogger('security')


def _get_pending_storage_fees_for_shipment(shipment):
    return StorageFee.objects.filter(
        parcel__shipment_items__shipment=shipment,
        status='pending',
    ).distinct()


def _mark_storage_fees_paid(payment):
    if not payment.shipment:
        return

    fee_ids = []
    if payment.notes:
        try:
            notes_data = json.loads(payment.notes)
            fee_ids = notes_data.get('storage_fee_ids', []) or []
        except (TypeError, json.JSONDecodeError):
            fee_ids = []

    fees_qs = StorageFee.objects.filter(status='pending')
    if fee_ids:
        fees_qs = fees_qs.filter(id__in=fee_ids)
    else:
        fees_qs = fees_qs.filter(parcel__shipment_items__shipment=payment.shipment).distinct()

    fees_qs.update(
        status='paid',
        payment=payment,
        paid_at=timezone.now(),
    )


class CreatePaymentOrderView(LoginRequiredMixin, View):
    """Create a Razorpay order for a shipment payment.

    Prevents duplicate orders by checking for a recent pending payment
    before creating a new one (guards against double-click).
    """

    def post(self, request, shipment_pk):
        shipment = get_object_or_404(
            Shipment, pk=shipment_pk, user=request.user
        )

        if not shipment.shipping_cost and shipment.payment_status != 'paid':
            return JsonResponse({'error': 'Shipping cost not set'}, status=400)

        service = RazorpayService()
        if not service.is_enabled:
            return JsonResponse({'error': 'Payments not configured'}, status=503)

        shipping_due = shipment.shipping_cost if shipment.payment_status != 'paid' else Decimal('0.00')

        pending_fees_qs = _get_pending_storage_fees_for_shipment(shipment)
        pending_storage_total = pending_fees_qs.aggregate(total=Sum('fee_amount'))['total'] or Decimal('0.00')
        pending_storage_total = pending_storage_total.quantize(Decimal('0.01'))

        total_due = (shipping_due + pending_storage_total).quantize(Decimal('0.01'))
        if total_due <= 0:
            return JsonResponse({'error': 'No pending charges for this shipment'}, status=400)

        # Amount in paise
        amount_paise = int(total_due * 100)

        fee_ids = [str(fee_id) for fee_id in pending_fees_qs.values_list('id', flat=True)]
        description_parts = []
        if shipping_due > 0:
            description_parts.append('shipping')
        if pending_storage_total > 0:
            description_parts.append('storage')
        charge_label = ' + '.join(description_parts) if description_parts else 'charges'

        with transaction.atomic():
            # Prevent double payment: check for recent pending payment (last 30 min)
            existing = Payment.objects.select_for_update().filter(
                shipment=shipment,
                user=request.user,
                status='pending',
                created_at__gte=timezone.now() - timedelta(minutes=30),
            ).order_by('-created_at').first()

            if existing and existing.razorpay_order_id:
                logger.info(
                    f"Returning existing payment order for {shipment.display_id}: "
                    f"{existing.razorpay_order_id}"
                )
                return JsonResponse({
                    'order_id': existing.razorpay_order_id,
                    'amount': int(existing.amount * 100),
                    'currency': existing.currency,
                    'key_id': service.key_id,
                    'payment_pk': str(existing.pk),
                })

            payment = Payment.objects.create(
                user=request.user,
                shipment=shipment,
                amount=total_due,
                currency=shipment.currency,
                payment_method='razorpay',
                status='pending',
                description=f'{charge_label.title()} for {shipment.display_id}',
                notes=json.dumps({
                    'shipment_id': str(shipment.pk),
                    'shipping_due': str(shipping_due),
                    'storage_due': str(pending_storage_total),
                    'storage_fee_ids': fee_ids,
                }),
            )

            order = service.create_order(
                amount_paise=amount_paise,
                currency=shipment.currency,
                receipt=payment.display_id,
                notes={
                    'shipment_id': str(shipment.pk),
                    'shipping_due': str(shipping_due),
                    'storage_due': str(pending_storage_total),
                },
            )

            if not order:
                payment.status = 'failed'
                payment.failure_reason = 'Order creation failed'
                payment.save()
                return JsonResponse({'error': 'Payment order creation failed'}, status=502)

            payment.razorpay_order_id = order['id']
            payment.save()

        return JsonResponse({
            'order_id': order['id'],
            'amount': amount_paise,
            'currency': shipment.currency,
            'key_id': service.key_id,
            'payment_pk': str(payment.pk),
        })


class VerifyPaymentView(LoginRequiredMixin, View):
    """Verify Razorpay payment signature and mark payment as captured."""

    def post(self, request):
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return HttpResponseBadRequest('Invalid JSON')

        razorpay_order_id = data.get('razorpay_order_id', '')
        razorpay_payment_id = data.get('razorpay_payment_id', '')
        razorpay_signature = data.get('razorpay_signature', '')

        if not all([razorpay_order_id, razorpay_payment_id, razorpay_signature]):
            return JsonResponse({'error': 'Missing payment parameters'}, status=400)

        payment = get_object_or_404(
            Payment,
            razorpay_order_id=razorpay_order_id,
            user=request.user,
        )

        service = RazorpayService()
        is_valid = service.verify_payment_signature(
            razorpay_order_id, razorpay_payment_id, razorpay_signature
        )

        if not is_valid:
            payment.status = 'failed'
            payment.failure_reason = 'Signature verification failed'
            payment.save()
            logger.warning(
                f"Payment signature FAILED: user={request.user.email} order={razorpay_order_id}"
            )
            return JsonResponse({'error': 'Payment verification failed'}, status=400)

        with transaction.atomic():
            payment.razorpay_payment_id = razorpay_payment_id
            payment.razorpay_signature = razorpay_signature
            payment.status = 'captured'
            payment.paid_at = timezone.now()
            payment.save()

            _mark_storage_fees_paid(payment)

            # Update shipment payment status
            if payment.shipment:
                payment.shipment.payment_status = 'paid'
                payment.shipment.paid_at = timezone.now()
                payment.shipment.advance_after_payment()
                payment.shipment.save()

        logger.info(
            f"Payment VERIFIED: user={request.user.email} "
            f"order={razorpay_order_id} amount={payment.amount}"
        )
        return JsonResponse({'status': 'success', 'payment_id': str(payment.pk)})


@method_decorator(csrf_exempt, name='dispatch')
class RazorpayWebhookView(View):
    """Handle Razorpay webhook callbacks (server-to-server)."""

    def post(self, request):
        signature = request.headers.get('X-Razorpay-Signature', '')
        if not signature:
            return HttpResponseBadRequest('Missing signature')

        # Webhook secret from AppSettings (preferred) or env fallback
        import os
        from apps.notifications.models import AppSettings

        settings = AppSettings.load()
        webhook_secret = (settings.razorpay_webhook_secret or '').strip() if settings else ''
        if not webhook_secret:
            webhook_secret = os.getenv('RAZORPAY_WEBHOOK_SECRET', '').strip()

        if not webhook_secret:
            logger.error("Razorpay webhook secret not configured in AppSettings or env")
            return JsonResponse({'error': 'Webhook not configured'}, status=503)

        service = RazorpayService()
        if not service.verify_webhook_signature(request.body, signature, webhook_secret):
            logger.warning("Razorpay webhook signature verification failed")
            return HttpResponseBadRequest('Invalid signature')

        try:
            payload = json.loads(request.body)
        except json.JSONDecodeError:
            return HttpResponseBadRequest('Invalid JSON')

        event = payload.get('event', '')

        if event == 'payment.captured':
            payment_entity = payload.get('payload', {}).get('payment', {}).get('entity', {})
            order_id = payment_entity.get('order_id')
            payment_id = payment_entity.get('id')

            if order_id:
                try:
                    payment = Payment.objects.get(razorpay_order_id=order_id)
                    if payment.status != 'captured':
                        with transaction.atomic():
                            payment.razorpay_payment_id = payment_id
                            payment.status = 'captured'
                            payment.paid_at = timezone.now()
                            payment.save()
                            _mark_storage_fees_paid(payment)
                            if payment.shipment:
                                payment.shipment.payment_status = 'paid'
                                payment.shipment.paid_at = timezone.now()
                                payment.shipment.advance_after_payment()
                                payment.shipment.save()
                        logger.info(f"Webhook: Payment captured for order {order_id}")
                except Payment.DoesNotExist:
                    logger.warning(f"Webhook: Payment not found for order {order_id}")

        elif event == 'payment.failed':
            payment_entity = payload.get('payload', {}).get('payment', {}).get('entity', {})
            order_id = payment_entity.get('order_id')
            if order_id:
                try:
                    payment = Payment.objects.get(razorpay_order_id=order_id)
                    payment.status = 'failed'
                    payment.failure_reason = payment_entity.get('error_description', 'Payment failed')
                    payment.save()
                    logger.info(f"Webhook: Payment failed for order {order_id}")
                except Payment.DoesNotExist:
                    pass

        return JsonResponse({'status': 'ok'})
