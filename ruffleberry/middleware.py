"""Security middleware for Ruffleberry."""

import logging
from django.core.cache import cache
from django.http import HttpResponseForbidden
from django.utils import timezone

security_logger = logging.getLogger('security')


class RateLimitMiddleware:
    """Rate limiting middleware for login and sensitive endpoints."""
    
    # Rate limit settings: (max_attempts, time_window_seconds)
    RATE_LIMITS = {
        '/accounts/login/': (10, 300),      # 10 attempts per 5 minutes
        '/accounts/verify-otp/': (5, 300),  # 5 attempts per 5 minutes
        '/accounts/login/google/': (10, 300),
        '/kyc/upload/': (10, 3600),         # 10 uploads per hour
        '/shipments/create/': (20, 3600),   # 20 shipments per hour
        '/locker/parcel/': (50, 3600),      # 50 parcel actions per hour
    }
    
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        # Check rate limit for POST requests on sensitive endpoints
        if request.method == 'POST':
            for path, (max_attempts, window) in self.RATE_LIMITS.items():
                if request.path.startswith(path):
                    if not self._check_rate_limit(request, path, max_attempts, window):
                        security_logger.warning(
                            f"Rate limit exceeded: {self._get_client_ip(request)} on {path}"
                        )
                        return HttpResponseForbidden(
                            '<h1>Too Many Requests</h1>'
                            '<p>Please wait a few minutes before trying again.</p>'
                        )
        
        response = self.get_response(request)
        return response
    
    def _get_client_ip(self, request):
        """Get client IP address."""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR', 'unknown')
    
    def _check_rate_limit(self, request, path, max_attempts, window):
        """Check if request is within rate limit."""
        ip = self._get_client_ip(request)
        cache_key = f"rate_limit:{path}:{ip}"
        
        attempts = cache.get(cache_key, 0)
        if attempts >= max_attempts:
            return False
        
        cache.set(cache_key, attempts + 1, window)
        return True


class SecurityHeadersMiddleware:
    """Add additional security headers to responses."""
    
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        response = self.get_response(request)
        
        # Content Security Policy
        response['Content-Security-Policy'] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://checkout.razorpay.com https://fonts.googleapis.com https://cdnjs.cloudflare.com; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com; "
            "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com; "
            "img-src 'self' data: https: blob:; "
            "connect-src 'self' https://*.supabase.co https://api.razorpay.com; "
            "frame-src https://api.razorpay.com https://checkout.razorpay.com; "
            "frame-ancestors 'none';"
        )
        
        # Permissions Policy
        response['Permissions-Policy'] = (
            "geolocation=(), microphone=(), camera=()"
        )
        
        return response


class LoginAttemptMiddleware:
    """Track and log login attempts."""
    
    MAX_FAILED_ATTEMPTS = 5
    LOCKOUT_DURATION = 900  # 15 minutes
    
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        response = self.get_response(request)
        
        # Log login attempts
        if request.path == '/accounts/login/' and request.method == 'POST':
            ip = self._get_client_ip(request)
            email = request.POST.get('email', 'unknown')
            
            if response.status_code == 302:  # Redirect = success (to OTP page)
                security_logger.info(f"Login attempt: {email} from {ip} - OTP sent")
                self._clear_failed_attempts(ip, email)
            else:
                security_logger.warning(f"Failed login: {email} from {ip}")
                self._record_failed_attempt(ip, email)
        
        # Log OTP verification
        if request.path == '/accounts/verify-otp/' and request.method == 'POST':
            ip = self._get_client_ip(request)
            
            if response.status_code == 302 and 'dashboard' in response.url:
                security_logger.info(f"Successful OTP verification from {ip}")
            else:
                security_logger.warning(f"Failed OTP verification from {ip}")
        
        return response
    
    def _get_client_ip(self, request):
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR', 'unknown')
    
    def _record_failed_attempt(self, ip, email):
        cache_key = f"failed_login:{ip}:{email}"
        attempts = cache.get(cache_key, 0)
        cache.set(cache_key, attempts + 1, self.LOCKOUT_DURATION)
        
        if attempts + 1 >= self.MAX_FAILED_ATTEMPTS:
            security_logger.critical(
                f"Account lockout triggered: {email} from {ip} - {attempts + 1} failed attempts"
            )
    
    def _clear_failed_attempts(self, ip, email):
        cache_key = f"failed_login:{ip}:{email}"
        cache.delete(cache_key)
