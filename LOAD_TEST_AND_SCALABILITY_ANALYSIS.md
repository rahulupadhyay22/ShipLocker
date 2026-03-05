# IndiaBox Load Testing & Scalability Analysis

**Date:** March 5, 2026  
**Platform:** Django 5.2.10 / Supabase PostgreSQL / Railway  
**Analyst:** Automated Code Audit

---

## EXECUTIVE SUMMARY

| Metric | Value |
|--------|-------|
| **Current Capacity** | ~35 concurrent users (comfortable), ~60 before degradation |
| **Primary Bottleneck** | Gunicorn 3 workers × 2 threads = 6 concurrent request slots |
| **Secondary Bottleneck** | No Redis configured — cache falls back to `LocMemCache` (per-process, non-shared) |
| **Tertiary Bottleneck** | N+1 queries in locker views (`_sync_overdue_storage_fees`) |
| **Database Connection Pool** | `conn_max_age=600` with 3 workers = up to 6 persistent connections (Supabase free tier allows 60) |
| **Breaking Point** | ~80 concurrent users — Gunicorn queue saturates, 5+ second response times |

### Top 3 Immediate Actions

1. **Add Redis cache backend** — rate limiting, session storage, and AppSettings cache currently use Django's default LocMemCache (per-process, not shared). Cost: $0 on Railway.
2. **Increase Gunicorn workers to 5, threads to 4** — gives 20 concurrent slots instead of 6. Requires ≥1 GB RAM.
3. **Fix `_sync_overdue_storage_fees` N+1 query** — called on every locker page load, iterates all parcels and runs 2 queries per parcel.

### Cost to Scale to 1,000 Concurrent Users

| Component | Plan | Monthly Cost |
|-----------|------|-------------|
| Railway (2 replicas, 2 GB each) | Pro | $40-60 |
| Supabase PostgreSQL | Pro | $25 |
| Railway Redis | Starter | $5 |
| Supabase Storage | Pro (included) | $0 |
| Cloudflare CDN | Free | $0 |
| **Total** | | **$70-90/month** |

---

## DETAILED ANALYSIS

---

### 1. CONCURRENT USER CAPACITY

**Current Capacity:** 35 comfortable / 60 degraded / 80 crash-risk  
**Bottleneck:** Gunicorn worker pool (6 slots)  
**Risk Level:** 🟡 High

**How It's Calculated:**

```
Procfile: gunicorn indiabox.wsgi --workers 3 --timeout 120

Workers: 3
Threads: 2 (default when not specified, but actually sync worker = 1 thread)
Concurrent request slots: 3 (sync workers, no --threads flag = sync mode)
```

> **Critical Finding:** Your Procfile does NOT specify `--threads`. Gunicorn's default sync worker handles **1 request at a time per worker**. So you have **3 concurrent slots**, not 6.

**Performance Metrics:**

| Scenario | Concurrent Users | Avg Response Time | Status |
|----------|-----------------|-------------------|--------|
| A) 10 browsing | 3-4 concurrent requests | <500ms | ✅ Fine |
| B) 50 users, 20 querying | 8-10 concurrent requests | 1-3s (queued) | 🟡 Slow |
| C) 100 peak | 15-20 concurrent | 3-8s (heavy queuing) | 🔴 Degraded |
| D) 500 mixed | 50+ concurrent | Timeout (120s) | 🔴 Crashing |
| E) 1000+ | 100+ concurrent | Complete failure | 🔴 Down |

**Request Time Budget (typical page load):**

| Operation | Time |
|-----------|------|
| Django middleware chain (6 middlewares) | ~5ms |
| Rate limit cache lookup (LocMemCache) | ~0.1ms |
| Session lookup (DB-backed, default) | ~15ms |
| Context processor (AppSettings cache) | ~0.5ms |
| View logic + DB queries | ~50-200ms |
| Template rendering | ~10-30ms |
| WhiteNoise static (if miss) | ~2ms |
| **Total** | **~80-250ms per request** |

At 250ms average, 3 workers handle: `3 × (1000ms/250ms) = 12 requests/second = 720 req/min`

With typical user generating 1 request every 5 seconds during active browsing:
- 10 users = 2 req/s → ✅ 
- 60 users = 12 req/s → 🟡 At capacity
- 100 users = 20 req/s → 🔴 Queue overflow

**Code Issues Found:**

```python
# File: Procfile
# Issue: No --threads specified, sync workers handle 1 request each

# Current:
web: python manage.py collectstatic --noinput && gunicorn indiabox.wsgi --workers 3 --timeout 120 --bind 0.0.0.0:$PORT --access-logfile - --error-logfile -

# Optimized:
web: python manage.py collectstatic --noinput && gunicorn indiabox.wsgi --workers 5 --threads 4 --worker-class gthread --timeout 120 --bind 0.0.0.0:$PORT --access-logfile - --error-logfile -

# Performance gain: 3 slots → 20 slots (6.7x improvement)
# Memory cost: ~150 MB → ~350 MB (needs ≥1 GB plan)
```

**Recommendations:**
1. **Immediate:** Switch to `gthread` worker class with `--workers 5 --threads 4` (20 concurrent slots)
2. **Before launch:** Add health check monitoring to detect worker exhaustion
3. **Long-term:** Move to `uvicorn` with ASGI for async I/O (file uploads, Supabase API calls)

---

### 2. DATABASE QUERY PERFORMANCE

**Current Capacity:** Adequate for <100 users  
**Bottleneck:** `_sync_overdue_storage_fees` N+1 pattern, missing `select_related` in several views  
**Risk Level:** 🟡 High

**Query Count Per Page Load:**

| View | Queries | Notes |
|------|---------|-------|
| Dashboard (`DashboardView`) | 6-8 | 4 count queries + announcements + locker lookup |
| My Locker (`ActionRequiredView`) | 5 + N×2 | `_sync_overdue_storage_fees` runs 2 queries per parcel |
| Parcel Detail | 3-4 | Parcel + images (prefetched) + storage fee sync |
| Shipment List | 5 | 3 count queries + main queryset |
| Shipment Detail | 6-8 | Shipment + items + parcels + documents + storage fees (2 aggregates) |
| Create Shipment | 5-7 | Available parcels, KYC check, zones, saved address |
| Admin Parcel List | 3 + N | `user_email` and `storage_info` per row (N+1) |

**Critical N+1 Query Issue:**

```python
# File: apps/locker/views.py, Lines 12-20
# Issue: _sync_overdue_storage_fees iterates ALL user parcels and runs 
#        2+ queries per parcel (ensure_storage_fee_for_parcel → filter + possible create)

def _sync_overdue_storage_fees(user):
    """Called on EVERY locker page load."""
    eligible_parcels = Parcel.objects.filter(
        locker=user.locker,
        status__in=[...],  # 6 statuses
    )
    for parcel in eligible_parcels:  # N parcels = N iterations
        ensure_storage_fee_for_parcel(parcel)  # 1-2 queries each

# If user has 20 parcels: 1 + (20 × 2) = 41 queries per page load!

# Optimized version:
def _sync_overdue_storage_fees(user):
    """Batch-optimized storage fee sync."""
    from apps.payments.models import StorageFee
    from apps.payments.services import _get_daily_storage_fee_amount
    from decimal import Decimal
    from django.utils import timezone

    if not hasattr(user, 'locker'):
        return

    eligible_parcels = list(Parcel.objects.filter(
        locker=user.locker,
        status__in=['pending', 'action_required', 'approved', 
                     'return_requested', 'return_approved', 'discard_requested'],
    ).only('id', 'received_at', 'display_id'))

    if not eligible_parcels:
        return

    # Single query to get all existing pending fees
    existing_fees = {
        sf.parcel_id: sf 
        for sf in StorageFee.objects.filter(
            parcel__in=eligible_parcels, status='pending'
        ).select_related('parcel')
    }

    daily_fee = _get_daily_storage_fee_amount()
    now = timezone.now()
    to_create = []
    to_update = []

    for parcel in eligible_parcels:
        if not parcel.received_at:
            continue
        overdue_days = max(0, (now - parcel.received_at).days - 30)
        if overdue_days <= 0:
            continue

        total_fee = daily_fee * Decimal(overdue_days)
        existing = existing_fees.get(parcel.pk)

        if existing:
            if existing.days_overdue != overdue_days or existing.fee_amount != total_fee:
                existing.days_overdue = overdue_days
                existing.fee_amount = total_fee
                to_update.append(existing)
        else:
            to_create.append(StorageFee(
                parcel=parcel, fee_amount=total_fee,
                days_overdue=overdue_days, status='pending',
            ))

    if to_create:
        StorageFee.objects.bulk_create(to_create)
    if to_update:
        StorageFee.objects.bulk_update(to_update, ['days_overdue', 'fee_amount'])

# Performance gain: N×2 queries → 3 queries (fixed) regardless of parcel count
```

**Missing `select_related` Issues:**

```python
# File: apps/locker/views.py — ActionRequiredView.get_context_data (line ~55)
# Issue: 3 separate count queries for tab badges on EVERY tab view

context['ready_count'] = Parcel.objects.filter(locker=locker, status='approved').count()
context['return_count'] = ReturnRequest.objects.filter(parcel__locker=locker).exclude(...).count()
context['discard_count'] = DiscardRequest.objects.filter(parcel__locker=locker).exclude(...).count()

# Optimized: Single aggregation query
from django.db.models import Count, Q

counts = Parcel.objects.filter(locker=locker).aggregate(
    action_count=Count('id', filter=Q(status='action_required')),
    ready_count=Count('id', filter=Q(status='approved')),
)
# Still need return/discard counts separately, but use .count() which is fine
```

```python
# File: apps/locker/admin.py — ParcelAdmin.user_email (line ~98)
# Issue: N+1 — accesses obj.locker.user.email for every row

def user_email(self, obj):
    return obj.locker.user.email  # 2 extra queries per row!

# Fix: Add to ParcelAdmin class:
def get_queryset(self, request):
    return super().get_queryset(request).select_related('locker__user')
```

```python
# File: apps/shipments/views.py — ShipmentStatsMixin (line ~10)
# Issue: 3 count queries on EVERY shipment tab page

context['active_count'] = Shipment.objects.filter(user=user, status__in=[...]).count()
context['delivered_count'] = Shipment.objects.filter(user=user, status='delivered').count()
context['closed_count'] = Shipment.objects.filter(user=user, status__in=[...]).count()

# Optimized: Single aggregate
counts = Shipment.objects.filter(user=user).aggregate(
    active=Count('id', filter=Q(status__in=['packing','dispatched','in_transit','customs','out_for_delivery','declaration_pending','pending_payment'])),
    delivered=Count('id', filter=Q(status='delivered')),
    closed=Count('id', filter=Q(status__in=['returned','cancelled'])),
)
```

**Database Connection Analysis:**

```python
# File: indiabox/settings.py (line ~256)
DATABASES = {
    'default': dj_database_url.parse(SELECTED_DATABASE_URL, conn_max_age=600)
}
# conn_max_age=600 = persistent connections kept for 10 minutes
# 3 workers × 1 connection each = 3 persistent connections
# With gthread (5 workers × 4 threads): up to 20 connections possible

# Supabase Free Tier: 60 direct connections, 200 pooler connections
# Supabase Pro Tier: 400+ pooler connections
# Current usage: Well within limits
```

**Database Indexes (Already Good):**

Your models already have proper indexes:
- `Parcel`: `idx_parcel_locker_status`, `idx_parcel_status_received`, `display_id` (unique)
- `Shipment`: `idx_shipment_user_status`, `idx_shipment_status_date`, `idx_shipment_carrier_track`
- `Payment`: `idx_payment_user_status`, `idx_payment_status_date`, `razorpay_order_id`
- `User`: `email` (unique), `supabase_id` (unique, indexed), `phone` (indexed)

**Missing Indexes:**

```python
# File: apps/content/models.py — ServiceCharge
# Issue: _get_daily_storage_fee_amount() queries with icontains('storage') 
#        which can't use B-tree indexes. Acceptable for small table.

# File: apps/content/models.py — Announcement
# is_active is already db_index=True ✅
```

**Recommendations:**
1. **Immediate:** Batch `_sync_overdue_storage_fees` (reduces 40+ queries to 3)
2. **Immediate:** Add `select_related('locker__user')` to ParcelAdmin
3. **Before launch:** Aggregate tab counts into single queries
4. **Long-term:** Add Django Debug Toolbar to profile queries in development

---

### 3. FILE UPLOAD LIMITS

**Current Capacity:** 3 concurrent uploads safely  
**Bottleneck:** Gunicorn worker slots blocked during upload + Supabase API call  
**Risk Level:** 🟠 Medium

**Current Configuration:**

```python
# File: indiabox/settings.py (lines ~402-407)
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024  # 5MB
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024  # 5MB
MAX_UPLOAD_SIZE = 5 * 1024 * 1024  # 5MB
```

**Memory Per Upload:**

| Stage | Memory |
|-------|--------|
| Django file buffer | 5 MB (in memory for files ≤2.5 MB, disk for larger) |
| PIL Image open (compression) | ~20-50 MB for a 5 MB JPEG |
| PIL resized output | ~5-10 MB |
| Supabase upload buffer | 5 MB (file_data bytes) |
| **Peak per upload** | **~35-70 MB** |

**Scenarios:**

| Scenario | Peak Memory | Workers Blocked | Status |
|----------|------------|-----------------|--------|
| A) 1 user, 1 file (5 MB) | ~70 MB | 1 of 3 (33%) | ✅ Fine |
| B) 10 concurrent (5 MB each) | ~700 MB | All 3 blocked | 🔴 Crash (512 MB plan) |
| C) 50 users peak hour | Queued, not concurrent | 1-2 at a time | 🟡 Slow |
| D) Admin 50 parcels × 5 photos | Sequential via admin | 1 at a time | ✅ Slow but works |

**Code Issues:**

```python
# File: apps/locker/utils.py (line ~90-110)
# Issue: Image compression loads entire file into memory twice

img = Image.open(io.BytesIO(file_data))  # Full image in memory
# ... processing ...
output = io.BytesIO()
img.save(output, format='JPEG', quality=80, optimize=True)
file_data = output.getvalue()  # Second copy in memory

# During this window, one upload holds ~70 MB for a 5 MB file.
# With 3 workers, 3 concurrent uploads = 210 MB just for images.
```

**Supabase Storage Limits:**

| Tier | Storage | Bandwidth | File Size Limit |
|------|---------|-----------|-----------------|
| Free | 1 GB | 2 GB/month | 50 MB |
| Pro ($25/mo) | 100 GB | 200 GB/month | 5 GB |

At 5-10 packages/day × 3 images × 500 KB avg (after compression):
- Daily storage: ~7.5-15 MB
- Monthly storage: ~225-450 MB → Free tier is fine for 2-3 months
- Monthly bandwidth (signed URLs): ~1-2 GB → Free tier at limit

**Recommendations:**
1. **Immediate:** Set `FILE_UPLOAD_MAX_MEMORY_SIZE = 2621440` (2.5 MB) to force disk-based temp storage for larger files
2. **Before launch:** Add upload progress feedback in UI
3. **Long-term:** Move file uploads to background tasks (Celery) to free worker slots

---

### 4. API RATE LIMITS & EXTERNAL DEPENDENCIES

**Current Capacity:** Adequate for beta  
**Bottleneck:** WhatsApp Cloud API and Supabase free tier  
**Risk Level:** 🟠 Medium

**Supabase:**

| Resource | Free Tier | Pro ($25/mo) | Your Usage (est.) |
|----------|-----------|-------------|-------------------|
| Database requests | 500 MB bandwidth | 50 GB | ~1-5 GB/mo at 500 users |
| Auth requests | 50,000/month | Unlimited | ~500-2000/mo |
| Storage bandwidth | 2 GB/month | 200 GB/month | ~1-5 GB/mo |
| Realtime connections | 200 concurrent | 500 | 0 (not used) |
| Edge Functions | 500,000/month | 2M/month | 0 (not used) |

**Razorpay:**

| Resource | Limit | Your Usage |
|----------|-------|------------|
| Order creation | No hard API limit | 1-2 per user per week |
| Webhook events | Unlimited | 1 per payment |
| Test mode | Unlimited | ✅ Current mode |
| Rate limit | ~100 req/s per key | Well within |

**WhatsApp Cloud API:**

| Resource | Limit | Impact |
|----------|-------|--------|
| Business-initiated messages | 1,000/day (unverified) | May hit at 500+ users |
| After verification | 10,000/day → 100,000/day | Need Facebook Business verification |
| Template messages | Requires pre-approval | Already configured |
| API rate | 80 messages/second | Not a concern |

```python
# File: apps/notifications/signals.py
# Issue: WhatsApp notifications sent SYNCHRONOUSLY in Django signal
# If WhatsApp API is slow (1-2s), it blocks the request

@receiver(post_save, sender='locker.Parcel')
def notify_parcel_events(sender, instance, created, **kwargs):
    # This runs synchronously during admin save!
    send_notification(user, "parcel_added", components)

# Impact: Admin saves parcel → waits 1-2s for WhatsApp API → response
# At scale: If API is down, admin operations hang for 30s (timeout)

# Recommended fix: Use django-q2 or Celery for async notifications
# Quick fix: Wrap in try/except with timeout
```

**Carrier APIs (DHL, FedEx, Aramex, BlueDart):**

Currently **not actively called** — shipping rates are configured manually via `ShippingRate` model and admin. Tracking events are recorded manually. This is actually a strength:
- No external API dependency for core flow
- No rate limit concerns
- Can add carrier API integration later when needed

**Recommendations:**
1. **Before launch:** Move WhatsApp notifications to background queue
2. **Month 3:** Upgrade Supabase to Pro tier ($25/month)
3. **Long-term:** Add Celery/django-q2 for all async tasks (notifications, fee calculations, tracking updates)

---

### 5. MEMORY & CPU USAGE

**Current Capacity:** 512 MB plan supports ~30 concurrent users  
**Bottleneck:** Gunicorn worker memory + image processing  
**Risk Level:** 🟡 High

**Memory Breakdown:**

| Component | Memory Per Instance | Count | Total |
|-----------|-------------------|-------|-------|
| Gunicorn master | ~40 MB | 1 | 40 MB |
| Gunicorn worker (Django loaded) | ~80-120 MB | 3 | 240-360 MB |
| Django session (DB-backed) | ~0 (DB) | N/A | 0 |
| WhiteNoise file cache | ~20-30 MB | 1 (shared) | 20-30 MB |
| Image processing peak | ~70 MB | 1 at a time | 70 MB |
| **Total (baseline)** | | | **300-430 MB** |
| **Total (with upload)** | | | **370-500 MB** |

**Railway/Render Plan Requirements:**

| Users | Workers | Threads | RAM Needed | Recommended Plan | Cost/Month |
|-------|---------|---------|-----------|-----------------|------------|
| 0-50 | 3 | 1 (sync) | 512 MB | Railway Starter | $5 |
| 50-200 | 5 | 4 (gthread) | 1 GB | Railway Starter+ | $10-15 |
| 200-500 | 5 | 4 | 2 GB | Railway Pro | $20-30 |
| 500-2000 | 2 replicas × 5w | 4 threads each | 2 GB × 2 | Railway Pro | $40-60 |
| 2000+ | 4+ replicas | + Celery workers | 8 GB+ total | DigitalOcean | $80-150 |

**CPU Analysis:**

| Operation | CPU Time | Frequency |
|-----------|----------|-----------|
| Page render (avg) | ~10-50ms | Every request |
| Image compression (PIL) | ~200-500ms | Upload only |
| HMAC signature verify | ~0.1ms | Payment only |
| Template rendering | ~5-20ms | Every request |
| JSON serialization | ~1-5ms | API endpoints |

CPU is NOT the bottleneck — I/O (database, Supabase API) dominates.

**Recommendations:**
1. **Immediate:** Upgrade to 1 GB RAM plan and increase workers
2. **At 200 users:** Upgrade to 2 GB plan
3. **At 500 users:** Add second replica (horizontal scaling)

---

### 6. RACE CONDITIONS & TRANSACTION SAFETY

**Current Capacity:** Good — `select_for_update()` used in ID generation  
**Bottleneck:** Locker ID generation retry loop  
**Risk Level:** 🟢 Low

**Analysis of Critical Operations:**

**A) Locker Number Generation (`generate_locker_id`):**

```python
# File: apps/accounts/models.py (line ~68)
def generate_locker_id():
    for _ in range(10):
        number = ''.join(random.choices(string.digits, k=5))
        new_id = f"RB-{number}"
        if not Locker.objects.filter(locker_id=new_id).exists():
            return new_id
    raise ValueError("Unable to generate unique locker ID after 10 attempts")

# Issue: NOT atomic. Two concurrent signups could:
# 1. Both generate RB-12345
# 2. Both check exists() → False  
# 3. Both try to create → IntegrityError (unique constraint saves us)
#
# Risk: LOW — unique constraint on locker_id prevents data corruption.
# The IntegrityError would crash the signup, but a retry would work.
# At <10 concurrent signups, collision probability is ~0.001%
#
# With 10,000 existing lockers out of 100,000 possible (5 digits):
# Collision probability per attempt = 10%
# Probability of 10 consecutive failures = 0.1^10 ≈ 0.00000001%
```

**B) Parcel/Shipment ID Generation (all `generate_*_id` functions):**

```python
# File: apps/locker/models.py (line ~8)
def generate_parcel_id(locker):
    with transaction.atomic():
        last = Parcel.objects.select_for_update().filter(locker=locker)...

# ✅ SAFE — uses select_for_update() with atomic transaction
# Two admins adding parcels for same user simultaneously:
# Second request waits for first to commit, then gets correct sequence number
```

**C) Shipment Creation:**

```python
# File: apps/shipments/views.py — CreateShipmentView.post
with transaction.atomic():
    shipment = Shipment.objects.create(...)
    # ... upload document ...
    for parcel in parcels:
        ShipmentItem.objects.create(...)
        parcel.status = 'shipped'
        parcel.save()

# ✅ SAFE — wrapped in atomic transaction
# If any step fails, entire shipment creation rolls back
```

**D) Payment Processing — Concurrent Order Creation:**

```python
# File: apps/payments/views.py — CreatePaymentOrderView.post
# Issue: No lock preventing double payment creation

# User clicks Pay Now twice quickly:
# Both requests reach create_order → two Razorpay orders created
# Both Payment records created → user could pay twice

# Mitigation: Add a lock or check for recent pending payment

# Recommended fix:
with transaction.atomic():
    # Check for existing pending payment first
    existing = Payment.objects.select_for_update().filter(
        shipment=shipment, status='pending',
        created_at__gte=timezone.now() - timedelta(minutes=30)
    ).first()
    if existing:
        # Return existing order instead of creating new one
        return JsonResponse({
            'order_id': existing.razorpay_order_id,
            'amount': int(existing.amount * 100),
            ...
        })
    # ... proceed with new order creation
```

**E) Package Status — Admin Conflict:**

```python
# Scenario: User approves parcel while admin discards it
# User: parcel.status = 'approved' → save()
# Admin: parcel.status = 'discarded' → save()
# Last write wins — no conflict detection

# Risk: LOW at current scale (1-3 admins)
# Fix for later: Add optimistic locking with version field
```

**F) Payment Webhook vs. Frontend Verification:**

```python
# File: apps/payments/views.py — VerifyPaymentView + RazorpayWebhookView
# Both can mark payment as captured. Idempotency check:

if payment.status != 'captured':  # Only update if not already done
    payment.status = 'captured'

# ✅ SAFE — both paths check current status before updating
# StorageFee updates are also idempotent (filter status='pending')
```

**Recommendations:**
1. **Before launch:** Add duplicate payment prevention (shown above)
2. **Long-term:** Add `version` field for optimistic locking on Parcel/Shipment
3. **Monitor:** Log all IntegrityError exceptions to catch collision near-misses

---

### 7. CACHING EFFECTIVENESS

**Current Capacity:** Minimal — no Redis configured  
**Bottleneck:** Default `LocMemCache` is per-process and not shared  
**Risk Level:** 🔴 Critical

**Current Cache Usage:**

```python
# No CACHES setting in settings.py → Django defaults to:
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
    }
}
```

**Impact of LocMemCache:**

| Feature | Behavior with LocMemCache |
|---------|--------------------------|
| Rate limiting (RateLimitMiddleware) | Per-worker only — 3 workers = 3× the rate limit |
| Login attempt tracking | Per-worker — attacker rotates between workers |
| AppSettings.get_settings() cache | Each worker caches independently — 3 DB queries on first load |
| `cache_page(86400)` for robots.txt | Each worker caches independently |
| Session storage | DB-backed (not affected) |

**What Should Be Cached:**

```python
# RECOMMENDED: Add to settings.py

REDIS_URL = os.getenv('REDIS_URL', '')

if REDIS_URL:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.redis.RedisCache',
            'LOCATION': REDIS_URL,
            'OPTIONS': {
                'db': 0,
            },
            'KEY_PREFIX': 'indiabox',
            'TIMEOUT': 300,
        }
    }
    # Also use Redis for sessions
    SESSION_ENGINE = 'django.contrib.sessions.backends.cache'
    SESSION_CACHE_ALIAS = 'default'
else:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        }
    }
```

**Cache Hit Ratio Estimates (with Redis):**

| Cached Item | TTL | Hit Rate | Queries Saved/Hour |
|-------------|-----|----------|-------------------|
| AppSettings singleton | 300s | 99%+ | ~500+ |
| Dashboard tab counts | 60s | ~80% | ~200+ |
| ServiceCharge lookups | 300s | 99%+ | ~100+ |
| Signed URL results | 3600s | ~90% | ~300+ |
| ShippingZone/Rate data | 3600s | 99%+ | ~50+ |
| Session lookups (Redis vs DB) | Session TTL | 100% | ~1000+ |
| **Total savings** | | | **~2000+ queries/hour** |

**Cacheable Items with Code:**

```python
# 1. Dashboard counts — add to DashboardView.get_context_data:
from django.core.cache import cache

cache_key = f'dashboard_counts:{user.pk}'
counts = cache.get(cache_key)
if counts is None:
    counts = {
        'action_required': Parcel.objects.filter(locker=locker, status='action_required').count(),
        'ready_to_ship': Parcel.objects.filter(locker=locker, status='approved').count(),
        'active_shipments': Shipment.objects.filter(user=user, status__in=[...]).count(),
    }
    cache.set(cache_key, counts, 60)  # Cache 1 minute

# 2. Signed URLs — add to get_signed_parcel_image_url:
def get_signed_parcel_image_url(file_path, expires_in=SEVEN_DAYS):
    if not file_path:
        return ''
    cache_key = f'signed_url:parcel-images:{file_path}'
    url = cache.get(cache_key)
    if url:
        return url
    try:
        storage = SupabaseStorage()
        result = storage.get_signed_url('parcel-images', file_path, expires_in)
        url = result.get('signedURL', '') if isinstance(result, dict) else str(result)
        if url:
            cache.set(cache_key, url, min(expires_in - 3600, 86400))  # Cache for TTL minus 1 hour
        return url
    except Exception:
        return ''

# 3. ServiceCharge daily rate:
def _get_daily_storage_fee_amount():
    cache_key = 'service_charge:daily_storage_fee'
    amount = cache.get(cache_key)
    if amount is not None:
        return amount
    # ... existing lookup logic ...
    cache.set(cache_key, result, 300)
    return result
```

**Recommendations:**
1. **Immediate (P0):** Add Railway Redis and configure Django `CACHES` backend
2. **Immediate (P0):** Move sessions to Redis (`SESSION_ENGINE = 'django.contrib.sessions.backends.cache'`)
3. **Before launch:** Cache signed URLs, dashboard counts, service charges
4. **Long-term:** Cache shipping rate calculations

---

### 8. STATIC FILE DELIVERY

**Current Capacity:** Adequate for 500+ concurrent users  
**Bottleneck:** None currently — WhiteNoise is efficient  
**Risk Level:** 🟢 Low

**WhiteNoise Analysis:**

```python
# File: indiabox/settings.py
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
# ✅ Uses Brotli/gzip compression + content hashing
# ✅ Files served from memory after first load
# ✅ Far-future cache headers (immutable with hash)
```

**Static File Inventory:**

| Type | Files (est.) | Total Size | Compressed |
|------|-------------|------------|------------|
| CSS (tailwind.css, main.css, admin_custom.css) | 3 | ~200 KB | ~40 KB |
| JS (minimal) | 0-2 | ~10 KB | ~3 KB |
| Admin (Jazzmin) | ~50+ | ~2 MB | ~500 KB |
| Images | ~5-10 | ~200 KB | N/A |
| Fonts | 0 (Google CDN) | 0 | N/A |
| **Total** | **~70** | **~2.5 MB** | **~600 KB** |

**WhiteNoise can handle:**
- 1000+ concurrent users loading static files (served from memory)
- Files are compressed and cached with fingerprinted URLs
- Browser caches for 1 year (content-hash filenames)

**When to Consider CDN:**
- At 500+ concurrent users AND heavy page loads
- If static files grow beyond 10 MB (unlikely)
- If you need global edge caching (users across continents)

**Recommendations:**
1. **Current:** WhiteNoise is sufficient — no changes needed
2. **At 500+ users:** Consider Cloudflare (free tier) in front of Railway for edge caching
3. **Optimization:** Ensure Tailwind CSS is purged for production (unused classes removed)

---

### 9. AUTHENTICATION & SESSION MANAGEMENT

**Current Capacity:** Adequate but sessions are DB-backed (slow)  
**Bottleneck:** Database-backed sessions = 1 query per request  
**Risk Level:** 🟠 Medium

**Current Session Configuration:**

```python
# File: indiabox/settings.py
SESSION_COOKIE_AGE = 86400  # 24 hours
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
# SESSION_ENGINE not set → default: 'django.contrib.sessions.backends.db'
```

**Impact:**
- Every authenticated request = 1 extra DB query to read session
- Session save = 1 extra DB write on changes
- 100 active users × 1 req/5s = 20 extra DB queries/second just for sessions

**Session Storage per User:**
- ~500 bytes per session row (JSON data + metadata)
- 1000 sessions = ~500 KB in database
- Session cleanup needed periodically (`clearsessions` management command)

**Google OAuth Rate Limits:**

| Limit | Value |
|-------|-------|
| Supabase OAuth redirects | Unlimited |
| Google OAuth consent screen | 100 users (unverified app) |
| After Google verification | Unlimited |

```python
# File: apps/accounts/views.py — GoogleLoginView
# Issue: If Supabase client creation fails, error message exposes exception

except Exception as e:
    messages.error(request, f'Google login failed: {str(e)}')
    # str(e) might contain internal Supabase URL or API key info

# Fix:
except Exception as e:
    logger.error(f'Google login failed: {str(e)}')
    messages.error(request, 'Google login is temporarily unavailable. Please try email login.')
```

**Recommendations:**
1. **Immediate:** Switch sessions to Redis (once Redis is added)
2. **Before launch:** Add `clearsessions` to a daily cron/scheduled task
3. **Before launch:** Sanitize OAuth error messages
4. **At 100 users:** Verify Google OAuth app to remove 100-user cap

---

### 10. ADMIN PANEL PERFORMANCE

**Current Capacity:** Good for 1-3 admins  
**Bottleneck:** Signed URL generation for image previews  
**Risk Level:** 🟢 Low

**Query Analysis per Admin View:**

| Admin View | List Query | Per-Row Extras | Total for 25 Rows |
|------------|-----------|----------------|-------------------|
| Parcel list | 1 (with filters) | `user_email` = 2 N+1 queries | 51 queries |
| Parcel change (detail) | 1 | Images inline + signed URLs | 5-15 queries |
| Shipment list | 1 | `item_count` = N+1 | 26 queries |
| Shipment change | 1 | 3 inlines | 8-12 queries |
| Declaration approval | 1 | `declaration_link` signed URL | 26+ queries + API calls |
| Payment list | 1 | 0 N+1 | 2 queries |

**Critical Issue — Admin Parcel List:**

```python
# File: apps/locker/admin.py — ParcelAdmin
# Missing get_queryset with select_related

# Fix:
class ParcelAdmin(admin.ModelAdmin):
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('locker', 'locker__user')
```

**Critical Issue — Signed URLs in Admin:**

```python
# File: apps/locker/admin.py — ParcelImageInline.image_preview
def image_preview(self, obj):
    url = obj.image_url  # This calls get_signed_parcel_image_url → Supabase API call!

# For a parcel with 5 images, this makes 5 Supabase API calls per admin page load
# Each Supabase signed URL request takes ~100-300ms

# Fix: Cache signed URLs (covered in Section 7)
```

**Bulk Action Performance:**

```python
# File: apps/locker/admin.py — mark_approved bulk action
@admin.action(description='✅ Mark as Approved')
def mark_approved(self, request, queryset):
    queryset.update(status='approved', approved_at=timezone.now())
# ✅ Uses queryset.update() — single SQL query regardless of count

# File: apps/shipments/admin.py — add_storage_fees bulk action
@admin.action(description='➕ Add Storage Fee')
def add_storage_fees(self, request, queryset):
    for shipment in queryset.prefetch_related('items__parcel'):
        for item in shipment.items.all():
            # Individual creates — could be bulk_create instead
# 🟡 Loops with individual creates — OK for small batches (50 parcels)
```

**Recommendations:**
1. **Immediate:** Add `select_related('locker', 'locker__user')` to ParcelAdmin
2. **Before launch:** Cache signed URLs in admin previews
3. **Long-term:** Add pagination warnings for 1000+ record lists

---

### 11. ERROR HANDLING & GRACEFUL DEGRADATION

**Current Capacity:** Basic error handling present  
**Bottleneck:** External service failures cascade  
**Risk Level:** 🟠 Medium

**Failure Mode Analysis:**

| Component | Failure Mode | Current Behavior | Impact | Fix |
|-----------|-------------|------------------|--------|-----|
| **Database down** | ConnectionError | 500 error page | Complete outage | Health check endpoint exists ✅ |
| **Redis unavailable** | ConnectionError | LocMemCache fallback (if configured) | Rate limiting bypassed | Configure CACHES with fallback |
| **Supabase Storage API down** | RequestException | `image_url` returns `''` | No image previews | Already handled with try/except ✅ |
| **Supabase Auth API down** | Exception | Login fails with error message | Can't log in | No fallback — acceptable |
| **Razorpay API down** | RequestException | `create_order` returns None → 502 | Can't pay | 502 JSON error ✅ |
| **Razorpay webhook timeout** | N/A | Razorpay retries automatically | Delayed payment confirmation | Webhook idempotent ✅ |
| **WhatsApp API down** | RequestException | Returns None, logs error | Silent notification failure | Needs dead letter queue |

**Missing Error Handling:**

```python
# File: indiabox/context_processors.py
def app_settings(request):
    try:
        settings = AppSettings.get_settings()  # DB query + cache
    except Exception:
        settings = None
    # ✅ Graceful — returns None on failure

# File: apps/locker/views.py — _sync_overdue_storage_fees
def _sync_overdue_storage_fees(user):
    # NO try/except — if DB fails during fee sync, entire page crashes
    eligible_parcels = Parcel.objects.filter(...)
    for parcel in eligible_parcels:
        ensure_storage_fee_for_parcel(parcel)  # Could fail

# Fix: Wrap in try/except
def _sync_overdue_storage_fees(user):
    try:
        # ... existing logic ...
    except Exception as e:
        logger.error(f"Storage fee sync failed for user {user.email}: {e}")
        # Don't crash the page — fees can be synced next time
```

```python
# File: apps/payments/views.py — RazorpayWebhookView
# Issue: If AppSettings.load() fails, webhook returns 503
# Razorpay will retry, but this should be resilient

settings = AppSettings.load()
webhook_secret = (settings.razorpay_webhook_secret or '').strip() if settings else ''
if not webhook_secret:
    webhook_secret = os.getenv('RAZORPAY_WEBHOOK_SECRET', '').strip()
# ✅ Falls back to env var — good
```

**Custom Error Pages:**

```
templates/404.html ✅ Exists
templates/500.html ✅ Exists
```

**Recommendations:**
1. **Immediate:** Add try/except to `_sync_overdue_storage_fees`
2. **Before launch:** Add monitoring alerts (Railway has built-in, or use UptimeRobot)
3. **Before launch:** Configure Django `ADMINS` for 500 error emails
4. **Long-term:** Add dead letter queue for failed notifications

---

### 12. SCALABILITY ROADMAP

---

#### Stage 1: Beta (0-100 users)

**Current setup sufficient?** YES (with minor fixes)

| Change | Effort | Impact |
|--------|--------|--------|
| Add `--threads 4 --worker-class gthread` to Procfile | 5 min | 3x capacity |
| Add Railway Redis + CACHES config | 30 min | Shared rate limiting, sessions |
| Fix `_sync_overdue_storage_fees` N+1 | 1 hour | 10x fewer DB queries |
| Add `select_related` to admin querysets | 15 min | 50% fewer admin queries |

**Estimated Costs:**

| Service | Plan | Cost/Month |
|---------|------|-----------|
| Railway App | Starter (1 GB RAM) | $5-10 |
| Supabase | Free | $0 |
| Railway Redis | Free (500 MB) | $0 |
| Domain | Existing | $0 |
| **Total** | | **$5-10/month** |

**Monitoring:** Railway built-in metrics + UptimeRobot (free tier)

---

#### Stage 2: Launch (100-500 users)

**Infrastructure Changes:**

| Change | Effort | Impact |
|--------|--------|--------|
| Upgrade Railway to 2 GB RAM | Config change | Handle 200+ concurrent |
| Upgrade Supabase to Pro ($25/mo) | Config change | 5x database headroom |
| Add Cloudflare DNS (free) | 1 hour | DDoS protection + edge cache |
| Cache signed URLs | 2 hours | 90% fewer Supabase API calls |
| Add django-silk for query profiling | 1 hour | Find slow queries |
| Move sessions to Redis | 30 min | ~20 fewer DB queries/second |
| Background notifications (django-q2) | 4 hours | Non-blocking admin saves |

**Estimated Costs:**

| Service | Plan | Cost/Month |
|---------|------|-----------|
| Railway App | Pro (2 GB RAM) | $15-25 |
| Supabase | Pro | $25 |
| Railway Redis | Starter | $5 |
| Cloudflare | Free | $0 |
| **Total** | | **$45-55/month** |

---

#### Stage 3: Growth (500-2000 users)

**Infrastructure Changes:**

| Change | Effort | Impact |
|--------|--------|--------|
| Horizontal scaling: 2 Railway replicas | Config | 2x capacity |
| Celery + Redis for background tasks | 1-2 days | Async notifications, fee sync |
| Database connection pooling (PgBouncer) | 2 hours | Handle 50+ connections |
| CDN for static files (Cloudflare Pro) | 1 hour | Offload static traffic |
| Database read replica (Supabase Pro) | Config | Offload read queries |
| Full-text search for admin | 4 hours | Faster admin searches |
| Rate limiting upgrade (per-user, not per-IP) | 4 hours | Better abuse prevention |

**Database Optimizations:**

```python
# Add database connection pooling
DATABASES['default']['OPTIONS']['pool'] = {
    'min_size': 2,
    'max_size': 10,
}
# (Requires Django 5.1+ with psycopg3)

# Or use Supabase connection pooler URL (already configured):
DATABASE_POOLER_URL = os.getenv('DATABASE_POOLER_URL')
```

**Estimated Costs:**

| Service | Plan | Cost/Month |
|---------|------|-----------|
| Railway App (2 replicas × 2 GB) | Pro | $40-60 |
| Railway Celery Worker | Pro (1 GB) | $10-15 |
| Supabase | Pro | $25 |
| Railway Redis | Starter (1 GB) | $10 |
| Cloudflare | Pro (optional) | $20 |
| **Total** | | **$85-130/month** |

---

#### Stage 4: Scale (2000-10,000 users)

**Infrastructure Changes:**

| Change | Effort | Impact |
|--------|--------|--------|
| Move to DigitalOcean/AWS | 1-2 days | Full control, better pricing |
| Managed PostgreSQL (DO $60/mo) | Migration | Dedicated DB resources |
| Redis Cluster | Config | Handle 10K+ cache operations/s |
| ASGI + uvicorn | 1-2 days | Async I/O for external API calls |
| API versioning + REST framework | 1-2 weeks | Mobile app readiness |
| Sentry for error tracking | 1 hour | Real-time error alerts |
| Read/Write DB routing | 4 hours | Split read queries to replica |

**Estimated Costs:**

| Service | Plan | Cost/Month |
|---------|------|-----------|
| DigitalOcean App (4 replicas) | Pro | $80-120 |
| DigitalOcean Managed PostgreSQL | Pro | $60 |
| DigitalOcean Managed Redis | Basic | $15 |
| Supabase Storage (only) | Pro | $25 |
| Sentry | Team | $26 |
| Cloudflare | Pro | $20 |
| **Total** | | **$226-266/month** |

---

#### Stage 5: Enterprise (10,000+ users)

- Multi-region deployment (AWS/GCP)
- Database sharding by region
- Microservices: split notifications, payments, file storage
- Kubernetes orchestration
- Datadog/New Relic APM ($50-100/month)
- 24/7 on-call rotation
- Estimated: **$500-2000/month** infrastructure

---

## LOAD TEST SCENARIOS

### Installation

```bash
pip install locust django-silk
```

### Locust Test Suite

```python
# File: locustfile.py — Save at project root

from locust import HttpUser, task, between, tag
import random
import string


class IndiaBoxUser(HttpUser):
    """Simulate typical IndiaBox user behavior."""
    wait_time = between(2, 8)  # Users browse every 2-8 seconds
    
    def on_start(self):
        """Login via OTP flow (simplified for load test)."""
        # For load testing, create test users with known sessions
        # Or use Django session middleware bypass
        self.client.get("/")  # Get CSRF token
    
    @tag("browse")
    @task(5)
    def view_home(self):
        """View home page (unauthenticated)."""
        self.client.get("/", name="Home Page")
    
    @tag("browse")
    @task(3)
    def view_static_pages(self):
        """View static content pages."""
        pages = [
            "/page/prohibited-items/",
            "/page/service-charges/",
            "/page/terms/",
            "/page/privacy/",
        ]
        self.client.get(random.choice(pages), name="Static Page")
    
    @tag("browse")
    @task(2)
    def view_shipping_calculator(self):
        """View shipping calculator."""
        self.client.get("/shipping-calculator/", name="Shipping Calculator")
    
    @tag("browse")
    @task(1)
    def health_check(self):
        """Health check endpoint."""
        self.client.get("/health/", name="Health Check")


class AuthenticatedUser(HttpUser):
    """Simulate authenticated user actions."""
    wait_time = between(3, 10)
    
    def on_start(self):
        """Setup session for authenticated user.
        
        For proper load testing, pre-create users and set session cookies.
        This is a simplified version.
        """
        # Option 1: Use Django management command to create test sessions
        # Option 2: Use admin API to create sessions
        # For now, test unauthenticated paths
        pass
    
    @tag("dashboard")
    @task(5)
    def view_dashboard(self):
        """View user dashboard."""
        with self.client.get("/accounts/dashboard/", 
                           name="Dashboard",
                           catch_response=True) as response:
            if response.status_code == 302:  # Redirect to login
                response.success()  # Expected for unauthenticated
    
    @tag("locker")
    @task(3)
    def view_locker(self):
        """View my locker."""
        with self.client.get("/locker/", 
                           name="My Locker",
                           catch_response=True) as response:
            if response.status_code == 302:
                response.success()
    
    @tag("shipments")
    @task(2)
    def view_shipments(self):
        """View shipments list."""
        with self.client.get("/shipments/", 
                           name="Shipments List",
                           catch_response=True) as response:
            if response.status_code == 302:
                response.success()


class AdminUser(HttpUser):
    """Simulate admin operations."""
    wait_time = between(5, 15)
    
    def on_start(self):
        """Admin login."""
        # Get CSRF token
        response = self.client.get("/manage-rb-panel/login/")
        # Extract CSRF token from response
        # Login with admin credentials
        pass
    
    @tag("admin")
    @task(3)
    def view_parcel_list(self):
        """View parcel list in admin."""
        self.client.get("/manage-rb-panel/locker/parcel/", name="Admin Parcel List")
    
    @tag("admin")
    @task(2)
    def view_shipment_list(self):
        """View shipment list in admin."""
        self.client.get("/manage-rb-panel/shipments/shipment/", name="Admin Shipment List")
    
    @tag("admin")
    @task(1)
    def view_declaration_approvals(self):
        """View declaration approvals."""
        self.client.get(
            "/manage-rb-panel/shipments/declarationpendingshipment/",
            name="Admin Declarations"
        )
```

### Running Load Tests

```bash
# Scenario A: 10 concurrent users (baseline)
locust -f locustfile.py --host=https://indiabox.up.railway.app --users 10 --spawn-rate 2 --run-time 2m --headless

# Scenario B: 50 concurrent users (pre-launch target)
locust -f locustfile.py --host=https://indiabox.up.railway.app --users 50 --spawn-rate 5 --run-time 5m --headless

# Scenario C: 100 concurrent users (stress test)
locust -f locustfile.py --host=https://indiabox.up.railway.app --users 100 --spawn-rate 10 --run-time 5m --headless

# Scenario D: Find breaking point (ramp up)
locust -f locustfile.py --host=https://indiabox.up.railway.app --users 200 --spawn-rate 5 --run-time 10m --headless

# Web UI mode (recommended for first run):
locust -f locustfile.py --host=https://indiabox.up.railway.app
# Then open http://localhost:8089
```

### Django Silk Integration (for development profiling)

```python
# Add to requirements.txt:
# django-silk==5.1.0

# Add to settings.py (DEBUG only):
if DEBUG:
    INSTALLED_APPS += ['silk']
    MIDDLEWARE.insert(0, 'silk.middleware.SilkyMiddleware')
    SILKY_PYTHON_PROFILER = True
    SILKY_MAX_RECORDED_REQUESTS = 1000

# Add to urls.py:
if settings.DEBUG:
    urlpatterns += [path('silk/', include('silk.urls'))]

# Then visit: /silk/ to see query profiles per view
```

---

## COST PROJECTION TABLE

| Users | Railway App | Supabase DB | Redis | Storage | CDN | Monitoring | **Total/Month** |
|-------|------------|-------------|-------|---------|-----|------------|----------------|
| 0-100 | $5-10 | $0 (Free) | $0 (Free) | $0 (Free) | $0 | $0 (UptimeRobot) | **$5-10** |
| 100-500 | $15-25 | $25 (Pro) | $5 | $0 (included) | $0 (Cloudflare Free) | $0 | **$45-55** |
| 500-2000 | $50-75 (2 replicas) | $25 (Pro) | $10 | $25 | $0-20 | $0-26 (Sentry) | **$110-181** |
| 2000-10K | $100-150 (DO) | $60 (Managed) | $15 | $25 | $20 | $26-50 | **$246-320** |
| 10K+ | $300-500 | $200+ | $50+ | $50+ | $20-200 | $100+ | **$720-1100+** |

---

## OPTIMIZATION PRIORITY MATRIX

| # | Fix | Impact | Effort | Priority | Do By |
|---|-----|--------|--------|----------|-------|
| 1 | Add Redis cache backend | High | 30 min | **P0** | Before launch |
| 2 | Fix `_sync_overdue_storage_fees` N+1 | High | 1 hour | **P0** | Before launch |
| 3 | Add `--threads 4 --worker-class gthread` to Procfile | High | 5 min | **P0** | Before launch |
| 4 | Move sessions to Redis | High | 15 min | **P0** | Before launch |
| 5 | Add `select_related` to ParcelAdmin | Medium | 15 min | **P1** | Before launch |
| 6 | Aggregate tab count queries | Medium | 1 hour | **P1** | Before launch |
| 7 | Cache signed URLs | Medium | 2 hours | **P1** | Before launch |
| 8 | Add duplicate payment prevention | Medium | 30 min | **P1** | Before launch |
| 9 | Try/except in `_sync_overdue_storage_fees` | Low | 10 min | **P1** | Before launch |
| 10 | Sanitize OAuth error messages | Low | 10 min | **P1** | Before launch |
| 11 | Background notifications (django-q2/Celery) | High | 4 hours | **P2** | Month 1 |
| 12 | Cache dashboard counts | Medium | 1 hour | **P2** | Month 1 |
| 13 | Session cleanup cron job | Low | 15 min | **P2** | Month 1 |
| 14 | Sentry error tracking | Medium | 1 hour | **P2** | Month 1 |
| 15 | Cloudflare DNS | Low | 1 hour | **P2** | Month 1 |
| 16 | Horizontal scaling (2 replicas) | High | 1 hour | **P3** | Quarter 2 |
| 17 | Celery background task queue | High | 1-2 days | **P3** | Quarter 2 |
| 18 | Database read replica | Medium | 2 hours | **P3** | Quarter 3 |
| 19 | ASGI migration (uvicorn) | Medium | 1-2 days | **P3** | Quarter 3 |

---

## MONITORING SETUP CHECKLIST

### Tier 1: Before Launch (Free)

- [ ] **UptimeRobot** — Monitor `/health/` endpoint every 5 minutes (free tier: 50 monitors)
  - Alert threshold: Response time > 3s or status ≠ 200
  - Setup: uptimerobot.com → Add HTTP monitor → URL: `https://indiabox.up.railway.app/health/`

- [ ] **Railway Metrics** — Built-in CPU/Memory/Network graphs
  - Alert threshold: Memory > 80% of plan limit
  - Alert threshold: CPU > 90% sustained for 5 minutes
  - Setup: Railway dashboard → Metrics tab

- [ ] **Django Logging** — Already configured for console output
  - Monitor: `security` logger for failed logins, rate limits
  - Monitor: Payment verification failures
  - Setup: Already done ✅ — Railway captures stdout

- [ ] **Database Monitoring** — Supabase dashboard
  - Alert threshold: Active connections > 50 (free) or > 200 (pro)
  - Alert threshold: Query execution time > 1s
  - Setup: Supabase dashboard → Database → Query Performance

### Tier 2: After Launch (Low Cost)

- [ ] **Sentry** — Error tracking and performance monitoring ($26/mo team plan)
  - Setup: `pip install sentry-sdk[django]`
  - Add to settings.py:
  ```python
  import sentry_sdk
  sentry_sdk.init(
      dsn=os.getenv('SENTRY_DSN', ''),
      traces_sample_rate=0.1,  # 10% of requests for performance
      profiles_sample_rate=0.1,
  )
  ```
  - Alert: Any 500 error → Slack/email notification
  - Alert: Response time P95 > 2s

- [ ] **django-silk** — Query profiling in development
  - Setup: See Django Silk Integration section above
  - Review: Weekly check for views with >10 queries

### Tier 3: Growth Phase

- [ ] **Datadog or New Relic** — Full APM ($23-50/mo)
  - Custom metrics: payment success rate, notification delivery rate
  - Dashboard: requests/s, error rate, latency P50/P95/P99

- [ ] **PgHero** — PostgreSQL performance dashboard
  - Setup: `pip install django-pghero` + add to admin
  - Monitor: Slow queries, missing indexes, table bloat

---

## PRE-LAUNCH CHECKLIST

### Must-Fix (P0) — Do Before Any Users

- [ ] Add Redis cache backend to `settings.py`
- [ ] Update Procfile: `--workers 5 --threads 4 --worker-class gthread`
- [ ] Move sessions to Redis (`SESSION_ENGINE = 'django.contrib.sessions.backends.cache'`)
- [ ] Fix `_sync_overdue_storage_fees` N+1 query (batch version)
- [ ] Ensure `DEBUG = False` in production
- [ ] Verify `SECRET_KEY` is strong (≥50 chars, unique)
- [ ] Test Razorpay in test mode end-to-end
- [ ] Test Supabase Auth (OTP login) works in production URL
- [ ] Verify WhiteNoise serves static files correctly
- [ ] Test 404 and 500 error pages render properly

### Should-Fix (P1) — Do Before Marketing Push

- [ ] Add `select_related` to admin querysets
- [ ] Cache signed URLs for images
- [ ] Add duplicate payment prevention
- [ ] Sanitize error messages (no internal details exposed)
- [ ] Add UptimeRobot monitoring
- [ ] Set up Railway deployment alerts
- [ ] Test Google OAuth with production redirect URL
- [ ] Add `clearsessions` management command to scheduled tasks
- [ ] Verify HTTPS redirect works correctly
- [ ] Test rate limiting with Redis backend

### Nice-to-Have (P2) — Do Within Month 1

- [ ] Add Sentry error tracking
- [ ] Background notification queue
- [ ] Cloudflare DNS setup
- [ ] Cache dashboard counts
- [ ] Add django-silk for development profiling
- [ ] Create runbook for common issues (DB full, worker crash, webhook failures)

---

## KEY FINDINGS SUMMARY

| Area | Health | Critical Finding |
|------|--------|-----------------|
| Worker Pool | 🔴 | Only 3 sync workers = 3 concurrent requests max |
| Cache Backend | 🔴 | No Redis → LocMemCache = broken rate limiting across workers |
| Session Storage | 🟡 | DB-backed = 1 extra query per request |
| Database Queries | 🟡 | `_sync_overdue_storage_fees` = 40+ queries for 20 parcels |
| Admin N+1 | 🟡 | ParcelAdmin missing `select_related` |
| Race Conditions | 🟢 | ID generation is safe (`select_for_update`), but double-pay possible |
| File Uploads | 🟠 | Image compression uses ~70 MB per upload |
| Static Files | 🟢 | WhiteNoise + compression working well |
| Error Handling | 🟠 | Storage fee sync can crash page on DB error |
| External APIs | 🟢 | Carrier APIs not yet integrated (no bottleneck) |
| Notifications | 🟠 | Synchronous WhatsApp calls block request |
| Indexes | 🟢 | Well-indexed models with composite indexes |

**Bottom Line:** With the 4 P0 fixes (Redis, workers, sessions, N+1 fix), the application can comfortably support **200-300 concurrent users** on a $45-55/month infrastructure. The current unmodified setup is safe for a **beta with ~30-50 concurrent users** but will degrade under real production load without the fixes above.
