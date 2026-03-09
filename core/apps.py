# core/apps.py
from django.apps import AppConfig

"""
DJANGO APP CONFIGURATION
------------------------
This file is the 'Registry' for the 'core' application.
It tells Django how the app should behave when the server starts up.

A critical role of AppConfig is the 'ready()' method. This is where
initialization logic goes—most importantly, connecting 'Signals'.
"""

class CoreConfig(AppConfig):
    # Sets the default data type for Primary Keys in this app (64-bit Integers)
    default_auto_field = 'django.db.models.BigAutoField'

    # The internal name of the application
    name = 'core'

    def ready(self):
        """
        THE STARTUP HOOK
        ----------------
        This method is called by Django as soon as the app is loaded.
        """
        # We MUST import signals here to 'register' them.
        # If we don't, our post_save triggers (like updating tags when
        # products change) will never run.
        import core.signals
