import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('locker', '0008_backfill_batches_for_existing_parcels'),
        ('personal_shop', '0003_research_fee_amount'),
    ]

    operations = [
        migrations.AddField(
            model_name='personalshoprequest',
            name='source_parcel',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='referencing_personal_shop_requests', to='locker.parcel',
                help_text="Existing trunk parcel the user picked as the basis for a Boutique Purchase "
                          "(e.g. 'buy from my trunk'), distinct from `parcel` (the new parcel created "
                          "once this request is purchased and delivered).",
            ),
        ),
    ]
