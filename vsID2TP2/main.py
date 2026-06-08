"""
main.py — Menú interactivo del sistema poliglota.
Ejecutar: python main.py
"""
"docker exec -it cassandra-tp2 cqlsh USE tp_supermercado;"
import json
from datetime import datetime
from database import conectar_todo
import operaciones


# =============================================================
# Helpers de presentación
# =============================================================
SEP  = "=" * 58
SEP2 = "-" * 58


def limpiar_para_json(obj):
    """Convierte tipos no serializables (Decimal, date, etc.) a string."""
    if isinstance(obj, dict):
        return {k: limpiar_para_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [limpiar_para_json(i) for i in obj]
    return obj


def mostrar_resultado(resultado):
    """Imprime el resultado de una operación de forma legible."""
    print(f"\n{SEP2}")
    print("  RESULTADO")
    print(SEP2)

    # Separar errores y advertencias del resto
    errores      = resultado.pop("errores",      [])
    advertencias = resultado.pop("advertencias", [])

    print(json.dumps(limpiar_para_json(resultado),
                    indent=2, ensure_ascii=False, default=str))

    if advertencias:
        print(f"\n  ADVERTENCIAS:")
        for a in advertencias:
            print(f"  ⚠  {a}")
    if errores:
        print(f"\n  ERRORES:")
        for e in errores:
            print(f"  ✗  {e}")

    # Restaurar para no mutar el dict original
    resultado["errores"]      = errores
    resultado["advertencias"] = advertencias


def pedir(prompt, default=None):
    """Pide un valor al usuario; si hay default lo muestra."""
    sufijo = f" [{default}]" if default else ""
    valor  = input(f"  {prompt}{sufijo}: ").strip()
    return valor if valor else default


def pedir_sucursal():
    return pedir("sucursal_id (ej: SUC_001)")


def pedir_fecha(label="fecha (YYYY-MM-DD)"):
    while True:
        valor = pedir(label, default=datetime.now().strftime("%Y-%m-%d"))
        try:
            datetime.strptime(valor, "%Y-%m-%d")
            return valor
        except ValueError:
            print("  Formato inválido. Usá YYYY-MM-DD.")


# =============================================================
# Handlers de cada operación
# =============================================================
def handler_op1(conexiones):
    print(f"\n{SEP}")
    print("  OP-1 — Dashboard de sucursal en tiempo real")
    print(f"{SEP}")
    suc = pedir_sucursal()
    if not suc:
        print("  Operación cancelada.")
        return
    resultado = operaciones.op1_dashboard(conexiones, suc)
    mostrar_resultado(resultado)


def handler_op2(conexiones):
    print(f"\n{SEP}")
    print("  OP-2 — Registrar venta")
    print(f"{SEP}")
    suc    = pedir_sucursal()
    cajero = pedir("cajero")
    medio  = pedir("medio de pago (Efectivo / Debito / Credito / QR / MercadoPago)")

    if not suc or not cajero or not medio:
        print("  Operación cancelada.")
        return

    print(f"\n  Ingresá los productos del ticket.")
    print(f"  Dejá producto_id vacío para terminar.\n")
    items = []
    while True:
        pid = pedir("producto_id")
        if not pid:
            break
        try:
            cant = float(pedir("cantidad"))
            items.append({"producto_id": pid, "cantidad": cant})
            print(f"  ✓  {pid} × {cant} agregado")
        except (ValueError, TypeError):
            print("  Cantidad inválida, ignorada.")

    if not items:
        print("  Sin productos. Operación cancelada.")
        return

    print(f"\n  Registrando ticket con {len(items)} producto(s)...")
    resultado = operaciones.op2_registrar_venta(conexiones, suc, cajero, medio, items)
    mostrar_resultado(resultado)


def handler_op3(conexiones):
    print(f"\n{SEP}")
    print("  OP-3 — Reporte de ventas histórico por categoría")
    print(f"{SEP}")
    fi = pedir_fecha("fecha inicio")
    ff = pedir_fecha("fecha fin   ")
    print(f"\n  Consultando {fi} → {ff}...")
    resultado = operaciones.op3_reporte_categorias(conexiones, fi, ff)
    mostrar_resultado(resultado)


def handler_op4(conexiones):
    print(f"\n{SEP}")
    print("  OP-4 — Recomendación de reposición de stock")
    print(f"{SEP}")
    suc = pedir_sucursal()
    if not suc:
        print("  Operación cancelada.")
        return
    print(f"\n  Buscando alertas de stock bajo en {suc}...")
    resultado = operaciones.op4_recomendacion_reposicion(conexiones, suc)
    mostrar_resultado(resultado)


def handler_op5(conexiones):
    print(f"\n{SEP}")
    print("  OP-5 — Cierre de caja y consolidación diaria")
    print(f"{SEP}")
    suc   = pedir_sucursal()
    fecha = pedir_fecha()
    if not suc:
        print("  Operación cancelada.")
        return
    print(f"\n  Consolidando {suc} del {fecha}...")
    resultado = operaciones.op5_cierre_caja(conexiones, suc, fecha)
    mostrar_resultado(resultado)


# =============================================================
# Menú principal
# =============================================================
OPCIONES = {
    "1": ("OP-1  Dashboard de sucursal en tiempo real  [3 motores]", handler_op1),
    "2": ("OP-2  Registrar venta y actualizar stock     [3 motores]", handler_op2),
    "3": ("OP-3  Reporte de ventas por categoría        [2 motores]", handler_op3),
    "4": ("OP-4  Recomendación de reposición de stock   [3 motores]", handler_op4),
    "5": ("OP-5  Cierre de caja y consolidación diaria  [3 motores]", handler_op5),
    "0": ("Salir", None),
}


def mostrar_menu():
    print(f"\n{SEP}")
    print("   SISTEMA POLIGLOTA — SUPERMERCADO  (TP2)")
    print(SEP)
    for key, (label, _) in OPCIONES.items():
        print(f"  {key}.  {label}")
    print(SEP)


def run():
    print(f"\n{SEP}")
    print("   Iniciando sistema poliglota...")
    print(SEP)
    conexiones = conectar_todo()

    motores_caidos = [k for k, v in conexiones.items() if v is None]
    if motores_caidos:
        print(f"\n  ⚠  Sin conexión: {', '.join(motores_caidos)}")
        print(f"     Algunas operaciones pueden fallar.")
        print(f"     Revisá el .env y que los servicios estén activos.\n")

    while True:
        mostrar_menu()
        opcion = input("  Seleccioná una opción: ").strip()

        if opcion == "0":
            print("\n  Cerrando conexiones...")
            if conexiones.get("neo4j"):
                conexiones["neo4j"].close()
            print("  Hasta luego.\n")
            break

        elif opcion in OPCIONES:
            _, handler = OPCIONES[opcion]
            try:
                handler(conexiones)
            except Exception as e:
                print(f"\n  Error inesperado: {e}")
                print(f"  Si el problema persiste, verificá las conexiones con test_conexiones.py")

        else:
            print("  Opción inválida. Ingresá un número del 0 al 5.")


if __name__ == "__main__":
    run()
