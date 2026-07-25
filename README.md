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

## Struttura del progetto

```
backend/
  app/
    main.py            FastAPI app + serve il frontend
    config.py           Config da .env
    db.py / models.py   SQLite + SQLAlchemy
    strava_client.py    OAuth2 + REST verso Strava
    garmin_client.py    Login (con MFA) + fetch dati Garmin
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
