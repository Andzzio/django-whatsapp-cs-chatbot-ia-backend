import json
from channels.generic.websocket import AsyncWebsocketConsumer


class DashboardConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.room_group_name = "dashboard_feed"

        # Join room group
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)

        await self.accept()

    async def disconnect(self, close_code):
        # Leave room group
        await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

    # Receive message from room group
    async def dashboard_update(self, event):
        message = event["message"]
        message_type = event.get("type_msg", "update")

        # Send message to WebSocket
        await self.send(text_data=json.dumps({"type": message_type, "data": message}))
