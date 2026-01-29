from django.urls import path
from . import views

app_name = 'kyc'

urlpatterns = [
    path('', views.KYCListView.as_view(), name='list'),
    path('upload/', views.KYCUploadView.as_view(), name='upload'),
    path('<uuid:pk>/', views.KYCDetailView.as_view(), name='detail'),
]
