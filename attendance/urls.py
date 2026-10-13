from django.urls import path
from . import views

app_name = "attendance"

urlpatterns = [
    path("live/", views.live_camera_view, name="live_camera"),
    path("video-feed/", views.video_feed_view, name="video_feed"),
    path("api/events/", views.attendance_events_api, name="events_api"),
]
