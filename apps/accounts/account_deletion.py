"""Self-service account deletion (DPDP Act 2023 right to erasure).

The `User` and `Locker` rows are never hard-deleted — Payment.user is
on_delete=CASCADE and Batch/BatchCharge (storage-billing ledger) hang off
Locker, and those financial records must be retained (Income Tax Act / GST
record-keeping expectations), not deleted just because the account owner
requested erasure. Instead we anonymize the User row in place and hard-delete
everything that's pure personal data with no financial/legal dependent.

A Parcel that was actually shipped is linked into a Shipment's customs
declaration via ShipmentItem (on_delete=CASCADE from Parcel) — deleting that
Parcel would silently destroy part of a retained shipment's record. So only
never-shipped parcels (no shipment_items) are deleted; shipped parcels, and
every Payment/Shipment/ShipmentItem/PersonalShopRequest/Batch/BatchCharge, are
left untouched.

Whether anonymize-and-retain actually satisfies DPDP's erasure standard
alongside the Income Tax Act's retention requirement is a legal judgment call,
not an engineering one — flagged for lawyer review, not decided here.
"""
import logging
import uuid

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import KYCDocument, SavedAddress
from apps.locker.models import Parcel
from apps.payments.models import BatchCharge

logger = logging.getLogger('security')


class DeletionBlocked(Exception):
    """Raised when a precondition stops account deletion from proceeding."""


def _check_preconditions(locker):
    if BatchCharge.objects.filter(batch__locker=locker, status='pending').exists():
        raise DeletionBlocked(
            'This account has an unpaid storage charge. Please settle it before requesting deletion.'
        )


@transaction.atomic
def delete_user_account(user):
    """Anonymize `user` and hard-delete their non-financial personal data.

    Returns a dict summary of what was removed, for the confirmation page /
    audit log. Raises DeletionBlocked if a precondition isn't met — nothing
    is changed in that case.
    """
    locker = getattr(user, 'locker', None)
    if locker is not None:
        _check_preconditions(locker)

    kyc_count = KYCDocument.objects.filter(user=user).count()
    address_count = SavedAddress.objects.filter(user=user).count()

    # post_delete signals on KYCDocument/ParcelImage already clean up the
    # matching Supabase Storage files (see apps/accounts/signals.py,
    # apps/locker/signals.py) — no need to duplicate that here.
    KYCDocument.objects.filter(user=user).delete()
    SavedAddress.objects.filter(user=user).delete()

    parcels_deleted = 0
    if locker is not None:
        never_shipped = Parcel.objects.filter(locker=locker).exclude(shipment_items__isnull=False)
        parcels_deleted = never_shipped.count()
        never_shipped.delete()  # cascades ParcelImage/ReturnRequest/DiscardRequest

    anonymized_email = f"deleted-{uuid.uuid4().hex}@deleted.cameltrunk.local"
    user.email = anonymized_email
    user.full_name = ''
    user.phone = ''
    user.whatsapp_number = ''
    user.whatsapp_verified = False
    user.is_active = False
    user.anonymized_at = timezone.now()
    supabase_id = user.supabase_id
    user.supabase_id = None
    user.save(update_fields=[
        'email', 'full_name', 'phone', 'whatsapp_number', 'whatsapp_verified',
        'is_active', 'anonymized_at', 'supabase_id',
    ])

    if locker is not None:
        locker.is_active = False
        locker.save(update_fields=['is_active'])

    supabase_auth_deleted = False
    if supabase_id:
        try:
            from apps.accounts.services import get_supabase_client
            get_supabase_client().auth.admin.delete_user(supabase_id)
            supabase_auth_deleted = True
        except Exception as e:
            # Caught (not re-raised) so a Supabase outage never rolls back the
            # DB-side anonymization that already succeeded. But the original
            # email can still request a fresh OTP against the still-live
            # Supabase Auth identity and recreate an account until this is
            # cleaned up by hand — logged loudly, not swallowed silently.
            logger.error(
                f"Supabase Auth deletion failed for anonymized user {user.pk} "
                f"(supabase_id={supabase_id}): {e}. Manual cleanup required in the Supabase dashboard."
            )

    logger.info(
        f"Account deletion completed: user={user.pk} kyc_docs={kyc_count} "
        f"addresses={address_count} never_shipped_parcels={parcels_deleted} "
        f"supabase_auth_deleted={supabase_auth_deleted}"
    )

    return {
        'kyc_documents_deleted': kyc_count,
        'addresses_deleted': address_count,
        'parcels_deleted': parcels_deleted,
        'supabase_auth_deleted': supabase_auth_deleted,
    }
