"""Payment views with Razorpay integration and signature verification."""

import json
import logging
from datetime import timedelta
from decimal import Decimal
from django.http import JsonResponse, HttpResponseBadRequest, Http404
from django.shortcuts import get_object_or_404
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.contrib.auth.mixins import LoginRequiredMixin
from django.utils import timezone
from django.db import transaction
from django.db.models import Sum

from .models import Payment, BatchCharge
from .services import RazorpayService
from apps.shipments.models import Shipment

logger = logging.getLogger('security')


def _get_pending_batch_charges_for_locker(locker):
    """All pending BatchCharge rows for this locker's batches. Unlike the old
    per-parcel StorageFee (scoped to a shipment via its parcels), a
    BatchCharge belongs to a Batch (a Trunk ID) — so this pulls in every
    outstanding charge across all of the locker's batches, not just ones
    tied to the parcels in the shipment being paid for. Used by
    CreatePaymentOrderView to bundle the locker's storage balance into a
    shipment's Razorpay order, same as the old StorageFee flow did."""
    return BatchCharge.objects.filter(batch__locker=locker, status='pending')


def _mark_batch_charges_paid(payment):
    """Marks the BatchCharge rows referenced in payment.notes['batch_charge_ids']
    as paid. Works for any payment_type — a future storage_batch payment
    flow populates this list the same way shipment payments once populated
    storage_fee_ids."""
    charge_ids = []
    if payment.notes:
        try:
            notes_data = json.loads(payment.notes)
            charge_ids = notes_data.get('batch_charge_ids', []) or []
        except (TypeError, json.JSONDecodeError):
            charge_ids = []

    if not charge_ids:
        return

    BatchCharge.objects.filter(status='pending', id__in=charge_ids).update(
        status='paid',
        payment=payment,
        paid_at=timezone.now(),
    )


def _activate_premium_subscription(payment):
    """Called once a premium_subscription Payment is captured, from both
    VerifyPaymentView and RazorpayWebhookView — kept in one place, same
    reason _mark_batch_charges_paid above is shared between the two."""
    from apps.locker.services.batch_billing import apply_upgrade, resolve_grace_period, get_open_batch

    locker = payment.user.locker
    today = timezone.localdate()
    was_free = locker.plan_type != 'paid'
    in_grace = locker.payment_grace_until is not None
    base_date = locker.premium_expires_at if (locker.premium_expires_at and locker.premium_expires_at > today) else today
    locker.plan_type = 'paid'
    locker.premium_expires_at = base_date + timedelta(days=365)
    locker.save(update_fields=['plan_type', 'premium_expires_at'])
    if in_grace:
        resolve_grace_period(locker, today, payment_succeeded=True)
    elif was_free:
        apply_upgrade(locker, today, active_batch=get_open_batch(locker))
    logger.info(f"Premium subscription activated/renewed: locker={locker.locker_id} expires={locker.premium_expires_at}")


class CreatePaymentOrderView(LoginRequiredMixin, View):
    """Create a Razorpay order for a shipment payment.

    Prevents duplicate orders by checking for a recent pending payment
    before creating a new one (guards against double-click).
    """

    def post(self, request, shipment_pk):
        shipment = get_object_or_404(
            Shipment, pk=shipment_pk, user=request.user
        )
        shipment.refresh_shipping_discount()

        if not shipment.shipping_cost and shipment.payment_status != 'paid':
            return JsonResponse({'error': 'Shipping cost not set'}, status=400)

        service = RazorpayService()
        if not service.is_enabled:
            return JsonResponse({'error': 'Payments not configured'}, status=503)

        shipping_due = shipment.shipping_cost if shipment.payment_status != 'paid' else Decimal('0.00')

        # Storage is billed per Trunk ID (Batch), not per shipment, but
        # paying for a shipment is a natural moment to settle the locker's
        # outstanding storage balance too — bundle it into the same order.
        locker = getattr(shipment.user, 'locker', None)
        pending_charges_qs = _get_pending_batch_charges_for_locker(locker) if locker else BatchCharge.objects.none()
        pending_storage_total = pending_charges_qs.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        pending_storage_total = pending_storage_total.quantize(Decimal('0.01'))

        total_due = (shipping_due + pending_storage_total).quantize(Decimal('0.01'))
        if total_due <= 0:
            return JsonResponse({'error': 'No pending charges for this shipment'}, status=400)

        # Amount in paise
        amount_paise = int(total_due * 100)

        charge_ids = [str(charge_id) for charge_id in pending_charges_qs.values_list('id', flat=True)]
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
                    'batch_charge_ids': charge_ids,
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


class CreatePremiumSubscriptionOrderView(LoginRequiredMixin, View):
    """Create a Razorpay order for a self-serve CamelTrunk Premium annual
    subscription. Operates on request.user directly (no URL-supplied
    object), so LoginRequiredMixin alone is correct — no ownership mixin
    needed since there's no pk being looked up."""

    def post(self, request):
        locker = getattr(request.user, 'locker', None)
        if locker is None:
            return JsonResponse({'error': 'No locker found'}, status=404)

        service = RazorpayService()
        if not service.is_enabled:
            return JsonResponse({'error': 'Payments not configured'}, status=503)

        from apps.notifications.models import AppSettings
        price = AppSettings.get_settings().premium_annual_price
        amount_paise = int(price * 100)

        with transaction.atomic():
            existing = Payment.objects.select_for_update().filter(
                user=request.user, payment_type='premium_subscription', status='pending',
                created_at__gte=timezone.now() - timedelta(minutes=30),
            ).order_by('-created_at').first()
            if existing and existing.razorpay_order_id:
                return JsonResponse({
                    'order_id': existing.razorpay_order_id,
                    'amount': int(existing.amount * 100),
                    'currency': existing.currency,
                    'key_id': service.key_id,
                    'payment_pk': str(existing.pk),
                })

            payment = Payment.objects.create(
                user=request.user, amount=price, currency='INR',
                payment_type='premium_subscription', payment_method='razorpay', status='pending',
                description='CamelTrunk Premium — annual subscription',
            )
            order = service.create_order(
                amount_paise=amount_paise, currency='INR', receipt=payment.display_id,
                notes={'user_id': str(request.user.id), 'purpose': 'premium_subscription'},
            )
            if not order:
                payment.status = 'failed'
                payment.failure_reason = 'Order creation failed'
                payment.save()
                return JsonResponse({'error': 'Payment order creation failed'}, status=502)

            payment.razorpay_order_id = order['id']
            payment.save()

        return JsonResponse({
            'order_id': order['id'], 'amount': amount_paise, 'currency': 'INR',
            'key_id': service.key_id, 'payment_pk': str(payment.pk),
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
            try:
                payment = Payment.objects.select_for_update().get(
                    razorpay_order_id=razorpay_order_id, user=request.user
                )
            except Payment.DoesNotExist:
                raise Http404

            if payment.status == 'captured':
                return JsonResponse({'status': 'success', 'payment_id': str(payment.pk)})

            payment.razorpay_payment_id = razorpay_payment_id
            payment.razorpay_signature = razorpay_signature
            payment.status = 'captured'
            payment.paid_at = timezone.now()
            payment.save()

            _mark_batch_charges_paid(payment)

            # Update shipment payment status
            if payment.shipment:
                payment.shipment.payment_status = 'paid'
                payment.shipment.paid_at = timezone.now()
                payment.shipment.advance_after_payment()
                payment.shipment.save()
            elif payment.personal_shop_request:
                payment.personal_shop_request.mark_paid()
            elif payment.payment_type == 'storage_batch':
                logger.info(f"Storage batch payment captured: payment={payment.pk} amount={payment.amount}")
            elif payment.payment_type == 'premium_subscription':
                _activate_premium_subscription(payment)

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
                    with transaction.atomic():
                        payment = Payment.objects.select_for_update().get(razorpay_order_id=order_id)

                        captured_now = payment.status != 'captured'
                        if captured_now:
                            payment.razorpay_payment_id = payment_id
                            payment.status = 'captured'
                            payment.paid_at = timezone.now()
                            payment.save()
                            _mark_batch_charges_paid(payment)
                            if payment.shipment:
                                payment.shipment.payment_status = 'paid'
                                payment.shipment.paid_at = timezone.now()
                                payment.shipment.advance_after_payment()
                                payment.shipment.save()
                            elif payment.personal_shop_request:
                                payment.personal_shop_request.mark_paid()
                            elif payment.payment_type == 'storage_batch':
                                logger.info(f"Storage batch payment captured via webhook: payment={payment.pk}")
                            elif payment.payment_type == 'premium_subscription':
                                _activate_premium_subscription(payment)
                    if captured_now:
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
