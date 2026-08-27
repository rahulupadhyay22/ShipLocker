"""Security middleware for IndiaBox."""

import logging
from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponseForbidden
from django.utils import timezone

security_logger = logging.getLogger('security')


class RateLimitMiddleware:
    """Rate limiting middleware, tiered by endpoint sensitivity.

    Thresholds come from settings.RATE_LIMIT_SETTINGS (env-configurable).
    'auth' routes are checked per-IP AND per-account, with exponential
    backoff lockouts on repeat abuse; 'public'/'authenticated' routes use a
    plain fixed window per-IP.
    """

    # path prefix -> category key in settings.RATE_LIMIT_SETTINGS
    PATH_CATEGORIES = {
        '/accounts/login/': 'auth',
        '/accounts/verify-otp/': 'auth',
        '/accounts/login/google/': 'auth',
        '/shipping-calculator/': 'public',
        '/kyc/upload/': 'authenticated',
        '/shipments/create/': 'authenticated',
        '/locker/parcel/': 'authenticated',
        '/personal-shop/new/': 'authenticated',
        '/personal-shop/requests/': 'authenticated',
        '/payments/verify/': 'authenticated',
        '/payments/webhook/razorpay/': 'public',
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == 'POST':
            for path, category in self.PATH_CATEGORIES.items():
                if request.path.startswith(path):
                    if not self._check(request, path, category):
                        security_logger.warning(
                            f"Rate limit exceeded: {self._get_client_ip(request)} on {path} ({category})"
                        )
                        return HttpResponseForbidden(
                            '<h1>Too Many Requests</h1>'
                            '<p>Please wait a few minutes before trying again.</p>'
                        )
                    break

        return self.get_response(request)

    def _get_client_ip(self, request):
        """Get client IP address."""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR', 'unknown')

    def _check(self, request, path, category):
        limits = settings.RATE_LIMIT_SETTINGS[category]
        ip = self._get_client_ip(request)

        keys = [f"rl:{category}:{path}:ip:{ip}"]
        if category == 'auth':
            account = (
                request.POST.get('email', '').strip().lower()
                or request.session.get('pending_email', '')
            )
            if account:
                keys.append(f"rl:{category}:{path}:acct:{account}")

        # any tripped key (IP or account) blocks the request
        for key in keys:
            if not self._check_one(key, limits, category):
                return False
        return True

    def _check_one(self, key, limits, category):
        """Fixed-window counter; 'auth' category adds exponential-backoff lockout."""
        lockout_key = f"{key}:lockout"
        if category == 'auth' and cache.get(lockout_key):
            return False

        attempts = cache.get(key, 0) + 1
        cache.set(key, attempts, limits['window'])
        if attempts <= limits['max_attempts']:
            return True

        if category == 'auth':
            violations_key = f"{key}:violations"
            violations = cache.get(violations_key, 0) + 1
            backoff_max = limits['backoff_max']
            cache.set(violations_key, violations, backoff_max)
            lockout = min(limits['backoff_base'] * (2 ** (violations - 1)), backoff_max)
            cache.set(lockout_key, True, lockout)
        return False


class LockerCacheMiddleware:
    """Primes request.user.locker in one query right after auth resolves.

    request.user.locker is read in most authenticated views (ownership
    mixins, dashboards, parcel/shipment/personal-shop views) — Django caches
    the reverse OneToOne after the first touch, but that first touch still
    means every one of those requests pays for the user fetch *and* a
    separate locker fetch. Priming it here keeps it at one extra query
    (same as today) instead of leaving it to whichever accessor happens to
    run first, and means the view code never needs to think about it.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if user.is_authenticated:
            from apps.accounts.models import Locker
            try:
                user.locker = Locker.objects.select_related('user').get(user=user)
            except Locker.DoesNotExist:
                pass
        return self.get_response(request)


class SecurityHeadersMiddleware:
    """Add additional security headers to responses."""
    
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        response = self.get_response(request)
        
        # Content Security Policy
        csp = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://checkout.razorpay.com https://cdn.razorpay.com https://fonts.googleapis.com https://cdnjs.cloudflare.com; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com; "
            "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com; "
            "img-src 'self' data: https: blob:; "
            "connect-src 'self' https://*.supabase.co https://api.razorpay.com https://lumberjack.razorpay.com; "
            "frame-src 'self' https://api.razorpay.com https://checkout.razorpay.com; "
            "frame-ancestors 'self';"
        )
        
        # Append 'unsafe-eval' for the admin panel to allow Alpine.js (used by Django Unfold) to run
        if request.path.startswith('/manage-rb-panel/'):
            csp = csp.replace("script-src 'self'", "script-src 'self' 'unsafe-eval'")
            
        response['Content-Security-Policy'] = csp
        
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
