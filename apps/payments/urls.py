from django.urls import path
from . import views

app_name = 'payments'

urlpatterns = [
    path(
        'create-order/<uuid:shipment_pk>/',
        views.CreatePaymentOrderView.as_view(),
        name='create_order',
    ),
    path(
        'verify/',
        views.VerifyPaymentView.as_view(),
        name='verify',
    ),
    path(
        'premium/create-order/',
        views.CreatePremiumSubscriptionOrderView.as_view(),
        name='premium_create_order',
    ),
    path(
        'webhook/razorpay/',
        views.RazorpayWebhookView.as_view(),
        name='razorpay_webhook',
    ),
]
