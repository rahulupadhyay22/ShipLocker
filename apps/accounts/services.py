"""Supabase integration services for authentication and storage."""

import logging
import os
from supabase import create_client, Client
from django.conf import settings
from indiabox.circuit_breaker import CircuitBreaker, CircuitOpenError

logger = logging.getLogger('security')

# Storage backs parcel-image/KYC/invoice uploads and the signed URLs
# dashboards fetch per image, all on request threads.
_storage_breaker = CircuitBreaker('supabase_storage', fail_threshold=5, reset_timeout=60, max_concurrency=6)

# Auth backs every login/OTP-verify request -- the busiest, most latency-
# sensitive Supabase call in the app.
_auth_breaker = CircuitBreaker('supabase_auth', fail_threshold=5, reset_timeout=60, max_concurrency=6)


def get_supabase_client() -> Client:
    """Get Supabase client instance."""
    url = ''
    key = ''
    try:
        from apps.notifications.models import AppSettings
        app_settings = AppSettings.get_settings()
        url = app_settings.supabase_url or ''
        key = app_settings.supabase_service_role_key or ''
    except Exception:
        pass
    if not url:
        url = settings.SUPABASE_URL
    if not key:
        key = settings.SUPABASE_KEY
    return create_client(url, key)


def get_supabase_anon_client() -> Client:
    """Get Supabase client with anon key (for frontend auth)."""
    url = ''
    key = ''
    try:
        from apps.notifications.models import AppSettings
        app_settings = AppSettings.get_settings()
        url = app_settings.supabase_url or ''
        key = app_settings.supabase_anon_key or ''
    except Exception:
        pass
    if not url:
        url = settings.SUPABASE_URL
    if not key:
        key = settings.SUPABASE_ANON_KEY
    return create_client(url, key)


class SupabaseAuth:
    """Handle Supabase authentication operations."""
    
    def __init__(self):
        self.client = get_supabase_client()
    
    def sign_up_with_email(self, email: str, password: str = None):
        """Sign up user with email (passwordless or with password)."""
        with _auth_breaker.call():
            if password:
                response = self.client.auth.sign_up({
                    "email": email,
                    "password": password
                })
            else:
                # Send magic link
                response = self.client.auth.sign_in_with_otp({
                    "email": email
                })
        return response

    def sign_in_with_otp(self, email: str):
        """Send OTP to email for passwordless login."""
        with _auth_breaker.call():
            return self.client.auth.sign_in_with_otp({
                "email": email
            })

    def verify_otp(self, email: str, token: str):
        """Verify OTP token."""
        with _auth_breaker.call():
            return self.client.auth.verify_otp({
                "email": email,
                "token": token,
                "type": "email"
            })

    def sign_in_with_google(self, redirect_url: str):
        """Get Google OAuth URL for sign in.

        The gotrue client uses the PKCE flow: it generates a code_verifier
        and only keeps it in its own (per-instance, in-memory) storage. Since
        a fresh client is built per request, we have to pull it out here and
        hand it back to the caller to stash somewhere that survives until the
        callback request (e.g. the Django session) — otherwise the later
        exchange_code_for_session() has no verifier to send and Supabase
        rejects it with "auth code and code verifier should be non-empty".
        """
        with _auth_breaker.call():
            gotrue = self.client.auth
            response = gotrue.sign_in_with_oauth({
                "provider": "google",
                "options": {
                    "redirect_to": redirect_url
                }
            })
            code_verifier = gotrue._storage.get_item(f"{gotrue._storage_key}-code-verifier")
        return response.url, code_verifier

    def exchange_code_for_session(self, auth_code: str, code_verifier: str):
        """Exchange the OAuth callback's authorization code for a Supabase session."""
        with _auth_breaker.call():
            return self.client.auth.exchange_code_for_session({
                "auth_code": auth_code,
                "code_verifier": code_verifier,
            })

    def get_user(self, access_token: str):
        """Get user from access token."""
        with _auth_breaker.call():
            return self.client.auth.get_user(access_token)

    def sign_out(self, access_token: str):
        """Sign out user."""
        with _auth_breaker.call():
            return self.client.auth.sign_out()


class SupabaseStorage:
    """Handle Supabase storage operations."""
    
    BUCKETS = {
        'parcels': 'parcel-images',
        'invoices': 'invoices',
        'kyc': 'kyc-documents',
    }
    
    def __init__(self):
        self.client = get_supabase_client()
    
    def upload_file(self, bucket_name: str, file_path: str, file_data: bytes, content_type: str = None, upsert: bool = False):
        """Upload file to Supabase storage."""
        options = {}
        if content_type:
            options['content-type'] = content_type
        if upsert:
            options['upsert'] = 'true'

        with _storage_breaker.call():
            return self.client.storage.from_(bucket_name).upload(
                file_path,
                file_data,
                options
            )

    def get_public_url(self, bucket_name: str, file_path: str):
        """Get public URL for a file."""
        return self.client.storage.from_(bucket_name).get_public_url(file_path)

    def get_signed_url(self, bucket_name: str, file_path: str, expires_in: int = 3600):
        """Get signed URL for private file access."""
        with _storage_breaker.call():
            return self.client.storage.from_(bucket_name).create_signed_url(
                file_path,
                expires_in
            )

    def delete_file(self, bucket_name: str, file_path: str):
        """Delete a file from storage."""
        with _storage_breaker.call():
            return self.client.storage.from_(bucket_name).remove([file_path])


def delete_storage_file(bucket_name: str, file_path: str):
    """Best-effort Supabase Storage delete for use in model post_delete
    signals — never raises, so a Storage outage can't block the DB delete
    that triggered it (the file would just become an orphan to clean up
    later, same as if this call didn't exist)."""
    if not file_path:
        return
    try:
        SupabaseStorage().delete_file(bucket_name, file_path)
    except Exception as e:
        logger.warning(f'Storage cleanup failed for {bucket_name}/{file_path}: {e}')
