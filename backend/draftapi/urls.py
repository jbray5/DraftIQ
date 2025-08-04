from django.urls import path
from .views import suggest_next_pick_view

urlpatterns = [
    path('suggest/', suggest_next_pick_view),
]
