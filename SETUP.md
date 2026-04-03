# SETUP.md — Ghid de instalare și configurare

## Cerințe

- **Home Assistant** 2024.1.0 sau mai nou.
- **HACS** (Home Assistant Community Store) instalat — [instrucțiuni HACS](https://hacs.xyz/docs/use/).
- Un cont activ pe **[e-bloc.ro](https://www.e-bloc.ro/)** (aplicația de administrare pentru asociațiile de proprietari).
- Cel puțin o asociație cu un apartament înregistrat în contul e-bloc.ro.
- **Licență** validă — [hubinteligent.org/donate?ref=ebloc](https://hubinteligent.org/donate?ref=ebloc) (perioadă de evaluare gratuită de 30 zile disponibilă la prima instalare).

---

## Metoda 1: Instalare prin HACS (recomandată)

### Pasul 1 — Adaugă repository-ul custom

1. Deschide Home Assistant → **HACS** → **Integrations**.
2. Click pe **⋮** (meniu, colț dreapta-sus) → **Custom repositories**.
3. Completează:
   - **Repository**: `https://github.com/cnecrea/e-bloc.ro`
   - **Category**: `Integration`
4. Click **Add**.

### Pasul 2 — Instalează integrarea

1. În **HACS → Integrations**, caută `E-bloc România`.
2. Click pe ea → **Download** → confirmă versiunea → **Download**.
3. **Repornește Home Assistant** (Settings → System → Restart).

### Pasul 3 — Configurează integrarea

1. Mergi la **Settings → Devices & Services → Add Integration**.
2. Caută `E-bloc România` sau `ebloc`.
3. Completează:
   - **Email**: adresa de email a contului e-bloc.ro.
   - **Parolă**: parola contului.
   - **Interval actualizare** (opțional): implicit 43200 secunde (12 ore). Acceptă valori între 43200 (12h) și 86400 (24h).
4. Click **Submit**.

Integrarea va:
- Autentifica contul la serverul e-bloc.ro.
- Descoperi automat asociațiile și apartamentele.
- Crea toți senzorii, butoanele și number selector-ii.

### Pasul 4 — Licență

Integrarea oferă o **perioadă de evaluare gratuită** la prima instalare. Fără o licență validă, se creează doar senzorul „Licență necesară".

Pentru a introduce o licență:
1. **Settings → Devices & Services → E-bloc România**
2. Click pe **Configure**
3. Selectează **Licență**
4. Introdu cheia de licență
5. Click **Submit**

Licențe disponibile la: [hubinteligent.org/licenta/ebloc](https://hubinteligent.org/licenta/ebloc)

---

## Metoda 2: Instalare manuală

### Pasul 1 — Descarcă fișierele

1. Descarcă arhiva ZIP de pe [GitHub Releases](https://github.com/cnecrea/e-bloc.ro/releases).
2. Extrage conținutul.

### Pasul 2 — Copiază în Home Assistant

Copiază folderul `ebloc` în directorul de integrări custom:

```
/config/custom_components/ebloc/
```

Structura finală trebuie să arate:

```
/config/custom_components/ebloc/
├── __init__.py
├── api.py
├── button.py
├── config_flow.py
├── const.py
├── coordinator.py
├── diagnostics.py
├── helpers.py
├── license.py
├── manifest.json
├── number.py
├── sensor.py
├── strings.json
└── translations/
    ├── en.json
    └── ro.json
```

### Pasul 3 — Repornește și configurează

1. Repornește Home Assistant.
2. Urmează pașii din **Metoda 1, Pasul 3** (adaugă integrarea din UI).

---

## Activarea licenței

Integrarea oferă o **perioadă de evaluare gratuită** la prima instalare. După expirare, senzorii vor afișa „Licență necesară".

### Pentru a activa o licență:

1. Mergi la **Settings → Devices & Services → E-bloc România**.
2. Click pe **Configure**.
3. Selectează **Licență**.
4. Introdu cheia de licență (format: `EBLO-XXXX-XXXX-XXXX-XXXX`).
5. Click **Submit**.

Statusul licenței va fi afișat:
- ✅ **Licență activă** — funcționalitate completă.
- ⏳ **Evaluare** — X zile rămase.
- ❌ **Licență expirată** — trebuie reînnoită.

---

## Configurarea intervalului de actualizare

Intervalul implicit este de **12 ore** (43200 secunde). Datele de pe e-bloc.ro nu se schimbă frecvent, așa că un interval de 12–24 ore este suficient.

### Modificare prin reconfigurare:

1. **Settings → Devices & Services → E-bloc România → Configure → Reconfigurare cont**.
2. Modifică câmpul **Interval actualizare (secunde)**.
3. Valori acceptate: `43200` (12h) – `86400` (24h).
4. Confirmă. Integrarea se va reîncărca automat.

---

## Multi-cont

Integrarea suportă mai multe conturi e-bloc.ro simultan. Fiecare cont creează un set separat de entități cu UserID unic.

1. **Settings → Devices & Services → Add Integration → E-bloc România**.
2. Introdu credențialele celui de-al doilea cont.
3. Entitățile vor avea prefixul `ebloc_{UserID2}_...` diferit.

---

## Ce se întâmplă după configurare

### Entități create automat

| Tip | Entitate | Descriere |
|-----|----------|-----------|
| **Sensor** | `sensor.ebloc_{UID}_cont_utilizator` | Date cont (email, nume, locație) |
| **Sensor** | `sensor.ebloc_{UID}_asociatie_{IdAsoc}` | Informații asociație (adresă, conducere) |
| **Sensor** | `sensor.ebloc_{UID}_index_{titlu}` | Index contor (m³) per contor descoperit |
| **Sensor** | `sensor.ebloc_{UID}_factura_restanta` | Factură restantă (Da/Nu + breakdown) |
| **Sensor** | `sensor.ebloc_{UID}_arhiva_plati` | Ultimele 12 plăți |
| **Sensor** | `sensor.ebloc_{UID}_numar_persoane` | Nr. persoane declarate |
| **Sensor** | `sensor.ebloc_{UID}_tichete` | Tichete/contact |
| **Sensor** | `sensor.ebloc_{UID}_carduri_salvate` | Carduri bancare salvate |
| **Sensor** | `sensor.ebloc_{UID}_citire_permisa` | Perioadă citire contoare (Da/Nu) |
| **Number** | `number.ebloc_{UID}_index_{titlu}_selector` | Selector local index (m³) |
| **Number** | `number.ebloc_{UID}_nr_persoane_selector` | Selector local nr. persoane |
| **Button** | `button.ebloc_{UID}_trimite_index_{titlu}` | Trimite indexul la API |
| **Button** | `button.ebloc_{UID}_trimite_nr_persoane` | Trimite nr. persoane la API |

### Dispozitiv

Toate entitățile sunt grupate sub un singur dispozitiv:
- **Nume**: `E-bloc România ({UserID})`
- **Producător**: `Ciprian Nicolae (cnecrea)`
- **Model**: `E-bloc România`

---

## Dezinstalare

### Din HACS
1. **HACS → Integrations → E-bloc România → ⋮ → Remove**.
2. Repornește Home Assistant.

### Manuală
1. **Settings → Devices & Services → E-bloc România → ⋮ → Delete**.
2. Șterge folderul `/config/custom_components/ebloc/`.
3. Repornește Home Assistant.

---

## Următorii pași

- Consultă **[README.md](README.md)** pentru descrierea completă a senzorilor și exemple de automatizări.
- Consultă **[DEBUG.md](DEBUG.md)** dacă întâmpini probleme.
- Raportează bug-uri pe [GitHub Issues](https://github.com/cnecrea/e-bloc.ro/issues).
