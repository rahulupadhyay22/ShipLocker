from django.shortcuts import render, redirect
from django.views import View
from django.views.generic import TemplateView
from django.contrib.auth import login, logout
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.http import JsonResponse

from .models import User, Locker
from .services import SupabaseAuth


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
        
        # Send OTP via Supabase
        try:
            auth = SupabaseAuth()
            auth.sign_in_with_otp(email)
            request.session['pending_email'] = email
            messages.success(request, f'OTP sent to {email}. Please check your inbox.')
            return redirect('accounts:verify_otp')
        except Exception as e:
            messages.error(request, f'Failed to send OTP: {str(e)}')
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
            messages.error(request, f'Google login failed: {str(e)}')
            return redirect('accounts:login')


class VerifyOTPView(View):
    """Verify OTP and create/login user."""
    template_name = 'accounts/verify_otp.html'
    
    def get(self, request):
        if 'pending_email' not in request.session:
            return redirect('accounts:login')
        return render(request, self.template_name, {
            'email': request.session.get('pending_email')
        })
    
    def post(self, request):
        email = request.session.get('pending_email')
        otp = request.POST.get('otp', '').strip()
        
        if not email or not otp:
            messages.error(request, 'Invalid request.')
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
            del request.session['pending_email']
            
            messages.success(request, 'Welcome back!' if not created else 'Account created successfully!')
            return redirect('accounts:dashboard')
            
        except Exception as e:
            messages.error(request, f'Invalid OTP: {str(e)}')
            return render(request, self.template_name, {'email': email})


class LogoutView(View):
    """Handle logout."""
    
    def get(self, request):
        logout(request)
        messages.success(request, 'You have been logged out.')
        return redirect('accounts:login')
    
    def post(self, request):
        return self.get(request)


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
        
        # Get parcel counts
        from apps.locker.models import Parcel
        action_required_parcels = Parcel.objects.filter(
            locker=locker, 
            status='action_required'
        )
        action_required_count = action_required_parcels.count()
        ready_to_ship_count = Parcel.objects.filter(
            locker=locker, 
            status='approved'
        ).count()
        
        # Get active shipments
        from apps.shipments.models import Shipment
        active_shipments = Shipment.objects.filter(
            user=user,
            status__in=['packing', 'dispatched', 'in_transit']
        ).count()
        
        context.update({
            'locker': locker,
            'announcements': announcements,
            'action_required_count': action_required_count,
            'urgent_items': action_required_parcels,
            'ready_to_ship_count': ready_to_ship_count,
            'active_shipments': active_shipments,
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
        user = request.user
        user.full_name = request.POST.get('full_name', user.full_name)
        user.phone = request.POST.get('phone', user.phone)
        user.whatsapp_number = request.POST.get('whatsapp_number', user.whatsapp_number)
        user.save()
        
        messages.success(request, 'Profile updated successfully.')
        return redirect('accounts:profile')
