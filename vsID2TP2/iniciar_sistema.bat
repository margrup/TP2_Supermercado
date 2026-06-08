@echo off
echo =======================================================
echo        SISTEMA POLIGLOTA - INICIO AUTOMATICO
echo =======================================================
echo.

echo [1/3] Iniciando motores en Docker (MongoDB y Cassandra)...
docker start mongo-tp2
docker start cassandra-tp2
echo Motores de Docker iniciados.
echo.

echo [2/3] Verificacion de Neo4j...
echo =======================================================
echo ATENCION: Neo4j Desktop no se puede iniciar desde aqui.
echo Por favor, asegurate de:
echo 1. Abrir la aplicacion "Neo4j Desktop" en tu Windows.
echo 2. Buscar tu instancia "TP2_Supermercado" y darle a START.
echo =======================================================
pause
echo.

echo [3/3] Iniciando la aplicacion Python...
call .\venv\Scripts\activate.bat
python main.py


