"""
operaciones.py — Las 5 operaciones poliglotas del TP2.

Correcciones aplicadas:
  - OP-1: usa metricas_horarias (no ventas_sucursal) para stats de la hora actual.
  - Toda query sobre ventas_producto incluye SIEMPRE producto_id + anio_mes
    (Cassandra rechaza queries que omitan parte de la Partition Key compuesta).
  - Todos los valores Decimal recibidos de Cassandra se castean a float() antes
    de operar aritméticamente con ellos.

Orden de escrituras en operaciones multi-motor:
  1. Cassandra  — fuente de verdad transaccional; si falla se aborta.
  2. Neo4j      — actualización del grafo de relaciones.
  3. MongoDB    — actualización de datos maestros / alertas.
"""
import uuid
from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal


# =============================================================
# OP-1: Dashboard de sucursal en tiempo real
# Motores: MongoDB + Cassandra + Neo4j
# =============================================================
def op1_dashboard(conexiones, sucursal_id):
    mongo     = conexiones["mongo"]
    neo4j     = conexiones["neo4j"]
    cassandra = conexiones["cassandra"]
    resultado = {"sucursal_id": sucursal_id, "errores": [], "advertencias": []}

    # --- MongoDB: datos maestros + promociones activas hoy ---
    try:
        sucursal = mongo["Sucursales"].find_one({"_id": sucursal_id})
        if not sucursal:
            resultado["errores"].append(
                f"Sucursal '{sucursal_id}' no encontrada — "
                f"recordá usar el formato SUC_001, SUC_002, etc."
            )
        else:
            resultado["sucursal"] = {
                "nombre":  sucursal.get("nombre"),
                "zona":    sucursal.get("zona"),
                "ciudad":  sucursal.get("ciudad"),
                "horario": sucursal.get("horario_atencion"),
            }

        hoy = datetime.now()
        promociones = list(mongo["Promociones"].find({
            "sucursales_aplicables": sucursal_id,
            "vigencia_inicio": {"$lte": hoy},
            "vigencia_fin":    {"$gte": hoy},
        }))
        resultado["promociones_activas"] = [
            {"id": p["_id"], "tipo": p["tipo"], "condicion": p.get("condicion_activacion")}
            for p in promociones
        ]

        proveedores = list(mongo["Proveedores"].find({}, {"_id": 1, "nombre": 1, "condiciones_pago": 1}))
        resultado["proveedores"] = proveedores
    except Exception as e:
        resultado["errores"].append(f"MongoDB: {e}")

    # --- Cassandra: metricas de la hora actual y curva del dia ---
    # CORRECCION: se usa metricas_horarias (tabla disenada para este caso de uso)
    # en lugar de escanear ventas_sucursal por timestamp.
    try:
        ahora       = datetime.now()
        hoy_date    = ahora.date()
        hora_actual = ahora.hour

        row_hora = cassandra.execute("""
            SELECT hora, total_ventas, cantidad_tickets, ticket_promedio
            FROM metricas_horarias
            WHERE sucursal_id=%s AND fecha=%s AND hora=%s
        """, (sucursal_id, hoy_date, hora_actual)).one()

        if row_hora:
            stats_hora = {
                "hora":             row_hora.hora,
                "total_vendido":    round(float(row_hora.total_ventas), 2),
                "cantidad_tickets": int(row_hora.cantidad_tickets),
                "ticket_promedio":  round(float(row_hora.ticket_promedio), 2),
            }
        else:
            stats_hora = {
                "hora":             hora_actual,
                "total_vendido":    0.0,
                "cantidad_tickets": 0,
                "ticket_promedio":  0.0,
            }

        curva = list(cassandra.execute("""
            SELECT hora, total_ventas, cantidad_tickets
            FROM metricas_horarias
            WHERE sucursal_id=%s AND fecha=%s
        """, (sucursal_id, hoy_date)))

        resultado["metricas_hora_actual"] = stats_hora
        resultado["curva_horaria_hoy"] = [
            {"hora": r.hora,
            "total": round(float(r.total_ventas), 2),
            "tickets": int(r.cantidad_tickets)}
            for r in curva
        ]

        alertas = list(cassandra.execute("""
            SELECT producto_id, nombre_producto, stock_actual, stock_minimo
            FROM alertas_stock WHERE sucursal_id=%s LIMIT 10
        """, (sucursal_id,)))
        resultado["alertas_stock"] = [
            {
                "producto_id":  a.producto_id,
                "nombre":       a.nombre_producto,
                "stock_actual": float(a.stock_actual),
                "stock_minimo": int(a.stock_minimo),
            }
            for a in alertas
        ]
    except Exception as e:
        resultado["errores"].append(f"Cassandra: {e}")

    # --- Neo4j: top 5 co-compras ---
    try:
        with neo4j.session() as session:
            existe = session.run(
                "MATCH ()-[r:CO_COMPRA]-() RETURN r LIMIT 1"
            ).single()

        if existe:
            with neo4j.session() as session:
                res = session.run("""
                    MATCH (a:Producto)-[r:CO_COMPRA]-(b:Producto)
                    WHERE elementId(a) < elementId(b)
                    RETURN a.nombre AS prod1, b.nombre AS prod2,
                        r.frecuencia AS frecuencia
                    ORDER BY r.frecuencia DESC LIMIT 5
                """)
                resultado["top_cocompras"] = [
                    {"producto_1": r["prod1"], "producto_2": r["prod2"],
                    "frecuencia": r["frecuencia"]}
                    for r in res
                ]
        else:
            resultado["top_cocompras"] = []
            resultado["advertencias"].append(
                "CO_COMPRA no existe aun — "
                "ejecuta primero la query de derivacion en Neo4j (TP1)"
            )
    except Exception as e:
        resultado["errores"].append(f"Neo4j: {e}")

    return resultado


# =============================================================
# OP-2: Registro de venta y actualizacion de stock
# Motores: Cassandra -> Neo4j -> MongoDB
# =============================================================
def op2_registrar_venta(conexiones, sucursal_id, cajero, medio_pago, items):
    """
    items: lista de dicts {"producto_id": str, "cantidad": float}
    """
    mongo     = conexiones["mongo"]
    neo4j     = conexiones["neo4j"]
    cassandra = conexiones["cassandra"]
    resultado = {"errores": [], "advertencias": []}

    if not items:
        resultado["errores"].append("La lista de productos esta vacia")
        return resultado

    productos_data = {}
    for item in items:
        prod = mongo["Productos"].find_one({"_id": item["producto_id"]})
        if not prod:
            resultado["errores"].append(
                f"Producto '{item['producto_id']}' no encontrado en MongoDB"
            )
            return resultado
        productos_data[item["producto_id"]] = prod

    ticket_id = str(uuid.uuid4())
    ahora     = datetime.now()
    fecha_cas = ahora.date()
    anio_mes  = ahora.strftime("%Y-%m")
    total     = sum(
        productos_data[i["producto_id"]]["precio_venta"] * i["cantidad"]
        for i in items
    )

    # === CASSANDRA ===
    try:
        lineas_str = [
            f"{i['producto_id']}|{i['cantidad']}|"
            f"{productos_data[i['producto_id']]['precio_venta']}|0.0"
            for i in items
        ]
        cassandra.execute("""
            INSERT INTO ventas_sucursal
            (sucursal_id, fecha, ticket_timestamp, ticket_id,
            cajero, medio_pago, total, lineas_venta)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (sucursal_id, fecha_cas, ahora, ticket_id,
            cajero, medio_pago, Decimal(str(round(total, 2))), lineas_str))

        for item in items:
            prod = productos_data[item["producto_id"]]
            # CORRECCION: producto_id + anio_mes siempre juntos (PK compuesta)
            cassandra.execute("""
                INSERT INTO ventas_producto
                (producto_id, anio_mes, ticket_timestamp, ticket_id,
                sucursal_id, cantidad, precio_unitario)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (item["producto_id"], anio_mes, ahora, ticket_id,
                sucursal_id,
                Decimal(str(item["cantidad"])),
                Decimal(str(prod["precio_venta"]))))

            cassandra.execute("""
                INSERT INTO stock_eventos
                (sucursal_id, producto_id, evento_timestamp,
                tipo_movimiento, cantidad, cajero_id, motivo)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (sucursal_id, item["producto_id"], ahora,
                "SALIDA_VENTA",
                Decimal(str(item["cantidad"])),
                cajero, "Venta en caja"))

        hora = ahora.hour
        row  = cassandra.execute("""
            SELECT total_ventas, cantidad_tickets FROM metricas_horarias
            WHERE sucursal_id=%s AND fecha=%s AND hora=%s
        """, (sucursal_id, fecha_cas, hora)).one()

        # CORRECCION: casteo Decimal -> float antes de operar
        prev_total  = float(row.total_ventas)  if row else 0.0
        prev_count  = int(row.cantidad_tickets) if row else 0
        nuevo_total = prev_total + total
        nuevo_count = prev_count + 1
        nuevo_prom  = nuevo_total / nuevo_count

        cassandra.execute("""
            INSERT INTO metricas_horarias
            (sucursal_id, fecha, hora, total_ventas, cantidad_tickets, ticket_promedio)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (sucursal_id, fecha_cas, hora,
            Decimal(str(round(nuevo_total, 2))),
            nuevo_count,
            Decimal(str(round(nuevo_prom, 2)))))

        resultado["cassandra"] = f"OK — ticket {ticket_id} registrado"
    except Exception as e:
        resultado["errores"].append(f"Cassandra: {e}")
        return resultado

    # === NEO4J ===
    try:
        ids   = [i["producto_id"] for i in items]
        pares = [
            (ids[i], ids[j])
            for i in range(len(ids))
            for j in range(i + 1, len(ids))
        ]
        with neo4j.session() as session:
            for id1, id2 in pares:
                a, b = (id1, id2) if id1 < id2 else (id2, id1)
                session.run("""
                    MATCH (a:Producto {id: $a}), (b:Producto {id: $b})
                    MERGE (a)-[r:CO_COMPRA]->(b)
                    ON CREATE SET r.frecuencia = 1
                    ON MATCH  SET r.frecuencia = r.frecuencia + 1
                """, a=a, b=b)
        resultado["neo4j"] = f"OK — {len(pares)} pares CO_COMPRA actualizados"
    except Exception as e:
        resultado["advertencias"].append(
            f"Neo4j: {e} — ticket ya persistido en Cassandra"
        )

    # === MONGODB ===
    try:
        alertas_generadas = 0
        for item in items:
            stock_doc = mongo["Stock"].find_one({
                "producto": item["producto_id"],
                "sucursal": sucursal_id,
            })
            if stock_doc:
                nueva_cant = stock_doc["cantidad_disponible"] - item["cantidad"]
                mongo["Stock"].update_one(
                    {"_id": stock_doc["_id"]},
                    {"$set": {
                        "cantidad_disponible":        max(0, nueva_cant),
                        "fecha_ultima_actualizacion": ahora,
                    }}
                )
                if nueva_cant < stock_doc["cantidad_minima"]:
                    prod = productos_data[item["producto_id"]]
                    cassandra.execute("""
                        INSERT INTO alertas_stock
                        (sucursal_id, alerta_timestamp, producto_id,
                        nombre_producto, stock_actual, stock_minimo, proveedor_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """, (sucursal_id, ahora, item["producto_id"],
                        prod["nombre"],
                        Decimal(str(max(0.0, nueva_cant))),
                        int(stock_doc["cantidad_minima"]),
                        prod["proveedor_id"]))
                    alertas_generadas += 1

        resultado["mongo"] = f"OK — {alertas_generadas} alertas de stock generadas"
    except Exception as e:
        resultado["advertencias"].append(
            f"MongoDB: {e} — stock no actualizado, ticket ya registrado"
        )

    resultado["ticket_id"] = ticket_id
    resultado["total"]     = round(total, 2)
    return resultado


# =============================================================
# OP-3: Reporte de ventas historico por categoria
# Motores: Cassandra + MongoDB
# =============================================================
def op3_reporte_categorias(conexiones, fecha_inicio_str, fecha_fin_str):
    mongo     = conexiones["mongo"]
    cassandra = conexiones["cassandra"]
    resultado = {"errores": [], "reporte": []}

    try:
        fi = datetime.strptime(fecha_inicio_str, "%Y-%m-%d")
        ff = datetime.strptime(fecha_fin_str,    "%Y-%m-%d")
    except ValueError:
        resultado["errores"].append("Formato de fecha invalido. Usar YYYY-MM-DD")
        return resultado

    if fi > ff:
        resultado["errores"].append("fecha_inicio debe ser anterior a fecha_fin")
        return resultado

    meses = []
    cur = fi.replace(day=1)
    while cur <= ff:
        meses.append(cur.strftime("%Y-%m"))
        cur = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)

    try:
        productos = list(mongo["Productos"].find(
            {}, {"_id": 1, "categoria": 1, "subcategoria": 1}
        ))
        prod_map = {p["_id"]: p for p in productos}
    except Exception as e:
        resultado["errores"].append(f"MongoDB: {e}")
        return resultado

    # CORRECCION: siempre se pasa producto_id + anio_mes (PK compuesta obligatoria)
    ventas_por_prod = {}
    try:
        query_str = """
            SELECT cantidad, precio_unitario, ticket_timestamp
            FROM ventas_producto
            WHERE producto_id=%s AND anio_mes=%s
        """
        futuros = []
        for prod_id in prod_map:
            for mes in meses:
                futuro = cassandra.execute_async(query_str, (prod_id, mes))
                futuros.append((prod_id, futuro))
                
        for prod_id, futuro in futuros:
            rows = futuro.result()
            for row in rows:
                ts = row.ticket_timestamp
                if fi <= ts <= ff + timedelta(days=1):
                    if prod_id not in ventas_por_prod:
                        ventas_por_prod[prod_id] = {"unidades": 0.0, "pesos": 0.0}
                    # CORRECCION: Decimal -> float
                    cant   = float(row.cantidad)
                    precio = float(row.precio_unitario)
                    ventas_por_prod[prod_id]["unidades"] += cant
                    ventas_por_prod[prod_id]["pesos"]    += cant * precio
    except Exception as e:
        resultado["errores"].append(f"Cassandra: {e}")

    por_categoria = {}
    for prod_id, ventas in ventas_por_prod.items():
        prod = prod_map.get(prod_id, {})
        key  = (prod.get("categoria", "Sin categoria"),
                prod.get("subcategoria", "Sin subcategoria"))
        if key not in por_categoria:
            por_categoria[key] = {"unidades": 0.0, "pesos": 0.0}
        por_categoria[key]["unidades"] += ventas["unidades"]
        por_categoria[key]["pesos"]    += ventas["pesos"]

    resultado["reporte"] = [
        {
            "categoria":         cat,
            "subcategoria":      sub,
            "unidades_vendidas": round(d["unidades"], 2),
            "total_pesos":       round(d["pesos"],    2),
        }
        for (cat, sub), d in sorted(
            por_categoria.items(), key=lambda x: -x[1]["pesos"]
        )
    ]
    resultado["periodo"]              = f"{fecha_inicio_str} -> {fecha_fin_str}"
    resultado["productos_con_ventas"] = len(ventas_por_prod)
    return resultado


# =============================================================
# OP-4: Recomendacion de reposicion de stock
# Motores: Cassandra + Neo4j + MongoDB
# =============================================================
def op4_recomendacion_reposicion(conexiones, sucursal_id):
    mongo     = conexiones["mongo"]
    neo4j     = conexiones["neo4j"]
    cassandra = conexiones["cassandra"]
    resultado = {"sucursal_id": sucursal_id, "recomendaciones": [], "errores": []}

    try:
        alertas = list(cassandra.execute("""
            SELECT producto_id, nombre_producto, stock_actual, stock_minimo, proveedor_id
            FROM alertas_stock WHERE sucursal_id=%s LIMIT 20
        """, (sucursal_id,)))

        if not alertas:
            resultado["mensaje"] = "No hay alertas de stock bajo activas para esta sucursal"
            return resultado
    except Exception as e:
        resultado["errores"].append(f"Cassandra: {e}")
        return resultado

    for alerta in alertas:
        rec = {
            "producto_id":  alerta.producto_id,
            "nombre":       alerta.nombre_producto,
            "stock_actual": float(alerta.stock_actual),  # CORRECCION: Decimal -> float
            "stock_minimo": int(alerta.stock_minimo),
            "sustitutos":   [],
            "proveedor":    None,
        }

        try:
            with neo4j.session() as session:
                res = session.run("""
                    MATCH (p:Producto {id: $prod_id})-[:SUSTITUTO_DE]-(s:Producto)
                    WHERE s.tiene_stock = 'TRUE'
                    OPTIONAL MATCH (p)-[r:CO_COMPRA]-(s)
                    RETURN s.id     AS id,
                        s.nombre AS nombre,
                        COALESCE(r.frecuencia, 0) AS frecuencia
                    ORDER BY frecuencia DESC LIMIT 3
                """, prod_id=alerta.producto_id)
                rec["sustitutos"] = [
                    {"id": r["id"], "nombre": r["nombre"],
                    "frecuencia_cocompra": r["frecuencia"]}
                    for r in res
                ]
        except Exception as e:
            resultado["errores"].append(f"Neo4j ({alerta.producto_id}): {e}")

        try:
            prod_doc = mongo["Productos"].find_one({"_id": alerta.producto_id})
            if prod_doc:
                prov = mongo["Proveedores"].find_one({"_id": prod_doc["proveedor_id"]})
                if prov:
                    rec["proveedor"] = {
                        "nombre":           prov.get("nombre"),
                        "condiciones_pago": prov.get("condiciones_pago"),
                        "plazo_entrega":    prov.get("plazo_entrega_dias"),
                    }
        except Exception as e:
            resultado["errores"].append(f"MongoDB ({alerta.producto_id}): {e}")

        resultado["recomendaciones"].append(rec)

    return resultado


# =============================================================
# OP-5: Cierre de caja y consolidacion diaria
# Motores: Cassandra -> MongoDB -> Neo4j
# =============================================================
def op5_cierre_caja(conexiones, sucursal_id, fecha_str):
    mongo     = conexiones["mongo"]
    neo4j     = conexiones["neo4j"]
    cassandra = conexiones["cassandra"]
    resultado = {
        "sucursal_id":  sucursal_id,
        "fecha":        fecha_str,
        "errores":      [],
        "advertencias": [],
    }

    try:
        fecha_dt  = datetime.strptime(fecha_str, "%Y-%m-%d")
        fecha_cas = fecha_dt.date()
    except ValueError:
        resultado["errores"].append("Formato de fecha invalido. Usar YYYY-MM-DD")
        return resultado

    # === CASSANDRA ===
    try:
        rows = list(cassandra.execute("""
            SELECT ticket_id, ticket_timestamp, total, lineas_venta, medio_pago
            FROM ventas_sucursal
            WHERE sucursal_id=%s AND fecha=%s
        """, (sucursal_id, fecha_cas)))

        if not rows:
            resultado["mensaje"] = (
                f"No hay tickets para {sucursal_id} en la fecha {fecha_str}"
            )
            return resultado

        # CORRECCION: Decimal -> float
        total_dia       = sum(float(r.total) for r in rows)
        ticket_promedio = total_dia / len(rows)

        horas     = Counter(r.ticket_timestamp.hour for r in rows)
        hora_pico = horas.most_common(1)[0]

        producto_counter = Counter()
        pares_cocompra   = Counter()

        for row in rows:
            ids_en_ticket = []
            for linea in (row.lineas_venta or []):
                partes = linea.split("|")
                if partes:
                    prod_id  = partes[0]
                    cantidad = float(partes[1]) if len(partes) > 1 else 1.0
                    producto_counter[prod_id] += cantidad
                    ids_en_ticket.append(prod_id)

            for i in range(len(ids_en_ticket)):
                for j in range(i + 1, len(ids_en_ticket)):
                    a, b = sorted([ids_en_ticket[i], ids_en_ticket[j]])
                    pares_cocompra[(a, b)] += 1

        consolidado = {
            "total_tickets":     len(rows),
            "total_vendido":     round(total_dia, 2),
            "ticket_promedio":   round(ticket_promedio, 2),
            "hora_pico":         hora_pico[0],
            "tickets_hora_pico": hora_pico[1],
            "top_10_productos":  [
                {"producto_id": pid, "unidades": round(float(cant), 2)}
                for pid, cant in producto_counter.most_common(10)
            ],
        }
        resultado["consolidado"] = consolidado
    except Exception as e:
        resultado["errores"].append(f"Cassandra: {e}")
        return resultado

    # === MONGODB ===
    try:
        reporte_doc = {
            "_id":             f"REPORTE_{sucursal_id}_{fecha_str}",
            "sucursal_id":     sucursal_id,
            "fecha":           fecha_str,
            "total_tickets":   consolidado["total_tickets"],
            "total_vendido":   consolidado["total_vendido"],
            "ticket_promedio": consolidado["ticket_promedio"],
            "hora_pico":       consolidado["hora_pico"],
            "top_productos":   consolidado["top_10_productos"],
            "generado_en":     datetime.now(),
        }
        mongo["ReportesDiarios"].replace_one(
            {"_id": reporte_doc["_id"]},
            reporte_doc,
            upsert=True
        )
        resultado["mongo"] = "OK — reporte diario persistido en ReportesDiarios"
    except Exception as e:
        resultado["advertencias"].append(f"MongoDB: {e} — reporte no guardado")

    # === NEO4J ===
    try:
        actualizados = 0
        with neo4j.session() as session:
            for (id1, id2), freq in pares_cocompra.items():
                session.run("""
                    MATCH (a:Producto {id: $id1}), (b:Producto {id: $id2})
                    MERGE (a)-[r:CO_COMPRA]->(b)
                    ON CREATE SET r.frecuencia = $freq
                    ON MATCH  SET r.frecuencia = r.frecuencia + $freq
                """, id1=id1, id2=id2, freq=freq)
                actualizados += 1
        resultado["neo4j"] = f"OK — {actualizados} relaciones CO_COMPRA actualizadas"
    except Exception as e:
        resultado["advertencias"].append(
            f"Neo4j: {e} — CO_COMPRA no actualizado, reporte ya guardado en MongoDB"
        )

    return resultado
