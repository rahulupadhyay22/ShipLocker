"""Supabase integration services for authentication and storage."""

import os
from supabase import create_client, Client
from django.conf import settings


def get_supabase_client() -> Client:
    """Get Supabase client instance."""
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)


def get_supabase_anon_client() -> Client:
    """Get Supabase client with anon key (for frontend auth)."""
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_ANON_KEY)


class SupabaseAuth:
    """Handle Supabase authentication operations."""
    
    def __init__(self):
        self.client = get_supabase_client()
    
    def sign_up_with_email(self, email: str, password: str = None):
        """Sign up user with email (passwordless or with password)."""
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
        return self.client.auth.sign_in_with_otp({
            "email": email
        })
    
    def verify_otp(self, email: str, token: str):
        """Verify OTP token."""
        return self.client.auth.verify_otp({
            "email": email,
            "token": token,
            "type": "email"
        })
    
    def sign_in_with_google(self, redirect_url: str):
        """Get Google OAuth URL for sign in."""
        response = self.client.auth.sign_in_with_oauth({
            "provider": "google",
            "options": {
                "redirect_to": redirect_url
            }
        })
        return response.url
    
    def get_user(self, access_token: str):
        """Get user from access token."""
        return self.client.auth.get_user(access_token)
    
    def sign_out(self, access_token: str):
        """Sign out user."""
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
    
    def upload_file(self, bucket_name: str, file_path: str, file_data: bytes, content_type: str = None):
        """Upload file to Supabase storage."""
        options = {}
        if content_type:
            options['content-type'] = content_type
        
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
        return self.client.storage.from_(bucket_name).create_signed_url(
            file_path,
            expires_in
        )
    
    def delete_file(self, bucket_name: str, file_path: str):
        """Delete a file from storage."""
        return self.client.storage.from_(bucket_name).remove([file_path])
