from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tickets', '0007_remove_agentstate_config_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='chatmessage',
            name='hidden',
            field=models.BooleanField(default=False),
        ),
    ]
