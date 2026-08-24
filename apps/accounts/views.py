from django.shortcuts import render, redirect, get_object_or_404
from django.views import View
from django.views.generic import TemplateView, CreateView, UpdateView, DeleteView
from django.contrib.auth import login, logout
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.db.models import Count, Q, Sum
from django.urls import reverse
from django.template.loader import render_to_string
from django.utils import timezone
import logging

from indiabox.mixins import ObjectOwnershipRequiredMixin, SecureActionMixin
from .models import User, Locker, SavedAddress
from .forms import SavedAddressForm
from .services import SupabaseAuth

logger = logging.getLogger('security')


class LoginView(View):
    """Handle email OTP login."""
    template_name = 'accounts/login.html'
    
    def get(self, request):
        if request.user.is_authenticated:
            return redirect('accounts:dashboard')
        return render(request, self.template_name)
    
    def post(self, request):
        email = request.POST.get('email', '').strip().lower()
        
        if not email:
            messages.error(request, 'Please enter your email address.')
            return render(request, self.template_name)
        
        # Validate email format
        from indiabox.validators import validate_email
        from django.core.exceptions import ValidationError
        try:
            validate_email(email)
        except ValidationError as e:
            messages.error(request, str(e))
            return render(request, self.template_name)
        
        # Send OTP via Supabase
        try:
            auth = SupabaseAuth()
            auth.sign_in_with_otp(email)
            request.session['pending_email'] = email
            # H5: Generate a session-bound token for OTP verification
            import secrets
            otp_session_token = secrets.token_urlsafe(32)
            request.session['otp_session_token'] = otp_session_token
            messages.success(request, f'OTP sent to {email}. Please check your inbox.')
            return redirect('accounts:verify_otp')
        except Exception as e:
            logger.error(f'OTP send failed for {email}: {e}')
            messages.error(request, 'Failed to send OTP. Please try again in a moment.')
            return render(request, self.template_name)


class GoogleLoginView(View):
    """Handle Google OAuth login via Supabase."""
    
    def get(self, request):
        if request.user.is_authenticated:
            return redirect('accounts:dashboard')
        
        try:
            auth = SupabaseAuth()
            # Get the OAuth URL from Supabase — must point back at our callback,
            # not the dashboard directly, so we can exchange the code for a session.
            redirect_url = request.build_absolute_uri(reverse('accounts:google_callback'))
            oauth_url, code_verifier = auth.sign_in_with_google(redirect_url)
            # PKCE: the verifier has to survive until the callback request, so
            # it rides in the Django session (mirrors the OTP session token below).
            request.session['google_code_verifier'] = code_verifier
            return redirect(oauth_url)
        except Exception as e:
            logger.error(f'Google login failed: {e}')
            messages.error(request, 'Google login is temporarily unavailable. Please try email login.')
            return redirect('accounts:login')


class GoogleCallbackView(View):
    """Exchange the Supabase Google OAuth callback code for a Django session."""

    def get(self, request):
        error = request.GET.get('error_description') or request.GET.get('error')
        if error:
            logger.error(f'Google login failed: {error}')
            messages.error(request, 'Google login was cancelled or failed. Please try again.')
            return redirect('accounts:login')

        code = request.GET.get('code')
        code_verifier = request.session.pop('google_code_verifier', None)
        if not code or not code_verifier:
            messages.error(request, 'Google login session expired. Please try again.')
            return redirect('accounts:login')

        try:
            auth = SupabaseAuth()
            result = auth.exchange_code_for_session(code, code_verifier)

            email = result.user.email
            user, created = User.objects.get_or_create(
                email=email,
                defaults={'supabase_id': result.user.id}
            )
            if created:
                Locker.objects.create(user=user)
            if not user.supabase_id:
                user.supabase_id = result.user.id
                user.save()

            login(request, user)
            messages.success(request, 'Welcome back!' if not created else 'Account created successfully!')
            return redirect('accounts:dashboard')
        except Exception as e:
            logger.error(f'Google login callback failed: {e}')
            messages.error(request, 'Google login failed. Please try again or use email login.')
            return redirect('accounts:login')


class VerifyOTPView(View):
    """Verify OTP and create/login user."""
    template_name = 'accounts/verify_otp.html'
    
    def get(self, request):
        if 'pending_email' not in request.session:
            return redirect('accounts:login')
        return render(request, self.template_name, {
            'email': request.session.get('pending_email'),
            'otp_session_token': request.session.get('otp_session_token', ''),
        })
    
    def post(self, request):
        email = request.session.get('pending_email')
        otp = request.POST.get('otp', '').strip()
        submitted_token = request.POST.get('otp_session_token', '')
        stored_token = request.session.get('otp_session_token', '')
        
        if not email or not otp:
            messages.error(request, 'Invalid request.')
            return redirect('accounts:login')
        
        # H5: Verify the session token matches
        if not stored_token or submitted_token != stored_token:
            messages.error(request, 'Session expired. Please request a new OTP.')
            return redirect('accounts:login')
        
        try:
            auth = SupabaseAuth()
            result = auth.verify_otp(email, otp)
            
            # Get or create user
            user, created = User.objects.get_or_create(
                email=email,
                defaults={'supabase_id': result.user.id if result.user else None}
            )
            
            # Create locker if new user
            if created:
                Locker.objects.create(user=user)
            
            # Update supabase_id if not set
            if not user.supabase_id and result.user:
                user.supabase_id = result.user.id
                user.save()
            
            # Login user
            login(request, user)
            # Clean up session tokens
            request.session.pop('pending_email', None)
            request.session.pop('otp_session_token', None)
            
            messages.success(request, 'Welcome back!' if not created else 'Account created successfully!')
            return redirect('accounts:dashboard')
            
        except Exception as e:
            logger.error(f'OTP verification failed for {email}: {e}')
            messages.error(request, 'Invalid or expired OTP. Please try again.')
            return render(request, self.template_name, {
                'email': email,
                'otp_session_token': stored_token,
            })


class LogoutView(View):
    """Handle logout — POST only to prevent CSRF-based logout."""
    
    def post(self, request):
        logout(request)
        messages.success(request, 'You have been logged out.')
        return redirect('content:home')
    
    def get(self, request):
        # Redirect GET requests to dashboard (don't log out on GET)
        if request.user.is_authenticated:
            return redirect('accounts:dashboard')
        return redirect('accounts:login')


class DashboardView(LoginRequiredMixin, TemplateView):
    """Main user dashboard."""
    template_name = 'accounts/dashboard.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        
        # Get user's locker
        try:
            locker = user.locker
        except Locker.DoesNotExist:
            locker = Locker.objects.create(user=user)
        
        # Get announcements
        from apps.content.models import Announcement
        announcements = Announcement.objects.filter(is_active=True).order_by('-created_at')[:5]
        
        # Get parcel counts with single aggregate query (instead of 3 separate counts)
        from apps.locker.models import Parcel
        parcel_counts = Parcel.objects.filter(locker=locker).aggregate(
            action_required_count=Count('id', filter=Q(status='action_required')),
            ready_to_ship_count=Count('id', filter=Q(status='approved')),
        )
        action_required_count = parcel_counts['action_required_count']
        ready_to_ship_count = parcel_counts['ready_to_ship_count']
        
        # Urgent items queryset (only evaluated if template needs it)
        action_required_parcels = Parcel.objects.filter(
            locker=locker, 
            status='action_required'
        )
        
        # Get active shipments
        from apps.shipments.models import Shipment
        active_shipments = Shipment.objects.filter(
            user=user,
            status__in=['packing', 'dispatched', 'in_transit']
        ).count()

        # Parcels currently held in the trunk (inspected, not yet shipped)
        trunk_parcels = list(Parcel.objects.filter(
            locker=locker, status__in=['action_required', 'approved']
        ))
        parcels_in_trunk = len(trunk_parcels)
        from apps.personal_shop.models import PersonalShopRequest
        incoming_count = PersonalShopRequest.objects.filter(locker=locker, status='purchased').count()

        weights = [float(p.weight_kg) for p in trunk_parcels if p.weight_kg]
        total_weight_kg = sum(weights)
        avg_weight_per_item = round(total_weight_kg / parcels_in_trunk, 2) if parcels_in_trunk else 0

        # Storage is billed per Trunk ID (Batch), not per parcel — "days left"
        # comes from the locker's one open batch, not a per-parcel property.
        from apps.locker.services.batch_billing import get_open_batch
        open_batch = get_open_batch(locker)
        if open_batch and open_batch.free_storage_end_date:
            storage_days_left = max(0, (open_batch.free_storage_end_date - timezone.localdate()).days)
        else:
            storage_days_left = 0

        avg_storage_days = round(
            sum((timezone.now() - p.received_at).days for p in trunk_parcels if p.received_at) / parcels_in_trunk, 1
        ) if parcels_in_trunk else 0

        declared_value_total = sum(float(p.item_price) for p in trunk_parcels if p.item_price)

        est_shipping_cost = None
        try:
            from apps.content.models import ShippingZone
            zone = ShippingZone.objects.filter(is_active=True).order_by('order').first()
            if zone and total_weight_kg:
                rate = zone.rates.filter(
                    is_active=True, min_weight__lte=total_weight_kg, max_weight__gt=total_weight_kg
                ).first()
                if rate:
                    est_shipping_cost = rate.calculate_price(total_weight_kg)
        except Exception:
            est_shipping_cost = None

        recent_activity = Parcel.objects.filter(locker=locker).order_by('-updated_at')[:5]
        recent_shipments = Shipment.objects.filter(user=user).annotate(
            items_count=Count('items')
        ).order_by('-created_at')[:5]

        context.update({
            'locker': locker,
            'is_premium': locker.is_premium,
            'announcements': announcements,
            'action_required_count': action_required_count,
            'urgent_items': action_required_parcels,
            'ready_to_ship_count': ready_to_ship_count,
            'active_shipments': active_shipments,
            'parcels_in_trunk': parcels_in_trunk,
            'incoming_count': incoming_count,
            'total_weight_kg': round(total_weight_kg, 2),
            'storage_days_left': storage_days_left,
            'avg_weight_per_item': avg_weight_per_item,
            'avg_storage_days': avg_storage_days,
            'declared_value_total': round(declared_value_total, 2),
            'est_shipping_cost': est_shipping_cost,
            'recent_activity': recent_activity,
            'recent_shipments': recent_shipments,
            'trunk_capacity': 30,
        })
        return context


class ProfileView(LoginRequiredMixin, View):
    """User profile management."""
    template_name = 'accounts/profile.html'
    
    def get(self, request):
        from apps.notifications.models import AppSettings

        app_settings = AppSettings.get_settings()
        return render(request, self.template_name, {
            'user': request.user,
            'locker': request.user.locker,
            'is_premium': request.user.locker.is_premium,
            'support_email': app_settings.support_email,
            'support_phone': app_settings.support_phone,
            'kyc_verified': request.user.kyc_documents.filter(status='approved').exists(),
            'total_shipments': request.user.shipments.count(),
            'total_parcels': request.user.locker.parcels.count(),
            'saved_addresses': request.user.saved_addresses.all(),
            'payments': request.user.payments.all()[:10],
        })
    
    def post(self, request):
        from indiabox.validators import validate_phone, validate_text_input
        from django.core.exceptions import ValidationError

        user = request.user
        try:
            full_name = validate_text_input(
                request.POST.get('full_name', user.full_name),
                field_name='Full name', min_length=2, max_length=255,
            )
        except ValidationError as e:
            messages.error(request, str(e))
            return redirect('accounts:profile')
        user.full_name = full_name

        phone = request.POST.get('phone', user.phone)
        if phone:
            try:
                validate_phone(phone)
                user.phone = phone
            except ValidationError as e:
                messages.error(request, str(e))
                return redirect('accounts:profile')
        
        whatsapp = request.POST.get('whatsapp_number', user.whatsapp_number)
        if whatsapp:
            try:
                validate_phone(whatsapp)
                user.whatsapp_number = whatsapp
            except ValidationError as e:
                messages.error(request, str(e))
                return redirect('accounts:profile')
        
        user.save()

        messages.success(request, 'Profile updated successfully.')
        return redirect('accounts:profile')


class _XhrAddressFormMixin:
    """Shared XHR behavior for the address create/update forms: the profile
    page's addresses tab loads the form fragment inline (instead of
    navigating to a separate page) and submits it optimistically. Non-XHR
    requests (JS disabled, direct URL visit) fall through to the normal
    Django form page + redirect."""

    def _is_xhr(self):
        return self.request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    def _render_form_fragment(self, form):
        return render_to_string('accounts/_address_form_fields.html', {
            'form': form,
            'form_action': self.form_action_url(),
            'object': getattr(self, 'object', None),
        }, request=self.request)

    def form_valid(self, form):
        response = super().form_valid(form)
        if self._is_xhr():
            card_html = render_to_string(
                'accounts/_address_card.html', {'address': self.object}, request=self.request,
            )
            return JsonResponse({'success': True, 'pk': str(self.object.pk), 'card_html': card_html})
        return response

    def form_invalid(self, form):
        if self._is_xhr():
            return JsonResponse({'success': False, 'form_html': self._render_form_fragment(form)}, status=400)
        return super().form_invalid(form)


class SavedAddressCreateView(_XhrAddressFormMixin, LoginRequiredMixin, SecureActionMixin, CreateView):
    model = SavedAddress
    form_class = SavedAddressForm
    template_name = 'accounts/address_form.html'

    def form_action_url(self):
        return reverse('accounts:address_add')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form_action'] = self.form_action_url()
        return context

    def get(self, request, *args, **kwargs):
        self.object = None
        if self._is_xhr():
            return HttpResponse(self._render_form_fragment(self.get_form()))
        return super().get(request, *args, **kwargs)

    def form_valid(self, form):
        form.instance.user = self.request.user
        response = super().form_valid(form)
        if not self._is_xhr():
            messages.success(self.request, 'Address saved.')
        return response

    def get_success_url(self):
        return reverse('accounts:profile') + '?tab=addresses'


class SavedAddressUpdateView(_XhrAddressFormMixin, LoginRequiredMixin, ObjectOwnershipRequiredMixin, SecureActionMixin, UpdateView):
    model = SavedAddress
    form_class = SavedAddressForm
    template_name = 'accounts/address_form.html'

    def get_queryset(self):
        return SavedAddress.objects.filter(user=self.request.user)

    def form_action_url(self):
        return reverse('accounts:address_edit', kwargs={'pk': self.object.pk})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form_action'] = self.form_action_url()
        return context

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        if self._is_xhr():
            return HttpResponse(self._render_form_fragment(self.get_form()))
        return super().get(request, *args, **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        if not self._is_xhr():
            messages.success(self.request, 'Address updated.')
        return response

    def get_success_url(self):
        return reverse('accounts:profile') + '?tab=addresses'


class SavedAddressDeleteView(LoginRequiredMixin, ObjectOwnershipRequiredMixin, SecureActionMixin, DeleteView):
    model = SavedAddress
    template_name = 'accounts/address_confirm_delete.html'

    def get_queryset(self):
        return SavedAddress.objects.filter(user=self.request.user)

    def form_valid(self, form):
        messages.success(self.request, 'Address deleted.')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('accounts:profile') + '?tab=addresses'


class SavedAddressSetDefaultView(LoginRequiredMixin, SecureActionMixin, View):
    def post(self, request, pk):
        address = get_object_or_404(SavedAddress, pk=pk, user=request.user)
        address.is_default = True
        address.save()

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': True, 'pk': str(address.pk)})

        messages.success(request, 'Default address updated.')
        return redirect(reverse('accounts:profile') + '?tab=addresses')
