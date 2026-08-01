-- =====================================================================
-- Skema SQLite aplikasi Asuhan Keperawatan
-- =====================================================================
-- Sengaja hanya dua tabel. Aplikasi ini tidak mengelola pasien atau
-- pengguna, jadi tidak ada tabel identitas -- hanya catatan asesmen dan
-- diagnosis yang dipilih perawat.
--
-- Catatan desain: intervensi SIKI TIDAK disalin ke database. Yang
-- disimpan hanya kode diagnosis; isinya diambil dari master 3S saat
-- ditampilkan. Dengan begitu, kalau master diperbarui (mis. redaksi
-- intervensi direvisi), catatan lama ikut menampilkan versi terbaru dan
-- tidak ada dua sumber kebenaran yang bisa berbeda.
-- =====================================================================

CREATE TABLE IF NOT EXISTS asesmen (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    nomor             TEXT    NOT NULL UNIQUE,
    label             TEXT,                     -- penanda bebas, mis. "Bed 3 / Tn. A"
    data_subjektif    TEXT    NOT NULL DEFAULT '',
    data_objektif     TEXT    NOT NULL DEFAULT '',
    sumber_input      TEXT    NOT NULL DEFAULT 'teks',   -- 'teks' | 'suara' | 'campuran'
    catatan           TEXT,
    dibuat_pada       TEXT    NOT NULL,
    diperbarui_pada   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS asesmen_diagnosis (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    asesmen_id        INTEGER NOT NULL,
    kode_diagnosis    TEXT    NOT NULL,
    prioritas         INTEGER NOT NULL DEFAULT 1,
    intervensi_dipilih TEXT,                    -- JSON list tindakan yang dicentang
    dibuat_pada       TEXT    NOT NULL,
    FOREIGN KEY (asesmen_id) REFERENCES asesmen(id) ON DELETE CASCADE,
    UNIQUE (asesmen_id, kode_diagnosis)
);

CREATE INDEX IF NOT EXISTS idx_diag_asesmen ON asesmen_diagnosis(asesmen_id);
CREATE INDEX IF NOT EXISTS idx_asesmen_dibuat ON asesmen(dibuat_pada);
