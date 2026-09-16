"""Behavioral baseline for apps/personal_shop/ Forms, captured BEFORE any
validator-reuse changes (repo-wide audit: several free-text fields bypass
indiabox.validators.validate_text_input, shop_phone bypasses validate_phone,
reference_image already uses validate_file_upload -- verified below against
current source, not assumed from the earlier audit summary).

Read directly before writing these (not assumed):
- apps/personal_shop/forms.py: none of the five request-type ModelForms
  (ProductLinkForm, ImageSearchForm, BoutiquePurchaseForm,
  LocalShopPurchaseForm, CustomRequestForm) import or call
  validate_text_input or validate_phone anywhere.
- apps/personal_shop/views.py: validate_file_upload IS called (twice, in
  PersonalShopRequestCreateView.post and PersonalShopRequestEditView.post),
  but only for reference_image, reading from form.cleaned_data (not raw
  request.FILES) -- no re-read-after-validation bug.
- notes/description/item_description/shop_address/shop_phone/preferred_size/
  maps_link/quantity/size/colour/city are NOT real model columns -- they're
  stored inside PersonalShopRequest.type_details, a plain JSONField with no
  DB-level length constraint. The ONLY constraints today come from each
  Form field's own declaration (max_length if given, or none at all).
- Concretely, per field (grep-verified against apps/personal_shop/forms.py):
    * ProductLinkForm.notes           -- max_length=300, optional
    * ProductLinkForm.size/.colour    -- max_length=50, optional
    * ImageSearchForm.description     -- NO max_length (unbounded), optional
    * BoutiquePurchaseForm.item_description -- NO max_length (unbounded),
      required=False at the field level but conditionally required by a
      custom clean() only when source_mode != 'trunk'
    * BoutiquePurchaseForm.preferred_size -- max_length=50, optional
    * LocalShopPurchaseForm.shop_address  -- NO max_length (unbounded),
      required=True (Django's default -- no required=False passed)
    * LocalShopPurchaseForm.shop_phone    -- max_length=20 (bounded), but a
      bare CharField with NO format/digit validation at all -- required=True
      by Django's default, DESPITE the template label reading "(If known)"
      (templates/personal_shop/request_form_local_shop.html line 39) --
      a real UI-vs-enforcement mismatch, pinned below at the time this was
      written. UPDATE: this specific mismatch was later fixed (Item 3,
      low-priority validation cleanup pass) -- shop_phone is now
      required=False, matching the label. See
      test_blank_shop_phone_now_accepted_matching_ui_label.
    * LocalShopPurchaseForm.item_description -- NO max_length (unbounded),
      required=True (Django's default, unlike BoutiquePurchaseForm's same-
      named field, which is required=False + conditionally enforced)
    * CustomRequestForm.description   -- max_length=500, required=True
      (both Django's default AND a redundant manual clean_description()
      re-check)
- reference_image (ImageSearchForm/BoutiquePurchaseForm/LocalShopPurchaseForm)
  is a Django forms.ImageField, not FileField -- Pillow verifies the file is
  a genuine, parseable image at Form.is_valid() time, BEFORE
  validate_file_upload ever runs in the view. Consequence verified below:
  a well-formed PDF (which validate_file_upload alone would accept) is
  rejected by ImageField/Pillow first, with Django's own error text --
  validate_file_upload's own content-type/magic-byte layer is only reached,
  in practice, for files Pillow already accepts as real images (so it
  mainly adds the SIZE check and the extension/declared-type-mismatch
  check on top of Pillow's own image-content verification).
"""
import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from unittest.mock import patch

from apps.accounts.models import Locker, User
from apps.personal_shop.models import PersonalShopRequest

MINIMAL_PDF = b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF'


def make_locker(email='validator-baseline@example.com'):
    user = User.objects.create(email=email, is_active=True)
    return Locker.objects.create(user=user)


def make_png(name='ref.png', content_type='image/png'):
    """A genuinely valid, Pillow-parseable 1x1 PNG, built with real PIL
    (not hand-written bytes) so it reliably passes Django's ImageField."""
    from PIL import Image

    buf = io.BytesIO()
    Image.new('RGB', (1, 1), color=(255, 0, 0)).save(buf, format='PNG')
    return SimpleUploadedFile(name, buf.getvalue(), content_type=content_type)


def make_large_valid_png(name='big.png', min_size=None):
    """A real, Pillow-parseable PNG whose encoded size exceeds min_size --
    built with actual PIL, not padded bytes, so it reliably passes Django's
    ImageField/Pillow check regardless of Pillow's tolerance for trailing
    garbage."""
    from django.conf import settings
    from PIL import Image

    if min_size is None:
        min_size = getattr(settings, 'MAX_UPLOAD_SIZE', 5 * 1024 * 1024)

    import random
    side = 1600
    while True:
        img = Image.new('RGB', (side, side))
        img.putdata([
            (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
            for _ in range(side * side)
        ])
        buf = io.BytesIO()
        img.save(buf, format='PNG', compress_level=0)
        data = buf.getvalue()
        if len(data) > min_size:
            return SimpleUploadedFile(name, data, content_type='image/png')
        side = int(side * 1.5)


class ProductLinkNotesBaselineTests(TestCase):
    """ProductLinkForm.notes: max_length=300, optional, no dangerous-pattern
    check today."""

    def setUp(self):
        self.locker = make_locker('notes@example.com')
        self.client.force_login(self.locker.user)
        self.url = reverse('personal_shop:request_create', args=['product_link'])

    def _post(self, **overrides):
        data = {'product_url': 'https://example.com/item', 'notes': 'Please gift wrap'}
        data.update(overrides)
        return self.client.post(self.url, data)

    def test_valid_notes_accepted(self):
        response = self._post(notes='Please gift wrap it nicely')
        req = PersonalShopRequest.objects.filter(locker=self.locker).first()
        self.assertIsNotNone(req)
        self.assertEqual(req.type_details['notes'], 'Please gift wrap it nicely')

    def test_blank_notes_accepted_field_is_optional(self):
        response = self._post(notes='')
        self.assertEqual(response.status_code, 302)
        req = PersonalShopRequest.objects.filter(locker=self.locker).first()
        self.assertEqual(req.type_details['notes'], '')

    def test_notes_at_300_chars_accepted(self):
        text = 'A' * 300
        self._post(notes=text)
        req = PersonalShopRequest.objects.filter(locker=self.locker).first()
        self.assertEqual(req.type_details['notes'], text)

    def test_notes_over_300_chars_rejected_by_django_max_length(self):
        response = self._post(notes='A' * 301)
        self.assertEqual(response.status_code, 200)  # re-renders form with errors, no redirect
        self.assertFalse(PersonalShopRequest.objects.filter(locker=self.locker).exists())

    # -- Intentional behavior change (validator-reuse implementation,
    # apps/personal_shop/forms.py ProductLinkForm.clean_notes): these four
    # tests originally pinned dangerous patterns as ACCEPTED (no
    # validate_text_input call existed). That gap was explicitly approved
    # to be closed -- notes now reuses validate_text_input, so these
    # patterns are rejected exactly like every other validate_text_input-
    # backed field in the codebase (Locker/Shipment/KYC/Profile).

    def test_script_tag_in_notes_now_rejected(self):
        response = self._post(notes='<script>alert(1)</script>')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PersonalShopRequest.objects.filter(locker=self.locker).exists())

    def test_iframe_in_notes_now_rejected(self):
        response = self._post(notes='<iframe src="evil.com"></iframe>')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PersonalShopRequest.objects.filter(locker=self.locker).exists())

    def test_javascript_url_in_notes_now_rejected(self):
        response = self._post(notes='javascript:alert(1)')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PersonalShopRequest.objects.filter(locker=self.locker).exists())

    def test_event_handler_in_notes_now_rejected(self):
        response = self._post(notes='<img onclick=alert(1)>')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PersonalShopRequest.objects.filter(locker=self.locker).exists())


class ImageSearchDescriptionBaselineTests(TestCase):
    """ImageSearchForm.description: intentionally bounded at max_length=500
    (approved decision, matching CustomRequestForm.description's same-app
    precedent) via validate_text_input in clean_description -- no Django-
    level max_length exists on this field, so validate_text_input's own
    length check is the SOLE length enforcer here (unlike notes/
    CustomRequestForm.description, where Django's own field max_length
    already fired first). Still optional -- required=False preserved."""

    def setUp(self):
        self.locker = make_locker('imgsearch@example.com')
        self.client.force_login(self.locker.user)
        self.url = reverse('personal_shop:request_create', args=['image_search'])

    def test_valid_description_accepted(self):
        response = self.client.post(self.url, {'description': 'a nice handbag'})
        self.assertEqual(response.status_code, 302)

    def test_blank_description_accepted_field_is_optional(self):
        response = self.client.post(self.url, {'description': ''})
        self.assertEqual(response.status_code, 302)

    def test_description_at_500_chars_accepted(self):
        text = 'A' * 500
        response = self.client.post(self.url, {'description': text})
        self.assertEqual(response.status_code, 302)
        req = PersonalShopRequest.objects.filter(locker=self.locker).first()
        self.assertEqual(req.type_details['description'], text)

    def test_description_over_500_chars_now_rejected(self):
        """Intentional behavior change (approved limit): previously
        unbounded, now capped at 500 via validate_text_input -- this is the
        validator's OWN length check firing, not Django's (no field-level
        max_length exists here)."""
        response = self.client.post(self.url, {'description': 'A' * 501})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PersonalShopRequest.objects.filter(locker=self.locker).exists())

    def test_very_long_description_now_rejected(self):
        text = 'A' * 20000
        response = self.client.post(self.url, {'description': text})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PersonalShopRequest.objects.filter(locker=self.locker).exists())

    def test_script_tag_in_description_now_rejected(self):
        response = self.client.post(self.url, {'description': '<script>alert(1)</script>'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PersonalShopRequest.objects.filter(locker=self.locker).exists())


class BoutiqueItemDescriptionBaselineTests(TestCase):
    """BoutiquePurchaseForm.item_description: intentionally bounded at
    max_length=500 (approved decision) via validate_text_input in
    clean_item_description, required=False at the field level (unchanged)
    but conditionally required by clean() only when source_mode != 'trunk'
    (the 'new boutique purchase' path, unchanged).

    Fixed wrinkle (smallest-possible change in BoutiquePurchaseForm.clean()):
    when item_description fails validate_text_input (dangerous pattern or
    >500 chars) in 'new' mode, the field-level clean_item_description raise
    means Django never populates cleaned_data['item_description'] -- so
    clean()'s own conditional-required check used to ALSO fire (it saw
    item_description as "missing"), producing two stacked error messages
    for one invalid input. clean() now checks
    `'item_description' not in self.errors` before adding the redundant
    "required" error -- exactly one error is reported per invalid
    submission now, verified below via response.context['form'].errors."""

    def setUp(self):
        self.locker = make_locker('boutique@example.com')
        self.client.force_login(self.locker.user)
        self.url = reverse('personal_shop:request_create', args=['boutique_purchase'])

    def _make_parcel(self):
        from apps.locker.models import Parcel
        return Parcel.objects.create(locker=self.locker, status='approved', item_name='Shoes')

    # -- 1. Boutique non-trunk + blank -> required error (only) -----------

    def test_blank_item_description_rejected_in_new_mode_with_single_required_error(self):
        response = self.client.post(self.url, {
            'boutique_name': 'Test Boutique', 'item_description': '', 'source_mode': 'new',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PersonalShopRequest.objects.filter(locker=self.locker).exists())
        errors = response.context['form'].errors['item_description']
        self.assertEqual(list(errors), ['Item description is required.'])

    # -- 2. Boutique non-trunk + valid text -> accepted --------------------

    def test_valid_new_purchase_accepted(self):
        response = self.client.post(self.url, {
            'boutique_name': 'Test Boutique', 'item_description': 'a silk dress', 'preferred_size': 'M',
        })
        self.assertEqual(response.status_code, 302)

    def test_item_description_at_500_chars_accepted(self):
        text = 'A' * 500
        response = self.client.post(self.url, {
            'boutique_name': 'Test Boutique', 'item_description': text,
        })
        self.assertEqual(response.status_code, 302)
        req = PersonalShopRequest.objects.filter(locker=self.locker).first()
        self.assertEqual(req.type_details['item_description'], text)

    # -- 3. Boutique non-trunk + dangerous pattern -> exactly one error ----

    def test_script_tag_in_item_description_rejected_with_single_error(self):
        response = self.client.post(self.url, {
            'boutique_name': 'Test Boutique', 'item_description': '<script>alert(1)</script>',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PersonalShopRequest.objects.filter(locker=self.locker).exists())
        errors = response.context['form'].errors['item_description']
        self.assertEqual(len(errors), 1)
        self.assertEqual(list(errors), ['Item Description contains disallowed content.'])
        self.assertNotIn('Item description is required.', errors)

    # -- 4. Boutique non-trunk + over 500 chars -> exactly one error -------

    def test_item_description_over_500_chars_rejected_with_single_error(self):
        response = self.client.post(self.url, {
            'boutique_name': 'Test Boutique', 'item_description': 'A' * 501,
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PersonalShopRequest.objects.filter(locker=self.locker).exists())
        errors = response.context['form'].errors['item_description']
        self.assertEqual(len(errors), 1)
        self.assertEqual(
            list(errors), ['Item Description must be between 1 and 500 characters.'],
        )
        self.assertNotIn('Item description is required.', errors)

    def test_very_long_item_description_rejected_with_single_error(self):
        response = self.client.post(self.url, {
            'boutique_name': 'Test Boutique', 'item_description': 'A' * 20000,
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PersonalShopRequest.objects.filter(locker=self.locker).exists())
        errors = response.context['form'].errors['item_description']
        self.assertEqual(len(errors), 1)

    # -- 5. Boutique trunk + blank -> accepted ------------------------------

    def test_blank_item_description_accepted_in_trunk_mode(self):
        """Unchanged conditional-required behavior: item_description is
        NOT required when source_mode == 'trunk' and a parcel is selected."""
        parcel = self._make_parcel()
        response = self.client.post(self.url, {
            'source_mode': 'trunk', 'source_parcel': str(parcel.pk), 'item_description': '',
        })
        self.assertEqual(response.status_code, 302)

    # -- 6. Boutique trunk + valid text -> accepted (and not required) -----

    def test_valid_item_description_accepted_in_trunk_mode(self):
        parcel = self._make_parcel()
        response = self.client.post(self.url, {
            'source_mode': 'trunk', 'source_parcel': str(parcel.pk), 'item_description': 'a note about it',
        })
        self.assertEqual(response.status_code, 302)
        req = PersonalShopRequest.objects.filter(locker=self.locker).first()
        self.assertEqual(req.type_details['item_description'], 'a note about it')

    # -- 7. Existing validator behavior remains intact: same coverage as
    # test_very_long_item_description_rejected_with_single_error and
    # test_item_description_over_500_chars_rejected_with_single_error
    # above -- length/pattern enforcement itself is unchanged by this fix,
    # only the error COUNT changed, from two down to one.


class LocalShopPurchaseBaselineTests(TestCase):
    """LocalShopPurchaseForm: shop_address (now bounded at max_length=300,
    approved decision, required -- unchanged), shop_phone (bounded at 20
    chars, format-checked via validate_phone when non-blank -- NOW
    required=False, matching the "(If known)" template label, per the
    Item 3 fix: this was a pre-existing authoring gap, not an intentional
    requirement -- see test_blank_shop_phone_now_accepted_matching_ui_label
    below), item_description (now bounded at max_length=500, approved
    decision, required -- unconditionally, unlike BoutiquePurchaseForm's
    same-named field -- unchanged). Neither shop_address nor
    item_description had a Django-level max_length before the earlier
    validator-reuse change, so validate_text_input's own length check is
    the sole length enforcer for both (unlike notes/
    CustomRequestForm.description, where Django's own field max_length
    already existed and fired first)."""

    def setUp(self):
        self.locker = make_locker('localshop@example.com')
        self.client.force_login(self.locker.user)
        self.url = reverse('personal_shop:request_create', args=['local_shop_purchase'])

    def _post(self, **overrides):
        data = {
            'shop_name': 'Test Shop', 'city': 'hyderabad',
            'shop_address': '123 Street, Landmark', 'shop_phone': '9999999999',
            'item_description': 'leather shoes',
        }
        data.update(overrides)
        return self.client.post(self.url, data)

    def test_valid_submission_accepted(self):
        response = self._post()
        self.assertEqual(response.status_code, 302)

    def test_blank_shop_address_rejected_field_is_required(self):
        response = self._post(shop_address='')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PersonalShopRequest.objects.filter(locker=self.locker).exists())

    def test_blank_shop_phone_now_accepted_matching_ui_label(self):
        """Item 3 fix: the template label reads '(If known)' and the
        request type's own sidebar tip phrases this as optional ("Add a
        contact number so we can confirm details", alongside "Mention the
        shop's open hours if known") -- no spec or business rule requires
        it. The field previously had no required=False, so Django's
        default required=True silently contradicted its own label; that
        was a pre-existing authoring gap, not an intentional requirement.
        LocalShopPurchaseForm.shop_phone is now required=False, matching
        the label -- a blank submission is accepted (this test previously
        pinned the OLD contradictory behavior, asserting rejection)."""
        response = self._post(shop_phone='')
        self.assertEqual(response.status_code, 302)
        req = PersonalShopRequest.objects.filter(locker=self.locker).first()
        self.assertIsNotNone(req)
        self.assertEqual(req.type_details['shop_phone'], '')

    def test_missing_shop_phone_key_also_accepted(self):
        data = {
            'shop_name': 'Test Shop', 'city': 'hyderabad',
            'shop_address': '123 Street, Landmark', 'item_description': 'leather shoes',
        }
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 302)

    def test_non_blank_shop_phone_still_fully_validated(self):
        """Confirms validate_phone is still enforced when a value IS
        supplied -- optional does not mean unvalidated."""
        response = self._post(shop_phone='still-not-a-phone!')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PersonalShopRequest.objects.filter(locker=self.locker).exists())

    def test_non_numeric_shop_phone_now_rejected(self):
        """Intentional behavior change (validator-reuse implementation,
        apps/personal_shop/forms.py LocalShopPurchaseForm.clean_shop_phone):
        shop_phone now reuses validate_phone, closing the gap this test
        originally pinned as accepted -- required=True and the max_length=20
        field constraint are both unchanged (approved decision)."""
        response = self._post(shop_phone='not-a-phone-at-all!')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PersonalShopRequest.objects.filter(locker=self.locker).exists())

    def test_valid_phone_with_separators_accepted(self):
        """validate_phone strips common separators before checking digit
        count -- confirms the accepted-format behavior, not just bare
        digit strings."""
        response = self._post(shop_phone='+91 98765 43210')
        self.assertEqual(response.status_code, 302)
        req = PersonalShopRequest.objects.filter(locker=self.locker).first()
        self.assertEqual(req.type_details['shop_phone'], '+91 98765 43210')

    def test_shop_phone_at_20_chars_and_a_valid_phone_accepted(self):
        """The exact-boundary case has to satisfy both constraints at once
        now: Django's own max_length=20 AND validate_phone's 7-15-digit
        cap (validate_phone strips separators before counting digits, so
        a 20-character value can still carry a valid <=15-digit phone)."""
        value = '+91 ((987)) 654-3210'  # 20 chars, 13 digits after stripping
        self.assertEqual(len(value), 20)
        self._post(shop_phone=value)
        req = PersonalShopRequest.objects.filter(locker=self.locker).first()
        self.assertEqual(req.type_details['shop_phone'], value)

    def test_shop_phone_20_digit_value_now_rejected_exceeds_validate_phone_max(self):
        """A 20-CHARACTER value that is also 20 DIGITS (no separators) now
        fails validate_phone's 15-digit cap, even though it still satisfies
        Django's own max_length=20 -- the two constraints don't always
        agree, and validate_phone is now the stricter one for pure-digit
        input at this length."""
        response = self._post(shop_phone='1' * 20)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PersonalShopRequest.objects.filter(locker=self.locker).exists())

    def test_shop_phone_over_20_chars_rejected_by_django_max_length(self):
        response = self._post(shop_phone='1' * 21)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PersonalShopRequest.objects.filter(locker=self.locker).exists())

    def test_blank_item_description_rejected_unconditionally(self):
        """Unlike BoutiquePurchaseForm's item_description (conditionally
        required via clean()), this one is required=True on the field
        itself -- always required, no 'trunk mode' escape hatch. Unchanged
        by the validator-reuse implementation."""
        response = self._post(item_description='')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PersonalShopRequest.objects.filter(locker=self.locker).exists())

    def test_script_tag_in_shop_address_now_rejected(self):
        """Intentional behavior change (approved)."""
        response = self._post(shop_address='<script>alert(1)</script>')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PersonalShopRequest.objects.filter(locker=self.locker).exists())

    def test_script_tag_in_item_description_now_rejected(self):
        response = self._post(item_description='<script>alert(1)</script>')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PersonalShopRequest.objects.filter(locker=self.locker).exists())

    def test_shop_address_at_300_chars_accepted(self):
        text = 'A' * 300
        response = self._post(shop_address=text)
        self.assertEqual(response.status_code, 302)
        req = PersonalShopRequest.objects.filter(locker=self.locker).first()
        self.assertEqual(req.type_details['shop_address'], text)

    def test_shop_address_over_300_chars_now_rejected(self):
        response = self._post(shop_address='A' * 301)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PersonalShopRequest.objects.filter(locker=self.locker).exists())

    def test_item_description_at_500_chars_accepted(self):
        text = 'A' * 500
        response = self._post(item_description=text)
        self.assertEqual(response.status_code, 302)
        req = PersonalShopRequest.objects.filter(locker=self.locker).first()
        self.assertEqual(req.type_details['item_description'], text)

    def test_item_description_over_500_chars_now_rejected(self):
        response = self._post(item_description='A' * 501)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PersonalShopRequest.objects.filter(locker=self.locker).exists())


class CustomRequestDescriptionBaselineTests(TestCase):
    """CustomRequestForm.description: max_length=500, required by Django's
    own field default (the old manual clean_description() re-check was
    verified dead code -- Django's own required check already fired first
    for a blank value both before and after the validator-reuse change;
    clean_description now reuses validate_text_input, adding dangerous-
    pattern rejection on top)."""

    def setUp(self):
        self.locker = make_locker('customreq@example.com')
        self.client.force_login(self.locker.user)
        self.url = reverse('personal_shop:request_create', args=['custom_request'])

    def test_valid_description_accepted(self):
        response = self.client.post(self.url, {'description': 'find me something rare'})
        self.assertEqual(response.status_code, 302)

    def test_blank_description_rejected(self):
        response = self.client.post(self.url, {'description': ''})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PersonalShopRequest.objects.filter(locker=self.locker).exists())

    def test_description_at_500_chars_accepted(self):
        text = 'A' * 500
        self.client.post(self.url, {'description': text})
        req = PersonalShopRequest.objects.filter(locker=self.locker).first()
        self.assertEqual(req.type_details['description'], text)

    def test_description_over_500_chars_rejected_by_django_max_length(self):
        response = self.client.post(self.url, {'description': 'A' * 501})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PersonalShopRequest.objects.filter(locker=self.locker).exists())

    def test_script_tag_in_description_now_rejected(self):
        """Intentional behavior change (validator-reuse implementation)."""
        response = self.client.post(self.url, {'description': '<script>alert(1)</script>'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PersonalShopRequest.objects.filter(locker=self.locker).exists())


class ReferenceImageBaselineTests(TestCase):
    """reference_image on ImageSearchForm -- shared field shape with
    BoutiquePurchaseForm/LocalShopPurchaseForm. Tests the two-layer gate:
    Django's ImageField/Pillow check first, then validate_file_upload in
    the view (reading from cleaned_data, not raw request.FILES)."""

    def setUp(self):
        self.locker = make_locker('refimage@example.com')
        self.client.force_login(self.locker.user)
        self.url = reverse('personal_shop:request_create', args=['image_search'])

    @patch('apps.accounts.services.SupabaseStorage.upload_file')
    def test_valid_png_accepted_and_uploaded(self, mock_upload):
        response = self.client.post(self.url, {
            'description': 'a bag', 'reference_image': make_png(),
        })
        self.assertEqual(response.status_code, 302)
        req = PersonalShopRequest.objects.filter(locker=self.locker).first()
        self.assertTrue(req.images.exists())
        mock_upload.assert_called_once()

    def test_pdf_rejected_by_imagefield_before_validate_file_upload_runs(self):
        """A well-formed PDF (validate_file_upload alone would accept this
        content-type) is rejected here by Django's ImageField/Pillow check
        first -- reference_image can never actually be a PDF today,
        despite validate_file_upload's own allowlist including PDF."""
        upload = SimpleUploadedFile('doc.pdf', MINIMAL_PDF, content_type='application/pdf')
        response = self.client.post(self.url, {'description': 'a bag', 'reference_image': upload})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PersonalShopRequest.objects.filter(locker=self.locker).exists())

    def test_non_image_garbage_bytes_rejected_by_imagefield(self):
        upload = SimpleUploadedFile('fake.png', b'NOT_A_REAL_IMAGE', content_type='image/png')
        response = self.client.post(self.url, {'description': 'a bag', 'reference_image': upload})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PersonalShopRequest.objects.filter(locker=self.locker).exists())

    def test_valid_image_with_mismatched_extension_rejected_by_validate_file_upload(self):
        """A genuinely valid PNG (passes Pillow) but declared with a
        mismatched extension/content-type is where validate_file_upload's
        own check is actually reached and does the rejecting."""
        upload = make_png(name='ref.txt', content_type='image/png')
        response = self.client.post(self.url, {'description': 'a bag', 'reference_image': upload})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PersonalShopRequest.objects.filter(locker=self.locker).exists())

    def test_oversized_but_valid_image_rejected_by_validate_file_upload_size_check(self):
        """Pillow doesn't check file size -- only validate_file_upload does.
        Uses a real oversized PNG (not padded bytes) to guarantee it passes
        the ImageField/Pillow gate first."""
        upload = make_large_valid_png()
        response = self.client.post(self.url, {'description': 'a bag', 'reference_image': upload})
        # Empirically confirmed (not assumed): Django's DATA_UPLOAD_MAX_
        # MEMORY_SIZE (also 5MB, see indiabox/settings.py) does NOT
        # intercept this at the multipart-parsing layer -- the request
        # reaches the view and validate_file_upload's own size check is
        # what rejects it, via the normal 200 form-re-render path, not a
        # 400 RequestDataTooBig.
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PersonalShopRequest.objects.filter(locker=self.locker).exists())

    def test_no_reference_image_still_succeeds_field_is_optional(self):
        response = self.client.post(self.url, {'description': 'a bag'})
        self.assertEqual(response.status_code, 302)
        req = PersonalShopRequest.objects.filter(locker=self.locker).first()
        self.assertFalse(req.images.exists())
