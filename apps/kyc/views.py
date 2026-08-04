from django.shortcuts import render, redirect, get_object_or_404
from django.views import View
from django.views.generic import ListView, DetailView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages

from apps.accounts.models import KYCDocument
from apps.locker.utils import upload_kyc_document, get_signed_kyc_url, get_user_locker_id


class KYCListView(LoginRequiredMixin, ListView):
    """List user's KYC documents."""
    template_name = 'kyc/list.html'
    context_object_name = 'documents'
    paginate_by = 20
    
    def get_queryset(self):
        return KYCDocument.objects.filter(user=self.request.user)


class KYCUploadView(LoginRequiredMixin, View):
    """Upload KYC documents."""
    template_name = 'kyc/upload.html'
    
    def get(self, request):
        return render(request, self.template_name)
    
    def post(self, request):
        from django.core.exceptions import ValidationError
        from indiabox.validators import validate_file_upload, sanitize_filename
        import logging
        
        security_logger = logging.getLogger('security')
        
        doc_type = request.POST.get('document_type')
        file = request.FILES.get('document')

        if not doc_type or not file:
            messages.error(request, 'Please select document type and file.')
            return render(request, self.template_name)

        if doc_type not in dict(KYCDocument.DOCUMENT_TYPES):
            messages.error(request, 'Invalid document type.')
            return render(request, self.template_name)

        # Validate file using security validators
        try:
            validate_file_upload(file)
        except ValidationError as e:
            messages.error(request, str(e))
            security_logger.warning(
                f"KYC upload rejected: {request.user.email} - {e}"
            )
            return render(request, self.template_name)
        
        # Sanitize filename
        safe_filename = sanitize_filename(file.name)
        
        try:
            # Upload to Supabase Storage (organized by locker_id)
            locker_id = get_user_locker_id(request.user)
            file_path = upload_kyc_document(
                file=file,
                locker_id=locker_id,
                doc_type=doc_type
            )
            
            # Create KYC document record
            KYCDocument.objects.create(
                user=request.user,
                document_type=doc_type,
                document_url=file_path,
                status='pending'
            )
            
            security_logger.info(
                f"KYC uploaded: {request.user.email} - {doc_type}"
            )
            messages.success(request, 'Document uploaded successfully! It will be reviewed shortly.')
            return redirect('kyc:list')
            
        except Exception as e:
            security_logger.error(
                f"KYC upload failed: {request.user.email} - {e}"
            )
            messages.error(request, 'Upload failed. Please try again in a moment.')
            return render(request, self.template_name)


class KYCDetailView(LoginRequiredMixin, DetailView):
    """View KYC document details."""
    template_name = 'kyc/detail.html'
    context_object_name = 'document'
    
    def get_queryset(self):
        return KYCDocument.objects.filter(user=self.request.user)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Get signed URL for document preview
        if self.object.document_url:
            context['signed_url'] = get_signed_kyc_url(self.object.document_url)
        return context
