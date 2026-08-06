"""
Management command to sync shipment tracking status from carrier APIs.

Usage:
    python manage.py sync_tracking           # Sync all active shipments
    python manage.py sync_tracking --carrier=dhl  # Sync only DHL shipments
    python manage.py sync_tracking --shipment-id=xxx  # Sync specific shipment

Schedule with cron (every 30 minutes):
    */30 * * * * cd /path/to/project && python manage.py sync_tracking
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from apps.shipments.models import Shipment, TrackingEvent
from apps.shipments.services.carrier_factory import CarrierFactory
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Sync shipment tracking status from carrier APIs'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--carrier',
            type=str,
            help='Filter by carrier (dhl, bluedart)',
        )
        parser.add_argument(
            '--shipment-id',
            type=str,
            help='Sync a specific shipment by ID',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be updated without saving',
        )
    
    def handle(self, *args, **options):
        carrier_filter = options.get('carrier')
        shipment_id = options.get('shipment_id')
        dry_run = options.get('dry_run', False)
        
        # Get shipments to sync
        # Only sync shipments that are in transit-related statuses
        active_statuses = [
            'dispatched',
            'in_transit',
            'customs',
            'out_for_delivery',
        ]
        
        queryset = Shipment.objects.filter(
            status__in=active_statuses,
            tracking_number__isnull=False,
        ).exclude(tracking_number='')
        
        if carrier_filter:
            queryset = queryset.filter(carrier=carrier_filter.lower())
        
        if shipment_id:
            queryset = queryset.filter(id=shipment_id)
        
        # Filter to supported carriers only
        supported_carriers = ['dhl', 'bluedart']
        queryset = queryset.filter(carrier__in=supported_carriers)
        
        total = queryset.count()
        updated = 0
        errors = 0
        
        self.stdout.write(f"Found {total} shipments to sync...")
        
        for shipment in queryset:
            try:
                result = self._sync_shipment(shipment, dry_run)
                if result:
                    updated += 1
            except Exception as e:
                errors += 1
                logger.error(f"Error syncing shipment {shipment.id}: {e}")
                self.stdout.write(
                    self.style.ERROR(f"  ✗ {shipment.id}: {str(e)}")
                )
        
        # Summary
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Sync complete!"))
        self.stdout.write(f"  Total: {total}")
        self.stdout.write(f"  Updated: {updated}")
        self.stdout.write(f"  Errors: {errors}")
        
        if dry_run:
            self.stdout.write(self.style.WARNING("  (Dry run - no changes saved)"))
    
    def _sync_shipment(self, shipment: Shipment, dry_run: bool) -> bool:
        """Sync a single shipment."""
        
        self.stdout.write(f"  Syncing {shipment.tracking_number} ({shipment.carrier})...")
        
        # Get tracking info from carrier
        result = CarrierFactory.track_shipment(
            shipment.carrier,
            shipment.tracking_number
        )
        
        if not result or not result.success:
            error_msg = result.error if result else "Unknown error"
            self.stdout.write(
                self.style.WARNING(f"    → Could not track: {error_msg}")
            )
            return False
        
        # Check if status changed
        old_status = shipment.status
        new_status = result.status
        
        if old_status == new_status:
            self.stdout.write(f"    → No change (still {old_status})")
            return False
        
        self.stdout.write(
            self.style.SUCCESS(f"    → Status: {old_status} → {new_status}")
        )
        
        if dry_run:
            return True
        
        # Update shipment status
        shipment.status = new_status
        
        # Update timestamps
        if new_status == 'delivered' and not shipment.delivered_at:
            shipment.delivered_at = result.timestamp or timezone.now()
        elif new_status == 'dispatched' and not shipment.dispatched_at:
            shipment.dispatched_at = result.timestamp or timezone.now()
        
        # Existing (status, timestamp) pairs for this shipment, one query instead of one per event
        existing_keys = set(
            TrackingEvent.objects.filter(shipment=shipment)
            .values_list('status', 'event_timestamp')
        )

        new_events = [
            TrackingEvent(
                shipment=shipment,
                status=event.get('status', ''),
                description=event.get('description', ''),
                location=event.get('location', ''),
                event_timestamp=event.get('timestamp'),
                raw_data=event,
            )
            for event in result.events
            if (event.get('status', ''), event.get('timestamp')) not in existing_keys
        ]

        with transaction.atomic():
            shipment.save()
            if new_events:
                TrackingEvent.objects.bulk_create(new_events, batch_size=500)

        return True
