from django.shortcuts import render, get_object_or_404, redirect
from django.views.generic import TemplateView, DetailView, ListView

from .models import StaticPage, ServiceCharge


class HomeView(TemplateView):
    """Landing page."""
    template_name = 'content/home.html'
    
    def get(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect('accounts:dashboard')
        return super().get(request, *args, **kwargs)


class StaticPageView(DetailView):
    """Generic static page view."""
    template_name = 'content/static_page.html'
    context_object_name = 'page'
    slug_field = 'slug'
    slug_url_kwarg = 'slug'
    
    def get_queryset(self):
        return StaticPage.objects.filter(is_active=True)


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
        from .models import ShippingZone, ShippingRate
        import json
        
        context = super().get_context_data(**kwargs)
        
        # Build zones data for JavaScript
        zones_data = []
        for zone in ShippingZone.objects.filter(is_active=True).prefetch_related('rates'):
            zone_info = {
                'id': str(zone.id),
                'name': zone.name,
                'countries': zone.get_countries_list(),
                'rates': []
            }
            for rate in zone.rates.filter(is_active=True).order_by('min_weight'):
                zone_info['rates'].append({
                    'min_weight': float(rate.min_weight),
                    'max_weight': float(rate.max_weight),
                    'rate_type': rate.rate_type,
                    'price': float(rate.price),
                    'delivery_min': rate.delivery_days_min,
                    'delivery_max': rate.delivery_days_max,
                })
            zones_data.append(zone_info)
        
        context['zones_json'] = json.dumps(zones_data)
        context['zones'] = ShippingZone.objects.filter(is_active=True)
        if self.request.user.is_authenticated:
            context['base_template'] = 'base.html'
        return context


class ServiceChargesView(ListView):
    """Service charges page."""
    template_name = 'content/service_charges.html'
    context_object_name = 'charges'
    
    def get_queryset(self):
        return ServiceCharge.objects.filter(is_active=True)


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


class FAQView(TemplateView):
    """Frequently Asked Questions page."""
    template_name = 'content/faq.html'


class TermsView(TemplateView):
    """Terms and Conditions page."""
    template_name = 'content/terms.html'


class PrivacyView(TemplateView):
    """Privacy Policy page."""
    template_name = 'content/privacy.html'


class AboutView(TemplateView):
    """About Us page."""
    template_name = 'content/about.html'
