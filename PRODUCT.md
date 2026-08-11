# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users
Overseas shoppers who need a local (India-based) virtual address to receive parcels from local retailers, then have those parcels inspected, consolidated, and forwarded internationally.

## Product Purpose
CamelTrunk (internal codename `indiabox`) is a Django-based international parcel forwarding platform. Users get a virtual locker address; warehouse staff receive, inspect, and photograph incoming parcels; users then approve, return, or discard them and request international shipment with KYC, customs declaration, payment, and multi-carrier tracking (Bluedart/DHL).

## Positioning
Full-service concierge, not just a locker/ship pipeline: alongside core parcel forwarding, CamelTrunk includes TrunkAssist, a personal shopping concierge feature (`apps/personal_shop`), and a membership/pricing plan model — going beyond what a generic forwarder (Shipito, MyUS-style) offers.

## Operating Context
- Locker lifecycle: parcel received → inspected → user approves/returns/discards → shipment requested.
- Shipment flow: customs declaration (ShipmentItem), Razorpay payment, KYC verification, carrier dispatch (Bluedart/DHL), tracking sync.
- Admin/warehouse staff operate through an obscured admin panel (`/manage-rb-panel/`, django-unfold) to process parcels and manage shipments.
- Membership/pricing plans exist as a confirmed capability (see `docs/superpowers` architecture specs) — undecided how tiers affect locker/shipping limits.

## Capabilities and Constraints
- Passwordless (OTP) auth via Supabase Auth integration; no stored passwords.
- File uploads (parcel images, KYC docs) go to Supabase Storage, private buckets — not local media.
- Runtime-configurable settings (warehouse address, payment keys, WhatsApp) live in `AppSettings`, editable by admins without redeploy.
- Security posture is a first-class constraint: rate limiting, ownership-enforced data access, CSP/security headers, audit logging via a dedicated `security` logger.
- No confirmed brand assets (logo, tagline, voice guide) yet — visual identity work starts from a blank slate.

## Brand Commitments
Public-facing name is **CamelTrunk**; internal project/codename is `indiabox`. No other binding brand assets, tagline, or voice guide confirmed yet.

## Evidence on Hand
- `docs/superpowers/specs/2026-08-05-gst-invoice-generation-design.md` and `docs/superpowers/plans/2026-08-05-gst-invoice-generation.md` — GST invoice generation spec/plan.
- Git history references an "architecture spec for membership pricing plans" and a merged TrunkAssist personal shopping concierge feature (`feature/personal-shopper` branch, `apps/personal_shop`) — confirms both are real, built capabilities, not aspirational.
- No testimonials, case studies, press, or real customer evidence on hand — future design work must not fabricate these.

## Product Principles
1. Trust and security are core to the product, not a bolt-on — reflected in KYC, ownership checks, and audit logging throughout.
2. The India corridor (customs, carriers, GST) is a first-class use case, not an afterthought.
3. Admin/warehouse operators are a real, distinct user role from the end shopper — design and workflows must serve both.
4. The product is expanding beyond pure forwarding (concierge shopping, membership tiers) — avoid designing surfaces that assume a single-service scope.

## Accessibility & Inclusion
No product-specific accessibility requirement established yet.
