from django.urls import path
from . import views

app_name = 'help_module'

urlpatterns = [
    path('', views.help_index, name='index'),
    path('<slug:topic_slug>/', views.help_detail, name='detail'),
]
