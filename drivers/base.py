"""
drivers/base.py — Interface base universal para todos os drivers de telemetria.

Todo driver novo deve herdar de BaseDriver e implementar:
  - connect()
  - read_telemetry() -> dict com as chaves padronizadas
  - disconnect()

O Driver Manager (em main.py) instancia o driver correto por site
baseado no campo 'protocolo' da tabela 'sites'.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Estrutura de telemetria normalizada — igual para todos os drivers
# ---------------------------------------------------------------------------
@dataclass
class Telemetria:
    tensao_barramento: float = 0.0      # V
    corrente_bateria: float = 0.0       # A
    temperatura_bateria: float = 0.0    # °C
    capacidade_bateria: float = 0.0     # % SoC
    autonomia_estimada: str = "Calculando..."
    status_conexao: str = "Desconectado"
    extras: dict = field(default_factory=dict)  # dados adicionais do driver (SoH, células, etc.)

    def to_dict(self) -> dict:
        d = {
            "tensao_barramento": self.tensao_barramento,
            "corrente_bateria": self.corrente_bateria,
            "temperatura_bateria": self.temperatura_bateria,
            "capacidade_bateria": self.capacidade_bateria,
            "autonomia_estimada": self.autonomia_estimada,
            "status_conexao": self.status_conexao,
        }
        d.update(self.extras)
        return d


@dataclass
class Alarme:
    ultimo_alarme: str = "Nenhum evento registrado"
    status_painel: str = "Normal"
    severidade: str = "Baixa"  # Baixa | Atenção | Alta | Crítica


# ---------------------------------------------------------------------------
# Classe base abstrata
# ---------------------------------------------------------------------------
class BaseDriver(ABC):
    """
    Contrato que todo driver de dispositivo deve cumprir.

    Parâmetros recebidos no __init__:
      - site_id (str): identificador do site
      - config (dict): conteúdo do campo driver_config da tabela sites
        Ex SNMP:       {"community": "public", "port": 161, "oids": {...}}
        Ex Modbus TCP: {"port": 502, "unit_id": 1, "registers": {...}}
        Ex Modbus RTU: {"port": "/dev/ttyUSB0", "baudrate": 9600, "slave_id": 1}
    """

    def __init__(self, site_id: str, ip: str, config: dict):
        self.site_id = site_id
        self.ip = ip          # IP ou path serial (/dev/ttyUSB0)
        self.config = config
        self._conectado = False

    @abstractmethod
    async def connect(self) -> bool:
        """Abre conexão com o dispositivo. Retorna True se OK."""
        ...

    @abstractmethod
    async def read_telemetry(self) -> Telemetria:
        """Lê dados do dispositivo e retorna Telemetria normalizada."""
        ...

    @abstractmethod
    async def disconnect(self):
        """Fecha conexão graciosamente."""
        ...

    # Método opcional — drivers que suportam escrita podem sobrescrever
    async def write_parameter(self, key: str, value) -> bool:
        raise NotImplementedError(f"Driver {self.__class__.__name__} não suporta escrita.")

    def __repr__(self):
        return f"<{self.__class__.__name__} site={self.site_id} ip={self.ip}>"


# ---------------------------------------------------------------------------
# Registry de drivers disponíveis
# ---------------------------------------------------------------------------
_REGISTRY: dict[str, type[BaseDriver]] = {}


def register_driver(name: str):
    """Decorador para registrar um driver pelo nome do protocolo."""
    def decorator(cls: type[BaseDriver]):
        _REGISTRY[name] = cls
        return cls
    return decorator


def get_driver_class(protocolo: str) -> Optional[type[BaseDriver]]:
    return _REGISTRY.get(protocolo)


def list_drivers() -> list[str]:
    return list(_REGISTRY.keys())