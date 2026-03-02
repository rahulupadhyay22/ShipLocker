from django.shortcuts import render, redirect, get_object_or_404
from django.views import View
from django.views.generic import ListView, DetailView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.utils import timezone
from django.db import transaction

from .models import Parcel, ParcelImage, ReturnRequest, DiscardRequest


class MyLockerView(LoginRequiredMixin, View):
    """Main My Locker page - redirects to appropriate tab."""
    
    def get(self, request):
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
        locker = self.request.user.locker
        context['tab'] = 'action_required'
        context['ready_count'] = Parcel.objects.filter(locker=locker, status='approved').count()
        context['return_count'] = ReturnRequest.objects.filter(parcel__locker=locker).exclude(status='completed').count()
        context['discard_count'] = DiscardRequest.objects.filter(parcel__locker=locker).exclude(status='discarded').count()
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
        locker = self.request.user.locker
        context['tab'] = 'ready_to_ship'
        context['action_count'] = Parcel.objects.filter(locker=locker, status='action_required').count()
        context['return_count'] = ReturnRequest.objects.filter(parcel__locker=locker).exclude(status='completed').count()
        context['discard_count'] = DiscardRequest.objects.filter(parcel__locker=locker).exclude(status='discarded').count()
        
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
        locker = self.request.user.locker
        context['tab'] = 'returns'
        context['action_count'] = Parcel.objects.filter(locker=locker, status='action_required').count()
        context['ready_count'] = Parcel.objects.filter(locker=locker, status='approved').count()
        context['discard_count'] = DiscardRequest.objects.filter(parcel__locker=locker).exclude(status='discarded').count()
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
        locker = self.request.user.locker
        context['tab'] = 'discards'
        context['action_count'] = Parcel.objects.filter(locker=locker, status='action_required').count()
        context['ready_count'] = Parcel.objects.filter(locker=locker, status='approved').count()
        context['return_count'] = ReturnRequest.objects.filter(parcel__locker=locker).exclude(status='completed').count()
        return context


class ParcelDetailView(LoginRequiredMixin, DetailView):
    """Parcel detail with approval form."""
    template_name = 'locker/parcel_detail.html'
    context_object_name = 'parcel'
    
    def get_queryset(self):
        return Parcel.objects.filter(
            locker=self.request.user.locker
        ).prefetch_related('images')


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
