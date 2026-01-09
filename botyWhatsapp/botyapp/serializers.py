from rest_framework import serializers
from .models import OwnerNotification


class OwnerNotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = OwnerNotification
        fields = "__all__"
