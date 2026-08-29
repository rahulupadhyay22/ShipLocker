from django.db import migrations


def seed_charge(apps, schema_editor):
    ServiceCharge = apps.get_model('content', 'ServiceCharge')
    ServiceCharge.objects.get_or_create(
        code='return_service_charge',
        defaults={
            'name': 'Return Service Charge',
            'description': 'Flat fee charged when a customer requests a parcel return.',
            'charge_type': 'flat',
            'percentage_rate': None,
            'amount': '199.00',
            'currency': 'INR',
            'is_active': True,
        },
    )


def unseed_charge(apps, schema_editor):
    ServiceCharge = apps.get_model('content', 'ServiceCharge')
    ServiceCharge.objects.filter(code='return_service_charge').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('content', '0011_faq'),
    ]

    operations = [
        migrations.RunPython(seed_charge, unseed_charge),
    ]
