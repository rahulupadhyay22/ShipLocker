from django.shortcuts import render, redirect, get_object_or_404
from django.views import View
from django.views.generic import ListView, DetailView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages

from apps.accounts.models import KYCDocument, ConsentRecord
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
        import logging
        from .forms import KYCUploadForm

        security_logger = logging.getLogger('security')

        # KYCUploadForm wraps the same indiabox.validators call (and the
        # same security-logging-on-file-rejection behavior) this view used
        # to make directly -- see apps/kyc/forms.py.
        form = KYCUploadForm(request.POST, request.FILES, user=request.user)
        if not form.is_valid():
            first_error = next(iter(form.errors.get_json_data().values()))[0]['message']
            messages.error(request, first_error)
            return render(request, self.template_name)

        doc_type = form.cleaned_data['document_type']
        file = form.cleaned_data['document']

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

            from apps.notifications.models import AppSettings
            from indiabox.mixins import SecureActionMixin
            ConsentRecord.objects.create(
                user=request.user,
                consent_type='kyc_upload',
                policy_version=AppSettings.get_settings().privacy_policy_version,
                ip_address=SecureActionMixin()._get_client_ip(request),
            )


            security_logger.info(
                f"KYC uploaded: {request.user.email} - {doc_type}"
            )
            messages.success(request, 'Document uploaded successfully! It will be reviewed shortly.')
            return redirect('kyc:list')
            
        except Exception:
            security_logger.exception(
                f"KYC upload failed: {request.user.email}"
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
