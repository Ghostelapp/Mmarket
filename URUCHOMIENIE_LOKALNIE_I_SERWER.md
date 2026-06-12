# MASK Market — uruchomienie lokalnie i na serwerze (krok po kroku)

Ten dokument opisuje **pełne uruchomienie** aplikacji:
- mobilny frontend Expo (`/frontend`)
- backend FastAPI (`/backend`)
- panel admina Next.js (`/admin`)
- baza MongoDB

---

## 1) Wymagania

## Lokalnie
- Node.js 20+
- Yarn 1.x
- Python 3.11+
- MongoDB 6+
- Git

## Serwer (VPS)
- Ubuntu 22.04/24.04
- Domena (np. `api.twojadomena.pl`, `admin.twojadomena.pl`)
- Nginx
- Certbot (SSL)

---

## 2) Struktura projektu

```bash
/app
  /backend     # FastAPI
  /frontend    # Expo mobile app
  /admin       # Next.js admin panel
```

---

## 3) Uruchomienie lokalne — krok po kroku

## Krok 1: Klon repo i wejście do projektu

```bash
git clone <URL_REPO> mask-market
cd mask-market
```

## Krok 2: Konfiguracja backend `.env`

Plik: `backend/.env`

```env
MONGO_URL="mongodb://localhost:27017"
DB_NAME="mask_market"
JWT_SECRET="USTAW_DLUGI_LOSOWY_SEKRET"
TWO_FA_ENCRYPTION_KEY="USTAW_ODDZIELNY_DLUGI_LOSOWY_SEKRET"
APP_ENV="development"
CORS_ALLOWED_ORIGINS="http://localhost:3000,http://localhost:8081,http://localhost:19006"
RATE_LIMIT_AUTH_PER_MINUTE="40"
RATE_LIMIT_GENERAL_PER_MINUTE="140"
API_PAGE_MAX_LIMIT="200"
ADMIN_PAGE_MAX_LIMIT="500"

# Konto administratora jest tworzone tylko przy jawnym włączeniu.
SEED_ADMIN="true"
ADMIN_EMAIL="admin@example.com"
ADMIN_PASSWORD="USTAW_UNIKALNE_HASLO_MINIMUM_16_ZNAKOW"

# W trybie lokalnych testów można jawnie włączyć symulację. Nigdy w produkcji.
ALLOW_INSECURE_PAYMENT_SIMULATION="true"
ENABLE_ONCHAIN_INDEXER="false"
VERIFY_CONTRACTS_ON_STARTUP="false"
VERIFY_CONTRACTS_FAIL_CLOSED="false"
CONTRACT_VERIFY_TIMEOUT_SECONDS="30"
RPC_REQUEST_TIMEOUT_SECONDS="15"
ALCHEMY_API_KEY_BASE=""
ALCHEMY_API_KEY_POLYGON=""
ALCHEMY_WEBHOOK_SIGNING_KEY=""
PLATFORM_WALLET_BASE=""
PLATFORM_WALLET_POLYGON=""
ESCROW_CONTRACT_BASE=""
ESCROW_CONTRACT_POLYGON=""

WEBAUTHN_RP_ID="localhost"
WEBAUTHN_RP_NAME="MASK Market"
WEBAUTHN_ALLOWED_ORIGINS="http://localhost:8081,http://localhost:19006"

ENABLE_R2_STORAGE="false"
R2_ACCOUNT_ID=""
R2_ACCESS_KEY_ID=""
R2_SECRET_ACCESS_KEY=""
R2_BUCKET_NAME=""
R2_PUBLIC_BASE_URL=""

ENABLE_AV_SCAN="false"
CLAMAV_COMMAND="clamscan"
```

## Krok 3: Konfiguracja frontend `.env`

Plik: `frontend/.env`

```env
EXPO_PUBLIC_BACKEND_URL=http://localhost:8001
```

> Jeśli testujesz na fizycznym telefonie przez Expo Go, użyj IP komputera, np.:
> `EXPO_PUBLIC_BACKEND_URL=http://192.168.1.20:8001`

## Krok 4: Konfiguracja panelu admina

Plik: `admin/.env.local`

```env
NEXT_PUBLIC_BACKEND_URL=http://localhost:8001
```

## Krok 5: Instalacja zależności

## Backend
```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cd ..
```

## Frontend
```bash
cd frontend
yarn install
cd ..
```

## Admin
```bash
cd admin
yarn install
cd ..
```

## Krok 6: Uruchom MongoDB

Lokalnie (systemowo):
```bash
sudo systemctl start mongod
sudo systemctl status mongod
```

## Krok 7: Uruchom backend

```bash
cd backend
source .venv/bin/activate
uvicorn server:app --host 0.0.0.0 --port 8001 --reload
```

Backend health:
```bash
curl http://localhost:8001/api/
```

Pole `contract_verification.status` ma wartość `healthy`, `degraded`, `checking`
albo `disabled`. Domyślnie błąd lub timeout RPC nie blokuje restartu API, ale
ustawia stan `degraded`. Ustaw `VERIFY_CONTRACTS_FAIL_CLOSED=true` wyłącznie,
jeśli świadomie chcesz przerwać start usługi przy błędzie weryfikacji.

Lokalne zdjęcia nie są katalogiem publicznym. Endpoint `/api/media/...`
sprawdza status oferty oraz sesję właściciela/admina. W produkcji zalecane jest
`ENABLE_R2_STORAGE=true`; linki podpisane R2 są ważne przez 5 minut.

## Krok 8: Uruchom frontend Expo

W nowym terminalu:

```bash
cd frontend
yarn start
```

Uruchom aplikację:
- `a` Android emulator
- `i` iOS simulator (macOS)
- Expo Go (QR code)
- web preview

## Krok 9: Uruchom panel admina

W nowym terminalu:

```bash
cd admin
yarn dev
```

Panel admina dostępny pod:
`http://localhost:3000`

---

## 4) Konto administratora

Nie istnieją domyślne dane logowania. Przy pierwszym uruchomieniu ustaw `SEED_ADMIN=true`,
`ADMIN_EMAIL` i unikalne `ADMIN_PASSWORD`, uruchom backend, a następnie wyłącz `SEED_ADMIN`.
Po pierwszym logowaniu włącz 2FA.

---

## 5) Uruchomienie na serwerze (VPS) — krok po kroku

Poniżej produkcyjny schemat: backend + admin jako usługi systemd, Nginx jako reverse proxy.

## Krok 1: Pakiety systemowe

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git nginx python3-venv python3-pip nodejs npm certbot python3-certbot-nginx
sudo npm i -g yarn
```

## Krok 2: Aplikacja i katalogi

```bash
sudo mkdir -p /opt/mask-market
sudo chown -R $USER:$USER /opt/mask-market
cd /opt/mask-market
git clone <URL_REPO> .
```

## Krok 3: Backend (FastAPI) na serwerze

```bash
cd /opt/mask-market/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Ustaw `backend/.env` (jak w sekcji lokalnej, ale z produkcyjnymi wartościami).

Utwórz usługę systemd:

`sudo nano /etc/systemd/system/mask-backend.service`

```ini
[Unit]
Description=MASK Market Backend
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/opt/mask-market/backend
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/mask-market/backend/.venv/bin/uvicorn server:app --host 0.0.0.0 --port 8001 --workers 2
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable mask-backend
sudo systemctl start mask-backend
sudo systemctl status mask-backend
```

## Krok 4: Admin panel (Next.js) na serwerze

```bash
cd /opt/mask-market/admin
yarn install
yarn build
```

Utwórz `admin/.env.local`:

```env
NEXT_PUBLIC_BACKEND_URL=https://api.twojadomena.pl
```

Utwórz usługę systemd:

`sudo nano /etc/systemd/system/mask-admin.service`

```ini
[Unit]
Description=MASK Market Admin Panel
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/opt/mask-market/admin
Environment=NODE_ENV=production
ExecStart=/usr/bin/yarn start
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable mask-admin
sudo systemctl start mask-admin
sudo systemctl status mask-admin
```

## Krok 5: Nginx reverse proxy

`sudo nano /etc/nginx/sites-available/mask-market`

```nginx
server {
    listen 80;
    server_name api.twojadomena.pl;

    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

server {
    listen 80;
    server_name admin.twojadomena.pl;

    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/mask-market /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

Jeśli publikujesz eksport webowy Expo, plik `frontend/public/_headers` jest
obsługiwany przez hostingi takie jak Netlify i Cloudflare Pages. Przy innym
hostingu ustaw równoważne nagłówki HTTP ręcznie, szczególnie:
`Content-Security-Policy` z `frame-ancestors 'none'`, `X-Frame-Options: DENY`,
`X-Content-Type-Options: nosniff` i `Referrer-Policy: no-referrer`. Sam tag
`meta` CSP nie potrafi wymusić `frame-ancestors`.

## Krok 6: SSL (Let’s Encrypt)

```bash
sudo certbot --nginx -d api.twojadomena.pl -d admin.twojadomena.pl
```

## Krok 7: Firewall

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
```

---

## 6) Podpięcie aplikacji mobilnej do serwera produkcyjnego

W `frontend/.env` ustaw:

```env
EXPO_PUBLIC_BACKEND_URL=https://api.twojadomena.pl
```

Następnie:

```bash
cd frontend
yarn start
```

---

## 7) Integracje (Alchemy / R2 / AV) — aktywacja

Po uzupełnieniu kluczy w `backend/.env`:

- `APP_ENV=production`
- `ALLOW_INSECURE_PAYMENT_SIMULATION=false`
- `SEED_ADMIN=false` po jednorazowym utworzeniu administratora
- jawne `CORS_ALLOWED_ORIGINS` i `WEBAUTHN_ALLOWED_ORIGINS`, wyłącznie adresy HTTPS
- oddzielne, losowe `JWT_SECRET` i `TWO_FA_ENCRYPTION_KEY` (minimum 32 znaki)
- `ENABLE_ONCHAIN_INDEXER=true`
- `ENABLE_R2_STORAGE=true`
- `ENABLE_AV_SCAN=true` (jeśli ClamAV jest gotowy)

Frontend wywołuje kontrakty płatności i escrow bezpośrednio z portfela, a backend
weryfikuje zarówno transfer USDC, jak i zdarzenie kontraktu powiązane z unikalną
referencją. Nie zastępuj tej integracji zwykłym transferem USDC na adres kontraktu.

Kompletne kontrakty, konfiguracja testnet/mainnet oraz procedura deployu są w
[`WDROZENIE_BLOCKCHAIN_TESTNET.md`](./WDROZENIE_BLOCKCHAIN_TESTNET.md).

### Migracja czatu do E2EE

Nowe wiadomości używają przypiętego klucza X25519 konta, zaszyfrowanych kopert
klucza Deal Room oraz uwierzytelnionego `NaCl secretbox`. Backend i administrator
nie posiadają kluczy prywatnych ani tekstu jawnego.

Przed pierwszym uruchomieniem produkcyjnym po aktualizacji usuń stare wiadomości,
które były jedynie kodowane Base64:

```bash
cd backend
python scripts/purge_legacy_chat.py --confirm-permanent-delete
```

Backend produkcyjny odmówi startu, jeśli wykryje stare wiadomości bez E2EE.
Klucz konta jest przypinany metodą TOFU. Utrata lokalnego klucza prywatnego
oznacza utratę dostępu do historii; nie wolno go cicho zastępować.

Restart backendu:

```bash
sudo systemctl restart mask-backend
```

---

## 8) Najczęstsze problemy

## 1. CORS / brak połączenia z API
- Sprawdź `EXPO_PUBLIC_BACKEND_URL`
- Sprawdź czy backend działa na `:8001`

## 2. Admin nie loguje
- Sprawdź `NEXT_PUBLIC_BACKEND_URL`
- Sprawdź logi `mask-admin` i `mask-backend`

## 3. WebAuthn/passkeys nie działa
- Sprawdź `WEBAUTHN_RP_ID` i `WEBAUTHN_ALLOWED_ORIGINS`
- Upewnij się, że origin dokładnie zgadza się z domeną

## 4. Upload zdjęć nie trafia do R2
- Sprawdź `ENABLE_R2_STORAGE=true`
- Sprawdź klucze R2 i nazwę bucketu

---

## 9) Przydatne komendy diagnostyczne

```bash
# backend
sudo systemctl status mask-backend
sudo journalctl -u mask-backend -n 200 --no-pager

# admin
sudo systemctl status mask-admin
sudo journalctl -u mask-admin -n 200 --no-pager

# nginx
sudo nginx -t
sudo systemctl status nginx
sudo tail -n 200 /var/log/nginx/error.log
```

---

## 10) Checklist „gotowe do startu”

- [ ] MongoDB działa
- [ ] Backend działa i odpowiada na `/api/`
- [ ] Frontend Expo łączy się z backendem
- [ ] Admin panel loguje się poprawnie
- [ ] SSL aktywny na API i admin
- [ ] Klucze Alchemy/R2 ustawione (jeśli używasz)

Gotowe ✅
