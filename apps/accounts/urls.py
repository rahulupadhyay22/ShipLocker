from django.urls import path
from . import views

app_name = 'accounts'

urlpatterns = [
    path('login/', views.LoginView.as_view(), name='login'),
    path('login/google/', views.GoogleLoginView.as_view(), name='google_login'),
    path('verify-otp/', views.VerifyOTPView.as_view(), name='verify_otp'),
    path('logout/', views.LogoutView.as_view(), name='logout'),
    path('profile/', views.ProfileView.as_view(), name='profile'),
    path('dashboard/', views.DashboardView.as_view(), name='dashboard'),
]
