"""
Carrier Service Factory.

Provides a unified interface to get the appropriate tracking service
based on the shipment carrier.
"""

from typing import Optional
from .dhl_service import DHLService, TrackingResult
from .bluedart_service import BlueDartService


class CarrierFactory:
    """Factory to get the appropriate carrier service."""
    
    # Map carrier codes to service classes
    CARRIER_SERVICES = {
        'dhl': DHLService,
        'bluedart': BlueDartService,
    }
    
    @classmethod
    def get_service(cls, carrier: str):
        """
        Get the tracking service for a carrier.
        
        Args:
            carrier: Carrier code (e.g., 'dhl', 'bluedart')
            
        Returns:
            Carrier service instance or None if not supported
        """
        carrier_lower = carrier.lower()
        service_class = cls.CARRIER_SERVICES.get(carrier_lower)
        
        if service_class:
            return service_class()
        
        return None
    
    @classmethod
    def track_shipment(cls, carrier: str, tracking_number: str) -> Optional[TrackingResult]:
        """
        Track a shipment using the appropriate carrier service.
        
        Args:
            carrier: Carrier code
            tracking_number: Tracking/AWB number
            
        Returns:
            TrackingResult or None if carrier not supported
        """
        service = cls.get_service(carrier)
        if service:
            return service.track(tracking_number)
        
        return TrackingResult(
            success=False,
            status='',
            status_description='',
            location='',
            timestamp=None,
            raw_status='',
            events=[],
            error=f'Carrier "{carrier}" not supported for tracking'
        )
    
    @classmethod
    def is_supported(cls, carrier: str) -> bool:
        """Check if a carrier is supported for tracking."""
        return carrier.lower() in cls.CARRIER_SERVICES
