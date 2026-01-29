from django.core.management.base import BaseCommand
from apps.locker.models import Parcel, generate_parcel_id
from apps.shipments.models import Shipment, generate_shipment_id
from django.db import transaction

class Command(BaseCommand):
    help = 'Backfill display_id for Parcels and Shipments'

    def handle(self, *args, **kwargs):
        self.stdout.write("Starting backfill process...")
        
        from django.db.models import Q
        # Backfill Parcels
        parcels = Parcel.objects.filter(Q(display_id__isnull=True) | Q(display_id=''))
        self.stdout.write(f"Found {parcels.count()} parcels to update.")
        
        updated_parcels = 0
        for parcel in parcels:
            with transaction.atomic():
                if not parcel.display_id:
                    parcel.display_id = generate_parcel_id(parcel.locker)
                    parcel.save(update_fields=['display_id'])
                    updated_parcels += 1
        
        self.stdout.write(self.style.SUCCESS(f"Updated {updated_parcels} parcels."))

        # Backfill Shipments
        shipments = Shipment.objects.filter(Q(display_id__isnull=True) | Q(display_id=''))
        self.stdout.write(f"Found {shipments.count()} shipments to update.")
        
        updated_shipments = 0
        for shipment in shipments:
            with transaction.atomic():
                if not shipment.display_id:
                    shipment.display_id = generate_shipment_id(shipment.user)
                    shipment.save(update_fields=['display_id'])
                    updated_shipments += 1
                    
        self.stdout.write(self.style.SUCCESS(f"Updated {updated_shipments} shipments."))
