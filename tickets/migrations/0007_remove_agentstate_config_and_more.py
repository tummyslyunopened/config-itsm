import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tickets', '0006_agent_models'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # Drop old models outright before touching ScheduleEntry
        migrations.DeleteModel(name='AgentState'),
        migrations.DeleteModel(name='AgentConfig'),

        # Create the new Agent model
        migrations.CreateModel(
            name='Agent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100)),
                ('system_prompt', models.TextField()),
                ('priority', models.IntegerField()),
                ('status', models.CharField(
                    choices=[('deliberating', 'Deliberating'), ('committed', 'Committed')],
                    default='deliberating', max_length=20,
                )),
                ('document', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('engineer', models.ForeignKey(
                    limit_choices_to={'profile__role': 'engineer'},
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='agents', to=settings.AUTH_USER_MODEL,
                )),
                ('ops_user', models.OneToOneField(
                    limit_choices_to={'profile__role': 'ops'},
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='agent', to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'ordering': ['priority'],
                'unique_together': {('engineer', 'priority')},
            },
        ),

        # Drop the old CharField and re-add as a nullable FK (loses old string data, which is fine)
        migrations.RemoveField(model_name='scheduleentry', name='created_by'),
        migrations.AddField(
            model_name='scheduleentry',
            name='created_by',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='schedule_entries',
                to='tickets.agent',
            ),
        ),

        # One-on-one agent chat
        migrations.CreateModel(
            name='AgentMessage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('role', models.CharField(
                    choices=[('user', 'User'), ('agent', 'Agent')],
                    max_length=10,
                )),
                ('body', models.TextField()),
                ('sent_at', models.DateTimeField(auto_now_add=True)),
                ('agent', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='messages', to='tickets.agent',
                )),
            ],
            options={'ordering': ['sent_at']},
        ),
    ]
