"""
migrate_db.py — Migra o banco telemetria.db existente para o novo esquema universal

Execute UMA VEZ antes de iniciar o novo main.py:
  python migrate_db.py

O que este script faz:
  1. Adiciona colunas 'protocolo' e 'driver_config' na tabela sites (se não existirem)
  2. Define protocolo='snmp' para todos os sites existentes (comportamento anterior)
  3. Preenche driver_config com os OIDs padrão Eltek para os sites existentes
  4. Cria a tabela driver_profiles com os perfis padrão
  5. NÃO apaga dados existentes (histórico, alarmes, sites)
"""

import json
import sqlite3
from pathlib import Path

DB_PATH = "telemetria.db"

ELTEK_CONFIG = {
    "community": "public",
    "port": 161,
    "version": 1,
    "oids": {
        "tensao":      "1.3.6.1.4.1.12148.10.2.4.1.1.0",
        "corrente":    "1.3.6.1.4.1.12148.10.2.4.1.2.0",
        "temperatura": "1.3.6.1.4.1.12148.10.2.4.1.3.0",
        "capacidade":  "1.3.6.1.4.1.12148.10.2.4.1.4.0"
    },
    "scale": {"tensao": 0.01, "corrente": 0.1, "temperatura": 1.0, "capacidade": 1.0},
    "capacidade_banco_ah": 400.0
}


def migrar():
    if not Path(DB_PATH).exists():
        print(f"❌ Banco '{DB_PATH}' não encontrado. Execute na mesma pasta do banco.")
        return

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    print("🔄 Iniciando migração do banco de dados...")

    # 1. Adicionar novas colunas na tabela sites
    for col, default in [("protocolo", "'snmp'"), ("driver_config", "'{}'")]:
        try:
            c.execute(f"ALTER TABLE sites ADD COLUMN {col} TEXT DEFAULT {default}")
            print(f"  ✅ Coluna '{col}' adicionada em 'sites'.")
        except sqlite3.OperationalError:
            print(f"  ⏭️  Coluna '{col}' já existe. Pulando.")

    # 2. Atualizar sites existentes com protocolo SNMP e config Eltek
    c.execute("SELECT site_id, protocolo FROM sites")
    sites = c.fetchall()
    for site_id, protocolo in sites:
        if not protocolo or protocolo in ("", "None", "null"):
            c.execute(
                "UPDATE sites SET protocolo=?, driver_config=? WHERE site_id=?",
                ("snmp", json.dumps(ELTEK_CONFIG), site_id)
            )
            print(f"  ✅ Site '{site_id}' → protocolo=snmp (Eltek padrão).")
        else:
            print(f"  ⏭️  Site '{site_id}' já tem protocolo='{protocolo}'. Sem alterações.")

    # 3. Criar tabela driver_profiles se não existir
    c.execute('''
        CREATE TABLE IF NOT EXISTS driver_profiles (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            nome          TEXT UNIQUE NOT NULL,
            protocolo     TEXT NOT NULL,
            fabricante    TEXT,
            descricao     TEXT,
            driver_config TEXT NOT NULL DEFAULT '{}'
        )
    ''')
    print("  ✅ Tabela 'driver_profiles' verificada/criada.")

    # 4. Seed dos perfis padrão
    perfis = [
        ("Eltek Smartpack S", "snmp", "Eltek / Delta",
         "Controladora retificadora 48V DC. OIDs SNMP v2c nativos.",
         json.dumps(ELTEK_CONFIG)),
        ("Modbus TCP Genérico", "modbus_tcp", "Genérico",
         "Qualquer dispositivo Modbus TCP. Ajuste endereços conforme mapa do fabricante.",
         json.dumps({
             "port": 502, "unit_id": 1, "timeout": 3.0, "capacidade_banco_ah": 200.0,
             "registers": {
                 "tensao":      {"address": 0, "count": 1, "scale": 0.1,  "type": "holding"},
                 "corrente":    {"address": 1, "count": 1, "scale": 0.1,  "type": "holding"},
                 "temperatura": {"address": 2, "count": 1, "scale": 0.1,  "type": "holding"},
                 "capacidade":  {"address": 3, "count": 1, "scale": 1.0,  "type": "holding"},
                 "soh":         {"address": 4, "count": 1, "scale": 1.0,  "type": "holding"},
             }
         })),
        ("Moura MSL RS485", "modbus_rtu", "Moura",
         "Bateria de lítio Moura MSL via RS485. Solicite mapa de registradores à Moura B2B.",
         json.dumps({
             "port": "/dev/ttyUSB0", "baudrate": 9600, "slave_id": 1,
             "bytesize": 8, "parity": "N", "stopbits": 1,
             "capacidade_banco_ah": 200.0,
             "registers": {
                 "tensao":      {"address": 0, "count": 1, "scale": 0.01, "type": "holding"},
                 "corrente":    {"address": 1, "count": 1, "scale": 0.01, "type": "holding"},
                 "temperatura": {"address": 2, "count": 1, "scale": 0.1,  "type": "holding"},
                 "capacidade":  {"address": 3, "count": 1, "scale": 1.0,  "type": "holding"},
                 "soh":         {"address": 4, "count": 1, "scale": 1.0,  "type": "holding"},
                 "alarme":      {"address": 5, "count": 1, "scale": 1.0,  "type": "holding"},
             }
         })),
        ("Simulador", "simulator", "Interno",
         "Modo offline para desenvolvimento e testes. Não requer hardware.",
         json.dumps({"tensao_base": 54.0, "capacidade_banco_ah": 400.0, "modo_alarme": True})),
    ]
    for perfil in perfis:
        try:
            c.execute(
                "INSERT INTO driver_profiles (nome, protocolo, fabricante, descricao, driver_config) VALUES (?,?,?,?,?)",
                perfil
            )
            print(f"  ✅ Perfil '{perfil[0]}' inserido.")
        except sqlite3.IntegrityError:
            print(f"  ⏭️  Perfil '{perfil[0]}' já existe. Pulando.")

    conn.commit()
    conn.close()
    print("\n✅ Migração concluída com sucesso! Pode iniciar o novo main.py.")
    print("   Comando: uvicorn main:app --host 0.0.0.0 --port 8000 --reload")


if __name__ == "__main__":
    migrar()