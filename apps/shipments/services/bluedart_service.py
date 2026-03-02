"""
BlueDart Tracking API Service.

BlueDart uses a REST API for tracking.
Contact BlueDart for API credentials and documentation.
"""

import os
import requests
import logging
from typing import Optional, Dict, Any
from datetime import datetime

from .dhl_service import TrackingResult

logger = logging.getLogger(__name__)


class BlueDartService:
    """BlueDart Tracking API Integration."""
    
    # BlueDart tracking API endpoint (REST)
    BASE_URL = "https://api.bluedart.com/tracking/v1/trackshipment"
    
    # Status mapping from BlueDart to our internal statuses
    STATUS_MAP = {
        # Pickup/Processing
        'picked up': 'dispatched',
        'shipment picked up': 'dispatched',
        'manifested': 'dispatched',
        'in transit': 'in_transit',
        'arrived at hub': 'in_transit',
        'departed hub': 'in_transit',
        'received at destination': 'in_transit',
        
        # Out for Delivery
        'out for delivery': 'out_for_delivery',
        'with delivery agent': 'out_for_delivery',
        
        # Delivered
        'delivered': 'delivered',
        'shipment delivered': 'delivered',
        
        # Issues
        'undelivered': 'out_for_delivery',
        'delivery attempted': 'out_for_delivery',
        'rto initiated': 'returned',
        'returned': 'returned',
        'returned to origin': 'returned',
    }
    
    def __init__(self):
        self.license_key = ''
        self.login_id = ''
        try:
            from apps.notifications.models import AppSettings
            app_settings = AppSettings.get_settings()
            if app_settings.bluedart_enabled:
                self.license_key = app_settings.bluedart_license_key or ''
                self.login_id = app_settings.bluedart_login_id or ''
        except Exception:
            pass
        if not self.license_key:
            self.license_key = os.environ.get('BLUEDART_LICENSE_KEY', '')
        if not self.login_id:
            self.login_id = os.environ.get('BLUEDART_LOGIN_ID', '')
        
        if not self.license_key or not self.login_id:
            logger.warning("BlueDart credentials not set. BlueDart tracking will not work.")
    
    def _get_headers(self) -> Dict[str, str]:
        """Get headers for API request."""
        return {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'LicenseKey': self.license_key,
        }
    
    def _map_status(self, bluedart_status: str) -> str:
        """Map BlueDart status to our internal status."""
        status_lower = bluedart_status.lower()
        
        for key, value in self.STATUS_MAP.items():
            if key in status_lower:
                return value
        
        # Default to in_transit if unknown
        return 'in_transit'
    
    def track(self, tracking_number: str) -> TrackingResult:
        """
        Track a shipment by AWB number.
        
        Args:
            tracking_number: BlueDart AWB number
            
        Returns:
            TrackingResult with status and events
        """
        if not self.license_key:
            return TrackingResult(
                success=False,
                status='',
                status_description='',
                location='',
                timestamp=None,
                raw_status='',
                events=[],
                error='BlueDart credentials not configured'
            )
        
        try:
            payload = {
                "handler": "tnt",
                "action": "custawbquery",
                "loginid": self.login_id,
                "lickey": self.license_key,
                "numbers": tracking_number,
                "format": "json"
            }
            
            response = requests.post(
                self.BASE_URL,
                json=payload,
                headers=self._get_headers(),
                timeout=30
            )
            
            response.raise_for_status()
            data = response.json()
            
            # Parse response - BlueDart structure varies
            # This is a generic handler, actual structure may differ
            shipment_data = data.get('ShipmentData', {})
            
            if not shipment_data:
                # Try alternate response format
                shipment_data = data.get('data', {})
            
            if not shipment_data:
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
            
            # Get status info
            raw_status = shipment_data.get('Status', shipment_data.get('status', ''))
            status_description = shipment_data.get('StatusDescription', raw_status)
            location = shipment_data.get('Location', shipment_data.get('location', ''))
            
            # Parse timestamp
            timestamp = None
            timestamp_str = shipment_data.get('StatusDateTime', shipment_data.get('timestamp'))
            if timestamp_str:
                try:
                    # Try various date formats
                    for fmt in ['%Y-%m-%d %H:%M:%S', '%d-%m-%Y %H:%M:%S', '%Y-%m-%dT%H:%M:%S']:
                        try:
                            timestamp = datetime.strptime(timestamp_str, fmt)
                            break
                        except:
                            continue
                except:
                    pass
            
            # Parse scan history/events
            events = []
            scans = shipment_data.get('Scans', shipment_data.get('scans', []))
            
            for scan in scans:
                event_timestamp = None
                scan_date = scan.get('ScanDateTime', scan.get('datetime'))
                if scan_date:
                    try:
                        for fmt in ['%Y-%m-%d %H:%M:%S', '%d-%m-%Y %H:%M:%S']:
                            try:
                                event_timestamp = datetime.strptime(scan_date, fmt)
                                break
                            except:
                                continue
                    except:
                        pass
                
                events.append({
                    'status': scan.get('ScanType', scan.get('status', '')),
                    'description': scan.get('Instructions', scan.get('description', '')),
                    'location': scan.get('ScannedLocation', scan.get('location', '')),
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
            logger.error(f"BlueDart API error for {tracking_number}: {e}")
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
