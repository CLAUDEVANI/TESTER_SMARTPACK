#!/bin/bash

echo "🚀 Iniciando o Servidor - Tester Smartpack NOC..."

# Navega para o diretório correto do projeto
cd /home/t430/Tester_Smartpack || exit

# Ativa o ambiente virtual
source venv/bin/activate

# Inicia o FastAPI usando uvicorn (acessível na porta 8000 de toda a rede)
uvicorn main:app --host 0.0.0.0 --port 8000