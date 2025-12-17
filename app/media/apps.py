"""Django app configuration for media app."""

from django.apps import AppConfig


class MediaConfig(AppConfig):
    """Configuration for the media app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "media"
    verbose_name = "Media"

    def ready(self) -> None:
        """Connect signal handlers when app is ready."""
        from media.signals import connect_signals

        connect_signals()
