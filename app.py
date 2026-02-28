import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime
import io
import threading
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import time

# --- TSE KURUMSAL VE MAİL AYARLARI ---
try:
    GONDERICI_MAIL = st.secrets["GONDERICI_MAIL"]
    GONDERICI_SIFRE = st.secrets["GONDERICI_SIFRE"].replace(" ", "")
    ADMIN_MAIL = st.secrets["ADMIN_MAIL"]
except Exception:
    st.error("Kritik Hata: Streamlit Secrets (Mail ayarları) bulunamadı!")
    st.stop()

SMTP_SUNUCU = "smtp.gmail.com"
SMTP_PORT = 465 

# --- 1. VERİTABANI MOTORU ---
def veritabanini_hazirla():
    conn = sqlite3.connect('tse_v4.db')
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS denetimler (
        id INTEGER PRIMARY KEY AUTOINCREMENT, basvuru_no TEXT, firma_adi TEXT NOT NULL, marka TEXT,
        arac_kategori TEXT, arac_tipi TEXT NOT NULL, varyant TEXT, versiyon TEXT, ticari_ad TEXT,
        gtip_no TEXT, birim TEXT, uretim_ulkesi TEXT, arac_sayisi TEXT, sasi_no TEXT UNIQUE, 
        basvuru_tarihi DATE, secim_tarihi DATE, il TEXT, durum TEXT DEFAULT 'Şasi Bekliyor',
        notlar TEXT, guncelleme_tarihi TEXT, ekleyen_kullanici TEXT, silme_talebi INTEGER DEFAULT 0, silme_nedeni TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS kullanicilar (
        id INTEGER PRIMARY KEY AUTOINCREMENT, kullanici_adi TEXT UNIQUE NOT NULL, sifre TEXT NOT NULL,
        rol TEXT NOT NULL, email TEXT, sorumlu_il TEXT, onay_durumu INTEGER DEFAULT 1, excel_yukleme_yetkisi INTEGER DEFAULT 0)''')
    conn.commit(); conn.close()

veritabanini_hazirla()

# --- BİLDİRİM MOTORU ---
def admin_bildirim_mail_at(konu, icerik):
    msg = MIMEMultipart()
    msg['From'], msg['To'], msg['Subject'] = GONDERICI_MAIL, ADMIN_MAIL, konu
    msg.attach(MIMEText(f"<html><body><h3>TSE Bildirim</h3><p>{icerik}</p></body></html>", 'html'))
    try:
        server = smtplib.SMTP_SSL(SMTP_SUNUCU, SMTP_PORT)
        server.login(GONDERICI_MAIL, GONDERICI_SIFRE)
        server.send_message(msg); server.quit()
    except: pass

# --- 2. VERİ FONKSİYONLARI ---
def durum_sayilarini_al():
    conn = sqlite3.connect('tse_v4.db')
    onay = conn.execute("SELECT COUNT(*) FROM kullanicilar WHERE onay_durumu = 0").fetchone()[0]
    silme = conn.execute("SELECT COUNT(*) FROM denetimler WHERE silme_talebi = 1").fetchone()[0]
    conn.close()
    return onay, silme

def verileri_getir():
    conn = sqlite3.connect('tse_v4.db')
    df = pd.read_sql_query("SELECT * FROM denetimler ORDER BY id DESC", conn)
    conn.close()
    df['secim_tarihi_dt'] = pd.to_datetime(df['secim_tarihi'], errors='coerce')
    bugun = pd.to_datetime(datetime.now().strftime("%Y-%m-%d"))
    df['Geçen Gün'] = (bugun - df['secim_tarihi_dt']).dt.days.apply(lambda x: str(int(x)) if pd.notnull(x) else '-')
    df['secim_tarihi'] = df['secim_tarihi_dt'].dt.strftime('%Y-%m-%d').fillna('-')
    for c in df.columns: 
        if c not in ['Geçen Gün', 'secim_tarihi_dt']: df[c] = df[c].fillna('-')
    return df

def satir_boya(row): 
    if row['durum'] == 'Şasi Bekliyor': return ['background-color: rgba(255, 193, 7, 0.3)'] * len(row)
    elif row['durum'] == 'Tamamlandı - Olumlu': return ['background-color: rgba(40, 167, 69, 0.3)'] * len(row)
    elif row['durum'] == 'Tamamlandı - Olumsuz': return ['background-color: rgba(220, 53, 69, 0.3)'] * len(row)
    return [''] * len(row)

# --- 3. OTURUM YÖNETİMİ ---
if 'giris_yapildi' not in st.session_state:
    st.session_state.update({'giris_yapildi': False, 'kullanici_adi': "", 'rol': "", 'sorumlu_il': ""})

def durum_guncelle_by_id(kayit_id, sasi_no, yeni_durum, notlar, starih="MEVCUT", talep_et_silme=False, silme_nedeni=""):
    conn = sqlite3.connect('tse_v4.db'); g_ani = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if starih == "MEVCUT": conn.cursor().execute('UPDATE denetimler SET sasi_no=?, durum=?, notlar=?, guncelleme_tarihi=?, silme_talebi=?, silme_nedeni=? WHERE id=?', (sasi_no, yeni_durum, notlar, g_ani, 1 if talep_et_silme else 0, silme_nedeni, kayit_id))
    else: conn.cursor().execute('UPDATE denetimler SET sasi_no=?, durum=?, secim_tarihi=?, notlar=?, guncelleme_tarihi=?, silme_talebi=?, silme_nedeni=? WHERE id=?', (sasi_no, yeni_durum, starih, notlar, g_ani, 1 if talep_et_silme else 0, silme_nedeni, kayit_id))
    conn.commit(); conn.close()
    if talep_et_silme: threading.Thread(target=admin_bildirim_mail_at, args=("⚠️ YENİ SİLME TALEBİ", f"{sasi_no} için talep var.")).start()

# --- 4. GİRİŞ VE ANA EKRAN ---
if not st.session_state.giris_yapildi:
    _, c2, _ = st.columns([1, 2, 1])
    with c2:
        st.title("🇹🇷 TSE PORTAL")
        tg, tk = st.tabs(["Giriş", "Kayıt"])
        with tg:
            with st.form("login"):
                ka, si = st.text_input("Kullanıcı"), st.text_input("Şifre", type="password")
                if st.form_submit_button("Giriş", use_container_width=True):
                    conn = sqlite3.connect('tse_v4.db'); u = conn.cursor().execute("SELECT rol, sorumlu_il, onay_durumu FROM kullanicilar WHERE kullanici_adi=? AND sifre=?", (ka, si)).fetchone(); conn.close()
                    if u and u[2]==1: st.session_state.update({'giris_yapildi':True, 'kullanici_adi':ka, 'rol':u[0], 'sorumlu_il':u[1]}); st.rerun()
                    else: st.error("Yetkisiz giriş.")
    st.stop()

b_onay, b_silme = durum_sayilarini_al()
df = verileri_getir()

with st.sidebar:
    st.header("TSE PANEL")
    st.write(f"👤 **{st.session_state.kullanici_adi}**")
    if st.button("🚪 Çıkış"): st.session_state.clear(); st.rerun()

tabs = st.tabs(["📊 Ana Tablo", "🛠️ İşlemler", "📥 Veri Girişi"])

with tabs[0]:
    istenen = ['sasi_no', 'durum', 'secim_tarihi', 'Geçen Gün', 'marka', 'arac_tipi', 'firma_adi', 'il']
    display_df = df[[c for c in istenen if c in df.columns]]
    st.dataframe(display_df.style.apply(satir_boya, axis=1), use_container_width=True)

with tabs[1]:
    st.subheader("Numune İşlemleri")
    # Mevcut şasi atama ve güncelleme kodların buraya gelecek (değişmediği için özetlendi)

# --- DÜZELTİLEN KISIM: EXCEL AKTARMA ---
with tabs[2]:
    st.subheader("📥 Veri Girişi")
    c_form, c_excel = st.columns(2)
    with c_form:
        with st.form("manuel"):
            st.write("Elden Kayıt")
            bn, fa, ma, ti, sn = st.text_input("B.No"), st.text_input("Firma"), st.text_input("Marka"), st.text_input("Tip"), st.text_input("Şasi")
            if st.form_submit_button("Ekle"):
                conn = sqlite3.connect('tse_v4.db')
                conn.cursor().execute("INSERT INTO denetimler (firma_adi, marka, arac_tipi, sasi_no, basvuru_no, durum, basvuru_tarihi, secim_tarihi, il) VALUES (?,?,?,?,?, 'Şasi Bekliyor', ?, ?, ?)", (fa, ma, ti, sn, bn, datetime.now().strftime("%Y-%m-%d"), datetime.now().strftime("%Y-%m-%d"), st.session_state.sorumlu_il))
                conn.commit(); conn.close(); st.rerun()
    
    with c_excel:
        up = st.file_uploader("Excel Yükle", type=['xlsx'])
        if up:
            temp_df = pd.read_excel(up)
            st.write("Dosya içeriği algılandı.")
            if st.button("Sisteme Aktar"):
                conn = sqlite3.connect('tse_v4.db')
                basarili = 0
                for _, row in temp_df.iterrows():
                    try:
                        conn.execute("""INSERT INTO denetimler 
                            (basvuru_no, firma_adi, marka, arac_tipi, il, durum, basvuru_tarihi) 
                            VALUES (?, ?, ?, ?, ?, 'Şasi Bekliyor', ?)""", 
                            (str(row.get('Başvuru No', '-')), str(row.get('Firma Adı', '-')), 
                             str(row.get('Marka', '-')), str(row.get('Araç Tipi', '-')), 
                             st.session_state.sorumlu_il, datetime.now().strftime("%Y-%m-%d")))
                        basarili += 1
                    except: continue
                conn.commit(); conn.close()
                st.success(f"{basarili} yeni kayıt tabloya işlendi!"); time.sleep(1); st.rerun()

if st.session_state.rol == "admin":
    # Yönetici paneli kodların (onay/silme)
    pass
