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


class TaskTrace(models.Model):
    task_id = models.CharField(max_length=100, unique=True, db_index=True)
    user_input = models.TextField(blank=True)
    goal = models.TextField(blank=True)
    selection_mode = models.CharField(max_length=20, default='auto')
    status = models.CharField(max_length=20, default='pending')
    hops = models.JSONField(default=list)
    final_result = models.JSONField(null=True, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'task_traces'
        ordering = ['-created_at']

    def __str__(self):
        return self.task_id
