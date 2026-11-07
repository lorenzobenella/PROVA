# Allenatore virtuale

Ogni 4 ore legge i tuoi dati Garmin (e Strava, se lo colleghi), decide l'allenamento del
giorno e te lo manda sul telefono, con i file `.zwo` / `.erg` da caricare sul Wahoo KICKR.
Obiettivo: alzare **FTP** e **VO2max** con 1 ora al giorno in settimana e 2 nel weekend,
più forza con bilanciere e manubri.

Gira su GitHub Actions, gratis, senza server. Usa solo la libreria standard di Python.

## Come ragiona

**Ciclo di 12 settimane**, che poi ricomincia con la FTP aggiornata. Tre blocchi da 4
settimane: 3 di carico crescente + 1 di scarico che finisce con il ramp test.

| | lun | mar | mer | gio | ven | sab (2 h) | dom (2 h) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **1. VO2max + forza** | Forza A pesante | VO2max lunghi | Z2 | Soglia | Forza B leggera | 30/15 + Z2 | Z2 |
| **2. Soglia (FTP)** | Forza mantenimento | Over-under | Z2 | VO2max mantenimento | Riposo | Sweet spot lungo | Z2 |
| **3. VO2max + soglia** | Forza + esplosività | VO2max lunghi | Z2 | Soglia | Riposo | Soglia lunga | Z2 |
| **Scarico (4ª sett.)** | Forza leggera | Richiamo 3×3' | Z2 45' | Z2 45' | Riposo | **Ramp test** | Z2 90' |

Le settimane di carico valgono circa 410-440 TSS (7,5-8,5 ore), quelle di scarico circa 240.
Le ripetute crescono di settimana in settimana (es. VO2max: 5×3' → 5×4' → 6×4').

**Ogni mattina** la seduta prevista viene adattata alla **prontezza** (si parte da 100 e si
tolgono punti):

| Segnale (da Garmin) | Penalità |
| --- | --- |
| HRV stato LOW / POOR, o sotto la tua fascia | da -15 a -35 |
| Sonno sotto 6,5 h / 6 h / 5 h, o punteggio sotto 60 / 50 | da -8 a -25 |
| Media delle ultime 3 notti sotto 6 h | -10 |
| Battito a riposo +4 / +6 bpm sulla tua mediana di 14 giorni | -8 / -15 |
| Body Battery sotto 50 / 35 | -6 / -12 |
| Forma (TSB) sotto -20 / -30 | -10 / -25 |

- 🟢 **verde** (80+): la seduta come prevista;
- 🟡 **gialla** (60-79): stessa seduta, circa il 40% di ripetute in meno e durata -25%;
  il ramp test viene rimandato;
- 🔴 **rossa** (sotto 60): solo 30-40' in Z1, oppure mobilità al posto della forza.

**Sedute saltate.** Se ieri era una seduta chiave e non risulta fatta (meno del 50% del TSS
previsto), oggi è un giorno facile, domani non è una seduta chiave e sei in verde, il coach
la recupera oggi. Altrimenti la lascia perdere: inseguire le sedute perse accumula fatica.

**Ramp test.** Il coach legge da Garmin il tuo miglior minuto del giorno del test e calcola la
FTP (75%). Se è entro ±12% dalla precedente la aggiunge da solo a
`garmin-dashboard/ftp_tests.json` e tutti i watt si aggiornano; altrimenti te lo segnala.

**Notifiche.** Il coach gira ogni 4 ore ma scrive solo quando serve:
- dalle 19 l'anteprima di domani (così ce l'hai anche se ti alleni alle 6);
- la mattina, quando sonno e HRV sono sincronizzati, solo se la seduta cambia;
- dopo l'allenamento, il riepilogo e la seduta di domani.

## Messa in funzione

1. **Rendi il repository privato** (Settings → General → Danger Zone → Change visibility).
   Il piano contiene sonno, HRV e battito: in un repository pubblico il coach non lo salva
   e ti arriva solo via Telegram/ntfy. In più, nei repository pubblici GitHub spegne le
   esecuzioni programmate dopo 60 giorni senza commit.
2. **Chiave Garmin.** Settings → Secrets and variables → Actions → New repository secret:
   `GARMIN_PERSONAL_API_KEY`. Se hai incollato la chiave in una chat, generane una nuova e
   usa quella.
3. **Notifiche** (consigliato Telegram):
   - scrivi a **@BotFather** su Telegram → `/newbot` → copia il token nel secret
     `TELEGRAM_BOT_TOKEN`;
   - manda un messaggio qualsiasi al tuo bot, poi apri
     `https://api.telegram.org/bot<TOKEN>/getUpdates` e copia il numero `"chat":{"id":...}`
     nel secret `TELEGRAM_CHAT_ID`;
   - in alternativa: app **ntfy**, iscriviti a un nome lungo e casuale e mettilo nel secret
     `NTFY_TOPIC` (chi conosce il nome legge i messaggi).
4. **Unisci questo lavoro nel branch principale del repository**: GitHub esegue i workflow
   programmati solo da lì.
5. Prima prova: Actions → **Allenatore** → Run workflow.

### Strava (facoltativo)

Oggi non serve: tutte le tue uscite sono già su Garmin. Se registri qualcosa solo su
Strava (es. Zwift), crea un'app su <https://www.strava.com/settings/api> (dominio di callback
`localhost`), poi sul tuo computer:

```bash
export STRAVA_CLIENT_ID=... STRAVA_CLIENT_SECRET=...
python -m coach.strava_auth url            # apri il link e autorizza
python -m coach.strava_auth token CODICE   # il codice è nella barra degli indirizzi
```

e salva `STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET` e `STRAVA_REFRESH_TOKEN` come secret.
Le uscite che Garmin ha già (stesso orario di inizio ±10') non vengono contate due volte.

## Personalizzare

Tutto in [`atleta.toml`](atleta.toml), modificabile anche dall'app GitHub:
- `disponibilita`: minuti per giorno della settimana;
- `eccezioni`: un giorno diverso dal solito, es. `"2026-11-21" = 60` o `= 0` se salti;
- `piano.inizio`: il lunedì della settimana 1; `piano.blocchi`: l'ordine dei blocchi.

Le sedute si adattano da sole ai minuti: prima tolgono ripetute, poi accorciano il lavoro,
e il tempo che avanza diventa Z2. La forza tiene gli esercizi principali.

## Uscite

- `out/PIANO.md`: oggi, prossimi 6 giorni, settimana in corso, indicatori (FTP, W/kg, FTP
  stimata dal miglior 20', VO2max stimato dal miglior 5', CTL/ATL/TSB, ore reali contro ore
  previste) e zone di potenza;
- `out/allenamenti/*.zwo` (Zwift, MyWhoosh, TrainingPeaks Virtual, Rouvy...) e `*.erg`
  (watt assoluti, quasi tutte le app per rulli) per oggi e domani;
- `out/piano.json`: lo stesso in formato leggibile da un programma.

## In locale

```bash
cd garmin-dashboard && python pull.py && cd ..        # scarica i dati (serve la chiave)
python -m coach --offline --no-notify                 # piano di oggi dai dati scaricati
python -m coach --offline --date 2026-11-03 --hour 7  # simula un altro giorno
python -m unittest discover -s coach/tests -t .       # test
```

## Limiti

- La Personal API di Garmin è in sola lettura: il coach non può mettere la seduta nel
  calendario Garmin o nell'app Wahoo. Per questo produce i file `.zwo`/`.erg`.
- La Personal API non espone il VO2max dell'orologio né la Training Readiness di Garmin:
  il VO2max è stimato dal miglior 5' (Sitko et al. 2021) e la prontezza è calcolata qui.
- Il TSS della forza è una stima (0,6 per minuto).
- Sono regole di allenamento standard, non un parere medico: dolori, malattia o febbre
  vincono su qualsiasi semaforo verde.
