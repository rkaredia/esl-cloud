"""
SAIS MAIN URL ROUTING (THE MAP)
------------------------------
This file is the 'Dispatcher' for the entire project.
When a user types a URL (like /admin/ or /help/), Django looks here
to find out which Python view should handle that request.

If you are coming from a Data Warehouse background, think of this as
the 'Logical Schema' that maps public endpoints to physical logic.

PROJECT STRUCTURE:
- /admin/: The core ESL management interface (Branded Admin).
- /help/: The user guide module.
- /set-store/: A functional endpoint for switching the active store.
"""

from django.contrib import admin
from core.views import select_store, set_active_store
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from core import views
from core.admin import admin_site # Our custom SAIS Control Panel
from django.views.generic import RedirectView
from django.conf.urls import handler500
from django.urls import path


urlpatterns = [
    # 1. Store Selection Workflow
    path('admin/select-store/', select_store, name='select_store'),
    path('set-store/<int:store_id>/', views.set_active_store, name='set_active_store'),
    
    # 2. Main Admin Interface
    # EDUCATIONAL: We replace the default 'admin.site.urls' with our custom
    # 'admin_site.urls' instance defined in core/admin/base.py.
    path('admin/', admin_site.urls),
    
    # 3. Help Module
    # include() tells Django to look at 'help_module/urls.py' for further routing.
    path('help/', include('help_module.urls')),

    # 4. Root Redirect: If you visit the base domain, send you straight to admin.
    path('', RedirectView.as_view(url='/admin/', permanent=True)),
]

# MEDIA HANDLING (Tag Images)
# ---------------------------
# In production, files are usually served by Nginx or S3.
# In Development (DEBUG=True), Django handles serving the BMP images itself.
if settings.DEBUG:
    # This line tells Django: "If the URL starts with /media/, look for the
    # file in the local 'media/' folder".
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
