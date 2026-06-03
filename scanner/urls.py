from django.urls import path

from .views import WorkingNodesView

urlpatterns = [
    path('confs/', WorkingNodesView.as_view(), name='confs'),
]
