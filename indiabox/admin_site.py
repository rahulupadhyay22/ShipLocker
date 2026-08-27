"""Gates django.contrib.admin behind OTP verification for staff/superusers.

Importing this module (see indiabox/urls.py) monkeypatches admin.site's class
to add the OTP check on top of Unfold's admin site, so every existing
@admin.register()'d ModelAdmin across the codebase is covered without
per-app changes.

Redirects unverified/unauthenticated admin requests to two_factor's login
view (mounted under /manage-rb-panel/account/ in indiabox/urls.py) rather
than the site-wide LOGIN_URL, which is the customer passwordless-OTP flow
used everywhere else in the app.
"""

from django.contrib import admin
from django.contrib.auth.views import redirect_to_login
from django.urls import reverse
from two_factor.admin import AdminSiteOTPRequiredMixin
from two_factor.views import (
    BackupTokensView, DisableView, LoginView, ProfileView,
    SetupCompleteView, SetupView,
)
from unfold.sites import UnfoldAdminSite


class OTPRequiredAdminSite(AdminSiteOTPRequiredMixin, UnfoldAdminSite):
    def login(self, request, extra_context=None):
        redirect_to = request.GET.get('next') or reverse('admin:index')
        return redirect_to_login(redirect_to, login_url=reverse('two_factor:login'))


admin.site.__class__ = OTPRequiredAdminSite


class UnfoldContextMixin:
    """Feeds Unfold's admin theme (colors, site branding, etc.) into two_factor's
    views, since they render through templates/two_factor/_base.html (which
    extends Unfold's admin login layout) but don't otherwise go through
    admin.site to pick up that context."""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(admin.site.each_context(self.request))
        return context


# Unfold-themed two_factor views (see UnfoldContextMixin), used in indiabox/urls.py
# in place of two_factor.urls' raw view classes. QRGeneratorView is omitted since
# it returns a PNG image, not a template.
class UnfoldLoginView(UnfoldContextMixin, LoginView):
    pass


class UnfoldSetupView(UnfoldContextMixin, SetupView):
    pass


class UnfoldSetupCompleteView(UnfoldContextMixin, SetupCompleteView):
    pass


class UnfoldBackupTokensView(UnfoldContextMixin, BackupTokensView):
    pass


class UnfoldProfileView(UnfoldContextMixin, ProfileView):
    pass


class UnfoldDisableView(UnfoldContextMixin, DisableView):
    pass
