# Custom authtoken migration - placeholder for compatibility

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("authtoken", "0003_tokenproxy"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="tokenproxy",
            options={"verbose_name": "token"},
        ),
    ]
