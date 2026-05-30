"""
consultas_cassandra.py
Consultas independientes de Cassandra — TP2 Sección 3.

Estructura según los requerimientos del TP:
  Sección 3.1 — Registro de ventas a alta velocidad      (queries 3.1.2 a–d)
  Sección 3.2 — Métricas de ventas en tiempo real        (queries 3.2.4 a–d)
  Sección 3.3 — Gestión de stock y alertas               (queries 3.3.6 a–c)

Nota sobre ALLOW FILTERING:
  En Cassandra, filtrar por columnas que no forman parte de la clave
  requiere ALLOW FILTERING, lo que implica un full-scan de la partición.
  Donde sea posible, se filtra en Python después de leer la partición
  completa (que ya está acotada por la partition key).
  Esto es la práctica correcta en Cassandra: diseñar las queries
  alrededor del modelo, no el modelo alrededor de las queries ad-hoc.

Ejecutar: python consultas_cassandra.py
"""
from datetime import datetime, timedelta
from database import get_cassandra_session

session = get_cassandra_session()


# =============================================================
# SECCIÓN 3.1 — Registro de ventas a alta velocidad
# =============================================================

def q_tickets_sucursal_fecha(sucursal_id, fecha_str):
    """
    3.1.2 a) Todos los tickets de una sucursal en una fecha dada,
            ordenados cronológicamente.

    La tabla ventas_sucursal tiene clustering ORDER BY ticket_timestamp DESC.
    Traemos la partición completa y ordenamos ASC en Python para obtener
    el orden cronológico sin modificar el modelo.
    Partition key exacta → no requiere ALLOW FILTERING.
    """
    fecha = datetime.strptime(fecha_str, "%Y-%m-%d").date()

    rows = session.execute("""
        SELECT ticket_id, ticket_timestamp, cajero, medio_pago, total
        FROM ventas_sucursal
        WHERE sucursal_id = %s AND fecha = %s
    """, (sucursal_id, fecha))

    tickets = sorted(list(rows), key=lambda r: r.ticket_timestamp)

    print(f"\n[3.1.2a] Tickets de {sucursal_id} el {fecha_str} "
        f"(orden cronológico) — {len(tickets)} tickets")
    for t in tickets[:10]:   # primeros 10 para no saturar la salida
        print(f"  {t.ticket_timestamp.strftime('%H:%M')}  "
            f"${float(t.total):>10.2f}  {t.medio_pago:<15}  {t.cajero}")
    if len(tickets) > 10:
        print(f"  ... y {len(tickets) - 10} más")

    return tickets


def q_total_vendido_2_horas(sucursal_id):
    """
    3.1.2 b) Total vendido por una sucursal en las últimas 2 horas.

    Usamos el clustering key ticket_timestamp para hacer un range scan
    dentro de la partición (sucursal_id, fecha).
    Si las 2 horas cruzan la medianoche, consultamos ambas particiones.
    """
    ahora        = datetime.now()
    hace_2_horas = ahora - timedelta(hours=2)

    fechas = {ahora.date()}
    if hace_2_horas.date() != ahora.date():
        fechas.add(hace_2_horas.date())

    tickets = []
    for fecha in fechas:
        rows = session.execute("""
            SELECT ticket_timestamp, total
            FROM ventas_sucursal
            WHERE sucursal_id = %s AND fecha = %s
            AND ticket_timestamp >= %s
        """, (sucursal_id, fecha, hace_2_horas))
        tickets.extend(list(rows))

    total_vendido = sum(float(r.total) for r in tickets)
    promedio      = total_vendido / len(tickets) if tickets else 0.0

    resultado = {
        "sucursal_id":      sucursal_id,
        "desde":            hace_2_horas.strftime("%H:%M"),
        "hasta":            ahora.strftime("%H:%M"),
        "cantidad_tickets": len(tickets),
        "total_vendido":    round(total_vendido, 2),
        "ticket_promedio":  round(promedio, 2),
    }

    print(f"\n[3.1.2b] Total vendido en las últimas 2 horas — {sucursal_id}")
    print(f"  Período        : {resultado['desde']} → {resultado['hasta']}")
    print(f"  Tickets        : {resultado['cantidad_tickets']}")
    print(f"  Total vendido  : ${resultado['total_vendido']:,.2f}")
    print(f"  Ticket promedio: ${resultado['ticket_promedio']:,.2f}")

    return resultado


def q_historial_producto_mes_actual(producto_id):
    """
    3.1.2 c) Historial de ventas de un producto específico en el mes actual.

    La partition key de ventas_producto es (producto_id, anio_mes).
    Con ambos campos se accede a la partición exacta sin ALLOW FILTERING.
    """
    anio_mes = datetime.now().strftime("%Y-%m")

    rows = session.execute("""
        SELECT ticket_timestamp, sucursal_id, cantidad, precio_unitario
        FROM ventas_producto
        WHERE producto_id = %s AND anio_mes = %s
    """, (producto_id, anio_mes))

    ventas = list(rows)
    total_unidades = sum(float(r.cantidad) for r in ventas)
    total_pesos    = sum(float(r.cantidad) * float(r.precio_unitario) for r in ventas)

    print(f"\n[3.1.2c] Historial de ventas de {producto_id} — {anio_mes}")
    print(f"  Registros encontrados : {len(ventas)}")
    print(f"  Unidades vendidas     : {round(total_unidades, 2)}")
    print(f"  Total en pesos        : ${round(total_pesos, 2):,.2f}")
    for v in ventas[:5]:
        print(f"  {v.ticket_timestamp.strftime('%Y-%m-%d %H:%M')}  "
            f"suc: {v.sucursal_id}  cant: {float(v.cantidad)}  "
            f"precio: ${float(v.precio_unitario):.2f}")
    if len(ventas) > 5:
        print(f"  ... y {len(ventas) - 5} registros más")

    return ventas


def q_tickets_mayor_monto(sucursal_id, fecha_str, monto_minimo=50000.0):
    """
    3.1.2 d) Tickets con total superior a $50.000 en una sucursal y fecha dados.

    No existe un índice secundario sobre 'total', por lo que Cassandra
    requeriría ALLOW FILTERING para filtrar en CQL.
    La práctica correcta es traer la partición completa (ya acotada
    por la partition key) y filtrar en la capa de aplicación.
    """
    fecha = datetime.strptime(fecha_str, "%Y-%m-%d").date()

    rows = session.execute("""
        SELECT ticket_id, ticket_timestamp, cajero, medio_pago, total
        FROM ventas_sucursal
        WHERE sucursal_id = %s AND fecha = %s
    """, (sucursal_id, fecha))

    tickets_altos = [r for r in rows if float(r.total) > monto_minimo]
    tickets_altos.sort(key=lambda r: float(r.total), reverse=True)

    print(f"\n[3.1.2d] Tickets con total > ${monto_minimo:,.0f} "
        f"en {sucursal_id} el {fecha_str}")
    print(f"  Encontrados: {len(tickets_altos)}")
    for t in tickets_altos:
        print(f"  ${float(t.total):>12,.2f}  {t.medio_pago:<15}  "
            f"{t.ticket_timestamp.strftime('%H:%M')}  {t.cajero}")

    return tickets_altos


# =============================================================
# SECCIÓN 3.2 — Métricas de ventas en tiempo real
# =============================================================

def q_actualizar_metricas_hora(sucursal_id, total_ticket):
    """
    3.2.4 a) Actualizar las métricas de la hora actual al registrar un ticket.

    Patrón read-modify-write: se lee el acumulado actual, se suma el
    nuevo ticket y se vuelve a escribir. Es un trade-off aceptado para
    el volumen de este TP; en producción se usaría una COUNTER TABLE
    o un mecanismo de batch externo.
    """
    ahora    = datetime.now()
    fecha    = ahora.date()
    hora     = ahora.hour

    row = session.execute("""
        SELECT total_ventas, cantidad_tickets
        FROM metricas_horarias
        WHERE sucursal_id = %s AND fecha = %s AND hora = %s
    """, (sucursal_id, fecha, hora)).one()

    from decimal import Decimal
    prev_total = float(row.total_ventas)  if row else 0.0
    prev_count = int(row.cantidad_tickets) if row else 0
    nuevo_total = prev_total + total_ticket
    nuevo_count = prev_count + 1
    nuevo_prom  = nuevo_total / nuevo_count

    session.execute("""
        INSERT INTO metricas_horarias
        (sucursal_id, fecha, hora, total_ventas, cantidad_tickets, ticket_promedio)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (sucursal_id, fecha, hora,
        Decimal(str(round(nuevo_total, 2))),
        nuevo_count,
        Decimal(str(round(nuevo_prom, 2)))))

    print(f"\n[3.2.4a] Métricas actualizadas — {sucursal_id} hora {hora}:00")
    print(f"  Total acumulado hora : ${round(nuevo_total, 2):,.2f}")
    print(f"  Tickets en la hora   : {nuevo_count}")
    print(f"  Ticket promedio      : ${round(nuevo_prom, 2):,.2f}")

    return {"hora": hora, "total": nuevo_total, "count": nuevo_count, "promedio": nuevo_prom}


def q_curva_horaria_hoy(sucursal_id):
    """
    3.2.4 b) Curva horaria de ventas de una sucursal para el día de hoy.

    Trae toda la partición (sucursal_id, fecha=hoy) de metricas_horarias.
    El clustering ORDER BY hora ASC devuelve las horas en orden natural.
    """
    hoy = datetime.now().date()

    rows = session.execute("""
        SELECT hora, total_ventas, cantidad_tickets, ticket_promedio
        FROM metricas_horarias
        WHERE sucursal_id = %s AND fecha = %s
    """, (sucursal_id, hoy))

    curva = list(rows)

    print(f"\n[3.2.4b] Curva horaria de {sucursal_id} — {hoy}")
    print(f"  {'Hora':<6} {'Total ventas':>14} {'Tickets':>8} {'Prom. ticket':>14}")
    print(f"  {'-'*46}")
    for r in curva:
        print(f"  {r.hora:02d}:00  "
            f"${float(r.total_ventas):>12,.2f}  "
            f"{int(r.cantidad_tickets):>7}  "
            f"${float(r.ticket_promedio):>12,.2f}")

    return curva


def q_comparar_sucursales(sucursal_id_1, sucursal_id_2, fecha_str, hora):
    """
    3.2.4 c) Comparar el desempeño de dos sucursales en la misma fecha y franja horaria.

    Dos queries de clave exacta (partition + clustering completo).
    Lectura O(1) por sucursal.
    """
    fecha = datetime.strptime(fecha_str, "%Y-%m-%d").date()

    comparacion = {}
    for suc_id in [sucursal_id_1, sucursal_id_2]:
        row = session.execute("""
            SELECT hora, total_ventas, cantidad_tickets, ticket_promedio
            FROM metricas_horarias
            WHERE sucursal_id = %s AND fecha = %s AND hora = %s
        """, (suc_id, fecha, hora)).one()

        if row:
            comparacion[suc_id] = {
                "total_ventas":    round(float(row.total_ventas), 2),
                "cantidad_tickets": int(row.cantidad_tickets),
                "ticket_promedio": round(float(row.ticket_promedio), 2),
            }
        else:
            comparacion[suc_id] = {
                "total_ventas": 0.0, "cantidad_tickets": 0, "ticket_promedio": 0.0
            }

    print(f"\n[3.2.4c] Comparación de sucursales — {fecha_str} hora {hora:02d}:00")
    print(f"  {'Sucursal':<12} {'Total ventas':>14} {'Tickets':>8} {'Prom. ticket':>14}")
    print(f"  {'-'*52}")
    for suc_id, datos in comparacion.items():
        print(f"  {suc_id:<12} "
            f"${datos['total_ventas']:>12,.2f}  "
            f"{datos['cantidad_tickets']:>7}  "
            f"${datos['ticket_promedio']:>12,.2f}")

    # Determinar ganador
    suc1, suc2 = sucursal_id_1, sucursal_id_2
    if comparacion[suc1]["total_ventas"] > comparacion[suc2]["total_ventas"]:
        print(f"\n  Mayor volumen en la franja: {suc1}")
    elif comparacion[suc2]["total_ventas"] > comparacion[suc1]["total_ventas"]:
        print(f"\n  Mayor volumen en la franja: {suc2}")
    else:
        print(f"\n  Empate en la franja horaria")

    return comparacion


def q_franja_mayor_ticket_promedio(sucursal_id):
    """
    3.2.4 d) Franja horaria de mayor ticket promedio de una sucursal en la última semana.

    Cassandra no tiene agregaciones entre particiones, por lo que se
    consulta cada día de la última semana de forma independiente (7 queries,
    una por partición) y se agrega el promedio semanal por hora en Python.
    """
    ahora = datetime.now()

    acumulado = {}   # {hora: {"suma": float, "dias": int}}

    for dias_atras in range(7):
        fecha = (ahora - timedelta(days=dias_atras)).date()
        rows  = session.execute("""
            SELECT hora, ticket_promedio, cantidad_tickets
            FROM metricas_horarias
            WHERE sucursal_id = %s AND fecha = %s
        """, (sucursal_id, fecha))

        for row in rows:
            if int(row.cantidad_tickets) == 0:
                continue
            if row.hora not in acumulado:
                acumulado[row.hora] = {"suma": 0.0, "dias": 0}
            acumulado[row.hora]["suma"] += float(row.ticket_promedio)
            acumulado[row.hora]["dias"] += 1

    if not acumulado:
        print(f"\n[3.2.4d] Sin datos para {sucursal_id} en la última semana")
        return None

    promedio_semanal = {
        hora: round(d["suma"] / d["dias"], 2)
        for hora, d in acumulado.items()
    }

    hora_pico   = max(promedio_semanal, key=promedio_semanal.get)
    prom_maximo = promedio_semanal[hora_pico]

    print(f"\n[3.2.4d] Franja de mayor ticket promedio — {sucursal_id} (última semana)")
    print(f"  {'Hora':<6} {'Prom. ticket (semana)':>22}")
    print(f"  {'-'*30}")
    for hora, prom in sorted(promedio_semanal.items()):
        marca = " ← PICO" if hora == hora_pico else ""
        print(f"  {hora:02d}:00  ${prom:>20,.2f}{marca}")

    print(f"\n  Franja pico: {hora_pico:02d}:00  —  ${prom_maximo:,.2f} promedio semanal")

    return {"hora_pico": hora_pico, "ticket_promedio_semana": prom_maximo,
            "detalle": promedio_semanal}


# =============================================================
# SECCIÓN 3.3 — Gestión de stock y alertas
# =============================================================

def q_registrar_movimiento_stock(sucursal_id, producto_id,
                                tipo_movimiento, cantidad, cajero_id, motivo=""):
    """
    3.3.6 a) Registrar un movimiento de stock.

    Tipos válidos:
    ENTRADA_INICIAL     — carga inicial del sistema
    ENTRADA_RECEPCION   — recepción de mercadería del proveedor
    SALIDA_VENTA        — descuento por venta registrada
    AJUSTE_MERMA        — ajuste por pérdida, vencimiento o rotura

    La tabla stock_eventos es append-only: nunca se modifica un registro,
    solo se agregan nuevos eventos. El stock actual se reconstruye sumando.
    """
    from decimal import Decimal

    tipos_validos = {"ENTRADA_INICIAL", "ENTRADA_RECEPCION", "SALIDA_VENTA", "AJUSTE_MERMA"}
    if tipo_movimiento not in tipos_validos:
        print(f"[ERROR] tipo_movimiento inválido: {tipo_movimiento}")
        return None

    ts = datetime.now()

    session.execute("""
        INSERT INTO stock_eventos
        (sucursal_id, producto_id, evento_timestamp,
        tipo_movimiento, cantidad, cajero_id, motivo)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (sucursal_id, producto_id, ts,
        tipo_movimiento, Decimal(str(cantidad)),
        cajero_id, motivo))

    print(f"\n[3.3.6a] Movimiento de stock registrado")
    print(f"  Sucursal       : {sucursal_id}")
    print(f"  Producto       : {producto_id}")
    print(f"  Tipo           : {tipo_movimiento}")
    print(f"  Cantidad       : {cantidad}")
    print(f"  Timestamp      : {ts.strftime('%Y-%m-%d %H:%M:%S')}")

    return {"sucursal_id": sucursal_id, "producto_id": producto_id,
            "tipo": tipo_movimiento, "cantidad": cantidad, "timestamp": str(ts)}


def q_stock_actual(sucursal_id, producto_id):
    """
    3.3.6 b) Stock actual de un producto en una sucursal sumando sus movimientos.

    Se lee la partición completa (sucursal_id, producto_id) de stock_eventos
    y se reconstruye el stock sumando entradas y restando salidas.
    Este es el patrón Event Sourcing aplicado a stock en Cassandra:
    el estado actual se deriva del historial de eventos.
    """
    ENTRADAS = {"ENTRADA_INICIAL", "ENTRADA_RECEPCION"}
    SALIDAS  = {"SALIDA_VENTA", "AJUSTE_MERMA"}

    rows = session.execute("""
        SELECT evento_timestamp, tipo_movimiento, cantidad
        FROM stock_eventos
        WHERE sucursal_id = %s AND producto_id = %s
    """, (sucursal_id, producto_id))

    eventos    = list(rows)
    stock      = 0.0
    entradas   = 0.0
    salidas    = 0.0

    for row in eventos:
        cant = float(row.cantidad)
        if row.tipo_movimiento in ENTRADAS:
            stock    += cant
            entradas += cant
        elif row.tipo_movimiento in SALIDAS:
            stock   -= cant
            salidas += cant

    stock_actual = round(max(0.0, stock), 3)

    print(f"\n[3.3.6b] Stock actual de {producto_id} en {sucursal_id}")
    print(f"  Eventos registrados : {len(eventos)}")
    print(f"  Total entradas      : {round(entradas, 3)}")
    print(f"  Total salidas       : {round(salidas, 3)}")
    print(f"  Stock actual        : {stock_actual}")

    # Detalle de los últimos 5 movimientos
    print(f"\n  Últimos movimientos:")
    for row in eventos[:5]:
        print(f"  {row.evento_timestamp.strftime('%Y-%m-%d %H:%M')}  "
            f"{row.tipo_movimiento:<22}  {float(row.cantidad)}")

    return {"stock_actual": stock_actual, "total_entradas": round(entradas, 3),
            "total_salidas": round(salidas, 3), "eventos": len(eventos)}


def q_alertas_stock_sucursal(sucursal_id, limite=20):
    """
    3.3.6 c) Listar las alertas de stock bajo activas para una sucursal.

    La partition key es sucursal_id, por lo que cada sucursal consulta
    solo sus propias alertas en O(1) sin scan global.
    """
    rows = session.execute("""
        SELECT alerta_timestamp, producto_id, nombre_producto,
            stock_actual, stock_minimo, proveedor_id
        FROM alertas_stock
        WHERE sucursal_id = %s
        LIMIT %s
    """, (sucursal_id, limite))

    alertas = list(rows)

    print(f"\n[3.3.6c] Alertas de stock bajo activas — {sucursal_id}")
    print(f"  Total alertas: {len(alertas)}")
    print(f"\n  {'Producto':<15} {'Nombre':<30} {'Stock actual':>12} {'Mínimo':>8}")
    print(f"  {'-'*68}")
    for a in alertas:
        print(f"  {a.producto_id:<15} {a.nombre_producto:<30} "
            f"{float(a.stock_actual):>11.1f}  {int(a.stock_minimo):>7}")

    return alertas


# =============================================================
# BLOQUE PRINCIPAL — demo de todas las queries
# =============================================================
if __name__ == "__main__":
    if session is None:
        print("[ERROR] No hay conexión con Cassandra. Verificá el .env y el estado del cluster.")
        exit(1)

    SEP = "=" * 60

    # Parámetros de ejemplo — ajustar según los datos generados
    SUCURSAL_1   = "SUC_001"
    SUCURSAL_2   = "SUC_002"
    HOY          = datetime.now().strftime("%Y-%m-%d")
    HORA_EJEMPLO = max(0, datetime.now().hour - 1)   # hora anterior a la actual

    # Para producto y fecha con datos, tomamos valores del rango generado
    from datetime import timedelta
    FECHA_CON_DATOS = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

    # Obtener un producto_id real desde la tabla (el primero que encontremos)
    row_prod = session.execute(
        "SELECT producto_id FROM ventas_producto LIMIT 1"
    ).one()
    PRODUCTO_ID = row_prod.producto_id if row_prod else "PROD_00001"

    print(f"\n{SEP}")
    print("  DEMO — Consultas Cassandra (TP2 Sección 3)")
    print(SEP)
    print(f"  Sucursal 1 : {SUCURSAL_1}")
    print(f"  Sucursal 2 : {SUCURSAL_2}")
    print(f"  Producto   : {PRODUCTO_ID}")
    print(f"  Fecha demo : {FECHA_CON_DATOS}")
    print(f"  Hora demo  : {HORA_EJEMPLO:02d}:00")

    # --- 3.1 ---
    print(f"\n{SEP}")
    print("  SECCIÓN 3.1 — Registro de ventas")
    print(SEP)
    q_tickets_sucursal_fecha(SUCURSAL_1, FECHA_CON_DATOS)
    q_total_vendido_2_horas(SUCURSAL_1)
    q_historial_producto_mes_actual(PRODUCTO_ID)
    q_tickets_mayor_monto(SUCURSAL_1, FECHA_CON_DATOS, monto_minimo=50000.0)

    # --- 3.2 ---
    print(f"\n{SEP}")
    print("  SECCIÓN 3.2 — Métricas en tiempo real")
    print(SEP)
    q_actualizar_metricas_hora(SUCURSAL_1, total_ticket=12500.0)
    q_curva_horaria_hoy(SUCURSAL_1)
    q_comparar_sucursales(SUCURSAL_1, SUCURSAL_2, HOY, HORA_EJEMPLO)
    q_franja_mayor_ticket_promedio(SUCURSAL_1)

    # --- 3.3 ---
    print(f"\n{SEP}")
    print("  SECCIÓN 3.3 — Gestión de stock y alertas")
    print(SEP)
    q_registrar_movimiento_stock(
        sucursal_id=SUCURSAL_1,
        producto_id=PRODUCTO_ID,
        tipo_movimiento="ENTRADA_RECEPCION",
        cantidad=50,
        cajero_id="DEPOSITO",
        motivo="Reposición semanal proveedor"
    )
    q_stock_actual(SUCURSAL_1, PRODUCTO_ID)
    q_alertas_stock_sucursal(SUCURSAL_1)

    print(f"\n{SEP}")
    print("  Demo finalizada.")
    print(SEP)
