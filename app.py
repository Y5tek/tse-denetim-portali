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

# Secrets'tan mail bilgilerini çekiyoruz
try:
    GONDERICI_MAIL = st.secrets["GONDERICI_MAIL"]
    GONDERICI_SIFRE = st.secrets["GONDERICI_SIFRE"].replace(" ", "")
    ADMIN_MAIL = st.secrets["ADMIN_MAIL"]
except Exception:
    st.error("Secrets ayarları eksik! Streamlit Cloud panelinden Secrets kısmını kontrol edin.")
    st.stop()

SMTP_SUNUCU = "smtp.gmail.com"
SMTP_PORT = 465 # SSL Portu Cloud için en güvenlisidir

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
    msg.attach(MIMEText(f"<html><body><h3>TSE Portal Bildirimi</h3><p>{icerik}</p></body></html>", 'html'))
    try:
        server = smtplib.SMTP_SSL(SMTP_SUNUCU, SMTP_PORT)
        server.login(GONDERICI_MAIL, GONDERICI_SIFRE)
        server.send_message(msg); server.quit()
        return True
    except Exception as e:
        print(f"MAİL HATASI: {str(e)}")
        return False

# --- 2. DURUM SORGULARI ---
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
    
    if not df.empty:
        # Arrow hatasını engellemek ve veriyi temizlemek için her şeyi stringe çeviriyoruz
        df_display = df.copy()
        df_display = df_display.astype(str).replace(['nan', 'None', '<NA>'], '-')
        return df_display
    return df

# --- 3. İŞLEM FONKSİYONU ---
def durum_guncelle_by_id(kayit_id, sasi_no, yeni_durum, notlar, starih="MEVCUT", talep_et_silme=False, silme_nedeni=""):
    conn = sqlite3.connect('tse_v4.db'); g_ani = datetime.now().strftime("%Y-%m-%d %H:%M:%S"); sil_v = 1 if talep_et_silme else 0
    if starih == "MEVCUT": conn.cursor().execute('UPDATE denetimler SET sasi_no=?, durum=?, notlar=?, guncelleme_tarihi=?, silme_talebi=?, silme_nedeni=? WHERE id=?', (sasi_no, yeni_durum, notlar, g_ani, sil_v, silme_nedeni, kayit_id))
    else: conn.cursor().execute('UPDATE denetimler SET sasi_no=?, durum=?, secim_tarihi=?, notlar=?, guncelleme_tarihi=?, silme_talebi=?, silme_nedeni=? WHERE id=?', (sasi_no, yeni_durum, starih, notlar, g_ani, sil_v, silme_nedeni, kayit_id))
    conn.commit(); conn.close()
    if talep_et_silme: 
        threading.Thread(target=mail_gonder, args=("⚠️ YENİ SİLME TALEBİ", f"Şasi: {sasi_no}<br>Neden: {silme_nedeni}")).start()

# --- 4. GİRİŞ EKRANI ---
if 'giris_yapildi' not in st.session_state:
    st.session_state.update({'giris_yapildi': False, 'kullanici_adi': "", 'rol': "", 'sorumlu_il': ""})

if not st.session_state.giris_yapildi:
    c1, c2, c3 = st.columns([1, 2, 1])
    with c2:
        st.markdown("<h1 style='text-align: center; color: #E03131;'>🇹🇷 TSE PORTAL</h1>", unsafe_allow_html=True)
        tg, tk = st.tabs(["🔐 Giriş", "📝 Kayıt"])
        with tg:
            with st.form("login"):
                ka, si = st.text_input("Kullanıcı"), st.text_input("Şifre", type="password")
                if st.form_submit_button("Giriş Yap", use_container_width=True):
                    conn = sqlite3.connect('tse_v4.db'); u = conn.cursor().execute("SELECT rol, sorumlu_il, onay_durumu FROM kullanicilar WHERE kullanici_adi=? AND sifre=?", (ka, si)).fetchone(); conn.close()
                    if u:
                        if u[2]==0: st.warning("Oturum onayı bekleniyor.")
                        else: st.session_state.update({'giris_yapildi':True, 'kullanici_adi':ka, 'rol':u[0], 'sorumlu_il':u[1]}); st.rerun()
                    else: st.error("❌ Hatalı giriş.")
        with tk:
            with st.form("register"):
                yk, ys, ye, yil = st.text_input("Kullanıcı Adı"), st.text_input("Şifre", type="password"), st.text_input("E-Posta"), st.selectbox("İl", ["Ankara", "İstanbul", "Bursa", "Kocaeli"])
                if st.form_submit_button("Kayıt Ol"):
                    try:
                        conn = sqlite3.connect('tse_v4.db'); conn.cursor().execute("INSERT INTO kullanicilar (kullanici_adi, sifre, rol, email, sorumlu_il, onay_durumu) VALUES (?, ?, 'kullanici', ?, ?, 0)", (yk, ys, ye, yil)); conn.commit(); conn.close()
                        threading.Thread(target=mail_gonder, args=("📝 YENİ KAYIT TALEBİ", f"Yeni üye: {yk}")).start()
                        st.success("Talep gönderildi."); time.sleep(1); st.rerun()
                    except: st.error("Kullanıcı adı mevcut.")
    st.stop()

# --- 5. ANA DASHBOARD ---
b_onay, b_silme = durum_sayilarini_al()
df = verileri_getir()

with st.sidebar:
    st.header("TSE PANEL")
    st.write(f"👤 **{st.session_state.kullanici_adi}**")
    if st.session_state.rol == "admin" and (b_onay + b_silme) > 0:
        st.error(f"🚨 {b_onay + b_silme} Bekleyen İşlem!")
    if st.button("🚪 Çıkış"): st.session_state.clear(); st.rerun()

tabs = st.tabs(["📊 Ana Tablo", "🛠️ Numune İşlemleri", "📥 Veri Girişi", f"👑 Yönetici ({b_onay+b_silme})"]) if st.session_state.rol == "admin" else st.tabs(["📊 Ana Tablo", "🛠️ Numune İşlemleri", "📥 Veri Girişi"])

with tabs[0]:
    st.subheader("📋 Denetim Listesi")
    st.dataframe(df, use_container_width=True)

with tabs[1]:
    st.subheader("İşlem Paneli")
    # Kullanıcı sadece kendi ilindekileri görür, admin hepsini
    i_df = df if st.session_state.rol == "admin" else df[df['il'] == st.session_state.sorumlu_il]
    
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("#### 🆕 Şasi Atama")
        bekleyen = i_df[i_df['durum'] == 'Şasi Bekliyor']
        if not bekleyen.empty:
            sel = st.selectbox("Başvuru Seç:", options=(bekleyen['id'].astype(str) + " | " + bekleyen['basvuru_no']).tolist(), index=None)
            if sel:
                sid = int(sel.split(" |")[0]); vin = st.text_input("VIN (Şasi)")
                if st.button("Kaydet"):
                    durum_guncelle_by_id(sid, vin, 'Teste Gönderildi', "", starih=datetime.now().strftime("%Y-%m-%d"))
                    st.rerun()
        else: st.info("Bekleyen başvuru yok.")

    with col2:
        st.markdown("#### 🔍 Güncelleme")
        islenmis = i_df[i_df['durum'] != 'Şasi Bekliyor']
        if not islenmis.empty:
            srch = st.selectbox("Şasi Ara:", options=(islenmis['id'].astype(str) + " | " + islenmis['sasi_no']).tolist(), index=None)
            if srch:
                # GÖRSELDEKİ HATAYI ÇÖZEN KONTROLLÜ KISIM
                sid_str = srch.split(" |")[0]
                matching_rows = islenmis[islenmis['id'] == sid_str]
                if not matching_rows.empty:
                    cur = matching_rows.iloc[0]
                    with st.form("upd"):
                        nd = st.selectbox("Durum", ["Teste Gönderildi", "Tamamlandı - Olumlu", "Tamamlandı - Olumsuz"])
                        sl = st.checkbox("Silme Talebi")
                        if st.form_submit_button("Güncelle"):
                            durum_guncelle_by_id(int(sid_str), cur['sasi_no'], nd, "", talep_et_silme=sl, silme_nedeni="Kullanıcı Talebi")
                            st.rerun()
        else: st.info("İşlenmiş kayıt yok.")

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
