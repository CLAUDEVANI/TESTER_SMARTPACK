#!/bin/bash

echo "🚀 Iniciando o NOC Telemetria Universal..."

# Navega para o diretório correto do projeto
cd /home/t430/Tester_Smartpack || exit

# Ativa o ambiente virtual
source venv/bin/activate

# Roda a migração do banco (segura — só age se necessário)
echo "🔄 Verificando migração do banco de dados..."
python migrate_db.py

# Inicia o FastAPI usando uvicorn (acessível na porta 8000 de toda a rede)
echo "✅ Iniciando servidor na porta 8000..."
uvicorn main:app --host 0.0.0.0 --port 8000