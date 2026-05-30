import os
from dotenv import load_dotenv
from pymongo import MongoClient
from neo4j import GraphDatabase
from cassandra.cluster import Cluster

load_dotenv()


def get_mongo_client():
    """Conecta a MongoDB y devuelve la instancia de la base de datos."""
    try:
        uri     = os.getenv("MONGO_URI")
        db_name = os.getenv("MONGO_DB_NAME")
        client  = MongoClient(uri, serverSelectionTimeoutMS=3000)
        client.server_info()
        print("[OK] Conectado a MongoDB con exito.")
        return client[db_name]
    except Exception as e:
        print(f"[ERROR] No se pudo conectar a MongoDB: {e}")
        return None


def get_neo4j_driver():
    """Conecta a Neo4j y devuelve el driver."""
    try:
        uri      = os.getenv("NEO4J_URI")
        user     = os.getenv("NEO4J_USER")
        password = os.getenv("NEO4J_PASS")
        driver   = GraphDatabase.driver(uri, auth=(user, password))
        driver.verify_connectivity()
        print("[OK] Conectado a Neo4j con exito.")
        return driver
    except Exception as e:
        print(f"[ERROR] No se pudo conectar a Neo4j: {e}")
        return None


def get_cassandra_session():
    """Conecta a Cassandra y devuelve la sesion con el keyspace activo."""
    try:
        nodes    = os.getenv("CASSANDRA_NODES", "127.0.0.1").split(",")
        keyspace = os.getenv("CASSANDRA_KEYSPACE")
        cluster  = Cluster(nodes)
        session  = cluster.connect()
        session.set_keyspace(keyspace)
        print(f"[OK] Conectado a Cassandra (Keyspace: {keyspace}) con exito.")
        return session
    except Exception as e:
        print(f"[ERROR] No se pudo conectar a Cassandra: {e}")
        return None


def conectar_todo():
    """
    Establece las tres conexiones y devuelve un dict con los clientes.
    Requerido por main.py y operaciones.py.
    Cualquier motor que falle devuelve None en su clave.
    """
    return {
        "mongo":     get_mongo_client(),
        "neo4j":     get_neo4j_driver(),
        "cassandra": get_cassandra_session(),
    }