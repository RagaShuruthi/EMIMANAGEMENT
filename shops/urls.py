"""
URL routing for the Shop Management module.
"""

from django.urls import path
from . import views

urlpatterns = [
    path("", views.shop_list_view, name="shop_list"),
    path("add/", views.add_shop_view, name="add_shop"),
    path("<int:pk>/", views.shop_detail_view, name="shop_detail"),
    path("<int:pk>/edit/", views.edit_shop_view, name="edit_shop"),
    path("<int:pk>/status/", views.shop_status_view, name="shop_status"),
    path("<int:pk>/account/", views.shop_account_view, name="shop_account"),
]
