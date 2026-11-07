"""Ottiene il refresh token Strava per il coach (da fare una volta sola, sul tuo computer).

    export STRAVA_CLIENT_ID=...  STRAVA_CLIENT_SECRET=...
    python -m coach.strava_auth url          stampa il link da aprire nel browser
    python -m coach.strava_auth token CODICE scambia il codice con il refresh token

Dopo "Autorizza" Strava ti manda su http://localhost/?...&code=CODICE: la pagina non si
apre, ed e' normale. Copia il valore di code dalla barra degli indirizzi.
Il refresh token va nel secret STRAVA_REFRESH_TOKEN del repository: non incollarlo in chat.
"""

import json
import os
import sys
import urllib.parse
import urllib.request

AUTHORIZE = "https://www.strava.com/oauth/authorize"
TOKEN = "https://www.strava.com/oauth/token"


def main(argv):
    cid, secret = os.environ.get("STRAVA_CLIENT_ID"), os.environ.get("STRAVA_CLIENT_SECRET")
    if not cid or (argv[:1] == ["token"] and not secret):
        print("Imposta STRAVA_CLIENT_ID e STRAVA_CLIENT_SECRET (li trovi su strava.com/settings/api).")
        return 1
    if argv[:1] == ["url"]:
        print(AUTHORIZE + "?" + urllib.parse.urlencode({
            "client_id": cid, "redirect_uri": "http://localhost", "response_type": "code",
            "approval_prompt": "force", "scope": "read,activity:read_all"}))
        return 0
    if argv[:1] == ["token"] and len(argv) == 2:
        body = urllib.parse.urlencode({"client_id": cid, "client_secret": secret, "code": argv[1],
                                       "grant_type": "authorization_code"}).encode()
        with urllib.request.urlopen(urllib.request.Request(TOKEN, data=body), timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        print("Refresh token (mettilo nel secret STRAVA_REFRESH_TOKEN):")
        print(data["refresh_token"])
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
