from django.urls import path
from . import views

"""
HELP MODULE ROUTING
-------------------
Defines the URL structure for the documentation system.
The 'app_name' allows for namespaced URLs (e.g., {% url 'help_module:index' %}).
"""

app_name = 'help_module'

urlpatterns = [
    # Base help center page (Grid view)
    path('', views.help_index, name='index'),

    # Detail view for a specific guide (e.g., /help/getting-started/)
    path('<slug:topic_slug>/', views.help_detail, name='detail'),
]
