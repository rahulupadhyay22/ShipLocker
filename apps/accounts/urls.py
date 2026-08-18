from django.urls import path
from . import views

app_name = 'accounts'

urlpatterns = [
    path('login/', views.LoginView.as_view(), name='login'),
    path('login/google/', views.GoogleLoginView.as_view(), name='google_login'),
    path('login/google/callback/', views.GoogleCallbackView.as_view(), name='google_callback'),
    path('verify-otp/', views.VerifyOTPView.as_view(), name='verify_otp'),
    path('logout/', views.LogoutView.as_view(), name='logout'),
    path('profile/', views.ProfileView.as_view(), name='profile'),
    path('dashboard/', views.DashboardView.as_view(), name='dashboard'),
    path('addresses/add/', views.SavedAddressCreateView.as_view(), name='address_add'),
    path('addresses/<uuid:pk>/edit/', views.SavedAddressUpdateView.as_view(), name='address_edit'),
    path('addresses/<uuid:pk>/delete/', views.SavedAddressDeleteView.as_view(), name='address_delete'),
    path('addresses/<uuid:pk>/default/', views.SavedAddressSetDefaultView.as_view(), name='address_set_default'),
]
