from django.urls import path
from . import views

app_name = 'personal_shop'

urlpatterns = [
    path('', views.TrunkAssistDashboardView.as_view(), name='dashboard'),
    path('requests/', views.PersonalShopRequestListView.as_view(), name='request_list'),
    path('new/<str:request_type>/', views.PersonalShopRequestCreateView.as_view(), name='request_create'),
    path('requests/<uuid:pk>/', views.PersonalShopRequestDetailView.as_view(), name='request_detail'),
    path('requests/<uuid:pk>/edit/', views.PersonalShopRequestEditView.as_view(), name='request_edit'),
    path('requests/<uuid:pk>/cancel/', views.PersonalShopRequestCancelView.as_view(), name='request_cancel'),
    path('requests/<uuid:pk>/quotation/', views.PersonalShopQuotationView.as_view(), name='quotation_detail'),
    path('requests/<uuid:pk>/quotation/decline/', views.PersonalShopQuotationDeclineView.as_view(), name='quotation_decline'),
    path('requests/<uuid:pk>/quotation/pay/', views.CreatePersonalShopPaymentOrderView.as_view(), name='quotation_pay'),
    path('requests/<uuid:pk>/payment/confirmation/', views.PersonalShopPaymentConfirmationView.as_view(), name='payment_confirmation'),
]
