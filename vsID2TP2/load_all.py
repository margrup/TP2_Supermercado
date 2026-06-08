import os
import json
# pyrefly: ignore [missing-import]
from pymongo import MongoClient
from neo4j import GraphDatabase
from cassandra.cluster import Cluster
from dotenv import load_dotenv

load_dotenv()

print("========================================")
print("1. CARGANDO DATOS EN MONGODB")
print("========================================")
try:
    client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017/"), serverSelectionTimeoutMS=5000)
    client.admin.command('ping')
    db = client[os.getenv("MONGO_DB_NAME", "tp_supermercado")]
    
    collections_files = {
        "Productos": "data/mongo_productos.jsonl",
        "Sucursales": "data/mongo_sucursales.jsonl",
        "Proveedores": "data/mongo_proveedores.jsonl",
        "Stock": "data/mongo_stock.jsonl",
        "Tickets": "data/mongo_tickets.jsonl",
        "Promociones": "data/mongo_promociones.jsonl"
    }

    for coll, filename in collections_files.items():
        if not os.path.exists(filename):
            print(f"[WARN] No se encontró el archivo {filename}")
            continue
            
        print(f"Cargando {filename} en colección {coll}...")
        db[coll].drop()
        data = []
        with open(filename, 'r', encoding='utf-8') as f:
            for line in f:
                data.append(json.loads(line))
        if data:
            db[coll].insert_many(data)
        print(f"[OK] {len(data)} documentos cargados.")
except Exception as e:
    print(f"[ERROR MONGODB] Asegurate de tener MongoDB prendido localmente. {e}")


print("\n========================================")
print("2. CARGANDO DATOS EN NEO4J")
print("========================================")
try:
    driver = GraphDatabase.driver(
        os.getenv("NEO4J_URI", "bolt://127.0.0.1:7687"),
        auth=(os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASS", "password123"))
    )
    driver.verify_connectivity()
    
    cypher_queries = [
        "LOAD CSV WITH HEADERS FROM 'file:///neo4j_nodos_productos.csv' AS row MERGE (p:Producto {id: row.id}) SET p.nombre = row.nombre, p.precio = toFloat(row.precio), p.marca = row.marca, p.tiene_stock = toBoolean(row.tiene_stock)",
        "LOAD CSV WITH HEADERS FROM 'file:///neo4j_nodos_proveedores.csv' AS row MERGE (p:Proveedor {id: row.id}) SET p.nombre = row.nombre, p.pais = row.pais",
        "LOAD CSV WITH HEADERS FROM 'file:///neo4j_nodos_sucursales.csv' AS row MERGE (s:Sucursal {id: row.id}) SET s.nombre = row.nombre, s.ciudad = row.ciudad",
        "LOAD CSV WITH HEADERS FROM 'file:///neo4j_nodos_tickets.csv' AS row MERGE (t:Ticket {id: row.id}) SET t.fecha = row.fecha, t.total = toFloat(row.total)",
        "LOAD CSV WITH HEADERS FROM 'file:///neo4j_nodos_categorias.csv' AS row MERGE (c:Categoria {id: row.id}) SET c.nombre = row.nombre",
        "LOAD CSV WITH HEADERS FROM 'file:///neo4j_rel_contiene.csv' AS row MATCH (t:Ticket {id: row.ticket_id}), (p:Producto {id: row.producto_id}) MERGE (t)-[r:CONTIENE]->(p) SET r.cantidad = toFloat(row.cantidad), r.precio_unitario = toFloat(row.precio_unitario)",
        "LOAD CSV WITH HEADERS FROM 'file:///neo4j_rel_provee.csv' AS row MATCH (pr:Proveedor {id: row.proveedor_id}), (p:Producto {id: row.producto_id}) MERGE (pr)-[:PROVEE]->(p)",
        "LOAD CSV WITH HEADERS FROM 'file:///neo4j_rel_pertenece_a.csv' AS row MATCH (p:Producto {id: row.producto_id}), (c:Categoria {id: row.categoria_id}) MERGE (p)-[:PERTENECE_A]->(c)",
        "LOAD CSV WITH HEADERS FROM 'file:///neo4j_rel_sustituto_de.csv' AS row MATCH (p1:Producto {id: row.producto_id_1}), (p2:Producto {id: row.producto_id_2}) MERGE (p1)-[:SUSTITUTO_DE]->(p2)",
        "LOAD CSV WITH HEADERS FROM 'file:///neo4j_rel_complementario_a.csv' AS row MATCH (p1:Producto {id: row.producto_id_1}), (p2:Producto {id: row.producto_id_2}) MERGE (p1)-[:COMPLEMENTARIO_A]->(p2)"
    ]

    with driver.session() as session:
        # Borramos base vieja si existe
        session.run("MATCH (n) DETACH DELETE n")
        for q in cypher_queries:
            print(f"Ejecutando: {q[:60]}...")
            session.run(q)
    print("[OK] Grafos de Neo4j generados exitosamente.")
except Exception as e:
    print(f"[ERROR NEO4J] Asegurate de tener Neo4j prendido. {e}")


print("\n--- FINALIZADO ---")
