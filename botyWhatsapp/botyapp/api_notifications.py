from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import OwnerNotification
from .serializers import OwnerNotificationSerializer


class NotificationViewSet(viewsets.ModelViewSet):
    """
    API para gestionar notificaciones del dueño.
    Permite listar, marcar como leída, borrar una o borrar todas.
    """

    queryset = OwnerNotification.objects.all().order_by("-created_at")
    serializer_class = OwnerNotificationSerializer

    @action(detail=False, methods=["delete"])
    def delete_all(self, request):
        """
        Borra TODAS las notificaciones.
        Uso: DELETE /api/notifications/delete_all/
        """
        count, _ = OwnerNotification.objects.all().delete()
        return Response(
            {"status": "success", "deleted_count": count}, status=status.HTTP_200_OK
        )

    @action(detail=False, methods=["patch"])
    def mark_all_read(self, request):
        """
        Marca todas como leídas.
        """
        updated = OwnerNotification.objects.filter(is_read=False).update(is_read=True)
        return Response(
            {"status": "success", "updated_count": updated}, status=status.HTTP_200_OK
        )
