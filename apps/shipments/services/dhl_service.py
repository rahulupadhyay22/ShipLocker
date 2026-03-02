"""
DHL Express Tracking API Service.

Uses the DHL Express MyDHL API for tracking shipments.
API Documentation: https://developer.dhl.com/api-reference/shipment-tracking
"""

import os
import requests
import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class TrackingResult:
    """Standardized tracking result across all carriers."""
    success: bool
    status: str  # Our internal status code
    status_description: str
    location: str
    timestamp: Optional[datetime]
    raw_status: str  # Original carrier status
    events: list  # List of tracking events
    error: Optional[str] = None


class DHLService:
    """DHL Express Tracking API Integration."""
    
    BASE_URL = "https://api-eu.dhl.com/track/shipments"
    
    # Status mapping from DHL to our internal statuses
    STATUS_MAP = {
        # Pre-transit
        'pre-transit': 'packing',
        'shipment information received': 'packing',
        
        # In Transit
        'transit': 'in_transit',
        'in transit': 'in_transit',
        'departed facility': 'in_transit',
        'arrived at facility': 'in_transit',
        'processed': 'in_transit',
        'clearance processing': 'in_transit',
        
        # Customs
        'customs': 'customs',
        'held at customs': 'customs',
        'clearance event': 'customs',
        'customs clearance': 'customs',
        
        # Out for Delivery
        'out for delivery': 'out_for_delivery',
        'with delivery courier': 'out_for_delivery',
        
        # Delivered
        'delivered': 'delivered',
        'shipment delivered': 'delivered',
        
        # Failed/Returned
        'delivery attempt': 'out_for_delivery',
        'returned': 'returned',
        'return': 'returned',
    }
    
    def __init__(self):
        self.api_key = ''
        try:
            from apps.notifications.models import AppSettings
            app_settings = AppSettings.get_settings()
            if app_settings.dhl_enabled and app_settings.dhl_api_key:
                self.api_key = app_settings.dhl_api_key
        except Exception:
            pass
        if not self.api_key:
            self.api_key = os.environ.get('DHL_API_KEY', '')
        if not self.api_key:
            logger.warning("DHL_API_KEY not set. DHL tracking will not work.")
    
    def _get_headers(self) -> Dict[str, str]:
        """Get headers for API request."""
        return {
            'DHL-API-Key': self.api_key,
            'Accept': 'application/json',
        }
    
    def _map_status(self, dhl_status: str) -> str:
        """Map DHL status to our internal status."""
        status_lower = dhl_status.lower()
        
        for key, value in self.STATUS_MAP.items():
            if key in status_lower:
                return value
        
        # Default to in_transit if unknown
        return 'in_transit'
    
    def track(self, tracking_number: str) -> TrackingResult:
        """
        Track a shipment by tracking number.
        
        Args:
            tracking_number: DHL tracking/waybill number
            
        Returns:
            TrackingResult with status and events
        """
        if not self.api_key:
            return TrackingResult(
                success=False,
                status='',
                status_description='',
                location='',
                timestamp=None,
                raw_status='',
                events=[],
                error='DHL API key not configured'
            )
        
        try:
            response = requests.get(
                self.BASE_URL,
                params={'trackingNumber': tracking_number},
                headers=self._get_headers(),
                timeout=30
            )
            
            if response.status_code == 404:
                return TrackingResult(
                    success=False,
                    status='',
                    status_description='Tracking number not found',
                    location='',
                    timestamp=None,
                    raw_status='',
                    events=[],
                    error='Tracking number not found'
                )
            
            response.raise_for_status()
            data = response.json()
            
            # Parse shipments
            shipments = data.get('shipments', [])
            if not shipments:
                return TrackingResult(
                    success=False,
                    status='',
                    status_description='No shipment data',
                    location='',
                    timestamp=None,
                    raw_status='',
                    events=[],
                    error='No shipment data found'
                )
            
            shipment = shipments[0]
            
            # Get latest status
            status_info = shipment.get('status', {})
            raw_status = status_info.get('status', '')
            status_description = status_info.get('description', '')
            location = status_info.get('location', {}).get('address', {}).get('addressLocality', '')
            
            # Parse timestamp
            timestamp = None
            timestamp_str = status_info.get('timestamp')
            if timestamp_str:
                try:
                    timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                except:
                    pass
            
            # Parse events
            events = []
            for event in shipment.get('events', []):
                event_timestamp = None
                if event.get('timestamp'):
                    try:
                        event_timestamp = datetime.fromisoformat(
                            event['timestamp'].replace('Z', '+00:00')
                        )
                    except:
                        pass
                
                events.append({
                    'status': event.get('status', ''),
                    'description': event.get('description', ''),
                    'location': event.get('location', {}).get('address', {}).get('addressLocality', ''),
                    'timestamp': event_timestamp,
                })
            
            return TrackingResult(
                success=True,
                status=self._map_status(raw_status),
                status_description=status_description,
                location=location,
                timestamp=timestamp,
                raw_status=raw_status,
                events=events,
            )
            
        except requests.exceptions.RequestException as e:
            logger.error(f"DHL API error for {tracking_number}: {e}")
            return TrackingResult(
                success=False,
                status='',
                status_description='',
                location='',
                timestamp=None,
                raw_status='',
                events=[],
                error=f'API error: {str(e)}'
            )
