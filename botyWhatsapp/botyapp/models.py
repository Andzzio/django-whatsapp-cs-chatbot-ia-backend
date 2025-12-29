from django.db import models


# Create your models here.
class Contact(models.Model):
    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=20, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_bot_active = models.BooleanField(default=True)
    bot_disabled_at = models.DateTimeField(null=True, blank=True)

    # CRM Fields
    tags = models.JSONField(default=list, blank=True)  # ["interesado_vestidos", "vip"]
    lead_score = models.IntegerField(default=0)  # 0-100
    last_intent = models.CharField(max_length=50, null=True, blank=True)
    notes = models.TextField(blank=True)


class Message(models.Model):
    contact = models.ForeignKey(
        Contact, on_delete=models.CASCADE, related_name="messages"
    )
    text = models.TextField()
    is_bot = models.BooleanField(default=False)
    timestamp = models.DateTimeField(auto_now_add=True)
    message_id = models.CharField(max_length=255, unique=True, null=True, blank=True)

    MESSAGE_TYPES = [
        ("text", "Text"),
        ("image", "Image"),
        ("audio", "Audio"),
        ("video", "Video"),
    ]
    message_type = models.CharField(
        max_length=50, choices=MESSAGE_TYPES, default="text"
    )
    media_id = models.CharField(max_length=255, null=True, blank=True)
    caption = models.TextField(null=True, blank=True)
    is_read = models.BooleanField(default=False)
    reply_to = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="replies"
    )


class ProductEmbedding(models.Model):
    """
    Almacena embeddings de productos para búsqueda semántica.
    Permite encontrar productos por similitud sin keywords.
    """

    retailer_id = models.CharField(max_length=255, unique=True, db_index=True)
    product_name = models.CharField(max_length=500)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, null=True)
    category = models.CharField(max_length=200, blank=True)

    # Vector embedding (JSON storage para flexibilidad)
    # En producción considerar pgvector para PostgreSQL
    embedding_vector = models.JSONField(null=True, blank=True)
    embedding_model = models.CharField(max_length=100, default="gemini-embedding-001")

    # Metadata para búsqueda
    search_text = models.TextField()  # name + description normalizado
    stock_quantity = models.IntegerField(default=0)
    is_available = models.BooleanField(default=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_synced = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["retailer_id"]),
            models.Index(fields=["is_available", "stock_quantity"]),
        ]

    def __str__(self):
        return f"{self.product_name} ({self.retailer_id})"


class ConversationState(models.Model):
    """
    Gestión de estado conversacional para ventas.
    Rastrea en qué etapa del embudo está el cliente.
    """

    STAGES = [
        ("discovery", "Descubrimiento"),
        ("engagement", "Compromiso"),
        ("consideration", "Consideración"),
        ("conversion", "Conversión"),
        ("closed", "Cerrado"),
    ]

    contact = models.OneToOneField(
        Contact, on_delete=models.CASCADE, related_name="conversation_state"
    )
    current_stage = models.CharField(max_length=20, choices=STAGES, default="discovery")

    # Engagement metrics
    engagement_score = models.FloatField(default=0.0)  # 0-100
    objection_count = models.IntegerField(default=0)  # Veces que ha objetado
    discount_given = models.DecimalField(
        max_digits=5, decimal_places=2, default=0.0
    )  # Descuento acumulado

    # Cart & preferences
    cart_items = models.JSONField(default=list, blank=True)  # [{product_id, qty}]
    viewed_products = models.JSONField(default=list, blank=True)  # [retailer_id, ...]
    preferred_category = models.CharField(max_length=200, blank=True)

    # Conversation context
    last_objection_type = models.CharField(
        max_length=50, blank=True
    )  # price, size, etc
    needs_human_handover = models.BooleanField(default=False)

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_interaction = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.contact.name} - {self.current_stage}"
