from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Login account. Asmah Finance has a single user: the Django superuser.
    Only superusers can sign in to the app or call the API."""

    phone = models.CharField(max_length=30, blank=True)

    @property
    def display_name(self):
        return self.get_full_name() or self.username

    def __str__(self):
        return self.display_name
