"""
drivers/modbus_rtu.py — Driver Modbus RTU (RS485 serial)

Compatível com:
  - Baterias Moura MSL / Telecom (com mapa de registradores fornecido pela Moura)
  - Qualquer bateria BMS com porta RS485
  - Conversores USB-RS485 (ex: CH340, CP2102)

Configuração esperada em driver_config (JSON):
{
  "port": "/dev/ttyUSB0",     // Windows: "COM3"
  "baudrate": 9600,
  "slave_id": 1,
  "bytesize": 8,
  "parity": "N",
  "stopbits": 1,
  "timeout": 2.0,
  "capacidade_banco_ah": 200.0,
  "registers": {
    "tensao":      {"address": 0,  "count": 1, "scale": 0.01, "type": "holding"},
    "corrente":    {"address": 1,  "count": 1, "scale": 0.01, "type": "holding"},
    "temperatura": {"address": 2,  "count": 1, "scale": 0.1,  "type": "holding"},
    "capacidade":  {"address": 3,  "count": 1, "scale": 1.0,  "type": "holding"},
    "soh":         {"address": 4,  "count": 1, "scale": 1.0,  "type": "holding"},
    "alarme":      {"address": 5,  "count": 1, "scale": 1.0,  "type": "holding"}
  }
}

NOTA: O mapa de registradores exato deve ser solicitado ao fabricante (Moura B2B)
via NDA. Os endereços acima são genéricos e podem não corresponder ao seu modelo.
"""

import asyncio
from .base import BaseDriver, Telemetria, register_driver

DEFAULT_REGISTERS_RTU = {
    "tensao":      {"address": 0, "count": 1, "scale": 0.01, "type": "holding"},
    "corrente":    {"address": 1, "count": 1, "scale": 0.01, "type": "holding"},
    "temperatura": {"address": 2, "count": 1, "scale": 0.1,  "type": "holding"},
    "capacidade":  {"address": 3, "count": 1, "scale": 1.0,  "type": "holding"},
    "soh":         {"address": 4, "count": 1, "scale": 1.0,  "type": "holding"},
}

# Mapa de alarmes padrão BMS (bit flags do registrador de alarme)
ALARM_FLAGS = {
    0: ("Sobretensão de Célula", "Alta"),
    1: ("Subtensão de Célula", "Alta"),
    2: ("Sobrecorrente de Carga", "Crítica"),
    3: ("Sobrecorrente de Descarga", "Crítica"),
    4: ("Temperatura Alta", "Alta"),
    5: ("Temperatura Baixa", "Atenção"),
    6: ("Falha no BMS", "Crítica"),
    7: ("Disjuntor Aberto", "Crítica"),
}


@register_driver("modbus_rtu")
class ModbusRTUDriver(BaseDriver):

    def __init__(self, site_id: str, ip: str, config: dict):
        # 'ip' aqui é o path serial: "/dev/ttyUSB0" ou "COM3"
        super().__init__(site_id, ip, config)
        self.serial_port = ip  # reutilizamos o campo ip para o path serial
        self.baudrate    = config.get("baudrate", 9600)
        self.slave_id    = config.get("slave_id", 1)
        self.bytesize    = config.get("bytesize", 8)
        self.parity      = config.get("parity", "N")
        self.stopbits    = config.get("stopbits", 1)
        self.timeout     = config.get("timeout", 2.0)
        self.cap_ah      = config.get("capacidade_banco_ah", 200.0)
        self.registers   = config.get("registers", DEFAULT_REGISTERS_RTU)
        self._client     = None
        self._lock       = asyncio.Lock()  # RS485 é half-duplex — serializa acessos

    async def connect(self) -> bool:
        try:
            from pymodbus.client import AsyncModbusSerialClient
            self._client = AsyncModbusSerialClient(
                port=self.serial_port,
                baudrate=self.baudrate,
                bytesize=self.bytesize,
                parity=self.parity,
                stopbits=self.stopbits,
                timeout=self.timeout,
            )
            ok = await self._client.connect()
            self._conectado = ok
            if ok:
                print(f"[ModbusRTU:{self.site_id}] Conectado em {self.serial_port} @ {self.baudrate}bps")
            else:
                print(f"[ModbusRTU:{self.site_id}] Falha ao abrir porta {self.serial_port}")
            return ok
        except ImportError:
            print(f"[ModbusRTU:{self.site_id}] pymodbus não instalado. Rode: pip install pymodbus pyserial")
            return False
        except Exception as e:
            print(f"[ModbusRTU:{self.site_id}] Erro: {e}")
            return False

    async def _read_register(self, reg_cfg: dict) -> float | None:
        async with self._lock:
            try:
                addr  = reg_cfg["address"]
                count = reg_cfg.get("count", 1)
                scale = reg_cfg.get("scale", 1.0)
                rtype = reg_cfg.get("type", "holding")

                if rtype == "input":
                    rr = await self._client.read_input_registers(addr, count, slave=self.slave_id)
                else:
                    rr = await self._client.read_holding_registers(addr, count, slave=self.slave_id)

                if rr.isError():
                    return None
                raw = rr.registers[0]
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
                tel.status_conexao = "Porta Serial Inacessível"
                return tel

        try:
            tensao      = await self._read_register(self.registers.get("tensao", DEFAULT_REGISTERS_RTU["tensao"]))
            corrente    = await self._read_register(self.registers.get("corrente", DEFAULT_REGISTERS_RTU["corrente"]))
            temperatura = await self._read_register(self.registers.get("temperatura", DEFAULT_REGISTERS_RTU["temperatura"]))
            capacidade  = await self._read_register(self.registers.get("capacidade", DEFAULT_REGISTERS_RTU["capacidade"]))
            soh         = await self._read_register(self.registers.get("soh", DEFAULT_REGISTERS_RTU["soh"])) if "soh" in self.registers else None

            # Lê registrador de alarmes (bit flags)
            alarme_bits = None
            if "alarme" in self.registers:
                raw_alarm = await self._read_register(self.registers["alarme"])
                if raw_alarm is not None:
                    alarme_bits = int(raw_alarm)

            if tensao is None and corrente is None:
                tel.status_conexao = "Erro de Leitura RTU"
                return tel

            tel.tensao_barramento   = tensao      or 0.0
            tel.corrente_bateria    = corrente    or 0.0
            tel.temperatura_bateria = temperatura or 0.0
            tel.capacidade_bateria  = capacidade  or 0.0
            tel.status_conexao      = "Online (RTU)"

            if soh is not None:
                tel.extras["soh"] = soh

            # Decodifica alarmes por bit flags
            if alarme_bits is not None and alarme_bits > 0:
                alarmes_ativos = []
                for bit, (descricao, _) in ALARM_FLAGS.items():
                    if alarme_bits & (1 << bit):
                        alarmes_ativos.append(descricao)
                tel.extras["alarmes_bms"] = alarmes_ativos

            # Autonomia
            carga_ah = self.cap_ah * (tel.capacidade_bateria / 100.0)
            if tel.tensao_barramento < 51.0 and tel.corrente_bateria > 0:
                horas = carga_ah / tel.corrente_bateria
                tel.autonomia_estimada = f"{round(horas, 1)} Horas"
            else:
                tel.autonomia_estimada = "AC Normal (Flutuação)"

        except Exception as e:
            tel.status_conexao = f"Erro RTU: {str(e)[:40]}"

        return tel

    async def disconnect(self):
        if self._client and self._client.connected:
            self._client.close()
        self._conectado = False