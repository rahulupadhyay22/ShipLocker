# CamelTrunk

A modern Django-based platform for international parcel forwarding, locker management, and shipping automation. Built for security, scalability, and user experience.

---

## 🚀 Features
- User dashboard with OTP-based (passwordless) authentication
- Locker management: receive, inspect, approve, return, or discard parcels
- Parcel image uploads (Supabase Storage)
- Shipment creation, tracking, and document management
- KYC document upload and verification
- Shipping calculator with live rate estimation
- Admin panel with advanced security and audit logging
- Rate limiting, security headers, and input validation throughout

---

## 🛠️ Tech Stack
- **Backend:** Django 6.x, PostgreSQL (Supabase)
- **Frontend:** Django Templates, HTML5, CSS3, JS
- **Storage:** Supabase Storage (private buckets)
- **Security:** Custom middleware, OTP login, rate limiting, file validation

---

## ⚡ Quick Start

1. **Clone the repo:**
   ```bash
   git clone https://github.com/your-org/CamelTrunk.git
   cd CamelTrunk
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment:**
   - Copy `.env.example` to `.env` and fill in your secrets (Supabase, DB, etc)

4. **Run migrations:**
   ```bash
   python manage.py migrate
   ```

5. **Create superuser:**
   ```bash
   python manage.py createsuperuser
   ```

6. **Start the server:**
   ```bash
   python manage.py runserver
   ```

---

## 🚂 Railway Deploy Notes

If deploy logs show `psycopg2.OperationalError` with `Network is unreachable` for a Supabase host, use a pooled/IPv4-safe URL.

- Set `DATABASE_POOLER_URL` in Railway to your Supabase pooler connection string.
- Keep `DATABASE_URL` as fallback if you want local compatibility.
- Optional: set `DATABASE_HOSTADDR` to a known IPv4 address if your provider cannot route IPv6.
- Optional: set `DB_CONNECT_TIMEOUT` (default: `10`).
- Set `ALLOWED_HOSTS` to include your domains (for example: `localhost,127.0.0.1,web-production-d1c948.up.railway.app`).

The app now prefers `DATABASE_POOLER_URL` over `DATABASE_URL` automatically.
It also auto-adds `RAILWAY_PUBLIC_DOMAIN` when available.

---

## 🔒 Security Highlights
- All sensitive endpoints rate-limited
- OTP login (no passwords stored)
- CSRF, XSS, and clickjacking protection
- File uploads validated for type, size, and extension
- All user data access is ownership-checked
- Security audit: `python manage.py security_check`

---

## 📦 Folder Structure
```
apps/
  accounts/      # User, auth, KYC
  locker/        # Locker, parcel, images
  shipments/     # Shipments, items, documents
  content/       # Static pages, calculator
CamelTrunk/     # Project settings, middleware, mixins
static/          # CSS, JS, images
templates/       # HTML templates
```

---

## 📝 License
MIT License. See [LICENSE](LICENSE) for details.

---

## 👨‍💻 Maintainers
- Rahul (Lead Developer)
- CamelTrunk Team

---

## 🌐 Live Demo
- [https://your-CamelTrunk-app.com](https://your-CamelTrunk-app.com)

---

## 📣 Contact & Support
- Email: rahulu825@gmail.com
- [Open an issue](https://github.com/your-org/CamelTrunk/issues)
#

