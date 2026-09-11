"""
Tests for the e-sign customs declaration feature (spec:
.claude/specs/12-esign-declaration.md).

Covers, per spec:
- Happy path: valid purpose + agree + non-blank name creates the Shipment
  with all declaration_* fields populated, and a ShipmentDocument(customs).
- Validation errors: missing/invalid purpose, missing agree checkbox, blank
  name -- each rejects with no Shipment created.
- No KYC/identity match: a signature_name that doesn't match the user's
  account name is still accepted (explicit spec requirement).
- Ownership/scoping: parcels not in the requesting user's locker, or not
  'approved', are rejected with "Invalid parcel selection".
- Auth guard: anonymous POST redirects to login.
- Double-submission guard: a parcel already 'shipped' (post-first-submit
  state) causes the second submission to be rejected, not double-created.
- declaration_purpose choices are validated against Shipment.DECLARATION_PURPOSE_CHOICES.
- IP capture via X-Forwarded-For (falls back to REMOTE_ADDR).
- Fields are populated correctly and exactly once at creation time.

DeclarationService.generate_pdf/.upload_pdf are mocked throughout -- no real
reportlab PDF build or Supabase Storage upload should happen in a unit test.
"""
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User, Locker, SavedAddress
from apps.locker.models import Parcel
from apps.shipments.models import Shipment, ShipmentDocument


def make_locker(user):
    return Locker.objects.create(user=user)


def make_parcel(locker, status='approved', **extra):
    defaults = dict(
        locker=locker,
        status=status,
        item_name='Test Item',
        item_price=100,
        item_currency='INR',
        category='electronics',
        customs_description='A test item',
        weight_kg=1,
    )
    defaults.update(extra)
    return Parcel.objects.create(**defaults)


class ESignDeclarationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email='signer@example.com', full_name='Rahul Signer')
        self.locker = make_locker(self.user)
        self.parcel = make_parcel(self.locker)

        self.other_user = User.objects.create(email='other@example.com', full_name='Other Person')
        self.other_locker = make_locker(self.other_user)
        self.other_parcel = make_parcel(self.other_locker)

        # Mock the PDF generation/upload so tests never touch reportlab
        # rendering internals or Supabase Storage.
        self.generate_pdf_patcher = patch(
            'apps.shipments.services.declaration_service.DeclarationService.generate_pdf',
            return_value=b'%PDF-fake-bytes',
        )
        self.upload_pdf_patcher = patch(
            'apps.shipments.services.declaration_service.DeclarationService.upload_pdf',
            return_value='shipment/RB-00001/customs_fake.pdf',
        )
        self.mock_generate_pdf = self.generate_pdf_patcher.start()
        self.mock_upload_pdf = self.upload_pdf_patcher.start()
        self.addCleanup(self.generate_pdf_patcher.stop)
        self.addCleanup(self.upload_pdf_patcher.stop)

    def _valid_data(self, **overrides):
        data = {
            'parcels': [str(self.parcel.id)],
            'shipment_type': 'international',
            'declaration_purpose': 'gift',
            'signature_agree': 'on',
            'signature_name': 'Rahul Signer',
            'recipient_name': 'Jane Doe',
            'recipient_phone': '9999999999',
            'recipient_email': 'jane@example.com',
            'address_line1': '1 Test Street',
            'address_line2': '',
            'city': 'Testville',
            'state': 'Test State',
            'postal_code': '123456',
            'country': 'United States',
        }
        data.update(overrides)
        return data

    def _post(self, data):
        return self.client.post(reverse('shipments:create'), data)

    # -- Happy path -----------------------------------------------------

    def test_happy_path_creates_shipment_with_declaration_fields(self):
        self.client.force_login(self.user)
        response = self._post(self._valid_data())

        self.assertEqual(Shipment.objects.count(), 1)
        shipment = Shipment.objects.get()
        self.assertEqual(shipment.declaration_purpose, 'gift')
        self.assertEqual(shipment.declaration_signed_name, 'Rahul Signer')
        self.assertIsNotNone(shipment.declaration_signed_at)
        self.assertIsNotNone(shipment.declaration_signed_ip)
        self.assertEqual(shipment.declaration_version, Shipment.DECLARATION_TEXT_VERSION)
        self.assertRedirects(response, reverse('shipments:detail', kwargs={'pk': shipment.pk}))

    def test_happy_path_creates_customs_shipment_document(self):
        self.client.force_login(self.user)
        self._post(self._valid_data())

        shipment = Shipment.objects.get()
        doc = ShipmentDocument.objects.get(shipment=shipment)
        self.assertEqual(doc.document_type, 'customs')
        self.assertTrue(doc.document_url)
        self.mock_generate_pdf.assert_called_once()
        self.mock_upload_pdf.assert_called_once()

    def test_happy_path_marks_selected_parcel_shipped(self):
        self.client.force_login(self.user)
        self._post(self._valid_data())

        self.parcel.refresh_from_db()
        self.assertEqual(self.parcel.status, 'shipped')

    def test_pdf_generation_failure_rolls_back_entire_shipment_creation(self):
        """Spec DoD: 'If PDF generation/upload fails, the entire shipment
        creation rolls back' -- no orphaned Shipment/ShipmentItem, and the
        parcel must NOT have been flipped to 'shipped'. The view catches
        the exception and redirects with a friendly error rather than
        letting it surface as a raw 500 (code-review fix)."""
        self.client.force_login(self.user)
        self.mock_generate_pdf.side_effect = Exception('reportlab exploded')

        response = self._post(self._valid_data())

        self.assertRedirects(response, reverse('shipments:create'))
        self.assertEqual(Shipment.objects.count(), 0)
        self.assertEqual(ShipmentDocument.objects.count(), 0)
        self.parcel.refresh_from_db()
        self.assertEqual(self.parcel.status, 'approved')

    def test_pdf_upload_failure_rolls_back_entire_shipment_creation(self):
        """Same DoD guarantee, but the failure point is the Storage upload
        rather than PDF generation itself."""
        self.client.force_login(self.user)
        self.mock_upload_pdf.side_effect = Exception('supabase storage down')

        response = self._post(self._valid_data())

        self.assertRedirects(response, reverse('shipments:create'))
        self.assertEqual(Shipment.objects.count(), 0)
        self.assertEqual(ShipmentDocument.objects.count(), 0)
        self.parcel.refresh_from_db()
        self.assertEqual(self.parcel.status, 'approved')

    # -- Validation errors ------------------------------------------------

    def test_missing_declaration_purpose_rejected(self):
        self.client.force_login(self.user)
        data = self._valid_data()
        del data['declaration_purpose']

        response = self._post(data)

        self.assertEqual(Shipment.objects.count(), 0)
        self.assertRedirects(response, reverse('shipments:create'))

    def test_invalid_declaration_purpose_rejected(self):
        self.client.force_login(self.user)
        response = self._post(self._valid_data(declaration_purpose='not_a_real_choice'))

        self.assertEqual(Shipment.objects.count(), 0)
        self.assertRedirects(response, reverse('shipments:create'))

    def test_all_valid_declaration_purpose_choices_accepted(self):
        self.client.force_login(self.user)
        for code, _label in Shipment.DECLARATION_PURPOSE_CHOICES:
            with self.subTest(purpose=code):
                Shipment.objects.all().delete()
                parcel = make_parcel(self.locker)
                response = self._post(self._valid_data(
                    parcels=[str(parcel.id)],
                    declaration_purpose=code,
                ))
                self.assertEqual(Shipment.objects.count(), 1)
                self.assertEqual(Shipment.objects.get().declaration_purpose, code)

    def test_missing_signature_agree_rejected(self):
        self.client.force_login(self.user)
        data = self._valid_data()
        del data['signature_agree']

        response = self._post(data)

        self.assertEqual(Shipment.objects.count(), 0)
        self.assertRedirects(response, reverse('shipments:create'))

    def test_signature_agree_not_on_rejected(self):
        self.client.force_login(self.user)
        response = self._post(self._valid_data(signature_agree='off'))

        self.assertEqual(Shipment.objects.count(), 0)

    def test_blank_signature_name_rejected(self):
        self.client.force_login(self.user)
        response = self._post(self._valid_data(signature_name=''))

        self.assertEqual(Shipment.objects.count(), 0)
        self.assertRedirects(response, reverse('shipments:create'))

    def test_whitespace_only_signature_name_rejected(self):
        self.client.force_login(self.user)
        response = self._post(self._valid_data(signature_name='   '))

        self.assertEqual(Shipment.objects.count(), 0)

    def test_overlong_signature_name_rejected_not_truncated(self):
        """Intentional behavior change (Forms migration, see
        apps/shipments/forms.py ShipmentCreateForm.clean_signature_name):
        signature_name now goes through indiabox.validators.validate_text_input
        like every other free-text field in this view, so an oversized name is
        rejected outright with no Shipment created -- it is no longer silently
        truncated to 255 chars before reaching the DB. This replaces the old
        test_overlong_signature_name_truncated_not_rejected, which asserted
        the truncation behavior this migration deliberately removed."""
        self.client.force_login(self.user)
        long_name = 'A' * 300
        response = self._post(self._valid_data(signature_name=long_name))

        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('shipments:create'))
        self.assertEqual(Shipment.objects.count(), 0)

    def test_signature_name_with_script_tag_rejected(self):
        """Intentional behavior change: signature_name now also runs through
        validate_text_input's dangerous-pattern blocklist, same as every
        other free-text field in this view (recipient_name, customs
        description, etc.) -- previously only a length-cap applied."""
        self.client.force_login(self.user)
        response = self._post(self._valid_data(signature_name='<script>alert(1)</script>'))

        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('shipments:create'))
        self.assertEqual(Shipment.objects.count(), 0)

    # -- No KYC / identity-match requirement -------------------------------

    def test_signature_name_not_matching_user_full_name_is_accepted(self):
        """Explicit spec requirement: no name-match validation. A nickname or
        family member's name must still be accepted."""
        self.client.force_login(self.user)
        response = self._post(self._valid_data(signature_name="Grandma's Helper"))

        self.assertEqual(Shipment.objects.count(), 1)
        shipment = Shipment.objects.get()
        self.assertEqual(shipment.declaration_signed_name, "Grandma's Helper")
        self.assertNotEqual(shipment.declaration_signed_name, self.user.full_name)
        self.assertRedirects(response, reverse('shipments:detail', kwargs={'pk': shipment.pk}))

    # -- Ownership / scoping ------------------------------------------------

    def test_parcel_belonging_to_another_user_rejected(self):
        self.client.force_login(self.user)
        response = self._post(self._valid_data(parcels=[str(self.other_parcel.id)]))

        self.assertEqual(Shipment.objects.count(), 0)
        self.assertRedirects(response, reverse('shipments:create'))
        # The other user's parcel must remain untouched.
        self.other_parcel.refresh_from_db()
        self.assertEqual(self.other_parcel.status, 'approved')

    def test_parcel_not_in_approved_status_rejected(self):
        self.client.force_login(self.user)
        pending_parcel = make_parcel(self.locker, status='pending')

        response = self._post(self._valid_data(parcels=[str(pending_parcel.id)]))

        self.assertEqual(Shipment.objects.count(), 0)
        self.assertRedirects(response, reverse('shipments:create'))

    # -- Auth guard -----------------------------------------------------

    def test_anonymous_post_redirects_to_login(self):
        response = self._post(self._valid_data())

        self.assertEqual(Shipment.objects.count(), 0)
        self.assertTrue(response.url.startswith('/accounts/login/'))
        self.assertEqual(response.status_code, 302)

    # -- Double submission / concurrency guard -------------------------

    def test_second_submission_for_already_shipped_parcel_rejected(self):
        """Simulates the post-first-submission state: the parcel has already
        flipped to 'shipped'. A second submission for the same parcel must
        fail with 'Invalid parcel selection' rather than creating a second
        Shipment, per the select_for_update() serialization rule in the spec."""
        self.client.force_login(self.user)

        # First (winning) submission succeeds.
        self._post(self._valid_data())
        self.assertEqual(Shipment.objects.count(), 1)
        self.parcel.refresh_from_db()
        self.assertEqual(self.parcel.status, 'shipped')

        # Second submission for the same (now-shipped) parcel is rejected.
        response = self._post(self._valid_data())

        self.assertEqual(Shipment.objects.count(), 1)
        self.assertRedirects(response, reverse('shipments:create'))

    # -- IP capture -------------------------------------------------------

    def test_ip_captured_from_x_forwarded_for_first_entry(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('shipments:create'),
            self._valid_data(),
            HTTP_X_FORWARDED_FOR='203.0.113.5, 70.41.3.18',
        )
        shipment = Shipment.objects.get()
        self.assertEqual(shipment.declaration_signed_ip, '203.0.113.5')

    def test_ip_falls_back_to_remote_addr_without_forwarded_header(self):
        self.client.force_login(self.user)
        self._post(self._valid_data())

        shipment = Shipment.objects.get()
        self.assertIsNotNone(shipment.declaration_signed_ip)

    def test_malformed_x_forwarded_for_falls_back_to_remote_addr_without_500(self):
        """Code-review fix: a garbage X-Forwarded-For value must not reach
        the GenericIPAddressField unvalidated (Postgres raises DataError on
        an invalid inet value -- this must not surface as a 500)."""
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('shipments:create'),
            self._valid_data(),
            HTTP_X_FORWARDED_FOR='not-an-ip-address',
        )

        self.assertEqual(response.status_code, 302)
        shipment = Shipment.objects.get()
        self.assertIsNotNone(shipment.declaration_signed_ip)
        self.assertNotEqual(shipment.declaration_signed_ip, 'not-an-ip-address')

    # -- Fields set once, correctly, at creation ---------------------------

    def test_declaration_fields_all_populated_together_on_success(self):
        self.client.force_login(self.user)
        self._post(self._valid_data(declaration_purpose='sample', signature_name='Sample Signer'))

        shipment = Shipment.objects.get()
        self.assertEqual(shipment.declaration_purpose, 'sample')
        self.assertEqual(shipment.declaration_signed_name, 'Sample Signer')
        self.assertIsNotNone(shipment.declaration_signed_at)
        self.assertIsNotNone(shipment.declaration_signed_ip)
        self.assertEqual(shipment.declaration_version, Shipment.DECLARATION_TEXT_VERSION)

    # -- SavedAddress uses validated data, not raw POST (Forms migration) --

    def test_saved_address_matches_validated_shipment_address(self):
        """Regression test for the audit-identified bug: the save-address
        block used to mix validated address_data with fresh, unvalidated
        request.POST reads for recipient_phone/recipient_email/
        address_line2/state. Since those two sources happened to hold the
        same raw string in the normal case, the divergence was only
        observable if a validator ever transformed the value. This asserts
        the SavedAddress created here is built from the exact same
        form.cleaned_data used for the Shipment itself, field for field."""
        self.client.force_login(self.user)
        response = self._post(self._valid_data(
            save_address='on',
            address_label='Office',
            address_line2='Suite 400',
            state='California',
            recipient_phone='9998887777',
            recipient_email='saved@example.com',
        ))

        shipment = Shipment.objects.get()
        saved = SavedAddress.objects.get(user=self.user)

        self.assertRedirects(response, reverse('shipments:detail', kwargs={'pk': shipment.pk}))
        self.assertEqual(saved.label, 'Office')
        self.assertEqual(saved.recipient_name, shipment.recipient_name)
        self.assertEqual(saved.recipient_phone, shipment.recipient_phone)
        self.assertEqual(saved.recipient_email, shipment.recipient_email)
        self.assertEqual(saved.address_line1, shipment.address_line1)
        self.assertEqual(saved.address_line2, shipment.address_line2)
        self.assertEqual(saved.address_line2, 'Suite 400')
        self.assertEqual(saved.city, shipment.city)
        self.assertEqual(saved.state, shipment.state)
        self.assertEqual(saved.state, 'California')
        self.assertEqual(saved.postal_code, shipment.postal_code)
        self.assertEqual(saved.country, shipment.country)
        self.assertTrue(saved.is_default)

    def test_save_address_off_creates_no_saved_address(self):
        self.client.force_login(self.user)
        self._post(self._valid_data())

        self.assertFalse(SavedAddress.objects.filter(user=self.user).exists())

    def test_save_address_updates_existing_default_with_validated_data(self):
        self.client.force_login(self.user)
        existing = SavedAddress.objects.create(
            user=self.user, label='Old', is_default=True,
            recipient_name='Old Name', recipient_phone='1111111111',
            address_line1='Old Street', city='Old City', state='Old State',
            postal_code='000000', country='Old Country',
        )

        self._post(self._valid_data(save_address='on', address_label='New', state='Texas'))

        existing.refresh_from_db()
        shipment = Shipment.objects.get()
        self.assertEqual(existing.label, 'New')
        self.assertEqual(existing.state, 'Texas')
        self.assertEqual(existing.state, shipment.state)
        self.assertEqual(SavedAddress.objects.filter(user=self.user).count(), 1)
