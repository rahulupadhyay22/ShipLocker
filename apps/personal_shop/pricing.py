# Maps each TrunkAssist request_type to the ServiceCharge.code that prices it
# (apps/content/models.py). The actual rate/minimum numbers live in the
# database now, admin-editable from the Service Charges page — not here.
REQUEST_TYPE_TO_SERVICE_CHARGE_CODE = {
    'product_link': 'trunkassist_product_link',
    'image_search': 'trunkassist_image_search',
    'cart_screenshot': 'trunkassist_cart_screenshot',
    'boutique_purchase': 'trunkassist_boutique_purchase',
    'local_shop_purchase': 'trunkassist_local_shop_purchase',
    'custom_request': 'trunkassist_custom_request',
}


def suggested_service_fee(request_type, product_value=None):
    """Returns the admin-configured suggested fee for this request_type, or
    None if no active ServiceCharge is configured for it (staff set one
    manually — custom_request in particular is always just a starting point,
    'final fee based on complexity' per the CamelTrunk pricing guide)."""
    from apps.content.models import ServiceCharge

    code = REQUEST_TYPE_TO_SERVICE_CHARGE_CODE.get(request_type)
    if code is None:
        return None
    charge = ServiceCharge.objects.filter(code=code, is_active=True).first()
    if charge is None:
        return None
    return charge.compute(product_value)
