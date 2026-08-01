"""
tests/test_asuhan.py
==========================================
Test alur aplikasi asuhan keperawatan.

Memakai berkas SQLite sementara supaya tidak menyentuh data kerja.
Streamlit di-mock karena yang diuji adalah logika, bukan tampilan.

Jalankan:
    cd tests
    python test_asuhan.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Arahkan database ke berkas sementara SEBELUM modul apa pun memuat config.
os.environ["ASUHAN_DB_PATH"] = os.path.join(
    tempfile.mkdtemp(prefix="asuhan_test_"), "test.db"
)

# Stub streamlit: hanya dibutuhkan agar core.config bisa membaca secrets.
_st = types.ModuleType("streamlit")
_st.secrets = {}
sys.modules.setdefault("streamlit", _st)

from core.kategori import kategori_dari_luaran, urutkan_prioritas  # noqa: E402
from database.connection import init_database, unit_of_work, reset_database  # noqa: E402
from models.asesmen import Asesmen, DiagnosisPilihan  # noqa: E402
from repositories.asesmen_repository import AsesmenRepository  # noqa: E402
from repositories.sdki_repository import SdkiRepository  # noqa: E402
from services import export_service  # noqa: E402
from services.diagnosis_service import DiagnosisService  # noqa: E402

PASS = 0
FAIL = 0


def check(label, condition, extra=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}  {extra}")


def main() -> int:
    init_database()
    reset_database()
    service = DiagnosisService()

    print("=" * 62)
    print("TEST 1 -- Master 3S termuat")
    print("=" * 62)
    repo = SdkiRepository()
    laporan = repo.validate()
    check("Master data valid", laporan["valid"], laporan["masalah"][:3])
    check("55 diagnosis termuat", laporan["jumlah"] == 55, laporan["jumlah"])
    check("Kategori diturunkan dari kode SLKI",
          kategori_dari_luaran("L.02008") == "Sirkulasi")

    print("\n" + "=" * 62)
    print("TEST 2 -- Usulan diagnosis dari data S & O")
    print("=" * 62)
    s = "Pasien mengeluh sesak saat berbaring, mudah lelah, sulit tidur"
    o = "Tampak edema tungkai, JVP meningkat, ronkhi basal, akral dingin"

    usulan = service.usulkan(s, o, limit=8)
    kode_usulan = [u["kode"] for u in usulan]
    check("Menghasilkan usulan", len(usulan) > 0, kode_usulan)
    check("D.0008 (Penurunan Curah Jantung) muncul", "D.0008" in kode_usulan, kode_usulan)
    check("D.0022 (Hipervolemia) muncul", "D.0022" in kode_usulan, kode_usulan)
    check("Setiap usulan menyertakan alasan (kata_cocok)",
          all(u.get("kata_cocok") for u in usulan))
    check("Setiap usulan punya kategori", all(u.get("kategori") for u in usulan))
    check("Terurut menurun berdasarkan skor",
          all(usulan[i]["skor"] >= usulan[i + 1]["skor"] for i in range(len(usulan) - 1)))

    check("S dan O digabung (O saja tetap menghasilkan usulan)",
          len(service.usulkan("", o)) > 0)
    check("Input kosong -> tidak ada usulan", service.usulkan("", "") == [])

    print("\n" + "=" * 62)
    print("TEST 2b -- Kosakata sehari-hari & singkatan ICU dikenali")
    print("=" * 62)
    # Perawat menulis istilah ruangan, bukan istilah baku SDKI. Tanpa
    # pemetaan sinonim, diagnosis yang jelas relevan tidak pernah muncul.
    padanan = [
        ("slem kental banyak kuning, batuk tidak efektif", "D.0001", "slem -> sputum"),
        ("Kalium 2,9 hasil laboratorium", "D.0034", "kalium -> elektrolit"),
        ("terpasang ventilator mode SIMV, PEEP 10", "D.0004", "SIMV/PEEP -> ventilasi"),
        ("AGD asidosis, PH 7,20, PCO2 50", "LOKAL.003", "asidosis/AGD -> pH"),
    ]
    for teks, kode, keterangan in padanan:
        hasil = [u["kode"] for u in service.usulkan("", teks, limit=6)]
        check(f"{keterangan} -> {kode} muncul", kode in hasil, hasil)

    print("\n" + "=" * 62)
    print("TEST 2c -- Kasus ICU kompleks: diagnosis respirasi tidak boleh tenggelam")
    print("=" * 62)
    # Kasus nyata dari pemakaian: pasien ICU dengan ventilator, hasil AGD,
    # dan sputum purulen. Sebelum pembobotan diperbaiki, Gangguan Pertukaran
    # Gas dan Bersihan Jalan Napas terlempar ke peringkat 9-11 dan tidak
    # muncul di layar, sementara Nyeri Akut naik hanya karena cocok pada
    # kata umum "dingin" dan "gelisah".
    icu = (
        "Pasien tampak gelisah, akral dingin, TD 80/50 mmHg dengan inotropik "
        "dosis tinggi, Nadi 120 x/mnt, RR 30 x/mnt. Hasil AGD asidosis "
        "respiratorik dan metabolik PH 7,20, PCO2 50, HCO3 16, Laktat 12. "
        "Terpasang IABP. Ventilator mode SIMV FiO2 60%, PEEP 10. Slem kental, "
        "banyak dan kuning. Hasil kultur pneumonia. Anuria, CRRT. Kalium 2,9."
    )
    tampil = [u["kode"] for u in service.usulkan("", icu, limit=8)]
    check("D.0003 Gangguan Pertukaran Gas muncul di layar", "D.0003" in tampil, tampil)
    check("D.0001 Bersihan Jalan Napas muncul di layar", "D.0001" in tampil, tampil)
    check("D.0004 Gangguan Ventilasi Spontan muncul", "D.0004" in tampil, tampil)
    check("LOKAL.003 Ketidakseimbangan Asam Basa muncul", "LOKAL.003" in tampil, tampil)
    check("D.0008 Penurunan Curah Jantung muncul", "D.0008" in tampil, tampil)

    # Penanda laboratorium alfanumerik harus terbaca; pola tokenizer lama
    # membuang "PCO2" menjadi "pco" lalu menghapusnya karena terlalu pendek.
    from repositories.sdki_repository import _tokenize
    for penanda in ("PCO2 50", "HCO3 16", "FiO2 60", "SpO2 92"):
        check(f"Penanda '{penanda.split()[0]}' terbaca sebagai kata kunci",
              len(_tokenize(penanda)) > 0, _tokenize(penanda))

    print("\n" + "=" * 62)
    print("TEST 3 -- Usulan urutan prioritas (kaidah ABC)")
    print("=" * 62)
    acak = ["D.0080", "D.0143", "D.0001", "D.0008", "D.0077"]
    urut = service.usulkan_prioritas(acak)
    check("Respirasi (D.0001) di urutan pertama", urut[0] == "D.0001", urut)
    check("Sirkulasi (D.0008) di urutan kedua", urut[1] == "D.0008", urut)
    check("Keamanan (D.0143) sebelum Nyeri (D.0077)",
          urut.index("D.0143") < urut.index("D.0077"), urut)
    check("Psikologis (D.0080) di urutan terakhir", urut[-1] == "D.0080", urut)
    check("Jumlah tidak berubah", len(urut) == len(acak))

    aktual_risiko = urutkan_prioritas([repo.find("D.0142"), repo.find("LOKAL.006")])
    check("Aktual didahulukan atas Risiko pada kategori sama",
          aktual_risiko[0]["kode"] == "LOKAL.006", [e["kode"] for e in aktual_risiko])

    print("\n" + "=" * 62)
    print("TEST 4 -- Simpan asesmen + diagnosis (satu transaksi)")
    print("=" * 62)
    asesmen = Asesmen(
        label="Bed 3",
        data_subjektif=s,
        data_objektif=o,
        sumber_input="campuran",
        catatan="Uji coba",
    )
    check("Asesmen dengan S/O terisi dinyatakan valid", asesmen.is_valid())
    check("Asesmen kosong dinyatakan tidak valid", not Asesmen().is_valid())

    pilihan = [
        DiagnosisPilihan("D.0001", 1, ["Monitor pola napas (frekuensi, kedalaman, usaha napas)"]),
        DiagnosisPilihan("D.0008", 2, []),
        DiagnosisPilihan("D.0143", 3, ["Pasang handrail tempat tidur"]),
    ]

    with unit_of_work() as conn:
        r = AsesmenRepository(conn)
        asesmen_id = r.create(asesmen)
        jumlah = r.set_diagnosis(asesmen_id, pilihan)

    check("Asesmen tersimpan", asesmen_id > 0)
    check("3 diagnosis tersimpan", jumlah == 3, jumlah)

    with unit_of_work() as conn:
        tersimpan = AsesmenRepository(conn).find(asesmen_id)

    check("Asesmen terbaca kembali", tersimpan is not None)
    check("Nomor ter-generate format ASM-",
          tersimpan.nomor.startswith("ASM-"), tersimpan.nomor)
    check("Data S tersimpan utuh", tersimpan.data_subjektif == s)
    check("Data O tersimpan utuh", tersimpan.data_objektif == o)
    check("Penanda tersimpan", tersimpan.label == "Bed 3")
    check("3 diagnosis terbaca", len(tersimpan.diagnosis) == 3)
    check("Urutan prioritas terjaga",
          [d.kode_diagnosis for d in tersimpan.diagnosis] == ["D.0001", "D.0008", "D.0143"],
          [d.kode_diagnosis for d in tersimpan.diagnosis])
    check("Intervensi terpilih tersimpan (JSON round-trip)",
          tersimpan.diagnosis[0].intervensi_dipilih ==
          ["Monitor pola napas (frekuensi, kedalaman, usaha napas)"],
          tersimpan.diagnosis[0].intervensi_dipilih)

    print("\n" + "=" * 62)
    print("TEST 5 -- Tabel asuhan lengkap")
    print("=" * 62)
    tabel = service.rakit_tabel(tersimpan.diagnosis)
    check("3 baris tabel", len(tabel) == 3, len(tabel))
    baris1 = tabel[0]
    check("Baris 1 prioritas 1", baris1["prioritas"] == 1)
    check("Nama diagnosis terisi", baris1["nama"] == "Bersihan Jalan Napas Tidak Efektif")
    check("Luaran SLKI terisi", baris1["luaran"]["kode"] == "L.01001", baris1["luaran"])
    check("Kategori terisi", baris1["kategori"] == "Respirasi", baris1["kategori"])
    check("4 kategori intervensi lengkap",
          all(k in baris1["intervensi"] for k in
              ("observasi", "terapeutik", "edukasi", "kolaborasi")))
    check("Kriteria diagnostik ikut terbawa", bool(baris1["kriteria"].get("mayor")))
    check("Intervensi dipilih terbawa ke tabel",
          len(baris1["intervensi_dipilih"]) == 1)

    # Kode yang tidak ada di master harus tetap muncul, bukan hilang diam-diam
    hilang = service.rakit_tabel([DiagnosisPilihan("D.9999", 1, [])])
    check("Kode tak dikenal tetap ditampilkan", len(hilang) == 1)
    check("Kode tak dikenal ditandai 'hilang'", hilang[0]["hilang"] is True)

    print("\n" + "=" * 62)
    print("TEST 6 -- Ekspor")
    print("=" * 62)
    md = export_service.ke_markdown(tersimpan, tabel)
    check("Markdown memuat nomor asesmen", tersimpan.nomor in md)
    check("Markdown memuat data S", "sesak saat berbaring" in md)
    check("Markdown memuat luaran SLKI", "L.01001" in md)
    check("Markdown memuat semua diagnosis",
          all(k in md for k in ("D.0001", "D.0008", "D.0143")))

    # Ekspor CSV diganti .docx dan .xlsx yang berisi tabel askep sungguhan;
    # rinciannya diuji di tests/test_ekspor.py.
    check("Ekspor Word menghasilkan berkas",
          isinstance(export_service.ke_docx(tersimpan, tabel), bytes))
    check("Ekspor Excel menghasilkan berkas",
          isinstance(export_service.ke_xlsx(tersimpan, tabel), bytes))
    check("Nama berkas memakai nomor asesmen",
          export_service.nama_berkas(tersimpan, "md") == f"{tersimpan.nomor}.md")

    print("\n" + "=" * 62)
    print("TEST 7 -- Riwayat & penomoran")
    print("=" * 62)
    with unit_of_work() as conn:
        r = AsesmenRepository(conn)
        id2 = r.create(Asesmen(data_subjektif="nyeri dada", data_objektif="meringis"))
        r.set_diagnosis(id2, [DiagnosisPilihan("D.0077", 1, [])])
        daftar = r.list_recent()
        total = r.total()

    check("2 asesmen tersimpan", total == 2, total)
    check("Riwayat mengembalikan 2 baris", len(daftar) == 2)
    check("Riwayat memuat jumlah diagnosis",
          sorted(d["jumlah_diagnosis"] for d in daftar) == [1, 3],
          [d["jumlah_diagnosis"] for d in daftar])
    check("Nomor asesmen unik",
          len({d["nomor"] for d in daftar}) == 2, [d["nomor"] for d in daftar])
    check("Nomor berurutan naik",
          daftar[0]["nomor"] != daftar[1]["nomor"])

    print("\n" + "=" * 62)
    print("TEST 8 -- Hapus & foreign key cascade")
    print("=" * 62)
    with unit_of_work() as conn:
        r = AsesmenRepository(conn)
        terhapus = r.delete(asesmen_id)
        sisa_dx = r.fetch_all(
            "SELECT * FROM asesmen_diagnosis WHERE asesmen_id=?", (asesmen_id,)
        )
        sisa_total = r.total()

    check("Asesmen terhapus", terhapus == 1)
    check("Diagnosis ikut terhapus (ON DELETE CASCADE aktif)",
          len(sisa_dx) == 0, len(sisa_dx))
    check("Tersisa 1 asesmen", sisa_total == 1, sisa_total)

    print("\n" + "=" * 62)
    print("TEST 9 -- Ganti daftar diagnosis pada asesmen yang sama")
    print("=" * 62)
    with unit_of_work() as conn:
        r = AsesmenRepository(conn)
        r.set_diagnosis(id2, [
            DiagnosisPilihan("D.0080", 1, []),
            DiagnosisPilihan("D.0077", 2, []),
        ])
        hasil = r.diagnosis_of(id2)

    check("Daftar tergantikan seluruhnya (bukan bertambah)", len(hasil) == 2, len(hasil))
    check("Urutan baru tersimpan",
          [d.kode_diagnosis for d in hasil] == ["D.0080", "D.0077"],
          [d.kode_diagnosis for d in hasil])

    print("\n" + "=" * 62)
    print(f"HASIL AKHIR: {PASS} PASS, {FAIL} FAIL")
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
