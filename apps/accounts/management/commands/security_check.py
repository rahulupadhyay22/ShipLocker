"""Management command to check security configuration."""

from django.core.management.base import BaseCommand
from django.conf import settings
import os


class Command(BaseCommand):
    help = 'Check security configuration of the application'

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING('\n🔐 INDIABOX SECURITY AUDIT\n'))
        self.stdout.write('=' * 50)
        
        issues = []
        
        # 1. SECRET_KEY Check
        self.stdout.write('\n1. SECRET_KEY')
        if settings.SECRET_KEY == 'django-insecure-dev-key':
            self.stdout.write(self.style.ERROR('   ❌ Using default insecure key!'))
            issues.append('SECRET_KEY is using default value')
        elif len(settings.SECRET_KEY) < 50:
            self.stdout.write(self.style.WARNING('   ⚠️  SECRET_KEY might be too short'))
            issues.append('SECRET_KEY might be too short')
        else:
            self.stdout.write(self.style.SUCCESS('   ✅ SECRET_KEY is properly set'))
        
        # 2. DEBUG Check
        self.stdout.write('\n2. DEBUG Mode')
        if settings.DEBUG:
            self.stdout.write(self.style.WARNING('   ⚠️  DEBUG=True (OK for development)'))
        else:
            self.stdout.write(self.style.SUCCESS('   ✅ DEBUG=False (Production mode)'))
        
        # 3. ALLOWED_HOSTS Check
        self.stdout.write('\n3. ALLOWED_HOSTS')
        if not settings.ALLOWED_HOSTS:
            self.stdout.write(self.style.ERROR('   ❌ ALLOWED_HOSTS is empty!'))
            issues.append('ALLOWED_HOSTS is empty')
        elif '*' in settings.ALLOWED_HOSTS:
            self.stdout.write(self.style.ERROR('   ❌ ALLOWED_HOSTS contains wildcard!'))
            issues.append('ALLOWED_HOSTS contains wildcard')
        else:
            self.stdout.write(self.style.SUCCESS(f'   ✅ ALLOWED_HOSTS: {settings.ALLOWED_HOSTS}'))
        
        # 4. Database SSL
        self.stdout.write('\n4. Database Connection')
        db_config = settings.DATABASES.get('default', {})
        db_engine = db_config.get('ENGINE', '')
        if 'postgresql' in db_engine:
            ssl_mode = db_config.get('OPTIONS', {}).get('sslmode', '')
            if ssl_mode == 'require':
                self.stdout.write(self.style.SUCCESS('   ✅ PostgreSQL with SSL required'))
            elif not settings.DEBUG:
                self.stdout.write(self.style.WARNING('   ⚠️  SSL not enforced in production'))
                issues.append('Database SSL not enforced')
            else:
                self.stdout.write(self.style.SUCCESS('   ✅ PostgreSQL (SSL not required in dev)'))
        elif 'sqlite' in db_engine:
            self.stdout.write(self.style.WARNING('   ⚠️  Using SQLite (development only)'))
        else:
            self.stdout.write(self.style.SUCCESS(f'   ✅ Database: {db_engine}'))
        
        # 5. Security Middleware
        self.stdout.write('\n5. Security Middleware')
        middlewares = settings.MIDDLEWARE
        security_checks = {
            'django.middleware.security.SecurityMiddleware': 'Security',
            'django.middleware.csrf.CsrfViewMiddleware': 'CSRF',
            'django.middleware.clickjacking.XFrameOptionsMiddleware': 'Clickjacking',
            'indiabox.middleware.RateLimitMiddleware': 'Rate Limiting',
            'indiabox.middleware.SecurityHeadersMiddleware': 'Security Headers',
            'indiabox.middleware.LoginAttemptMiddleware': 'Login Tracking',
        }
        for middleware, name in security_checks.items():
            if middleware in middlewares:
                self.stdout.write(self.style.SUCCESS(f'   ✅ {name} middleware enabled'))
            else:
                self.stdout.write(self.style.ERROR(f'   ❌ {name} middleware missing'))
                issues.append(f'{name} middleware missing')
        
        # 6. Production Security Settings
        self.stdout.write('\n6. Production Security Settings')
        if not settings.DEBUG:
            prod_settings = {
                'SECURE_SSL_REDIRECT': getattr(settings, 'SECURE_SSL_REDIRECT', False),
                'SESSION_COOKIE_SECURE': getattr(settings, 'SESSION_COOKIE_SECURE', False),
                'CSRF_COOKIE_SECURE': getattr(settings, 'CSRF_COOKIE_SECURE', False),
                'SECURE_HSTS_SECONDS': getattr(settings, 'SECURE_HSTS_SECONDS', 0),
                'X_FRAME_OPTIONS': getattr(settings, 'X_FRAME_OPTIONS', ''),
            }
            for setting, value in prod_settings.items():
                if value:
                    self.stdout.write(self.style.SUCCESS(f'   ✅ {setting}: {value}'))
                else:
                    self.stdout.write(self.style.ERROR(f'   ❌ {setting} not set'))
                    issues.append(f'{setting} not set in production')
        else:
            self.stdout.write(self.style.WARNING('   ⚠️  Skipped (DEBUG=True)'))
        
        # 7. Session Security
        self.stdout.write('\n7. Session Security')
        self.stdout.write(self.style.SUCCESS(f'   ✅ SESSION_COOKIE_AGE: {settings.SESSION_COOKIE_AGE}s'))
        self.stdout.write(self.style.SUCCESS(f'   ✅ SESSION_COOKIE_HTTPONLY: {settings.SESSION_COOKIE_HTTPONLY}'))
        
        # 8. File Upload Limits
        self.stdout.write('\n8. File Upload Security')
        max_size = getattr(settings, 'MAX_UPLOAD_SIZE', 0)
        allowed_types = getattr(settings, 'ALLOWED_UPLOAD_TYPES', [])
        self.stdout.write(self.style.SUCCESS(f'   ✅ MAX_UPLOAD_SIZE: {max_size // (1024*1024)}MB'))
        self.stdout.write(self.style.SUCCESS(f'   ✅ ALLOWED_TYPES: {", ".join(allowed_types)}'))
        
        # Summary
        self.stdout.write('\n' + '=' * 50)
        if issues:
            self.stdout.write(self.style.ERROR(f'\n⚠️  Found {len(issues)} issue(s):'))
            for issue in issues:
                self.stdout.write(self.style.ERROR(f'   • {issue}'))
        else:
            self.stdout.write(self.style.SUCCESS('\n✅ All security checks passed!'))
        
        self.stdout.write('')
