# Casa Automação — HA + temperatura (VPS 1 GB)

Liga/desliga uma tomada Tuya via Home Assistant conforme a temperatura de **Santa Cruz do Sul - RS**.

- **Home Assistant** em Docker (limite ~700 MB RAM)
- **Worker Python** Alpine (~64 MB) que consulta [Open-Meteo](https://open-meteo.com/) (sem chave) e chama a API do HA
- Janela **10h–17h** (America/Sao_Paulo), checagem a cada **15 min**
- Liga se **≥ 28 °C**, desliga se **≤ 25 °C** (histerese entre os limiares)
- Às **17h** força desligar se estiver ligada; só volta a avaliar temperatura no dia seguinte às **10h**

## Requisitos

- Docker + Docker Compose
- ~1 GB RAM livre
- App **Smart Life** ou **Tuya Smart** com a tomada já cadastrada
- Conta no [Tuya IoT Platform](https://iot.tuya.com/) (para a integração oficial)

## Subir o stack

```bash
cp .env.example .env
# Deixe HA_TOKEN e ENTITY_ID como placeholder no primeiro boot

docker compose up -d homeassistant
```

Abra `http://SEU_IP:8123`, conclua o onboarding e siga os passos abaixo. Depois preencha o `.env` e suba o worker:

```bash
docker compose up -d
docker compose logs -f automacao
```

## Setup Home Assistant + Tuya

1. Crie o usuário no onboarding do HA.
2. **Token:** Perfil (canto inferior esquerdo) → **Security** → **Long-Lived Access Tokens** → Create Token. Cole em `HA_TOKEN` no `.env`.
3. **Integração Tuya:**
   - No app Smart Life/Tuya, anote o **User Code** (conta / settings).
   - Em [iot.tuya.com](https://iot.tuya.com/), crie um Cloud Project, vincule o app e autorize os dispositivos.
   - No HA: **Settings → Devices & Services → Add Integration → Tuya** e conclua o login.
4. **Entity ID:** **Developer Tools → States**, filtre por `switch.` e copie o id da tomada (ex.: `switch.tomada_sala`) para `ENTITY_ID` no `.env`.
5. Reinicie só o worker: `docker compose up -d automacao`

Sem Tuya configurada o HA sobe normalmente; a tomada só aparece depois da integração.

## Variáveis (`.env`)

| Variável | Padrão | Descrição |
|---|---|---|
| `HA_TOKEN` | — | Token de acesso do HA |
| `ENTITY_ID` | — | Ex.: `switch.minha_tomada` |
| `TEMP_ON` | 28 | Liga se temperatura ≥ este valor |
| `TEMP_OFF` | 25 | Desliga se temperatura ≤ este valor |
| `POLL_SECONDS` | 900 | Intervalo entre checagens (15 min) |
| `WINDOW_START` / `WINDOW_END` | 10 / 17 | Janela horária `[start, end)` |
| `LAT` / `LON` | SCS | Coordenadas Open-Meteo |
| `LOG_DIR` | `/logs` | Pasta de logs no container (`./logs` no host) |
| `LOG_RETENTION_DAYS` | 7 | Remove logs com mais de N dias |

O worker usa `HA_URL=http://homeassistant:8123` na rede Docker (definido no compose).

## Comportamento

- **10h–17h:** avalia temperatura a cada 15 min e liga/desliga conforme os limiares.
- **Às 17h:** se a tomada estiver ligada, desliga. Depois dorme até as **10h** do dia seguinte (sem novas checagens de temperatura).
- **Entre 25 °C e 28 °C** (dentro da janela): mantém o estado atual (histerese).
- Erros de API são logados; o loop continua.

## Logs

Arquivo detalhado em `./logs/automacao.log` (rotação diária à meia-noite).  
Backups antigos e arquivos com mais de **7 dias** são removidos automaticamente.

```bash
tail -f logs/automacao.log
```

## Comandos úteis

```bash
docker compose ps
docker compose logs -f homeassistant
docker compose logs -f automacao
docker stats homeassistant automacao   # conferir uso de RAM
docker compose down
```

## Segurança na VPS

A porta `8123` fica exposta para onboarding e uso. Em produção, restrinja por firewall ou coloque um reverse proxy (HTTPS). Não versione o arquivo `.env`.
