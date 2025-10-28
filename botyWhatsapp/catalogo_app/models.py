from django.db import models

# Create your models here.

class Talla(models.Model):
    name = models.CharField(max_length=10, unique=True)
    
    def __str__(self):
        return self.name
    
    class Meta:
        verbose_name = "Talla de Producto"
        verbose_name_plural = "Tallas de Productos"
        ordering=["name"]

class Catalog(models.Model):
    category = models.CharField(max_length=255)
    name = models.CharField(max_length=255)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    stock = models.IntegerField(default=0)
    tallas = models.ManyToManyField(Talla, related_name='prendas')
    color = models.CharField(max_length=255 , default="Rojo")
    #Tabla de medidas
    
    def __str__(self):
        return self.name