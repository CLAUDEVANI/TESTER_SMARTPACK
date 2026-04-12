"""
drivers/__init__.py — Importa todos os drivers para acionar o @register_driver de cada um.
Basta importar este pacote para que o registry fique populado.
"""

from .base import BaseDriver, Telemetria, Alarme, get_driver_class, list_drivers, register_driver
from .snmp import SNMPDriver
from .modbus_tcp import ModbusTCPDriver
from .modbus_rtu import ModbusRTUDriver
from .simulator import SimulatorDriver

__all__ = [
    "BaseDriver", "Telemetria", "Alarme",
    "get_driver_class", "list_drivers", "register_driver",
    "SNMPDriver", "ModbusTCPDriver", "ModbusRTUDriver", "SimulatorDriver",
]