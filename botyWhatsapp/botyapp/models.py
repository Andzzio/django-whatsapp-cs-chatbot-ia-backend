from django.db import models


# Create your models here.
class Contact(models.Model):
    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=20, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_bot_active = models.BooleanField(default=True)
    needs_human_attention = models.BooleanField(default=False)  # Solicitud de vendedor
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
    image_url = models.URLField(
        max_length=500, blank=True, null=True
    )  # URL de imagen del producto

    # Vector embedding (JSON storage para flexibilidad)
    # En producción considerar pgvector para PostgreSQL
    embedding_vector = models.JSONField(null=True, blank=True)
    embedding_model = models.CharField(max_length=100, default="gemini-embedding-001")

    # Image embedding para identificación visual (NUEVO)
    image_embedding_vector = models.JSONField(
        null=True,
        blank=True,
        help_text="Embedding multimodal de la imagen del producto para identificación visual",
    )

    # Metadata para búsqueda
    search_text = models.TextField()  # name + description normalizado

    # Stock dividido por tallas
    stock_s = models.IntegerField(default=0, verbose_name="Stock S")
    stock_m = models.IntegerField(default=0, verbose_name="Stock M")
    stock_l = models.IntegerField(default=0, verbose_name="Stock L")
    stock_xl = models.IntegerField(default=0, verbose_name="Stock XL")

    is_available = models.BooleanField(default=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_synced = models.DateTimeField(null=True, blank=True)

    @property
    def total_stock(self):
        """Stock total sumando todas las tallas"""
        return self.stock_s + self.stock_m + self.stock_l + self.stock_xl

    def has_stock(self, size=None):
        """Verifica si hay stock (opcionalmente de una talla específica)"""
        if size:
            size_lower = size.lower()
            return getattr(self, f"stock_{size_lower}", 0) > 0
        return self.total_stock > 0

    class Meta:
        indexes = [
            models.Index(fields=["retailer_id"]),
            models.Index(fields=["is_available"]),
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


class Order(models.Model):
    """
    Sistema de pedidos para checkout.
    Maneja el flujo: Producto → Dirección → Pago
    """

    STATUS_CHOICES = [
        ("PENDING", "Pendiente"),
        ("PENDING_SIZE", "Pendiente - Sin Talla"),
        ("CONFIRMED", "Confirmado"),
        ("SHIPPED", "Enviado"),
        ("DELIVERED", "Entregado"),
        ("CANCELLED", "Cancelado"),
    ]

    CHECKOUT_STAGES = [
        ("CONFIRMING_PRODUCT", "Confirmando Producto"),
        ("COLLECTING_ADDRESS", "Capturando Dirección"),
        ("PROCESSING_PAYMENT", "Procesando Pago"),
        ("COMPLETED", "Completado"),
    ]

    contact = models.ForeignKey(
        Contact, on_delete=models.CASCADE, related_name="orders"
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="PENDING")
    checkout_stage = models.CharField(
        max_length=30, choices=CHECKOUT_STAGES, default="CONFIRMING_PRODUCT"
    )

    # Shipping info
    shipping_district = models.CharField(max_length=100, blank=True)
    shipping_address = models.TextField(blank=True)
    shipping_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # Payment info
    payment_method = models.CharField(max_length=50, blank=True)
    payment_proof = models.TextField(blank=True)  # URL o referencia

    # Amounts
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # Stock control
    stock_deducted = models.BooleanField(default=False)
    stock_deducted_at = models.DateTimeField(null=True, blank=True)

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["contact", "status"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"Orden #{self.id} - {self.contact.name} - {self.status}"

    @property
    def total_items(self):
        return sum(item.quantity for item in self.items.all())

    def calculate_totals(self):
        """Calcula subtotal y total"""
        self.subtotal = sum(item.quantity * item.price for item in self.items.all())
        self.total_amount = self.subtotal + self.shipping_cost - self.discount
        self.save(update_fields=["subtotal", "total_amount"])


class OrderItem(models.Model):
    """Items individuales de un pedido"""

    order = models.ForeignKey(Order, related_name="items", on_delete=models.CASCADE)
    product = models.ForeignKey(
        ProductEmbedding, null=True, blank=True, on_delete=models.SET_NULL
    )
    product_name = models.CharField(max_length=200)
    quantity = models.IntegerField(default=1)
    price = models.DecimalField(max_digits=10, decimal_places=2)

    SIZE_CHOICES = [
        ("S", "S"),
        ("M", "M"),
        ("L", "L"),
        ("XL", "XL"),
    ]
    size = models.CharField(
        max_length=2,
        choices=SIZE_CHOICES,
        null=True,
        blank=True,
        verbose_name="Talla",
        help_text="Talla del producto (S, M, L, XL)",
    )

    @property
    def subtotal(self):
        return self.quantity * self.price

    def __str__(self):
        size_str = f" ({self.size})" if self.size else " (Sin talla)"
        return f"{self.product_name}{size_str} x{self.quantity}"

    def save(self, *args, **kwargs):
        # Auto-populate product info snapshot
        if (
            not self.product_name and self.product
        ):  # Added check for self.product to prevent error if product is null
            self.product_name = self.product.product_name
        super().save(*args, **kwargs)


class Snippet(models.Model):
    """
    Snippets de texto para respuestas rápidas.
    Cada token (usuario dashboard) tiene sus propios snippets?
    Por ahora lo haremos global o por token si el usuario lo pide.
    Asumiremos global por simplicidad o vinculado a nada específico por ahora.
    """

    token = models.CharField(max_length=255, db_index=True, default="default")
    shortcut = models.CharField(max_length=50)  # Ej: /saludo
    content = models.TextField()  # Ej: Hola, ¿cómo estás?
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("token", "shortcut")

    def __str__(self):
        return f"{self.shortcut} -> {self.content[:20]}..."
