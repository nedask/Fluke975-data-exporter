# Fluke 975 Data Manager

A complete toolkit for downloading, exporting and graphing logged data from a
**Fluke 975 AirMeter** (firmware **1C**). Includes a modern GUI, a fast
command‑line downloader, and a compatibility wrapper that fixes several
long‑standing quirks of the meter's serial interface.

---

## 🇬🇧 English

### Overview

The Fluke 975 logs environmental measurements (temperature, humidity,
dewpoint, wetbulb, CO, CO₂) into internal memory as 32‑byte records,
grouped into 256‑byte blocks. This software talks to the meter over a
9600‑baud serial link and either:

- **Downloads everything** to a CSV file (`975dlall.py`, `fluke.py` GUI), or
- **Reads the session directory** and pulls each session by start block
  (`fluke975.py` legacy path).

Three separate tools are included so you can pick whichever suits you:

| File | What it does |
|------|--------------|
| `run_fluke975.py` | **Required by all tools.** Serial wrapper that fixes the Fluke's reset‑on‑open behaviour, pacing, and timeouts. Import it as a module, don't run it directly. |
| `fluke.py` | **Tkinter GUI** — connect, pick date range, export, inspect sessions, plot variables. |
| `975dlall.py` | **Fast CLI downloader** — forward/reverse block scan, `--since` filter, CSV output. |
| `fluke975.py` | **Legacy CLI** — reads the QD 0 session directory first, then downloads each session. Slower, but gives per‑session start addresses and record counts. |

---

### Requirements

- Python **3.8+**
- `pyserial` (`pip install pyserial`)
- `tkinter` (bundled with most Python installs; on Debian/Ubuntu: `sudo apt install python3-tk`)

**Linux permissions:** your user must be in the `dialout` group to open
serial ports without `sudo`:

```bash
sudo usermod -aG dialout $USER
# log out and back in
```

---

### Quick start (GUI)

```bash
python3 fluke.py
```

1. Plug the Fluke 975 in via USB.
2. The Fluke should appear in the **Port** dropdown — it's tagged `FLUKE`
   and auto‑selected.
3. Click **Connect**. The meter is reset by the open, so wait for the
   `● FLUKE 975 ...` badge to go green.
4. After connect, the app **auto‑scans the date range** and fills the
   `From` / `To` fields. You can override them or clear them.
5. Choose an output CSV path, then click **Export All**.
6. When finished, the **Sessions** table lists every detected session and
   the **Graph** tab auto‑loads the CSV.

Pass `--verbose` if something goes wrong:

```bash
python3 fluke.py --verbose
```

---

### Quick start (CLI — `975dlall.py`)

The fastest way to grab everything:

```bash
python3 975dlall.py --out fluke975.csv
```

Reverse scan (newest first, fast for large logs):

```bash
python3 975dlall.py --reverse --out fluke975.csv
```

Only records since a given date, using the reverse scan:

```bash
python3 975dlall.py --since 2025-01-01 --out fluke975.csv
```

Tune the timings if you have a slow meter:

```bash
python3 975dlall.py --silence-timeout 1.0 --read-timeout 3.0 --out fluke975.csv
```

Useful flags:

| Flag | Default | Meaning |
|------|---------|---------|
| `--out` | `fluke975.csv` | Output CSV file |
| `--since YYYY-MM-DD` | — | Only records on/after this date (forces reverse scan) |
| `--reverse` | off | Reverse scan without a `--since` cutoff |
| `--start-block` | `0` | First block to scan (forward) or highest block (reverse) |
| `--max-blocks` | `10000` | Upper bound for forward scan / reverse top probe |
| `--stop-after-empty` | `3` | Forward scan: stop after N consecutive no‑new blocks |
| `--silence-timeout` | `0.6` | Seconds of no response before declaring "no data" |
| `--read-timeout` | `2.5` | Max seconds per block |

---

### Quick start (legacy CLI — `fluke975.py`)

Uses the meter's own session directory. Slower, but you get exact
per‑session counts:

```bash
python3 fluke975.py --port /dev/ttyACM0 --output fluke975.csv
```

Output CSV includes `session`, `session_start`, `session_start_address`,
`session_record_count`, and every measurement field.

---

### Fluke 975 quirks you need to know

These are the reasons this toolkit exists. Understanding them will save
you a lot of confusion.

#### 1. **A "session" is 30 seconds apart (default)**

The meter has **no explicit session concept**. Every logged record is
just a 32‑byte entry with a BCD timestamp. Two consecutive records belong
to the **same session** only if their timestamps differ by **at most
30 seconds** (`--gap-seconds 30.0` in `975dlall.py`, `gap_seconds=30.0`
in the GUI).

If the gap between two records exceeds 30 s, a new session is started in
the CSV (`session` column increments).

**Consequences:**

- If you press *Log* on the meter twice within 30 seconds, the two runs
  will be merged into one session.
- If you forget the meter is logging and pick it up an hour later, you
  get one giant session. Set `--gap-seconds` higher (e.g. `300`) to force
  longer gaps to count as the same run.
- The GUI exposes this implicitly through the **Sessions** table — each
  row is one group separated by 30 s+ gaps.

#### 2. **Opening the serial port resets the meter**

The Fluke 975's USB‑serial bridge asserts DTR on open, which triggers a
reset on the meter. If you open the port and immediately send `ID\r`,
you get either nothing or garbage.

`run_fluke975.py` handles this with a **warm‑up open → close → wait →
reopen** cycle, then a short settle before the first command. If you ever
talk to the meter with raw `pyserial`, replicate this or you'll fight
ghost timeouts forever.

#### 3. **You must pace commands**

After a read, the meter needs a brief pause before the next write.
`run_fluke975.py` enforces this via `FLUKE_PREWRITE` (default `0.05` s in
the GUI, `0.3` s in standalone use). Hammering it will cause responses
to be dropped silently.

#### 4. **Silence ≠ error**

When you request a block that contains no data (past the newest written
block), the meter simply **doesn't respond**. This is normal. It is not a
timeout or a NAK.

The downloader uses this as a stop signal: if `--silence-timeout` (default
`0.6` s) elapses with zero bytes received, it treats that block as empty
and moves on. Set it shorter for faster scans; set it longer if your
meter is very slow to respond.

#### 5. **QD 2 responses are exactly 268 bytes**

```
"0\r"        (2 bytes, ack)
"QD 2"       (4 bytes, echo)
selector     (4 bytes, matches your request)
payload      (256 bytes = 8 × 32‑byte records)
CRC          (2 bytes, CRC‑16/MODBUS little‑endian)
```

`fetch_block()` in `975dlall.py` returns the instant 268 bytes have
arrived — there's no need to wait for a quiet line. If you get fewer than
268 bytes, the block is treated as bad and skipped.

#### 6. **CRC is CRC‑16/MODBUS**

Polynomial `0xA001`, initial `0xFFFF`, stored **little‑endian**. A bad
CRC on a block is not usually fatal — the block is just retried by the
scan logic (up to a streak of 3 errors before aborting).

#### 7. **Timestamps are BCD, not binary**

Each record's timestamp is six bytes: `HH MM SS DD MM YY`, each byte
in **binary‑coded decimal**. For example, byte `0x25` means *25*, not *37*.
If the meter's clock is wrong (dead coin cell, first power‑on), the
timestamps will be wrong too and nothing in the data can fix that.

#### 8. **Two record types live side by side**

| `record_type` | Meaning | CO₂ field |
|---------------|---------|-----------|
| `0x01D8` | Old record format | 32‑bit, bytes 20–23 |
| `0x03F8` | New record format | 16‑bit at bytes 20–21, plus 16‑bit `extra` at 22–23 |

The firmware version doesn't tell you which format you'll see — the meter
writes both. `975dlall.py` and the GUI handle both automatically. If
you're writing your own parser, check `record_type` and pick the right
offset.

#### 9. **Blocks are 256 bytes, records are 32 bytes → 8 records per block**

`BLOCK_RESPONSE_LEN = 268`, `PAYLOAD_RECORD_SIZE = 32`,
`PAYLOAD_RECORDS = 8`. A record whose `record` field is `0` is an empty
slot and is skipped during parsing.

#### 10. **Start addresses are 256‑byte aligned**

The QD 0 session directory gives each session a `start_address` that is
always a multiple of 256. Divide by 256 to get the starting block number
(`start_block = start_address // 256`). If you ever see a non‑aligned
address in QD 0, the response is corrupt — retry.

#### 11. **The meter doesn't tell you how many blocks it has**

There is no "get number of blocks" command. You have to **probe**:
`find_top_block()` in `975dlall.py` does an exponential probe
(1, 2, 4, 8, …) followed by a binary search to land on the highest
block that contains data. That's why reverse scans start with a short
"probe" phase before walking down.

#### 12. **The logging clock is the meter's clock**

If the Fluke's clock is wrong, all timestamps will be wrong. There is no
way to read or set the clock via this protocol. Fix it on the meter
itself before logging.

#### 13. **Meter temperature in 0.1 °C, RH in 0.01 %**

Temperature, dewpoint and wetbulb are **signed 16‑bit integers in tenths**
of a degree. RH is **unsigned 16‑bit in hundredths of a percent**. CO
is unsigned 16‑bit in ppm. CO₂ is either 16‑ or 32‑bit ppm depending on
record type (see #8).

#### 14. **Partial exports are saved on cancel**

Pressing **Cancel** in the GUI (or Ctrl‑C in the CLI) stops the scan but
still writes out every record collected so far. Use this to your
advantage on a huge log — you don't lose progress.

---

### Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `ID failed — power‑cycle the meter and retry` | Warm‑up open missed the reset, or meter not ready | Unplug USB, plug back in, wait 2 s, retry |
| `Permission denied: '/dev/ttyACM0'` | User not in `dialout` | `sudo usermod -aG dialout $USER`, log out/in |
| `No serial ports found` | USB‑serial bridge missing / kernel module not loaded | `dmesg \| tail`, `ls /dev/ttyACM*`, `ls /dev/serial/by-id/` |
| Every block reports `silence` | Meter has no data (fresh meter), or silence timeout too short | Try `--silence-timeout 1.5`; check the meter's own display |
| `3 consecutive errors — aborting` | USB flakiness, or wrong baud | Replug, verify `baud=9600` |
| Data shows plausible values but wrong dates | Meter clock is wrong | Set clock on the meter |
| GUI auto‑range scan cancelled on connect | You clicked Cancel during the probe | Click **Show available** again |
| `QD 0` CRC failure | Serial corruption | Increase `--silence-timeout`; re‑seat the cable |

If all else fails, run with verbose debug:

```bash
FLUKE_DEBUG=1 python3 975dlall.py --verbose --out test.csv
```

You'll see every byte on the wire.

---

### Output CSV schema

```
session, record_type, record, timestamp,
temperature_c, rh_percent, dewpoint_c, wetbulb_c,
co_ppm, co2_ppm, extra
```

- `session` — integer, incremented when the inter‑record gap exceeds
  `--gap-seconds` (default **30 s**)
- `record_type` — `0x01D8` or `0x03F8`
- `record` — meter's internal record number (non‑zero)
- `timestamp` — `YYYY‑MM‑DD HH:MM:SS` in meter's local time

---

## 🇱🇹 Lietuviškai

### Apžvalga

Fluke 975 orinis matuoklis (programinė aparatinė įranga **1C**) į vidinę
atmintį įrašo aplinkos matavimus (temperatūrą, drėgmę, rasos tašką,
šlapio termometro temperatūrą, CO, CO₂) kaip 32 baitų įrašus, sugrupuotus
į 256 baitų blokus. Ši programinė įranga bendrauja su matuokliu per
9600 bodų nuosekliąją jungtį ir gali:

- **Atsisiųsti viską** į CSV failą (`975dlall.py`, `fluke.py` GUI), arba
- **Perskaityti sesijų katalogą** ir atsisiųsti kiekvieną sesiją pagal
  pradinį bloką (`fluke975.py` senasis kelias).

Yra trys atskiri įrankiai — rinkitės patogiausią:

| Failas | Ką daro |
|--------|---------|
| `run_fluke975.py` | **Būtinas visiems įrankiams.** Nuosekliosios jungties apvalkalas, ištaisantis Fluke atsistatymą atidarant, komandų tempą ir laukimo laikus. Importuokite kaip modulį, nevykdykite tiesiogiai. |
| `fluke.py` | **Tkinter GUI** — prisijungimas, datų intervalo pasirinkimas, eksportas, sesijų peržiūra, grafikai. |
| `975dlall.py` | **Greitas CLI atsisiuntėjas** — tiesioginis/atbulinis blokų skenavimas, `--since` filtras, CSV išvestis. |
| `fluke975.py` | **Senasis CLI** — pirmiausia skaito QD 0 sesijų katalogą, tada atsisiunčia kiekvieną sesiją. Lėtesnis, bet pateikia kiekvienos sesijos pradžios adresus ir įrašų skaičių. |

---

### Reikalavimai

- Python **3.8+**
- `pyserial` (`pip install pyserial`)
- `tkinter` (paprastai būna kartu su Python; Debian/Ubuntu:
  `sudo apt install python3-tk`)

**Linux teisės:** jūsų naudotojas turi būti grupėje `dialout`, kad galėtų
atidaryti nuosekliąsias jungtis be `sudo`:

```bash
sudo usermod -aG dialout $USER
# atsijunkite ir prisijunkite iš naujo
```

---

### Greita pradžia (GUI)

```bash
python3 fluke.py
```

1. Prijunkite Fluke 975 per USB.
2. Matuoklis atsiras **Port** sąraše — pažymėtas `FLUKE` ir
   automatiškai pasirinktas.
3. Spauskite **Connect**. Atidarant jungtį matuoklis atsistato, todėl
   palaukite, kol ženkliukas `● FLUKE 975 ...` taps žalias.
4. Po prisijungimo programa **automatiškai nuskaito datų intervalą** ir
   užpildo `From` / `To` laukus. Galite juos pakeisti arba išvalyti.
5. Pasirinkite CSV failo kelią ir spauskite **Export All**.
6. Baigus **Sessions** lentelė parodys visas aptiktas sesijas, o
   **Graph** skirtukas automatiškai įkels CSV.

Jei kas nors nepavyksta, paleiskite su `--verbose`:

```bash
python3 fluke.py --verbose
```

---

### Greita pradžia (CLI — `975dlall.py`)

Greičiausias būdas atsisiųsti viską:

```bash
python3 975dlall.py --out fluke975.csv
```

Atbulinis skenavimas (nuo naujausio, greitas dideliems žurnalams):

```bash
python3 975dlall.py --reverse --out fluke975.csv
```

Tik įrašai nuo nurodytos datos, naudojant atbulinį skenavimą:

```bash
python3 975dlall.py --since 2025-01-01 --out fluke975.csv
```

Jei matuoklis lėtas, pakoreguokite laikus:

```bash
python3 975dlall.py --silence-timeout 1.0 --read-timeout 3.0 --out fluke975.csv
```

Naudingi parametrai:

| Parametras | Numatyta | Reikšmė |
|------------|----------|---------|
| `--out` | `fluke975.csv` | Išvesties CSV failas |
| `--since YYYY-MM-DD` | — | Tik įrašai nuo šios datos (įjungia atbulinį skenavimą) |
| `--reverse` | išjungta | Atbulinis skenavimas be `--since` |
| `--start-block` | `0` | Pirmas blokas (tiesioginiame) arba aukščiausias blokas (atbuliniame) |
| `--max-blocks` | `10000` | Viršutinė riba tiesioginiam skenavimui / atbulinio viršaus paieškai |
| `--stop-after-empty` | `3` | Tiesioginis: sustoti po N tuščių blokų iš eilės |
| `--silence-timeout` | `0.6` | Sekundės be atsakymo iki „nėra duomenų" |
| `--read-timeout` | `2.5` | Maksimalus laikas vienam blokui |

---

### Greita pradžia (senasis CLI — `fluke975.py`)

Naudoja paties matuoklio sesijų katalogą. Lėčiau, bet gaunate tikslius
kiekvienos sesijos skaičius:

```bash
python3 fluke975.py --port /dev/ttyACM0 --output fluke975.csv
```

CSV išvestyje yra `session`, `session_start`, `session_start_address`,
`session_record_count` ir visi matavimų laukai.

---

### Fluke 975 ypatumai, kuriuos reikia žinoti

Štai kodėl egzistuoja šis įrankių rinkinys. Suprasdami šiuos dalykus
sutaupysite daug laiko.

#### 1. **„Sesija" — tai 30 sekundžių skirtumas (numatytasis)**

Matuoklis **neturi aiškios sesijos sąvokos**. Kiekvienas įrašas yra tik
32 baitų įrašas su BCD laiko žyma. Du gretimi įrašai priklauso **tai
pačiai sesijai** tik jei jų laiko žymos skiriasi **ne daugiau kaip 30
sekundžių** (`--gap-seconds 30.0` faile `975dlall.py`, `gap_seconds=30.0`
GUI programoje).

Jei tarpas tarp dviejų įrašų viršija 30 s, CSV faile pradedama nauja
sesija (`session` stulpelis padidėja).

**Pasekmės:**

- Jei matuoklyje paspausite *Log* du kartus per 30 sekundžių, abu
  paleidimai bus sujungti į vieną sesiją.
- Jei pamiršite, kad matuoklis registruoja, ir pasiimsite jį po valandos,
  gausite vieną didžiulę sesiją. Padidinkite `--gap-seconds` (pvz., iki
  `300`), kad ilgesni tarpai būtų laikomi ta pačia sesija.
- GUI tai parodo per **Sessions** lentelę — kiekviena eilutė yra viena
  grupė, atskirta 30 s ar didesniu tarpu.

#### 2. **Nuosekliosios jungties atidarymas atstato matuoklį**

Fluke 975 USB‑nuoseklusis tiltelis atidarant nustato DTR, o tai sukelia
matuoklio atsistatymą. Jei atidarysite jungtį ir iškart siųsite `ID\r`,
gausite tuščią atsakymą arba šiukšles.

`run_fluke975.py` tai sprendžia **atidarymo apšilimu → uždarymu →
palaukimu → pakartotiniu atidarymu**, po to trumpai palaukia prieš
pirmą komandą. Jei kada nors bendrausite su matuokliu tiesiogiai per
`pyserial`, pakartokite šią seką, kitaip kovosite su „vaiduokliškais"
laukimo laikais.

#### 3. **Komandos turi būti išdėstytos laike**

Po skaitymo matuokliui reikia trumpos pauzės prieš kitą rašymą.
`run_fluke975.py` tai užtikrina per `FLUKE_PREWRITE` (numatyta `0.05` s
GUI programoje, `0.3` s savarankiškai). Jei plaksite komandomis be
pauzių, atsakymai bus tyliai prarandami.

#### 4. **Tyla ≠ klaida**

Kai prašote bloko, kuriame nėra duomenų (už naujausio įrašyto bloko),
matuoklis tiesiog **neatsako**. Tai normalu. Tai ne laukimo laikas ir ne
NAK.

Atsisiuntėjas tai naudoja kaip sustojimo signalą: jei `--silence-timeout`
(numatyta `0.6` s) praeina negavus nė baito, blokas laikomas tuščiu ir
svarstomas toliau. Sumažinkite jį greitesniam skenavimui; padidinkite,
jei matuoklis atsako labai lėtai.

#### 5. **QD 2 atsakymai yra lygiai 268 baitai**

```
"0\r"        (2 baitai, patvirtinimas)
"QD 2"       (4 baitai, pakartojimas)
selektorius  (4 baitai, atitinka jūsų užklausą)
duomenys     (256 baitai = 8 × 32 baitų įrašai)
CRC          (2 baitai, CRC‑16/MODBUS little‑endian)
```

`fetch_block()` faile `975dlall.py` grįžta iškart, kai tik atvyksta 268
baitai — nereikia laukti tylios linijos. Jei gaunate mažiau nei 268
baitus, blokas laikomas blogu ir praleidžiamas.

#### 6. **CRC yra CRC‑16/MODBUS**

Polinomas `0xA001`, pradinė reikšmė `0xFFFF`, saugoma **little‑endian**.
Blogas bloko CRC paprastai ne fatališkas — blokas tiesiog kartojamas
skenavimo logikos (iki 3 klaidų iš eilės, tada sustabdoma).

#### 7. **Laiko žymos yra BCD, ne dvejetainės**

Kiekvieno įrašo laiko žyma yra šeši baitai: `HH MM SS DD MM YY`,
kiekvienas baitas **dvejetainiu koduotu dešimtainiu (BCD)**. Pavyzdžiui,
baitas `0x25` reiškia *25*, o ne *37*. Jei matuoklio laikrodis neteisingas
(išsikrovusi baterija, pirmas įjungimas), laiko žymos taip pat bus
neteisingos, ir jokie duomenys to nepataisys.

#### 8. **Du įrašų tipai gyvuoja kartu**

| `record_type` | Reikšmė | CO₂ laukas |
|---------------|---------|------------|
| `0x01D8` | Senasis formatas | 32 bitų, baitai 20–23 |
| `0x03F8` | Naujasis formatas | 16 bitų prie 20–21 baitų, plius 16 bitų `extra` prie 22–23 |

Programinės įrangos versija nepasako, kurį formatą matysite — matuoklis
rašo abu. `975dlall.py` ir GUI automatiškai apdoroja abu. Jei rašote
savo analizatorių, patikrinkite `record_type` ir pasirinkite tinkamą
poslinkį.

#### 9. **Blokai yra 256 baitai, įrašai — 32 baitai → 8 įrašai bloke**

`BLOCK_RESPONSE_LEN = 268`, `PAYLOAD_RECORD_SIZE = 32`,
`PAYLOAD_RECORDS = 8`. Įrašas, kurio laukas `record` yra `0`, yra tuščia
vieta ir analizės metu praleidžiamas.

#### 10. **Pradžios adresai lygiuojami į 256 baitų**

QD 0 sesijų katalogas kiekvienai sesijai pateikia `start_address`, kuris
visada yra 256 kartotinis. Padalinkite iš 256, kad gautumėte pradinį
bloko numerį (`start_block = start_address // 256`). Jei kada pamatysite
nesulygintą adresą QD 0, atsakymas sugadintas — kartokite.

#### 11. **Matuoklis nepasako, kiek turi blokų**

Nėra komandos „gauti blokų skaičių". Reikia **zondavimo**:
`find_top_block()` faile `975dlall.py` atlieka eksponentinį zondavimą
(1, 2, 4, 8, …), po to dvejetainę paiešką, kol randa aukščiausią bloką
su duomenimis. Štai kodėl atbuliniai skenavimai pradedami trumpa
„zondavimo" faze prieš einant žemyn.

#### 12. **Registravimo laikrodis — tai matuoklio laikrodis**

Jei Fluke laikrodis neteisingas, visos laiko žymos bus neteisingos. Šiuo
protokolu negalima nei perskaityti, nei nustatyti laikrodžio. Nustatykite
jį pačiame matuoklyje prieš registruodami.

#### 13. **Temperatūra 0,1 °C, drėgmė 0,01 %**

Temperatūra, rasos taškas ir šlapio termometro temperatūra yra
**signed 16‑bit sveikieji skaičiai dešimtosiomis laipsnio dalimis**.
Drėgmė yra **unsigned 16‑bit šimtosiomis procento dalimis**. CO yra
unsigned 16‑bit ppm. CO₂ yra 16 arba 32 bitų ppm, priklausomai nuo įrašo
tipo (žr. #8).

#### 14. **Daliniai eksportai išsaugomi atšaukus**

Paspaudus **Cancel** GUI programoje (arba Ctrl‑C CLI), skenavimas
sustabdomas, bet visi iki tol surinkti įrašai vis tiek įrašomi. Tai
naudinga su dideliais žurnalais — neprarandate progreso.

---

### Trikčių šalinimas

| Simptomas | Tikėtina priežastis | Sprendimas |
|-----------|---------------------|------------|
| `ID failed — power‑cycle the meter and retry` | Apšilimo atidarymas praleido atsistatymą arba matuoklis nepasiruošęs | Atjunkite USB, prijunkite, palaukite 2 s, kartokite |
| `Permission denied: '/dev/ttyACM0'` | Naudotojas ne grupėje `dialout` | `sudo usermod -aG dialout $USER`, atsijunkite/prisijunkite |
| `No serial ports found` | Trūksta USB‑nuosekliojo tiltelio / branduolio modulis neįkeltas | `dmesg \| tail`, `ls /dev/ttyACM*`, `ls /dev/serial/by-id/` |
| Visi blokai praneša `silence` | Matuoklis neturi duomenų (naujas) arba per trumpas tylos laukimas | Bandykite `--silence-timeout 1.5`; patikrinkite matuoklio ekraną |
| `3 consecutive errors — aborting` | USB nestabilumas arba neteisingas bodų skaičius | Prijunkite iš naujo, patikrinkite `baud=9600` |
| Duomenys atrodo teisingi, bet datos neteisingos | Matuoklio laikrodis neteisingas | Nustatykite laikrodį matuoklyje |
| GUI automatinis intervalo skenavimas atšauktas prisijungus | Paspaudėte Cancel zondavimo metu | Spauskite **Show available** dar kartą |
| `QD 0` CRC klaida | Nuosekliosios jungties korupcija | Padidinkite `--silence-timeout`; perdėkite kabelį |

Jei niekas nepadeda, paleiskite su išsamiu derinimu:

```bash
FLUKE_DEBUG=1 python3 975dlall.py --verbose --out test.csv
```

Pamatysite kiekvieną baitą jungtyje.

---

### Išvesties CSV schema

```
session, record_type, record, timestamp,
temperature_c, rh_percent, dewpoint_c, wetbulb_c,
co_ppm, co2_ppm, extra
```

- `session` — sveikasis skaičius, padidinamas, kai tarpas tarp įrašų
  viršija `--gap-seconds` (numatyta **30 s**)
- `record_type` — `0x01D8` arba `0x03F8`
- `record` — matuoklio vidinis įrašo numeris (ne nulis)
- `timestamp` — `YYYY‑MM‑DD HH:MM:SS` matuoklio vietiniu laiku

---

## License

MIT. Use at your own risk — this is unofficial, reverse‑engineered
software. Not affiliated with Fluke Corporation.

## Contributing

Bug reports and pull requests welcome. When reporting, please include:

- `FLUKE_DEBUG=1 python3 975dlall.py --verbose` output (redact any PII)
- Firmware version shown by `ID`
- Output of `lsusb` and `dmesg | tail -30` after plugging the meter in
