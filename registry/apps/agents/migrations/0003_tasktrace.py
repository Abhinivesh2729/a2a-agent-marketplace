# Generated manually for TaskTrace model

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('agents', '0002_alter_agent_id'),
    ]

    operations = [
        migrations.CreateModel(
            name='TaskTrace',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('task_id', models.CharField(db_index=True, max_length=100, unique=True)),
                ('user_input', models.TextField(blank=True)),
                ('goal', models.TextField(blank=True)),
                ('selection_mode', models.CharField(default='auto', max_length=20)),
                ('status', models.CharField(default='pending', max_length=20)),
                ('hops', models.JSONField(default=list)),
                ('final_result', models.JSONField(blank=True, null=True)),
                ('error', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
            ],
            options={
                'db_table': 'task_traces',
                'ordering': ['-created_at'],
            },
        ),
    ]
