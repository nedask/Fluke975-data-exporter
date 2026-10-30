# Fluke 975 Data Extractor

### Export logged measurements from the Fluke 975 AirMeter

**English + Lietuvių kalba**

A small utility for downloading measurement sessions stored on a **Fluke 975 AirMeter (firmware 1C)** over a serial connection and exporting them to CSV.

Ši programa skirta atsisiųsti **Fluke 975 AirMeter (1C programinės aparatinės įrangos versija)** išsaugotus matavimų duomenis per nuoseklųjį prievadą ir eksportuoti juos į CSV failą.


## Contents / Turinys

- [English](#english)

- [Lietuvių kalba](#lietuvių-kalba)

- [Troubleshooting / Trikčių šalinimas](#troubleshooting--trikčių-šalinimas)

- [Technical notes / Techninės pastabos](#technical-notes--techninės-pastabos)

# English

## What does it do?

The project provides two ways to export data:

| Tool | Best for | Description |
| - | - | - |
| `fluke.py` | Most users | Graphical interface (GUI) with connection controls, export progress, data preview, and logs. |
| `975dlall.py` | Automation and scripts | Command-line exporter for terminal use, including headless environments. |


**Recommendation:** Start with `fluke.py` unless you specifically need command-line automation.

## Quick start: graphical interface

### 1. Install the dependency

```
pip install pyserial

pip install tk
```

### 2. Start the application

```
python3 fluke.py
```

### 3. Connect the meter

1. Connect the Fluke 975 to your computer using USB.

2. The serial port may appear as `/dev/ttyACM0` on Linux or `COMx` on Windows. The device USB vendor ID is `0f7e`.

3. In the **Device** bar, the application will try to select the Fluke automatically. If it does not, select the correct port and click **Refresh**.

4. Click **Connect**. A successful connection should show a green status similar to `● FLUKE,975,…`.

### 4. Export your measurements

1. Choose an output filename. The default is `fluke975.csv`.

2. Click **Start export** or press **Ctrl+E**.

3. Watch the progress bar and the **Data preview** tab while records are being downloaded.

A typical full export may finish in around **1–3 seconds** when fast mode works. Actual time depends on the device and the data available.

To stop an export, click **Cancel** or press **Esc**. Any partial results are still written to the CSV file.

> **USB note:** The original documentation lists USB 1.0 transfer rates as 1.5 Mbps (Low Speed) and up to 12 Mbps (Full Speed). These are interface rates, not a guarantee of the export speed.

## Command-line export

Use `975dlall.py` when you want to export data from a terminal or automate the process.

### Options

| Option | What it does |
| - | - |
| `--out PATH` | Output CSV path. Default: `fluke975.csv`. |
| `--max-blocks N` | Maximum number of blocks to scan from `--start-block`. Default: `10000`. |
| `--start-block N` | First block to scan in forward mode, or highest block in reverse mode. |
| `--stop-after-empty N` | In forward mode, stop after this many blocks contain no new records. Default: `3`. |
| `--gap-seconds F` | Time gap, in seconds, used to split records into sessions. Default: `30`. |
| `--since YYYY-MM-DD` | Export records dated on or after this date. Automatically selects reverse scanning. |
| `--reverse` | Force reverse scanning, with or without `--since`. |
| `--silence-timeout F` | Seconds without incoming bytes before a block is considered silent. Default: `0.6`. |
| `--read-timeout F` | Maximum time allowed to read one block, in seconds. Default: `2.5`. |
| `-v`, `--verbose` | Print per-block debugging information. |


### Examples

**Export all available records (forward scan):**

```
python3 975dlall.py --out full.csv
```

**Export records from a chosen date onward (reverse scan):**

```
python3 975dlall.py --since 2026-10-01 --out recent.csv
```

Replace `2026-10-01` with the date you need, using the `YYYY-MM-DD` format.

**Force reverse scanning without a date filter:**

```
python3 975dlall.py --reverse --out last.csv
```

## GUI options reference

### Device bar

| Control | Description |
| - | - |
| **Port** | Serial port such as `/dev/ttyACM*`, `/dev/serial/by-id/…`, or `COMn`. A Fluke device (`VID 0f7e`) is highlighted automatically when detected. |
| **Refresh** | Scan available serial ports again. Use after connecting or reconnecting the meter. |
| **Connect** | Send the device identification request (`ID\r`) and check for a response beginning with `0\rFLUKE,…`. |
| **Disconnect** | Close the serial connection. |


### Export tab: basic options

| Option | Default | Description |
| - | - | - |
| **Only records since** | Off | Filters the downloaded records to dates on or after `YYYY-MM-DD`. This filter is applied after downloading. |
| **Overwrite file** | On | When off, records are appended to an existing CSV without adding another header row. |
| **⚡ Fast mode** | On | Uses the `QD 0` directory command. Automatically falls back to adaptive scanning if needed. |
| **Verbose** | Off | Writes detailed serial-byte information to standard error and sets `FLUKE_DEBUG=1`. |


### Export tab: advanced scanning

| Option | Default | Description |
| - | -: | - |
| **Scan blocks 0 to** | `65535` | Upper limit for adaptive scanning. Ignored in fast mode. |
| **Max gap probe** | `4096` | In adaptive mode, maximum distance to probe ahead across empty gaps. |
| **Silence timeout** | `0.6 s` | How long to wait without incoming bytes before treating a block as silent. |
| **Read timeout** | `2.5 s` | Maximum time allowed for a single block read. |
| **Exhaustive** | On | In adaptive mode, uses a 100-empty-block stopping threshold instead of 3. |


### Data preview tab

- **Stats line:** shows the number of records, number of sessions, and timestamp range.

- **Preview table:** displays the 20 most recent records.

- **Session details:** double-click a session in the **Sessions** list on the Export tab to view per-session minimum and maximum values for temperature (T), relative humidity (RH), carbon monoxide (CO), and carbon dioxide (CO₂).

### Log tab

The **Log** tab contains a timestamped activity log. Click **Save as…** to save it.

## Troubleshooting

- **The meter is not listed:** Check the USB cable and connection, then click **Refresh**. Confirm that the operating system can see the serial port.

- **Connection fails:** Make sure the correct port is selected and that no other application is using it.

- **Export is slow or incomplete:** Try the default **Fast mode** first. If necessary, use the command-line options to adjust scanning and timeout behaviour.

- **You need more detail:** Enable **Verbose** to collect additional diagnostic output.

These are general checks based on the documented controls; the README does not specify every possible device or operating-system error.


# Lietuvių kalba

## Kam skirta ši programa?

Šis projektas leidžia atsisiųsti „Fluke 975 AirMeter“ matavimų įrašus ir išsaugoti juos CSV faile. Palaikoma **Fluke 975 AirMeter su 1C programinės aparatinės įrangos versija** ir nuoseklusis ryšys.

Yra du naudojimo būdai:

| Programa | Kam geriausiai tinka | Aprašymas |
| - | - | - |
| `fluke.py` | Daugumai naudotojų | Grafinė sąsaja su prisijungimo valdikliais, eksporto eiga, duomenų peržiūra ir žurnalu. |
| `975dlall.py` | Automatizavimui ir scenarijams | Eksportavimas per komandų eilutę, taip pat aplinkose be grafinės sąsajos. |


**Rekomendacija:** pradėkite nuo `fluke.py`, nebent jums reikia automatizuoti eksportavimą per komandų eilutę.

## Greita pradžia: grafinė sąsaja

### 1. Įdiekite reikalingą biblioteką

```
pip install pyserial

pip install tk
```

### 2. Paleiskite programą

```
python3 fluke.py
```

### 3. Prijunkite matuoklį

1. USB kabeliu prijunkite „Fluke 975“ prie kompiuterio.

2. „Linux“ sistemoje nuoseklusis prievadas gali būti rodomas kaip `/dev/ttyACM0`, o „Windows“ sistemoje – kaip `COMx`. Įrenginio USB gamintojo ID yra `0f7e`.

3. Programos juostoje **Device** ji bandys automatiškai parinkti „Fluke“ įrenginį. Jei nepavyks, pasirinkite tinkamą prievadą ir spustelėkite **Refresh**.

4. Spustelėkite **Connect**. Sėkmingai prisijungus turėtų būti rodomas žalias būsenos pranešimas, panašus į `● FLUKE,975,…`.

### 4. Eksportuokite matavimus

1. Pasirinkite išvesties failo pavadinimą. Pagal numatytuosius nustatymus naudojamas `fluke975.csv`.

2. Spustelėkite **Start export** arba paspauskite **Ctrl+E**.

3. Stebėkite eigos juostą ir skirtuką **Data preview**, kuriame atsisiunčiami įrašai rodomi realiuoju laiku.

Jei veikia greitasis režimas, visas eksportavimas paprastai gali užtrukti apie **1–3 sekundes**. Tikroji trukmė priklauso nuo įrenginio ir jame saugomų duomenų.

Norėdami nutraukti eksportavimą, spustelėkite **Cancel** arba paspauskite **Esc**. Jau gauti daliniai rezultatai vis tiek įrašomi į CSV failą.

> **Pastaba apie USB:** pradinėje dokumentacijoje nurodyti USB 1.0 duomenų perdavimo greičiai: 1,5 Mbps (mažas greitis, „Low Speed“) ir iki 12 Mbps (visas greitis, „Full Speed“). Tai sąsajos greičiai, o ne garantuotas failo eksportavimo greitis.

## Eksportavimas per komandų eilutę

Naudokite `975dlall.py`, jei norite eksportuoti duomenis terminale arba automatizuoti šį procesą.

### Parinktys

| Parinktis | Paskirtis |
| - | - |
| `--out PATH` | Išvesties CSV failo kelias. Numatytoji reikšmė: `fluke975.csv`. |
| `--max-blocks N` | Didžiausias tikrinamų blokų skaičius, skaičiuojant nuo `--start-block`. Numatytoji reikšmė: `10000`. |
| `--start-block N` | Pirmasis tikrinamas blokas įprastu režimu arba aukščiausias blokas atvirkštinio skenavimo režimu. |
| `--stop-after-empty N` | Įprastu režimu sustoja, kai nurodytas blokų skaičius iš eilės nepateikia naujų įrašų. Numatytoji reikšmė: `3`. |
| `--gap-seconds F` | Laiko tarpas sekundėmis, pagal kurį įrašai skirstomi į atskiras sesijas. Numatytoji reikšmė: `30`. |
| `--since YYYY-MM-DD` | Eksportuoja įrašus nuo nurodytos datos imtinai. Automatiškai parenka atvirkštinį skenavimą. |
| `--reverse` | Priverstinai įjungia atvirkštinį skenavimą, su datos filtru arba be jo. |
| `--silence-timeout F` | Kiek sekundžių negavus duomenų blokas laikomas tyliu. Numatytoji reikšmė: `0.6`. |
| `--read-timeout F` | Didžiausias vieno bloko skaitymo laikas sekundėmis. Numatytoji reikšmė: `2.5`. |
| `-v`, `--verbose` | Rodo išsamią kiekvieno bloko diagnostinę informaciją. |


### Pavyzdžiai

**Eksportuoti visus pasiekiamus įrašus (įprastas skenavimas):**

```
python3 975dlall.py --out full.csv
```

**Eksportuoti įrašus nuo pasirinktos datos (atvirkštinis skenavimas):**

```
python3 975dlall.py --since 2026-10-01 --out recent.csv
```

Pakeiskite `2026-10-01` reikiama data. Naudokite formatą `YYYY-MM-DD` (metai-mėnuo-diena).

**Priverstinai naudoti atvirkštinį skenavimą be datos filtro:**

```
python3 975dlall.py --reverse --out last.csv
```

## Grafinės sąsajos parinkčių aprašymas

### Įrenginio juosta (**Device**)

| Valdiklis | Aprašymas |
| - | - |
| **Port** | Nuoseklusis prievadas, pavyzdžiui, `/dev/ttyACM*`, `/dev/serial/by-id/…` arba `COMn`. Aptikus „Fluke“ įrenginį (`VID 0f7e`), jis automatiškai paryškinamas. |
| **Refresh** | Iš naujo ieško galimų nuosekliųjų prievadų. Naudokite prijungę arba iš naujo prijungę matuoklį. |
| **Connect** | Išsiunčia įrenginio identifikavimo užklausą (`ID\r`) ir tikrina, ar atsakymas prasideda `0\rFLUKE,…`. |
| **Disconnect** | Uždaro nuoseklųjį ryšį. |


### Skirtukas „Export“ – pagrindinės parinktys

| Parinktis | Numatytoji reikšmė | Aprašymas |
| - | - | - |
| **Only records since** | Išjungta | Palieka įrašus, kurių data yra nurodyta arba vėlesnė (`YYYY-MM-DD`). Filtras taikomas po duomenų atsisiuntimo. |
| **Overwrite file** | Įjungta | Išjungus, įrašai pridedami prie esamo CSV failo, nekartojant antraštės eilutės. |
| **⚡ Fast mode** | Įjungta | Naudoja `QD 0` katalogo komandą. Jei reikia, automatiškai pereina prie adaptyvaus skenavimo. |
| **Verbose** | Išjungta | Išveda išsamią informaciją apie nuosekliuoju ryšiu gaunamus baitus ir nustato `FLUKE_DEBUG=1`. |


### Skirtukas „Export“ – išplėstinis skenavimas

| Parinktis | Numatytoji reikšmė | Aprašymas |
| - | -: | - |
| **Scan blocks 0 to** | `65535` | Adaptyvaus skenavimo viršutinė riba. Greitajame režime ignoruojama. |
| **Max gap probe** | `4096` | Adaptyviuoju režimu – didžiausias atstumas, per kurį tikrinama už tuščių blokų esančių duomenų. |
| **Silence timeout** | `0.6 s` | Kiek laukti negaunant duomenų, kad blokas būtų laikomas tyliu. |
| **Read timeout** | `2.5 s` | Didžiausias vieno bloko skaitymo laikas. |
| **Exhaustive** | Įjungta | Adaptyviuoju režimu naudojama 100 tuščių blokų sustabdymo riba vietoj 3. |


### Skirtukas „Data preview“ – duomenų peržiūra

- **Statistikos eilutė:** rodo bendrą įrašų ir sesijų skaičių bei laiko žymų intervalą.

- **Peržiūros lentelė:** rodo 20 naujausių įrašų.

- **Sesijos informacija:** skirtuke **Export** dukart spustelėkite sesiją sąraše **Sessions**, kad pamatytumėte tos sesijos minimalią ir maksimalią temperatūros (T), santykinės drėgmės (RH), anglies monoksido (CO) ir anglies dioksido (CO₂) reikšmes.

### Skirtukas „Log“ – žurnalas

Skirtuke **Log** pateikiamas veiksmų žurnalas su laiko žymomis. Norėdami jį išsaugoti, spustelėkite **Save as…**.

## Trikčių šalinimas

- **Matuoklio sąraše nėra:** patikrinkite USB kabelį ir jungtį, tada spustelėkite **Refresh**. Įsitikinkite, kad operacinė sistema aptinka nuoseklųjį prievadą.

- **Nepavyksta prisijungti:** patikrinkite, ar pasirinktas tinkamas prievadas ir ar jo nenaudoja kita programa.

- **Eksportavimas lėtas arba neužbaigtas:** pirmiausia išbandykite numatytąjį **Fast mode**. Jei reikia, komandų eilutėje pakoreguokite skenavimo ir laukimo laiko parinktis.

- **Reikia daugiau diagnostinės informacijos:** įjunkite **Verbose**, kad būtų pateikta daugiau informacijos.

Tai bendro pobūdžio patikros pagal dokumentacijoje aprašytus valdiklius; ne visos galimos įrenginio ar operacinės sistemos klaidos čia aprašytos.


# Technical notes / Techninės pastabos

This section is intended for developers and users who want to understand the device protocol.

Šis skyrius skirtas kūrėjams ir naudotojams, norintiems geriau suprasti įrenginio ryšio protokolą.

- **CRC:** Modbus CRC-16, polinomas `0xA001`, pradinė reikšmė `0xFFFF`; CRC pridedamas mažosios baitų tvarkos formatu (*little-endian*).

- **`QD 0` directory command:** The request consists of `QD 0` plus CRC. The response contains `0\rQD 0`, a 1-byte count, *N* entries of 16 bytes each, and CRC. Each entry contains a BCD timestamp (`HH MM SS DD MM YY`), a 32-bit unsigned start address aligned to 256 bytes, a 32-bit unsigned record count, and 2 unknown bytes.

- **`QD 0` katalogo komanda:** Užklausą sudaro `QD 0` ir CRC. Atsakyme pateikiama `0\rQD 0`, 1 baito skaitiklis, *N* po 16 baitų įrašų ir CRC. Kiekviename įraše yra BCD formato laiko žyma (`HH MM SS DD MM YY`), 32 bitų be ženklo pradžios adresas, suderintas su 256 baitų riba, 32 bitų be ženklo įrašų skaičius ir 2 nežinomi baitai.

- **`QD 2` block command:** The request consists of `QD 2`, a selector (`00` + 16-bit block number + `00`), and CRC. The response is 268 bytes: `0\rQD 2`, a 4-byte selector, a 256-byte payload, and CRC.

- **`QD 2` bloko komanda:** Užklausą sudaro `QD 2`, parinkiklis (`00` + 16 bitų bloko numeris + `00`) ir CRC. Atsakymas yra 268 baitų: `0\rQD 2`, 4 baitų parinkiklis, 256 baitų duomenų dalis ir CRC.

- **Record format / Įrašo formatas (32 bytes / 32 baitai):** `type:u16`, `num:u16`, `ts:6×BCD`, `T:i16/10`, `RH:u16/100`, `dew:i16/10`, `wet:i16/10`, `CO:u16`, followed by either `CO₂:u32` (older format `0x01D8`) or `CO₂:u16 + extra:u16` (newer format `0x03F8`).

- **Byte order / Baitų tvarka:** All multibyte values use little-endian byte order. / Visų kelių baitų reikšmių baitų tvarka yra mažoji (*little-endian*).

## Measurement abbreviations / Matavimo santrumpos

| Abbreviation | English | Lietuviškai |
| - | - | - |
| T | Temperature | Temperatūra |
| RH | Relative humidity | Santykinė drėgmė |
| CO | Carbon monoxide | Anglies monoksidas |
| CO₂ | Carbon dioxide | Anglies dioksidas |
| CSV | Comma-separated values file | Kableliais atskirtų reikšmių failas |


## Before you begin / Prieš pradedant

- Keep the original measurement files safe before overwriting or appending to an existing CSV.  
Prieš perrašydami esamą CSV failą arba pridėdami prie jo duomenų, pasidarykite atsarginę kopiją.

- If you are unsure which serial port belongs to the meter, disconnect the meter, check the port list, reconnect it, and click **Refresh**.  
Jei nežinote, kuris nuoseklusis prievadas priklauso matuokliui, atjunkite matuoklį, patikrinkite prievadų sąrašą, prijunkite jį iš naujo ir spustelėkite **Refresh**.

- Keep the command names and code examples unchanged when following the instructions; translate only the explanatory text.  
Vykdydami instrukcijas nekeiskite komandų pavadinimų ir kodo pavyzdžių – versti reikia tik paaiškinamąjį tekstą.
