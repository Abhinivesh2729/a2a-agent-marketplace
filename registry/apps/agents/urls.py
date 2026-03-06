from django.urls import path
from . import views

urlpatterns = [
    path('agents/register', views.register_agent),
    path('agents/list', views.list_agents),
    path('agents/search', views.search_agents),
    path('orchestrate', views.orchestrate),
]
