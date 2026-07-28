from apps.accounts.models import User, KYCDocument
from apps.locker.models import Parcel, ReturnRequest, DiscardRequest
from apps.shipments.models import Shipment
from apps.payments.models import StorageFee, Payment
from django.db.models import Sum


def dashboard_callback(request, context):
    """
    Callback function for Django Unfold to inject database statistics
    into the admin index context.
    """
    # 1. Parcel Statistics
    total_parcels = Parcel.objects.count()
    parcels_pending = Parcel.objects.filter(status='pending').count()
    parcels_action = Parcel.objects.filter(status='action_required').count()
    parcels_approved = Parcel.objects.filter(status='approved').count()

    # 2. Shipment Statistics
    total_shipments = Shipment.objects.count()
    shipments_packing = Shipment.objects.filter(status='packing').count()
    shipments_transit = Shipment.objects.filter(status__in=['dispatched', 'in_transit', 'customs', 'out_for_delivery']).count()
    shipments_decl_pending = Shipment.objects.filter(status='declaration_pending').count()

    # 3. Request Statistics
    pending_returns = ReturnRequest.objects.filter(status='requested').count()
    pending_discards = DiscardRequest.objects.filter(status='requested').count()
    pending_kyc = KYCDocument.objects.filter(status='pending').count()

    # 4. Payments & Revenue Statistics
    total_payments = Payment.objects.filter(status='captured').count()
    captured_payment_sum = Payment.objects.filter(status='captured').aggregate(total=Sum('amount'))['total'] or 0
    pending_fees = StorageFee.objects.filter(status='pending').count()
    pending_fees_sum = StorageFee.objects.filter(status='pending').aggregate(total=Sum('fee_amount'))['total'] or 0

    # User Statistics
    total_users = User.objects.filter(is_active=True).count()

    context.update({
        # Summary KPI Cards
        "kpis": [
            {
                "title": "Total Active Users",
                "value": total_users,
                "icon": "people",
                "color": "primary"
            },
            {
                "title": "Total Received Parcels",
                "value": total_parcels,
                "icon": "inventory_2",
                "color": "primary"
            },
            {
                "title": "Active Shipments",
                "value": shipments_transit + shipments_packing,
                "icon": "local_shipping",
                "color": "primary"
            },
            {
                "title": "Revenue (Captured)",
                "value": f"₹{captured_payment_sum:,.2f}",
                "icon": "payments",
                "color": "success"
            }
        ],
        # Detailed Stats Breakdown
        "parcel_stats": {
            "pending": parcels_pending,
            "action_required": parcels_action,
            "approved": parcels_approved,
        },
        "shipment_stats": {
            "declaration_pending": shipments_decl_pending,
            "packing": shipments_packing,
            "in_transit": shipments_transit,
        },
        "pending_actions": {
            "kyc_approvals": pending_kyc,
            "return_requests": pending_returns,
            "discard_requests": pending_discards,
        },
        "financial_stats": {
            "total_payments_count": total_payments,
            "pending_storage_fees_count": pending_fees,
            "pending_storage_fees_total": f"₹{pending_fees_sum:,.2f}"
        }
    })
    return context
