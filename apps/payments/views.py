"""Payment views with Razorpay integration and signature verification."""

import json
import logging
from django.http import JsonResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.contrib.auth.mixins import LoginRequiredMixin
from django.utils import timezone
from django.db import transaction

from .models import Payment
from .services import RazorpayService
from apps.shipments.models import Shipment

logger = logging.getLogger('security')


class CreatePaymentOrderView(LoginRequiredMixin, View):
    """Create a Razorpay order for a shipment payment."""

    def post(self, request, shipment_pk):
        shipment = get_object_or_404(
            Shipment, pk=shipment_pk, user=request.user
        )

        if not shipment.shipping_cost:
            return JsonResponse({'error': 'Shipping cost not set'}, status=400)

        service = RazorpayService()
        if not service.is_enabled:
            return JsonResponse({'error': 'Payments not configured'}, status=503)

        # Amount in paise
        amount_paise = int(shipment.shipping_cost * 100)

        with transaction.atomic():
            payment = Payment.objects.create(
                user=request.user,
                shipment=shipment,
                amount=shipment.shipping_cost,
                currency=shipment.currency,
                payment_method='razorpay',
                status='pending',
                description=f'Shipping for {shipment.display_id}',
            )

            order = service.create_order(
                amount_paise=amount_paise,
                currency=shipment.currency,
                receipt=payment.display_id,
                notes={'shipment_id': str(shipment.pk)},
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

            # Update shipment payment status
            if payment.shipment:
                payment.shipment.payment_status = 'paid'
                payment.shipment.paid_at = timezone.now()
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

        # Webhook secret should be in AppSettings or env
        import os
        webhook_secret = os.getenv('RAZORPAY_WEBHOOK_SECRET', '')
        if not webhook_secret:
            logger.error("RAZORPAY_WEBHOOK_SECRET not configured")
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
                            if payment.shipment:
                                payment.shipment.payment_status = 'paid'
                                payment.shipment.paid_at = timezone.now()
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
