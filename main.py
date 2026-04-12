"""
main.py — Backend universal de telemetria de energia
Suporta: SNMP, Modbus TCP, Modbus RTU, Simulador (e qualquer driver futuro)

Arquitetura:
  - Cada site tem um 'protocolo' e um 'driver_config' (JSON) na tabela sites
  - O Driver Manager instancia o driver correto por site
  - Toda telemetria é normalizada para o mesmo formato pelo driver
  - O frontend e o banco nunca mudam — só os drivers evoluem

Instalação:
  pip install fastapi uvicorn pysnmp pymodbus pyserial fpdf matplotlib

Execução:
  uvicorn main:app --host 0.0.0.0 --port 8000 --reload
"""

import asyncio
import json
import os
import sqlite3
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel

# Importa o pacote de drivers — registra todos automaticamente
import drivers
from drivers import get_driver_class, list_drivers, BaseDriver, Telemetria, Alarme

app = FastAPI(
    title="NOC Telemetria Universal",
    description="Monitoramento universal de energia: SNMP, Modbus TCP, Modbus RTU",
    version="2.0.0"
)

# ---------------------------------------------------------------------------
# Estado global em memória (por site)
# ---------------------------------------------------------------------------
SITES: dict[str, dict] = {}                  # site_id -> {nome, ip, protocolo, driver_config}
DRIVER_INSTANCES: dict[str, BaseDriver] = {} # site_id -> instância do driver ativo
telemetria_atual: dict[str, dict] = {}
alarmes_ativos: dict[str, dict] = {}

# ---------------------------------------------------------------------------
# Banco de dados
# ---------------------------------------------------------------------------
DB_PATH = "telemetria.db"


def db_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db_conn()
    c = conn.cursor()

    # Tabela de sites com suporte a múltiplos protocolos (Fase 1→4)
    c.execute('''
        CREATE TABLE IF NOT EXISTS sites (
            site_id       TEXT PRIMARY KEY,
            nome          TEXT,
            ip            TEXT,
            protocolo     TEXT DEFAULT 'simulator',
            driver_config TEXT DEFAULT '{}'
        )
    ''')

    # Migração segura: adiciona colunas novas se ainda não existirem
    for col, default in [("protocolo", "'simulator'"), ("driver_config", "'{}'")]:
        try:
            c.execute(f"ALTER TABLE sites ADD COLUMN {col} TEXT DEFAULT {default}")
        except sqlite3.OperationalError:
            pass

    c.execute('''
        CREATE TABLE IF NOT EXISTS historico (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            site_id     TEXT DEFAULT 's1',
            timestamp   DATETIME DEFAULT CURRENT_TIMESTAMP,
            tensao      REAL,
            corrente    REAL,
            temperatura REAL,
            capacidade  REAL
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS alarmes_historico (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            site_id     TEXT DEFAULT 's1',
            timestamp   DATETIME DEFAULT CURRENT_TIMESTAMP,
            evento      TEXT,
            severidade  TEXT,
            status      TEXT
        )
    ''')

    # Fase 4: Tabela de perfis de driver (mapa de registradores/OIDs configurável via UI)
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

    # Tabela de configurações globais
    c.execute('''
        CREATE TABLE IF NOT EXISTS config (
            chave TEXT PRIMARY KEY,
            valor TEXT
        )
    ''')

    # Insere perfis padrão se não existirem
    _seed_default_profiles(c)

    # Insere sites de exemplo se a tabela estiver vazia
    c.execute("SELECT COUNT(*) FROM sites")
    if c.fetchone()[0] == 0:
        eltek_cfg = json.dumps({
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
        })
        sim_cfg = json.dumps({"tensao_base": 54.0, "capacidade_banco_ah": 400.0})

        c.executemany(
            "INSERT OR IGNORE INTO sites (site_id, nome, ip, protocolo, driver_config) VALUES (?,?,?,?,?)",
            [
                ("s1", "Data Center Alpha (Matriz)", "192.168.10.20", "snmp",      eltek_cfg),
                ("s2", "Site Beta (Filial)",          "192.168.10.21", "simulator", sim_cfg),
                ("s3", "Claro",                        "192.168.15.22", "simulator", sim_cfg),
            ]
        )

    conn.commit()
    conn.close()


def _seed_default_profiles(cursor):
    """Insere perfis de driver padrão para fabricantes conhecidos."""
    perfis = [
        (
            "Eltek Smartpack S",
            "snmp",
            "Eltek / Delta",
            "Controladora retificadora 48V DC. OIDs SNMP v2c nativos.",
            json.dumps({
                "community": "public", "port": 161, "version": 1,
                "oids": {
                    "tensao":      "1.3.6.1.4.1.12148.10.2.4.1.1.0",
                    "corrente":    "1.3.6.1.4.1.12148.10.2.4.1.2.0",
                    "temperatura": "1.3.6.1.4.1.12148.10.2.4.1.3.0",
                    "capacidade":  "1.3.6.1.4.1.12148.10.2.4.1.4.0"
                },
                "scale": {"tensao": 0.01, "corrente": 0.1, "temperatura": 1.0, "capacidade": 1.0},
                "capacidade_banco_ah": 400.0
            })
        ),
        (
            "Modbus TCP Genérico",
            "modbus_tcp",
            "Genérico",
            "Qualquer dispositivo Modbus TCP. Ajuste os endereços conforme o mapa do fabricante.",
            json.dumps({
                "port": 502, "unit_id": 1, "timeout": 3.0,
                "capacidade_banco_ah": 200.0,
                "registers": {
                    "tensao":      {"address": 0, "count": 1, "scale": 0.1,  "type": "holding"},
                    "corrente":    {"address": 1, "count": 1, "scale": 0.1,  "type": "holding"},
                    "temperatura": {"address": 2, "count": 1, "scale": 0.1,  "type": "holding"},
                    "capacidade":  {"address": 3, "count": 1, "scale": 1.0,  "type": "holding"},
                    "soh":         {"address": 4, "count": 1, "scale": 1.0,  "type": "holding"}
                }
            })
        ),
        (
            "Moura MSL RS485",
            "modbus_rtu",
            "Moura",
            "Bateria de lítio Moura MSL via RS485. Solicite o mapa de registradores à Moura B2B.",
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
                    "alarme":      {"address": 5, "count": 1, "scale": 1.0,  "type": "holding"}
                }
            })
        ),
        (
            "Simulador",
            "simulator",
            "Interno",
            "Modo offline para desenvolvimento e testes. Não requer hardware.",
            json.dumps({"tensao_base": 54.0, "capacidade_banco_ah": 400.0, "modo_alarme": True})
        ),
    ]
    cursor.executemany(
        "INSERT OR IGNORE INTO driver_profiles (nome, protocolo, fabricante, descricao, driver_config) VALUES (?,?,?,?,?)",
        perfis
    )


# ---------------------------------------------------------------------------
# Driver Manager
# ---------------------------------------------------------------------------

def carregar_sites_do_banco():
    """Carrega sites do banco e inicializa estado em memória."""
    global SITES, telemetria_atual, alarmes_ativos

    conn = db_conn()
    rows = conn.execute("SELECT site_id, nome, ip, protocolo, driver_config FROM sites").fetchall()
    conn.close()

    SITES.clear()
    for r in rows:
        sid = r["site_id"]
        try:
            cfg = json.loads(r["driver_config"] or "{}")
        except Exception:
            cfg = {}

        SITES[sid] = {
            "nome":      r["nome"],
            "ip":        r["ip"],
            "protocolo": r["protocolo"] or "simulator",
            "driver_config": cfg
        }
        if sid not in telemetria_atual:
            telemetria_atual[sid] = Telemetria().to_dict()
        if sid not in alarmes_ativos:
            alarmes_ativos[sid] = Alarme().__dict__


def _criar_driver(site_id: str) -> Optional[BaseDriver]:
    """Instancia o driver correto para um site."""
    site = SITES.get(site_id)
    if not site:
        return None

    # Verifica se o simulador global está ativo
    conn = db_conn()
    row = conn.execute("SELECT valor FROM config WHERE chave='simulador_global'").fetchone()
    conn.close()
    simulador_global = (row["valor"] == "true") if row else False

    protocolo = "simulator" if simulador_global else site.get("protocolo", "simulator")
    DriverClass = get_driver_class(protocolo)

    if DriverClass is None:
        print(f"[DriverManager] Protocolo '{protocolo}' não reconhecido para {site_id}. Usando simulador.")
        DriverClass = get_driver_class("simulator")

    return DriverClass(
        site_id=site_id,
        ip=site["ip"],
        config=site["driver_config"]
    )


async def _init_drivers():
    """Cria e conecta todos os drivers na inicialização."""
    for site_id in list(SITES.keys()):
        driver = _criar_driver(site_id)
        if driver:
            await driver.connect()
            DRIVER_INSTANCES[site_id] = driver
            print(f"[DriverManager] {site_id} → {driver.__class__.__name__} ({SITES[site_id]['ip']})")


async def _reload_driver(site_id: str):
    """Desconecta e recria o driver de um site (após mudança de config)."""
    if site_id in DRIVER_INSTANCES:
        try:
            await DRIVER_INSTANCES[site_id].disconnect()
        except Exception:
            pass
        del DRIVER_INSTANCES[site_id]

    driver = _criar_driver(site_id)
    if driver:
        await driver.connect()
        DRIVER_INSTANCES[site_id] = driver


# ---------------------------------------------------------------------------
# Workers de background
# ---------------------------------------------------------------------------

async def worker_telemetria():
    """Loop principal de leitura de telemetria — usa o driver de cada site."""
    print("📡 Worker de telemetria iniciado.")
    while True:
        for site_id in list(SITES.keys()):
            driver = DRIVER_INSTANCES.get(site_id)
            if not driver:
                continue
            try:
                tel = await driver.read_telemetry()
                telemetria_atual[site_id] = tel.to_dict()
            except Exception as e:
                telemetria_atual[site_id]["status_conexao"] = f"Erro worker: {str(e)[:30]}"
        await asyncio.sleep(2)


async def worker_alarmes():
    """Geração de alarmes — simulador usa proximo_alarme(), outros via traps/polling."""
    print("🚨 Worker de alarmes iniciado.")
    while True:
        await asyncio.sleep(4)
        for site_id in list(SITES.keys()):
            driver = DRIVER_INSTANCES.get(site_id)
            if not driver:
                continue
            # Simulador gera alarmes ciclicamente
            if hasattr(driver, "proximo_alarme"):
                evento, sev, status = driver.proximo_alarme()
                alm = alarmes_ativos.setdefault(site_id, {})
                if alm.get("ultimo_alarme") != evento:
                    alm["ultimo_alarme"] = evento
                    alm["severidade"]   = sev
                    alm["status_painel"] = status
                    salvar_alarme_db(site_id, evento, sev, status)

            # Driver RTU — alarmes embutidos na telemetria (extras.alarmes_bms)
            tel_dict = telemetria_atual.get(site_id, {})
            alarmes_bms = tel_dict.get("alarmes_bms")
            if alarmes_bms:
                evento = ", ".join(alarmes_bms[:2])  # resume os primeiros 2
                alm = alarmes_ativos.setdefault(site_id, {})
                if alm.get("ultimo_alarme") != evento:
                    alm["ultimo_alarme"] = evento
                    alm["severidade"]   = "Crítica"
                    alm["status_painel"] = "Em Alarme"
                    salvar_alarme_db(site_id, evento, "Crítica", "Em Alarme")


async def worker_historico():
    """Salva snapshot de telemetria no banco a cada 60 segundos."""
    print("💾 Worker de histórico iniciado.")
    while True:
        await asyncio.sleep(60)
        agora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        conn = db_conn()
        try:
            for site_id, tel in list(telemetria_atual.items()):
                conn.execute(
                    "INSERT INTO historico (site_id, timestamp, tensao, corrente, temperatura, capacidade) VALUES (?,?,?,?,?,?)",
                    (site_id, agora,
                     tel.get("tensao_barramento", 0),
                     tel.get("corrente_bateria", 0),
                     tel.get("temperatura_bateria", 0),
                     tel.get("capacidade_bateria", 0))
                )
            conn.commit()
        except Exception as e:
            print(f"[HistóricoDB] Erro: {e}")
        finally:
            conn.close()


class SNMPTrapReceiver(asyncio.DatagramProtocol):
    """Escuta traps SNMP na porta 1162 (sem root) para qualquer site."""
    def connection_made(self, transport):
        print("📻 SNMP Trap listener ativo na porta UDP 1162.")

    def datagram_received(self, data, addr):
        ip_origem = addr[0]
        site_id = next(
            (sid for sid, s in SITES.items() if s["ip"] == ip_origem),
            None
        )
        if not site_id:
            return

        pacote = data.decode("ascii", errors="ignore").lower()
        if "rectifier" in pacote or "fail" in pacote:
            evento, sev = "Falha de Retificador detectada", "Crítica"
        elif "mains" in pacote:
            evento, sev = "Falha de Rede (AC)", "Alta"
        else:
            evento, sev = "Evento SNMP Genérico", "Atenção"

        alm = alarmes_ativos.setdefault(site_id, {})
        alm.update({"ultimo_alarme": evento, "severidade": sev, "status_painel": "Em Alarme"})
        salvar_alarme_db(site_id, evento, sev, "Em Alarme")


def salvar_alarme_db(site_id, evento, severidade, status):
    agora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        conn = db_conn()
        conn.execute(
            "INSERT INTO alarmes_historico (site_id, timestamp, evento, severidade, status) VALUES (?,?,?,?,?)",
            (site_id, agora, evento, severidade, status)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[AlarmDB] {e}")


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup():
    init_db()
    carregar_sites_do_banco()
    await _init_drivers()

    asyncio.create_task(worker_telemetria())
    asyncio.create_task(worker_alarmes())
    asyncio.create_task(worker_historico())

    # Trap listener SNMP
    loop = asyncio.get_running_loop()
    try:
        await loop.create_datagram_endpoint(
            lambda: SNMPTrapReceiver(),
            local_addr=("0.0.0.0", 1162)
        )
    except PermissionError:
        print("⚠️  Porta 1162 requer sudo ou ajuste no SO. SNMP Traps desabilitados.")


# ---------------------------------------------------------------------------
# Rotas de API — Sites
# ---------------------------------------------------------------------------

@app.get("/api/sites")
async def get_sites():
    return SITES


class SiteConfig(BaseModel):
    site_id: str
    nome: str
    ip: str
    protocolo: str = "simulator"
    driver_config: dict = {}


@app.post("/api/sites")
async def salvar_site(site: SiteConfig):
    conn = db_conn()
    conn.execute(
        "REPLACE INTO sites (site_id, nome, ip, protocolo, driver_config) VALUES (?,?,?,?,?)",
        (site.site_id, site.nome, site.ip, site.protocolo, json.dumps(site.driver_config))
    )
    conn.commit()
    conn.close()
    carregar_sites_do_banco()
    await _reload_driver(site.site_id)
    return {"status": "sucesso", "protocolo_ativo": site.protocolo}


@app.delete("/api/sites/{site_id}")
async def deletar_site(site_id: str):
    if site_id == "s1":
        raise HTTPException(400, "Não é possível excluir o site matriz (s1).")
    if site_id in DRIVER_INSTANCES:
        await DRIVER_INSTANCES[site_id].disconnect()
        del DRIVER_INSTANCES[site_id]
    conn = db_conn()
    conn.execute("DELETE FROM sites WHERE site_id=?", (site_id,))
    conn.commit()
    conn.close()
    carregar_sites_do_banco()
    return {"status": "sucesso"}


# ---------------------------------------------------------------------------
# Rotas de API — Telemetria e Alarmes
# ---------------------------------------------------------------------------

@app.get("/api/telemetria")
async def get_telemetria(site_id: str = "s1"):
    return telemetria_atual.get(site_id, Telemetria().to_dict())


@app.get("/api/alarmes")
async def get_alarmes(site_id: str = "s1"):
    return alarmes_ativos.get(site_id, Alarme().__dict__)


# ---------------------------------------------------------------------------
# Rotas de API — Configurações Globais (Simulador)
# ---------------------------------------------------------------------------

class SimuladorConfig(BaseModel):
    ativo: bool

@app.get("/api/config/simulador")
async def get_config_simulador():
    conn = db_conn()
    row = conn.execute("SELECT valor FROM config WHERE chave='simulador_global'").fetchone()
    conn.close()
    ativo = (row["valor"] == "true") if row else False
    return {"ativo": ativo}

@app.post("/api/config/simulador")
async def set_config_simulador(cfg: SimuladorConfig):
    conn = db_conn()
    valor = "true" if cfg.ativo else "false"
    conn.execute("REPLACE INTO config (chave, valor) VALUES ('simulador_global', ?)", (valor,))
    conn.execute("DELETE FROM historico")
    conn.execute("DELETE FROM alarmes_historico")
    conn.commit()
    conn.close()
    
    for sid in SITES:
        alarmes_ativos[sid] = Alarme().__dict__
        
    carregar_sites_do_banco()
    for site_id in list(SITES.keys()):
        await _reload_driver(site_id)
        
    return {"status": "sucesso"}


# ---------------------------------------------------------------------------
# Rotas de API — Drivers (Fase 4)
# ---------------------------------------------------------------------------

@app.get("/api/drivers")
async def listar_drivers():
    """Lista todos os protocolos/drivers disponíveis no sistema."""
    return {"drivers": list_drivers()}


@app.get("/api/driver-profiles")
async def listar_profiles():
    """Lista os perfis de driver configuráveis (mapa de registradores/OIDs)."""
    conn = db_conn()
    rows = conn.execute("SELECT * FROM driver_profiles ORDER BY protocolo, nome").fetchall()
    conn.close()
    return {"profiles": [dict(r) for r in rows]}


class DriverProfile(BaseModel):
    nome: str
    protocolo: str
    fabricante: str = ""
    descricao: str = ""
    driver_config: dict = {}


@app.post("/api/driver-profiles")
async def salvar_profile(profile: DriverProfile):
    """Cria ou atualiza um perfil de driver via UI (Fase 4)."""
    conn = db_conn()
    conn.execute(
        """INSERT INTO driver_profiles (nome, protocolo, fabricante, descricao, driver_config)
           VALUES (?,?,?,?,?)
           ON CONFLICT(nome) DO UPDATE SET
             protocolo=excluded.protocolo, fabricante=excluded.fabricante,
             descricao=excluded.descricao, driver_config=excluded.driver_config""",
        (profile.nome, profile.protocolo, profile.fabricante,
         profile.descricao, json.dumps(profile.driver_config))
    )
    conn.commit()
    conn.close()
    return {"status": "sucesso"}


@app.delete("/api/driver-profiles/{profile_id}")
async def deletar_profile(profile_id: int):
    conn = db_conn()
    conn.execute("DELETE FROM driver_profiles WHERE id=?", (profile_id,))
    conn.commit()
    conn.close()
    return {"status": "sucesso"}


@app.get("/api/driver-profiles/{profile_id}/apply/{site_id}")
async def aplicar_profile(profile_id: int, site_id: str):
    """Aplica um perfil de driver a um site específico (Fase 4)."""
    conn = db_conn()
    row = conn.execute("SELECT * FROM driver_profiles WHERE id=?", (profile_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Perfil não encontrado.")
    conn.execute(
        "UPDATE sites SET protocolo=?, driver_config=? WHERE site_id=?",
        (row["protocolo"], row["driver_config"], site_id)
    )
    conn.commit()
    conn.close()
    carregar_sites_do_banco()
    await _reload_driver(site_id)
    return {"status": "sucesso", "perfil": row["nome"], "site": site_id}


# ---------------------------------------------------------------------------
# Rotas de API — Scan de rede
# ---------------------------------------------------------------------------

@app.get("/api/scan")
async def escanear_rede(subnet: str = None):
    import ipaddress, socket
    if not subnet:
        try:
            local_ip = socket.gethostbyname(socket.gethostname())
            subnet = local_ip.rsplit(".", 1)[0] + ".0/24"
        except Exception:
            subnet = "192.168.1.0/24"

    try:
        rede = ipaddress.IPv4Network(subnet, strict=False)
    except ValueError:
        return {"erro": f"Subnet inválida: {subnet}", "dispositivos": []}

    encontrados = []
    OID_SYSDESCR = "1.3.6.1.2.1.1.1.0"
    comunidades = ["public", "private", "eltek", "smartpack"]

    async def probe_snmp(ip_str: str):
        try:
            from pysnmp.hlapi.asyncio import (
                getCmd, CommunityData, UdpTransportTarget,
                ContextData, ObjectType, ObjectIdentity, SnmpEngine
            )
            for com in comunidades:
                errInd, errStat, _, varBinds = await getCmd(
                    SnmpEngine(),
                    CommunityData(com, mpModel=1),
                    UdpTransportTarget((ip_str, 161), timeout=0.8, retries=0),
                    ContextData(),
                    ObjectType(ObjectIdentity(OID_SYSDESCR))
                )
                if not errInd and not errStat:
                    desc = str(varBinds[0][1]) if varBinds else "Dispositivo SNMP"
                    is_eltek = any(k in desc.lower() for k in ["eltek", "smartpack", "rectifier", "delta"])
                    return {"ip": ip_str, "descricao": desc[:80], "comunidade": com,
                            "protocolo": "snmp", "provavel_eltek": is_eltek}
        except Exception:
            pass
        return None

    hosts = list(rede.hosts())[:255]
    resultados = await asyncio.gather(*[probe_snmp(str(ip)) for ip in hosts], return_exceptions=True)
    for r in resultados:
        if r and isinstance(r, dict):
            encontrados.append(r)

    encontrados.sort(key=lambda x: (not x.get("provavel_eltek"), x["ip"]))
    return {"subnet_varrida": str(rede), "total_encontrados": len(encontrados), "dispositivos": encontrados}


# ---------------------------------------------------------------------------
# Rotas de API — Histórico e Reset
# ---------------------------------------------------------------------------

@app.get("/api/historico")
def get_historico(site_id: str = "s1", data_inicio: str = None, data_fim: str = None):
    conn = db_conn()
    q  = "SELECT timestamp, tensao, corrente, temperatura, capacidade FROM historico WHERE site_id=?"
    qa = "SELECT timestamp, evento, severidade, status FROM alarmes_historico WHERE site_id=?"
    p  = [site_id]
    pa = [site_id]
    if data_inicio:
        q  += " AND timestamp >= ?"; p.append(data_inicio.replace("T", " "))
        qa += " AND timestamp >= ?"; pa.append(data_inicio.replace("T", " "))
    if data_fim:
        q  += " AND timestamp <= ?"; p.append(data_fim.replace("T", " "))
        qa += " AND timestamp <= ?"; pa.append(data_fim.replace("T", " "))
    q  += " ORDER BY timestamp ASC"
    qa += " ORDER BY timestamp DESC"

    telemetria = [dict(r) for r in conn.execute(q, p).fetchall()]
    alarmes    = [dict(r) for r in conn.execute(qa, pa).fetchall()]
    conn.close()
    return {"telemetria": telemetria, "alarmes": alarmes}


@app.post("/api/reset")
async def resetar_banco():
    conn = db_conn()
    conn.execute("DELETE FROM historico")
    conn.execute("DELETE FROM alarmes_historico")
    try:
        conn.execute("DELETE FROM sqlite_sequence WHERE name='historico'")
        conn.execute("DELETE FROM sqlite_sequence WHERE name='alarmes_historico'")
    except Exception:
        pass
    conn.commit()
    conn.close()
    for sid in SITES:
        alarmes_ativos[sid] = Alarme().__dict__
    return {"status": "sucesso"}


# ---------------------------------------------------------------------------
# Geração de laudo PDF (preservada e melhorada)
# ---------------------------------------------------------------------------

@app.get("/api/relatorio")
def gerar_relatorio_pdf(site_id: str = "s1", data_inicio: str = None, data_fim: str = None):
    try:
        from fpdf import FPDF
    except ImportError:
        return {"erro": "Instale fpdf: pip install fpdf"}

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        has_mpl = True
    except ImportError:
        has_mpl = False

    conn = db_conn()
    q  = "SELECT timestamp, tensao, corrente, temperatura, capacidade FROM historico WHERE site_id=?"
    qa = "SELECT timestamp, evento, severidade, status FROM alarmes_historico WHERE site_id=?"
    p, pa = [site_id], [site_id]
    if data_inicio:
        q  += " AND timestamp >= ?"; p.append(data_inicio.replace("T", " "))
        qa += " AND timestamp >= ?"; pa.append(data_inicio.replace("T", " "))
    if data_fim:
        q  += " AND timestamp <= ?"; p.append(data_fim.replace("T", " "))
        qa += " AND timestamp <= ?"; pa.append(data_fim.replace("T", " "))
    dados         = conn.execute(q, p).fetchall()
    dados_alarmes = conn.execute(qa, pa).fetchall()
    conn.close()

    site_info  = SITES.get(site_id, {})
    nome_site  = site_info.get("nome", "Desconhecido")
    protocolo  = site_info.get("protocolo", "N/A").upper()

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 10, "LAUDO TÉCNICO DE ENERGIA E TELEMETRIA", ln=True, align="C")
    pdf.set_font("Arial", "B", 12)
    pdf.cell(0, 8, f"SISTEMA: {nome_site.upper()} | PROTOCOLO: {protocolo}", ln=True, align="C")
    pdf.ln(4)

    pdf.set_font("Arial", "B", 10)
    pdf.cell(0, 6, "1. IDENTIFICAÇÃO E RESPONSABILIDADE TÉCNICA", ln=True)
    pdf.set_font("Arial", size=10)
    pdf.multi_cell(0, 6,
        "Responsável Técnico: [NOME DO TÉCNICO / ENGENHEIRO]\n"
        "Registro (CREA/CRT): [000000000-0]\n"
        f"Protocolo de Comunicação: {protocolo}\n"
        f"Data de Emissão: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}"
    )
    pdf.ln(4)

    pdf.set_font("Arial", "B", 10)
    pdf.cell(0, 6, "2. RESUMO ANALÍTICO DE TELEMETRIA", ln=True)
    pdf.set_font("Arial", size=10)
    temps = []
    if not dados:
        pdf.cell(0, 6, "Nenhum registro encontrado para os filtros aplicados.", ln=True)
    else:
        tensoes   = [d[1] for d in dados]
        correntes = [d[2] for d in dados]
        temps     = [d[3] for d in dados]
        pdf.multi_cell(0, 6,
            f"Amostras analisadas: {len(dados)}\n"
            f"Tensão VCC: Mín {min(tensoes):.2f}V | Máx {max(tensoes):.2f}V | Média {sum(tensoes)/len(tensoes):.2f}V\n"
            f"Corrente de pico: {max(correntes):.2f}A\n"
            f"Temperatura de pico: {max(temps):.1f}°C"
        )
        if has_mpl:
            datas = [d[0].split(" ")[1] for d in dados]
            plt.figure(figsize=(10, 3))
            plt.plot(datas, tensoes, color="#2196F3", linewidth=1.5, label="Tensão VCC (V)")
            plt.title("Estabilidade do Barramento DC", fontsize=10)
            plt.ylabel("Volts (V)")
            plt.legend(); plt.grid(True, linestyle="--", alpha=0.5)
            step = max(1, len(datas) // 10)
            plt.xticks(datas[::step], rotation=30, fontsize=8)
            plt.tight_layout()
            plt.savefig("_temp_chart.png"); plt.close()
            pdf.ln(2); pdf.image("_temp_chart.png", w=180)
            os.remove("_temp_chart.png")

    pdf.add_page()
    pdf.set_font("Arial", "B", 10)
    pdf.cell(0, 6, "3. ANÁLISE CRÍTICA DE ALARMES", ln=True)
    pdf.set_font("Arial", size=10)

    falha_ret  = any("Retificador" in str(a[1]) for a in dados_alarmes)
    falha_rede = any("Rede" in str(a[1]) for a in dados_alarmes)
    falha_disj = any("Disjuntor" in str(a[1]) for a in dados_alarmes)

    texto = ""
    if falha_ret or falha_rede:
        texto += "- Evento de falha de retificação/rede AC identificado. Impacto no MTBF.\n\n"
    if falha_disj:
        texto += "- Disjuntor de bateria aberto detectado. Risco de perda de redundância.\n\n"
    if temps and max(temps) > 25.0:
        texto += f"- Temperatura de pico {max(temps):.1f}°C acima do recomendado (≤25°C para VRLA).\n\n"
    if not texto:
        texto = "Nenhuma anomalia crítica detectada no período analisado.\n"
    pdf.multi_cell(0, 6, texto)

    parecer = "APTO"
    if (temps and max(temps) > 25.0) or falha_ret or falha_rede:
        parecer = "APTO COM RESTRIÇÕES"
    if falha_disj or any(a[2] == "Crítica" for a in dados_alarmes):
        parecer = "INAPTO — RISCO OPERACIONAL IMINENTE"

    pdf.ln(4)
    pdf.set_font("Arial", "B", 11)
    pdf.cell(0, 6, f"PARECER: {parecer}", ln=True)

    pdf.add_page()
    pdf.set_font("Arial", "B", 10)
    pdf.cell(0, 6, "ANEXO I — LOG DE ALARMES", ln=True)
    pdf.set_font("Arial", "B", 8)
    for lbl, w in [("Data/Hora", 38), ("Evento", 85), ("Severidade", 30), ("Status", 30)]:
        pdf.cell(w, 6, lbl, border=1, align="C")
    pdf.ln()
    pdf.set_font("Arial", size=8)
    for a in dados_alarmes:
        pdf.cell(38, 6, str(a[0]), border=1, align="C")
        pdf.cell(85, 6, str(a[1])[:45], border=1, align="C")
        pdf.cell(30, 6, str(a[2]), border=1, align="C")
        pdf.cell(30, 6, str(a[3]), border=1, align="C")
        pdf.ln()

    path = f"laudo_{site_id}.pdf"
    pdf.output(path)
    return FileResponse(path, media_type="application/pdf",
                        filename=f"laudo_{site_id}.pdf",
                        headers={"Content-Disposition": f"attachment; filename=laudo_{site_id}.pdf"})


# ---------------------------------------------------------------------------
# Servir frontend
# ---------------------------------------------------------------------------

@app.get("/")
async def painel():
    base = os.path.dirname(os.path.abspath(__file__))
    return FileResponse(os.path.join(base, "index.html"))


@app.get("/relatorio-web")
async def relatorio_web():
    base = os.path.dirname(os.path.abspath(__file__))
    return FileResponse(os.path.join(base, "dashboard_laudo.html"))