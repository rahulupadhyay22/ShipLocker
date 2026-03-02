"""Security mixins for IndiaBox views."""

import logging
from django.core.exceptions import PermissionDenied
from django.http import Http404

security_logger = logging.getLogger('security')


class UserOwnershipMixin:
    """
    Mixin to verify that the user owns the object they're trying to access.
    
    This provides an additional layer of security beyond LoginRequiredMixin.
    Even if a user is authenticated, they should only access their own data.
    
    Usage:
        class MyView(LoginRequiredMixin, UserOwnershipMixin, DetailView):
            model = MyModel
            owner_field = 'user'  # or 'locker__user' for related fields
    """
    
    owner_field = 'user'  # Default field to check ownership
    
    def get_queryset(self):
        """Filter queryset to only include user's own objects."""
        queryset = super().get_queryset()
        
        # Build the filter based on owner_field
        if '__' in self.owner_field:
            # Handle related field like 'locker__user'
            filter_kwargs = {self.owner_field: self.request.user}
        else:
            filter_kwargs = {self.owner_field: self.request.user}
        
        return queryset.filter(**filter_kwargs)
    
    def get_object(self, queryset=None):
        """Get object and verify ownership."""
        obj = super().get_object(queryset)
        
        # Additional verification for security logging
        if not self._verify_ownership(obj):
            security_logger.warning(
                f"Unauthorized access attempt: User {self.request.user.email} "
                f"tried to access {obj.__class__.__name__} {obj.pk}"
            )
            raise PermissionDenied("You don't have permission to access this resource.")
        
        return obj
    
    def _verify_ownership(self, obj):
        """Verify that the current user owns the object."""
        # Navigate through related fields if needed
        fields = self.owner_field.split('__')
        current = obj
        
        for field in fields[:-1]:
            current = getattr(current, field, None)
            if current is None:
                return False
        
        owner = getattr(current, fields[-1], None)
        return owner == self.request.user


class LockerOwnershipMixin:
    """
    Specialized mixin for objects owned through user's locker.
    
    Usage:
        class ParcelDetailView(LoginRequiredMixin, LockerOwnershipMixin, DetailView):
            model = Parcel
    """
    
    def get_queryset(self):
        """Filter queryset to only include objects from user's locker."""
        queryset = super().get_queryset()
        
        # Handle different model types
        model = queryset.model
        
        # Direct locker relationship
        if hasattr(model, 'locker'):
            return queryset.filter(locker=self.request.user.locker)
        
        # Parcel relationship (for ReturnRequest, DiscardRequest)
        if hasattr(model, 'parcel'):
            return queryset.filter(parcel__locker=self.request.user.locker)
        
        # User relationship
        if hasattr(model, 'user'):
            return queryset.filter(user=self.request.user)
        
        # Fallback - log warning and return empty
        security_logger.warning(
            f"LockerOwnershipMixin: Unknown model type {model.__name__}"
        )
        return queryset.none()


class SecureActionMixin:
    """
    Mixin for views that perform sensitive actions.
    
    Provides additional security checks and logging.
    """
    
    def dispatch(self, request, *args, **kwargs):
        """Log all sensitive actions."""
        response = super().dispatch(request, *args, **kwargs)
        
        if request.method == 'POST':
            security_logger.info(
                f"Secure action: {self.__class__.__name__} by {request.user.email} "
                f"from {self._get_client_ip(request)}"
            )
        
        return response
    
    def _get_client_ip(self, request):
        """Get client IP address."""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR', 'unknown')


class ObjectOwnershipRequiredMixin:
    """
    Generic mixin that raises 404 if user doesn't own the object.
    
    More secure than returning PermissionDenied as it doesn't reveal
    that the object exists.
    """
    
    def get_object(self, queryset=None):
        """Get object - raises 404 if not owned by user."""
        try:
            obj = super().get_object(queryset)
        except Exception:
            raise Http404("Object not found")
        
        if not self._user_owns_object(obj):
            # Log the attempt
            security_logger.warning(
                f"Access denied: {self.request.user.email} tried to access "
                f"{obj.__class__.__name__} they don't own"
            )
            # Return 404 instead of 403 for security (don't reveal existence)
            raise Http404("Object not found")
        
        return obj
    
    def _user_owns_object(self, obj):
        """Check if user owns the object. Override in subclasses."""
        # Check common ownership patterns
        if hasattr(obj, 'user'):
            return obj.user == self.request.user
        if hasattr(obj, 'locker'):
            return obj.locker == getattr(self.request.user, 'locker', None)
        if hasattr(obj, 'parcel') and hasattr(obj.parcel, 'locker'):
            return obj.parcel.locker == getattr(self.request.user, 'locker', None)
        return False
