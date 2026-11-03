"""
REST API Views for Shop Management.
Enforces authentication, role permissions, and tenant isolation.
"""

from django.db.models import Q
from rest_framework import generics, status, permissions
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import Shop
from .serializers import ShopSerializer


class IsAdminOrShopOwner(permissions.BasePermission):
    """
    Custom permission:
    - Administrators have full access to all shop records.
    - Shop owners can only view or modify their own associated shop.
    """
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        # Admin / Superuser has full access
        if request.user.is_admin_role or request.user.is_superuser:
            return True
        # Shop owner can only access their own shop
        return obj.owner_account_id == request.user.id


class ShopListCreateAPIView(generics.ListCreateAPIView):
    """
    GET: List shops (Admins see all; Shop owners see only their own shop).
    POST: Register a new shop (Admins only).
    Endpoint: /api/v1/shops/
    """
    serializer_class = ShopSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdminOrShopOwner]

    def get_queryset(self):
        user = self.request.user
        if user.is_admin_role or user.is_superuser:
            queryset = Shop.objects.all()
        else:
            queryset = Shop.objects.filter(owner_account=user)

        # Optional search query parameter
        q = self.request.query_params.get("q", "").strip()
        if q:
            queryset = queryset.filter(
                Q(name__icontains=q) |
                Q(owner_name__icontains=q) |
                Q(phone__icontains=q)
            )

        # Optional status filter parameter
        status_param = self.request.query_params.get("status", "").strip().lower()
        if status_param == "active":
            queryset = queryset.filter(is_active=True)
        elif status_param == "inactive":
            queryset = queryset.filter(is_active=False)

        return queryset

    def perform_create(self, serializer):
        user = self.request.user
        if not (user.is_admin_role or user.is_superuser):
            raise permissions.exceptions.PermissionDenied(
                "Only administrators are authorized to register new shops."
            )
        serializer.save()


class ShopDetailAPIView(generics.RetrieveUpdateAPIView):
    """
    GET: Retrieve shop details.
    PUT/PATCH: Update shop details.
    Endpoint: /api/v1/shops/<int:pk>/
    """
    queryset = Shop.objects.all()
    serializer_class = ShopSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdminOrShopOwner]


class ShopStatusToggleAPIView(APIView):
    """
    POST: Toggle active/inactive status of a shop.
    Endpoint: /api/v1/shops/<int:pk>/status/
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        user = request.user
        if not (user.is_admin_role or user.is_superuser):
            return Response(
                {"detail": "Only administrators can change shop authorization status."},
                status=status.HTTP_403_FORBIDDEN
            )

        try:
            shop = Shop.objects.get(pk=pk)
        except Shop.DoesNotExist:
            return Response({"detail": "Shop not found."}, status=status.HTTP_404_NOT_FOUND)

        shop.is_active = not shop.is_active
        shop.save(update_fields=["is_active", "updated_at"])

        return Response(
            {
                "id": shop.id,
                "name": shop.name,
                "is_active": shop.is_active,
                "message": f"Shop has been {'activated' if shop.is_active else 'deactivated'}."
            },
            status=status.HTTP_200_OK
        )

