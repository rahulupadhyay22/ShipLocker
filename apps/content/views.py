from django.shortcuts import render, get_object_or_404, redirect
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
from django.views.generic import TemplateView, DetailView, ListView

from .models import StaticPage, ServiceCharge

# ponytail: 15min cache_page on rarely-changing public pages that render identically
# regardless of auth state. Skipped on HomeView (auth-conditional redirect), and on
# ShippingCalculatorView/ServiceChargesView/ProhibitedItemsView/RefundPolicyView
# (AuthAwareBaseMixin renders a different base template for logged-in users).
STATIC_PAGE_CACHE_SECONDS = 60 * 15

# Mirrors apps/locker/services/batch_billing.py::create_batch's free-storage
# window (20 days free-plan / 30 days paid-plan) — kept as a separate literal
# here rather than imported, because spec 11-pricing.md's Definition of done
# requires batch_billing.py's diff to contain only the Premium storage-
# discount insertion; a display-only constant has no business living there.
FREE_PLAN_DISPLAY_STORAGE_DAYS = 20
PREMIUM_PLAN_DISPLAY_STORAGE_DAYS = 30


class AuthAwareBaseMixin:
    """Renders inside the app shell (base.html) for logged-in users, the
    marketing shell (public_base.html) otherwise -- same page either way."""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.user.is_authenticated:
            context['base_template'] = 'base.html'
        return context


class HomeView(TemplateView):
    """Landing page. Identical for every anonymous visitor, so the rendered
    page is cached -- but the auth check runs BEFORE the cache lookup, never
    inside it, so an authenticated user's redirect can never get written into
    (or served from) the shared anonymous-visitor cache entry."""
    template_name = 'content/home.html'

    def get(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect('accounts:dashboard')
        return self._cached_get(request, *args, **kwargs)

    @method_decorator(cache_page(STATIC_PAGE_CACHE_SECONDS))
    def _cached_get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        from apps.accounts.models import Locker
        from apps.locker.models import UserQuota
        from apps.notifications.models import AppSettings
        from apps.payments.services import _lookup_consolidation_fee_standard

        context = super().get_context_data(**kwargs)
        context['premium_annual_price'] = AppSettings.get_settings().premium_annual_price
        context.update(Locker.premium_rate_percentages())
        context['consolidation_fee_standard'] = _lookup_consolidation_fee_standard()

        context['free_storage_days'] = FREE_PLAN_DISPLAY_STORAGE_DAYS
        context['premium_storage_days'] = PREMIUM_PLAN_DISPLAY_STORAGE_DAYS
        # Read from the field default rather than a third named constant —
        # unlike the two storage-day constants above, UserQuota isn't
        # constrained against touching apps/locker/services/batch_billing.py,
        # so there's no reason not to source this one directly from the model.
        context['free_passes_per_year'] = UserQuota._meta.get_field('annual_quota').default

        return context


@method_decorator(cache_page(STATIC_PAGE_CACHE_SECONDS), name='dispatch')
class StaticPageView(DetailView):
    """Generic static page view."""
    template_name = 'content/static_page.html'
    context_object_name = 'page'
    slug_field = 'slug'
    slug_url_kwarg = 'slug'
    
    def get_queryset(self):
        return StaticPage.objects.filter(is_active=True)


class ProhibitedItemsView(AuthAwareBaseMixin, TemplateView):
    """Prohibited items page."""
    template_name = 'content/prohibited_items.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        try:
            context['page'] = StaticPage.objects.get(slug='prohibited-items', is_active=True)
        except StaticPage.DoesNotExist:
            context['page'] = None
        return context


class ShippingCalculatorView(AuthAwareBaseMixin, TemplateView):
    """Shipping calculator page."""
    template_name = 'content/shipping_calculator.html'
    
    def get_context_data(self, **kwargs):
        from .services import get_zones_data, build_zones_json

        context = super().get_context_data(**kwargs)

        zones_data = get_zones_data()
        context['zones_json'] = build_zones_json()  # same cached data, just JSON-encoded
        context['zones'] = zones_data
        return context


class ServiceChargesView(AuthAwareBaseMixin, ListView):
    """Service charges page."""
    template_name = 'content/service_charges.html'
    context_object_name = 'charges'
    
    def get_queryset(self):
        return ServiceCharge.objects.filter(is_active=True)


class RefundPolicyView(AuthAwareBaseMixin, TemplateView):
    """Refund policy page."""
    template_name = 'content/refund_policy.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        try:
            context['page'] = StaticPage.objects.get(slug='refund-policy', is_active=True)
        except StaticPage.DoesNotExist:
            context['page'] = None
        return context


@method_decorator(cache_page(STATIC_PAGE_CACHE_SECONDS), name='dispatch')
class DutiesView(TemplateView):
    """Duties and customs information page."""
    template_name = 'content/duties.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        try:
            context['page'] = StaticPage.objects.get(slug='duties', is_active=True)
        except StaticPage.DoesNotExist:
            context['page'] = None
        return context


@method_decorator(cache_page(STATIC_PAGE_CACHE_SECONDS), name='dispatch')
class FAQView(TemplateView):
    """Frequently Asked Questions page."""
    template_name = 'content/faq.html'


@method_decorator(cache_page(STATIC_PAGE_CACHE_SECONDS), name='dispatch')
class TermsView(TemplateView):
    """Terms and Conditions page."""
    template_name = 'content/terms.html'


@method_decorator(cache_page(STATIC_PAGE_CACHE_SECONDS), name='dispatch')
class PrivacyView(TemplateView):
    """Privacy Policy page."""
    template_name = 'content/privacy.html'

    def get_context_data(self, **kwargs):
        from apps.notifications.models import AppSettings
        context = super().get_context_data(**kwargs)
        app_settings = AppSettings.get_settings()
        context['grievance_officer_name'] = app_settings.grievance_officer_name
        context['grievance_officer_email'] = app_settings.grievance_officer_email
        return context


@method_decorator(cache_page(STATIC_PAGE_CACHE_SECONDS), name='dispatch')
class AboutView(TemplateView):
    """About Us page."""
    template_name = 'content/about.html'
