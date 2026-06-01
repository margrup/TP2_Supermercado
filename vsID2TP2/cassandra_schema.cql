-- =============================================================
-- cassandra_schema.cql
-- Keyspace y tablas para el TP Supermercado (TP2)
--
-- Cómo ejecutar desde Docker:
--   docker exec -i <nombre_contenedor> cqlsh < cassandra_schema.cql
--
-- O desde cqlsh interactivo:
--   SOURCE '/ruta/cassandra_schema.cql'
-- =============================================================

CREATE KEYSPACE IF NOT EXISTS tp_supermercado
    WITH replication = {'class': 'SimpleStrategy', 'replication_factor': 1}
    AND durable_writes = true;

USE tp_supermercado;

-- -------------------------------------------------------------
-- UDT: linea_venta
-- Caso de uso: estructura fuertemente tipada para las líneas de venta
-- dentro de un ticket. Evita errores de parseo y formato.
-- -------------------------------------------------------------
CREATE TYPE IF NOT EXISTS linea_venta (
    producto_id TEXT,
    cantidad DECIMAL,
    precio_unitario DECIMAL,
    descuento_aplicado DECIMAL
);

-- -------------------------------------------------------------
-- Tabla 1: ventas_sucursal
-- Caso de uso: registro de cada ticket por sucursal y fecha.
--
-- Partition key: (sucursal_id, fecha)
-- Justificación: cada sucursal-día es una partición independiente.
-- Evita que una sucursal grande sature un nodo único y garantiza
-- que la partición no crezca indefinidamente (una por día).
--
-- Clustering: ticket_timestamp DESC
-- Justificación: los tickets más recientes se leen primero,
-- optimizando consultas de "última hora" o "últimas N ventas".
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ventas_sucursal (
    sucursal_id      TEXT,
    fecha            DATE,
    ticket_timestamp TIMESTAMP,
    ticket_id        TEXT,
    cajero           TEXT,
    medio_pago       TEXT,
    total            DECIMAL,
    lineas_venta     LIST<frozen<linea_venta>>,
    PRIMARY KEY ((sucursal_id, fecha), ticket_timestamp, ticket_id)
) WITH CLUSTERING ORDER BY (ticket_timestamp DESC, ticket_id ASC)
AND compaction = {
    'class': 'TimeWindowCompactionStrategy',
    'compaction_window_unit': 'DAYS',
    'compaction_window_size': 1
};


-- -------------------------------------------------------------
-- Tabla 2: ventas_producto
-- Caso de uso: historial de ventas de un producto específico.
--
-- Partition key: (producto_id, anio_mes)
-- Justificación: permite analizar la evolución mensual de un
-- producto. La partición por mes evita crecimiento indefinido:
-- cada mes cierra su partición y no vuelve a escribirse.
--
-- Clustering: ticket_timestamp DESC
-- Justificación: ventas más recientes primero.
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ventas_producto (
    producto_id      TEXT,
    anio_mes         TEXT,
    ticket_timestamp TIMESTAMP,
    ticket_id        TEXT,
    sucursal_id      TEXT,
    cantidad         DECIMAL,
    precio_unitario  DECIMAL,
    PRIMARY KEY ((producto_id, anio_mes), ticket_timestamp, ticket_id)
) WITH CLUSTERING ORDER BY (ticket_timestamp DESC, ticket_id ASC)
AND compaction = {
    'class': 'TimeWindowCompactionStrategy',
    'compaction_window_unit': 'DAYS',
    'compaction_window_size': 1
};


-- -------------------------------------------------------------
-- Tabla 3: metricas_horarias
-- Caso de uso: curva horaria de ventas de una sucursal en un día.
--
-- Partition key: (sucursal_id, fecha)
-- Justificación: toda la curva horaria de un día se lee en una
-- sola query de partición. Una fila por hora = máximo 24 filas
-- por partición (tamaño trivial, nunca crece indefinidamente).
--
-- Clustering: hora ASC
-- Justificación: lectura en orden cronológico (0 a 23).
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS metricas_horarias (
    sucursal_id      TEXT,
    fecha            DATE,
    hora             INT,
    total_ventas     DECIMAL,
    cantidad_tickets INT,
    ticket_promedio  DECIMAL,
    PRIMARY KEY ((sucursal_id, fecha), hora)
) WITH CLUSTERING ORDER BY (hora ASC)
AND compaction = {
    'class': 'TimeWindowCompactionStrategy',
    'compaction_window_unit': 'DAYS',
    'compaction_window_size': 1
};


-- -------------------------------------------------------------
-- Tabla 4: stock_eventos
-- Caso de uso: log de movimientos de stock (entradas, salidas,
-- ajustes por merma). Permite reconstruir el stock actual
-- sumando todos los movimientos de una partición.
--
-- Partition key: (sucursal_id, producto_id)
-- Justificación: todos los movimientos de un producto en una
-- sucursal viven juntos. Tamaño acotado: un producto activo
-- con alta rotación genera ~50 movimientos/día × 365 = ~18.000
-- filas/año, dentro del límite recomendado de Cassandra.
--
-- Clustering: evento_timestamp DESC
-- Justificación: el movimiento más reciente se lee primero,
-- optimizando la consulta de stock actual.
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stock_eventos (
    sucursal_id      TEXT,
    producto_id      TEXT,
    evento_timestamp TIMESTAMP,
    tipo_movimiento  TEXT,
    cantidad         DECIMAL,
    cajero_id        TEXT,
    motivo           TEXT,
    PRIMARY KEY ((sucursal_id, producto_id), evento_timestamp)
) WITH CLUSTERING ORDER BY (evento_timestamp DESC)
AND compaction = {
    'class': 'TimeWindowCompactionStrategy',
    'compaction_window_unit': 'DAYS',
    'compaction_window_size': 1
};


-- -------------------------------------------------------------
-- Tabla 5: alertas_stock
-- Caso de uso: alertas de stock bajo activas por sucursal.
--
-- Partition key: sucursal_id
-- Justificación: cada sucursal consulta únicamente sus propias
-- alertas sin hacer un scan global. El número de alertas activas
-- por sucursal es acotado (máximo = cantidad de productos).
--
-- Clustering: alerta_timestamp DESC, producto_id ASC
-- Justificación: las alertas más recientes primero;
-- producto_id garantiza unicidad en el clustering key.
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS alertas_stock (
    sucursal_id      TEXT,
    alerta_timestamp TIMESTAMP,
    producto_id      TEXT,
    nombre_producto  TEXT,
    stock_actual     DECIMAL,
    stock_minimo     INT,
    proveedor_id     TEXT,
    PRIMARY KEY (sucursal_id, alerta_timestamp, producto_id)
) WITH CLUSTERING ORDER BY (alerta_timestamp DESC, producto_id ASC);
