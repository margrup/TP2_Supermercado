from database import get_mongo_client, get_neo4j_driver, get_cassandra_session

print("Iniciando verificación de conexiones políglotas...\n")

# Intentar conectar a los 3 motores
mongo_db = get_mongo_client()
neo4j_driver = get_neo4j_driver()
cassandra_session = get_cassandra_session()

print("\n--- RESUMEN DE ESTADO ---")
print(f"MongoDB:   {'[LISTO]' if mongo_db is not None else '[FALLÓ]'}")
print(f"Neo4j:     {'[LISTO]' if neo4j_driver is not None else '[FALLÓ]'}")
print(f"Cassandra: {'[LISTO]' if cassandra_session is not None else '[FALLÓ]'}")

# Cerrar el driver de Neo4j si se abrió correctamente
if neo4j_driver:
    neo4j_driver.close()