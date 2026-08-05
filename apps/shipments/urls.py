from django.urls import path
from . import views

app_name = 'shipments'

urlpatterns = [
    path('', views.ShipmentsListView.as_view(), name='list'),
    path('create/', views.CreateShipmentView.as_view(), name='create'),
    path('active/', views.ActiveShipmentsView.as_view(), name='active'),
    path('delivered/', views.DeliveredShipmentsView.as_view(), name='delivered'),
    path('closed/', views.ClosedShipmentsView.as_view(), name='closed'),
    path('<uuid:pk>/', views.ShipmentDetailView.as_view(), name='detail'),
    path('<uuid:pk>/service/', views.SelectShippingServiceView.as_view(), name='select_service'),
    path('customs-help/', views.CustomsHelpView.as_view(), name='customs_help'),
]
