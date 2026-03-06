from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = []

    operations = [
        migrations.CreateModel(
            name='Agent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True)),
                ('name', models.CharField(max_length=100, unique=True)),
                ('description', models.TextField()),
                ('capabilities', models.JSONField(default=list)),
                ('endpoint_url', models.URLField()),
                ('status', models.CharField(default='active', max_length=20)),
                ('registered_at', models.DateTimeField(auto_now_add=True)),
                ('last_seen', models.DateTimeField(auto_now=True)),
            ],
            options={'db_table': 'agents'},
        ),
    ]
