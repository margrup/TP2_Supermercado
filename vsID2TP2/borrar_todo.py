import os
from dotenv import load_dotenv
from cassandra.cluster import Cluster

load_dotenv()

def vaciar_cassandra():
    print("\n[INFO] Iniciando purga absoluta de datos en Cassandra...")
    
    nodes = os.getenv("CASSANDRA_NODES", "127.0.0.1").split(",")
    keyspace = os.getenv("CASSANDRA_KEYSPACE", "tp_supermercado")
    
    try:
        # Nos conectamos al cluster SIN especificar el keyspace, 
        # porque si el keyspace no existe, la conexión fallaría.
        cluster = Cluster(nodes)
        session = cluster.connect() 
        
        print(f"[INFO] Eliminando el keyspace '{keyspace}'...")
        session.execute(f"DROP KEYSPACE IF EXISTS {keyspace}")
        print(f"[OK] Cassandra: Keyspace '{keyspace}' y todo su contenido (Tablas, UDTs, Datos) eliminados con éxito.")
        
        # Verificar que ya no existe consultando el schema del sistema
        rows = session.execute("SELECT keyspace_name FROM system_schema.keyspaces")
        keyspaces = [row.keyspace_name for row in rows]
        if keyspace not in keyspaces:
            print(f"[OK] Verificación exitosa: El keyspace '{keyspace}' ya no existe en el cluster.")
            print("\n[SIGUIENTES PASOS]:")
            print("  1. Ejecuta tu script 'cassandra_schema.cql' para recrear las tablas y el nuevo UDT.")
            print("  2. Ejecuta 'python cassandra_seeder.py' para repoblar los datos.")
        else:
            print(f"[ERROR] La verificación falló: el keyspace '{keyspace}' aún existe.")
            
    except Exception as e:
        print(f"[ERROR] Error al conectar o eliminar en Cassandra: {e}")

if __name__ == "__main__":
    vaciar_cassandra()
    print("\n[FIN] Proceso finalizado.")