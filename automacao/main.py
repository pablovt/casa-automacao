#!/usr/bin/env python3
"""Worker leve: temperatura (Open-Meteo) -> liga/desliga switch no Home Assistant."""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from zoneinfo import ZoneInfo

LOG_DIR = Path(os.environ.get("LOG_DIR", "/logs"))
LOG_RETENTION_DAYS = int(os.environ.get("LOG_RETENTION_DAYS", "7"))
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"

log = logging.getLogger("automacao")


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log.setLevel(logging.INFO)
    log.handlers.clear()
    log.propagate = False

    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATEFMT)

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    log.addHandler(stream)

    # Arquivo diário; mantém LOG_RETENTION_DAYS backups (1 semana).
    file_handler = TimedRotatingFileHandler(
        filename=str(LOG_DIR / "automacao.log"),
        when="midnight",
        interval=1,
        backupCount=LOG_RETENTION_DAYS,
        encoding="utf-8",
        utc=False,
    )
    file_handler.suffix = "%Y-%m-%d"
    file_handler.setFormatter(formatter)
    log.addHandler(file_handler)


def purge_old_logs(now: datetime | None = None) -> None:
    """Remove arquivos de log com mais de LOG_RETENTION_DAYS (rede de segurança)."""
    if not LOG_DIR.is_dir():
        return
    cutoff = (now or datetime.now()).timestamp() - LOG_RETENTION_DAYS * 86400
    removed = 0
    for path in LOG_DIR.glob("automacao.log*"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
                log.info("Log antigo removido (> %s dias): %s", LOG_RETENTION_DAYS, path.name)
        except OSError as exc:
            log.warning("Falha ao remover log %s: %s", path, exc)
    if removed == 0:
        log.info(
            "Limpeza de logs: nenhum arquivo com mais de %s dias em %s",
            LOG_RETENTION_DAYS,
            LOG_DIR,
        )


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or value == "":
        log.error("Variável obrigatória ausente: %s", name)
        sys.exit(1)
    return value


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return float(raw)


setup_logging()

HA_URL = env("HA_URL", "http://homeassistant:8123").rstrip("/")
HA_TOKEN = env("HA_TOKEN")
ENTITY_ID = env("ENTITY_ID")
TEMP_ON = env_float("TEMP_ON", 28.0)
TEMP_OFF = env_float("TEMP_OFF", 25.0)
POLL_SECONDS = env_int("POLL_SECONDS", 900)
WINDOW_START = env_int("WINDOW_START", 10)
WINDOW_END = env_int("WINDOW_END", 17)
LAT = env("LAT", "-29.7175")
LON = env("LON", "-52.4258")
TZ_NAME = env("TZ", "America/Sao_Paulo")
TZ = ZoneInfo(TZ_NAME)


def http_json(method: str, url: str, body: dict | None = None, auth: bool = False) -> dict:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if auth:
        headers["Authorization"] = f"Bearer {HA_TOKEN}"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))


def wait_for_ha(max_attempts: int = 60, delay: int = 10) -> None:
    url = f"{HA_URL}/api/"
    for attempt in range(1, max_attempts + 1):
        try:
            result = http_json("GET", url, auth=True)
            log.info("Home Assistant pronto: %s", result.get("message", result))
            return
        except Exception as exc:  # noqa: BLE001 — retry genérico no boot
            log.warning(
                "Aguardando HA (%s/%s): %s",
                attempt,
                max_attempts,
                exc,
            )
            time.sleep(delay)
    log.error("Home Assistant não respondeu a tempo")
    sys.exit(1)


def fetch_temperature() -> float:
    params = urllib.parse.urlencode(
        {
            "latitude": LAT,
            "longitude": LON,
            "current": "temperature_2m",
            "timezone": TZ_NAME,
        }
    )
    url = f"https://api.open-meteo.com/v1/forecast?{params}"
    log.info("Consultando Open-Meteo (lat=%s lon=%s)", LAT, LON)
    payload = http_json("GET", url)
    temp = float(payload["current"]["temperature_2m"])
    observed = payload["current"].get("time", "?")
    log.info("Temperatura recebida: %.1f°C (observado em %s)", temp, observed)
    return temp


def get_switch_state() -> str:
    url = f"{HA_URL}/api/states/{ENTITY_ID}"
    payload = http_json("GET", url, auth=True)
    state = str(payload.get("state", "unknown"))
    log.info("Estado atual da tomada %s: %s", ENTITY_ID, state)
    return state


def set_switch(turn_on: bool, motivo: str) -> None:
    service = "turn_on" if turn_on else "turn_off"
    acao = "LIGAR" if turn_on else "DESLIGAR"
    url = f"{HA_URL}/api/services/switch/{service}"
    log.info(
        "Ação: %s tomada %s | motivo: %s | serviço HA: switch.%s",
        acao,
        ENTITY_ID,
        motivo,
        service,
    )
    http_json("POST", url, body={"entity_id": ENTITY_ID}, auth=True)
    log.info("Comando confirmado enviado ao Home Assistant.")


def in_window(now: datetime) -> bool:
    # Janela [WINDOW_START, WINDOW_END): ex. 10h inclusive até 17h exclusive.
    return WINDOW_START <= now.hour < WINDOW_END


def seconds_until_next_window_start(now: datetime) -> int:
    """Segundos até o próximo WINDOW_START (ex.: 10h), no mínimo 1s."""
    target = now.replace(hour=WINDOW_START, minute=0, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return max(1, int((target - now).total_seconds()))


def ensure_switch_off() -> None:
    state = get_switch_state()
    if state == "on":
        set_switch(
            False,
            motivo=f"fora da janela {WINDOW_START:02d}:00–{WINDOW_END:02d}:00 (forçar desligar)",
        )
    else:
        log.info(
            "Ação: NENHUMA | fora da janela e tomada já desligada (estado=%s).",
            state,
        )


def tick() -> int:
    """Executa um ciclo e devolve quantos segundos dormir até o próximo."""
    now = datetime.now(TZ)
    log.info(
        "--- Ciclo iniciado | %s | janela=%02d:00–%02d:00 | limiares on≥%.0f off≤%.0f ---",
        now.strftime("%Y-%m-%d %H:%M:%S %Z"),
        WINDOW_START,
        WINDOW_END,
        TEMP_ON,
        TEMP_OFF,
    )

    if not in_window(now):
        log.info(
            "Horário fora da janela operacional. Desligamento obrigatório se ligada; "
            "próxima avaliação de temperatura só às %02d:00.",
            WINDOW_START,
        )
        ensure_switch_off()
        purge_old_logs(now)
        sleep_for = seconds_until_next_window_start(now)
        retoma = now + timedelta(seconds=sleep_for)
        log.info(
            "Dormindo até %s (%ss). Sem checagens de temperatura nesse intervalo.",
            retoma.strftime("%Y-%m-%d %H:%M:%S"),
            sleep_for,
        )
        return sleep_for

    temp = fetch_temperature()
    state = get_switch_state()
    log.info(
        "Avaliação | temp=%.1f°C | switch=%s | regras: liga≥%.0f desliga≤%.0f",
        temp,
        state,
        TEMP_ON,
        TEMP_OFF,
    )

    if temp >= TEMP_ON and state == "off":
        set_switch(
            True,
            motivo=f"temperatura {temp:.1f}°C ≥ limiar de ligar {TEMP_ON:.0f}°C e tomada estava off",
        )
    elif temp <= TEMP_OFF and state == "on":
        set_switch(
            False,
            motivo=f"temperatura {temp:.1f}°C ≤ limiar de desligar {TEMP_OFF:.0f}°C e tomada estava on",
        )
    else:
        if TEMP_OFF < temp < TEMP_ON:
            razao = (
                f"histerese: {temp:.1f}°C está entre {TEMP_OFF:.0f}°C e {TEMP_ON:.0f}°C "
                f"(mantém estado={state})"
            )
        elif temp >= TEMP_ON and state == "on":
            razao = f"já ligada e temp {temp:.1f}°C ≥ {TEMP_ON:.0f}°C"
        elif temp <= TEMP_OFF and state == "off":
            razao = f"já desligada e temp {temp:.1f}°C ≤ {TEMP_OFF:.0f}°C"
        else:
            razao = f"estado={state} compatível com temp={temp:.1f}°C"
        log.info("Ação: NENHUMA | %s", razao)

    next_at = now + timedelta(seconds=POLL_SECONDS)
    end_today = now.replace(hour=WINDOW_END, minute=0, second=0, microsecond=0)
    if next_at >= end_today:
        sleep_for = max(1, int((end_today - now).total_seconds()))
        log.info(
            "Próximo ciclo ajustado para o fim da janela (%02d:00) em %ss (desligar se ligada).",
            WINDOW_END,
            sleep_for,
        )
        return sleep_for

    log.info("Próximo ciclo em %ss.", POLL_SECONDS)
    return POLL_SECONDS


def main() -> None:
    log.info(
        "Iniciando worker | entity=%s | on≥%.0f off≤%.0f | poll=%ss | "
        "janela=%02d–%02d | coords=%s,%s | logs=%s | retenção=%s dias",
        ENTITY_ID,
        TEMP_ON,
        TEMP_OFF,
        POLL_SECONDS,
        WINDOW_START,
        WINDOW_END,
        LAT,
        LON,
        LOG_DIR,
        LOG_RETENTION_DAYS,
    )
    purge_old_logs()
    wait_for_ha()
    while True:
        sleep_for = POLL_SECONDS
        try:
            sleep_for = tick()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            log.error("HTTP %s ao chamar API: %s | corpo: %s", exc.code, exc.reason, body)
        except Exception as exc:  # noqa: BLE001 — não derrubar o loop
            log.exception("Erro no ciclo: %s", exc)
        time.sleep(sleep_for)


if __name__ == "__main__":
    main()
