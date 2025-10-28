from catalogo_app.models import Catalog # Asume que tu modelo se llama Catalog
from django.db.models import Q
import json

def normalizar_termino(termino):
    """Convierte el término a singular y minúsculas para una mejor coincidencia en DB."""
    termino = termino.lower().strip()
    if termino.endswith('s'):
        return termino[:-1] # Quita la 's' final para ir al singular
    return termino

def consultar_catalogo(nombre_producto: str):
    """
    Busca productos en PostgreSQL usando la lógica AND/OR y normalización
    para ser tolerante a errores y plurales.
    """
    
    termino_limpio = nombre_producto.strip()
    palabras_clave = termino_limpio.split()
    
    # --- 1. LÓGICA DE BÚSQUEDA AND (Más precisa) ---
    
    productos_encontrados = Catalog.objects.none()

    if palabras_clave:
        consulta_and = Q()
        for palabra in palabras_clave:
            palabra_normalizada = normalizar_termino(palabra) # 👈 NORMALIZACIÓN
            
            # El filtro busca la palabra en CUALQUIERA de los campos (OR)
            filtro_palabra = (
                Q(name__icontains=palabra_normalizada) | 
                Q(category__icontains=palabra_normalizada) | 
                Q(color__icontains=palabra_normalizada) |
                Q(tallas__name__icontains=palabra_normalizada)
            )
            # Combinamos cada palabra clave con AND (&)
            consulta_and &= filtro_palabra
        
        productos_encontrados = Catalog.objects.filter(consulta_and).distinct()

    # --- 2. FALLBACK A LÓGICA OR (Más amplia, si AND falla) ---
    if not productos_encontrados.exists() and len(palabras_clave) > 1:
        
        print("FALLBACK: Búsqueda AND muy estricta, intentando OR.")
        
        consulta_or = Q()
        for palabra in palabras_clave:
            palabra_normalizada = normalizar_termino(palabra) # 👈 NORMALIZACIÓN
            
            # Combinamos los filtros con OR (|)
            consulta_or |= (
                Q(name__icontains=palabra_normalizada) | 
                Q(category__icontains=palabra_normalizada) | 
                Q(color__icontains=palabra_normalizada) |
                Q(tallas__name__icontains=palabra_normalizada)
            )
        
        productos_encontrados = Catalog.objects.filter(consulta_or).distinct()

    # --- 3. FORMATO DE RESULTADOS ---
    resultados_para_ia = []
    
    for producto in productos_encontrados:
        tallas_list = list(producto.tallas.values_list('name', flat=True))
        
        resultados_para_ia.append({
            "name": producto.name,
            "category": producto.category,
            "price": float(producto.price),
            "stock": producto.stock,
            "color": producto.color,
            "available_sizes": ", ".join(tallas_list)
        })

    # 4. Devolver los resultados estructurados
    if resultados_para_ia:
        return {
            "encontrado": True,
            "data": resultados_para_ia
        }
    else:
        return {
            "encontrado": False, 
            "mensaje": f"No se encontró un producto relacionado con '{nombre_producto}'."
        }