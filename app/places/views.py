from django.shortcuts import render
from core.model_mixins import UUIDPrimaryKeyMixin

# Create your views here.

class Places(UUIDPrimaryKeyMixin):
    google_place_id = models.CharField(max_length=255, unique=True)
    name = models.CharField(max_length=255, blank=True)
    lat = models.FloatField(null=True, blank=True)
    lng = models.FloatField(null=True, blank=True)
