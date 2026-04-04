# ⚡ Tester Smartpack NOC (Network Operations Center)

Sistema de telemetria, gestão preditiva e monitoramento centralizado (MultiSite) para retificadores **Eltek Smartpack S** e infraestruturas de missão crítica (Data Centers).

Este projeto foi desenvolvido utilizando tendências de UX/UI modernas para Centros de Controle (NOC), incluindo modo preditivo, gêmeos digitais e design responsivo, substituindo a necessidade de navegação física no hardware.

---

## 🏗️ Arquitetura do Sistema

O sistema utiliza uma arquitetura **Client-Server Asíncrona**, garantindo alta performance mesmo ao monitorar múltiplos Data Centers simultaneamente.

### 1. Backend (Python / FastAPI)
* **Framework:** FastAPI (Alta performance, ASGI).
* **Workers Assíncronos (`asyncio`):**
  * **SNMP GET Worker:** Varre ativamente (Polling) a rede a cada 2 segundos, consultando os OIDs do Smartpack de múltiplos IPs.
  * **SNMP Trap Receiver:** Servidor UDP rodando na porta `1162` aguardando interrupções proativas (Traps) enviadas pelos retificadores no instante em que uma falha ocorre.
  * **DB Persistence Worker:** Salva um "snapshot" de telemetria no banco de dados a cada 60 segundos por Site.
* **Gerador de Laudos:** Utiliza `fpdf` e `matplotlib` para injetar gráficos analíticos e cruzar dados de alarmes, gerando pareceres automatizados em PDF.

### 2. Banco de Dados (SQLite3)
Banco local (`telemetria.db`) auto-gerido com as seguintes tabelas principais:
* `sites`: Armazena a relação de Data Centers (ID, Nome, IP).
* `historico`: Tabela temporal contendo (Tensao, Corrente, Temperatura, Capacidade) vinculada a um Site ID.
* `alarmes_historico`: Tabela de logs contendo eventos, severidade e status de reconhecimento.
* `config`: Variáveis globais do sistema (ex: Status do Simulador).

### 3. Frontend (HTML / JS / CSS Nativo)
* **Zero Build-Tools:** Interface construída puramente em Javascript/CSS (Vanilla), sem necessidade de Node.js, Webpack ou React, facilitando a portabilidade.
* **Bibliotecas Externas:** Apenas `Chart.js` via CDN para renderização flexível de gráficos.
* **Comunicação:** Chamadas assíncronas (`fetch`) consumindo a API REST do Backend a cada 1 segundo.

---

## ✨ Principais Funcionalidades (UX 2026)

1. **Gestão MultiSite Centralizada:** Capacidade de adicionar, editar e alternar dinamicamente entre diversos Data Centers no mesmo painel, sem perda de histórico.
2. **Inteligência Preditiva (KPIs):** O algoritmo abandona o modelo puramente "Reativo" e analisa o SoC (State of Charge) vs Dreno de Corrente para prever com exatidão o tempo restante (autonomia) em horas caso haja falha comercial.
3. **Gêmeo Digital 3D:** Interface renderiza os módulos retificadores. Se um módulo físico falha na infraestrutura, o respectivo slot pisca em vermelho na interface virtual.
4. **Design Adaptativo para NOC:**
   * Janelas "Pop-out" flutuantes para organização em múltiplos monitores.
   * Modo Tela Cheia `(:fullscreen)` nativo que reestrutura os blocos de malha (Flexbox/Grid) para maximizar gráficos de telemetria.
   * Alternância entre Temas (Dark Mode e Light Mode) com persistência local.
5. **Avisos Sensoriais e Contextuais:** Quando um alarme "Crítico" acontece, toda a UI reage (Pulsos visuais, luzes virtuais e bipes embutidos no navegador via `AudioContext`).

---

## 📂 Estrutura de Diretórios

```text
/home/t430/Tester_Smartpack/
│
├── main.py                 # Core do Backend (Rotas FastAPI e Lógica SNMP)
├── telemetria.db           # Banco de Dados SQLite3 (Autogerado)
│
├── index.html              # Frontend: Painel Ao Vivo (Monitoramento/NOC)
├── dashboard_laudo.html    # Frontend: Painel Analítico e Exportação de Histórico
│
├── start.sh                # Script de inicialização automatizada (Linux)
├── venv/                   # Ambiente Virtual Python (Isolamento de libs)
└── README.md               # Esta documentação
```

---

## 🔌 Configuração e Comunicação (SNMP)

Para que o sistema consiga conversar com os dispositivos reais, verifique se a controladora Eltek Smartpack S está configurada da seguinte forma na aba de Rede:
* **Versão SNMP:** v1 ou v2c
* **Porta SNMP:** 161
* **Comunidade (Community String):** Deve coincidir com a variável `SNMP_COMMUNITY` definida no topo do arquivo `main.py` (Padrão sugerido: `sua_senha_aqui`).
* **Trap Destination:** O IP do servidor onde este dashboard está rodando (Porta 1162).

### Modificando OIDs
Os identificadores padrão utilizados no código são:
* `Tensão`: .1.3.6.1.4.1.12148.10.2.4.1.1.0
* `Corrente`: .1.3.6.1.4.1.12148.10.2.4.1.2.0
* `Temperatura`: .1.3.6.1.4.1.12148.10.2.4.1.3.0
* `Capacidade`: .1.3.6.1.4.1.12148.10.2.4.1.4.0

*(Consulte a MIB da Eltek caso necessite auditar OIDs de geradores, climatização, etc.)*

---

## 🚀 Como Executar o Projeto

### 1. Pré-requisitos
* Python 3.8+
* Dependências pip: `fastapi`, `uvicorn`, `pysnmp`, `fpdf`, `matplotlib`

Instale as dependências executando:
```bash
pip install fastapi uvicorn pysnmp fpdf matplotlib
```

### 2. Inicialização via Script (Recomendado para Linux)
Dê permissão de execução e rode o shell script:
```bash
chmod +x start.sh
./start.sh
```

### 3. Inicialização Manual
```bash
source venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8000
```

### 4. Acesso
Abra o navegador no computador local ou em qualquer máquina da mesma rede e acesse:
* **Painel Ao Vivo:** `http://localhost:8000` ou `http://<IP-DO-SERVIDOR>:8000`
* **Laudo Web:** `http://localhost:8000/relatorio-web`

---

## 🎮 Modo Simulador
O sistema foi desenhado com um "Modo Simulador" embutido para testes de UX, demonstrações para clientes ou cenários em que o hardware não está conectado. 

Ele simula comportamentos em cadeia (Ex: Quando ocorre "Falha de Rede", a tensão cai organicamente, a bateria começa a descarregar e o sistema prevê a autonomia).

**Para ativar/desativar:** No painel principal (`index.html`), clique na engrenagem **(⚙️ Configurações)** e interaja com o switch "Modo Simulador". O Backend alternará instantaneamente entre o simulador e o ping na rede física, sem precisão de reinicialização do serviço.

---

## 📄 Relatórios em PDF
A rota `/api/relatorio` contém uma lógica de "Parecer Técnico Inteligente". Ao gerar um laudo, o Python analisa a base de dados filtrada e determina autonomamente se o Data Center está `APTO`, `APTO COM RESTRIÇÕES` ou `INAPTO`, com base na temperatura das baterias (> 25ºC) e na ausência de retificadores, injetando gráficos de curva e textos dinâmicos no arquivo PDF final.