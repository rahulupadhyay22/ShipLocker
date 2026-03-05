import logging

from django.shortcuts import render, redirect, get_object_or_404
from django.views import View
from django.views.generic import ListView, DetailView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.utils import timezone
from django.db import transaction
from django.db.models import Count, Q

from .models import Parcel, ParcelImage, ReturnRequest, DiscardRequest

logger = logging.getLogger('security')


def _sync_overdue_storage_fees(user):
    """Batch-optimized storage fee sync after 30 free days.

    Performance: reduces N×2 queries (per parcel) down to 3 fixed queries
    regardless of parcel count. Wrapped in try/except so page loads are
    never blocked by storage-fee calculation failures.
    """
    try:
        from apps.payments.models import StorageFee
        from apps.payments.services import _get_daily_storage_fee_amount
        from decimal import Decimal

        if not hasattr(user, 'locker'):
            return

        # Query 1: Get all eligible parcels in ONE query
        eligible_parcels = list(Parcel.objects.filter(
            locker=user.locker,
            status__in=[
                'pending', 'action_required', 'approved',
                'return_requested', 'return_approved', 'discard_requested',
            ],
        ).only('id', 'received_at', 'display_id'))

        if not eligible_parcels:
            return

        # Query 2: Get ALL existing pending fees in ONE query
        existing_fees = {
            sf.parcel_id: sf
            for sf in StorageFee.objects.filter(
                parcel__in=eligible_parcels, status='pending',
            ).select_related('parcel')
        }

        # IDs of parcels that already have ANY fee (pending, paid, or waived)
        parcels_with_any_fee = set(
            StorageFee.objects.filter(
                parcel__in=eligible_parcels,
            ).values_list('parcel_id', flat=True)
        )

        daily_fee = _get_daily_storage_fee_amount()
        now = timezone.now()
        to_create = []
        to_update = []

        for parcel in eligible_parcels:
            if not parcel.received_at:
                continue

            storage_days = max(0, (now - parcel.received_at).days)
            overdue_days = max(0, storage_days - 30)
            if overdue_days <= 0:
                continue

            total_fee = daily_fee * Decimal(overdue_days)
            existing = existing_fees.get(parcel.pk)

            if existing:
                # Update existing pending fee if values changed
                if existing.days_overdue != overdue_days or existing.fee_amount != total_fee:
                    existing.days_overdue = overdue_days
                    existing.fee_amount = total_fee
                    to_update.append(existing)
            elif parcel.pk not in parcels_with_any_fee:
                # Only create if no historical fee exists (paid/waived)
                to_create.append(StorageFee(
                    parcel=parcel,
                    fee_amount=total_fee,
                    days_overdue=overdue_days,
                    status='pending',
                ))

        # Query 3a: Bulk create new fees
        if to_create:
            StorageFee.objects.bulk_create(to_create)

        # Query 3b: Bulk update changed fees
        if to_update:
            StorageFee.objects.bulk_update(to_update, ['days_overdue', 'fee_amount'])

    except Exception as e:
        logger.error(f"Storage fee sync failed for user {getattr(user, 'email', 'unknown')}: {e}")
        # Fail silently — fees will sync on next page visit


def _get_locker_tab_counts(locker):
    """Get all locker tab counts in a single aggregate query.

    Returns dict with action_count, ready_count, return_count, discard_count.
    Replaces 4 separate count() queries with 1 aggregate call.
    """
    from django.db.models import Count, Q, Subquery, OuterRef

    parcel_counts = Parcel.objects.filter(locker=locker).aggregate(
        action_count=Count('id', filter=Q(status='action_required')),
        ready_count=Count('id', filter=Q(status='approved')),
    )

    # Return and discard counts come from different models, but are small queries
    parcel_counts['return_count'] = ReturnRequest.objects.filter(
        parcel__locker=locker
    ).exclude(status='completed').count()
    parcel_counts['discard_count'] = DiscardRequest.objects.filter(
        parcel__locker=locker
    ).exclude(status='discarded').count()

    return parcel_counts


class MyLockerView(LoginRequiredMixin, View):
    """Main My Locker page - redirects to appropriate tab."""
    
    def get(self, request):
        _sync_overdue_storage_fees(request.user)

        # Check if user has action required items first
        locker = request.user.locker
        action_count = Parcel.objects.filter(locker=locker, status='action_required').count()
        
        if action_count > 0:
            return redirect('locker:action_required')
        return redirect('locker:ready_to_ship')


class ActionRequiredView(LoginRequiredMixin, ListView):
    """Items requiring user action/approval."""
    template_name = 'locker/action_required.html'
    context_object_name = 'parcels'
    paginate_by = 20
    
    def get_queryset(self):
        return Parcel.objects.filter(
            locker=self.request.user.locker,
            status='action_required'
        ).prefetch_related('images')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        _sync_overdue_storage_fees(self.request.user)
        locker = self.request.user.locker
        context['tab'] = 'action_required'
        context.update(_get_locker_tab_counts(locker))
        return context


class ReadyToShipView(LoginRequiredMixin, ListView):
    """Items approved and ready to ship."""
    template_name = 'locker/ready_to_ship.html'
    context_object_name = 'parcels'
    paginate_by = 20
    
    def get_queryset(self):
        return Parcel.objects.filter(
            locker=self.request.user.locker,
            status='approved'
        ).prefetch_related('images')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        _sync_overdue_storage_fees(self.request.user)
        locker = self.request.user.locker
        context['tab'] = 'ready_to_ship'
        context.update(_get_locker_tab_counts(locker))
        
        # KYC status
        context['has_kyc'] = self.request.user.kyc_documents.filter(status='approved').exists()
        return context


class ReturnsView(LoginRequiredMixin, ListView):
    """Return requests."""
    template_name = 'locker/returns.html'
    context_object_name = 'return_requests'
    paginate_by = 20
    
    def get_queryset(self):
        return ReturnRequest.objects.filter(
            parcel__locker=self.request.user.locker
        ).select_related('parcel')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        _sync_overdue_storage_fees(self.request.user)
        locker = self.request.user.locker
        context['tab'] = 'returns'
        context.update(_get_locker_tab_counts(locker))
        return context


class DiscardsView(LoginRequiredMixin, ListView):
    """Discard requests."""
    template_name = 'locker/discards.html'
    context_object_name = 'discard_requests'
    paginate_by = 20
    
    def get_queryset(self):
        return DiscardRequest.objects.filter(
            parcel__locker=self.request.user.locker
        ).select_related('parcel')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        _sync_overdue_storage_fees(self.request.user)
        locker = self.request.user.locker
        context['tab'] = 'discards'
        context.update(_get_locker_tab_counts(locker))
        return context


class ParcelDetailView(LoginRequiredMixin, DetailView):
    """Parcel detail with approval form."""
    template_name = 'locker/parcel_detail.html'
    context_object_name = 'parcel'
    
    def get_queryset(self):
        return Parcel.objects.filter(
            locker=self.request.user.locker
        ).prefetch_related('images')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        _sync_overdue_storage_fees(self.request.user)
        return context


class ApproveParcelView(LoginRequiredMixin, View):
    """Handle parcel approval."""
    
    def post(self, request, pk):
        parcel = get_object_or_404(Parcel, pk=pk, locker=request.user.locker)
        
        if parcel.status != 'action_required':
            messages.error(request, 'This parcel cannot be approved.')
            return redirect('locker:parcel_detail', pk=pk)
        
        with transaction.atomic():
            # Update parcel with user's declaration
            parcel.item_name = request.POST.get('item_name', parcel.item_name)
            parcel.item_price = request.POST.get('item_price') or parcel.item_price
            parcel.category = request.POST.get('category', parcel.category)
            parcel.customs_description = request.POST.get('customs_description', parcel.customs_description)
            
            # Handle invoice upload
            invoice_file = request.FILES.get('invoice')
            if invoice_file:
                # Validate file upload
                from indiabox.validators import validate_file_upload
                from django.core.exceptions import ValidationError
                try:
                    validate_file_upload(invoice_file)
                except ValidationError as e:
                    messages.error(request, str(e))
                    return redirect('locker:parcel_detail', pk=pk)
                
                try:
                    from .utils import upload_invoice
                    invoice_url = upload_invoice(
                        file=invoice_file,
                        locker_id=str(request.user.locker.locker_id),
                        parcel_display_id=parcel.display_id
                    )
                    parcel.invoice_url = invoice_url
                except Exception as e:
                    messages.warning(request, f'Invoice upload failed: {str(e)}. Parcel still approved.')
            
            parcel.status = 'approved'
            parcel.approved_at = timezone.now()
            parcel.save()
        
        messages.success(request, f'Parcel {parcel.tracking_number} approved and ready to ship!')
        return redirect('locker:ready_to_ship')


class RequestReturnView(LoginRequiredMixin, View):
    """Request return for a parcel."""
    
    def post(self, request, pk):
        parcel = get_object_or_404(Parcel, pk=pk, locker=request.user.locker)
        reason = request.POST.get('reason', '')
        
        if parcel.status not in ['action_required', 'approved']:
            messages.error(request, 'Return cannot be requested for this parcel.')
            return redirect('locker:parcel_detail', pk=pk)
        
        with transaction.atomic():
            ReturnRequest.objects.create(parcel=parcel, reason=reason)
            parcel.status = 'return_requested'
            parcel.save()
        
        messages.success(request, 'Return request submitted.')
        return redirect('locker:returns')


class RequestDiscardView(LoginRequiredMixin, View):
    """Request discard for a parcel."""
    
    def post(self, request, pk):
        parcel = get_object_or_404(Parcel, pk=pk, locker=request.user.locker)
        reason = request.POST.get('reason', '')
        
        if parcel.status not in ['action_required', 'approved']:
            messages.error(request, 'Discard cannot be requested for this parcel.')
            return redirect('locker:parcel_detail', pk=pk)
        
        with transaction.atomic():
            DiscardRequest.objects.create(parcel=parcel, reason=reason)
            parcel.status = 'discard_requested'
            parcel.save()
        
        messages.warning(request, 'Discard request submitted. This action is irreversible once confirmed.')
        return redirect('locker:discards')
