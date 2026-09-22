# Pentax Wi-Fi Auto
# Prompt WIFI PENTAX

Usługa dla Alpine Linux, która łączy się z punktem dostępowym aparatu Pentax K-70, pobiera zdjęcia i udostępnia je przez Samba oraz Jellyfin. Po jednorazowej konfiguracji usługa uruchamia się razem z serwerem, ponawia połączenie po wybudzeniu i sama wznawia synchronizację, gdy aparat oraz jego Wi-Fi są dostępne.

Ścieżki, interfejs Wi-Fi, adres aparatu, filtrowanie dat, folder JPEG, timeouty i harmonogramy są ustawiane w `/etc/pentax-sync.env`. Część aparatu korzysta z Pentaxowego API `/v1`, więc inne urządzenie musi udostępniać zgodne endpointy i format odpowiedzi.

## Jak to działa

1. Usługa OpenRC próbuje połączyć kartę `wlan0` z Wi-Fi aparatu. Interfejs Ethernet `eth0` nadal obsługuje LAN i domyślną trasę.
2. Usługa sprawdza aparat pod adresem `http://192.168.0.1`. Co 2 sekundy sprawdza najnowsze zdjęcie, a pełną listę karty aparatu odświeża co 60 sekund.
3. Pobiera zdjęcia JPG/JPEG, DNG i PEF. Stan zapisuje w SQLite, więc po restarcie rozpoznaje pobrane pliki i ponawia nieudane próby.
4. Zapisuje pobierany plik jako `.part`, sprawdza rozmiar i format, a dopiero po poprawnym pobraniu zmienia jego nazwę na docelową. Przerwana transmisja jest ponawiana od początku; transfer nie jest wznawiany od przerwanego miejsca. Niekompletny plik nie jest pokazywany jako gotowe zdjęcie.
5. Po pobraniu RAW-a tworzy JPEG-owy podgląd z JPEG-a osadzonego w RAW-ie. Podgląd nie jest pełnym wywołaniem RAW-a i dziedziczy balans bieli oraz wyostrzenie ustawione w aparacie.
6. Prosi Jellyfin o odświeżenie biblioteki po nowych zdjęciach oraz co 5 minut. Podczas długiego transferu sprawdza, czy pora na odświeżenie, po każdym zakończonym pliku.

Usługa nie usuwa plików z karty aparatu.

## Gdzie trafiają pliki

Katalog synchronizacji na serwerze:

```text
/home/daniel/media/fotex/DIRECT/
├── _IMG0001.DNG
├── _IMG0002.PEF
└── _jpeg/
    ├── _IMG0001.jpg
    └── _IMG0002.jpg
```

RAW-y pozostają w `DIRECT`, a JPEG-i z aparatu i podglądy wygenerowane z RAW-ów trafiają do `DIRECT/_jpeg`. Pliki widać w udziale Samba pod adresem:

```text
\\192.168.1.150\daniel\media\fotex\DIRECT
```

W Jellyfin biblioteka `DIRECT` powinna wskazywać na `/home/daniel/media/fotex/DIRECT`. Odświeżanie skanuje zawartość tej biblioteki, w tym folder `_jpeg`.

## Ustawienia serwera

Aktywna konfiguracja znajduje się na serwerze w `/etc/pentax-sync.env` i zawiera hasło Wi-Fi oraz klucz API Jellyfin. Nie umieszczaj tych danych w repozytorium. Plik przykładowy `etc/pentax-sync.env.example` nie zawiera sekretów.

Najważniejsze opcje:

| Zmienna | Znaczenie | Przykład |
| --- | --- | --- |
| `CAMERA_SSID` | Nazwa sieci Wi-Fi aparatu | `PENTAX_9A9B62` |
| `CAMERA_WIFI_PASSWORD` | Hasło Wi-Fi aparatu | ustaw lokalnie na serwerze |
| `CAMERA_BASE_URL` | Adres API aparatu | `http://192.168.0.1` |
| `WIFI_INTERFACE` | Karta Wi-Fi używana do połączenia z aparatem | `wlan0` |
| `CAMERA_STATIC_ADDRESS` | Adres interfejsu Wi-Fi, gdy aparat nie przydzieli go przez DHCP | `192.168.0.2/24` |
| `UDHCPC_SCRIPT_PATH` | Ścieżka do pomocnika DHCP interfejsu aparatu | `/opt/pentax-sync/udhcpc-script` |
| `PHOTO_ROOT` | Katalog docelowy RAW-ów i folderu `_jpeg` | `/srv/pentax-sync/photos` |
| `JPEG_SUBDIR` | Nazwa podfolderu na oryginalne JPEG-i i podglądy RAW; pusta wartość zapisuje je obok RAW-ów | `_jpeg` |
| `PHOTO_DATE_FROM` | Włącznie akceptowana data wykonania zdjęcia; pusta wartość wyłącza filtr | `2026-07-20` |
| `INITIAL_SYNC` | Zachowanie przy pierwszej inwentaryzacji karty: `baseline` pomija zastane pliki, `all` importuje je | `baseline` |
| `RAW_PREVIEW_MAX_SIDE` | Maksymalny rozmiar dłuższego boku podglądu w pikselach | `3840` |
| `RAW_PREVIEW_QUALITY` | Jakość JPEG 1–100; niższa wartość oznacza mocniejszą kompresję | `70` |
| `JELLYFIN_URL` | Adres Jellyfin widziany przez serwer | `http://127.0.0.1:8096` |
| `JELLYFIN_API_KEY` | Klucz API używany do odświeżania biblioteki | ustaw lokalnie na serwerze |
| `JELLYFIN_REFRESH_DEBOUNCE` | Cisza po pobieraniu przed odświeżeniem | `10` sekund |
| `JELLYFIN_REFRESH_INTERVAL` | Okres między odświeżeniami biblioteki | `300` sekund |
| `POLL_INTERVAL` | Odstęp pomiędzy kontrolami usługi | `2` sekundy |
| `FULL_SCAN_INTERVAL` | Okres pełnej listy zdjęć z aparatu | `60` sekund |
| `CONNECT_RETRY_INTERVAL` | Maksymalny odstęp pomiędzy kolejnymi próbami połączenia | `12` sekund |
| `CAMERA_REQUEST_TIMEOUT` | Limit czasu zapytań API aparatu | `5` sekund |
| `CAMERA_DOWNLOAD_TIMEOUT` | Limit czasu pojedynczego transferu zdjęcia | `180` sekund |

Data `PHOTO_DATE_FROM` jest włącznie. Usługa pobiera datę wykonania ze szczegółów zdjęcia, jeśli lista aparatu jej nie zawiera. Po zmianie filtra wcześniej pominięte zdjęcia są sprawdzane ponownie.

Parametry `RAW_PREVIEW_MAX_SIDE` i `RAW_PREVIEW_QUALITY` dotyczą tylko dodatkowych JPEG-ów. Nie zmieniają RAW-ów ani JPEG-ów oryginalnych z aparatu. Zmiana ustawień nie przerabia automatycznie istniejących podglądów.

## Instalacja na Alpine Linux

Usługa wymaga Python 3, iwd, OpenRC oraz BusyBox `udhcpc`. Do generowania podglądów RAW potrzebne są `libraw-tools` i `ffmpeg`:

```sh
apk add git python3 iwd libraw-tools ffmpeg
```

Sklonuj repozytorium do katalogu aplikacji:


```sh
git clone https://github.com/danieldrozdzewicz/PENTAX_WIFI_AUTO.git /opt/pentax-sync
```

Utwórz konfigurację i zainstaluj usługę OpenRC:

```sh
cp /opt/pentax-sync/etc/pentax-sync.env.example /etc/pentax-sync.env
chmod 0600 /etc/pentax-sync.env
vi /etc/pentax-sync.env
cp /opt/pentax-sync/etc/init.d/pentax-sync /etc/init.d/pentax-sync
chmod 0755 /etc/init.d/pentax-sync /opt/pentax-sync/bin/pentax-sync
chmod 0755 /opt/pentax-sync/udhcpc-script
```

W `/etc/pentax-sync.env` ustaw nazwę sieci i hasło aparatu. Wklej tam również klucz API Jellyfin i dostosuj `PHOTO_ROOT`, `WIFI_INTERFACE`, `CAMERA_BASE_URL` oraz `CAMERA_STATIC_ADDRESS` do swojej sieci. Katalog `PHOTO_ROOT` jest tworzony automatycznie; udostępnij go przez Sambę i dodaj do biblioteki Jellyfin. Następnie włącz usługę przy starcie systemu:

```sh
PYTHONPATH=/opt/pentax-sync python3 -m pentax_sync --validate-config
rc-update add pentax-sync default
rc-service pentax-sync start
```

Usługa zapisuje profil iwd z uprawnieniami tylko dla roota. Pomocnik DHCP nie instaluje bramy domyślnej z Wi-Fi aparatu; połączenie LAN pozostaje na `eth0`.

## Aktualizacja

Zachowaj `/etc/pentax-sync.env`, bazę `/var/lib/pentax-sync/state.db` i pobrane zdjęcia. Zaktualizuj kod bez zastępowania konfiguracji:

```sh
rc-service pentax-sync stop
cd /opt/pentax-sync
git pull --ff-only
rc-service pentax-sync start
```

Po zmianie `PHOTO_DATE_FROM`, ustawień podglądu lub interwału odświeżania uruchom usługę ponownie. Zmiana jakości i rozmiaru dotyczy podglądów tworzonych od tego momentu; dotychczasowe pliki w `_jpeg` trzeba przerobić osobno, jeśli mają używać nowych ustawień.

## Codzienne użycie i kontrola

Po uruchomieniu serwera i włączeniu Wi-Fi aparatu nie trzeba ręcznie startować synchronizacji. Usługa działa w OpenRC, czeka na sieć aparatu, automatycznie ponawia połączenie i wyszukuje zdjęcia wykonane podczas jej niedostępności.

Przydatne polecenia:

```sh
rc-service pentax-sync status
PYTHONPATH=/opt/pentax-sync python3 -m pentax_sync --status
tail -f /var/log/pentax-sync.log
iwctl station wlan0 show
ip route
ls -la /home/daniel/media/fotex/DIRECT
ls -la /home/daniel/media/fotex/DIRECT/_jpeg
```

Status pokazuje połączenie z aparatem, liczbę pobranych plików, kolejkę błędów oraz stan Jellyfin. Pliki `.part` wskazują na niedokończoną próbę; zostaną nadpisane przy kolejnym pobieraniu.

## Rozwiązywanie problemów

**Usługa nie łączy się z aparatem**

- Sprawdź, czy na aparacie jest włączone Wi-Fi i aktywny punkt dostępowy.
- Na serwerze sprawdź `iwctl station wlan0 show` i upewnij się, że karta widzi lub łączy się z siecią aparatu.
- Sprawdź `CAMERA_SSID`, hasło oraz adres `CAMERA_BASE_URL` w `/etc/pentax-sync.env`.
- Sprawdź `ip route`: trasa domyślna powinna nadal prowadzić przez LAN, a nie `wlan0`.

**Zdjęcia są na dysku, ale nie widać ich w Jellyfin**

- Sprawdź, czy biblioteka Jellyfin `DIRECT` wskazuje na `/home/daniel/media/fotex/DIRECT`.
- Sprawdź, czy `JELLYFIN_API_KEY` jest ustawiony i poprawny oraz czy Jellyfin działa pod `JELLYFIN_URL`.
- Sprawdź log `/var/log/pentax-sync.log` pod kątem komunikatów `Jellyfin refresh`.
- JPEG-i są w `DIRECT/_jpeg`; sprawdź również ten folder w Jellyfin.

**Brakuje podglądów RAW**

- Upewnij się, że zainstalowano `libraw-tools` i `ffmpeg` (`command -v simple_dcraw ffmpeg`).
- Sprawdź, czy obok RAW-a powstał podgląd o tej samej nazwie w `DIRECT/_jpeg`.
- Podgląd jest wyciągany z JPEG-a osadzonego w RAW-ie. W tym trybie nie można niezależnie ustawić auto-balansu bieli ani wywołać RAW-a z pełną rozdzielczością matrycy.

## Pliki i katalogi systemowe

| Ścieżka | Zawartość |
| --- | --- |
| `/opt/pentax-sync` | Kod aplikacji |
| `/etc/pentax-sync.env` | Sekrety i konfiguracja; uprawnienia `0600` |
| `/etc/init.d/pentax-sync` | Usługa OpenRC |
| `/var/lib/pentax-sync/state.db` | Stan i historia synchronizacji |
| `/run/pentax-sync/status.json` | Bieżący status usługi |
| `/var/log/pentax-sync.log` | Log synchronizacji |
| `/home/daniel/media/fotex/DIRECT` | RAW-y i folder `_jpeg` |
