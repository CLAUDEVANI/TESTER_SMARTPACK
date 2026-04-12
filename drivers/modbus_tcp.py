"""
drivers/modbus_tcp.py — Driver Modbus TCP universal

Compatível com:
  - Baterias Moura MSL / Telecom com gateway Modbus TCP
  - Qualquer inversor, UPS ou bateria com interface Ethernet Modbus
  - Gateways IoT (ex: Raspberry Pi + RS485 expondo Modbus TCP)

Configuração esperada em driver_config (JSON):
{
  "port": 502,
  "unit_id": 1,
  "timeout": 3.0,
  "capacidade_banco_ah": 400.0,
  "registers": {
    "tensao":      {"address": 0,  "count": 1, "scale": 0.1,  "type": "holding"},
    "corrente":    {"address": 1,  "count": 1, "scale": 0.1,  "type": "holding"},
    "temperatura": {"address": 2,  "count": 1, "scale": 0.1,  "type": "holding"},
    "capacidade":  {"address": 3,  "count": 1, "scale": 0.1,  "type": "holding"},
    "soh":         {"address": 4,  "count": 1, "scale": 0.1,  "type": "holding"}
  }
}

Registradores padrão genéricos são usados como fallback.
Solicite o "Mapa de Registradores Modbus" do fabricante para preencher corretamente.
"""

import asyncio
from .base import BaseDriver, Telemetria, register_driver

# Mapa de registradores padrão (genérico — ajuste conforme fabricante)
DEFAULT_REGISTERS = {
    "tensao":      {"address": 0,  "count": 1, "scale": 0.1,  "type": "holding"},
    "corrente":    {"address": 1,  "count": 1, "scale": 0.1,  "type": "holding"},
    "temperatura": {"address": 2,  "count": 1, "scale": 0.1,  "type": "holding"},
    "capacidade":  {"address": 3,  "count": 1, "scale": 1.0,  "type": "holding"},
    "soh":         {"address": 4,  "count": 1, "scale": 1.0,  "type": "holding"},
}


@register_driver("modbus_tcp")
class ModbusTCPDriver(BaseDriver):

    def __init__(self, site_id: str, ip: str, config: dict):
        super().__init__(site_id, ip, config)
        self.port      = config.get("port", 502)
        self.unit_id   = config.get("unit_id", 1)
        self.timeout   = config.get("timeout", 3.0)
        self.cap_ah    = config.get("capacidade_banco_ah", 400.0)
        self.registers = config.get("registers", DEFAULT_REGISTERS)
        self._client   = None

    async def connect(self) -> bool:
        try:
            from pymodbus.client import AsyncModbusTcpClient
            self._client = AsyncModbusTcpClient(
                host=self.ip, port=self.port, timeout=self.timeout
            )
            ok = await self._client.connect()
            self._conectado = ok
            if not ok:
                print(f"[ModbusTCP:{self.site_id}] Falha ao conectar em {self.ip}:{self.port}")
            return ok
        except ImportError:
            print(f"[ModbusTCP:{self.site_id}] pymodbus não instalado. Rode: pip install pymodbus")
            return False
        except Exception as e:
            print(f"[ModbusTCP:{self.site_id}] Erro de conexão: {e}")
            return False

    async def _read_register(self, reg_cfg: dict) -> float | None:
        """Lê um registrador Holding ou Input e retorna o valor escalado."""
        if self._client is None or not self._client.connected:
            return None
        try:
            addr  = reg_cfg["address"]
            count = reg_cfg.get("count", 1)
            scale = reg_cfg.get("scale", 1.0)
            rtype = reg_cfg.get("type", "holding")

            if rtype == "input":
                rr = await self._client.read_input_registers(addr, count, slave=self.unit_id)
            else:
                rr = await self._client.read_holding_registers(addr, count, slave=self.unit_id)

            if rr.isError():
                return None
            raw = rr.registers[0]
            # Converte para signed 16-bit se necessário (ex: correntes negativas)
            if raw > 32767:
                raw -= 65536
            return round(raw * scale, 2)
        except Exception:
            return None

    async def read_telemetry(self) -> Telemetria:
        tel = Telemetria(status_conexao="Conectando...")

        if self._client is None or not self._client.connected:
            ok = await self.connect()
            if not ok:
                tel.status_conexao = "Falha de Conexão"
                return tel

        try:
            tensao      = await self._read_register(self.registers.get("tensao", DEFAULT_REGISTERS["tensao"]))
            corrente    = await self._read_register(self.registers.get("corrente", DEFAULT_REGISTERS["corrente"]))
            temperatura = await self._read_register(self.registers.get("temperatura", DEFAULT_REGISTERS["temperatura"]))
            capacidade  = await self._read_register(self.registers.get("capacidade", DEFAULT_REGISTERS["capacidade"]))
            soh         = await self._read_register(self.registers.get("soh", DEFAULT_REGISTERS["soh"])) if "soh" in self.registers else None

            if tensao is None and corrente is None:
                tel.status_conexao = "Erro de Leitura"
                return tel

            tel.tensao_barramento   = tensao      or 0.0
            tel.corrente_bateria    = corrente    or 0.0
            tel.temperatura_bateria = temperatura or 0.0
            tel.capacidade_bateria  = capacidade  or 0.0
            tel.status_conexao      = "Online"

            if soh is not None:
                tel.extras["soh"] = soh

            # Autonomia
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
        if self._client and self._client.connected:
            self._client.close()
        self._conectado = False