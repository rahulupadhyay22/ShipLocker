from django.urls import path
from . import views

app_name = 'content'

urlpatterns = [
    path('', views.HomeView.as_view(), name='home'),
    path('page/<slug:slug>/', views.StaticPageView.as_view(), name='page'),
    path('prohibited-items/', views.ProhibitedItemsView.as_view(), name='prohibited_items'),
    path('shipping-calculator/', views.ShippingCalculatorView.as_view(), name='shipping_calculator'),
    path('service-charges/', views.ServiceChargesView.as_view(), name='service_charges'),
    path('refund-policy/', views.RefundPolicyView.as_view(), name='refund_policy'),
    path('duties/', views.DutiesView.as_view(), name='duties'),
    path('faq/', views.FAQView.as_view(), name='faq'),
    path('terms/', views.TermsView.as_view(), name='terms'),
    path('privacy/', views.PrivacyView.as_view(), name='privacy'),
    path('about/', views.AboutView.as_view(), name='about'),
]
