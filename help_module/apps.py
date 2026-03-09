from django.apps import AppConfig

"""
HELP MODULE CONFIGURATION
-------------------------
Standard Django application registry for the user documentation module.
"""

class HelpModuleConfig(AppConfig):
    # Sets 64-bit integer IDs as the default for this app's models
    default_auto_field = "django.db.models.BigAutoField"

    # Internal module name
    name = "help_module"
