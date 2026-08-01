# Asuhan Keperawatan — SDKI / SLKI / SIKI

Aplikasi ringan untuk membantu tenaga klinis menyusun rencana asuhan
dari data pemeriksaan. Dua alur, dipilih di awal:

**💉 Perawat** — diagnosis keperawatan berbasis SDKI/SLKI/SIKI

```
Isi data S & O  →  usulan diagnosis  →  pilih & susun prioritas
                                              ↓
                              tabel asuhan lengkap (SDKI + SLKI + SIKI)
```

Saat memilih intervensi tersedia **✓ Pilih semua** dan **Kosongkan** per
diagnosis, plus tombol **semua** per kategori (Observasi / Terapeutik /
Edukasi / Kolaborasi) — berguna karena perawat sering mengambil seluruh
observasi tapi menyaring terapeutik sesuai kondisi pasien.

**🩺 Dokter** — Panduan Praktik Klinis (PPK)

```
Temuan klinis  →  kemungkinan diagnosis  →  pilih satu
                                              ↓
                    kriteria diagnosis + panduan tatalaksana lengkap
```

**Tanpa manajemen pasien.** Pemilihan profesi di awal **bukan
autentikasi** — tidak ada verifikasi identitas maupun pembatasan
kewenangan, hanya menentukan alur kerja mana yang ditampilkan.

---

## Deploy

Siap di-deploy secara teknis, tapi ada **dua keputusan** yang harus
diambil lebih dulu soal penyimpanan data dan kerahasiaan — lihat
**[DEPLOY.md](DEPLOY.md)**.

---

## Menjalankan

```bash
pip install -r requirements.txt
streamlit run app.py
```

Database SQLite dibuat otomatis di `data/asuhan.db` saat pertama jalan.
Tidak perlu setup server apa pun.

### Voice-to-text

**Berjalan tanpa API key.** Memakai Google Speech Recognition gratis
lewat pustaka `SpeechRecognition`, yang sudah ada di `requirements.txt`.

Batasnya jujur: layanan gratis ini memakai titik akhir yang tidak resmi
didokumentasikan Google, ada pembatasan jumlah permintaan, dan Google
dapat menghentikannya sewaktu-waktu. Untuk alat bantu pembelajaran itu
dapat diterima; untuk layanan yang harus selalu tersedia, tidak.

#### Alur rekam

Satu tombol besar: tekan untuk mulai, tekan lagi untuk berhenti — teks
langsung muncul di kolom. Tidak ada pemutar audio dan tidak ada tombol
transkripsi terpisah; rekaman di sini bukan untuk didengarkan ulang,
hanya untuk diubah jadi teks.

Hasil transkripsi **disambung** ke teks yang sudah ada, jadi bisa merekam
beberapa kali sambil menyunting manual di antaranya.

### Terjemahan (opsional)

Bila `GOOGLE_TRANSLATE_API_KEY` diisi, muncul tombol **Terjemahkan ke
Indonesia** di bawah kolom S dan O.

Gunanya konkret: kriteria SDKI berbahasa Indonesia, jadi bagian catatan
yang ditulis dalam bahasa Inggris tidak akan pernah cocok sebelum
diterjemahkan.

> `GOOGLE_TRANSLATE_API_KEY` dan `GOOGLE_STT_API_KEY` adalah **dua API
> terpisah** di Google Cloud. Satu key tidak otomatis berlaku untuk
> keduanya.

#### Opsional: Google Cloud Speech-to-Text (berbayar)

Lebih andal dan lebih akurat untuk istilah klinis, karena mendukung
daftar frasa prioritas — tanpa itu "ortopnea" sering menjadi "orto nea"
dan "JVP" menjadi "je vi pi", justru kata yang paling menentukan hasil
pencocokan diagnosis.

```bash
export GOOGLE_STT_API_KEY=xxxx
```

atau isi di `.streamlit/secrets.toml`.

> **API key Google tidak berlaku lintas layanan.** Key untuk Google
> Translate **tidak bisa** dipakai untuk Speech-to-Text. Masing-masing API
> harus diaktifkan tersendiri di Google Cloud Console. Bila key sudah
> diisi tetapi transkripsi selalu gagal, kemungkinan besar
> "Cloud Speech-to-Text API" belum diaktifkan untuk key tersebut —
> aplikasi menampilkan pesan galat dari Google apa adanya agar
> penyebabnya terlihat.

Metode gratis tetap berjalan meski key tidak ada, jadi fitur suara tidak
pernah mati hanya karena urusan key.

---

## Struktur

Mengikuti pola berlapis yang sama dengan proyek besar SmartCare, jadi
kalau nanti mau digabung, pemetaannya jelas.

```
asuhan/
├── app.py                    entry Streamlit (router tipis)
├── core/
│   ├── config.py             konfigurasi & kredensial (dari environment)
│   ├── kategori.py           kategori SDKI + kaidah urutan prioritas
│   ├── profesi.py            profesi & pemetaan alur kerja
│   └── exceptions.py
├── database/
│   ├── connection.py         koneksi & transaksi SQLite
│   └── schema.sql
├── models/asesmen.py         domain model
├── repositories/
│   ├── base_repository.py
│   ├── sdki_repository.py    master 3S (55 diagnosis)
│   ├── ppk_repository.py     panduan praktik klinis (15 PPK)
│   └── asesmen_repository.py
├── services/
│   ├── diagnosis_service.py  usulan, prioritas, perakitan tabel (perawat)
│   ├── ppk_service.py        usulan PPK & penanda kondisi kritis (dokter)
│   ├── speech_service.py     Google Speech-to-Text
│   └── export_service.py     Markdown & CSV
├── components/tabel_asuhan.py
├── pages/
│   ├── asesmen/              alur perawat
│   ├── tatalaksana/          alur dokter
│   └── riwayat/
├── tools/
│   ├── validasi_sdki.py      validasi master 3S setelah diedit
│   ├── validasi_ppk.py       validasi PPK setelah diedit
│   ├── validasi_indikator.py validasi indikator luaran
│   ├── json_ke_excel.py      ekspor JSON -> Excel untuk disunting
│   ├── excel_ke_json.py      impor Excel/CSV -> JSON
│   ├── impor_mapping_rsjpdhk.py  impor berkas kerja mapping RSJPDHK
│   └── pdf_ke_excel.py       ekstrak tabel PDF -> Excel (perlu diperiksa)
├── data/
│   ├── sdki_slki_siki.json   master 3S (55 diagnosis)
│   ├── indikator_slki.json   indikator luaran (41 luaran, 225 indikator)
│   └── ppk_kardiovaskular.json  PPK (15 diagnosis)
└── tests/
    ├── test_asuhan.py        56 assertion (alur perawat)
    ├── test_ppk.py           61 assertion (alur dokter)
    └── test_konversi.py      43 assertion (konversi Excel/JSON)
```

Aturan lapisan: `pages`/`components` tidak menulis SQL, `services` tidak
menyentuh database langsung, `repositories` satu-satunya penulis SQL.

---

## Hasil ekspor

Tiga format, semuanya memakai tata letak lembar askep yang sama:

| No | Diagnosis Keperawatan (SDKI) | Luaran (SLKI) | Intervensi Keperawatan (SIKI) |

| Format | Untuk apa |
|---|---|
| **Word (.docx)** | Tabel siap salin-tempel ke dokumen asuhan. Lanskap, bergaris, intervensi bernomor per kategori |
| **Excel (.xlsx)** | Sama, dalam lembar kerja. Sel multi-baris, tinggi baris menyesuaikan isi, siap cetak lanskap |
| **Markdown (.md)** | Teks polos untuk catatan atau dokumentasi |

Satu baris = satu diagnosis, dengan intervensi dikelompokkan per kategori
SIKI di dalam satu sel. Bentuk ini dipilih karena menyerupai lembar askep
yang dipakai sehari-hari, sehingga bisa langsung ditempel.

Bila tidak ada intervensi yang dicentang, seluruhnya ditampilkan sebagai
acuan — lebih berguna daripada kolom kosong.

> Ekspor CSV sudah dihapus. CSV menyatukan seluruh intervensi menjadi
> satu untaian dipisah titik koma; pada D.0008 hasilnya 344 karakter
> menggumpal dalam satu sel dan praktis tidak terbaca.

---

## Yang perlu dipahami sebelum dipakai

### Usulan diagnosis bukan penegakan diagnosis

Usulan dihasilkan dengan **mencocokkan kata kunci** pada data S & O
terhadap kriteria SDKI — bukan penalaran klinis. Konsekuensinya:

- Bisa memunculkan diagnosis yang tidak relevan
- Bisa **melewatkan** diagnosis yang relevan, kalau perawat memakai
  istilah yang tidak ada di kriteria

Karena itu pencarian manual selalu tersedia berdampingan, bukan sebagai
cadangan. Setiap usulan menyertakan kata apa yang cocok, supaya perawat
bisa menilai apakah dasarnya masuk akal.

**Pembobotan kata.** Kata yang muncul di banyak diagnosis (mis. "gelisah")
diberi bobot kecil; kata yang hanya muncul di satu-dua diagnosis (mis.
"sputum", "PCO2") diberi bobot besar. Tanpa ini, diagnosis yang kebetulan
memuat kata umum naik peringkat tanpa alasan klinis dan mendesak turun
diagnosis yang cocok pada temuan menentukan.

**Penanda laboratorium terbaca utuh.** PCO2, HCO3, FiO2, SpO2, PO2
diperlakukan sebagai satu kata kunci — bukan dipotong jadi "pco"/"hco"
lalu dibuang karena terlalu pendek.

**Kosakata sehari-hari & singkatan ICU sudah dipetakan.** Perawat menulis
"slem kental", kriteria SDKI menulis "sputum berlebih"; ditulis "Kalium
2,9", kriteria menulis "ketidakseimbangan elektrolit". Pemetaan padanan
ada di `_SINONIM` pada `repositories/sdki_repository.py`, mencakup istilah
ruangan (slem, dahak, bengkak, lemas) dan singkatan intensif (IABP, SIMV,
PEEP, CRRT, AGD, JVP).

Tambahkan padanan setempat di situ bila unit Anda memakai istilah lain —
ini cara tercepat menaikkan cakupan usulan tanpa menyentuh master data.

Skor yang muncul berguna untuk mengurutkan kandidat, **bukan** ukuran
kepastian klinis. Jangan dibaca sebagai persentase.

### Usulan prioritas hanya titik awal

Tombol "Susun otomatis" memakai kaidah **ABC** — Respirasi → Sirkulasi →
Keamanan → kebutuhan lain, dengan diagnosis Aktual didahulukan atas
Risiko. Konteks pasien sering membalik urutan ini (mis. Risiko Jatuh bisa
jadi prioritas utama pada pasien yang hemodinamiknya sudah stabil).
Urutan yang disimpan adalah urutan yang perawat tentukan.

### Sumber data 3S

`data/sdki_slki_siki.json` berisi 55 diagnosis (49 SDKI resmi + 6
diagnosis tambahan hasil kesepakatan internal, berkode `LOKAL.xxx`),
diimpor langsung dari `SDKI_SLKI_SIKI_mapping_RSJPDHK_2026.xlsx`.

Enam diagnosis lokal bertanda **PERLU VERIFIKASI** — belum dipastikan
kesesuaiannya dengan SDKI resmi. Aplikasi menampilkan penanda ⚠️ pada
diagnosis ini saat dipilih maupun di tabel akhir, supaya perawat tahu
mana yang masih perlu dikonfirmasi terhadap kebijakan unit.

Redaksi kriteria dan intervensi merupakan adaptasi kerja internal
RSJPDHK, **bukan salinan verbatim** buku SDKI/SLKI/SIKI PPNI. Verifikasi
terhadap buku resmi tetap diperlukan sebelum dipakai sebagai acuan legal
atau audit klinis.

Untuk memperbarui isinya tanpa menyentuh kode:

```bash
export ASUHAN_SDKI_JSON=/path/ke/sdki-versi-baru.json
```

### Privasi

Identitas pasien tidak disimpan. Kolom **Penanda** sengaja dibuat bebas
(mis. "Bed 3", "Shift pagi") supaya tidak ada data pribadi yang masuk ke
berkas SQLite. Kalau Anda mengisinya dengan nama pasien, berkas
`data/asuhan.db` menjadi berisi data medis dan harus diperlakukan
sesuai ketentuan kerahasiaan rekam medis di tempat Anda.

---

## Menambah / mengubah diagnosis

Cukup edit **satu berkas**: `data/sdki_slki_siki.json`. Kode Python tidak
perlu disentuh.

### Bentuk satu entri

```json
{
 "kode": "D.0006",
 "nama": "Risiko Aspirasi",
 "jenis": "Risiko",
 "is_sdki": true,
 "kriteria": {
  "mayor": [],
  "minor": [],
  "faktor_risiko": ["Penurunan tingkat kesadaran", "Gangguan menelan"]
 },
 "luaran": { "kode": "L.01006", "nama": "Tingkat Aspirasi Menurun" },
 "intervensi": {
  "observasi":   ["Monitor tingkat kesadaran, batuk, muntah"],
  "terapeutik":  ["Posisikan semi-Fowler 30-45 derajat"],
  "edukasi":     ["Anjurkan makan secara perlahan"],
  "kolaborasi":  ["Kolaborasi dengan tim terapi wicara, jika perlu"]
 },
 "catatan": null,
 "terkait": []
}
```

Tambahkan entri baru ke dalam array `"diagnosis"`, lalu perbarui angka
`meta.jumlah_diagnosis` (opsional — hanya informasi).

### Setelah mengedit, WAJIB validasi

```bash
python tools/validasi_sdki.py
```

Alat ini menangkap kesalahan sintaks JSON (menunjukkan baris persisnya)
sekaligus masalah struktur: kode duplikat, luaran kosong, jenis salah
tulis, kategori intervensi hilang, dan referensi `terkait` yang
menggantung. Perbaiki sampai hasilnya hijau, baru jalankan aplikasi.

### Empat hal yang mudah terlewat

**1. Prefix kode luaran menentukan kategori DAN urutan prioritas.**
Ini yang paling tidak kelihatan. `L.01` = Respirasi, `L.02` = Sirkulasi,
`L.14` = Keamanan, dan seterusnya (lihat `core/kategori.py`). Salah
memberi kode luaran berarti diagnosis itu masuk kategori yang keliru dan
urutan prioritas otomatisnya ikut salah. Kalau memakai prefix baru yang
belum terdaftar, daftarkan dulu di `core/kategori.py` — kalau tidak,
kategorinya jadi "Lainnya" dan selalu diurutkan paling akhir.

**2. `jenis` menentukan kriteria mana yang wajib.**
`"Aktual"` wajib punya `kriteria.mayor`; `"Risiko"` wajib punya
`kriteria.faktor_risiko`. Penulisannya persis begitu — "Actual" atau
"aktual " (dengan spasi) akan ditolak validator.

**3. Keempat kategori intervensi harus ada**, meski isinya list kosong
`[]`. Nama kuncinya persis: `observasi`, `terapeutik`, `edukasi`,
`kolaborasi`. Salah ketik (mis. `edukasii`) tidak error, tapi isinya
diam-diam tidak muncul di aplikasi — validator menandai ini sebagai
peringatan.

**4. Kata dalam kriteria menentukan usulan otomatis.**
Pencocokan memakai kata dari `kriteria` dan `nama`. Jadi kalau perawat di
tempat Anda lazim menulis "kaki bengkak" sementara kriteria hanya menulis
"Edema", diagnosis itu bisa terlewat. Dua cara mengatasinya: tulis
variasi istilahnya di `kriteria.minor`, atau tambahkan pemetaan sinonim
di `_SINONIM` pada `repositories/sdki_repository.py`.

### Untuk diagnosis di luar SDKI

Pakai kode berawalan `LOKAL.` dan set `"is_sdki": false`. Isi `catatan`
untuk menjelaskan asal-usulnya, dan `terkait` untuk menunjuk diagnosis
SDKI yang berhubungan:

```json
"catatan": "Versi aktual dari Risiko Infeksi (D.0142)",
"terkait": ["D.0142"]
```

### Menguji versi baru tanpa menimpa yang lama

```bash
python tools/validasi_sdki.py /path/ke/sdki-baru.json   # validasi dulu
export ASUHAN_SDKI_JSON=/path/ke/sdki-baru.json          # baru pakai
streamlit run app.py
```

Berkas bawaan tetap utuh, jadi mudah kembali kalau ada masalah.


---


---

## Indikator luaran (SLKI)

Setiap luaran kini disertai **indikator terukur** — 41 luaran, 225
indikator. Sebelumnya luaran hanya berupa "L.02008 Curah Jantung
Meningkat", yang tidak dapat dievaluasi karena tidak jelas apa yang
diukur dan berapa targetnya.

Tiap indikator memuat:

| | |
|---|---|
| **Jenis** | `skala5` (skala 1-5 SLKI) atau `angka` (nilai terukur) |
| **Target** | mis. "4-5", "60-100", "kurang dari 3" |
| **Satuan** | mis. x/menit, mmHg, derajat C, NRS 0-10 |
| **Arah** | meningkat / menurun / membaik |
| **Baseline** | **dikosongkan** - diisi perawat saat penilaian awal |

**Baseline sengaja tidak diisi otomatis.** Nilai awal harus berasal dari
penilaian perawat; mengisinya dengan perkiraan membuat evaluasi kemajuan
ikut salah.

### Waktu evaluasi menyesuaikan kegawatan

| Luaran | Evaluasi |
|---|---|
| Sirkulasi spontan | 1 jam |
| Perfusi serebral | 4 jam |
| Curah jantung, bersihan jalan napas | 8 jam |
| Nyeri | 1 jam setelah intervensi |
| Risiko jatuh | per shift |
| Citra tubuh, memori | 1 minggu |

### Memperbarui

Indikator disimpan **terpisah** dari master 3S di
`data/indikator_slki.json`. Keduanya berubah dengan irama berbeda: master
mengikuti buku SDKI/SLKI/SIKI, sedangkan target dan waktu evaluasi kerap
disesuaikan kebijakan unit. Memisahkannya membuat pembaruan salah satu
tidak berisiko merusak yang lain.

```bash
python tools/validasi_indikator.py
```

Validator juga membandingkan dengan master 3S dan melaporkan luaran yang
belum punya indikator.

> Nilai target bersifat **umum untuk dewasa**. Sesuaikan dengan usia dan
> target individual pasien.


---

## Alur dokter (PPK)

### ⚠️ Data bawaan adalah DRAF — wajib diganti

`data/ppk_kardiovaskular.json` berisi **65 PPK kardiovaskular** yang saya
susun sebagai **kerangka kerja** berdasarkan pedoman umum (PERKI, ESC,
AHA). Ini **bukan PPK resmi rumah sakit mana pun**.

Sebelum dipakai dalam pelayanan, **ganti dengan PPK resmi yang berlaku di
rumah sakit Anda**. Dosis obat sengaja **tidak dicantumkan** karena harus
mengikuti formularium dan protokol setempat — mencantumkan dosis yang
tidak diverifikasi lebih berbahaya daripada tidak mencantumkannya.

Progres perluasan menuju ±60 PPK: lihat **[CATATAN_PPK.md](CATATAN_PPK.md)**.

Isi yang tersedia:

| Kategori | Diagnosis |
|---|---|
| Sindrom Koroner | STEMI, NSTEMI, Angina Stabil, UAP, MINOCA, Vasospastik, Mikrovaskular, Pasca-IKP, Pasca-CABG |
| Gagal Jantung | Acute HF, HFrEF, HFmrEF, HFpEF, Right HF, ADHF |
| Kegawatan | Syok kardiogenik, edema paru, emboli paru, diseksi aorta, henti jantung, aneurisma torakal & abdominal, hematoma intramural, hipertensi pulmonal, CTEPH, badai listrik, dukungan mekanik |
| Aritmia | AF, Atrial Flutter, AVNRT, AVRT/WPW, SVT, PVC, PAC, VT, VF, Torsades, Sick Sinus, AV Block I/II/Total |
| Hipertensi | Krisis, Esensial, Resisten, Sekunder |
| PJK | Angina pektoris stabil |
| Katup | AS, AR, MS, MR, TR, Pulmonary Valve Disease |
| Miokardium | Miokarditis, DCM, HCM, RCM, Takotsubo |
| Perikardium | Perikarditis, Tamponade, Efusi, Konstriktif |
| Infeksi | Endokarditis, Katup Prostetik, Infeksi CIED |

### Kenapa alurnya berbeda dari perawat

Alur perawat menghasilkan **beberapa diagnosis sekaligus** yang disusun
berdasarkan prioritas — memang begitu sifat diagnosis keperawatan.

Alur dokter mencari **satu diagnosis kerja**. Kandidat lain yang muncul
adalah **diagnosis banding yang harus disingkirkan**, bukan daftar yang
semuanya dipakai. Karena itu di alur dokter tidak ada penyusunan
prioritas.

### Penanda kondisi kritis 🔴

Enam kondisi ditandai kritis: STEMI, syok kardiogenik, edema paru akut,
emboli paru, diseksi aorta, dan henti jantung.

Kondisi kritis yang **tidak masuk daftar teratas** tetap ditampilkan
terpisah sebagai pengingat "jangan sampai terlewat". Alasannya konkret:
pada keluhan nyeri dada, diseksi aorta sering kalah skor dari sindrom
koroner akut karena gejalanya lebih sedikit disebutkan — padahal
tatalaksananya **berlawanan**. Antikoagulan menyelamatkan pada SKA,
tetapi membahayakan pada diseksi.

Daftar kode kritis ada di `KODE_KRITIS` pada
`services/ppk_service.py`. Perbarui bila menambah PPK kegawatan baru —
`tools/validasi_ppk.py` akan memperingatkan bila ada kode kritis yang
tidak lagi ada di data.

### Menambah / mengubah PPK

Edit `data/ppk_kardiovaskular.json`, lalu **wajib validasi**:

```bash
python tools/validasi_ppk.py
```

Bentuk satu entri:

```json
{
 "kode": "PPK.CV.001",
 "icd10": "I21.9",
 "nama": "Sindrom Koroner Akut — STEMI",
 "kategori": "Sindrom Koroner Akut",
 "definisi": "...",
 "kriteria": {
  "anamnesis":         ["..."],
  "pemeriksaan_fisik": ["..."],
  "penunjang":         ["..."],
  "kriteria_diagnosis":["..."]
 },
 "tatalaksana": {
  "awal":            ["..."],
  "farmakologis":    ["..."],
  "non_farmakologis":["..."],
  "rujukan":         ["..."]
 },
 "edukasi":    ["..."],
 "komplikasi": ["..."],
 "referensi": "PERKI; ESC Guidelines"
}
```

Keempat bagian `kriteria` dan `tatalaksana` **harus ada**, meski isinya
list kosong `[]`. Salah ketik nama bagian tidak menimbulkan error tapi
isinya diam-diam tidak muncul — validator menandainya sebagai peringatan.

Untuk memakai berkas PPK sendiri tanpa menimpa bawaan:

```bash
python tools/validasi_ppk.py /path/ppk-rs-anda.json
export ASUHAN_PPK_JSON=/path/ppk-rs-anda.json
streamlit run app.py
```

### Batasan yang harus dipahami

Usulan diagnosis pada alur dokter adalah **pencocokan kata kunci**, bukan
penalaran diagnostik. Skornya **bukan urutan kemungkinan diagnosis** —
banyak kondisi kardiovaskular berbagi gejala yang hampir sama, dan
kondisi paling berbahaya sering justru bukan yang paling banyak kata
cocoknya. Perlakukan seluruh daftar sebagai diagnosis banding yang perlu
disingkirkan lewat pemeriksaan, bukan sebagai peringkat.


---

## Mengedit lewat Excel (disarankan)

Mengedit JSON langsung rawan salah koma. Alur ini lebih aman untuk tim
klinis: bekerja di Excel, lalu kembalikan ke JSON.

```
JSON  ──ekspor──▶  Excel  ──(sunting)──▶  JSON  ──▶  validasi
```

### 1. Ekspor ke Excel

```bash
python tools/json_ke_excel.py            # keduanya
python tools/json_ke_excel.py ppk        # PPK saja
python tools/json_ke_excel.py sdki       # master 3S saja
```

Menghasilkan `data/*.xlsx` dengan satu baris per diagnosis, kolom sudah
diberi judul dan lebar yang pas, plus lembar **Petunjuk** berisi aturan
pengisian.

### 2. Sunting di Excel

Satu baris = satu diagnosis. Untuk kolom berisi daftar (kriteria,
intervensi, tatalaksana), tulis tiap butir pada **baris tersendiri di
dalam satu sel** — tekan **Alt+Enter** untuk baris baru dalam sel.

Kolom boleh dipindah urutannya; pencocokan memakai **judul kolom**, bukan
posisi. Kolom tambahan buatan sendiri diabaikan saat impor.

### 3. Impor kembali

```bash
python tools/excel_ke_json.py data/ppk_kardiovaskular.xlsx
```

Format (sdki/ppk) dideteksi otomatis dari judul kolom. Dua pengaman:

- **Berkas lama dicadangkan** otomatis (`*.backup-YYYYMMDD-HHMMSS.json`)
- **Impor dibatalkan** bila ada kode kosong/duplikat atau bagian wajib
  yang kosong — JSON lama tidak disentuh sama sekali

### 4. Validasi

```bash
python tools/validasi_ppk.py     # atau validasi_sdki.py
```

### Catatan: titik koma aman

Pemisah butir hanya **baris baru** dan tanda **pipa `|`**. Titik koma
sengaja **tidak** dipakai sebagai pemisah, karena lazim muncul di dalam
kalimat klinis — mis. *"Kendali laju: beta-blocker atau penyekat kanal
kalsium; digoksin pada gagal jantung"*. Kalau `;` dijadikan pemisah,
kalimat itu terpotong jadi dua butir dan maknanya berubah, tanpa pesan
error apa pun.

### Impor langsung dari berkas kerja RSJPDHK

Kalau Anda memakai berkas `SDKI_SLKI_SIKI_mapping_RSJPDHK_*.xlsx` apa
adanya (tanpa menata ulang kolomnya):

```bash
python tools/impor_mapping_rsjpdhk.py SDKI_SLKI_SIKI_mapping_RSJPDHK_2026.xlsx
python tools/validasi_sdki.py
```

Importer ini menyesuaikan diri dengan tata letak berkas kerja tersebut:

- kolom **Kriteria** yang menggabungkan `mayor:` / `minor:` / `FR:`
  dipecah otomatis menjadi tiga bagian
- **jenis** (Aktual/Risiko) disimpulkan dari penanda tersebut — `FR:`
  berarti Risiko
- baris penanda **"DX TAMBAHAN (TIDAK ADA DI SDKI)"** dikenali sebagai
  pemisah, dan baris di bawahnya diberi kode `LOKAL.001`–`LOKAL.006`
  secara otomatis
- kolom **Status Verifikasi** ikut tersimpan dan tampil sebagai penanda
  ⚠️ di aplikasi

Berkas JSON lama otomatis dicadangkan, dan impor dibatalkan bila ada
masalah — sama seperti `excel_ke_json.py`.


---

## Mengekstrak dari PDF

```bash
python tools/pdf_ke_excel.py berkas.pdf
```

**Hasilnya Excel untuk diperiksa, bukan langsung JSON.** Ini disengaja.

### Kenapa harus diperiksa manual

Ekstraksi PDF menyisipkan kesalahan karakter yang tidak terlihat sekilas.
Diuji pada berkas mapping RSJPDHK, tiga kode luaran rusak:

| Terbaca | Seharusnya |
|---|---|
| `L.0L1001` | `L.01001` |
| `L.0L2016` | `L.02016` |
| `L.1L4138` | `L.14138` |

Kesalahan ini **tidak memunculkan error**. Pada master 3S, dua digit
pertama kode luaran menentukan kategori **dan** urutan prioritas — kode
yang rusak membuat diagnosis masuk kategori "Lainnya" dan selalu
diurutkan paling akhir, diam-diam.

Tool menandai sel mencurigakan dengan **latar kuning** (bentuk kode tidak
lazim, huruf terselip di antara angka, spasi hilang antar-kata).
Penandaan ini membantu, **tidak menjamin** semua kesalahan tertangkap.

### Bukti nyata: PDF kehilangan isi

Data awal aplikasi ini pernah disusun dari PDF. Setelah dibandingkan
dengan berkas Excel aslinya, ketahuan ada isi yang **hilang tanpa jejak**:

- **D.0008** (Penurunan Curah Jantung) kehilangan **3 dari 5** butir
  intervensi observasi — termasuk *"Monitor tekanan darah dan nadi"* dan
  *"Monitor intake dan output cairan"*
- **D.0013** kehilangan 2 faktor risiko, termasuk *"usia lebih dari 60
  tahun"*

Semuanya karena teks antar-kolom menyatu saat PDF dibaca. Tidak ada
error, tidak ada tanda apa pun — isinya hanya berkurang.

### Kalau punya file Excel aslinya, pakai itu

PDF adalah format cetak, bukan format data. Sebagian informasi memang
hilang saat dicetak ke PDF dan tidak bisa dipulihkan. Jalur PDF hanya
untuk keadaan ketika berkas aslinya benar-benar tidak ada.

Kalau PDF-nya hasil pindaian (gambar), ekstraksi otomatis tidak mungkin —
masukkan data manual memakai templat dari `json_ke_excel.py`.


---

## Test

```bash
cd tests
python test_asuhan.py     # 56 assertion — alur perawat
python test_ppk.py        # 61 assertion — alur dokter
python test_konversi.py   # 43 assertion — konversi Excel/JSON
python test_konkurensi.py    # 21 assertion — banyak pengguna serentak
python test_ui_intervensi.py # 16 assertion — pemilihan intervensi
python test_ekspor.py        # 60 assertion — Word / Excel / Markdown
python test_suara.py         # 18 assertion — voice-to-text
python test_form_state.py    # 21 assertion — state form & terjemahan
python test_indikator.py     # 38 assertion — indikator luaran
```

`test_asuhan.py`: master 3S, usulan diagnosis, kaidah prioritas,
simpan/baca, tabel lengkap, ekspor, riwayat, cascade delete.

`test_ppk.py`: integritas PPK, usulan dari temuan klinis (STEMI, diseksi
aorta, gagal jantung, FA, henti jantung, emboli paru), penandaan kondisi
kritis, dan pemisahan alur per profesi.

Test memakai berkas SQLite sementara, tidak menyentuh `data/asuhan.db`.

---

## Batasan yang diketahui

- **Rekaman suara maksimal ~60 detik.** Google membatasi endpoint sinkron
  yang dipakai di sini. Untuk yang lebih panjang, rekam dalam beberapa
  potongan — hasil transkripsi ditambahkan ke teks yang sudah ada, bukan
  menimpanya.
- **`st.audio_input` butuh Streamlit ≥ 1.40.** Kalau versi Anda lebih
  lama, tombol rekam tidak muncul dan input teks tetap bisa dipakai.
- **Belum ada edit asesmen tersimpan.** Yang tersimpan bisa dilihat dan
  dihapus, tapi belum bisa disunting ulang.
