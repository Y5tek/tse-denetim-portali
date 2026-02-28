import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import threading

# --- AYARLAR ---
st.set_page_config(page_title="TSE Denetim Portalı", layout="wide", page_icon="🚗")

# Secrets kontrolü (Şifre boşluksuz olmalı)
try:
    GONDERICI_MAIL = st.secrets["GONDERICI_MAIL"]
    GONDERICI_SIFRE = st.secrets["GONDERICI_SIFRE"].replace(" ", "")
    ADMIN_MAIL = st.secrets["ADMIN_MAIL"]
except Exception:
    st.error("Hata: Streamlit Secrets (Mail ayarları) bulunamadı! Lütfen Cloud panelinden kontrol edin.")
    st.stop()

# --- MAİL GÖNDERME FONKSİYONU ---
def mail_at(alici, konu, icerik_html):
    msg = MIMEMultipart()
    msg['From'] = GONDERICI_MAIL
    msg['To'] = alici
    msg['Subject'] = konu
    msg.attach(MIMEText(icerik_html, 'html'))
    try:
        # Port 465 (SSL) Cloud ortamı için en kararlısıdır
        server = smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15)
        server.login(GONDERICI_MAIL, GONDERICI_SIFRE)
        server.send_message(msg)
        server.quit()
        print(f"BAŞARILI: Mail gönderildi -> {alici}")
        return True
    except Exception as e:
        print(f"MAİL HATASI: {str(e)}")
        return False

# --- VERİTABANI İŞLEMLERİ ---
def veritabanini_hazirla():
    conn = sqlite3.connect('tse_v4.db')
    conn.execute('''CREATE TABLE IF NOT EXISTS denetimler (
        id INTEGER PRIMARY KEY AUTOINCREMENT, basvuru_no TEXT, firma_adi TEXT, marka TEXT,
        arac_tipi TEXT, sasi_no TEXT UNIQUE, basvuru_tarihi TEXT, secim_tarihi TEXT, il TEXT, 
        durum TEXT DEFAULT 'Şasi Bekliyor', notlar TEXT, silme_talebi INTEGER DEFAULT 0, silme_nedeni TEXT)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS kullanicilar (
        id INTEGER PRIMARY KEY AUTOINCREMENT, kullanici_adi TEXT UNIQUE, sifre TEXT,
        rol TEXT, email TEXT, sorumlu_il TEXT, onay_durumu INTEGER DEFAULT 0)''')
    conn.commit()
    conn.close()

veritabanini_hazirla()

def verileri_getir():
    conn = sqlite3.connect('tse_v4.db')
    df = pd.read_sql_query("SELECT * FROM denetimler ORDER BY id DESC", conn)
    conn.close()
    
    # Hata veren 'Geçen Gün' hesaplaması ve sütunu tamamen kaldırıldı 
    if not df.empty:
        # Tüm tabloyu stringe çevirerek tablo motoru (Arrow) hatalarını bitiriyoruz 
        df = df.astype(str).replace(['nan', 'None', '<NA>'], '-')
    return df

# --- OTURUM YÖNETİMİ ---
if 'giris' not in st.session_state: st.session_state.update({'giris': False, 'user': "", 'rol': ""})

if not st.session_state.giris:
    c1, c2, c3 = st.columns([1, 2, 1])
    with c2:
        st.title("🇹🇷 TSE PORTAL")
        t1, t2 = st.tabs(["Giriş Yap", "Kayıt Ol"])
        with t2:
            with st.form("kayit"):
                ka, si, em, il = st.text_input("Kullanıcı Adı"), st.text_input("Şifre"), st.text_input("E-Posta"), st.selectbox("Sorumlu İl", ["Ankara", "İstanbul", "Bursa", "Kocaeli"])
                if st.form_submit_button("Kayıt Ol"):
                    conn = sqlite3.connect('tse_v4.db')
                    try:
                        conn.execute("INSERT INTO kullanicilar (kullanici_adi, sifre, rol, email, sorumlu_il) VALUES (?,?,'uzman',?,?)", (ka, si, em, il))
                        conn.commit()
                        # ADMİNE MAİL TETİKLE
                        threading.Thread(target=mail_at, args=(ADMIN_MAIL, "YENİ ÜYE TALEBİ", f"Sisteme yeni bir kayıt geldi: {ka}")).start()
                        st.success("Kayıt başarılı! Onay için mail gönderildi.")
                    except: st.error("Hata: Bu kullanıcı adı zaten var.")
                    finally: conn.close()
        with t1:
            with st.form("login"):
                ka, si = st.text_input("Kullanıcı"), st.text_input("Şifre", type="password")
                if st.form_submit_button("Giriş"):
                    conn = sqlite3.connect('tse_v4.db'); u = conn.execute("SELECT rol FROM kullanicilar WHERE kullanici_adi=? AND sifre=? AND onay_durumu=1", (ka, si)).fetchone(); conn.close()
                    if u: st.session_state.update({'giris': True, 'user': ka, 'rol': u[0]}); st.rerun()
                    else: st.error("Hesap onaylanmamış veya bilgiler hatalı!")
    st.stop()

# --- ANA EKRAN ---
df = verileri_getir()
tabs = st.tabs(["📊 Ana Tablo", "🛠️ İşlemler", "👑 Yönetici Paneli"])

with tabs[0]:
    st.subheader("📋 Güncel Denetim Listesi")
    # 'Geçen Gün' sütunu listeden çıkarıldı 
    st.dataframe(df, use_container_width=True)

with tabs[1]:
    st.subheader("⚠️ Araç Silme Talebi")
    if not df.empty:
        sasi = st.selectbox("İşlem Yapılacak Şasi:", df['sasi_no'].tolist())
        neden = st.text_area("Silme Nedeni:")
        if st.button("Talebi Admin'e Gönder"):
            conn = sqlite3.connect('tse_v4.db')
            conn.execute("UPDATE denetimler SET silme_talebi=1, silme_nedeni=? WHERE sasi_no=?", (neden, sasi))
            conn.commit(); conn.close()
            # MAİL GÖNDERİMİNİ BAŞLAT
            threading.Thread(target=mail_at, args=(ADMIN_MAIL, "⚠️ SİLME TALEBİ", f"Sistemde {sasi} numaralı şasi için silme talebi oluşturuldu.")).start()
            st.success("Talebiniz kaydedildi ve Admin'e mail gönderildi.")
    else:
        st.info("Kayıtlı araç bulunamadı.")
