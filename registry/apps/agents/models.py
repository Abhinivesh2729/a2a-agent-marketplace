from django.db import models


class Agent(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField()
    capabilities = models.JSONField(default=list)
    endpoint_url = models.URLField()
    status = models.CharField(max_length=20, default='active')
    registered_at = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'agents'

    def __str__(self):
        return self.name
