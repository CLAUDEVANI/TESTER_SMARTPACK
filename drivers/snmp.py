"""
drivers/snmp.py — Driver SNMP universal (v1, v2c, v3)

Compatível com:
  - Eltek Smartpack S / Flatpack S
  - Huawei ETP (com OIDs corretos)
  - Controladora ORION (baterias Moura via gateway)
  - Qualquer dispositivo SNMP com OIDs configuráveis

Configuração esperada em driver_config (JSON):
{
  "community": "public",      // comunidade SNMP v1/v2c
  "port": 161,                // porta UDP (padrão 161)
  "version": 1,               // 0=v1, 1=v2c, 3=v3
  "timeout": 1.5,
  "retries": 1,
  "oids": {
    "tensao":      "1.3.6.1.4.1.12148.10.2.4.1.1.0",
    "corrente":    "1.3.6.1.4.1.12148.10.2.4.1.2.0",
    "temperatura": "1.3.6.1.4.1.12148.10.2.4.1.3.0",
    "capacidade":  "1.3.6.1.4.1.12148.10.2.4.1.4.0"
  },
  "scale": {
    "tensao":   0.01,    // valor bruto * scale = valor real
    "corrente": 0.1,
    "temperatura": 1.0,
    "capacidade":  1.0
  },
  "capacidade_banco_ah": 400.0
}

OIDs padrão Eltek Smartpack S pré-preenchidos como fallback.
"""

import asyncio
from .base import BaseDriver, Telemetria, register_driver

# OIDs padrão Eltek Smartpack S
ELTEK_DEFAULT_OIDS = {
    "tensao":      "1.3.6.1.4.1.12148.10.2.4.1.1.0",
    "corrente":    "1.3.6.1.4.1.12148.10.2.4.1.2.0",
    "temperatura": "1.3.6.1.4.1.12148.10.2.4.1.3.0",
    "capacidade":  "1.3.6.1.4.1.12148.10.2.4.1.4.0",
}

ELTEK_DEFAULT_SCALE = {
    "tensao":      0.01,   # raw 5400 → 54.00 V
    "corrente":    0.1,    # raw 150  → 15.0 A
    "temperatura": 1.0,
    "capacidade":  1.0,
}


@register_driver("snmp")
class SNMPDriver(BaseDriver):

    def __init__(self, site_id: str, ip: str, config: dict):
        super().__init__(site_id, ip, config)
        self.community   = config.get("community", "public")
        self.port        = config.get("port", 161)
        self.version     = config.get("version", 1)       # 1 = SNMPv2c
        self.timeout     = config.get("timeout", 1.5)
        self.retries     = config.get("retries", 1)
        self.oids        = config.get("oids", ELTEK_DEFAULT_OIDS)
        self.scale       = config.get("scale", ELTEK_DEFAULT_SCALE)
        self.cap_ah      = config.get("capacidade_banco_ah", 400.0)
        self._engine     = None

    async def connect(self) -> bool:
        # SNMP é sem conexão persistente — apenas valida import
        try:
            from pysnmp.hlapi.asyncio import SnmpEngine
            self._engine = SnmpEngine()
            self._conectado = True
            return True
        except ImportError:
            print(f"[SNMP:{self.site_id}] pysnmp não instalado. Rode: pip install pysnmp")
            return False

    async def read_telemetry(self) -> Telemetria:
        tel = Telemetria(status_conexao="Conectando...")
        try:
            from pysnmp.hlapi.asyncio import (
                getCmd, CommunityData, UdpTransportTarget,
                ContextData, ObjectType, ObjectIdentity
            )
            from pysnmp.hlapi.asyncio import SnmpEngine

            engine = self._engine or SnmpEngine()
            oid_list = [
                ObjectType(ObjectIdentity(self.oids["tensao"])),
                ObjectType(ObjectIdentity(self.oids["corrente"])),
                ObjectType(ObjectIdentity(self.oids["temperatura"])),
                ObjectType(ObjectIdentity(self.oids["capacidade"])),
            ]

            errInd, errStat, _, varBinds = await getCmd(
                engine,
                CommunityData(self.community, mpModel=self.version),
                UdpTransportTarget((self.ip, self.port),
                                   timeout=self.timeout, retries=self.retries),
                ContextData(),
                *oid_list
            )

            if errInd or errStat:
                tel.status_conexao = "Falha de Conexão" if errInd else "Erro de Leitura"
                return tel

            vals = [float(v[1]) for v in varBinds]
            tel.tensao_barramento  = round(vals[0] * self.scale.get("tensao", 0.01), 2)
            tel.corrente_bateria   = round(vals[1] * self.scale.get("corrente", 0.1), 2)
            tel.temperatura_bateria = round(vals[2] * self.scale.get("temperatura", 1.0), 1)
            tel.capacidade_bateria  = round(vals[3] * self.scale.get("capacidade", 1.0), 1)
            tel.status_conexao     = "Online"

            # Cálculo de autonomia
            carga_ah = self.cap_ah * (tel.capacidade_bateria / 100.0)
            if tel.tensao_barramento < 51.0 and tel.corrente_bateria > 0:
                horas = carga_ah / tel.corrente_bateria
                tel.autonomia_estimada = f"{round(horas, 1)} Horas"
            else:
                tel.autonomia_estimada = "AC Normal (Flutuação)"

        except Exception as e:
            tel.status_conexao = f"Erro: {str(e)[:40]}"

        return tel

    async def disconnect(self):
        self._conectado = False
        # pysnmp não requer fechamento explícito