from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django.core.cache import cache
import openpyxl
from openpyxl.styles import Font, Alignment, PatternFill
from .models import ProductEmbedding
from logger import log


@csrf_exempt
def export_products_excel(request):
    """
    GET /api/products/export/excel/
    Exporta todos los productos a un archivo Excel
    """
    token = request.headers.get("Authorization")
    if token != settings.DASH_TOKEN:
        return HttpResponse("Unauthorized", status=403)

    if request.method != "GET":
        return HttpResponse("Method not allowed", status=405)

    try:
        # Crear workbook
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Inventario"

        # Headers
        headers = [
            "ID Producto",
            "Nombre",
            "Precio (S/)",
            "Stock",
            "Disponible",
            "Categoría",
        ]
        ws.append(headers)

        # Estilos header
        header_fill = PatternFill(
            start_color="4472C4", end_color="4472C4", fill_type="solid"
        )
        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")

        # Datos
        products = ProductEmbedding.objects.all().order_by("product_name")
        for product in products:
            ws.append(
                [
                    product.retailer_id,
                    product.product_name,
                    float(product.price) if product.price else 0.0,
                    product.stock_quantity,
                    "Sí" if product.is_available else "No",
                    product.category or "",
                ]
            )

        # Ajustar anchos de columna
        ws.column_dimensions["A"].width = 25
        ws.column_dimensions["B"].width = 45
        ws.column_dimensions["C"].width = 15
        ws.column_dimensions["D"].width = 12
        ws.column_dimensions["E"].width = 15
        ws.column_dimensions["F"].width = 25

        # Freeze header row
        ws.freeze_panes = "A2"

        # Crear response
        response = HttpResponse(
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        response["Content-Disposition"] = 'attachment; filename="inventario.xlsx"'
        wb.save(response)

        log.info(f"Excel exported: {products.count()} products")
        return response

    except Exception as e:
        log.error(f"Error exporting Excel: {e}")
        return HttpResponse(f"Error: {str(e)}", status=500)


@csrf_exempt
def import_products_excel(request):
    """
    POST /api/products/import/excel/
    Importa stock desde un archivo Excel
    Body: multipart/form-data con archivo 'file'
    """
    token = request.headers.get("Authorization")
    if token != settings.DASH_TOKEN:
        return JsonResponse({"error": "Unauthorized"}, status=403)

    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    try:
        excel_file = request.FILES.get("file")
        if not excel_file:
            return JsonResponse({"error": "No file provided"}, status=400)

        # Leer Excel
        wb = openpyxl.load_workbook(excel_file, data_only=True)
        ws = wb.active

        updated_count = 0
        errors = []
        skipped_count = 0

        # Saltar header (row 1)
        for row_idx, row in enumerate(
            ws.iter_rows(min_row=2, values_only=True), start=2
        ):
            # row = (retailer_id, name, price, stock, available, category)
            if not row or len(row) < 4:
                skipped_count += 1
                continue

            retailer_id = row[0]
            stock = row[3]
            available = row[4] if len(row) > 4 else None

            if not retailer_id:
                skipped_count += 1
                continue

            try:
                product = ProductEmbedding.objects.get(retailer_id=str(retailer_id))

                # Actualizar stock
                if stock is not None:
                    try:
                        product.stock_quantity = int(stock)
                    except (ValueError, TypeError):
                        errors.append(f"Fila {row_idx}: Stock inválido '{stock}'")
                        continue

                # Actualizar disponibilidad
                if available is not None:
                    available_str = str(available).lower().strip()
                    product.is_available = available_str in [
                        "sí",
                        "si",
                        "yes",
                        "true",
                        "1",
                    ]
                elif product.stock_quantity == 0:
                    # Auto-marcar como no disponible si stock es 0
                    product.is_available = False

                product.save()
                updated_count += 1

            except ProductEmbedding.DoesNotExist:
                errors.append(f"Fila {row_idx}: Producto '{retailer_id}' no encontrado")
            except Exception as e:
                errors.append(f"Fila {row_idx}: {str(e)}")

        # Invalidar cache
        if updated_count > 0 and hasattr(settings, "CATALOG_ID"):
            cache_key = f"catalog_products_{settings.CATALOG_ID}"
            cache.delete(cache_key)

        log.info(
            f"Excel imported: {updated_count} products updated, "
            f"{skipped_count} skipped, {len(errors)} errors"
        )

        return JsonResponse(
            {
                "status": "success",
                "updated": updated_count,
                "skipped": skipped_count,
                "errors": errors[:10],  # Limitar a 10 errores para no saturar
                "total_errors": len(errors),
            }
        )

    except Exception as e:
        log.error(f"Error importing Excel: {e}")
        return JsonResponse({"error": str(e)}, status=500)
