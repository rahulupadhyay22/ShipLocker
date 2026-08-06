"""Self-check for RateLimitMiddleware tiering + backoff, and strict input validators.
Run: python manage.py test indiabox.tests"""
import time
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, RequestFactory, override_settings

from indiabox.middleware import RateLimitMiddleware
from indiabox.validators import validate_text_input, validate_address, validate_file_upload
from indiabox.circuit_breaker import CircuitBreaker, CircuitOpenError


@override_settings(RATE_LIMIT_SETTINGS={
    'auth': {'max_attempts': 2, 'window': 300, 'backoff_base': 10, 'backoff_max': 100},
    'public': {'max_attempts': 2, 'window': 300},
    'authenticated': {'max_attempts': 2, 'window': 300},
})
class RateLimitMiddlewareTests(TestCase):
    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()
        self.mw = RateLimitMiddleware(lambda r: object())

    def _post(self, path, email=None):
        req = self.factory.post(path, {'email': email} if email else {})
        req.session = {}
        return req

    def test_auth_blocks_after_max_attempts_per_ip(self):
        for _ in range(2):
            self.assertTrue(self.mw._check(self._post('/accounts/login/', 'a@x.com'), '/accounts/login/', 'auth'))
        self.assertFalse(self.mw._check(self._post('/accounts/login/', 'a@x.com'), '/accounts/login/', 'auth'))

    def test_auth_per_account_blocks_even_from_new_ip(self):
        req = self._post('/accounts/login/', 'shared@x.com')
        req.META['REMOTE_ADDR'] = '1.1.1.1'
        for _ in range(2):
            self.assertTrue(self.mw._check(req, '/accounts/login/', 'auth'))
        req2 = self._post('/accounts/login/', 'shared@x.com')
        req2.META['REMOTE_ADDR'] = '2.2.2.2'
        self.assertFalse(self.mw._check(req2, '/accounts/login/', 'auth'))

    def test_auth_lockout_backs_off_exponentially(self):
        req = self._post('/accounts/login/', 'b@x.com')
        for _ in range(2):
            self.mw._check(req, '/accounts/login/', 'auth')
        self.mw._check(req, '/accounts/login/', 'auth')  # 1st violation -> lockout 10s
        lockout1 = cache.get('rl:auth:/accounts/login/:ip:127.0.0.1:lockout')
        self.assertTrue(lockout1)
        cache.delete('rl:auth:/accounts/login/:ip:127.0.0.1:lockout')
        self.mw._check(req, '/accounts/login/', 'auth')  # 2nd violation -> lockout 20s
        self.assertEqual(cache.get('rl:auth:/accounts/login/:ip:127.0.0.1:violations'), 2)

    def test_public_and_authenticated_have_no_lockout_key(self):
        req = self._post('/shipping-calculator/')
        for _ in range(3):
            self.mw._check(req, '/shipping-calculator/', 'public')
        self.assertIsNone(cache.get('rl:public:/shipping-calculator/:ip:127.0.0.1:lockout'))


class StrictInputValidationTests(TestCase):
    def test_rejects_script_tag_instead_of_stripping(self):
        with self.assertRaises(ValidationError):
            validate_text_input('<script>alert(1)</script>', field_name='Name')

    def test_rejects_overlong_input_instead_of_truncating(self):
        with self.assertRaises(ValidationError):
            validate_text_input('a' * 501, field_name='Name', max_length=500)

    def test_rejects_non_string_type(self):
        with self.assertRaises(ValidationError):
            validate_text_input(12345, field_name='Name')

    def test_accepts_clean_input_unmodified(self):
        self.assertEqual(validate_text_input('Rahul Kumar', field_name='Name'), 'Rahul Kumar')

    def test_address_rejects_missing_required_field(self):
        with self.assertRaises(ValidationError):
            validate_address({
                'recipient_name': 'Rahul', 'address_line1': 'Street 1',
                'city': '', 'country': 'India', 'postal_code': '110001',
            })

    def test_address_rejects_bad_postal_code(self):
        with self.assertRaises(ValidationError):
            validate_address({
                'recipient_name': 'Rahul', 'address_line1': 'Street 1',
                'city': 'Delhi', 'country': 'India', 'postal_code': '<img src=x>',
            })


class FileUploadContentValidationTests(TestCase):
    def test_accepts_real_jpeg_bytes(self):
        content = b'\xff\xd8\xff\xe0' + b'\x00' * 20
        f = SimpleUploadedFile('photo.jpg', content, content_type='image/jpeg')
        self.assertTrue(validate_file_upload(f))

    def test_rejects_script_disguised_with_image_extension_and_content_type(self):
        # attacker renames a script to .jpg and spoofs the Content-Type header
        content = b'<script>alert(document.cookie)</script>'
        f = SimpleUploadedFile('photo.jpg', content, content_type='image/jpeg')
        with self.assertRaises(ValidationError):
            validate_file_upload(f)

    def test_rejects_html_disguised_as_pdf(self):
        content = b'<html><body>not a pdf</body></html>'
        f = SimpleUploadedFile('doc.pdf', content, content_type='application/pdf')
        with self.assertRaises(ValidationError):
            validate_file_upload(f)

    def test_rejects_real_png_bytes_declared_as_pdf(self):
        content = b'\x89PNG\r\n\x1a\n' + b'\x00' * 20
        f = SimpleUploadedFile('photo.png.pdf', content, content_type='application/pdf')
        with self.assertRaises(ValidationError):
            validate_file_upload(f)

    def test_sniff_does_not_consume_file_for_downstream_read(self):
        content = b'%PDF-1.4' + b'\x00' * 20
        f = SimpleUploadedFile('doc.pdf', content, content_type='application/pdf')
        validate_file_upload(f)
        self.assertEqual(f.read(), content)


class CircuitBreakerTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_trips_after_threshold_and_fast_fails_without_running_body(self):
        breaker = CircuitBreaker('test-svc', fail_threshold=3, reset_timeout=60, max_concurrency=2)
        calls = []

        def failing_call():
            with breaker.call():
                calls.append(1)  # only reached if the circuit let the call through
                raise ConnectionError('dependency down')

        for _ in range(3):
            with self.assertRaises(ConnectionError):
                failing_call()
        self.assertEqual(len(calls), 3)

        # Circuit now open: body must not execute (fast-fail, no network attempt)
        with self.assertRaises(CircuitOpenError):
            failing_call()
        self.assertEqual(len(calls), 3, 'wrapped call ran even though circuit was open')

    def test_success_resets_failure_count(self):
        breaker = CircuitBreaker('test-svc-2', fail_threshold=3, reset_timeout=60, max_concurrency=2)

        for _ in range(2):
            with self.assertRaises(ConnectionError):
                with breaker.call():
                    raise ConnectionError('slow')

        with breaker.call():
            pass  # success clears the failure streak

        for _ in range(2):
            with self.assertRaises(ConnectionError):
                with breaker.call():
                    raise ConnectionError('slow')

        # Only 2 consecutive failures since the reset -- still below threshold=3
        with breaker.call():
            pass

    def test_recovers_after_cooldown(self):
        breaker = CircuitBreaker('test-svc-3', fail_threshold=1, reset_timeout=60, max_concurrency=2)

        with self.assertRaises(ConnectionError):
            with breaker.call():
                raise ConnectionError('down')

        with self.assertRaises(CircuitOpenError):
            with breaker.call():
                self.fail('body must not run while circuit is open')

        # Simulate cooldown elapsing by clearing the cached open-until marker
        cache.delete(breaker._open_key)

        with breaker.call():
            pass  # half-open trial succeeds, circuit closes

        with self.assertRaises(ConnectionError):
            with breaker.call():
                raise ConnectionError('down again')  # proves circuit was closed, not still-open

    def test_concurrency_cap_rejects_beyond_limit_without_tripping_failures(self):
        breaker = CircuitBreaker('test-svc-4', fail_threshold=5, reset_timeout=60, max_concurrency=1)

        with breaker.call():
            # A second concurrent caller must be rejected immediately --
            # this is what stops one slow dependency from queuing every thread.
            with self.assertRaises(CircuitOpenError):
                with breaker.call():
                    pass

        # Slot freed after the first call exited; unrelated calls are unaffected.
        with breaker.call():
            pass

    def test_unrelated_dependency_breaker_is_independent(self):
        broken = CircuitBreaker('broken-dep', fail_threshold=1, reset_timeout=60, max_concurrency=2)
        healthy = CircuitBreaker('healthy-dep', fail_threshold=1, reset_timeout=60, max_concurrency=2)

        with self.assertRaises(ConnectionError):
            with broken.call():
                raise ConnectionError('down')

        with self.assertRaises(CircuitOpenError):
            with broken.call():
                pass

        # A tripped breaker for one dependency must not affect another's breaker
        with healthy.call():
            pass

    def test_logs_when_it_opens_and_again_when_it_recovers(self):
        breaker = CircuitBreaker('test-svc-5', fail_threshold=1, reset_timeout=60, max_concurrency=2)

        with self.assertLogs('security', level='WARNING') as trip_logs:
            with self.assertRaises(ConnectionError):
                with breaker.call():
                    raise ConnectionError('down')
        self.assertTrue(any('OPEN' in m for m in trip_logs.output), 'opening the circuit must be logged')

        cache.set(breaker._open_key, time.time() - 1, timeout=600)  # cooldown elapsed, marker still present

        with self.assertLogs('security', level='WARNING') as recover_logs:
            with breaker.call():
                pass
        self.assertTrue(any('RECOVERED' in m for m in recover_logs.output), 'recovery must also be logged, not just the trip')
