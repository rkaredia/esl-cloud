"""
URL configuration for esl_cloud project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from core.views import select_store, set_active_store
from django.urls import path, include
from django.conf import settings  # Add this
from django.conf.urls.static import static  # Add this
from core import views
from core.admin import admin_site # Import your custom site
from django.views.generic import RedirectView
from django.conf.urls import handler500
from django.urls import path


urlpatterns = [
    path('admin/select-store/', select_store, name='select_store'),
    
    # CHANGE THIS LINE:
    # path('admin/', admin.site.urls),
    
    # TO THIS:
    path('admin/', admin_site.urls), # Use the instance that has the 'sais_admin' name
    path('set-store/<int:store_id>/', views.set_active_store, name='set_active_store'),
    path('', RedirectView.as_view(url='/admin/', permanent=True)),
]

# This line is the "magic" that tells Django to serve the tag images
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)