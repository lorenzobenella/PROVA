# Garmin dashboard

Scarica i tuoi dati dalla **Garmin Connect Personal API** ufficiale (sola lettura)
e costruisce una dashboard HTML statica. Solo librerie standard di Python (3.10+).

## La chiave

Lo script la cerca in quest'ordine:

1. variabile d'ambiente `GARMIN_PERSONAL_API_KEY` (da usare nelle sessioni cloud di Claude Code);
2. file `.env.local` in questa cartella, riga `GARMIN_PERSONAL_API_KEY=la-tua-chiave`
   (senza spazi né virgolette). Va bene sul tuo computer.

La chiave non viene mai stampata. `.env.local`, `data/` e `dashboard.html` sono in
`.gitignore`: contengono la chiave o i tuoi dati di salute e non devono finire su GitHub.

## Comandi

| Comando | Cosa fa |
| --- | --- |
| `python test_connection.py --check` | dice solo se la chiave è impostata |
| `python test_connection.py` | prova la chiave e mostra le ultime 5 attività |
| `python pull.py` | scarica gli ultimi 12 mesi; le volte dopo solo il nuovo |
| `python pull.py --all` | scarica tutto lo storico |
| `python pull.py --fit ID` | scarica il file FIT originale di un'attività |
| `python build.py` | costruisce `dashboard.html` dai dati in `data/` |
| `./refresh.sh` (Mac/Linux) o `refresh.bat` (Windows) | `pull.py` + `build.py` |

## Come sono salvati i dati

- `data/activities/AAAA-MM.json`: le attività di ogni mese, per `activityId`.
- `data/daily|sleep|hrv|weight/<da>_<a>.json`: le risposte grezze di Garmin, una
  finestra per file (60 giorni i riepiloghi, 31 il sonno, 365 HRV e peso). Una
  finestra scaricata almeno 3 giorni dopo la sua fine non viene più riscaricata.
- `data/hrzones.json`, `data/fitnessage.json`: zone cardio ed età fitness.
- `data/token.json`: il token di accesso (24 ore), rinnovato da solo.

Se Garmin risponde 500 lo script dimezza l'intervallo e riprova; su 429/502/503
aspetta e riprova; una risposta 204 vuol dire "nessun dato" e non è un errore.
