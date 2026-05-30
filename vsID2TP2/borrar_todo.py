from database import get_mongo_client, get_neo4j_driver, get_cassandra_session

db_mongo = get_mongo_client()
driver_neo4j = get_neo4j_driver()
session_cassandra = get_cassandra_session()

def vaciar_motores_completo():
    print("\n[INFO] Iniciando purga absoluta de datos...")
    
    # 1. Limpieza en MongoDB
    if db_mongo is not None:
        colecciones = ["Productos", "Sucursales", "Proveedores", "Promociones", "Tickets"]
        for col in colecciones:
            db_mongo[col].drop()
        print("[OK] MongoDB: Colecciones eliminadas con éxito.")
        
    # 2. Limpieza en Neo4j
    if driver_neo4j is not None:
        with driver_neo4j.session() as session:
            session.run("MATCH (n) DETACH DELETE n")
        print("[OK] Neo4j: Todos los nodos y relaciones fueron eliminados del grafo.")
        
    # 3. Limpieza en Cassandra
    if session_cassandra is not None:
        tablas = ["ventas_sucursal", "ventas_producto", "stock_eventos", "alertas_stock"]
        for table in tablas:
            try:
                session_cassandra.execute(f"TRUNCATE tp2_supermercado.{table}")
            except Exception as e:
                print(f"[Aviso] No se pudo vaciar la tabla {table}: {e}")
        print("[OK] Cassandra: Tablas vaciadas (estructuras DDL preservadas).")

if __name__ == "__main__":
    if db_mongo is None or driver_neo4j is None or session_cassandra is None:
        print("[ERROR] Asegurate de tener los 3 motores encendidos antes de limpiar.")
    else:
        vaciar_motores_completo()
        if driver_neo4j:
            driver_neo4j.close()
        print("\n[FIN] Todo el ecosistema quedó vacío y listo para tu nuevo código.")