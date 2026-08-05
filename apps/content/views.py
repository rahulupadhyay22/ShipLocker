from django.shortcuts import render, get_object_or_404, redirect
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
from django.views.generic import TemplateView, DetailView, ListView

from .models import StaticPage, ServiceCharge

# ponytail: 15min cache_page on rarely-changing public pages, skip HomeView (auth-conditional redirect)
STATIC_PAGE_CACHE_SECONDS = 60 * 15


class HomeView(TemplateView):
    """Landing page."""
    template_name = 'content/home.html'
    
    def get(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect('accounts:dashboard')
        return super().get(request, *args, **kwargs)


@method_decorator(cache_page(STATIC_PAGE_CACHE_SECONDS), name='dispatch')
class StaticPageView(DetailView):
    """Generic static page view."""
    template_name = 'content/static_page.html'
    context_object_name = 'page'
    slug_field = 'slug'
    slug_url_kwarg = 'slug'
    
    def get_queryset(self):
        return StaticPage.objects.filter(is_active=True)


@method_decorator(cache_page(STATIC_PAGE_CACHE_SECONDS), name='dispatch')
class ProhibitedItemsView(TemplateView):
    """Prohibited items page."""
    template_name = 'content/prohibited_items.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        try:
            context['page'] = StaticPage.objects.get(slug='prohibited-items', is_active=True)
        except StaticPage.DoesNotExist:
            context['page'] = None
        return context


class ShippingCalculatorView(TemplateView):
    """Shipping calculator page."""
    template_name = 'content/shipping_calculator.html'
    
    def get_context_data(self, **kwargs):
        from .models import ShippingZone
        from .services import build_zones_json

        context = super().get_context_data(**kwargs)

        context['zones_json'] = build_zones_json()
        context['zones'] = ShippingZone.objects.filter(is_active=True)
        if self.request.user.is_authenticated:
            context['base_template'] = 'base.html'
        return context


@method_decorator(cache_page(STATIC_PAGE_CACHE_SECONDS), name='dispatch')
class ServiceChargesView(ListView):
    """Service charges page."""
    template_name = 'content/service_charges.html'
    context_object_name = 'charges'
    
    def get_queryset(self):
        return ServiceCharge.objects.filter(is_active=True)


@method_decorator(cache_page(STATIC_PAGE_CACHE_SECONDS), name='dispatch')
class RefundPolicyView(TemplateView):
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


@method_decorator(cache_page(STATIC_PAGE_CACHE_SECONDS), name='dispatch')
class AboutView(TemplateView):
    """About Us page."""
    template_name = 'content/about.html'
