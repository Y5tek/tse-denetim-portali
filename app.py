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

# --- TSE KURUMSAL VE GÜVENLİ MAİL AYARLARI ---
st.set_page_config(page_title="TSE Araç İthalat Denetim Portalı", layout="wide", page_icon="🚗")

# Secrets kontrolü
try:
    GONDERICI_MAIL = st.secrets["GONDERICI_MAIL"]
    GONDERICI_SIFRE = st.secrets["GONDERICI_SIFRE"].replace(" ", "")
    ADMIN_MAIL = st.secrets["ADMIN_MAIL"]
except Exception:
    st.error("Secrets ayarları eksik! Streamlit Cloud panelinden kontrol edin.")
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
        rol TEXT NOT NULL, email TEXT, sorumlu_il TEXT, onay_durumu INTEGER DEFAULT 0, excel_yukleme_yetkisi INTEGER DEFAULT 0)''')
    conn.commit(); conn.close()

veritabanini_hazirla()

# --- BİLDİRİM MOTORU ---
def mail_gonder(konu, icerik):
    msg = MIMEMultipart()
    msg['From'], msg['To'], msg['Subject'] = GONDERICI_MAIL, ADMIN_MAIL, konu
    msg.attach(MIMEText(f"<html><body><h3>TSE Bildirimi</h3><p>{icerik}</p></body></html>", 'html'))
    try:
        server = smtplib.SMTP_SSL(SMTP_SUNUCU, SMTP_PORT)
        server.login(GONDERICI_MAIL, GONDERICI_SIFRE)
        server.send_message(msg); server.quit()
        return True
    except: return False

# --- 2. DURUM VE VERİ ÇEKME ---
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
    
    # Tarih ve Geçen Gün Hesaplama (Geri Getirildi)
    df['secim_tarihi_dt'] = pd.to_datetime(df['secim_tarihi'], errors='coerce')
    bugun = pd.to_datetime(datetime.now().strftime("%Y-%m-%d"))
    df['Geçen Gün'] = (bugun - df['secim_tarihi_dt']).dt.days
    
    # Tabloyu metin formatına sabitle (Hataları önlemek için)
    df_display = df.copy()
    df_display['secim_tarihi'] = df_display['secim_tarihi_dt'].dt.strftime('%Y-%m-%d')
    for col in df_display.columns:
        df_display[col] = df_display[col].astype(str).replace(['nan', 'None', '<NA>'], '-')
    
    return df_display

# RENKLENDİRME FONKSİYONU (Geri Getirildi)
def satir_boya(row): 
    if row['durum'] == 'Şasi Bekliyor': return ['background-color: rgba(255, 193, 7, 0.2)'] * len(row)
    elif row['durum'] == 'Tamamlandı - Olumlu': return ['background-color: rgba(40, 167, 69, 0.2)'] * len(row)
    elif row['durum'] == 'Tamamlandı - Olumsuz': return ['background-color: rgba(220, 53, 69, 0.2)'] * len(row)
    return [''] * len(row)

# --- 3. İŞLEMLER ---
def durum_guncelle_by_id(kayit_id, sasi_no, yeni_durum, notlar, starih="MEVCUT", talep_et_silme=False, silme_nedeni=""):
    conn = sqlite3.connect('tse_v4.db'); g_ani = datetime.now().strftime("%Y-%m-%d %H:%M:%S"); sil_v = 1 if talep_et_silme else 0
    if starih == "MEVCUT": conn.cursor().execute('UPDATE denetimler SET sasi_no=?, durum=?, notlar=?, guncelleme_tarihi=?, silme_talebi=?, silme_nedeni=? WHERE id=?', (sasi_no, yeni_durum, notlar, g_ani, sil_v, silme_nedeni, kayit_id))
    else: conn.cursor().execute('UPDATE denetimler SET sasi_no=?, durum=?, secim_tarihi=?, notlar=?, guncelleme_tarihi=?, silme_talebi=?, silme_nedeni=? WHERE id=?', (sasi_no, yeni_durum, starih, notlar, g_ani, sil_v, silme_nedeni, kayit_id))
    conn.commit(); conn.close()
    if talep_et_silme: 
        threading.Thread(target=mail_gonder, args=("⚠️ YENİ SİLME TALEBİ", f"Şasi: {sasi_no}")).start()

# --- 4. OTURUM VE EKRAN ---
if 'giris_yapildi' not in st.session_state:
    st.session_state.update({'giris_yapildi': False, 'kullanici_adi': "", 'rol': "", 'sorumlu_il': ""})

if not st.session_state.giris_yapildi:
    c1, c2, c3 = st.columns([1, 2, 1])
    with c2:
        st.markdown("<h1 style='text-align: center; color: #E03131;'>🇹🇷 TSE PORTAL</h1>", unsafe_allow_html=True)
        tg, tk = st.tabs(["🔐 Giriş", "📝 Kayıt"])
        with tg:
            with st.form("l"):
                ka, si = st.text_input("Kullanıcı"), st.text_input("Şifre", type="password")
                if st.form_submit_button("Giriş"):
                    conn = sqlite3.connect('tse_v4.db'); u = conn.cursor().execute("SELECT rol, sorumlu_il, onay_durumu FROM kullanicilar WHERE kullanici_adi=? AND sifre=?", (ka, si)).fetchone(); conn.close()
                    if u and u[2]==1: st.session_state.update({'giris_yapildi':True, 'kullanici_adi':ka, 'rol':u[0], 'sorumlu_il':u[1]}); st.rerun()
                    else: st.error("Hatalı bilgiler veya onay bekleyen hesap.")
        with tk:
            with st.form("r"):
                yk, ys, ye, yil = st.text_input("Kullanıcı Adı"), st.text_input("Şifre"), st.text_input("E-Posta"), st.selectbox("İl", ["Ankara", "İstanbul", "Bursa", "Kocaeli"])
                if st.form_submit_button("Kayıt Ol"):
                    conn = sqlite3.connect('tse_v4.db')
                    conn.execute("INSERT INTO kullanicilar (kullanici_adi, sifre, rol, email, sorumlu_il, onay_durumu) VALUES (?, ?, 'kullanici', ?, ?, 0)", (yk, ys, ye, yil))
                    conn.commit(); conn.close()
                    threading.Thread(target=mail_gonder, args=("📝 YENİ KAYIT TALEBİ", f"Üye: {yk}")).start()
                    st.success("Talep Admin'e iletildi."); st.rerun()
    st.stop()

# --- 5. ANA PANEL ---
b_onay, b_silme = durum_sayilarini_al()
df = verileri_getir()

with st.sidebar:
    st.header("TSE PANEL")
    st.write(f"👤 **{st.session_state.kullanici_adi}**")
    if st.session_state.rol == "admin" and (b_onay + b_silme) > 0:
        st.error(f"🚨 {b_onay + b_silme} Bekleyen İşlem!")
    if st.button("🚪 Çıkış"): st.session_state.clear(); st.rerun()

# SEKME TANIMLARI VE SÜTUN SIRALAMASI
t_labels = ["📊 Ana Tablo", "🛠️ Numune İşlemleri", "📥 Veri Girişi"]
if st.session_state.rol == "admin": t_labels.append(f"👑 Yönetici ({b_onay+b_silme})")
tabs = st.tabs(t_labels)

with tabs[0]:
    st.subheader("📋 Denetim Listesi")
    # İSTEDİĞİN SÜTUN SIRALAMASI (Geri Getirildi)
    sutun_sirasi = ['sasi_no', 'durum', 'secim_tarihi', 'Geçen Gün', 'marka', 'arac_tipi', 'firma_adi', 'arac_kategori', 'il']
    final_df = df[[c for c in sutun_sirasi if c in df.columns]]
    
    # RENKLİ TABLO GÖSTERİMİ
    st.dataframe(final_df.style.apply(satir_boya, axis=1), use_container_width=True, height=600)

with tabs[1]:
    st.subheader("İşlem Paneli")
    i_df = df if st.session_state.rol == "admin" else df[df['il'] == st.session_state.sorumlu_il]
    
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("#### 🆕 Şasi Atama")
        bekleyen = i_df[i_df['durum'] == 'Şasi Bekliyor']
        if not bekleyen.empty:
            sel = st.selectbox("Başvuru:", options=(bekleyen['id'].astype(str) + " | " + bekleyen['basvuru_no']).tolist(), index=None)
            if sel:
                sid = int(sel.split(" |")[0]); vin = st.text_input("VIN")
                if st.button("Kaydet"):
                    durum_guncelle_by_id(sid, vin, 'Teste Gönderildi', "", starih=datetime.now().strftime("%Y-%m-%d"))
                    st.rerun()
        else: st.info("Bekleyen kayıt yok.")

    with col2:
        st.markdown("#### 🔍 Güncelleme")
        islenmis = i_df[i_df['durum'] != 'Şasi Bekliyor']
        if not islenmis.empty:
            srch = st.selectbox("Şasi Seç:", options=(islenmis['id'].astype(str) + " | " + islenmis['sasi_no']).tolist(), index=None)
            if srch:
                sid_num = int(srch.split(" |")[0])
                # HATA ÖNLEYİCİ KONTROL (Loglardaki IndexError çözümü)
                match = islenmis[islenmis['id'].astype(str) == str(sid_num)]
                if not match.empty:
                    cur = match.iloc[0]
                    with st.form("up"):
                        nd = st.selectbox("Durum", ["Teste Gönderildi", "Tamamlandı - Olumlu", "Tamamlandı - Olumsuz"])
                        sl = st.checkbox("Silme Talebi")
                        if st.form_submit_button("Güncelle"):
                            durum_guncelle_by_id(sid_num, cur['sasi_no'], nd, "", talep_et_silme=sl, silme_nedeni="Kullanıcı Talebi")
                            st.rerun()

if st.session_state.rol == "admin":
    with tabs[3]:
        st.subheader("⚙️ Yönetim")
        c_o, c_s = st.columns(2)
        with c_o:
            st.write(f"Onay Bekleyenler ({b_onay})")
            conn = sqlite3.connect('tse_v4.db'); k_df = pd.read_sql_query("SELECT * FROM kullanicilar WHERE onay_durumu=0", conn); conn.close()
            for _, r in k_df.iterrows():
                if st.button(f"Onayla: {r['kullanici_adi']}", key=f"o_{r['id']}"):
                    c = sqlite3.connect('tse_v4.db'); c.execute("UPDATE kullanicilar SET onay_durumu=1 WHERE id=?", (r['id'],)); c.commit(); c.close(); st.rerun()
        with c_s:
            st.write(f"Silme Talepleri ({b_silme})")
            for _, r in df[df['silme_talebi']=="1"].iterrows():
                if st.button(f"SİL: {r['sasi_no']}", key=f"s_{r['id']}"):
                    c = sqlite3.connect('tse_v4.db'); c.execute("DELETE FROM denetimler WHERE id=?", (r['id'],)); c.commit(); c.close(); st.rerun()
