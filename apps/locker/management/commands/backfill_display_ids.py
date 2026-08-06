from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from apps.locker.models import Parcel
from apps.shipments.models import Shipment

BATCH_SIZE = 500


class Command(BaseCommand):
    help = 'Backfill display_id for Parcels and Shipments'

    def handle(self, *args, **kwargs):
        self.stdout.write("Starting backfill process...")

        updated_parcels = self._backfill(
            queryset=Parcel.objects.filter(Q(display_id__isnull=True) | Q(display_id='')).select_related('locker').order_by('locker_id', 'created_at'),
            group_key=lambda p: p.locker_id,
            existing_max=lambda group_key: self._max_seq(Parcel.objects.filter(locker_id=group_key), '-P'),
            id_prefix=lambda p: f"{p.locker.locker_id}-P",
        )
        self.stdout.write(self.style.SUCCESS(f"Updated {updated_parcels} parcels."))

        updated_shipments = self._backfill(
            queryset=Shipment.objects.filter(Q(display_id__isnull=True) | Q(display_id='')).select_related('user__locker').order_by('user_id', 'created_at'),
            group_key=lambda s: s.user_id,
            existing_max=lambda group_key: self._max_seq(Shipment.objects.filter(user_id=group_key), '-S'),
            id_prefix=lambda s: f"{(s.user.locker.locker_id if getattr(s.user, 'locker', None) else f'U{str(s.user_id)[:6].upper()}')}-S",
        )
        self.stdout.write(self.style.SUCCESS(f"Updated {updated_shipments} shipments."))

    @staticmethod
    def _max_seq(queryset, suffix_sep):
        """Highest existing sequence number already used for this group (0 if none)."""
        max_num = 0
        for display_id in queryset.exclude(Q(display_id__isnull=True) | Q(display_id='')).values_list('display_id', flat=True):
            try:
                num = int(display_id.rsplit(suffix_sep, 1)[1])
            except (ValueError, IndexError):
                continue
            max_num = max(max_num, num)
        return max_num

    def _backfill(self, queryset, group_key, existing_max, id_prefix):
        objects = list(queryset)
        if not objects:
            self.stdout.write("Nothing to update.")
            return 0

        self.stdout.write(f"Found {len(objects)} to update.")

        next_seq = {}
        for obj in objects:
            key = group_key(obj)
            if key not in next_seq:
                next_seq[key] = existing_max(key) + 1
            obj.display_id = f"{id_prefix(obj)}{next_seq[key]:03d}"
            next_seq[key] += 1

        with transaction.atomic():
            for i in range(0, len(objects), BATCH_SIZE):
                type(objects[0]).objects.bulk_update(objects[i:i + BATCH_SIZE], ['display_id'])

        return len(objects)
