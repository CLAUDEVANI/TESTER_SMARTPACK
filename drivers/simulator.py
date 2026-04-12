"""
drivers/simulator.py — Driver simulador para desenvolvimento e testes

Quando protocolo = "simulator", este driver é usado.
Simula dados realistas de telemetria e cicla alarmes automaticamente.
Não requer hardware real.

Configuração esperada em driver_config (JSON):
{
  "tensao_base": 54.0,
  "capacidade_banco_ah": 400.0,
  "modo_alarme": true       // se true, cicla alarmes automaticamente
}
"""

import asyncio
import random
from .base import BaseDriver, Telemetria, register_driver

EVENTOS_SIMULADOS = [
    ("Nenhum evento registrado", "Baixa", "Normal"),
    ("Falha de Retificador detectada", "Crítica", "Em Alarme"),
    ("Falha de Rede (AC)", "Alta", "Em Alarme"),
    ("Temperatura da Bateria Alta", "Atenção", "Em Alarme"),
    ("Disjuntor de Bateria Aberto", "Crítica", "Em Alarme"),
    ("Tensão de Barramento Baixa", "Alta", "Em Alarme"),
    ("Falha no Teste de Bateria", "Atenção", "Em Alarme"),
    ("Sobretensão no Retificador", "Alta", "Em Alarme"),
    ("Porta do Gabinete Aberta", "Baixa", "Atenção"),
]


@register_driver("simulator")
class SimulatorDriver(BaseDriver):

    def __init__(self, site_id: str, ip: str, config: dict):
        super().__init__(site_id, ip, config)
        self.tensao_base  = config.get("tensao_base", 54.0)
        self.cap_ah       = config.get("capacidade_banco_ah", 400.0)
        self.modo_alarme  = config.get("modo_alarme", True)
        self._capacidade  = 80.0   # estado interno da capacidade
        self._alarm_idx   = 0
        self._ultimo_alarme = "Nenhum evento registrado"
        self._ciclos      = 0

    async def connect(self) -> bool:
        self._conectado = True
        print(f"[Simulador:{self.site_id}] Online (tensão base {self.tensao_base}V)")
        return True

    async def read_telemetry(self) -> Telemetria:
        self._ciclos += 1

        em_alarme = self._ultimo_alarme == "Falha de Rede (AC)"

        if em_alarme:
            tensao    = round(random.uniform(47.0, 50.5), 2)
            self._capacidade = max(0.0, self._capacidade - 0.5)
        else:
            base = self.tensao_base
            tensao    = round(random.uniform(base - 0.3, base + 0.2), 2)
            self._capacidade = min(100.0, self._capacidade + 0.3)

        corrente    = round(random.uniform(10.0, 35.5), 2)
        temperatura = round(random.uniform(22.0, 26.5), 1)
        capacidade  = round(self._capacidade, 1)

        # Autonomia
        carga_ah = self.cap_ah * (capacidade / 100.0)
        if tensao < 51.0 and corrente > 0:
            autonomia = f"{round(carga_ah / corrente, 1)} Horas"
        else:
            autonomia = "AC Normal (Flutuação)"

        tel = Telemetria(
            tensao_barramento=tensao,
            corrente_bateria=corrente,
            temperatura_bateria=temperatura,
            capacidade_bateria=capacidade,
            autonomia_estimada=autonomia,
            status_conexao="Simulador Online",
            extras={"soh": round(random.uniform(88.0, 98.0), 1)}
        )
        return tel

    def proximo_alarme(self) -> tuple[str, str, str]:
        """Retorna próximo evento simulado (chamado pelo scheduler de alarmes)."""
        if not self.modo_alarme:
            return ("Nenhum evento registrado", "Baixa", "Normal")
        evento = EVENTOS_SIMULADOS[self._alarm_idx % len(EVENTOS_SIMULADOS)]
        self._alarm_idx += 1
        self._ultimo_alarme = evento[0]
        return evento

    async def disconnect(self):
        self._conectado = False