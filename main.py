import asyncio
import random
import sqlite3
import os
from datetime import datetime
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
from pysnmp.hlapi.asyncio import *

app = FastAPI(title="Tester_Smartpack", description="API de Integração com Eltek Smartpack S")

# --- DICIONÁRIO MULTISITE ---
SITES = {}
telemetria_atual = {}
alarmes_ativos = {}

def carregar_sites_do_banco():
    global SITES, telemetria_atual, alarmes_ativos
    conn = sqlite3.connect('telemetria.db')
    cursor = conn.cursor()
    cursor.execute("SELECT site_id, nome, ip FROM sites")
    rows = cursor.fetchall()
    
    SITES.clear()
    for r in rows:
        sid, nome, ip = r
        SITES[sid] = {"nome": nome, "ip": ip}
        if sid not in telemetria_atual:
            telemetria_atual[sid] = {"tensao_barramento": 0.0, "corrente_bateria": 0.0, "temperatura_bateria": 0.0, "capacidade_bateria": 0.0, "autonomia_estimada": "Calculando...", "status_conexao": "Desconectado"}
        if sid not in alarmes_ativos:
            alarmes_ativos[sid] = {"ultimo_alarme": "Nenhum evento registrado", "status_painel": "Normal", "severidade": "Baixa"}
    
    conn.close()

# --- CONFIGURAÇÃO DE AMBIENTE ---
SIMULAR_MODULO = True  # Mude para False quando for conectar no painel real

ELTEK_IP = "192.168.10.20"  # IP Padrão de fábrica do Smartpack S
SNMP_PORT = 161
SNMP_COMMUNITY = "sua_senha_aqui"  # Comunidade de leitura (verifique no painel)
CAPACIDADE_BANCO_AH = 400.0  # Configuração: Capacidade total do banco de baterias em Ampere-hora

# OIDs SNMP (Substitua estes OIDs de exemplo pelos numéricos exatos do seu arquivo MIB)
OID_TENSAO = '1.3.6.1.4.1.12148.10.2.4.1.1.0' 
OID_CORRENTE = '1.3.6.1.4.1.12148.10.2.4.1.2.0'
OID_TEMPERATURA = '1.3.6.1.4.1.12148.10.2.4.1.3.0'
OID_CAPACIDADE = '1.3.6.1.4.1.12148.10.2.4.1.4.0'

async def ler_dados_snmp():
    """Worker do SNMP GET (Telemetria)"""
    if SIMULAR_MODULO:
        print("📡 Modo SIMULADOR SNMP GET ativado!")
        while True:
            for site_id in list(SITES.keys()):
                tel = telemetria_atual.get(site_id)
                alm = alarmes_ativos.get(site_id)
                if not tel or not alm: continue
                tel["status_conexao"] = "Simulador Online"
                tel["corrente_bateria"] = round(random.uniform(10.0, 35.5), 2)
                tel["temperatura_bateria"] = round(random.uniform(22.0, 26.5), 1)
                
                if alm.get("ultimo_alarme") == "Falha de Rede (AC)":
                    tel["tensao_barramento"] = round(random.uniform(47.0, 50.5), 2)
                    tel["capacidade_bateria"] = max(0.0, tel["capacidade_bateria"] - 0.5)
                else:
                    base_v = 53.5 if site_id == "s1" else 54.0
                    tel["tensao_barramento"] = round(random.uniform(base_v - 0.3, base_v + 0.2), 2)
                    tel["capacidade_bateria"] = min(100.0, tel["capacidade_bateria"] + 0.5)
                    
                carga_ah = CAPACIDADE_BANCO_AH * (tel["capacidade_bateria"] / 100.0)
                if tel["tensao_barramento"] < 51.0 and tel["corrente_bateria"] > 0:
                    horas = carga_ah / tel["corrente_bateria"]
                    tel["autonomia_estimada"] = f"{round(horas, 1)} Horas"
                else:
                    tel["autonomia_estimada"] = "AC Normal (Flutuação)"

            await asyncio.sleep(2)
        return

    snmp_engine = SnmpEngine()
    
    while True:
        for site_id, config in list(SITES.items()):
            ip = config["ip"]
            tel = telemetria_atual.get(site_id, {})
            alm = alarmes_ativos.get(site_id, {})
            status_anterior = tel.get("status_conexao", "Desconectado")
            try:
                errorIndication, errorStatus, errorIndex, varBinds = await getCmd(
                    snmp_engine,
                    CommunityData(SNMP_COMMUNITY, mpModel=1),
                    UdpTransportTarget((ip, SNMP_PORT), timeout=1.5, retries=1),
                    ContextData(),
                    ObjectType(ObjectIdentity(OID_TENSAO)),
                    ObjectType(ObjectIdentity(OID_CORRENTE)),
                    ObjectType(ObjectIdentity(OID_TEMPERATURA)),
                    ObjectType(ObjectIdentity(OID_CAPACIDADE))
                )

                if errorIndication or errorStatus:
                    novo_status = "Falha de Conexão" if errorIndication else "Erro de Leitura"
                    tel["status_conexao"] = novo_status
                    tel["tensao_barramento"] = 0.0
                    tel["corrente_bateria"] = 0.0
                    tel["temperatura_bateria"] = 0.0
                    tel["capacidade_bateria"] = 0.0
                    tel["autonomia_estimada"] = "--"
                    
                    if status_anterior == "Online" or status_anterior == "Desconectado":
                        evento = f"Perda de Comunicação SNMP ({novo_status})"
                        alm["ultimo_alarme"] = evento
                        alm["severidade"] = "Crítica"
                        alm["status_painel"] = "Inacessível"
                        salvar_alarme_db(site_id, evento, "Crítica", "Inacessível")
                else:
                    tel["status_conexao"] = "Online"
                    if status_anterior != "Online" and status_anterior != "Desconectado":
                        evento = "Comunicação Restabelecida"
                        alm["ultimo_alarme"] = evento
                        alm["severidade"] = "Baixa"
                        alm["status_painel"] = "Normal"
                        salvar_alarme_db(site_id, evento, "Baixa", "Normal")
                    valores = [v[1] for v in varBinds]
                    
                    tel["tensao_barramento"] = round(float(valores[0]) / 100.0, 2)
                    tel["corrente_bateria"] = round(float(valores[1]) / 10.0, 2)
                    tel["temperatura_bateria"] = round(float(valores[2]), 1)
                    tel["capacidade_bateria"] = round(float(valores[3]), 1)
                    
                    carga_ah = CAPACIDADE_BANCO_AH * (tel["capacidade_bateria"] / 100.0)
                    if tel["tensao_barramento"] < 51.0 and tel["corrente_bateria"] > 0:
                        tel["autonomia_estimada"] = f"{round(carga_ah / tel['corrente_bateria'], 1)} Horas"
                    else:
                        tel["autonomia_estimada"] = "AC Normal"
            except Exception as e:
                tel["status_conexao"] = "Erro Crítico"
                tel["tensao_barramento"] = 0.0
                tel["corrente_bateria"] = 0.0
                tel["temperatura_bateria"] = 0.0
                tel["capacidade_bateria"] = 0.0
                tel["autonomia_estimada"] = "--"
                
        # Aguarda 2 segundos antes de sondar o painel novamente
        await asyncio.sleep(2)

class SNMPTrapReceiver(asyncio.DatagramProtocol):
    """
    Worker do SNMP (Alarmes).
    Fica escutando a rede. Se o painel disparar uma falha, ela cai aqui.
    """
    def connection_made(self, transport):
        self.transport = transport
        print("Servidor SNMP Trap escutando na porta UDP 1162...")

    def datagram_received(self, data, addr):
        # Quando um pacote UDP chega do IP do painel, disparamos o alarme
        ip_origem = addr[0]
        site_detectado = "s1"
        for sid, conf in SITES.items():
            if conf["ip"] == ip_origem:
                site_detectado = sid
                break
        
        print(f"⚠️ Trap SNMP recebido de {site_detectado} ({ip_origem})!")
        
        # Extraindo texto visível dentro do pacote SNMP bruto
        pacote_bruto = data.decode('ascii', errors='ignore').lower()
        
        if "rectifier" in pacote_bruto or "fail" in pacote_bruto:
            evento = "Falha de Retificador detectada"
            sev = "Crítica"
        elif "mains" in pacote_bruto:
            evento = "Falha de Rede (AC)"
            sev = "Alta"
        else:
            evento = "Evento SNMP Genérico"
            sev = "Atenção"

        alm = alarmes_ativos[site_detectado]
        alm["ultimo_alarme"] = evento
        alm["severidade"] = sev
        alm["status_painel"] = "Em Alarme"
        salvar_alarme_db(site_detectado, evento, sev, "Em Alarme")

def salvar_alarme_db(site_id, evento, severidade, status):
    """Salva um novo evento de alarme no banco de dados local"""
    try:
        agora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        conn = sqlite3.connect('telemetria.db')
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO alarmes_historico (site_id, timestamp, evento, severidade, status)
            VALUES (?, ?, ?, ?, ?)
        ''', (site_id, agora, evento, severidade, status))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Erro ao salvar alarme no SQLite: {e}")

async def simular_alarmes():
    """Worker para gerar alarmes no modo SIMULADOR"""
    print("🚨 Modo SIMULADOR de Alarmes ativado!")
    eventos = [
        ("Nenhum evento registrado", "Baixa", "Normal"),
        ("Falha de Retificador detectada", "Crítica", "Em Alarme"),
        ("Falha de Rede (AC)", "Alta", "Em Alarme"),
        ("Temperatura da Bateria Alta", "Atenção", "Em Alarme"),
        ("Disjuntor de Bateria Aberto", "Crítica", "Em Alarme"),
        ("Tensão de Barramento Baixa", "Alta", "Em Alarme"),
        ("Falha no Teste de Bateria", "Atenção", "Em Alarme"),
        ("Sobretensão no Retificador", "Alta", "Em Alarme"),
        ("Porta do Gabinete Aberta", "Baixa", "Atenção")
    ]
    
    indices = {}
    while True:
        await asyncio.sleep(4) 
        
        if not SIMULAR_MODULO:
            continue
        
        for site_id in list(SITES.keys()):
            if site_id not in indices: indices[site_id] = 0
            idx = indices.get(site_id, 0)
            evento = eventos[idx]
            
            alm = alarmes_ativos[site_id]
            if alm["ultimo_alarme"] != evento[0]:
                alm["ultimo_alarme"] = evento[0]
                alm["severidade"] = evento[1]
                alm["status_painel"] = evento[2]
                salvar_alarme_db(site_id, evento[0], evento[1], evento[2])
            
            indices[site_id] = (idx + 1) % len(eventos)

def init_db():
    """Cria o banco de dados SQLite e a tabela caso não existam"""
    global SIMULAR_MODULO
    conn = sqlite3.connect('telemetria.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS historico (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_id TEXT DEFAULT 's1',
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            tensao REAL,
            corrente REAL,
            temperatura REAL,
            capacidade REAL
        )
    ''')
    
    # Tabela de Configuração de Rede e Dispositivos
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sites (
            site_id TEXT PRIMARY KEY,
            nome TEXT,
            ip TEXT
        )
    ''')
    
    # Nova tabela para o Histórico de Alarmes
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS alarmes_historico (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_id TEXT DEFAULT 's1',
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            evento TEXT,
            severidade TEXT,
            status TEXT
        )
    ''')
    
    # Tabela de Configurações Globais
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS config (
            chave TEXT PRIMARY KEY,
            valor TEXT
        )
    ''')
    cursor.execute("SELECT valor FROM config WHERE chave='SIMULAR_MODULO'")
    row = cursor.fetchone()
    if row:
        SIMULAR_MODULO = (row[0] == 'true')
    else:
        cursor.execute("INSERT INTO config (chave, valor) VALUES ('SIMULAR_MODULO', 'true')")
    
    # Migração segura para quem já criou o banco sem o site_id
    try:
        cursor.execute("ALTER TABLE historico ADD COLUMN site_id TEXT DEFAULT 's1'")
    except sqlite3.OperationalError: pass
    try:
        cursor.execute("ALTER TABLE alarmes_historico ADD COLUMN site_id TEXT DEFAULT 's1'")
    except sqlite3.OperationalError: pass
    
    # Cadastra o site inicial padrão caso a tabela esteja vazia
    cursor.execute("SELECT COUNT(*) FROM sites")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO sites (site_id, nome, ip) VALUES ('s1', 'Data Center Alpha (Matriz)', '192.168.10.20')")
        cursor.execute("INSERT INTO sites (site_id, nome, ip) VALUES ('s2', 'Site Beta (Filial)', '192.168.10.21')")
        
    conn.commit()
    conn.close()
    carregar_sites_do_banco()

async def salvar_historico_db():
    """Worker para salvar dados no banco a cada 60 segundos"""
    print("💾 BD Worker ativado: Salvando telemetria a cada 1 minuto...")
    while True:
        await asyncio.sleep(60) # Espera 60 segundos
        try:
            agora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            conn = sqlite3.connect('telemetria.db')
            cursor = conn.cursor()
            for site_id, tel in list(telemetria_atual.items()):
                cursor.execute('''
                    INSERT INTO historico (site_id, timestamp, tensao, corrente, temperatura, capacidade)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (
                    site_id,
                    agora,
                    tel["tensao_barramento"],
                    tel["corrente_bateria"],
                    tel["temperatura_bateria"],
                    tel["capacidade_bateria"]
                ))
            conn.commit()
            conn.close()
            print("💾 Histórico salvo no BD com sucesso.")
        except Exception as e:
            print(f"Erro ao salvar no SQLite: {e}")

@app.on_event("startup")
async def iniciar_background_tasks():
    """Liga os dois motores em paralelo assim que a API sobe"""
    # Inicializa o banco e liga o worker de gravação
    init_db()
    asyncio.create_task(salvar_historico_db())
    
    # Liga os leitores e simuladores (eles checam o modo SIMULAR_MODULO internamente a cada passo)
    asyncio.create_task(ler_dados_snmp())
    asyncio.create_task(simular_alarmes())
    
    # Abre a porta UDP 1162 incondicionalmente para escutar os Traps SNMP reais de imediato
    loop = asyncio.get_running_loop()
    await loop.create_datagram_endpoint(
        lambda: SNMPTrapReceiver(),
        local_addr=('0.0.0.0', 1162)
    )

@app.get("/api/sites")
async def get_sites():
    return SITES

class SimuladorConfig(BaseModel):
    ativo: bool

@app.get("/api/config/simulador")
def get_simulador():
    return {"ativo": SIMULAR_MODULO}

@app.post("/api/config/simulador")
async def set_simulador(cfg: SimuladorConfig):
    global SIMULAR_MODULO
    try:
        modo_anterior = SIMULAR_MODULO
        novo_modo = cfg.ativo
        
        conn = sqlite3.connect('telemetria.db', timeout=10)
        cursor = conn.cursor()
        cursor.execute("REPLACE INTO config (chave, valor) VALUES ('SIMULAR_MODULO', ?)", ('true' if novo_modo else 'false',))
        
        # Zera o banco de dados e as variáveis se houver mudança de modo
        if modo_anterior != novo_modo:
            try:
                cursor.execute("DELETE FROM historico")
                cursor.execute("DELETE FROM alarmes_historico")
                cursor.execute("DELETE FROM sqlite_sequence WHERE name='historico'")
                cursor.execute("DELETE FROM sqlite_sequence WHERE name='alarmes_historico'")
            except Exception: pass
                
            for sid in list(SITES.keys()):
                if sid in telemetria_atual:
                    telemetria_atual[sid].update({
                        "tensao_barramento": 0.0, "corrente_bateria": 0.0,
                        "temperatura_bateria": 0.0, "capacidade_bateria": 0.0,
                        "autonomia_estimada": "Aguardando Leitura...", "status_conexao": "Conectando..." if not novo_modo else "Simulador Online"
                    })
                if sid in alarmes_ativos:
                    alarmes_ativos[sid].update({
                        "ultimo_alarme": "Aguardando rede..." if not novo_modo else "Nenhum evento registrado", 
                        "status_painel": "Desconhecido" if not novo_modo else "Normal", 
                        "severidade": "Baixa"
                    })
                    
        conn.commit()
        conn.close()
        
        SIMULAR_MODULO = novo_modo
        return {"status": "sucesso", "ativo": SIMULAR_MODULO}
    except Exception as e:
        return {"status": "erro", "mensagem": str(e)}

class SiteConfig(BaseModel):
    site_id: str
    nome: str
    ip: str

@app.post("/api/sites")
async def salvar_site(site: SiteConfig):
    conn = sqlite3.connect('telemetria.db')
    cursor = conn.cursor()
    cursor.execute("REPLACE INTO sites (site_id, nome, ip) VALUES (?, ?, ?)", (site.site_id, site.nome, site.ip))
    conn.commit()
    conn.close()
    carregar_sites_do_banco()
    return {"status": "sucesso"}

@app.delete("/api/sites/{site_id}")
async def deletar_site(site_id: str):
    if site_id == "s1":
        return {"status": "erro", "mensagem": "Não é possível excluir o site matriz (s1)."}
    conn = sqlite3.connect('telemetria.db')
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sites WHERE site_id=?", (site_id,))
    conn.commit()
    conn.close()
    carregar_sites_do_banco()
    return {"status": "sucesso"}

@app.get("/api/telemetria")
async def get_telemetria(site_id: str = "s1"):
    return telemetria_atual.get(site_id, telemetria_atual.get("s1", {"status_conexao": "Desconectado"}))

# --- NOVA ROTA PARA O APLICATIVO ---
@app.get("/api/alarmes")
async def get_alarmes(site_id: str = "s1"):
    return alarmes_ativos.get(site_id, alarmes_ativos.get("s1", {"ultimo_alarme": "Nenhum", "severidade": "Baixa", "status_painel": "Normal"}))

# --- ROTA PARA ZERAR O BANCO DE DADOS ---
@app.post("/api/reset")
async def resetar_banco():
    """Limpa todo o histórico de telemetria e alarmes do banco de dados"""
    try:
        conn = sqlite3.connect('telemetria.db')
        cursor = conn.cursor()
        cursor.execute("DELETE FROM historico")
        cursor.execute("DELETE FROM alarmes_historico")
        cursor.execute("DELETE FROM sqlite_sequence WHERE name='historico'")
        cursor.execute("DELETE FROM sqlite_sequence WHERE name='alarmes_historico'")
        conn.commit()
        conn.close()
        
        # Reseta a memória local para sumir o alarme atual da tela
        for sid in SITES:
            alarmes_ativos[sid] = {
                "ultimo_alarme": "Nenhum evento registrado",
                "status_painel": "Normal",
                "severidade": "Baixa"
            }
        return {"status": "sucesso"}
    except Exception as e:
        return {"status": "erro", "mensagem": str(e)}

# --- ROTA PARA GERAÇÃO DO LAUDO PDF ---
@app.get("/api/relatorio")
def gerar_relatorio_pdf(site_id: str = "s1", data_inicio: str = None, data_fim: str = None):
    """Gera um PDF com o histórico e análises baseado nos filtros de data"""
    try:
        from fpdf import FPDF
    except ImportError:
        return {"erro": "A biblioteca FPDF não está instalada. Rode: pip install fpdf"}

    try:
        import matplotlib
        matplotlib.use('Agg')  # Backend sem display — obrigatório em servidor headless
        import matplotlib.pyplot as plt
        has_matplotlib = True
    except ImportError:
        has_matplotlib = False

    conn = sqlite3.connect('telemetria.db')
    cursor = conn.cursor()
    
    query = "SELECT timestamp, tensao, corrente, temperatura, capacidade FROM historico WHERE site_id=?"
    params = [site_id]
    
    # Aplica os filtros se existirem (formato de entrada do html: YYYY-MM-DDTHH:MM)
    if data_inicio:
        query += " AND timestamp >= ?"
        params.append(data_inicio.replace("T", " "))
    if data_fim:
        query += " AND timestamp <= ?"
        params.append(data_fim.replace("T", " "))
        
    cursor.execute(query, params)
    dados = cursor.fetchall()
    
    # --- Query dos Alarmes ---
    query_al = "SELECT timestamp, evento, severidade, status FROM alarmes_historico WHERE site_id=?"
    params_al = [site_id]
    if data_inicio:
        query_al += " AND timestamp >= ?"
        params_al.append(data_inicio.replace("T", " "))
    if data_fim:
        query_al += " AND timestamp <= ?"
        params_al.append(data_fim.replace("T", " "))
    cursor.execute(query_al, params_al)
    dados_alarmes = cursor.fetchall()
    
    conn.close()

    # Inicializa o PDF
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    
    # Cabeçalho
    nome_site = SITES.get(site_id, {}).get("nome", "Desconhecido")
    pdf.set_font("Arial", 'B', 16)
    pdf.cell(0, 10, txt="LAUDO TÉCNICO DE ENERGIA E TELEMETRIA", ln=True, align='C')
    pdf.set_font("Arial", 'B', 12)
    pdf.cell(0, 10, txt=f"SISTEMA RETIFICADOR - {nome_site.upper()}", ln=True, align='C')
    pdf.ln(5)
    
    # 1. Identificação e Responsabilidade Técnica
    pdf.set_font("Arial", 'B', 10)
    pdf.cell(0, 6, txt="1. IDENTIFICAÇÃO E RESPONSABILIDADE TÉCNICA", ln=True)
    pdf.set_font("Arial", size=10)
    pdf.multi_cell(0, 6, txt=(
        "Responsável Técnico: [NOME DO SEU TÉCNICO / ENGENHEIRO]\n"
        "Título: Técnico em Eletrotécnica / Graduando em Engenharia Elétrica\n"
        "Registro (CREA/CRT): [000000000-0]\n"
        "ART/TRT Vinculada: [NÚMERO DA ART OU TRT]\n\n"
        "Equipamento Analisado: Controladora Eltek Smartpack S (Sistema DC)\n"
        "Fabricante: Eltek / Delta\n"
        "Número de Série do Chassi: [PREENCHER S/N]\n"
        "Localização Física: Data Center Principal - Site Aether Grid\n"
        f"Data da Emissão do Laudo: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}"
    ))
    pdf.ln(5)
    
    # 2. Objetivo e Metodologia
    pdf.set_font("Arial", 'B', 10)
    pdf.cell(0, 6, txt="2. OBJETIVO E METODOLOGIA", ln=True)
    pdf.set_font("Arial", size=10)
    pdf.multi_cell(0, 6, txt=(
        "Objetivo: Análise de falhas após evento crítico e avaliação preditiva/preventiva da infraestrutura "
        "de energia ininterrupta em corrente contínua do Data Center.\n\n"
        "Metodologia: Os dados foram extraídos do histórico automatizado do equipamento via protocolo SNMP "
        "(Simple Network Management Protocol). Foram analisados OIDs de telemetria contínua e traps de falha."
    ))
    pdf.ln(5)

    # 3. Referências Normativas
    pdf.set_font("Arial", 'B', 10)
    pdf.cell(0, 6, txt="3. REFERÊNCIAS NORMATIVAS", ln=True)
    pdf.set_font("Arial", size=10)
    pdf.multi_cell(0, 6, txt=(
        "- ABNT NBR 5410: Instalações elétricas de baixa tensão.\n"
        "- ABNT NBR 14565: Cabeamento estruturado para edifícios comerciais e data centers.\n"
        "- Manuais Técnicos de Operação e Manutenção do Fabricante (Eltek)."
    ))
    pdf.ln(5)
    
    # 4. Resumo Analítico
    pdf.set_font("Arial", 'B', 10)
    pdf.cell(0, 6, txt="4. RESUMO ANALÍTICO DE TELEMETRIA", ln=True)
    pdf.set_font("Arial", size=10)
    temps = []
    if not dados:
        pdf.cell(0, 6, txt="Nenhum registro de telemetria encontrado para os filtros aplicados.", ln=True)
    else:
        tensoes = [d[1] for d in dados]
        correntes = [d[2] for d in dados]
        temps = [d[3] for d in dados]
        
        pdf.multi_cell(0, 6, txt=(
            f"Amostras Analisadas no Período: {len(dados)}\n"
            f"Tensão de Operação em Corrente Contínua (VCC): Mín {min(tensoes):.2f}V | Máx {max(tensoes):.2f}V | Média {(sum(tensoes)/len(tensoes)):.2f}V\n"
            f"Corrente de Carga/Descarga dos Elementos Acumuladores: Pico Registrado de {max(correntes):.2f}A\n"
            f"Temperatura do Banco de Baterias VRLA: Pico Registrado de {max(temps):.1f}°C"
        ))
        
        # Renderização do Gráfico
        if has_matplotlib:
            datas_graf = [d[0].split(' ')[1] for d in dados] # Obtém apenas o horário
            plt.figure(figsize=(10, 4))
            plt.plot(datas_graf, tensoes, label='Tensão VCC (V)', color='#2196F3', linewidth=1.5)
            plt.title("Estabilidade do Barramento DC no Período", fontsize=10)
            plt.ylabel("Volts (V)")
            plt.legend()
            plt.grid(True, linestyle='--', alpha=0.5)
            # Controla a poluição do eixo X
            if len(datas_graf) > 10:
                plt.xticks(datas_graf[::len(datas_graf)//10], rotation=30, fontsize=8)
            else:
                plt.xticks(rotation=30, fontsize=8)
            plt.tight_layout()
            plt.savefig("temp_chart.png")
            plt.close()
            
            pdf.ln(2)
            pdf.image("temp_chart.png", w=180)
            os.remove("temp_chart.png")
    
    pdf.add_page()

    # 5. Análise Crítica dos Dados
    pdf.set_font("Arial", 'B', 10)
    pdf.cell(0, 6, txt="5. ANÁLISE CRÍTICA DOS EVENTOS E ALARMES", ln=True)
    pdf.set_font("Arial", size=10)
    
    texto_analise = "Durante a extração do log operacional, foi identificado o seguinte cenário técnico:\n\n"
    
    # Deteccão automática de correlações
    falha_retificador = any("Retificador" in str(al[1]) for al in dados_alarmes)
    falha_rede = any("Rede" in str(al[1]) for al in dados_alarmes)
    falha_disjuntor = any("Disjuntor" in str(al[1]) for al in dados_alarmes)
    
    if falha_retificador or falha_rede:
        texto_analise += "- Correlação de Eventos e MTBF: A telemetria apontou eventos atrelados a falha no estágio de retificação ou rede AC. Quando a falha no retificador ocorre, ele é incapacitado de realizar a conversão da energia alternada, forçando o barramento a interromper a tensão de flutuação e assumir o fornecimento de energia DC estritamente pelos elementos acumuladores. A repetição dessa anomalia impacta agressivamente o MTBF (Tempo Médio Entre Falhas) da operação de missão crítica do Data Center.\n\n"
        
    if falha_disjuntor:
        texto_analise += "- Severidade Crítica (Proteções): Foi identificado um log de 'Disjuntor de Bateria Aberto'. Tal comportamento isola parte da redundância energética do barramento, deixando a carga exposta à queda total em caso de interrupção da rede elétrica (Utility).\n\n"
        
    if temps and max(temps) > 25.0:
        texto_analise += f"- Desvio de Temperatura: Os sensores reportaram uma máxima de {max(temps):.1f}°C. De acordo com as diretrizes de projeto, operações acima de 25°C em tecnologias VRLA aceleram reações de corrosão interna, diminuindo exponencialmente a vida útil projetada e aumentando os riscos de degradação térmica.\n\n"
    else:
        texto_analise += "- Térmica Controlada: O perfil de temperatura manteve-se nos parâmetros aceitáveis (≤ 25°C), fator crucial para assegurar a autonomia e integridade físico-química dos blocos de baterias.\n\n"
        
    pdf.multi_cell(0, 6, txt=texto_analise)
    pdf.ln(5)

    # 6. Conclusão e Plano de Ação
    pdf.set_font("Arial", 'B', 10)
    pdf.cell(0, 6, txt="6. PARECER TÉCNICO E PLANO DE AÇÃO", ln=True)
    
    # Inferência do status
    parecer = "APTO"
    if (temps and max(temps) > 25.0) or falha_retificador or falha_rede:
        parecer = "APTO COM RESTRIÇÕES (Requer Manutenção Preditiva)"
    if falha_disjuntor or any(al[2] == "Crítica" for al in dados_alarmes):
        parecer = "INAPTO (Risco Iminente Operacional)"
        
    pdf.set_font("Arial", 'B', 11)
    pdf.cell(0, 6, txt=f"Status da Infraestrutura Analisada: {parecer}", ln=True)
    pdf.ln(2)
    
    pdf.set_font("Arial", size=10)
    texto_plano = "Com base nas análises efetuadas, recomendo a execução do seguinte plano de ação corretiva:\n"
    if falha_retificador:
        texto_plano += " - Substituição ou reparo em bancada dos módulos retificadores que acusaram indisponibilidade.\n"
    if falha_disjuntor:
        texto_plano += " - Restabelecimento imediato e rearme físico dos disjuntores seccionadores de bateria.\n"
    if temps and max(temps) > 25.0:
        texto_plano += " - Inspeção no sistema de refrigeração de precisão (HVAC) da sala de acumuladores.\n"
    texto_plano += " - Reaperto das conexões de potência (barramentos, cabos de descida) usando torquímetro, aliado a inspeção termográfica em regime de carga nominal.\n"
    
    pdf.multi_cell(0, 6, txt=texto_plano)
    pdf.ln(10)

    # 7. Anexos
    pdf.add_page()
    pdf.set_font("Arial", 'B', 10)
    pdf.cell(0, 6, txt="ANEXO I - TABELA DO LOG DE ALARMES SNMP", ln=True)
    if not dados_alarmes:
        pdf.set_font("Arial", size=9)
        pdf.cell(0, 6, txt="Nenhum evento registrado no período.", ln=True)
    else:
        pdf.set_font("Arial", 'B', 8)
        pdf.cell(35, 6, "Data/Hora", border=1, align='C')
        pdf.cell(85, 6, "Evento", border=1, align='C')
        pdf.cell(30, 6, "Severidade", border=1, align='C')
        pdf.cell(30, 6, "Status", border=1, align='C')
        pdf.ln()
        
        pdf.set_font("Arial", size=8)
        for alarme in dados_alarmes:
            pdf.cell(35, 6, str(alarme[0]), border=1, align='C')
            pdf.cell(85, 6, str(alarme[1])[:45], border=1, align='C')
            pdf.cell(30, 6, str(alarme[2]), border=1, align='C')
            pdf.cell(30, 6, str(alarme[3]), border=1, align='C')
            pdf.ln()
            
    pdf.ln(10)
    pdf.set_font("Arial", 'B', 10)
    pdf.cell(0, 6, txt="ANEXO II - DADOS BRUTOS DE TELEMETRIA", ln=True)
    pdf.set_font("Arial", 'B', 8)
    pdf.cell(38, 6, "Data/Hora", border=1, align='C')
    pdf.cell(38, 6, "VCC (V)", border=1, align='C')
    pdf.cell(38, 6, "Corrente Carga (A)", border=1, align='C')
    pdf.cell(38, 6, "Temperatura (C)", border=1, align='C')
    pdf.cell(38, 6, "Capacidade (%)", border=1, align='C')
    pdf.ln()
    
    pdf.set_font("Arial", size=8)
    # Imprime no máximo os primeiros 100 registros para evitar que o laudo fique com 50 páginas inúteis.
    # O histórico completo o analista consulta na interface web ou exportando para CSV no Dashboard
    for linha in dados[:100]:
        pdf.cell(38, 6, str(linha[0]), border=1, align='C')
        pdf.cell(38, 6, f"{linha[1]:.2f}", border=1, align='C')
        pdf.cell(38, 6, f"{linha[2]:.2f}", border=1, align='C')
        pdf.cell(38, 6, f"{linha[3]:.1f}", border=1, align='C')
        pdf.cell(38, 6, f"{linha[4]:.1f}", border=1, align='C')
        pdf.ln()
        
    if len(dados) > 100:
        pdf.set_font("Arial", 'I', 8)
        pdf.cell(0, 6, txt=f"... e mais {len(dados)-100} registros suprimidos. Para o arquivo integral, utilize a exportação em CSV.", ln=True)

    file_path = f"laudo_smartpack_{site_id}.pdf"
    pdf.output(file_path)
    
    return FileResponse(file_path, media_type='application/pdf', filename=f"laudo_{site_id}.pdf")

# --- DASHBOARD DE HISTÓRICO (LAUDO WEB) ---
@app.get("/relatorio-web")
async def dashboard_historico():
    """Dashboard web para visualização interativa do histórico e laudo"""
    return FileResponse("dashboard_laudo.html")

@app.get("/api/historico")
def get_historico(site_id: str = "s1", data_inicio: str = None, data_fim: str = None):
    """Retorna os dados históricos em JSON para os gráficos do dashboard web"""
    conn = sqlite3.connect('telemetria.db')
    conn.row_factory = sqlite3.Row  # Permite acessar colunas pelo nome (como dicionário)
    cursor = conn.cursor()
    
    # --- Query da Telemetria ---
    query = "SELECT timestamp, tensao, corrente, temperatura, capacidade FROM historico WHERE site_id=?"
    params = [site_id]
    if data_inicio:
        query += " AND timestamp >= ?"
        params.append(data_inicio.replace("T", " "))
    if data_fim:
        query += " AND timestamp <= ?"
        params.append(data_fim.replace("T", " "))
    query += " ORDER BY timestamp ASC"
        
    cursor.execute(query, params)
    telemetria = [dict(row) for row in cursor.fetchall()]
    
    # --- Query dos Alarmes ---
    query_al = "SELECT timestamp, evento, severidade, status FROM alarmes_historico WHERE site_id=?"
    params_al = [site_id]
    if data_inicio:
        query_al += " AND timestamp >= ?"
        params_al.append(data_inicio.replace("T", " "))
    if data_fim:
        query_al += " AND timestamp <= ?"
        params_al.append(data_fim.replace("T", " "))
    query_al += " ORDER BY timestamp DESC"
    
    cursor.execute(query_al, params_al)
    alarmes = [dict(row) for row in cursor.fetchall()]
    
    conn.close()
    return {
        "telemetria": telemetria,
        "alarmes": alarmes
    }

# --- DASHBOARD WEB (Nativo para PC/Notebook) ---
@app.get("/")
async def painel_desktop():
    """Dashboard acessível pelo navegador no PC/Notebook"""
    return FileResponse("index.html")