import logging
from decimal import Decimal, InvalidOperation

from django.shortcuts import render, redirect, get_object_or_404
from django.views import View
from django.views.generic import ListView, DetailView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.utils import timezone
from django.db import transaction
from django.db.models import Count, Q
from django.core.paginator import Paginator

from .models import Parcel, ParcelImage, ReturnRequest, DiscardRequest

logger = logging.getLogger('security')


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


class MyTrunkView(LoginRequiredMixin, View):
    """Unified trunk view: action-required + ready-to-ship + returns + discards in one grid."""
    template_name = 'locker/my_trunk.html'

    def get(self, request):
        locker = request.user.locker

        # Storage is billed per Trunk ID (Batch), not per parcel — every
        # parcel in this locker shares the same "days left" figure, so
        # compute it once from the locker's one open batch instead of a
        # removed per-parcel property.
        from .services.batch_billing import get_open_batch
        open_batch = get_open_batch(locker)
        if open_batch and open_batch.free_storage_end_date:
            locker_days_left = max(0, (open_batch.free_storage_end_date - timezone.localdate()).days)
        else:
            locker_days_left = 0

        items = []
        for p in Parcel.objects.filter(locker=locker, status='action_required').prefetch_related('images'):
            items.append({
                'parcel': p, 'kind': 'action_required', 'status_label': 'Action Required',
                'status_class': 'status-action', 'title': p.item_name or 'Unnamed Item',
                'weight_kg': p.weight_kg, 'display_id': p.display_id, 'date': p.received_at,
                'image': p.images.first(), 'pk': p.pk, 'days_left': locker_days_left,
            })
        for p in Parcel.objects.filter(locker=locker, status='approved').prefetch_related('images'):
            items.append({
                'parcel': p, 'kind': 'ready_to_ship', 'status_label': 'Ready to Ship',
                'status_class': 'status-approved', 'title': p.item_name or 'Unnamed Item',
                'weight_kg': p.weight_kg, 'display_id': p.display_id, 'date': p.received_at,
                'image': p.images.first(), 'pk': p.pk, 'days_left': locker_days_left,
            })
        return_status_class = {
            'completed': 'status-delivered', 'rejected': 'status-action',
        }
        for r in ReturnRequest.objects.filter(parcel__locker=locker).exclude(status='completed').select_related('parcel').prefetch_related('parcel__images'):
            items.append({
                'parcel': r.parcel, 'kind': 'returns', 'status_label': r.get_status_display(),
                'status_class': return_status_class.get(r.status, 'status-pending'),
                'title': r.parcel.item_name or r.parcel.tracking_number,
                'weight_kg': r.parcel.weight_kg, 'display_id': r.parcel.display_id, 'date': r.requested_at,
                'image': r.parcel.images.first(), 'pk': r.parcel.pk, 'days_left': locker_days_left,
            })
        discard_status_class = {'discarded': 'status-delivered'}
        for d in DiscardRequest.objects.filter(parcel__locker=locker).exclude(status='discarded').select_related('parcel').prefetch_related('parcel__images'):
            items.append({
                'parcel': d.parcel, 'kind': 'discards', 'status_label': d.get_status_display(),
                'status_class': discard_status_class.get(d.status, 'status-action'),
                'title': d.parcel.item_name or d.parcel.tracking_number,
                'weight_kg': d.parcel.weight_kg, 'display_id': d.parcel.display_id, 'date': d.requested_at,
                'image': d.parcel.images.first(), 'pk': d.parcel.pk, 'days_left': locker_days_left,
            })

        items.sort(key=lambda i: i['date'] or timezone.now(), reverse=True)

        active_tab = request.GET.get('tab', 'ready_to_ship')
        if active_tab not in ('action_required', 'ready_to_ship', 'returns', 'discards'):
            active_tab = 'ready_to_ship'
        items = [i for i in items if i['kind'] == active_tab]

        paginator = Paginator(items, 20)
        page_obj = paginator.get_page(request.GET.get('page'))

        context = {
            'page_obj': page_obj,
            'items': page_obj.object_list,
            'active_tab': active_tab,
            'locker': locker,
            'has_kyc': request.user.kyc_documents.filter(status='approved').exists(),
        }
        context.update(_get_locker_tab_counts(locker))
        return render(request, self.template_name, context)


class ParcelDetailView(LoginRequiredMixin, DetailView):
    """Parcel detail with approval form."""
    template_name = 'locker/parcel_detail.html'
    context_object_name = 'parcel'
    
    def get_queryset(self):
        return Parcel.objects.filter(
            locker=self.request.user.locker
        ).prefetch_related('images', 'shipment_items__shipment')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        images = list(self.object.images.all())
        context['primary_image'] = images[0] if images else None
        context['earliest_image'] = images[-1] if images else None

        shipment_items = list(self.object.shipment_items.all())
        context['shipment_item'] = shipment_items[0] if shipment_items else None

        # Storage is billed per Trunk ID (Batch) now, not per parcel — this
        # parcel's "storage left" comes from its locker's one open batch.
        from .services.batch_billing import get_open_batch
        batch = get_open_batch(self.object.locker)
        if batch and batch.free_storage_end_date:
            context['storage_days_remaining'] = max(0, (batch.free_storage_end_date - timezone.localdate()).days)
            context['storage_is_overdue'] = context['storage_days_remaining'] <= 0
        else:
            context['storage_days_remaining'] = 0
            context['storage_is_overdue'] = batch is not None and batch.batch_status == 'active_chargeable'

        return context


class ApproveParcelView(LoginRequiredMixin, View):
    """Handle parcel approval."""
    
    def post(self, request, pk):
        with transaction.atomic():
            parcel = get_object_or_404(
                Parcel.objects.select_for_update(), pk=pk, locker=request.user.locker
            )

            if parcel.status != 'action_required':
                messages.error(request, 'This parcel cannot be approved.')
                return redirect('locker:parcel_detail', pk=pk)

            # Update parcel with user's declaration
            parcel.item_name = request.POST.get('item_name', parcel.item_name)
            raw_item_price = request.POST.get('item_price')
            if raw_item_price:
                try:
                    parcel.item_price = Decimal(raw_item_price)
                except InvalidOperation:
                    messages.error(request, 'Invalid item price.')
                    return redirect('locker:parcel_detail', pk=pk)
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
                        parcel_display_id=parcel.display_id,
                        is_personal_shop=parcel.personal_shop_request.exists(),
                    )
                    parcel.invoice_url = invoice_url
                except Exception as e:
                    logger.error(f'Invoice upload failed for parcel {parcel.pk}: {e}')
                    messages.warning(request, 'Invoice upload failed. Parcel still approved.')
            
            parcel.status = 'approved'
            parcel.approved_at = timezone.now()
            parcel.save()
        
        messages.success(request, f'Parcel {parcel.tracking_number} approved and ready to ship!')
        return redirect('locker:ready_to_ship')


class RequestReturnView(LoginRequiredMixin, View):
    """Request return for a parcel."""
    
    def post(self, request, pk):
        reason = request.POST.get('reason', '')

        with transaction.atomic():
            parcel = get_object_or_404(
                Parcel.objects.select_for_update(), pk=pk, locker=request.user.locker
            )

            if parcel.status not in ['action_required', 'approved']:
                messages.error(request, 'Return cannot be requested for this parcel.')
                return redirect('locker:parcel_detail', pk=pk)

            ReturnRequest.objects.create(parcel=parcel, reason=reason)
            parcel.status = 'return_requested'
            parcel.save()

        messages.success(request, 'Return request submitted.')
        return redirect('locker:returns')


class RequestDiscardView(LoginRequiredMixin, View):
    """Request discard for a parcel."""
    
    def post(self, request, pk):
        reason = request.POST.get('reason', '')

        with transaction.atomic():
            parcel = get_object_or_404(
                Parcel.objects.select_for_update(), pk=pk, locker=request.user.locker
            )

            if parcel.status not in ['action_required', 'approved']:
                messages.error(request, 'Discard cannot be requested for this parcel.')
                return redirect('locker:parcel_detail', pk=pk)

            DiscardRequest.objects.create(parcel=parcel, reason=reason)
            parcel.status = 'discard_requested'
            parcel.save()

        messages.warning(request, 'Discard request submitted. This action is irreversible once confirmed.')
        return redirect('locker:discards')
