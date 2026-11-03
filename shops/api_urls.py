"""
REST API URL routing for /api/v1/shops/.
"""

from django.urls import path
from . import api_views

urlpatterns = [
    path("", api_views.ShopListCreateAPIView.as_view(), name="api_shop_list_create"),
    path("<int:pk>/", api_views.ShopDetailAPIView.as_view(), name="api_shop_detail"),
    path("<int:pk>/status/", api_views.ShopStatusToggleAPIView.as_view(), name="api_shop_status_toggle"),
]

