from django.db import migrations, models


def designate_scheduler_per_engineer(apps, schema_editor):
    """For each engineer, mark their highest-priority (lowest priority number)
    agent as the scheduler. Engineers with no agents are unaffected."""
    Agent = apps.get_model('tickets', 'Agent')
    seen_engineers = set()
    for agent in Agent.objects.order_by('engineer_id', 'priority'):
        if agent.engineer_id in seen_engineers:
            continue
        seen_engineers.add(agent.engineer_id)
        agent.is_scheduler = True
        agent.save(update_fields=['is_scheduler'])


def unset_all_schedulers(apps, schema_editor):
    Agent = apps.get_model('tickets', 'Agent')
    Agent.objects.update(is_scheduler=False)


class Migration(migrations.Migration):

    dependencies = [
        ('tickets', '0008_chatmessage_hidden'),
    ]

    operations = [
        migrations.AddField(
            model_name='agent',
            name='is_scheduler',
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(designate_scheduler_per_engineer, unset_all_schedulers),
        migrations.AddConstraint(
            model_name='agent',
            constraint=models.UniqueConstraint(
                fields=['engineer'],
                condition=models.Q(is_scheduler=True),
                name='one_scheduler_per_engineer',
            ),
        ),
    ]
