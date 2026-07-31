from django.urls import path
from . import views

app_name = 'locker'

urlpatterns = [
    path('', views.MyTrunkView.as_view(), name='my_locker'),
    path('action-required/', views.MyTrunkView.as_view(), name='action_required'),
    path('ready-to-ship/', views.MyTrunkView.as_view(), name='ready_to_ship'),
    path('returns/', views.MyTrunkView.as_view(), name='returns'),
    path('discards/', views.MyTrunkView.as_view(), name='discards'),
    path('parcel/<uuid:pk>/', views.ParcelDetailView.as_view(), name='parcel_detail'),
    path('parcel/<uuid:pk>/approve/', views.ApproveParcelView.as_view(), name='approve_parcel'),
    path('parcel/<uuid:pk>/return/', views.RequestReturnView.as_view(), name='request_return'),
    path('parcel/<uuid:pk>/discard/', views.RequestDiscardView.as_view(), name='request_discard'),
]
