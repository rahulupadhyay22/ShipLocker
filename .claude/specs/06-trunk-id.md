# Spec: Trunk ID

## Overview

Standardize the existing locker-ID concept into the formal **CamelTrunk Virtual Address Standard**. Every user receives one permanent, unique **Trunk ID** in the format:

```
CT-HYD-XXXXXX
```

Example:

```
CT-HYD-483921
CT-HYD-027154
CT-HYD-900873
```

The `HYD` prefix is fixed and represents the CamelTrunk Hyderabad warehouse. The six-digit suffix is a randomly generated unique number. Every customer shares the same warehouse address and warehouse contact number, while their Trunk ID uniquely identifies them.

The existing `Locker.locker_id` already fulfills most of this responsibility (`RB-#####`, random, admin-editable warehouse address/phone via `AppSettings`). This change only updates the ID format to the new CamelTrunk standard and ensures the Trunk ID and customer name are visible wherever warehouse staff and customers need them, especially during parcel intake.

---

## Depends on

* `02-mytrunk-ui.md`
* `04-my-profile-ui.md`

Assumes the existing:

* `apps.accounts.models.Locker`
* `apps.notifications.models.AppSettings`

---

## App(s) touched

### accounts

* Update `Locker.locker_id` generation logic.

### locker

* Show Trunk ID + customer name in admin parcel list/detail screens.

No new app required.

---

## Routes

No new routes.

Existing Django-Unfold admin views under:

```
/manage-rb-panel/
```

will display the customer's:

* Full name
* Trunk ID

for faster parcel matching.

---

## Model Changes

### Locker

Update `generate_locker_id()` to generate IDs in the format:

```
CT-HYD-XXXXXX
```

where:

* `CT` = CamelTrunk
* `HYD` = Fixed warehouse code
* `XXXXXX` = Random 6-digit unique number

Example:

```
CT-HYD-483921
```

Requirements:

* Continue using the existing collision-retry pattern.
* Generate a random six-digit number.
* Check uniqueness before returning.
* Raise `ValueError` if uniqueness cannot be achieved after the existing maximum retry count.
* Prefer Python's `secrets` module over `random` for generating the numeric suffix.

No new counter model.

No sequential numbering.

No database sequence.

No race-prone shared counter.

---

### Existing Lockers

Do **not** migrate existing IDs.

Existing lockers like:

```
RB-38192
```

remain unchanged permanently.

This avoids breaking:

* printed parcel labels
* historical shipments
* parcel history
* customer references

---

### Parcel IDs

No changes.

Existing logic:

```
<locker_id>-P001
```

naturally becomes:

```
CT-HYD-483921-P001
```

for newly created lockers.

---

## Templates

### Modify

```
templates/accounts/profile.html
```

Verify that the Trunk ID block displays correctly with the new format.

Ensure:

* no overflow
* wraps cleanly on mobile
* desktop layout remains unchanged

---

### Modify

Admin Parcel screens (`ParcelAdmin`)

Display both:

* Customer Name
* Trunk ID

prominently in:

* Parcel List
* Parcel Detail

Recommended list columns:

* Parcel
* Customer Name
* Trunk ID
* Status

Warehouse staff should be able to identify customers primarily by Trunk ID.

---

## Files to Change

```
apps/accounts/models.py
```

* Update `generate_locker_id()`

```
apps/locker/admin.py
```

* Show customer name
* Show Trunk ID
* Add Trunk ID to admin list display/search

---

## Files to Create

None.

---

## New Dependencies

None.

---

## Rules for Implementation

* Use Django ORM only.
* Parameterized queries only if raw SQL is unavoidable.
* Authenticated views must continue using ownership mixins from `indiabox/mixins.py`.
* Security-relevant actions log through the `security` logger.
* Use CSS variables from `static/css/main.css`.
* Uploaded files continue using Supabase Storage.
* All templates extend `templates/base.html`.
* Keep the existing collision-retry logic in `generate_locker_id()`.
* Generate a random unique six-digit suffix.
* Use the fixed prefix `CT-HYD`.
* Do not modify or migrate existing `RB-#####` locker IDs.

---

## Definition of Done

* [ ] `python manage.py migrate` runs successfully (no new migrations required).
* [ ] Newly created users receive IDs in the format:

```
CT-HYD-483921
```

* [ ] Generated Trunk IDs are unique.
* [ ] Existing `RB-#####` lockers continue functioning normally.
* [ ] Parcel display IDs automatically become:

```
CT-HYD-483921-P001
```

for new lockers.

* [ ] `/manage-rb-panel/` parcel list and detail screens display customer name and Trunk ID together.
* [ ] Profile page displays the new Trunk ID correctly on desktop and mobile.



