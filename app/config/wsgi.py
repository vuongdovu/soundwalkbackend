"""
WSGI config for the Django application.

WSGI (Web Server Gateway Interface) is the traditional Python web server
interface. While this project primarily uses ASGI via Uvicorn, WSGI is
provided as a fallback for compatibility with traditional deployment options.

This file exposes the WSGI callable as a module-level variable named `application`.

For more information on this file, see:
https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_wsgi_application()
