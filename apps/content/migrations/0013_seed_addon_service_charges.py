from django.db import migrations


ADDON_CHARGES = [
    {
        'code': 'addon_insurance', 'name': 'Add-on: Insurance',
        'description': 'Optional coverage for the full declared value of your shipment against loss or damage in transit. 2% of declared value, minimum ₹99.',
        'charge_type': 'percentage', 'percentage_rate': '2.00', 'amount': '99.00',
    },
    {
        'code': 'addon_extra_photos', 'name': 'Add-on: Extra Photos',
        'description': 'Extra photos of your items before packing, beyond the standard intake set. Flat ₹149.',
        'charge_type': 'flat', 'percentage_rate': None, 'amount': '149.00',
    },
    {
        'code': 'addon_priority_packing', 'name': 'Add-on: Priority Packing',
        'description': 'Your shipment jumps the warehouse packing queue. Flat ₹299.',
        'charge_type': 'flat', 'percentage_rate': None, 'amount': '299.00',
    },
    {
        'code': 'addon_gift_wrapping', 'name': 'Add-on: Gift Wrapping',
        'description': 'Your shipment is gift-wrapped before it ships. Flat ₹99.',
        'charge_type': 'flat', 'percentage_rate': None, 'amount': '99.00',
    },
]


def seed_charges(apps, schema_editor):
    ServiceCharge = apps.get_model('content', 'ServiceCharge')
    for entry in ADDON_CHARGES:
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


def unseed_charges(apps, schema_editor):
    ServiceCharge = apps.get_model('content', 'ServiceCharge')
    codes = [entry['code'] for entry in ADDON_CHARGES]
    ServiceCharge.objects.filter(code__in=codes).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('content', '0012_seed_return_service_charge'),
    ]

    operations = [
        migrations.RunPython(seed_charges, unseed_charges),
    ]
