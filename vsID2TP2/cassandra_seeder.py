"""
cassandra_seeder.py
Lee los archivos JSONL generados por generar_datos.py (TP1)
y puebla las 5 tablas de Cassandra con datos coherentes.

Prerequisito: ejecutar primero cassandra_schema.cql en Cassandra.
Ejecutar: python cassandra_seeder.py
"""
import json
import os
from datetime import datetime
from decimal import Decimal
from database import get_cassandra_session, LineaVenta

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

def leer_jsonl(filename):
    path = os.path.join(DATA_DIR, filename)
    datos = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                datos.append(json.loads(line))
    return datos


def poblar_cassandra():
    print("\n[INFO] Iniciando seeder de Cassandra...")

    session = get_cassandra_session()
    if session is None:
        print("[ERROR] Sin conexión a Cassandra. Abortando.")
        return

    print("[INFO] Leyendo archivos de datos...")
    try:
        tickets   = leer_jsonl("mongo_tickets.jsonl")
        stock     = leer_jsonl("mongo_stock.jsonl")
        productos = leer_jsonl("mongo_productos.jsonl")
    except FileNotFoundError as e:
        print(f"[ERROR] Archivo no encontrado: {e}")
        print("        Ejecutá primero generar_datos.py para crear los archivos en data/")
        return

    # Vaciar tablas para evitar datos acumulados de ejecuciones anteriores
    print("[INFO] Truncando tablas antiguas (Limpieza)...")
    tablas = ['ventas_sucursal', 'ventas_producto', 'metricas_horarias', 'alertas_stock', 'stock_eventos']
    for t in tablas:
        try:
            session.execute(f"TRUNCATE {t}")
        except Exception as e:
            print(f"[WARN] No se pudo truncar {t}: {e}")

    print(f"         tickets  : {len(tickets)}")
    print(f"         stock    : {len(stock)}")
    print(f"         productos: {len(productos)}")

    productos_map = {p["_id"]: p for p in productos}

    # --- Prepared statements ---
    ps_ventas_suc = session.prepare("""
        INSERT INTO ventas_sucursal
        (sucursal_id, fecha, ticket_timestamp, ticket_id,
        cajero, medio_pago, total, lineas_venta)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """)

    ps_ventas_prod = session.prepare("""
        INSERT INTO ventas_producto
        (producto_id, anio_mes, ticket_timestamp, ticket_id,
        sucursal_id, cantidad, precio_unitario)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """)

    ps_metricas = session.prepare("""
        INSERT INTO metricas_horarias
        (sucursal_id, fecha, hora,
        total_ventas, cantidad_tickets, ticket_promedio)
        VALUES (?, ?, ?, ?, ?, ?)
    """)

    ps_stock_evento = session.prepare("""
        INSERT INTO stock_eventos
        (sucursal_id, producto_id, evento_timestamp,
        tipo_movimiento, cantidad, cajero_id, motivo)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """)

    ps_alerta = session.prepare("""
        INSERT INTO alertas_stock
        (sucursal_id, alerta_timestamp, producto_id,
        nombre_producto, stock_actual, stock_minimo, proveedor_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """)

    # =========================================================
    # TABLAS 1 y 2: ventas_sucursal y ventas_producto
    # =========================================================
    print(f"\n[INFO] Cargando ventas_sucursal y ventas_producto ({len(tickets)} tickets)...")

    # Acumulador para metricas_horarias: {(suc_id, fecha, hora): {total, count}}
    metricas_agg = {}
    total_lineas  = 0

    for t in tickets:
        fecha_raw = t["fecha_hora"]
        fecha_str = fecha_raw["$date"].replace("Z", "") if isinstance(fecha_raw, dict) else fecha_raw
        ts        = datetime.fromisoformat(fecha_str)
        fecha_cas = ts.date()
        anio_mes  = ts.strftime("%Y-%m")

        lineas_udt = [
            LineaVenta(
                producto_id=l['producto'],
                cantidad=Decimal(str(l['cantidad'])),
                precio_unitario=Decimal(str(l['precio_unitario'])),
                descuento_aplicado=Decimal(str(l.get('descuento_aplicado', 0.0)))
            )
            for l in t["lineas_venta"]
        ]

        session.execute(ps_ventas_suc, (
            t["sucursal"], fecha_cas, ts, t["_id"],
            t["cajero"], t["medio_pago"],
            Decimal(str(t["total"])), lineas_udt
        ))

        for linea in t["lineas_venta"]:
            session.execute(ps_ventas_prod, (
                linea["producto"], anio_mes, ts, t["_id"],
                t["sucursal"],
                Decimal(str(linea["cantidad"])),
                Decimal(str(linea["precio_unitario"]))
            ))
            
            # Registrar también el movimiento de salida en el historial de stock
            session.execute(ps_stock_evento, (
                t["sucursal"], linea["producto"],
                ts, "SALIDA_VENTA",
                Decimal(str(linea["cantidad"])),
                t["cajero"], "Venta en mostrador"
            ))
            total_lineas += 1

        # Acumular métricas horarias
        clave = (t["sucursal"], fecha_cas, ts.hour)
        if clave not in metricas_agg:
            metricas_agg[clave] = {"total": Decimal("0"), "count": 0}
        metricas_agg[clave]["total"] += Decimal(str(t["total"]))
        metricas_agg[clave]["count"] += 1

    print(f"         ventas_sucursal : {len(tickets)} filas insertadas")
    print(f"         ventas_producto : {total_lineas} filas insertadas")

    # =========================================================
    # TABLA 3: metricas_horarias
    # =========================================================
    print(f"\n[INFO] Cargando metricas_horarias ({len(metricas_agg)} combinaciones)...")

    for (suc_id, fecha, hora), datos in metricas_agg.items():
        promedio = (datos["total"] / datos["count"]).quantize(Decimal("0.01"))
        session.execute(ps_metricas, (
            suc_id, fecha, hora,
            datos["total"], datos["count"], promedio
        ))

    print(f"         metricas_horarias: {len(metricas_agg)} filas insertadas")

    # =========================================================
    # TABLAS 4 y 5: stock_eventos y alertas_stock
    # =========================================================
    print(f"\n[INFO] Cargando stock_eventos y alertas_stock ({len(stock)} registros)...")

    ts_carga     = datetime.now()
    alertas_cont = 0

    for s in stock:
        fecha_raw = s["fecha_ultima_actualizacion"]
        fecha_str = fecha_raw["$date"].replace("Z", "") if isinstance(fecha_raw, dict) else fecha_raw
        ts_stock = datetime.fromisoformat(fecha_str)

        # Evento de carga inicial para cada producto-sucursal
        session.execute(ps_stock_evento, (
            s["sucursal"], s["producto"],
            ts_stock, "ENTRADA_INICIAL",
            Decimal(str(s["cantidad_disponible"])),
            "SISTEMA", "Carga inicial del sistema"
        ))

        # Si está por debajo del mínimo → alerta
        if s["cantidad_disponible"] < s["cantidad_minima"]:
            prod = productos_map.get(s["producto"], {})
            session.execute(ps_alerta, (
                s["sucursal"], ts_carga, s["producto"],
                prod.get("nombre", "Desconocido"),
                Decimal(str(s["cantidad_disponible"])),
                int(s["cantidad_minima"]),
                prod.get("proveedor_id", "")
            ))
            alertas_cont += 1

    print(f"         stock_eventos   : {len(stock)} filas insertadas")
    print(f"         alertas_stock   : {alertas_cont} filas insertadas")

    print("\n[OK] Cassandra poblada correctamente.\n")


if __name__ == "__main__":
    poblar_cassandra()