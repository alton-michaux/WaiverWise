from django.db import models


class Explanation(models.Model):
    """Cached Claude explanation, keyed by a hash of the model, page kind, and data sent."""

    key = models.CharField(max_length=64, unique=True)
    kind = models.CharField(max_length=20)
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
