from django.db import models

# Create your models here.
class Contact(models.Model):
    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=20, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_bot_active = models.BooleanField(default=True)
    bot_disabled_at = models.DateTimeField(null=True, blank=True)
    
class Message(models.Model):
    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name="messages")
    text = models.TextField()
    is_bot = models.BooleanField(default=False)
    timestamp = models.DateTimeField(auto_now_add=True)
    message_id = models.CharField(max_length=255, unique=True, null=True, blank=True)
    
    MESSAGE_TYPES = [
        ('text', 'Text'),
        ('image', 'Image'),
        ('audio', 'Audio'),
        ('video', 'Video'),
    ]
    message_type = models.CharField(max_length=10, choices=MESSAGE_TYPES, default='text')
    media_id = models.CharField(max_length=255, null=True, blank=True)
    caption = models.TextField(null=True, blank=True)
    is_read = models.BooleanField(default=False)