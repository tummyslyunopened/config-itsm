from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('tickets', '0009_agent_is_scheduler'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='agent',
            name='one_scheduler_per_engineer',
        ),
        migrations.RemoveField(
            model_name='agent',
            name='is_scheduler',
        ),
    ]
