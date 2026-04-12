[![Python Application CI](https://github.com/SEU_USUARIO/SEU_REPOSITORIO/actions/workflows/ci.yml/badge.svg)](https://github.com/SEU_USUARIO/SEU_REPOSITORIO/actions/workflows/ci.yml)

# NOC Telemetria Universal v2.0

Sistema de monitoramento de energia com suporte a múltiplos protocolos e fabricantes.

## Protocolos suportados

| Protocolo     | Driver              | Equipamentos                              |
|---------------|---------------------|-------------------------------------------|
| `snmp`        | SNMPDriver          | Eltek Smartpack S, Huawei ETP, ORION      |
| `modbus_tcp`  | ModbusTCPDriver     | Qualquer BMS/inversor com Ethernet        |
| `modbus_rtu`  | ModbusRTUDriver     | Moura MSL, baterias RS485 genéricas       |
| `simulator`   | SimulatorDriver     | Desenvolvimento / testes sem hardware     |

---

## Instalação

```bash
# 1. Instalar dependências
pip install -r requirements.txt

# 2. Se estiver migrando do projeto anterior (preserva todos os dados)
python migrate_db.py

# 3. Iniciar o servidor
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Acesse: http://localhost:8000

---

## Estrutura do projeto

```
projeto/
├── main.py                  # API FastAPI — core agnóstico de protocolo
├── migrate_db.py            # Migração do banco existente
├── requirements.txt
├── telemetria.db            # Banco SQLite (criado automaticamente)
├── index.html               # Dashboard principal (sem alterações)
├── dashboard_laudo.html     # Dashboard de histórico (sem alterações)
└── drivers/
    ├── __init__.py          # Registry de drivers
    ├── base.py              # Interface BaseDriver + Telemetria normalizada
    ├── snmp.py              # Driver SNMP (Eltek, genérico)
    ├── modbus_tcp.py        # Driver Modbus TCP
    ├── modbus_rtu.py        # Driver Modbus RTU / RS485
    └── simulator.py         # Driver simulador
```

---

## Configuração de sites (por protocolo)

Cada site no banco tem os campos `protocolo` e `driver_config` (JSON).

### Eltek Smartpack S (SNMP)
```json
{
  "protocolo": "snmp",
  "driver_config": {
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
}
```

### Bateria Modbus TCP
```json
{
  "protocolo": "modbus_tcp",
  "driver_config": {
    "port": 502,
    "unit_id": 1,
    "registers": {
      "tensao":      {"address": 0, "count": 1, "scale": 0.1, "type": "holding"},
      "corrente":    {"address": 1, "count": 1, "scale": 0.1, "type": "holding"},
      "temperatura": {"address": 2, "count": 1, "scale": 0.1, "type": "holding"},
      "capacidade":  {"address": 3, "count": 1, "scale": 1.0, "type": "holding"}
    }
  }
}
```

### Moura MSL via RS485 (Modbus RTU)
```json
{
  "protocolo": "modbus_rtu",
  "ip": "/dev/ttyUSB0",
  "driver_config": {
    "port": "/dev/ttyUSB0",
    "baudrate": 9600,
    "slave_id": 1,
    "capacidade_banco_ah": 200.0,
    "registers": {
      "tensao":      {"address": 0, "count": 1, "scale": 0.01, "type": "holding"},
      "corrente":    {"address": 1, "count": 1, "scale": 0.01, "type": "holding"},
      "temperatura": {"address": 2, "count": 1, "scale": 0.1,  "type": "holding"},
      "capacidade":  {"address": 3, "count": 1, "scale": 1.0,  "type": "holding"}
    }
  }
}
```

> **Nota Moura:** Os endereços de registrador acima são genéricos.
> Solicite o "Manual de Comunicação Modbus e Mapa de Registradores do BMS"
> ao suporte de engenharia B2B da Moura (pode exigir NDA).

---

## Como adicionar um novo driver (ex: Huawei, Victron, SMA)

1. Crie `drivers/meu_fabricante.py`:

```python
from .base import BaseDriver, Telemetria, register_driver

@register_driver("meu_protocolo")   # nome que vai no campo 'protocolo' do site
class MeuFabricanteDriver(BaseDriver):

    async def connect(self) -> bool:
        # abre conexão
        return True

    async def read_telemetry(self) -> Telemetria:
        # lê dados e retorna Telemetria normalizada
        return Telemetria(
            tensao_barramento=48.0,
            corrente_bateria=10.0,
            temperatura_bateria=25.0,
            capacidade_bateria=90.0,
            status_conexao="Online"
        )

    async def disconnect(self):
        pass
```

2. Importe em `drivers/__init__.py`:
```python
from .meu_fabricante import MeuFabricanteDriver
```

3. Configure um site com `"protocolo": "meu_protocolo"` — pronto!

---

## API endpoints

| Método | Rota                              | Descrição                                    |
|--------|-----------------------------------|----------------------------------------------|
| GET    | `/api/sites`                      | Lista todos os sites                          |
| POST   | `/api/sites`                      | Cria/atualiza site (com protocolo + config)   |
| DELETE | `/api/sites/{site_id}`            | Remove site                                   |
| GET    | `/api/telemetria?site_id=s1`      | Telemetria atual do site                      |
| GET    | `/api/alarmes?site_id=s1`         | Alarme atual do site                          |
| GET    | `/api/historico?site_id=s1`       | Histórico filtrado por data                   |
| GET    | `/api/drivers`                    | Lista drivers disponíveis                     |
| GET    | `/api/driver-profiles`            | Lista perfis de driver configuráveis          |
| POST   | `/api/driver-profiles`            | Cria/atualiza perfil                          |
| GET    | `/api/driver-profiles/{id}/apply/{site_id}` | Aplica perfil a um site         |
| GET    | `/api/scan?subnet=192.168.1.0/24` | Scan SNMP na rede                             |
| GET    | `/api/relatorio?site_id=s1`       | Gera laudo PDF                                |
| POST   | `/api/reset`                      | Limpa histórico do banco                      |

---

## Hardware para Modbus RTU (RS485)

Para conectar baterias via RS485 você precisa de um conversor:

- **USB-RS485**: CH340G, CP2102, FT232 (~R$30 no Mercado Livre)
  - Windows: aparece como `COM3`, `COM4`, etc.
  - Linux: aparece como `/dev/ttyUSB0`, `/dev/ttyUSB1`, etc.
- **Raspberry Pi**: use um HAT RS485 ou módulo MAX485

**Pinagem RS485 padrão Moura (verificar no manual físico):**
- Pino 1 do RJ45: RS485+ (A)
- Pino 2 do RJ45: RS485− (B)
- Pino 3 ou 5: GND

---

## Fases de implementação concluídas

- [x] **Fase 1** — Refatoração: driver SNMP extraído, core agnóstico
- [x] **Fase 2** — Modbus TCP: suporte a baterias com Ethernet
- [x] **Fase 3** — Modbus RTU: suporte a RS485 serial (Moura e genéricas)
- [x] **Fase 4** — Mapa de registradores configurável via UI (`/api/driver-profiles`)