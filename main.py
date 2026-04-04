import asyncio
import random
import sqlite3
import os
from datetime import datetime
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, FileResponse
from pysnmp.hlapi.asyncio import *

app = FastAPI(title="Tester_Smartpack", description="API de Integração com Eltek Smartpack S")

# Memória da Telemetria (SNMP GET)
telemetria_atual = {
    "tensao_barramento": 0.0,
    "corrente_bateria": 0.0,
    "temperatura_bateria": 0.0,
    "capacidade_bateria": 0.0,
    "status_conexao": "Desconectado"
}

# Memória dos Alarmes (SNMP)
alarmes_ativos = {
    "ultimo_alarme": "Nenhum evento registrado",
    "status_painel": "Normal",
    "severidade": "Baixa"
}

# --- CONFIGURAÇÃO DE AMBIENTE ---
SIMULAR_MODULO = True  # Mude para False quando for conectar no painel real

ELTEK_IP = "192.168.10.20"  # IP Padrão de fábrica do Smartpack S
SNMP_PORT = 161
SNMP_COMMUNITY = "sua_senha_aqui"  # Comunidade de leitura (verifique no painel)

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
            telemetria_atual["status_conexao"] = "Simulador Online"
            # Valores flutuantes realistas para painel de 48V
            telemetria_atual["tensao_barramento"] = round(random.uniform(53.0, 54.2), 2)
            telemetria_atual["corrente_bateria"] = round(random.uniform(10.0, 35.5), 2)
            telemetria_atual["temperatura_bateria"] = round(random.uniform(22.0, 26.5), 1)
            telemetria_atual["capacidade_bateria"] = round(random.uniform(95.0, 100.0), 1)
            await asyncio.sleep(2)
        return

    snmp_engine = SnmpEngine()
    
    while True:
        try:
            # Executa a requisição SNMP GET para os 4 OIDs de uma só vez
            errorIndication, errorStatus, errorIndex, varBinds = await getCmd(
                snmp_engine,
                CommunityData(SNMP_COMMUNITY, mpModel=1), # mpModel=1 indica SNMPv2c
                UdpTransportTarget((ELTEK_IP, SNMP_PORT)),
                ContextData(),
                ObjectType(ObjectIdentity(OID_TENSAO)),
                ObjectType(ObjectIdentity(OID_CORRENTE)),
                ObjectType(ObjectIdentity(OID_TEMPERATURA)),
                ObjectType(ObjectIdentity(OID_CAPACIDADE))
            )

            if errorIndication:
                telemetria_atual["status_conexao"] = "Falha de Conexão"
                print(f"Falha ao conectar no SNMP {ELTEK_IP}: {errorIndication}")
            elif errorStatus:
                telemetria_atual["status_conexao"] = "Erro de Leitura"
                print(f"Erro SNMP: {errorStatus.prettyPrint()}")
            else:
                telemetria_atual["status_conexao"] = "Online"
                
                # varBinds é uma lista de tuplas (OID, Valor). Extraímos apenas os valores numéricos
                valores = [v[1] for v in varBinds]
                
                # OBS: O Eltek costuma mandar valores multiplicados por 10 ou 100 para evitar enviar decimais pela rede.
                # Ajuste a divisão das linhas abaixo de acordo com o que visualizar ao conectar o cabo.
                telemetria_atual["tensao_barramento"] = round(float(valores[0]) / 100.0, 2)
                telemetria_atual["corrente_bateria"] = round(float(valores[1]) / 10.0, 2)
                telemetria_atual["temperatura_bateria"] = round(float(valores[2]), 1)
                telemetria_atual["capacidade_bateria"] = round(float(valores[3]), 1)
                
        except Exception as e:
            print(f"Erro inesperado no SNMP GET: {e}")
            telemetria_atual["status_conexao"] = "Erro Crítico"
            
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
        print(f"⚠️ Trap SNMP recebido de {addr}!")
        
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

        alarmes_ativos["ultimo_alarme"] = evento
        alarmes_ativos["severidade"] = sev
        alarmes_ativos["status_painel"] = "Em Alarme"
        salvar_alarme_db(evento, sev, "Em Alarme")

def salvar_alarme_db(evento, severidade, status):
    """Salva um novo evento de alarme no banco de dados local"""
    try:
        conn = sqlite3.connect('telemetria.db')
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO alarmes_historico (evento, severidade, status)
            VALUES (?, ?, ?)
        ''', (evento, severidade, status))
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
    
    indice = 0
    ultimo_estado = ""
    while True:
        # Muda o status a cada 4 segundos para dar tempo de ver todos no painel
        await asyncio.sleep(4) 
        
        evento = eventos[indice]
        
        if ultimo_estado != evento[0]:
            alarmes_ativos["ultimo_alarme"] = evento[0]
            alarmes_ativos["severidade"] = evento[1]
            alarmes_ativos["status_painel"] = evento[2]
            salvar_alarme_db(evento[0], evento[1], evento[2])
            ultimo_estado = evento[0]
        
        # Avança para o próximo alarme. Se chegar no último, volta para o primeiro (loop)
        indice = (indice + 1) % len(eventos)

def init_db():
    """Cria o banco de dados SQLite e a tabela caso não existam"""
    conn = sqlite3.connect('telemetria.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS historico (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            tensao REAL,
            corrente REAL,
            temperatura REAL,
            capacidade REAL
        )
    ''')
    
    # Nova tabela para o Histórico de Alarmes
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS alarmes_historico (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            evento TEXT,
            severidade TEXT,
            status TEXT
        )
    ''')
    conn.commit()
    conn.close()

async def salvar_historico_db():
    """Worker para salvar dados no banco a cada 60 segundos"""
    print("💾 BD Worker ativado: Salvando telemetria a cada 1 minuto...")
    while True:
        await asyncio.sleep(60) # Espera 60 segundos
        try:
            conn = sqlite3.connect('telemetria.db')
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO historico (tensao, corrente, temperatura, capacidade)
                VALUES (?, ?, ?, ?)
            ''', (
                telemetria_atual["tensao_barramento"],
                telemetria_atual["corrente_bateria"],
                telemetria_atual["temperatura_bateria"],
                telemetria_atual["capacidade_bateria"]
            ))
            conn.commit()
            conn.close()
            print(f"💾 Histórico salvo no BD: {telemetria_atual['tensao_barramento']}V")
        except Exception as e:
            print(f"Erro ao salvar no SQLite: {e}")

@app.on_event("startup")
async def iniciar_background_tasks():
    """Liga os dois motores em paralelo assim que a API sobe"""
    # Inicializa o banco e liga o worker de gravação
    init_db()
    asyncio.create_task(salvar_historico_db())
    
    if SIMULAR_MODULO:
        asyncio.create_task(ler_dados_snmp())
        asyncio.create_task(simular_alarmes())
    else:
        # 1. Liga o leitor SNMP GET real
        asyncio.create_task(ler_dados_snmp())
        
        # 2. Abre a porta UDP 1162 para escutar os Traps SNMP reais
        loop = asyncio.get_running_loop()
        await loop.create_datagram_endpoint(
            lambda: SNMPTrapReceiver(),
            local_addr=('0.0.0.0', 1162)
        )

@app.get("/api/telemetria")
async def get_telemetria():
    return telemetria_atual

# --- NOVA ROTA PARA O APLICATIVO ---
@app.get("/api/alarmes")
async def get_alarmes():
    """O cliente vai consumir essa rota para exibir status de falha"""
    return alarmes_ativos

# --- ROTA PARA GERAÇÃO DO LAUDO PDF ---
@app.get("/api/relatorio")
def gerar_relatorio_pdf(data_inicio: str = None, data_fim: str = None):
    """Gera um PDF com o histórico e análises baseado nos filtros de data"""
    try:
        from fpdf import FPDF
    except ImportError:
        return {"erro": "A biblioteca FPDF não está instalada. Rode: pip install fpdf"}

    conn = sqlite3.connect('telemetria.db')
    cursor = conn.cursor()
    
    query = "SELECT timestamp, tensao, corrente, temperatura, capacidade FROM historico WHERE 1=1"
    params = []
    
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
    query_al = "SELECT timestamp, evento, severidade, status FROM alarmes_historico WHERE 1=1"
    params_al = []
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
    pdf.add_page()
    
    # Cabeçalho
    pdf.set_font("Arial", 'B', 16)
    pdf.cell(0, 10, txt="LAUDO TÉCNICO - AETHER GRID (SMARTPACK S)", ln=True, align='C')
    pdf.set_font("Arial", size=10)
    pdf.cell(0, 10, txt=f"Gerado em: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}", ln=True, align='C')
    pdf.ln(5)
    
    # Análise Estatística (Resumo)
    pdf.set_font("Arial", 'B', 12)
    pdf.cell(0, 10, txt="1. Resumo Analítico", ln=True)
    pdf.set_font("Arial", size=10)
    if not dados:
        pdf.cell(0, 10, txt="Nenhum registro encontrado para os filtros aplicados.", ln=True)
    else:
        tensoes = [d[1] for d in dados]
        correntes = [d[2] for d in dados]
        pdf.cell(0, 8, txt=f"Total de Registros: {len(dados)}", ln=True)
        pdf.cell(0, 8, txt=f"Tensão Barramento - Mínima: {min(tensoes):.2f}V | Máxima: {max(tensoes):.2f}V | Média: {(sum(tensoes)/len(tensoes)):.2f}V", ln=True)
        pdf.cell(0, 8, txt=f"Corrente Bateria - Mínima: {min(correntes):.2f}A | Máxima: {max(correntes):.2f}A", ln=True)
    
    pdf.ln(5)
    
    # Tabela de Dados Brutos
    pdf.set_font("Arial", 'B', 12)
    pdf.cell(0, 10, txt="2. Registros de Telemetria", ln=True)
    pdf.set_font("Arial", 'B', 9)
    pdf.cell(45, 8, "Data/Hora", border=1, align='C')
    pdf.cell(35, 8, "Tensão (V)", border=1, align='C')
    pdf.cell(35, 8, "Corrente (A)", border=1, align='C')
    pdf.cell(35, 8, "Temperatura (C)", border=1, align='C')
    pdf.cell(35, 8, "Capacidade (%)", border=1, align='C')
    pdf.ln()
    
    pdf.set_font("Arial", size=8)
    for linha in dados:
        pdf.cell(45, 8, str(linha[0]), border=1, align='C')
        pdf.cell(35, 8, f"{linha[1]:.2f}", border=1, align='C')
        pdf.cell(35, 8, f"{linha[2]:.2f}", border=1, align='C')
        pdf.cell(35, 8, f"{linha[3]:.1f}", border=1, align='C')
        pdf.cell(35, 8, f"{linha[4]:.1f}", border=1, align='C')
        pdf.ln()
        
    pdf.ln(10)
    
    # Tabela de Alarmes Brutos
    pdf.set_font("Arial", 'B', 12)
    pdf.cell(0, 10, txt="3. Historico de Eventos e Alarmes (SNMP)", ln=True)
    if not dados_alarmes:
        pdf.set_font("Arial", size=10)
        pdf.cell(0, 10, txt="Nenhum evento registrado no periodo.", ln=True)
    else:
        pdf.set_font("Arial", 'B', 9)
        pdf.cell(45, 8, "Data/Hora", border=1, align='C')
        pdf.cell(85, 8, "Evento", border=1, align='C')
        pdf.cell(30, 8, "Severidade", border=1, align='C')
        pdf.cell(30, 8, "Status", border=1, align='C')
        pdf.ln()
        
        pdf.set_font("Arial", size=8)
        for alarme in dados_alarmes:
            pdf.cell(45, 8, str(alarme[0]), border=1, align='C')
            pdf.cell(85, 8, str(alarme[1])[:45], border=1, align='C') # Corta o texto se for muito longo
            pdf.cell(30, 8, str(alarme[2]), border=1, align='C')
            pdf.cell(30, 8, str(alarme[3]), border=1, align='C')
            pdf.ln()

    file_path = "laudo_smartpack.pdf"
    pdf.output(file_path)
    
    return FileResponse(file_path, media_type='application/pdf', filename="laudo_smartpack.pdf")

# --- DASHBOARD WEB (Nativo para PC/Notebook) ---
@app.get("/")
async def painel_desktop():
    """Dashboard acessível pelo navegador no PC/Notebook"""
    return FileResponse("index.html")