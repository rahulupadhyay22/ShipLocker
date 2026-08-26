import logging
import uuid
from django.shortcuts import render
from django.views.generic import ListView, DetailView, TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Q

from .models import Shipment, ShipmentItem, ShipmentDocument

logger = logging.getLogger('security')

# Statuses (plus paid) at/after which a shipment's service tier is locked in.
SERVICE_LOCKED_STATUSES = (
    'packing', 'dispatched', 'in_transit', 'customs',
    'out_for_delivery', 'delivered', 'returned', 'cancelled',
)


def _is_service_locked(shipment):
    return shipment.payment_status == 'paid' or shipment.status in SERVICE_LOCKED_STATUSES


def _match_shipping_zone(shipment):
    from apps.content.models import ShippingZone
    country = (shipment.country or '').strip().upper()
    if not country:
        return None
    for zone in ShippingZone.objects.filter(is_active=True).order_by('order').prefetch_related('rates'):
        if country in zone.get_countries_list():
            return zone
    return None


def _payment_summary(shipment):
    """Shipping/storage/total figures shown in the Payment Summary card.
    Shared by the detail page context and the AJAX service-selection response
    so both compute the total-due math identically.

    Storage is billed per Trunk ID (Batch), not per shipment — BatchCharge
    doesn't belong to any specific shipment's parcels, so "pending" here is
    the locker's whole outstanding storage balance (not just charges from
    parcels in this shipment). CreatePaymentOrderView bundles that same
    pending total into this shipment's Razorpay order, so paying for the
    shipment settles the storage balance too. "Paid" is scoped to storage
    charges actually settled via a payment for *this* shipment."""
    from decimal import Decimal
    from django.db.models import Sum
    from apps.payments.models import BatchCharge

    locker = getattr(shipment.user, 'locker', None)
    if locker is None:
        # No Locker exists for this user (shouldn't happen in the normal
        # signup flow, but nothing here should ever 500 on a payment path) —
        # there can be no batches, so no storage balance either.
        pending_total = paid_total = Decimal('0.00')
    else:
        pending_total = BatchCharge.objects.filter(
            batch__locker=locker, status='pending',
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        paid_total = BatchCharge.objects.filter(
            batch__locker=locker, status='paid', payment__shipment=shipment,
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

    shipping_amount = Decimal(str(shipment.shipping_cost or 0))
    consolidation_fee = Decimal(str(shipment.consolidation_fee or 0))
    storage_total = pending_total + paid_total
    unpaid_charges = shipping_amount + consolidation_fee

    return {
        'storage_fee_pending': pending_total,
        'storage_fee_paid': paid_total,
        'storage_fee_total': storage_total,
        'shipping_amount': shipping_amount,
        'consolidation_fee': consolidation_fee,
        'shipping_discount_amount': shipment.shipping_discount_amount,
        'consolidation_fee_discount_amount': shipment.consolidation_fee_discount_amount,
        'shipment_total_amount': unpaid_charges + storage_total,
        'shipment_amount_due': pending_total + (unpaid_charges if shipment.payment_status != 'paid' else Decimal('0.00')),
    }


def _get_service_options(shipment):
    """List of priced service-tier options for this shipment, or None if
    weight isn't known yet. Empty list means weight is known but no
    zone/rate matches (shown as a distinct 'contact support' state)."""
    from decimal import Decimal

    if shipment.total_weight_kg is None:
        return None

    zone = _match_shipping_zone(shipment)
    if not zone:
        return []

    locker = getattr(shipment.user, 'locker', None)

    options = []
    for code, label in Shipment.SERVICE_TYPE_CHOICES:
        rate = zone.rates.filter(
            is_active=True, service_type=code,
            min_weight__lte=shipment.total_weight_kg,
            max_weight__gt=shipment.total_weight_kg,
        ).first()
        if rate:
            standard = Decimal(str(rate.calculate_price(shipment.total_weight_kg))).quantize(Decimal('0.01'))
            final, _discount = (
                locker.apply_shipping_discount(standard) if locker else (standard, Decimal('0.00'))
            )
            options.append({
                'code': code,
                'label': label,
                'price': final,
                'standard_price': standard,
                'delivery_days_min': rate.delivery_days_min,
                'delivery_days_max': rate.delivery_days_max,
            })
    return options


class ShipmentStatsMixin:
    """Mixin to provide shipment status counts with single aggregate query."""
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        counts = Shipment.objects.filter(user=user).aggregate(
            total_count=Count('id'),
            in_transit_count=Count('id', filter=Q(status__in=['in_transit', 'out_for_delivery'])),
            customs_count=Count('id', filter=Q(status='customs')),
            delivered_count=Count('id', filter=Q(status='delivered')),
        )
        context.update(counts)
        return context


class ShipmentsListView(LoginRequiredMixin, ShipmentStatsMixin, ListView):
    """Main shipments page - defaults to Active."""
    template_name = 'shipments/list.html'
    context_object_name = 'shipments'
    paginate_by = 20
    
    def get_queryset(self):
        return Shipment.objects.filter(user=self.request.user).order_by('-created_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['shipment_status_choices'] = Shipment.STATUS_CHOICES
        return context


class ActiveShipmentsView(LoginRequiredMixin, ShipmentStatsMixin, ListView):
    """Active shipments tab."""
    template_name = 'shipments/active.html'
    context_object_name = 'shipments'
    paginate_by = 20
    
    def get_queryset(self):
        return Shipment.objects.filter(
            user=self.request.user,
            status__in=['packing', 'dispatched', 'in_transit', 'customs', 'out_for_delivery', 'declaration_pending', 'pending_payment']
        ).order_by('-created_at')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['tab'] = 'active'
        return context


class DeliveredShipmentsView(LoginRequiredMixin, ShipmentStatsMixin, ListView):
    """Delivered shipments tab."""
    template_name = 'shipments/delivered.html'
    context_object_name = 'shipments'
    paginate_by = 20
    
    def get_queryset(self):
        return Shipment.objects.filter(
            user=self.request.user,
            status='delivered'
        ).order_by('-created_at')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['tab'] = 'delivered'
        return context


class ClosedShipmentsView(LoginRequiredMixin, ShipmentStatsMixin, ListView):
    """Returned & Cancelled shipments tab."""
    template_name = 'shipments/closed.html'
    context_object_name = 'shipments'
    paginate_by = 20
    
    def get_queryset(self):
        return Shipment.objects.filter(
            user=self.request.user,
            status__in=['returned', 'cancelled']
        ).order_by('-created_at')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['tab'] = 'closed'
        return context


class ShipmentDetailView(LoginRequiredMixin, DetailView):
    """Shipment detail page."""
    template_name = 'shipments/detail.html'
    context_object_name = 'shipment'
    
    def get_queryset(self):
        return Shipment.objects.filter(
            user=self.request.user
        ).prefetch_related('items__parcel__images', 'documents', 'tracking_events')

    def get_context_data(self, **kwargs):
        from apps.notifications.models import AppSettings

        context = super().get_context_data(**kwargs)
        shipment = self.object
        shipment.refresh_shipping_discount()

        context['warehouse_address'] = AppSettings.get_settings().warehouse_address
        context['service_options'] = _get_service_options(shipment)
        context['service_locked'] = _is_service_locked(shipment)
        locker = getattr(shipment.user, 'locker', None)
        context['is_premium'] = bool(locker and locker.is_premium)
        if locker is not None:
            context['premium_savings'] = locker.premium_savings_display
        # Use the prefetched .all() (cached) rather than .filter(), which would
        # issue a fresh query and defeat the prefetch_related('documents') above.
        prefetched_docs = list(shipment.documents.all())
        invoice_docs = [d for d in prefetched_docs if d.document_type == 'invoice']
        context['invoice_document'] = max(invoice_docs, key=lambda d: d.uploaded_at, default=None)
        context['awb_document'] = next((d for d in prefetched_docs if d.document_type == 'label'), None)
        context['other_documents'] = [d for d in prefetched_docs if d.document_type not in ('invoice', 'label')]

        context.update(_payment_summary(shipment))
        return context


class CustomsHelpView(LoginRequiredMixin, TemplateView):
    """Customs help page with carrier contacts."""
    template_name = 'shipments/customs_help.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['carriers'] = [
            {
                'name': 'DHL Express',
                'phone': '+1 (800) 225-5345',
                'url': 'https://www.dhl.com/us-en/home/tracking.html'
            },
            {
                'name': 'FedEx',
                'phone': '+1 (800) 463-3339',
                'url': 'https://www.fedex.com/en-us/tracking.html'
            },
            {
                'name': 'UPS',
                'phone': '+1 (800) 742-5877',
                'url': 'https://www.ups.com/track',
                'upload_url': 'https://www.ups.com/upseforms'
            },
        ]
        return context


from django.shortcuts import redirect
from django.views import View
from django.contrib import messages
from django.db import transaction
from apps.locker.models import Parcel
from apps.accounts.models import SavedAddress
from .models import ShipmentItem


class CreateShipmentView(LoginRequiredMixin, View):
    """Create a new shipment from approved parcels."""
    template_name = 'shipments/create.html'
    
    def get(self, request):
        # Get user's approved parcels not already in a shipment
        locker = request.user.locker
        available_parcels = Parcel.objects.filter(
            locker=locker,
            status='approved'
        ).exclude(
            shipment_items__isnull=False
        ).prefetch_related('images')
        
        # Check if user has approved KYC
        has_kyc = request.user.kyc_documents.filter(status='approved').exists()
        
        # Get active shipping zones for country dropdown
        from apps.content.models import ShippingZone
        zones = ShippingZone.objects.filter(is_active=True).order_by('order')

        # Saved addresses for the "choose delivery address" picker (default first)
        saved_addresses = request.user.saved_addresses.order_by('-is_default', '-updated_at')
        default_address = saved_addresses.first()

        # Ship Selected on My Trunk sends the chosen parcel IDs — narrow the list
        # to just those (still scoped to this user's locker/approved queryset above).
        # Validate as UUIDs first so a malformed value doesn't 500 on id__in.
        preselected_ids = set()
        for raw_id in request.GET.getlist('parcels'):
            try:
                preselected_ids.add(str(uuid.UUID(raw_id)))
            except (ValueError, TypeError, AttributeError):
                continue
        if preselected_ids:
            available_parcels = available_parcels.filter(id__in=preselected_ids)

        # Attach billable weight to each parcel for the create-shipment
        # summary sidebar. Storage is billed per Trunk ID (Batch), not per
        # parcel, so there's no per-parcel "overdue days" figure anymore —
        # instead we show the locker's whole outstanding storage balance
        # once below, independent of which parcels happen to be checked.
        from apps.payments.services import _get_consolidation_fee_amount, _lookup_consolidation_fee_standard
        from apps.payments.views import _get_pending_batch_charges_for_locker
        from apps.content.services import build_zones_json
        from django.db.models import Sum
        from decimal import Decimal

        parcels = list(available_parcels)
        for parcel in parcels:
            parcel.billable_weight = parcel.billable_weight_kg or 0

        pending_charges = _get_pending_batch_charges_for_locker(locker)
        storage_fee_pending = pending_charges.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        storage_fee_days_pending = pending_charges.count()

        return render(request, self.template_name, {
            'parcels': parcels,
            'has_kyc': has_kyc,
            'zones': zones,
            'zones_json': build_zones_json(),
            'consolidation_fee_amount': _get_consolidation_fee_amount(locker),
            'consolidation_fee_standard': _lookup_consolidation_fee_standard(),
            'storage_fee_pending': storage_fee_pending,
            'storage_fee_days_pending': storage_fee_days_pending,
            'default_address': default_address,
            'saved_addresses': saved_addresses,
            'preselected_ids': preselected_ids,
        })
    
    def post(self, request):
        from django.core.exceptions import ValidationError
        from indiabox.validators import validate_file_upload, validate_address, validate_phone, validate_email

        # Get selected parcel IDs
        parcel_ids = request.POST.getlist('parcels')
        shipment_type = request.POST.get('shipment_type', 'international')

        if not parcel_ids:
            messages.error(request, 'Please select at least one parcel.')
            return redirect('shipments:create')

        if shipment_type not in dict(Shipment.TYPE_CHOICES):
            messages.error(request, 'Invalid shipment type.')
            return redirect('shipments:create')

        # Check for declaration file
        declaration_file = request.FILES.get('declaration_file')
        if not declaration_file:
            messages.error(request, 'Please upload the signed declaration form.')
            return redirect('shipments:create')

        # Validate declaration file
        try:
            validate_file_upload(declaration_file)
        except ValidationError as e:
            messages.error(request, str(e))
            return redirect('shipments:create')

        # Validate address + contact fields against a strict schema
        try:
            address_data = validate_address({
                'recipient_name': request.POST.get('recipient_name', ''),
                'address_line1': request.POST.get('address_line1', ''),
                'address_line2': request.POST.get('address_line2', ''),
                'city': request.POST.get('city', ''),
                'state': request.POST.get('state', ''),
                'postal_code': request.POST.get('postal_code', ''),
                'country': request.POST.get('country', ''),
            })
            recipient_phone = request.POST.get('recipient_phone', '')
            if recipient_phone:
                validate_phone(recipient_phone)
            recipient_email = request.POST.get('recipient_email', '')
            if recipient_email:
                validate_email(recipient_email)
        except ValidationError as e:
            messages.error(request, str(e))
            return redirect('shipments:create')

        # Validate parcels belong to user
        locker = request.user.locker
        parcels = list(Parcel.objects.filter(
            id__in=parcel_ids,
            locker=locker,
            status='approved'
        ))

        if len(parcels) != len(parcel_ids):
            messages.error(request, 'Invalid parcel selection.')
            return redirect('shipments:create')
        
        # Locked in now, at creation, so it doesn't drift if the admin later
        # edits the 'Consolidation' ServiceCharge amount (e.g. after payment).
        # Applies to every shipment regardless of parcel count, not just 2+.
        from apps.payments.services import _get_consolidation_fee_amount, _lookup_consolidation_fee_standard
        consolidation_fee = _get_consolidation_fee_amount(locker)
        consolidation_fee_standard = _lookup_consolidation_fee_standard()

        with transaction.atomic():
            # Create shipment with declaration_pending status
            shipment = Shipment.objects.create(
                user=request.user,
                shipment_type=shipment_type,
                status='declaration_pending',
                recipient_name=address_data.get('recipient_name', ''),
                address_line1=address_data.get('address_line1', ''),
                address_line2=address_data.get('address_line2', ''),
                city=address_data.get('city', ''),
                state=address_data.get('state', ''),
                postal_code=address_data.get('postal_code', ''),
                country=address_data.get('country', ''),
                recipient_phone=recipient_phone,
                recipient_email=recipient_email,
                consolidation_fee=consolidation_fee,
                consolidation_fee_standard=consolidation_fee_standard,
            )

            # Optionally save as default address for quick reuse
            if request.POST.get('save_address') == 'on':
                default_saved = request.user.saved_addresses.filter(is_default=True).first()
                address_payload = {
                    'label': request.POST.get('address_label', '').strip(),
                    'recipient_name': address_data.get('recipient_name', ''),
                    'recipient_phone': request.POST.get('recipient_phone', ''),
                    'recipient_email': request.POST.get('recipient_email', ''),
                    'address_line1': address_data.get('address_line1', ''),
                    'address_line2': request.POST.get('address_line2', ''),
                    'city': address_data.get('city', ''),
                    'state': request.POST.get('state', ''),
                    'postal_code': address_data.get('postal_code', ''),
                    'country': address_data.get('country', ''),
                    'is_default': True,
                }

                if default_saved:
                    for field, value in address_payload.items():
                        setattr(default_saved, field, value)
                    default_saved.save()
                else:
                    SavedAddress.objects.create(user=request.user, **address_payload)
            
            # Upload declaration file to Supabase Storage
            from apps.locker.utils import upload_shipment_document, get_user_locker_id
            try:
                # Reset file pointer to beginning
                declaration_file.seek(0)
                locker_id = get_user_locker_id(request.user)
                declaration_path = upload_shipment_document(
                    declaration_file,
                    locker_id,
                    shipment.display_id, 
                    'declaration'
                )
                # Create document record with the file path
                ShipmentDocument.objects.create(
                    shipment=shipment,
                    document_type='customs',
                    document_url=declaration_path  # This is the file path in storage
                )
            except Exception as e:
                logger.error(f'Declaration upload failed for shipment {shipment.pk}: {e}')
                messages.warning(request, 'Declaration upload failed. Shipment created without document.')
            
            # Add parcels to shipment
            ShipmentItem.objects.bulk_create(
                [ShipmentItem(shipment=shipment, parcel=parcel) for parcel in parcels]
            )
            # Individual .save() calls, not a bulk .update() — Django never
            # sends pre_save/post_save signals for QuerySet.update(), and
            # apps/locker/signals.py relies on those to decrement/close the
            # locker's batch when a parcel leaves the warehouse.
            for parcel in parcels:
                parcel.status = 'shipped'
                parcel.save(update_fields=['status', 'updated_at'])
        
        messages.success(request, 'Shipment created! Your declaration is pending approval.')
        return redirect('shipments:detail', pk=shipment.pk)


from django.views.generic.detail import SingleObjectMixin
from indiabox.mixins import UserOwnershipMixin


def _is_ajax(request):
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest'


class SelectShippingServiceView(LoginRequiredMixin, UserOwnershipMixin, SingleObjectMixin, View):
    """Customer selects Express/Standard/Economy for a priced shipment.
    Recalculates and locks in shipping_cost + service_type. Responds with
    JSON for the AJAX picker on the detail page (no full-page reload);
    falls back to a redirect+message for any non-AJAX caller."""
    model = Shipment

    def _fail(self, request, shipment, message):
        if _is_ajax(request):
            from django.http import JsonResponse
            return JsonResponse({'status': 'error', 'message': message}, status=400)
        messages.error(request, message)
        return redirect('shipments:detail', pk=shipment.pk)

    def _succeed(self, request, shipment, message):
        if _is_ajax(request):
            from django.http import JsonResponse
            data = {k: str(v) for k, v in _payment_summary(shipment).items()}
            data.update({
                'status': 'success',
                'message': message,
                'service_type': shipment.service_type,
                'service_type_display': shipment.get_service_type_display(),
                'payment_status_display': shipment.get_payment_status_display(),
                'currency': shipment.currency,
            })
            return JsonResponse(data)
        messages.success(request, message)
        return redirect('shipments:detail', pk=shipment.pk)

    def post(self, request, *args, **kwargs):
        shipment = self.get_object()

        service_type = request.POST.get('service_type')
        valid_types = dict(Shipment.SERVICE_TYPE_CHOICES)
        if service_type not in valid_types:
            return self._fail(request, shipment, 'Please choose a valid shipping service.')

        if shipment.total_weight_kg is None:
            return self._fail(request, shipment, 'Weight has not been finalized for this shipment yet.')

        if _is_service_locked(shipment):
            logger.warning(
                f"Service tier change rejected (locked): {request.user.email} "
                f"shipment {shipment.pk} status={shipment.status} payment_status={shipment.payment_status}"
            )
            return self._fail(request, shipment, "This shipment's service tier can no longer be changed.")

        from decimal import Decimal, ROUND_HALF_UP

        zone = _match_shipping_zone(shipment)
        rate = None
        if zone:
            rate = zone.rates.filter(
                is_active=True,
                service_type=service_type,
                min_weight__lte=shipment.total_weight_kg,
                max_weight__gt=shipment.total_weight_kg,
            ).first()

        if not rate:
            logger.warning(
                f"No matching ShippingRate: user={request.user.email} shipment={shipment.pk} "
                f"country={shipment.country} service_type={service_type} weight={shipment.total_weight_kg}"
            )
            return self._fail(
                request, shipment,
                'No rate is available for the selected service and your shipment\'s '
                'weight/destination. Please contact support.'
            )

        price = Decimal(str(rate.calculate_price(shipment.total_weight_kg))).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
        locker = getattr(request.user, 'locker', None)
        final_price, _discount = (
            locker.apply_shipping_discount(price) if locker else (price, Decimal('0.00'))
        )
        shipment.service_type = service_type
        shipment.shipping_cost = final_price
        shipment.shipping_cost_standard = price
        shipment.save(update_fields=['service_type', 'shipping_cost', 'shipping_cost_standard', 'updated_at'])

        logger.info(
            f"Service tier selected: user={request.user.email} shipment={shipment.pk} "
            f"service_type={service_type} shipping_cost={final_price} discount={_discount}"
        )
        return self._succeed(request, shipment, f'{valid_types[service_type]} service selected. Total updated.')
