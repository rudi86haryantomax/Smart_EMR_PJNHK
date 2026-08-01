"""
tests/test_ppk.py
==========================================
Test alur dokter: repository PPK, service, dan pemisahan profesi.

Menguji juga hal yang paling mudah salah pada alur ini: apakah kondisi
kritis tetap terlihat meski skor kecocokan katanya rendah. Pada nyeri
dada, diseksi aorta sering kalah skor dari SKA padahal tatalaksananya
berlawanan — antikoagulan menyelamatkan pada SKA, membahayakan pada
diseksi.

Jalankan:
    cd tests && python test_ppk.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["ASUHAN_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="ppk_test_"), "t.db")

_st = types.ModuleType("streamlit")
_st.secrets = {}
sys.modules.setdefault("streamlit", _st)

from core import profesi as prof  # noqa: E402
from repositories.ppk_repository import PpkRepository  # noqa: E402
from services.ppk_service import KODE_KRITIS, PpkService  # noqa: E402

PASS = 0
FAIL = 0


def check(label, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}  {extra}")


def kode_teratas(service, teks, n=5):
    return [u["kode"] for u in service.usulkan(teks, limit=n)]


def main() -> int:
    repo = PpkRepository()
    service = PpkService()

    print("=" * 62)
    print("TEST 1 -- Integritas data PPK")
    print("=" * 62)
    laporan = repo.validate()
    check("Data PPK valid", laporan["valid"], laporan["masalah"][:3])
    check("65 PPK termuat", laporan["jumlah"] == 65, laporan["jumlah"])
    check("Tidak ada kode duplikat", len(repo.all_codes()) == len(set(repo.all_codes())))
    check("Peringatan draf tercantum di meta", "DRAF" in repo.peringatan.upper(), repo.peringatan[:60])
    check("Semua punya referensi", all(e.get("referensi") for e in repo.all()))
    check("Semua punya ICD-10", all(e.get("icd10") for e in repo.all()))

    print("\n" + "=" * 62)
    print("TEST 2 -- Struktur kriteria & tatalaksana")
    print("=" * 62)
    for kode in ["PPK.CV.001", "PPK.CV.003", "PPK.CV.012"]:
        kriteria = repo.kriteria(kode)
        tata = repo.tatalaksana(kode)
        check(f"{kode}: 4 bagian kriteria lengkap",
              all(k in kriteria for k in
                  ("anamnesis", "pemeriksaan_fisik", "penunjang", "kriteria_diagnosis")))
        check(f"{kode}: 4 bagian tatalaksana lengkap",
              all(k in tata for k in ("awal", "farmakologis", "non_farmakologis", "rujukan")))

    check("Ambil satu bagian saja mengembalikan list",
          isinstance(repo.tatalaksana("PPK.CV.001", "awal"), list))
    check("Kode tak dikenal -> dict/list kosong",
          repo.kriteria("PPK.XX.999") == {} and repo.tatalaksana("PPK.XX.999", "awal") == [])

    print("\n" + "=" * 62)
    print("TEST 3 -- Finder & filter")
    print("=" * 62)
    e = repo.find("PPK.CV.001")
    check("find() ketemu", e is not None)
    check("Nama benar", e and "STEMI" in e["nama"], e and e["nama"])
    check("find() case-insensitive", repo.find("ppk.cv.001") is not None)
    check("exists() bekerja", repo.exists("PPK.CV.013") and not repo.exists("PPK.XX.999"))
    check("12 kategori", len(repo.kategori_list()) == 12, len(repo.kategori_list()))
    check("Kategori Aritmia berisi 14", len(repo.by_kategori("Aritmia")) == 14,
          len(repo.by_kategori("Aritmia")))
    check("Kategori Gagal Jantung berisi 6",
          len(repo.by_kategori("Gagal Jantung")) == 6, len(repo.by_kategori("Gagal Jantung")))
    check("Kategori Penyakit Katup Jantung berisi 6",
          len(repo.by_kategori("Penyakit Katup Jantung")) == 6,
          len(repo.by_kategori("Penyakit Katup Jantung")))
    check("Kategori 'Kegawatan Kardiovaskular' berisi 12",
          len(repo.by_kategori("Kegawatan Kardiovaskular")) == 12,
          len(repo.by_kategori("Kegawatan Kardiovaskular")))

    check("search by nama", "PPK.CV.004" in [x["kode"] for x in repo.search("fibrilasi")])
    check("search by ICD-10", len(repo.search("I48")) >= 1)
    check("search by kategori", len(repo.search("aritmia")) >= 1)
    check("search kosong -> []", repo.search("") == [])

    try:
        repo.get("PPK.XX.999")
        check("get() melempar NotFoundError", False)
    except Exception as exc:
        check("get() melempar NotFoundError", type(exc).__name__ == "NotFoundError")

    print("\n" + "=" * 62)
    print("TEST 4 -- Usulan PPK dari temuan klinis")
    print("=" * 62)

    stemi = kode_teratas(service,
        "nyeri dada retrosternal 30 menit menjalar ke lengan kiri, keringat dingin, "
        "EKG elevasi ST di sadapan anterior", 3)
    check("Gambaran STEMI -> PPK.CV.001 peringkat 1", stemi and stemi[0] == "PPK.CV.001", stemi)

    diseksi = kode_teratas(service,
        "nyeri dada hebat mendadak menjalar ke punggung, tekanan darah berbeda antar lengan, "
        "nadi asimetris", 3)
    check("Gambaran diseksi -> PPK.CV.012 peringkat 1", diseksi and diseksi[0] == "PPK.CV.012", diseksi)

    gjk = kode_teratas(service,
        "sesak memberat, ortopnea, bengkak tungkai, JVP meningkat, ronkhi basal", 4)
    check("Gambaran gagal jantung -> PPK.CV.003 muncul", "PPK.CV.003" in gjk, gjk)

    fa = kode_teratas(service, "berdebar, nadi ireguler ireguler, EKG tidak ada gelombang P", 3)
    check("Gambaran FA -> PPK.CV.004 muncul", "PPK.CV.004" in fa, fa)

    henti = kode_teratas(service,
        "pasien kolaps mendadak, tidak responsif, tidak bernapas, nadi karotis tidak teraba", 3)
    check("Gambaran henti jantung -> PPK.CV.013 peringkat 1",
          henti and henti[0] == "PPK.CV.013", henti)

    emboli = kode_teratas(service,
        "sesak mendadak, nyeri dada pleuritik, riwayat imobilisasi lama pascaoperasi", 4)
    check("Gambaran emboli paru -> PPK.CV.010 muncul", "PPK.CV.010" in emboli, emboli)

    print("\n  -- kelompok aritmia (tahap 1 perluasan) --")
    aritmia = [
        ("takikardia QRS lebar teratur, riwayat infark, hipotensi", "PPK.CV.022", "VT"),
        ("kolaps, tidak responsif, nadi tidak teraba, undulasi tidak teratur", "PPK.CV.023", "VF"),
        ("takikardia ventrikel polimorfik puntiran, QTc memanjang, hipokalemia", "PPK.CV.024", "Torsades"),
        ("bradikardia 35 kali per menit, sinkop, disosiasi P dan QRS", "PPK.CV.028", "Blok AV total"),
        ("berdebar mendadak berhenti mendadak, QRS sempit teratur, usia muda", "PPK.CV.017", "AVNRT"),
        ("interval PR pendek, gelombang delta, QRS melebar", "PPK.CV.018", "WPW"),
        ("EKG gigi gergaji sadapan inferior, laju ventrikel 150 teratur", "PPK.CV.016", "Atrial flutter"),
        ("interval PR konstan lalu gelombang P tidak dihantarkan, pusing", "PPK.CV.027", "Mobitz II"),
        ("bradikardia sinus, jeda sinus, laju tidak naik saat aktivitas", "PPK.CV.025", "Sick sinus"),
    ]
    for teks, kode, nama in aritmia:
        hasil = kode_teratas(service, teks, 3)
        check(f"{nama} -> {kode} di 3 besar", kode in hasil, hasil)

    print("\n  -- gagal jantung & katup (tahap 2 perluasan) --")
    tahap2 = [
        ("sesak aktivitas, ortopnea, edema, fraksi ejeksi 30 persen", "PPK.CV.029", "HFrEF"),
        ("gejala gagal jantung, fraksi ejeksi 45 persen", "PPK.CV.030", "HFmrEF"),
        ("sesak aktivitas, fraksi ejeksi 55 persen, disfungsi diastolik, obesitas", "PPK.CV.031", "HFpEF"),
        ("JVP meningkat, hepatomegali berdenyut, asites, paru bersih", "PPK.CV.032", "Gagal jantung kanan"),
        ("murmur diastolik dekresendo, tekanan nadi melebar, nadi Corrigan", "PPK.CV.034", "AR"),
        ("murmur diastolik apeks, opening snap, riwayat demam reumatik", "PPK.CV.035", "MS"),
        ("murmur holosistolik apeks menjalar ke aksila, prolaps mitral", "PPK.CV.036", "MR"),
        ("murmur holosistolik sternum kiri bawah mengeras inspirasi, gelombang v", "PPK.CV.037", "TR"),
        ("murmur ejeksi sela iga kedua kiri, klik ejeksi, koreksi tetralogi Fallot", "PPK.CV.038", "Katup pulmonal"),
    ]
    for teks, kode, nama in tahap2:
        hasil = kode_teratas(service, teks, 3)
        check(f"{nama} -> {kode} di 3 besar", kode in hasil, hasil)

    print("\n  -- sindrom koroner (tahap 3 perluasan) --")
    tahap3 = [
        ("nyeri dada istirahat 20 menit, troponin normal berulang, depresi ST", "PPK.CV.039", "UAP"),
        ("troponin meningkat, angiografi tanpa stenosis bermakna, MRI jantung", "PPK.CV.040", "MINOCA"),
        ("nyeri dada istirahat dini hari, elevasi ST transien, membaik nitrat", "PPK.CV.041", "Vasospastik"),
        ("angina saat aktivitas, angiografi normal, cadangan aliran koroner menurun", "PPK.CV.042", "Mikrovaskular"),
        ("pascapemasangan stent koroner, antiplatelet ganda", "PPK.CV.043", "Pasca IKP"),
        ("pascabedah pintas arteri koroner, luka sternotomi", "PPK.CV.044", "Pasca CABG"),
    ]
    for teks, kode, nama in tahap3:
        # Batas 6 mengikuti jumlah yang benar-benar ditampilkan di layar.
        # MINOCA berbagi gambaran dengan miokarditis dan NSTEMI, sehingga
        # wajar berada di bawah keduanya -- yang penting tetap terlihat.
        hasil = kode_teratas(service, teks, 6)
        check(f"{nama} -> {kode} tampil di layar", kode in hasil, hasil)

    print("\n  -- miokard & perikardium (tahap 4 perluasan) --")
    tahap4 = [
        ("dilatasi ventrikel kiri, fraksi ejeksi menurun, riwayat keluarga, alkohol", "PPK.CV.045", "DCM"),
        ("ketebalan dinding 18 mm, murmur mengeras Valsava, sinkop saat aktivitas", "PPK.CV.046", "HCM"),
        ("ventrikel normal dinding menebal, atrium membesar, Kussmaul, amiloidosis", "PPK.CV.047", "RCM"),
        ("nyeri dada setelah stres emosional, ballooning apikal, koroner tidak tersumbat", "PPK.CV.048", "Takotsubo"),
        ("hipotensi, JVP meningkat, bunyi jantung menjauh, pulsus paradoksus, efusi", "PPK.CV.049", "Tamponade"),
        ("efusi perikardium pada ekokardiografi tanpa gangguan hemodinamik", "PPK.CV.050", "Efusi"),
        ("asites, JVP meningkat, Kussmaul, pericardial knock, penebalan perikardium", "PPK.CV.051", "Konstriktif"),
    ]
    for teks, kode, nama in tahap4:
        hasil = kode_teratas(service, teks, 3)
        check(f"{nama} -> {kode} di 3 besar", kode in hasil, hasil)

    check("Tamponade ditandai kritis", service.is_kritis("PPK.CV.049"))

    print("\n  -- hipertensi, aorta, infeksi, pulmonal, kegawatan (tahap 5) --")
    tahap5 = [
        ("tekanan darah 160/95 berulang, tanpa gejala, riwayat keluarga", "PPK.CV.052", "HT esensial"),
        ("tekanan darah di atas target meski tiga obat termasuk diuretik", "PPK.CV.053", "HT resisten"),
        ("hipertensi usia muda, hipokalemia spontan, rasio aldosteron renin", "PPK.CV.054", "HT sekunder"),
        ("pelebaran aorta torakalis pada CT, sindrom Marfan, suara serak", "PPK.CV.055", "Aneurisma torakal"),
        ("massa abdomen berdenyut, perokok, USG aorta abdominalis melebar", "PPK.CV.056", "Aneurisma abdominal"),
        ("penebalan dinding aorta bulan sabit tanpa lumen palsu", "PPK.CV.057", "IMH"),
        ("kemerahan bengkak kantong alat pacu jantung, demam, erosi kulit", "PPK.CV.059", "Infeksi CIED"),
        ("sesak menetap setelah emboli paru, V/Q scan defek perfusi menetap", "PPK.CV.061", "CTEPH"),
        ("kejut ICD berulang tiga kali dalam 24 jam, takikardia ventrikel", "PPK.CV.062", "Badai listrik"),
        ("syok kardiogenik tidak berespons vasoaktif, ECMO, alat bantu ventrikel", "PPK.CV.063", "MCS"),
    ]
    for teks, kode, nama in tahap5:
        hasil = kode_teratas(service, teks, 3)
        check(f"{nama} -> {kode} di 3 besar", kode in hasil, hasil)

    print("\n  -- vaskular perifer --")
    vaskular = [
        ("nyeri betis saat berjalan mereda istirahat, nadi dorsalis pedis melemah", "PPK.CV.064", "PAD klaudikasio"),
        ("nyeri tungkai saat istirahat malam, luka tidak sembuh, ankle brachial index rendah", "PPK.CV.064", "PAD iskemia kritis"),
        ("bengkak tungkai satu sisi, nyeri betis, imobilisasi lama pascaoperasi", "PPK.CV.065", "DVT"),
    ]
    for teks, kode, nama in vaskular:
        hasil = kode_teratas(service, teks, 3)
        check(f"{nama} -> {kode} di 3 besar", kode in hasil, hasil)

    # DVT dan emboli paru satu spektrum; menemukan salah satu berarti
    # menilai kemungkinan yang lain, jadi keduanya wajib muncul bersama.
    keduanya = kode_teratas(
        service, "bengkak tungkai satu sisi nyeri, sesak mendadak nyeri dada pleuritik", 4
    )
    check("DVT & emboli paru muncul berdampingan",
          "PPK.CV.065" in keduanya and "PPK.CV.010" in keduanya, keduanya)

    check("Hematoma intramural ditandai kritis (sama dengan diseksi)",
          service.is_kritis("PPK.CV.057"))
    check("Badai listrik ditandai kritis", service.is_kritis("PPK.CV.062"))

    check("Temuan kosong -> []", service.usulkan("") == [])
    check("Teks tanpa kata bermakna -> []", service.usulkan("dan atau pada dari") == [])

    print("\n" + "=" * 62)
    print("TEST 5 -- Penandaan kondisi kritis")
    print("=" * 62)
    check("14 kondisi ditandai kritis", len(KODE_KRITIS) == 14, len(KODE_KRITIS))
    check("Semua kode kritis ada di data", all(repo.exists(k) for k in KODE_KRITIS))
    check("Diseksi aorta ditandai kritis", service.is_kritis("PPK.CV.012"))
    check("Henti jantung ditandai kritis", service.is_kritis("PPK.CV.013"))
    check("Angina stabil TIDAK ditandai kritis", not service.is_kritis("PPK.CV.008"))

    hasil = service.usulkan("nyeri dada hebat, EKG elevasi ST", limit=5)
    check("Usulan menyertakan penanda 'kritis'", all("kritis" in u for u in hasil))

    # Ini inti pengujiannya: pada nyeri dada, kondisi fatal yang kalah skor
    # harus tetap muncul lewat jalur terpisah.
    teks = "nyeri dada hebat mendadak, keringat dingin, tekanan darah tinggi"
    top3 = service.usulkan(teks, limit=3)
    terlewat = service.kandidat_kritis_terlewat(teks, [u["kode"] for u in top3])
    check("Ada kondisi kritis di luar 3 besar yang tetap terdeteksi",
          len(terlewat) > 0, [t["kode"] for t in terlewat])
    check("Seluruh kandidat terlewat memang kondisi kritis",
          all(t["kode"] in KODE_KRITIS for t in terlewat))
    check("Kandidat terlewat tidak menduplikasi yang sudah muncul",
          not ({t["kode"] for t in terlewat} & {u["kode"] for u in top3}))

    print("\n" + "=" * 62)
    print("TEST 6 -- Perakitan PPK untuk tampilan")
    print("=" * 62)
    rakit = service.rakit("PPK.CV.012")
    check("rakit() berhasil", rakit is not None)
    check("Menandai kondisi kritis", rakit["kritis"] is True)
    check("Kriteria dalam 4 bagian terurut", len(rakit["kriteria"]) == 4)
    check("Tatalaksana dalam 4 bagian terurut", len(rakit["tatalaksana"]) == 4)
    check("Bagian pertama kriteria = anamnesis", rakit["kriteria"][0][0] == "anamnesis")
    check("Bagian pertama tatalaksana = awal", rakit["tatalaksana"][0][0] == "awal")
    check("ICD-10 terbawa", rakit["icd10"] == "I71.0", rakit["icd10"])
    check("Komplikasi terbawa", len(rakit["komplikasi"]) > 0)
    check("Edukasi terbawa", len(rakit["edukasi"]) > 0)
    check("rakit() kode tak dikenal -> None", service.rakit("PPK.XX.999") is None)

    print("\n" + "=" * 62)
    print("TEST 7 -- Pemisahan alur per profesi")
    print("=" * 62)
    check("Dua profesi tersedia", len(prof.PROFESI) == 2)
    check("Perawat -> halaman asesmen", prof.halaman_awal("perawat") == "asesmen")
    check("Dokter -> halaman tatalaksana", prof.halaman_awal("dokter") == "tatalaksana")
    check("Perawat tidak melihat menu tatalaksana",
          not prof.boleh_akses("tatalaksana", "perawat"))
    check("Dokter tidak melihat menu asesmen",
          not prof.boleh_akses("asesmen", "dokter"))
    check("Riwayat dapat diakses keduanya",
          prof.boleh_akses("riwayat", "perawat") and prof.boleh_akses("riwayat", "dokter"))
    check("Profesi tak dikenal -> fallback perawat", prof.normalize("apoteker") == "perawat")
    check("Profesi kosong -> fallback perawat", prof.normalize(None) == "perawat")
    check("Nama & ikon tersedia",
          prof.nama("dokter") == "Dokter" and bool(prof.ikon("dokter")))

    print("\n" + "=" * 62)
    print(f"HASIL AKHIR: {PASS} PASS, {FAIL} FAIL")
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
