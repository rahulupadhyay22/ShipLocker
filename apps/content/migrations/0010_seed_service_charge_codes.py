from django.db import migrations


TRUNKASSIST_CHARGES = [
    {
        'code': 'trunkassist_product_link', 'name': 'TrunkAssist – Product Link',
        'description': '5% of item value, minimum ₹199.',
        'charge_type': 'percentage', 'percentage_rate': '5.00', 'amount': '199.00',
    },
    {
        'code': 'trunkassist_image_search', 'name': 'TrunkAssist – Image Search',
        'description': '6% of item value, minimum ₹299.',
        'charge_type': 'percentage', 'percentage_rate': '6.00', 'amount': '299.00',
    },
    {
        'code': 'trunkassist_cart_screenshot', 'name': 'TrunkAssist – Cart Screenshot',
        'description': '6% of item value, minimum ₹299.',
        'charge_type': 'percentage', 'percentage_rate': '6.00', 'amount': '299.00',
    },
    {
        'code': 'trunkassist_boutique_purchase', 'name': 'TrunkAssist – Boutique Purchase',
        'description': '7% of item value, minimum ₹399, plus applicable travel/local delivery.',
        'charge_type': 'percentage', 'percentage_rate': '7.00', 'amount': '399.00',
    },
    {
        'code': 'trunkassist_local_shop_purchase', 'name': 'TrunkAssist – Local Shop Purchase',
        'description': 'Flat ₹499, plus actual travel/transport.',
        'charge_type': 'flat', 'percentage_rate': None, 'amount': '499.00',
    },
    {
        'code': 'trunkassist_custom_request', 'name': 'TrunkAssist – Custom Request',
        'description': 'Starting from ₹499 — final fee based on complexity.',
        'charge_type': 'flat', 'percentage_rate': None, 'amount': '499.00',
    },
]


def seed_charges(apps, schema_editor):
    ServiceCharge = apps.get_model('content', 'ServiceCharge')

    for entry in TRUNKASSIST_CHARGES:
        ServiceCharge.objects.get_or_create(
            code=entry['code'],
            defaults={
                'name': entry['name'],
                'description': entry['description'],
                'charge_type': entry['charge_type'],
                'percentage_rate': entry['percentage_rate'],
                'amount': entry['amount'],
                'currency': 'INR',
                'is_active': True,
            },
        )

    # Give any existing Consolidation Fee row (previously looked up by a
    # fragile name__icontains='consolidat' match) a stable code, rather than
    # creating a new one — preserves whatever amount an admin already set.
    ServiceCharge.objects.filter(
        name__icontains='consolidat', code__isnull=True,
    ).update(code='consolidation_fee')


def unseed_charges(apps, schema_editor):
    ServiceCharge = apps.get_model('content', 'ServiceCharge')
    codes = [entry['code'] for entry in TRUNKASSIST_CHARGES]
    ServiceCharge.objects.filter(code__in=codes).delete()
    ServiceCharge.objects.filter(code='consolidation_fee').update(code=None)


class Migration(migrations.Migration):

    dependencies = [
        ('content', '0009_servicecharge_pricing_fields'),
    ]

    operations = [
        migrations.RunPython(seed_charges, unseed_charges),
    ]
