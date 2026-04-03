# E-bloc România — Integrare Home Assistant

[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2025.11%2B-41BDF5?logo=homeassistant&logoColor=white)](https://www.home-assistant.io/)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/cnecrea/e-bloc.ro)](https://github.com/cnecrea/e-bloc.ro/releases)
[![GitHub Stars](https://img.shields.io/github/stars/cnecrea/e-bloc.ro?style=flat&logo=github)](https://github.com/cnecrea/e-bloc.ro/stargazers)
[![Instalări](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/cnecrea/e-bloc.ro/main/statistici/shields/descarcari.json)](https://github.com/cnecrea/e-bloc.ro)
[![Ultima versiune](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/cnecrea/e-bloc.ro/main/statistici/shields/ultima_release.json)](https://github.com/cnecrea/e-bloc.ro/releases/latest)

Integrare custom pentru [Home Assistant](https://www.home-assistant.io/) care aduce datele din aplicația **[e-bloc.ro](https://www.e-bloc.ro/)** (by Xisoft) direct în dashboard-ul tău. Monitorizează contoare, facturi, plăți, tichete, și trimite indecși sau număr de persoane — totul fără a deschide aplicația mobilă.

---

## Caracteristici principale

- **Autodiscovery complet** — asociațiile, apartamentele și contoarele sunt descoperite automat la autentificare.
- **10 tipuri de senzori** cu atribute detaliate în limba română.
- **Trimitere indecși** direct din Home Assistant (number selector + buton), cu fallback pe ultimul index valid.
- **Trimitere nr. persoane** direct din Home Assistant, cu fallback pe ultima valoare declarată.
- **Interval actualizare configurabil** — de la 12 ore (implicit) la 24 ore.
- **Sistem de licențiere** cu perioadă de evaluare gratuită.
- **Diagnosticare integrată** — export complet pentru depanare (fără date sensibile).
- **Multi-cont** — suport pentru mai mulți utilizatori simultani.
- **Logging detaliat** — fiecare trimitere de index sau nr. persoane este logată pas cu pas.
- **Sistem de licență** — evaluare gratuită la prima instalare, apoi licență validă pentru funcționalitate completă.

---

## Entități create

### Senzori (`sensor.*`)

Fiecare senzor urmează formatul `sensor.ebloc_{UserID}_{nume_senzor}`.

---

#### 1. Licență necesară

Apare **doar** când licența lipsește sau a expirat. Dispare automat la activarea licenței.

| Câmp | Valoare exemplu |
|------|----------------|
| **Stare** | `Evaluare (30 zile)` / `Licență necesară` / `Licență expirată` |
| **Atribute** | Fingerprint, status, tip licență, cache valid |

---

#### 2. Cont utilizator
`sensor.ebloc_{UserID}_cont_utilizator`

| Câmp | Valoare exemplu |
|------|----------------|
| **Stare** | `utilizator@exemplu.ro` |
| **ID utilizator** | `12345` |
| **Titlu** | `Dl.` |
| **Nume** | `Popescu` |
| **Prenume** | `Ion` |
| **Email** | `utilizator@exemplu.ro` |
| **Țară** | `Romania` |
| **Regiune** | `Timiș` |
| `--- Conexiune actuală` | *(separator)* |
| **Oraș** | `Timișoara` |
| **Latitudine** | `45.7489` |
| **Longitudine** | `21.2087` |

---

#### 3. Asociație
`sensor.ebloc_{UserID}_asociatie_{IdAsoc}`

| Câmp | Valoare exemplu |
|------|----------------|
| **Stare** | `7890` *(ID-ul asociației)* |
| **ID asociație** | `7890` |
| **Nume asociație** | `Asociația de proprietari Exemplu 15` |
| **CUI** | `RO12345678` |
| **Adresă** | `Str. Exemplu, nr. 15, bl. A, sc. 1, Timișoara, jud. Timiș, cod 300100` |
| **Oraș** | `Timișoara` |
| **Sector** | *(doar pentru București)* |
| **Județ** | `Timiș` |
| `--- Președinte` | *(separator)* |
| **Administrator** | `Admin Exemplu SRL` |
| **Președinte** | `Ionescu Gheorghe` |
| **Cenzor** | `Marinescu Ana` |
| **Contabil** | `Georgescu Maria` |
| `--- Încasări` | *(separator)* |
| **Locul încasărilor** | `Sediul asociației, luni-vineri 10:00-14:00` |
| **Telefon** | `0256 123 456` |
| **Email asociație** | `asociatie@exemplu.ro` |
| **Avertisment** | *(mesaj opțional de la asociație)* |

---

#### 4. Index contor (per contor)
`sensor.ebloc_{UserID}_index_{titlu_slug}`

Câte un senzor per contor (apă rece, apă caldă, gaz, etc.). Unitate de măsură: **m³**. Clasă de status: **Total în creștere**.

**Logica valorii principale:** senzorul afișează `index_nou` dacă acesta există și nu este null, altfel revine la `index_vechi`. Astfel, imediat după trimiterea unui index nou, senzorul reflectă noua valoare.

| Câmp | Valoare exemplu |
|------|----------------|
| **Stare** | `340.000 m³` |
| **ID contor** | `91` |
| **Denumire contor** | `AR bucătărie` |
| **Tip contor** | `Apă rece` |
| **Index actual** | `340.000 m³` |
| **Index nou** | `340.000 m³` / `Nesetat` |
| **Editare permisă** | `Da` / `Nu` |
| **Drept editare** | `Da` / `Nu` |
| **Serie contor** | `ABC123456` |
| **Etichetă contor** | `2653485016` |
| **Estimat** | `Nu` |

Tipuri contor: `1` = Apă rece, `2` = Apă caldă, `3` = Gaz, `4` = Energie.

> **Notă:** Atributul „Index actual" înlocuiește fostul „Index vechi" începând cu versiunea 1.1.0. Acesta reflectă valoarea curentă relevantă, nu neapărat `index_vechi` din API.

---

#### 5. Factură restantă
`sensor.ebloc_{UserID}_factura_restanta`

| Câmp | Valoare exemplu |
|------|----------------|
| **Stare** | `Da` / `Nu` |
| **Restanță luna februarie** | `375.05 lei` |
| **Restanță luna martie** | `280.00 lei` |
| `--- Detalii factură` | *(separator)* |
| **Încălzire februarie** | `150.00 lei` |
| **Apă rece februarie** | `45.05 lei` |
| **Fond reparații februarie** | `180.00 lei` |
| `--- Total restanțe` | *(separator)* |
| **Total** | `655.05 lei` |

---

#### 6. Arhivă plăți
`sensor.ebloc_{UserID}_arhiva_plati`

Afișează ultimele 12 plăți (chitanțe).

| Câmp | Valoare exemplu |
|------|----------------|
| **Stare** | `12` *(numărul de plăți afișate)* |
| **Total plăți** | `12` |
| **Plătită pe 15 februarie 2026** | `278.31 lei` |
| **Plătită pe 18 ianuarie 2026** | `312.50 lei` |
| **Plătită pe 20 decembrie 2025** | `295.00 lei` |

---

#### 7. Număr persoane
`sensor.ebloc_{UserID}_numar_persoane`

| Câmp | Valoare exemplu |
|------|----------------|
| **Stare** | `2` *(numărul de persoane declarate)* |

Acest senzor nu are atribute secundare — doar valoarea principală.

---

#### 8. Tichete
`sensor.ebloc_{UserID}_tichete`

Afișează primele 5 tichete cu detalii de status (deschis/închis) preluate individual.

| Câmp | Valoare exemplu |
|------|----------------|
| **Stare** | `3` *(numărul total de tichete)* |
| **Total tichete** | `3` |
| **Tichet ID 305715** | `Închis` |
| **Tichet ID 305820** | `Deschis` |
| **Tichet ID 306001** | `Închis` |

---

#### 9. Carduri salvate
`sensor.ebloc_{UserID}_carduri_salvate`

| Câmp | Valoare exemplu |
|------|----------------|
| **Stare** | `2` *(numărul de carduri)* |
| **Total carduri** | `2` |
| **Card 1 — Bancă** | `Banca Transilvania` |
| **Card 1 — Ultimele 4 cifre** | `4523` |
| **Card 1 — Data expirare** | `12/28` |
| **Card 1 — ID** | `78901` |
| `---` | *(separator)* |
| **Card 2 — Bancă** | `ING Bank` |
| **Card 2 — Ultimele 4 cifre** | `8901` |
| **Card 2 — Data expirare** | `03/27` |
| **Card 2 — ID** | `78902` |

---

#### 10. Citire permisă
`sensor.ebloc_{UserID}_citire_permisa`

Determină dacă poți transmite indecșii în perioada curentă. Verificarea primară se face pe baza datelor calendaristice (`indecsi_start` ≤ azi ≤ `indecsi_end`), cu fallback pe flag-ul `can_edit_index` din API.

| Câmp | Valoare exemplu |
|------|----------------|
| **Stare** | `Da` / `Nu` |
| **Perioadă start** | `2026-03-25 00:00:00` |
| **Perioadă sfârșit** | `2026-04-05 23:59:59` |
| **Data scadență** | `2026-04-10` |
| **Flag API (can_edit_index)** | `Da` / `Nu` |

---

### Butoane (`button.*`)

Butoanele sunt disponibile doar cu licență validă. Fiecare acțiune este logată detaliat (nivel `info`).

#### Trimite index (per contor)
`button.ebloc_{UserID}_trimite_index_{titlu_slug}`

Preia valoarea din **number selector** (Index contor) și o trimite la API-ul e-bloc.ro. Apare doar pentru contoarele cu `right_edit_index = 1`. După trimitere, coordinatorul face refresh automat și senzorul se actualizează.

#### Trimite nr. persoane
`button.ebloc_{UserID}_trimite_nr_persoane`

Preia valoarea din **number selector** (Nr. persoane) și o trimite la API. Luna este calculată **dinamic** — se folosește mereu luna curentă, nu o valoare stale din inițializare.

---

### Number selectors (`number.*`)

Selectoarele sunt locale — modificarea valorii **nu afectează** senzorul corespunzător și **nu trimite** date la API. Trimiterea se face exclusiv prin butonul asociat.

#### Index contor (selector)
`number.ebloc_{UserID}_index_{titlu_slug}_selector`

Selector local cu unitate **m³**. Permite introducerea noului index (0–999999.999, pas 0.001). La inițializare, pornește cu ultimul index disponibil din API (`index_nou` dacă există, altfel `index_vechi`) — dacă butonul este apăsat din greșeală, retrimite ultima valoare validă în loc de 0.

#### Nr. persoane (selector)
`number.ebloc_{UserID}_nr_persoane_selector`

Selector local (0–10, pas 1). La inițializare, pornește cu ultima valoare `nr_pers` din API — aceeași logică de fallback ca la selectorul de index.

---

## Exemple de automatizări

### 1. Notificare când ai factură restantă

```yaml
automation:
  - alias: "Notificare factură restantă e-bloc"
    trigger:
      - platform: state
        entity_id: sensor.ebloc_12345_factura_restanta
        to: "Da"
    action:
      - service: notify.mobile_app_telefonul_meu
        data:
          title: "Factură restantă e-bloc"
          message: >
            Ai o restanță de {{ state_attr('sensor.ebloc_12345_factura_restanta', 'Total') }}.
            Verifică detaliile în aplicație.
```

### 2. Notificare când se deschide perioada de citire contoare

```yaml
automation:
  - alias: "Perioada de citire contoare s-a deschis"
    trigger:
      - platform: state
        entity_id: sensor.ebloc_12345_citire_permisa
        to: "Da"
    action:
      - service: notify.mobile_app_telefonul_meu
        data:
          title: "Citire contoare permisă"
          message: >
            Perioada de transmitere a indecșilor s-a deschis!
            Termen limită: {{ state_attr('sensor.ebloc_12345_citire_permisa', 'Perioadă sfârșit') }}.
            Nu uita să transmiți indecșii.
```

### 3. Notificare când se închide perioada (cu 2 zile înainte)

```yaml
automation:
  - alias: "Avertizare termen citire contoare"
    trigger:
      - platform: template
        value_template: >
          {% set end = state_attr('sensor.ebloc_12345_citire_permisa', 'Perioadă sfârșit') %}
          {% if end %}
            {{ (as_datetime(end) - now()).days == 2 }}
          {% else %}
            false
          {% endif %}
    action:
      - service: notify.mobile_app_telefonul_meu
        data:
          title: "Termen citire contoare!"
          message: "Mai ai 2 zile pentru a transmite indecșii contoarelor!"
```

### 4. Trimitere automată indecși când e perioadă deschisă

```yaml
automation:
  - alias: "Trimite indecși automat"
    trigger:
      - platform: state
        entity_id: sensor.ebloc_12345_citire_permisa
        to: "Da"
    condition:
      - condition: template
        value_template: >
          {{ states('number.ebloc_12345_index_ar_bucatarie_selector') | float > 0 }}
    action:
      - service: button.press
        target:
          entity_id: button.ebloc_12345_trimite_index_ar_bucatarie
```

### 5. Notificare plată nouă

```yaml
automation:
  - alias: "Plată nouă înregistrată"
    trigger:
      - platform: state
        entity_id: sensor.ebloc_12345_arhiva_plati
    condition:
      - condition: template
        value_template: >
          {{ trigger.to_state.state | int > trigger.from_state.state | int }}
    action:
      - service: notify.mobile_app_telefonul_meu
        data:
          title: "Plată înregistrată"
          message: "O nouă plată a fost înregistrată în e-bloc."
```

### 6. Notificare tichet nou

```yaml
automation:
  - alias: "Tichet nou e-bloc"
    trigger:
      - platform: state
        entity_id: sensor.ebloc_12345_tichete
    condition:
      - condition: template
        value_template: >
          {{ trigger.to_state.state | int > trigger.from_state.state | int }}
    action:
      - service: notify.mobile_app_telefonul_meu
        data:
          title: "Tichet nou"
          message: >
            Ai {{ states('sensor.ebloc_12345_tichete') }} tichete.
            Verifică statusul în Home Assistant.
```

### 7. Dashboard card — rezumat apartament

```yaml
type: entities
title: "Apartament e-bloc"
entities:
  - entity: sensor.ebloc_12345_asociatie_7890
    name: "Asociație"
  - entity: sensor.ebloc_12345_numar_persoane
    name: "Persoane declarate"
  - entity: sensor.ebloc_12345_citire_permisa
    name: "Citire permisă"
  - type: divider
  - entity: sensor.ebloc_12345_index_ar_bucatarie
    name: "Index apă rece bucătărie"
  - entity: sensor.ebloc_12345_index_ac_baie
    name: "Index apă caldă baie"
  - type: divider
  - entity: sensor.ebloc_12345_factura_restanta
    name: "Restanțe"
  - entity: sensor.ebloc_12345_arhiva_plati
    name: "Plăți recente"
```

---

## Logging

Butoanele de trimitere (index și nr. persoane) logează fiecare pas al fluxului: apăsare, lookup entity, stare curentă, trimitere API, răspuns server, succes sau eșec. Nivelul implicit este `info` pentru fluxul normal și `warning` doar pentru erori reale.

Pentru a vizualiza logurile, adaugă în `configuration.yaml`:

```yaml
logger:
  default: warning
  logs:
    custom_components.ebloc: info
```

---

## Cerințe

- **Home Assistant** 2024.1.0 sau mai nou
- **HACS** instalat (opțional, dar recomandat)
- **Cont e-bloc.ro** activ — [www.e-bloc.ro](https://www.e-bloc.ro/)
- **Licență** validă — [hubinteligent.org/donate?ref=ebloc](https://hubinteligent.org/donate?ref=ebloc) (sau perioada de evaluare gratuită)

---

## Instalare

Consultă **[SETUP.md](SETUP.md)** pentru ghidul complet de instalare și configurare.

## Depanare

Consultă **[DEBUG.md](DEBUG.md)** pentru ghidul de depanare.

---

## Structura fișierelor

```
custom_components/ebloc/
├── __init__.py          # Inițializare integrare + lifecycle licență
├── api.py               # Client API e-bloc.ro (REST, 14 endpoint-uri)
├── button.py            # Butoane (trimite index, trimite nr. persoane)
├── config_flow.py       # ConfigFlow + OptionsFlow (reconfigurare, licență)
├── const.py             # Constante (URL-uri, intervale, sentinel values)
├── coordinator.py       # DataUpdateCoordinator (fetch paralel, luna corectă contoare)
├── diagnostics.py       # Export diagnosticare (date mascate)
├── helpers.py           # Funcții helper (conversii date, mascare email)
├── license.py           # Sistem licențiere (Ed25519 + HMAC-SHA256)
├── manifest.json        # Manifest integrare (v1.1.0)
├── number.py            # Number selectors (index contor, nr. persoane)
├── sensor.py            # 10 tipuri de senzori cu atribute românești
├── strings.json         # Traduceri interfață
├── translations/
│   ├── en.json
│   └── ro.json
└── brand/
    ├── icon.png          # 256×256
    ├── icon@2x.png       # 512×512
    ├── logo.png          # 256×256
    ├── logo@2x.png       # 512×512
    ├── dark_icon.png     # 256×256
    ├── dark_icon@2x.png  # 512×512
    ├── dark_logo.png     # 256×256
    └── dark_logo@2x.png  # 512×512
```

---

## Suport

- **Issues**: [github.com/cnecrea/e-bloc.ro/issues](https://github.com/cnecrea/e-bloc.ro/issues)
- **Documentație**: [github.com/cnecrea/e-bloc.ro](https://github.com/cnecrea/e-bloc.ro)
- **Licență**: [hubinteligent.org/licenta/ebloc](https://hubinteligent.org/licenta/ebloc)

---

## Licență

Această integrare necesită o cheie de licență pentru funcționalitate completă. O perioadă de evaluare gratuită este disponibilă la prima instalare. Gestionarea licenței se face din **Setări → Integrări → E-bloc România → Configurare → Licență**.

Dezvoltat de **Ciprian Nicolae** ([@cnecrea](https://github.com/cnecrea)).
