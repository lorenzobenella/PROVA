# Cycling & Health Analytics

App locale (self-hosted) che si collega al tuo account **Strava** e al tuo
account **Garmin Connect** per analizzare le tue prestazioni ciclistiche e il
tuo stato di salute/recupero in un'unica dashboard.

- **Strava** → fonte delle attività in bici (potenza, frequenza cardiaca,
  distanza, dislivello) via OAuth2 ufficiale.
- **Garmin Connect** → fonte dei dati di salute/benessere (sonno, HRV, body
  battery, stress, training readiness) via login diretto, perché Garmin non
  offre un'API pubblica per uso personale.

Tutti i dati vengono scaricati e salvati in un database SQLite **locale**
(`backend/data/app.db`) — nessun dato lascia la tua macchina se non verso
Strava/Garmin stessi.

## Cosa calcola

- **Curva di potenza** (best power a 5s / 1min / 5min / 20min / 60min) dagli
  stream delle attività Strava.
- **FTP stimata** (95% della miglior potenza a 20 minuti, ultimi 90 giorni).
- **Training load** per attività: TSS se hai un misuratore di potenza,
  altrimenti una stima hrTSS basata su frequenza cardiaca.
- **Performance Management Chart** (CTL/ATL/TSB, alla Coggan/TrainingPeaks)
  per vedere forma, fatica e fitness nel tempo.
- **Punteggio di prontezza** giornaliero (0-100) che combina training
  readiness Garmin, sonno, HRV, body battery e stress.

## Setup

### 1. Requisiti

- Python 3.11+

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env
```

### 2. Configura Strava

1. Crea un'app su <https://www.strava.com/settings/api>.
2. Come "Authorization Callback Domain" usa `localhost`.
3. Copia Client ID e Client Secret in `backend/.env`:

```
STRAVA_CLIENT_ID=...
STRAVA_CLIENT_SECRET=...
STRAVA_REDIRECT_URI=http://localhost:8000/api/auth/strava/callback
```

### 3. Garmin Connect

Non serve configurare nulla in anticipo: il login con email e password (più
eventuale codice MFA) si fa direttamente dalla tab "Collega account"
dell'app. Sotto il cofano viene usata la libreria non ufficiale
[`garminconnect`](https://github.com/cyberjunky/python-garminconnect), dato
che Garmin non fornisce un'API pubblica per account personali.

### 4. Avvia l'app

```bash
cd backend
uvicorn app.main:app --reload
```

Apri <http://localhost:8000>, vai su "Collega account", collega Strava e
Garmin, poi premi "Sincronizza ora". Le prime sincronizzazioni scaricano
fino a 180 giorni di attività Strava e 30 giorni di dati Garmin.

## Server MCP (collegare Claude ai dati Garmin)

Oltre alla dashboard, il progetto espone i dati di benessere Garmin come
**server MCP**, così da poterli interrogare direttamente da Claude (o da un
altro client MCP) in linguaggio naturale.

Serve a colmare un buco preciso: Garmin divide il programma sviluppatori in
API attività e **Health API**, e quest'ultima richiede un'approvazione a
parte. I connettori basati sull'API ufficiale riescono quindi a leggere le
attività ma restituiscono valori vuoti per sonno, HRV, Body Battery, stress,
SpO2 e training readiness. Questo server legge quelle metriche tramite lo
stesso client non ufficiale `garminconnect` già usato dall'app.

### 1. Autenticati una volta

Il server non può chiedere il codice MFA da solo, quindi il login si fa una
volta sola e produce un token store riutilizzabile:

```bash
cd backend
python -m app.garmin_login
export GARMIN_TOKEN_STORE=~/.garminconnect
```

In alternativa, se hai già collegato Garmin dalla dashboard, il server
riusa automaticamente la sessione salvata nel database SQLite locale.

### 2. Registra il server nel client MCP

```json
{
  "mcpServers": {
    "garmin-wellness": {
      "command": "/percorso/assoluto/PROVA/backend/.venv/bin/python",
      "args": ["-m", "app.mcp_server"],
      "cwd": "/percorso/assoluto/PROVA/backend",
      "env": { "GARMIN_TOKEN_STORE": "/home/tuo-utente/.garminconnect" }
    }
  }
}
```

### 3. Tool disponibili

| Tool | Cosa restituisce |
| --- | --- |
| `get_wellness_snapshot` | Tutte le metriche di recupero di un giorno |
| `get_sleep_summary` | Durata, fasi (profondo/leggero/REM/sveglio) e punteggio |
| `get_hrv_status` | HRV notturna, media settimanale e baseline personale |
| `get_body_battery` | Massimo, minimo, energia caricata e consumata |
| `get_stress` | Stress medio/massimo e tempo per livello |
| `get_spo2` | Saturazione notturna media e minima |
| `get_training_readiness` | Punteggio 0-100 e fattori che lo compongono |
| `get_daily_stats` | Frequenza cardiaca a riposo, passi, calorie |
| `get_connection_status` | Diagnostica: quali metriche stanno rispondendo |

Tutti i tool a serie storica accettano `from_date`, `to_date` e `limit`
(default 7 giorni, massimo 100) e restituiscono i giorni dal più recente. I
giorni senza dati vengono omessi invece di essere riempiti di zeri, così un
orologio non indossato non viene scambiato per un valore reale.

Garmin va interrogato un giorno alla volta, quindi un intervallo costa una
richiesta per giorno: le richieste vengono parallelizzate su 4 thread
(`GARMIN_MCP_MAX_WORKERS`), un compromesso fra reattività e rispetto di un
endpoint non ufficiale.

## Struttura del progetto

```
backend/
  app/
    main.py            FastAPI app + serve il frontend
    config.py           Config da .env
    db.py / models.py   SQLite + SQLAlchemy
    strava_client.py    OAuth2 + REST verso Strava
    garmin_client.py    Login (con MFA) + fetch dati Garmin
    mcp_server.py       Server MCP che espone i dati di benessere
    garmin_login.py     Login interattivo one-off -> token store per MCP
    sync.py             Orchestrazione sync + calcolo training load
    analysis.py         Curva di potenza, FTP, TSS/hrTSS, PMC, readiness
    routes_auth.py       /api/auth/*
    routes_api.py        /api/cycling/*, /api/health/*, /api/sync
frontend/
  index.html, static/    Dashboard (vanilla JS + Chart.js, nessuna build)
```

## Note

- Attività non-bici vengono ignorate (Strava): `Ride`, `VirtualRide`,
  `GravelRide`, `MountainBikeRide`, `EBikeRide`, `Handcycle`.
- Garmin a volte richiede un secondo passaggio MFA al primo login: l'app
  gestisce questo flusso nella tab "Collega account".
- Le formule di training load/PMC sono stime standard nel ciclismo
  (TrainingPeaks-style), utili per individuare trend — non sostituiscono un
  test di soglia in laboratorio o il parere di un allenatore.
