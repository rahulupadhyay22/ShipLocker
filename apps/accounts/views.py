from django.shortcuts import render, redirect
from django.views import View
from django.views.generic import TemplateView
from django.contrib.auth import login, logout
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.http import JsonResponse
from django.db.models import Count, Q, Sum
import logging

from .models import User, Locker
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
            # Get the OAuth URL from Supabase
            redirect_url = request.build_absolute_uri('/accounts/dashboard/')
            oauth_url = auth.sign_in_with_google(redirect_url)
            return redirect(oauth_url)
        except Exception as e:
            logger.error(f'Google login failed: {e}')
            messages.error(request, 'Google login is temporarily unavailable. Please try email login.')
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
        return redirect('accounts:login')
    
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
        incoming_count = Parcel.objects.filter(locker=locker, status='pending').count()

        weights = [float(p.weight_kg) for p in trunk_parcels if p.weight_kg]
        total_weight_kg = sum(weights)
        avg_weight_per_item = round(total_weight_kg / parcels_in_trunk, 2) if parcels_in_trunk else 0

        storage_days_left = min((p.days_remaining_free for p in trunk_parcels), default=30)
        avg_storage_days = round(
            sum(p.storage_days for p in trunk_parcels) / parcels_in_trunk, 1
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
        return render(request, self.template_name, {
            'user': request.user,
            'locker': request.user.locker,
        })
    
    def post(self, request):
        from indiabox.validators import validate_phone, sanitize_text_input
        from django.core.exceptions import ValidationError
        
        user = request.user
        user.full_name = sanitize_text_input(request.POST.get('full_name', user.full_name), max_length=255)
        
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
