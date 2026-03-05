"""
IndiaBox Load Test Suite
========================
Run with: locust -f locustfile.py --host=https://indiabox.up.railway.app

Scenarios:
  - Baseline (10 users):   locust -f locustfile.py --users 10 --spawn-rate 2 --run-time 2m --headless
  - Pre-launch (50 users): locust -f locustfile.py --users 50 --spawn-rate 5 --run-time 5m --headless
  - Stress (100 users):    locust -f locustfile.py --users 100 --spawn-rate 10 --run-time 5m --headless
  - Break point (200):     locust -f locustfile.py --users 200 --spawn-rate 5 --run-time 10m --headless

Web UI: locust -f locustfile.py  →  http://localhost:8089
"""

from locust import HttpUser, task, between, tag, events
import random
import time
import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
#  SLA THRESHOLDS
# ---------------------------------------------------------------------------
PAGE_LOAD_SLA_MS = 2000       # < 2 seconds page load
DASHBOARD_SLA_MS = 1000       # < 1 second dashboard
API_SLA_MS = 5000             # < 5 seconds payment processing
FAILURE_RATE_SLA = 0.001      # < 0.1% failure rate


# ---------------------------------------------------------------------------
#  PUBLIC PAGES (unauthenticated)
# ---------------------------------------------------------------------------
class PublicUser(HttpUser):
    """Simulate anonymous visitors browsing public pages."""
    weight = 5  # 5x more public browsers than authenticated users
    wait_time = between(2, 8)

    @tag("public", "home")
    @task(10)
    def view_home(self):
        """Landing page — most visited."""
        with self.client.get("/", name="[Public] Home", catch_response=True) as resp:
            if resp.elapsed.total_seconds() * 1000 > PAGE_LOAD_SLA_MS:
                resp.failure(f"Slow: {resp.elapsed.total_seconds():.2f}s")

    @tag("public")
    @task(3)
    def view_about(self):
        self.client.get("/about/", name="[Public] About")

    @tag("public")
    @task(3)
    def view_faq(self):
        self.client.get("/faq/", name="[Public] FAQ")

    @tag("public")
    @task(2)
    def view_prohibited_items(self):
        self.client.get("/prohibited-items/", name="[Public] Prohibited Items")

    @tag("public")
    @task(2)
    def view_shipping_calculator(self):
        self.client.get("/shipping-calculator/", name="[Public] Shipping Calculator")

    @tag("public")
    @task(2)
    def view_service_charges(self):
        self.client.get("/service-charges/", name="[Public] Service Charges")

    @tag("public")
    @task(1)
    def view_terms(self):
        self.client.get("/page/terms/", name="[Public] Terms")

    @tag("public")
    @task(1)
    def view_privacy(self):
        self.client.get("/page/privacy/", name="[Public] Privacy")

    @tag("public", "health")
    @task(1)
    def health_check(self):
        """Health endpoint — should always be fast."""
        with self.client.get("/health/", name="[System] Health Check", catch_response=True) as resp:
            if resp.elapsed.total_seconds() * 1000 > 500:
                resp.failure(f"Health check slow: {resp.elapsed.total_seconds():.2f}s")
            elif resp.status_code != 200:
                resp.failure(f"Health check returned {resp.status_code}")

    @tag("public")
    @task(1)
    def view_login_page(self):
        """Login page load (GET only)."""
        self.client.get("/accounts/login/", name="[Public] Login Page")


# ---------------------------------------------------------------------------
#  AUTHENTICATED USER SIMULATION
# ---------------------------------------------------------------------------
class AuthenticatedUser(HttpUser):
    """Simulate logged-in users browsing their locker and shipments.
    
    NOTE: For real load testing, pre-create test users and sessions.
    This version tests redirect behavior (302 to login) which still 
    exercises middleware, session lookup, and view dispatch.
    """
    weight = 3
    wait_time = between(3, 10)

    @tag("auth", "dashboard")
    @task(5)
    def view_dashboard(self):
        """Dashboard — checked 2-3x per day per user."""
        with self.client.get(
            "/accounts/dashboard/",
            name="[Auth] Dashboard",
            catch_response=True,
        ) as resp:
            if resp.status_code == 302:
                resp.success()  # Expected redirect to login
            elif resp.elapsed.total_seconds() * 1000 > DASHBOARD_SLA_MS:
                resp.failure(f"Dashboard slow: {resp.elapsed.total_seconds():.2f}s")

    @tag("auth", "locker")
    @task(4)
    def view_locker(self):
        """My Locker page."""
        with self.client.get(
            "/locker/",
            name="[Auth] My Locker",
            catch_response=True,
        ) as resp:
            if resp.status_code == 302:
                resp.success()

    @tag("auth", "locker")
    @task(2)
    def view_action_required(self):
        self.client.get("/locker/action-required/", name="[Auth] Action Required",
                        allow_redirects=False)

    @tag("auth", "locker")
    @task(2)
    def view_ready_to_ship(self):
        self.client.get("/locker/ready-to-ship/", name="[Auth] Ready to Ship",
                        allow_redirects=False)

    @tag("auth", "shipments")
    @task(3)
    def view_shipments(self):
        """Shipments list."""
        self.client.get("/shipments/", name="[Auth] Shipments", allow_redirects=False)

    @tag("auth", "shipments")
    @task(1)
    def view_active_shipments(self):
        self.client.get("/shipments/active/", name="[Auth] Active Shipments",
                        allow_redirects=False)

    @tag("auth", "shipments")
    @task(1)
    def view_delivered_shipments(self):
        self.client.get("/shipments/delivered/", name="[Auth] Delivered Shipments",
                        allow_redirects=False)

    @tag("auth", "profile")
    @task(1)
    def view_profile(self):
        self.client.get("/accounts/profile/", name="[Auth] Profile",
                        allow_redirects=False)

    @tag("auth", "kyc")
    @task(1)
    def view_kyc(self):
        self.client.get("/kyc/upload/", name="[Auth] KYC Upload",
                        allow_redirects=False)


# ---------------------------------------------------------------------------
#  ADMIN SIMULATION
# ---------------------------------------------------------------------------
class AdminUser(HttpUser):
    """Simulate admin panel usage during business hours."""
    weight = 1  # Much fewer admins than users
    wait_time = between(5, 20)

    @tag("admin")
    @task(3)
    def admin_parcel_list(self):
        """Admin views parcel list (heaviest query)."""
        self.client.get(
            "/manage-rb-panel/locker/parcel/",
            name="[Admin] Parcel List",
            allow_redirects=False,
        )

    @tag("admin")
    @task(2)
    def admin_shipment_list(self):
        self.client.get(
            "/manage-rb-panel/shipments/shipment/",
            name="[Admin] Shipment List",
            allow_redirects=False,
        )

    @tag("admin")
    @task(2)
    def admin_declaration_list(self):
        self.client.get(
            "/manage-rb-panel/shipments/declarationpendingshipment/",
            name="[Admin] Declaration Approvals",
            allow_redirects=False,
        )

    @tag("admin")
    @task(1)
    def admin_payment_list(self):
        self.client.get(
            "/manage-rb-panel/payments/payment/",
            name="[Admin] Payment List",
            allow_redirects=False,
        )

    @tag("admin")
    @task(1)
    def admin_user_list(self):
        self.client.get(
            "/manage-rb-panel/accounts/user/",
            name="[Admin] User List",
            allow_redirects=False,
        )

    @tag("admin")
    @task(1)
    def admin_index(self):
        self.client.get(
            "/manage-rb-panel/",
            name="[Admin] Dashboard",
            allow_redirects=False,
        )


# ---------------------------------------------------------------------------
#  PEAK HOUR SIMULATION (6-9 PM IST mix)
# ---------------------------------------------------------------------------
class PeakHourUser(HttpUser):
    """Simulate peak-hour behavior: rapid browsing + shipment checks."""
    weight = 2
    wait_time = between(1, 5)  # Faster browsing during peak

    @tag("peak")
    @task(5)
    def rapid_dashboard_check(self):
        """Users checking dashboard frequently."""
        self.client.get("/accounts/dashboard/", name="[Peak] Dashboard",
                        allow_redirects=False)

    @tag("peak")
    @task(3)
    def check_locker(self):
        self.client.get("/locker/", name="[Peak] Locker", allow_redirects=False)

    @tag("peak")
    @task(2)
    def check_shipments(self):
        self.client.get("/shipments/", name="[Peak] Shipments",
                        allow_redirects=False)

    @tag("peak")
    @task(1)
    def static_page_random(self):
        pages = ["/about/", "/faq/", "/shipping-calculator/"]
        self.client.get(random.choice(pages), name="[Peak] Static Page")


# ---------------------------------------------------------------------------
#  EVENT HOOKS — aggregate SLA reporting
# ---------------------------------------------------------------------------
@events.quitting.add_listener
def on_quit(environment, **kwargs):
    """Print SLA compliance summary on test completion."""
    stats = environment.runner.stats
    total = stats.total

    if total.num_requests == 0:
        return

    failure_rate = total.num_failures / total.num_requests
    avg_ms = total.avg_response_time or 0
    p95_ms = total.get_response_time_percentile(0.95) or 0
    p99_ms = total.get_response_time_percentile(0.99) or 0

    print("\n" + "=" * 70)
    print("  INDIABOX LOAD TEST — SLA COMPLIANCE REPORT")
    print("=" * 70)
    print(f"  Total Requests:    {total.num_requests:,}")
    print(f"  Failed Requests:   {total.num_failures:,}")
    print(f"  Failure Rate:      {failure_rate:.4%}  (SLA: < 0.1%)")
    print(f"  Avg Response:      {avg_ms:.0f} ms")
    print(f"  P95 Response:      {p95_ms:.0f} ms  (SLA: < 2000 ms)")
    print(f"  P99 Response:      {p99_ms:.0f} ms")
    print(f"  Requests/sec:      {total.current_rps:.1f}")
    print("-" * 70)

    sla_pass = True
    if failure_rate > FAILURE_RATE_SLA:
        print(f"  ❌ FAILURE RATE SLA BREACHED ({failure_rate:.4%} > {FAILURE_RATE_SLA:.4%})")
        sla_pass = False
    if p95_ms > PAGE_LOAD_SLA_MS:
        print(f"  ❌ P95 RESPONSE TIME SLA BREACHED ({p95_ms:.0f}ms > {PAGE_LOAD_SLA_MS}ms)")
        sla_pass = False
    if sla_pass:
        print("  ✅ ALL SLAs MET")
    print("=" * 70 + "\n")
