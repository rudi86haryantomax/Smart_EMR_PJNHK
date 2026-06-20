import traceback
from typing import Dict, Any, List
from app.services.kamus_3s import KAMUS_3S, ekstrak_metadata, seleksi_diagnosa

def extract_clinical_data(teks_raw: str) -> List[Dict[str, Any]]:
    try:
        # Ekstraksi metadata (TTV, irama, kondisi khusus, dll.)
        meta = ekstrak_metadata(teks_raw)

        # Seleksi diagnosa berdasarkan indikator mayor/minor
        keys_terpilih, gejala_map = seleksi_diagnosa(teks_raw)

        daftar_asuhan = []
        for k in keys_terpilih:

            # k selalu berupa string dari seleksi_diagnosa
            ref = KAMUS_3S.get(k)
            if not ref:
                continue

            # Pengamanan variabel TD
            td_value = meta.get("td", "120/80")
            td_sys = td_value.split("/")[0] if "/" in td_value else td_value

            # ref adalah DiagnosisItem (dataclass), akses via atribut bukan .get()
            from dataclasses import asdict
            diagnosa_filled = ref.diagnosa.format(
                kondisi_khusus=meta.get("kondisi_khusus", ""),
                td=td_sys,
                nadi=meta.get("nadi", ""),
                rr=meta.get("rr", ""),
                irama_ekg=meta.get("irama_ekg", ""),
                skala_nyeri=meta.get("skala_nyeri", ""),
                tanda_gejala=gejala_map.get(k, "tanda klinis terkait"),
            )

            daftar_asuhan.append({
                "kode_diagnosa": ref.kode_sdki,
                "diagnosa_keperawatan": diagnosa_filled,
                "luaran_keperawatan": ref.slki,
                "rencana_intervensi": ref.siki,
            })

        return daftar_asuhan

    except Exception as e:
        error_msg = traceback.format_exc()
        return [
            {
                "kode_diagnosa": "ERR-500",
                "diagnosa_keperawatan": f"CRITICAL SYSTEM ERROR: {str(e)}",
                "luaran_keperawatan": "Terjadi galat kompilasi di mesin backend.",
                "rencana_intervensi": {"Traceback Log": error_msg},
            }
        ]