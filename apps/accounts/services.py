"""Supabase integration services for authentication and storage."""

import logging
import os
from decimal import Decimal
from supabase import create_client, Client
from django.conf import settings
from indiabox.circuit_breaker import CircuitBreaker, CircuitOpenError

logger = logging.getLogger('security')

ZERO = Decimal('0.00')

# Storage backs parcel-image/KYC/invoice uploads and the signed URLs
# dashboards fetch per image, all on request threads.
_storage_breaker = CircuitBreaker('supabase_storage', fail_threshold=5, reset_timeout=60, max_concurrency=6)

# Auth backs every login/OTP-verify request -- the busiest, most latency-
# sensitive Supabase call in the app.
_auth_breaker = CircuitBreaker('supabase_auth', fail_threshold=5, reset_timeout=60, max_concurrency=6)


def get_supabase_client() -> Client:
    """Get Supabase client instance."""
    url = ''
    key = ''
    try:
        from apps.notifications.models import AppSettings
        app_settings = AppSettings.get_settings()
        url = app_settings.supabase_url or ''
        key = app_settings.supabase_service_role_key or ''
    except Exception:
        pass
    if not url:
        url = settings.SUPABASE_URL
    if not key:
        key = settings.SUPABASE_KEY
    return create_client(url, key)


def get_supabase_anon_client() -> Client:
    """Get Supabase client with anon key (for frontend auth)."""
    url = ''
    key = ''
    try:
        from apps.notifications.models import AppSettings
        app_settings = AppSettings.get_settings()
        url = app_settings.supabase_url or ''
        key = app_settings.supabase_anon_key or ''
    except Exception:
        pass
    if not url:
        url = settings.SUPABASE_URL
    if not key:
        key = settings.SUPABASE_ANON_KEY
    return create_client(url, key)


class SupabaseAuth:
    """Handle Supabase authentication operations."""
    
    def __init__(self):
        self.client = get_supabase_client()
    
    def sign_up_with_email(self, email: str, password: str = None):
        """Sign up user with email (passwordless or with password)."""
        with _auth_breaker.call():
            if password:
                response = self.client.auth.sign_up({
                    "email": email,
                    "password": password
                })
            else:
                # Send magic link
                response = self.client.auth.sign_in_with_otp({
                    "email": email
                })
        return response

    def sign_in_with_otp(self, email: str):
        """Send OTP to email for passwordless login."""
        with _auth_breaker.call():
            return self.client.auth.sign_in_with_otp({
                "email": email
            })

    def verify_otp(self, email: str, token: str):
        """Verify OTP token."""
        with _auth_breaker.call():
            return self.client.auth.verify_otp({
                "email": email,
                "token": token,
                "type": "email"
            })

    def sign_in_with_google(self, redirect_url: str):
        """Get Google OAuth URL for sign in.

        The gotrue client uses the PKCE flow: it generates a code_verifier
        and only keeps it in its own (per-instance, in-memory) storage. Since
        a fresh client is built per request, we have to pull it out here and
        hand it back to the caller to stash somewhere that survives until the
        callback request (e.g. the Django session) — otherwise the later
        exchange_code_for_session() has no verifier to send and Supabase
        rejects it with "auth code and code verifier should be non-empty".
        """
        with _auth_breaker.call():
            gotrue = self.client.auth
            response = gotrue.sign_in_with_oauth({
                "provider": "google",
                "options": {
                    "redirect_to": redirect_url
                }
            })
            code_verifier = gotrue._storage.get_item(f"{gotrue._storage_key}-code-verifier")
        return response.url, code_verifier

    def exchange_code_for_session(self, auth_code: str, code_verifier: str):
        """Exchange the OAuth callback's authorization code for a Supabase session."""
        with _auth_breaker.call():
            return self.client.auth.exchange_code_for_session({
                "auth_code": auth_code,
                "code_verifier": code_verifier,
            })

    def get_user(self, access_token: str):
        """Get user from access token."""
        with _auth_breaker.call():
            return self.client.auth.get_user(access_token)

    def sign_out(self, access_token: str):
        """Sign out user."""
        with _auth_breaker.call():
            return self.client.auth.sign_out()


class SupabaseStorage:
    """Handle Supabase storage operations."""
    
    BUCKETS = {
        'parcels': 'parcel-images',
        'invoices': 'invoices',
        'kyc': 'kyc-documents',
    }
    
    def __init__(self):
        self.client = get_supabase_client()
    
    def upload_file(self, bucket_name: str, file_path: str, file_data: bytes, content_type: str = None, upsert: bool = False):
        """Upload file to Supabase storage."""
        options = {}
        if content_type:
            options['content-type'] = content_type
        if upsert:
            options['upsert'] = 'true'

        with _storage_breaker.call():
            return self.client.storage.from_(bucket_name).upload(
                file_path,
                file_data,
                options
            )

    def get_public_url(self, bucket_name: str, file_path: str):
        """Get public URL for a file."""
        return self.client.storage.from_(bucket_name).get_public_url(file_path)

    def get_signed_url(self, bucket_name: str, file_path: str, expires_in: int = 3600):
        """Get signed URL for private file access."""
        with _storage_breaker.call():
            return self.client.storage.from_(bucket_name).create_signed_url(
                file_path,
                expires_in
            )

    def delete_file(self, bucket_name: str, file_path: str):
        """Delete a file from storage."""
        with _storage_breaker.call():
            return self.client.storage.from_(bucket_name).remove([file_path])


def delete_storage_file(bucket_name: str, file_path: str):
    """Best-effort Supabase Storage delete for use in model post_delete
    signals — never raises, so a Storage outage can't block the DB delete
    that triggered it (the file would just become an orphan to clean up
    later, same as if this call didn't exist)."""
    if not file_path:
        return
    try:
        SupabaseStorage().delete_file(bucket_name, file_path)
    except Exception as e:
        logger.warning(f'Storage cleanup failed for {bucket_name}/{file_path}: {e}')


def calculate_premium_savings(locker):
    """Recompute lifetime Premium savings from scratch via live aggregate
    queries — NOT called by any view (spec 11a moved display reads to the
    denormalized Locker.premium_savings_display, updated incrementally at
    each payment-finalize point via Locker.record_premium_savings()). Kept
    as the audit/backfill source of truth: this is what
    apps/accounts/migrations/0007_backfill_premium_savings_amount.py's
    formula mirrors, and what a drift-recompute would re-run.

    Premium lockers: real money already saved, summed across all four
    discount categories (TrunkAssist, shipping, storage, consolidation)
    over paid/approved history — never pending/unpaid records, same
    "locked historical charge" rule as everywhere else in spec 11. Free
    lockers: a hypothetical, built by re-applying today's discount rates
    to the 'standard' (undiscounted) totals — NOT 'actual'. A currently-
    Free locker can still have Premium-priced history (they downgraded),
    where actual < standard already; using actual there would discount an
    already-discounted figure a second time.

    Sum() aggregates scoped to this locker's FK-indexed history — not a
    per-row Python loop — see spec 11a for the perf reasoning on why this
    runs inline per-request rather than through a cache.

    Thin wrapper around calculate_premium_savings_breakdown() — collapses
    its per-category dict (now including consolidation, which has its own
    record_premium_savings() call site — see apps/payments/views.py::
    _record_shipment_premium_savings) into the single lump total this
    function has always returned, so it reconciles with
    Locker.premium_savings_amount again.
    """
    breakdown = calculate_premium_savings_breakdown(locker)
    amount = sum(breakdown['categories'].values(), ZERO)
    if locker.is_premium:
        label = f"You've saved ₹{amount} with Premium so far" if amount > 0 else ''
    else:
        label = f"You could have saved ₹{amount} with Premium so far — upgrade now" if amount > 0 else ''
    return {'is_premium': locker.is_premium, 'amount': amount, 'label': label}


def calculate_premium_savings_breakdown(locker):
    """Per-category live aggregate breakdown for the dedicated Account >
    Subscription page (/accounts/subscription/) — itemized TrunkAssist/
    Shipping/Storage/Consolidation numbers, not the single lump total
    calculate_premium_savings() (and the denormalized banner field it used
    to back) show. This page is visited occasionally, not loaded on every
    page view, so the perf case for denormalizing doesn't apply here —
    live aggregates, same as calculate_premium_savings() used to run before
    spec 11a moved the banner to Locker.premium_savings_amount.

    Per-row, not locker-level: each row contributes the real discount
    already applied to IT (standard - actual) if that row actually got one,
    else the hypothetical standard * rate for a row that never was
    discounted (paid while Free, or predates this category). This is NOT
    branched on the locker's current is_premium — a locker that changed
    plan partway through its history has some rows genuinely discounted and
    some not, and locker-level branching mispriced whichever set didn't
    match the current plan (see code review: it made this total diverge
    from the denormalized Locker.premium_savings_amount, which already
    accumulates this way per row via record_premium_savings()). For a
    locker that's been on one plan its whole history, this collapses to
    exactly the old real/hypothetical branches.

    Consolidation is the one category that ISN'T a percentage discount —
    apps.payments.services._get_consolidation_fee_amount waives it entirely
    for Premium (100% off, not PREMIUM_*_DISCOUNT_RATE off). So an
    undiscounted consolidation row's hypothetical is the full standard
    amount, not standard * a rate less than 1.
    """
    from decimal import Decimal, ROUND_HALF_UP
    from django.db.models import Sum, Count, Q, Case, When, F, Value, DecimalField
    from django.db.models.functions import Coalesce
    from .models import Locker
    from apps.personal_shop.models import PersonalShopQuotation
    from apps.shipments.models import Shipment
    from apps.payments.models import BatchCharge

    MONEY_FIELD = DecimalField(max_digits=10, decimal_places=2)

    def per_row_savings(standard_field, actual_field, rate):
        """real discount for a row that actually got one; else the
        hypothetical standard * rate. A single Sum(Case(...)) aggregate —
        not a per-row Python loop."""
        return Sum(Case(
            When(**{f'{actual_field}__lt': F(standard_field)}, then=F(standard_field) - F(actual_field)),
            default=Coalesce(F(standard_field), Value(ZERO)) * rate,
            output_field=MONEY_FIELD,
        ))

    quotation_totals = PersonalShopQuotation.objects.filter(
        request__locker=locker, quotation_type='purchase', status='approved',
    ).aggregate(
        standard=Sum('service_fee_standard_amount'), count=Count('id'),
        savings=per_row_savings('service_fee_standard_amount', 'service_fee_amount',
                                 Locker.PREMIUM_SERVICE_FEE_DISCOUNT_RATE),
    )
    shipment_totals = Shipment.objects.filter(
        user__locker=locker, payment_status='paid',
    ).aggregate(
        standard=Sum('shipping_cost_standard'),
        consolidation_standard=Sum('consolidation_fee_standard'),
        count=Count('id'),
        consolidation_count=Count('id', filter=Q(consolidation_fee_standard__gt=0)),
        savings=per_row_savings('shipping_cost_standard', 'shipping_cost',
                                 Locker.PREMIUM_SHIPPING_DISCOUNT_RATE),
        # 100% off, not a rate — see docstring.
        consolidation_savings=per_row_savings('consolidation_fee_standard', 'consolidation_fee', Decimal('1.00')),
    )
    batch_totals = BatchCharge.objects.filter(
        batch__locker=locker, status='paid',
    ).aggregate(
        standard=Sum('amount_standard'), count=Count('id'),
        savings=per_row_savings('amount_standard', 'amount', Locker.PREMIUM_STORAGE_DISCOUNT_RATE),
    )

    def rounded(amount):
        return (amount or ZERO).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    categories = {
        'trunkassist': rounded(quotation_totals['savings']),
        'shipping': rounded(shipment_totals['savings']),
        'storage': rounded(batch_totals['savings']),
        'consolidation': rounded(shipment_totals['consolidation_savings']),
    }

    total = sum(categories.values(), ZERO)

    standards = {
        'trunkassist': quotation_totals['standard'] or ZERO,
        'shipping': shipment_totals['standard'] or ZERO,
        'storage': batch_totals['standard'] or ZERO,
        'consolidation': shipment_totals['consolidation_standard'] or ZERO,
    }
    counts = {
        'trunkassist': quotation_totals['count'] or 0,
        'shipping': shipment_totals['count'] or 0,
        'storage': batch_totals['count'] or 0,
        'consolidation': shipment_totals['consolidation_count'] or 0,
    }

    def category_row(key):
        standard = standards[key]
        disc = categories[key]
        pct = (disc / standard * 100).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP) if standard > 0 else None
        return {
            'standard': standard,
            'effective': max(ZERO, standard - disc),
            'discount': disc,
            'pct': pct,
            'count': counts[key],
        }

    category_detail = {key: category_row(key) for key in categories}
    categories_used = sum(1 for detail in category_detail.values() if detail['count'] > 0)

    return {
        'is_premium': locker.is_premium,
        'categories': categories,
        'category_detail': category_detail,
        'total': total,
        'categories_used': categories_used,
        'shipments_count': shipment_totals['count'] or 0,
    }


def _monthly_category_totals(locker):
    """Per-calendar-month {'standard', 'discount'} totals, merged across
    the three savings source models — the single shared data source for
    both the hero sparkline (calculate_premium_savings_trend) and its
    percentage badge (windowed_savings_pct). Both read from this same dict
    and the same _trend_window_months() selection, so the chart and the
    number sitting next to it are structurally guaranteed to describe the
    same months — no separate lifetime-vs-windowed drift between them.

    Premium lockers only: a Free locker's hypothetical has no real payment
    timeline to plot, only a lump sum (returns {} for Free).
    """
    if not locker.is_premium:
        return {}

    from collections import defaultdict
    from django.db.models import Sum
    from django.db.models.functions import Coalesce, TruncMonth
    from apps.personal_shop.models import PersonalShopQuotation
    from apps.shipments.models import Shipment
    from apps.payments.models import BatchCharge

    monthly = defaultdict(lambda: {'standard': ZERO, 'discount': ZERO})

    def accumulate(qs, standard_field, actual_field, date_field):
        rows = qs.annotate(
            month=TruncMonth(Coalesce(date_field, 'created_at'))
        ).values('month').annotate(standard=Sum(standard_field), actual=Sum(actual_field))
        for row in rows:
            if row['month'] is None:
                continue
            standard = row['standard'] or ZERO
            disc = max(ZERO, standard - (row['actual'] or ZERO))
            bucket = monthly[row['month'].date()]
            bucket['standard'] += standard
            bucket['discount'] += disc

    accumulate(
        PersonalShopQuotation.objects.filter(request__locker=locker, quotation_type='purchase', status='approved'),
        'service_fee_standard_amount', 'service_fee_amount', 'request__paid_at',
    )
    shipments = Shipment.objects.filter(user__locker=locker, payment_status='paid')
    accumulate(shipments, 'shipping_cost_standard', 'shipping_cost', 'paid_at')
    accumulate(shipments, 'consolidation_fee_standard', 'consolidation_fee', 'paid_at')
    accumulate(
        BatchCharge.objects.filter(batch__locker=locker, status='paid'),
        'amount_standard', 'amount', 'paid_at',
    )
    return monthly


def _trend_window_months(monthly, months=6):
    """Last up-to-`months` calendar months with activity, chronological —
    the exact window calculate_premium_savings_trend() and
    windowed_savings_pct() must agree on. Below 2 months there's nothing
    meaningful to plot a line through, so callers treat [] as 'no chart'."""
    if len(monthly) < 2:
        return []
    return sorted(monthly.keys())[-months:]


def calculate_premium_savings_trend(locker, monthly=None):
    """Cumulative real Premium savings by calendar month, last up to 6
    months with activity — feeds the Subscription page's hero sparkline.
    The running total INCLUDES months before the window (so the line
    doesn't start artificially low), but only points inside the window are
    returned. Returns [] below 2 data points in the window — the caller
    hides the chart region entirely in that case.

    `monthly` lets a caller that also needs windowed_savings_pct() (the
    Subscription page) compute _monthly_category_totals() once and pass it
    to both, instead of each function querying it independently."""
    if monthly is None:
        monthly = _monthly_category_totals(locker)
    window = _trend_window_months(monthly)
    if len(window) < 2:
        return []

    window_set = set(window)
    running = ZERO
    points = []
    for month in sorted(monthly.keys()):
        running += monthly[month]['discount']
        if month in window_set:
            points.append({'label': month.strftime('%b'), 'value': running})
    return points


def build_sparkline_geometry(trend, width=240, height=70, pad=6):
    """Turn calculate_premium_savings_trend()'s [{'label','value'}, ...]
    into SVG polyline coordinates for the Subscription page's hero chart —
    kept as a pure function (no request/template coupling) so it's testable
    on its own. Returns None if there isn't enough data to draw a line."""
    if not trend or len(trend) < 2:
        return None

    values = [point['value'] for point in trend]
    min_v, max_v = min(values), max(values)
    span = (max_v - min_v) or 1
    n = len(values)

    dots = []
    for i, v in enumerate(values):
        x = round((i / (n - 1)) * width, 1)
        y = round(height - pad - float(v - min_v) / float(span) * (height - 2 * pad), 1)
        dots.append({'x': x, 'y': y})

    points_str = ' '.join(f"{d['x']},{d['y']}" for d in dots)
    return {'points_str': points_str, 'dots': dots}


def _pct_from_monthly(monthly, window):
    """Pure arithmetic core of windowed_savings_pct() — total discount over
    total standard price, summed only over `window` (a list of month
    dates). Split out from the DB-querying wrapper so it's testable with a
    synthetic `monthly` dict, no database required."""
    if len(window) < 2:
        return None
    total_standard = sum((monthly[m]['standard'] for m in window), ZERO)
    total_discount = sum((monthly[m]['discount'] for m in window), ZERO)
    if total_standard <= 0:
        return None
    from decimal import ROUND_HALF_UP
    return int((total_discount / total_standard * 100).to_integral_value(rounding=ROUND_HALF_UP))


def windowed_savings_pct(locker, monthly=None):
    """'X% less than without Premium' badge next to the hero sparkline —
    deliberately scoped to the SAME up-to-6-month window
    calculate_premium_savings_trend() plots (both read _monthly_category_
    totals() and _trend_window_months() the same way), so the badge never
    describes a period the chart isn't showing. A locker with, say, 14
    months of Premium history and a big one-off saving in month 2 will NOT
    have that month's discount silently inflating a number sitting next to
    a chart that only plots the last 6 months.

    This replaced an earlier lifetime-total version (total discount over
    the locker's entire history) that could diverge sharply from what the
    chart actually showed once a locker had more than 6 months of activity
    — see code review. Deliberately NOT a month-over-month growth rate
    either: with only a few months of history, growth-since-first-month
    swings wildly (e.g. +500% off a tiny first month) and reads as a
    fabricated/misleading number. Returns None when there's no chart to
    pair it with, or no standard spend in that window to divide by.

    `monthly` lets a caller that also needs calculate_premium_savings_trend()
    (the Subscription page) compute _monthly_category_totals() once and
    pass it to both — see that function's docstring."""
    if monthly is None:
        monthly = _monthly_category_totals(locker)
    window = _trend_window_months(monthly)
    return _pct_from_monthly(monthly, window)
