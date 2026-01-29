from django.urls import path
from . import views

app_name = 'locker'

urlpatterns = [
    path('', views.MyLockerView.as_view(), name='my_locker'),
    path('action-required/', views.ActionRequiredView.as_view(), name='action_required'),
    path('ready-to-ship/', views.ReadyToShipView.as_view(), name='ready_to_ship'),
    path('returns/', views.ReturnsView.as_view(), name='returns'),
    path('discards/', views.DiscardsView.as_view(), name='discards'),
    path('parcel/<uuid:pk>/', views.ParcelDetailView.as_view(), name='parcel_detail'),
    path('parcel/<uuid:pk>/approve/', views.ApproveParcelView.as_view(), name='approve_parcel'),
    path('parcel/<uuid:pk>/return/', views.RequestReturnView.as_view(), name='request_return'),
    path('parcel/<uuid:pk>/discard/', views.RequestDiscardView.as_view(), name='request_discard'),
]
