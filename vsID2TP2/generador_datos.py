import json
import csv
import random
from datetime import datetime, timedelta
from faker import Faker
import os

# Crear la carpeta data/ si no existe
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

fake = Faker('es_AR')
# Semilla opcional para reproducibilidad — comentar si se quiere variedad en cada ejecución
# random.seed(42)

HOY = datetime.now()

# ==========================================
# CONFIGURACIÓN DE VOLÚMENES (TP req. 3.1.5)
# ==========================================
VOL_SUCURSALES  = 5
VOL_PROVEEDORES = 20
VOL_PRODUCTOS   = 500
VOL_TICKETS     = 2000

print("Iniciando generación de datos de prueba...\n")


# ==========================================
# BLOQUE 1 — ENTIDADES MAESTRAS
# ==========================================

# --- SUCURSALES ---
sucursales = []
zonas = ["Norte", "Sur", "Este", "Oeste", "Centro"]
for i in range(1, VOL_SUCURSALES + 1):
    sucursales.append({
        "_id":              f"SUC_{i:03d}",
        "nombre":           f"Supermercado {fake.city()}",
        "direccion":        fake.street_address(),
        "ciudad":           "Buenos Aires",
        "zona":             zonas[i - 1],
        "superficie":       random.randint(500, 2000),
        "horario_atencion": "08:00 - 22:00"
    })

# --- PROVEEDORES ---
proveedores = []
for i in range(1, VOL_PROVEEDORES + 1):
    proveedores.append({
        "_id":                 f"PROV_{i:03d}",
        "nombre":              fake.company(),
        "cuit":                fake.ssn(),
        "condiciones_pago":    random.choice(["30 dias", "60 dias", "Contado"]),
        "plazo_entrega_dias":  random.randint(1, 7),
        "productos_que_provee": []   # Se completa al generar productos (embedding intencional)
    })

# --- CATEGORÍAS ---
# En MongoDB: campos embebidos dentro de cada producto (no colección separada).
# En Neo4j: nodos independientes para poder modelar relaciones entre categorías (req. 7.e).
categorias_dict = {
    "CAT_01": {"nombre": "Lácteos",    "subcategorias": ["Leches", "Quesos", "Yogures", "Cremas"]},
    "CAT_02": {"nombre": "Bebidas",    "subcategorias": ["Gaseosas", "Aguas", "Cervezas", "Vinos", "Jugos"]},
    "CAT_03": {"nombre": "Almacén",    "subcategorias": ["Fideos", "Arroz", "Conservas", "Aceites", "Harinas"]},
    "CAT_04": {"nombre": "Limpieza",   "subcategorias": ["Detergentes", "Lavandina", "Jabones", "Desodorantes de ambiente"]},
    "CAT_05": {"nombre": "Carnicería", "subcategorias": ["Vacuno", "Cerdo", "Pollo", "Embutidos"]},
}


def generar_ean13():
    """Genera un código EAN-13 con dígito verificador válido."""
    digits = [random.randint(0, 9) for _ in range(12)]
    check  = (10 - sum((3 if i % 2 else 1) * d for i, d in enumerate(digits)) % 10) % 10
    return ''.join(map(str, digits)) + str(check)


# --- PRODUCTOS ---
productos          = []
codigos_usados     = set()
proveedores_by_id  = {p["_id"]: p for p in proveedores}

for i in range(1, VOL_PRODUCTOS + 1):
    cat_id   = random.choice(list(categorias_dict.keys()))
    cat_info = categorias_dict[cat_id]
    subcategoria = random.choice(cat_info["subcategorias"])
    proveedor    = random.choice(proveedores)

    u_medida = "Kg" if cat_id == "CAT_05" else "Unidad"
    precio_c = round(random.uniform(100, 5000), 2)

    # Código de barras único (campo explícito, separado del _id)
    codigo_barras = generar_ean13()
    while codigo_barras in codigos_usados:
        codigo_barras = generar_ean13()
    codigos_usados.add(codigo_barras)

    prod = {
        "_id":          f"PROD_{i:05d}",
        "codigo_barras": codigo_barras,
        "nombre":        f"{subcategoria} {fake.word().capitalize()}",
        "categoria":     cat_info["nombre"],
        "categoria_id":  cat_id,              # clave foránea lógica para neo4j
        "subcategoria":  subcategoria,
        "marca":         fake.last_name(),
        "proveedor_id":  proveedor["_id"],
        "precio_costo":  precio_c,
        "precio_venta":  round(precio_c * random.uniform(1.3, 1.8), 2),
        "unidad_medida": u_medida
    }
    productos.append(prod)
    proveedores_by_id[proveedor["_id"]]["productos_que_provee"].append(prod["_id"])

productos_by_id = {p["_id"]: p for p in productos}


# ==========================================
# BLOQUE 2 — RELACIONES DE DOMINIO
# ==========================================
#
# Estas relaciones son datos del dominio (conocidos de antemano).
# Los CSVs son archivos planos; las relaciones las creará el alumno
# en Neo4j usando LOAD CSV + MERGE.

# --- SUSTITUTOS (bidireccional: misma categoría, distinto proveedor) ---
sustitutos_pares = set()
sustitutos       = []

for p1 in productos:
    if random.random() < 0.35:
        candidatos = [
            p2 for p2 in productos
            if p2["categoria"]    == p1["categoria"]
            and p2["_id"]         != p1["_id"]
            and p2["proveedor_id"] != p1["proveedor_id"]
        ]
        if candidatos:
            p2  = random.choice(candidatos)
            par = tuple(sorted([p1["_id"], p2["_id"]]))
            if par not in sustitutos_pares:
                sustitutos_pares.add(par)
                # Ambas direcciones: A→B y B→A
                sustitutos.append({"producto_id_1": p1["_id"], "producto_id_2": p2["_id"]})
                sustitutos.append({"producto_id_1": p2["_id"], "producto_id_2": p1["_id"]})

# --- COMPLEMENTARIOS (pares con sentido comercial entre categorías relacionadas) ---
# Se generan ~30 pares fuertes que se inyectarán con alta frecuencia en los tickets,
# lo que garantiza que el coeficiente de asociación supere 0.7 en las queries de Neo4j.

pares_cat_complementarias = [
    ("CAT_03", "CAT_01"),  # Almacén + Lácteos   (ej.: fideos + queso)
    ("CAT_03", "CAT_02"),  # Almacén + Bebidas   (ej.: arroz + agua)
    ("CAT_05", "CAT_02"),  # Carnicería + Bebidas(ej.: asado + cerveza)
    ("CAT_01", "CAT_02"),  # Lácteos + Bebidas   (ej.: yogur + jugo)
]

complementarios_pares = set()
complementarios       = []
mapa_complementarios  = {}   # producto_id → complementario_id (para el sesgo en tickets)

for cat_a, cat_b in pares_cat_complementarias:
    prods_a = [p for p in productos if p["categoria_id"] == cat_a]
    prods_b = [p for p in productos if p["categoria_id"] == cat_b]
    for _ in range(8):   # 8 pares × 4 combinaciones = ~32 pares fuertes
        if not prods_a or not prods_b:
            continue
        p1  = random.choice(prods_a)
        p2  = random.choice(prods_b)
        par = tuple(sorted([p1["_id"], p2["_id"]]))
        if par not in complementarios_pares and p1["_id"] != p2["_id"]:
            complementarios_pares.add(par)
            complementarios.append({"producto_id_1": p1["_id"], "producto_id_2": p2["_id"]})
            # Mapa bidireccional para la inyección de sesgo
            mapa_complementarios[p1["_id"]] = p2["_id"]
            mapa_complementarios[p2["_id"]] = p1["_id"]


# ==========================================
# BLOQUE 3 — STOCK
# ==========================================
stock                      = []
stock_total_por_producto   = {p["_id"]: 0 for p in productos}

for p in productos:
    for s in sucursales:
        cantidad_min = random.randint(20, 50)

        # ~20 % de registros con stock por debajo del punto de reposición
        if random.random() < 0.20:
            cantidad_disp = random.randint(0, cantidad_min - 1)
        else:
            cantidad_disp = random.randint(cantidad_min, 500)

        stock.append({
            "_id":                        f"STK_{p['_id']}_{s['_id']}",
            "producto":                   p["_id"],
            "sucursal":                   s["_id"],
            "cantidad_disponible":        cantidad_disp,
            "cantidad_minima":            cantidad_min,
            "fecha_ultima_actualizacion": HOY.strftime("%Y-%m-%dT%H:%M:%S")
        })
        stock_total_por_producto[p["_id"]] += cantidad_disp

# tiene_stock = True si el producto tiene stock total > 0 en todas las sucursales
tiene_stock = {pid: total > 0 for pid, total in stock_total_por_producto.items()}


# ==========================================
# BLOQUE 4 — TICKETS
# ==========================================
#
# Rango temporal: últimos 90 días desde hoy.
# Esto garantiza datos para:
#   - req. 3.1.4.c: "último mes" (mes actual o anterior)
#   - req. 3.1.4.d: "variación vs período anterior" (dos meses comparables)

FECHA_INICIO = HOY - timedelta(days=90)

tickets        = []
neo4j_contiene = []   # Datos crudos de la relación Ticket -[CONTIENE]-> Producto

for i in range(1, VOL_TICKETS + 1):
    t_id        = f"TK_{i:07d}"
    sucursal_id = random.choice(sucursales)["_id"]

    dias_offset = random.randint(0, 89)
    hora        = random.randint(8, 21)
    minuto      = random.randint(0, 59)
    fecha_hora  = FECHA_INICIO + timedelta(days=dias_offset, hours=hora, minutes=minuto)

    cant_items = random.randint(5, 12)

    # ---- Inyección de sesgo (corregido) ----
    # Se trabaja sobre IDs para evitar el bug de mutación de índices en la lista original.
    ids_ticket = [p["_id"] for p in random.sample(productos, cant_items)]

    for pid in list(ids_ticket):   # copia para iterar sin modificar en el mismo loop
        if pid in mapa_complementarios and random.random() < 0.85:
            comp_id = mapa_complementarios[pid]
            if comp_id not in ids_ticket:
                ids_ticket.append(comp_id)

    # Reconstruir objetos (ya sin duplicados por el control de ids_ticket)
    productos_ticket = [productos_by_id[pid] for pid in ids_ticket]

    lineas_mongo = []
    total_ticket = 0.0

    for prod in productos_ticket:
        cant     = round(random.uniform(0.5, 3.5), 3) if prod["unidad_medida"] == "Kg" else random.randint(1, 5)
        precio_u = prod["precio_venta"]
        descuento = round(precio_u * cant * 0.20, 2) if random.random() < 0.15 else 0.0
        subtotal  = round(precio_u * cant - descuento, 2)
        total_ticket += subtotal

        lineas_mongo.append({
            "producto":          prod["_id"],
            "cantidad":          cant,
            "precio_unitario":   precio_u,
            "descuento_aplicado": descuento
        })

        neo4j_contiene.append({
            "ticket_id":  t_id,
            "producto_id": prod["_id"],
            "cantidad":   cant
        })

    tickets.append({
        "_id":          t_id,
        "sucursal":     sucursal_id,
        "fecha_hora":   fecha_hora.strftime("%Y-%m-%dT%H:%M:%S"),
        "cajero":       fake.name(),
        "medio_pago":   random.choice(["Efectivo", "Debito", "Credito", "QR", "MercadoPago"]),
        "total":        round(total_ticket, 2),
        "lineas_venta": lineas_mongo
    })


# ==========================================
# BLOQUE 5 — PROMOCIONES (fechas dinámicas)
# ==========================================
# 6 promociones activas hoy + 4 vencidas  → la query 3.1.4.e devuelve resultados reales.

promociones = []
for i in range(1, 11):
    if i <= 6:
        inicio = HOY - timedelta(days=random.randint(1, 15))
        fin    = HOY + timedelta(days=random.randint(1, 20))
    else:
        fin    = HOY - timedelta(days=random.randint(1, 30))
        inicio = fin  - timedelta(days=30)

    promociones.append({
        "_id":                    f"PROMO_{i:03d}",
        "tipo":                   random.choice(["2x1", "70% 2da Unidad", "3x2", "Descuento Bancario"]),
        "productos_involucrados": [p["_id"] for p in random.sample(productos, random.randint(2, 6))],
        "sucursales_aplicables":  [s["_id"] for s in random.sample(sucursales, random.randint(2, 5))],
        "vigencia_inicio":        inicio.strftime("%Y-%m-%d"),
        "vigencia_fin":           fin.strftime("%Y-%m-%d"),
        "condicion_activacion":   random.choice(["Socio Club", "App", "Tarjeta Banco Nacion", "Sin condicion"])
    })


# ==========================================
# BLOQUE 6 — EXPORTACIÓN MONGODB (JSONL)
# ==========================================

def exportar_jsonl(datos, filename):
    filepath = os.path.join(DATA_DIR, filename) # <-- Ruteo a carpeta
    with open(filepath, 'w', encoding='utf-8') as f:
        for item in datos:
            f.write(json.dumps(item, ensure_ascii=False) + '\n')

print("Exportando archivos JSONL para MongoDB...")
exportar_jsonl(productos,   'mongo_productos.jsonl')
exportar_jsonl(sucursales,  'mongo_sucursales.jsonl')
exportar_jsonl(proveedores, 'mongo_proveedores.jsonl')
exportar_jsonl(stock,       'mongo_stock.jsonl')
exportar_jsonl(tickets,     'mongo_tickets.jsonl')
exportar_jsonl(promociones, 'mongo_promociones.jsonl')


# ==========================================
# BLOQUE 7 — EXPORTACIÓN NEO4J (CSV datos crudos)
# ==========================================


def exportar_csv(datos, filename, fieldnames):
    filepath = os.path.join(DATA_DIR, filename) # <-- Ruteo a carpeta
    with open(filepath, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(datos)

print("Exportando archivos CSV para Neo4j...")

# Nodos
exportar_csv(
    [{
        "id":           p["_id"],
        "codigo_barras": p["codigo_barras"],
        "nombre":       p["nombre"],
        "categoria":    p["categoria"],
        "subcategoria": p["subcategoria"],
        "tiene_stock":  str(tiene_stock[p["_id"]]).upper(),   # TRUE / FALSE
        "precio_venta": p["precio_venta"]
    } for p in productos],
    'neo4j_nodos_productos.csv',
    ['id', 'codigo_barras', 'nombre', 'categoria', 'subcategoria', 'tiene_stock', 'precio_venta']
)

exportar_csv(
    [{"id": p["_id"], "nombre": p["nombre"], "cuit": p["cuit"]} for p in proveedores],
    'neo4j_nodos_proveedores.csv',
    ['id', 'nombre', 'cuit']
)

exportar_csv(
    [{"id": s["_id"], "nombre": s["nombre"], "ciudad": s["ciudad"], "zona": s["zona"]} for s in sucursales],
    'neo4j_nodos_sucursales.csv',
    ['id', 'nombre', 'ciudad', 'zona']
)

exportar_csv(
    [{
        "id":          t["_id"],
        "fecha_hora":  t["fecha_hora"],
        "sucursal_id": t["sucursal"],
        "medio_pago":  t["medio_pago"],
        "total":       t["total"]
    } for t in tickets],
    'neo4j_nodos_tickets.csv',
    ['id', 'fecha_hora', 'sucursal_id', 'medio_pago', 'total']
)

exportar_csv(
    [{"id": cat_id, "nombre": info["nombre"]} for cat_id, info in categorias_dict.items()],
    'neo4j_nodos_categorias.csv',
    ['id', 'nombre']
)

# Relaciones
exportar_csv(
    neo4j_contiene,
    'neo4j_rel_contiene.csv',
    ['ticket_id', 'producto_id', 'cantidad']
)

exportar_csv(
    [{"proveedor_id": p["proveedor_id"], "producto_id": p["_id"]} for p in productos],
    'neo4j_rel_provee.csv',
    ['proveedor_id', 'producto_id']
)

exportar_csv(
    [{"producto_id": p["_id"], "categoria_id": p["categoria_id"]} for p in productos],
    'neo4j_rel_pertenece_a.csv',
    ['producto_id', 'categoria_id']
)

exportar_csv(sustitutos,     'neo4j_rel_sustituto_de.csv',     ['producto_id_1', 'producto_id_2'])
exportar_csv(complementarios,'neo4j_rel_complementario_a.csv', ['producto_id_1', 'producto_id_2'])


# ==========================================
# RESUMEN
# ==========================================
activas_hoy = sum(1 for p in promociones if p["vigencia_fin"] >= HOY.strftime("%Y-%m-%d"))

print("\n=== RESUMEN DE DATOS GENERADOS ===")
print(f"  Productos        : {len(productos)}")
print(f"  Sucursales       : {len(sucursales)}")
print(f"  Proveedores      : {len(proveedores)}")
print(f"  Registros stock  : {len(stock)}")
print(f"  Tickets          : {len(tickets)}")
print(f"  Líneas totales   : {len(neo4j_contiene)}")
print(f"  Sustitutos       : {len(sustitutos) // 2} pares ({len(sustitutos)} relaciones bidireccionales)")
print(f"  Complementarios  : {len(complementarios)} pares")
print(f"  Promociones      : {len(promociones)} total — {activas_hoy} activas hoy")
print("\n=== ARCHIVOS MONGODB ===")
for f in ['mongo_productos','mongo_sucursales','mongo_proveedores','mongo_stock','mongo_tickets','mongo_promociones']:
    print(f"  {f}.jsonl")
print("\n=== ARCHIVOS NEO4J ===")
for f in ['neo4j_nodos_productos','neo4j_nodos_proveedores','neo4j_nodos_sucursales',
        'neo4j_nodos_tickets','neo4j_nodos_categorias']:
    print(f"  {f}.csv  (nodos)")
for f in ['neo4j_rel_contiene','neo4j_rel_provee','neo4j_rel_pertenece_a',
        'neo4j_rel_sustituto_de','neo4j_rel_complementario_a']:
    print(f"  {f}.csv  (relaciones)")
print("\n¡Listo!")