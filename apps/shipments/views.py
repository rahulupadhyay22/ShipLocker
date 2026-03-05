from django.shortcuts import render
from django.views.generic import ListView, DetailView, TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Q

from .models import Shipment, ShipmentItem, ShipmentDocument


class ShipmentStatsMixin:
    """Mixin to provide shipment status counts with single aggregate query."""
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        counts = Shipment.objects.filter(user=user).aggregate(
            active_count=Count('id', filter=Q(
                status__in=['packing', 'dispatched', 'in_transit', 'customs',
                            'out_for_delivery', 'declaration_pending', 'pending_payment']
            )),
            delivered_count=Count('id', filter=Q(status='delivered')),
            closed_count=Count('id', filter=Q(status__in=['returned', 'cancelled'])),
        )
        context.update(counts)
        return context


class ShipmentsListView(LoginRequiredMixin, ShipmentStatsMixin, ListView):
    """Main shipments page - defaults to Active."""
    template_name = 'shipments/list.html'
    context_object_name = 'shipments'
    paginate_by = 20
    
    def get_queryset(self):
        # Default to showing active shipments
        return Shipment.objects.filter(
            user=self.request.user,
            status__in=['packing', 'dispatched', 'in_transit', 'customs', 'out_for_delivery', 'declaration_pending', 'pending_payment']
        ).order_by('-created_at')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['tab'] = 'active'
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
        ).prefetch_related('items__parcel', 'documents')

    def get_context_data(self, **kwargs):
        from decimal import Decimal
        from django.db.models import Sum
        from apps.payments.models import StorageFee

        context = super().get_context_data(**kwargs)
        shipment = self.object

        parcel_ids = list(shipment.items.values_list('parcel_id', flat=True))

        if not parcel_ids:
            context['storage_fee_pending'] = Decimal('0.00')
            context['storage_fee_paid'] = Decimal('0.00')
            context['storage_fee_total'] = Decimal('0.00')
            return context

        pending_total = StorageFee.objects.filter(
            parcel_id__in=parcel_ids,
            status='pending',
        ).aggregate(total=Sum('fee_amount'))['total'] or Decimal('0.00')

        paid_total = StorageFee.objects.filter(
            parcel_id__in=parcel_ids,
            status='paid',
        ).aggregate(total=Sum('fee_amount'))['total'] or Decimal('0.00')

        shipping_amount = Decimal(str(shipment.shipping_cost or 0))
        storage_total = pending_total + paid_total

        context['storage_fee_pending'] = pending_total
        context['storage_fee_paid'] = paid_total
        context['storage_fee_total'] = storage_total
        context['shipping_amount'] = shipping_amount
        context['shipment_total_amount'] = shipping_amount + storage_total
        context['shipment_amount_due'] = pending_total + (shipping_amount if shipment.payment_status != 'paid' else Decimal('0.00'))
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
        )
        
        # Check if user has approved KYC
        has_kyc = request.user.kyc_documents.filter(status='approved').exists()
        
        # Get active shipping zones for country dropdown
        from apps.content.models import ShippingZone
        zones = ShippingZone.objects.filter(is_active=True).order_by('order')

        # Prefill form with user's default saved address (if any)
        default_address = request.user.saved_addresses.filter(is_default=True).first()
        if not default_address:
            default_address = request.user.saved_addresses.first()
        
        return render(request, self.template_name, {
            'parcels': available_parcels,
            'has_kyc': has_kyc,
            'zones': zones,
            'default_address': default_address,
        })
    
    def post(self, request):
        # Get selected parcel IDs
        parcel_ids = request.POST.getlist('parcels')
        shipment_type = request.POST.get('shipment_type', 'international')
        
        if not parcel_ids:
            messages.error(request, 'Please select at least one parcel.')
            return redirect('shipments:create')
        
        # Check for declaration file
        declaration_file = request.FILES.get('declaration_file')
        if not declaration_file:
            messages.error(request, 'Please upload the signed declaration form.')
            return redirect('shipments:create')
        
        # Validate declaration file
        from indiabox.validators import validate_file_upload
        from django.core.exceptions import ValidationError
        try:
            validate_file_upload(declaration_file)
        except ValidationError as e:
            messages.error(request, str(e))
            return redirect('shipments:create')
        
        # Validate address fields
        from indiabox.validators import validate_address
        try:
            address_data = validate_address({
                'recipient_name': request.POST.get('recipient_name', ''),
                'address_line1': request.POST.get('address_line1', ''),
                'city': request.POST.get('city', ''),
                'postal_code': request.POST.get('postal_code', ''),
                'country': request.POST.get('country', ''),
            })
        except ValidationError as e:
            messages.error(request, str(e))
            return redirect('shipments:create')
        
        # Validate parcels belong to user
        locker = request.user.locker
        parcels = Parcel.objects.filter(
            id__in=parcel_ids,
            locker=locker,
            status='approved'
        )
        
        if parcels.count() != len(parcel_ids):
            messages.error(request, 'Invalid parcel selection.')
            return redirect('shipments:create')
        
        with transaction.atomic():
            # Create shipment with declaration_pending status
            shipment = Shipment.objects.create(
                user=request.user,
                shipment_type=shipment_type,
                status='declaration_pending',
                recipient_name=address_data.get('recipient_name', ''),
                address_line1=address_data.get('address_line1', ''),
                address_line2=request.POST.get('address_line2', ''),
                city=address_data.get('city', ''),
                state=request.POST.get('state', ''),
                postal_code=address_data.get('postal_code', ''),
                country=address_data.get('country', ''),
                recipient_phone=request.POST.get('recipient_phone', ''),
                recipient_email=request.POST.get('recipient_email', ''),
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
                messages.warning(request, f'Declaration upload failed: {str(e)}. Shipment created without document.')
            
            # Add parcels to shipment
            for parcel in parcels:
                ShipmentItem.objects.create(shipment=shipment, parcel=parcel)
                parcel.status = 'shipped'
                parcel.save()
        
        messages.success(request, 'Shipment created! Your declaration is pending approval.')
        return redirect('shipments:detail', pk=shipment.pk)
