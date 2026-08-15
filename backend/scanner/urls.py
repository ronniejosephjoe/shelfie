from django.urls import path

from . import views

app_name = "scanner"

urlpatterns = [
    path("scans/", views.ScanListCreateView.as_view(), name="scan-list-create"),
    path("scans/<uuid:pk>/", views.ScanDetailView.as_view(), name="scan-detail"),
    path("detected-books/<uuid:pk>/decide/", views.DetectedBookDecisionView.as_view(), name="detected-book-decide"),
    path("library/", views.LibraryListView.as_view(), name="library-list"),
]
