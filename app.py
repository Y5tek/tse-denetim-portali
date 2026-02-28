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

# --- TSE KURUMSAL VE MAİL AYARLARI (SECRETS ENTEGRASYONU) ---
st.set_page_config(page_title="TSE Araç İthalat Denetim Portalı", layout="wide", page_icon="🚗")

# Secrets'tan bilgileri çekiyoruz
try:
    GONDERICI_MAIL = st.secrets["GONDERICI_MAIL"]
    GONDERICI_SIFRE = st.secrets["GONDERICI_SIFRE"]
    ADMIN_MAIL = st.secrets["ADMIN_MAIL"]
except Exception as e:
    st.error("Secrets ayarları eksik! Lütfen Streamlit Cloud panelinden Secrets kısmını kontrol edin.")
    st.stop()

SMTP_SUNUCU = "smtp.gmail.com"
SMTP_PORT = 465  # Cloud ortamında 465 (SSL) daha kararlıdır

# --- GENEL MAİL GÖNDERİM MOTORU (SSL DESTEKLİ) ---
def mail_gonder(alici, konu, icerik_html):
    msg = MIMEMultipart()
    msg['From'] = GONDERICI_MAIL
    msg['To'] = alici
    msg['Subject'] = konu
    msg.attach(MIMEText(icerik_html, 'html'))
    
    try:
        # SSL üzerinden güvenli bağlantı (Cloud uyumlu)
        server = smtplib.SMTP_SSL(SMTP_SUNUCU, SMTP_PORT)
        server.login(GONDERICI_MAIL, GONDERICI_SIFRE)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        # Hatayı terminale bas (Logs kısmında görünür)
        print(f"MAİL HATASI ({alici}): {str(e)}")
        return False

# --- İL BAZLI MAİL DAĞITIM MOTORU ---
def excel_yukle_ve_mail_at(df_excel):
    conn = sqlite3.connect('tse_v4.db')
    # İllerin listesini al ve standartlaştır
    df_excel['STANDART_IL'] = df_excel['Birim'].apply(lambda x: "Ankara" if "ANKARA" in str(x).upper() 
                                                     else ("Kocaeli" if "KOCAELİ" in str(x).upper() or "KOCAELI" in str(x).upper()
                                                     else ("İstanbul" if "İSTANBUL" in str(x).upper() or "ISTANBUL" in str(x).upper()
                                                     else "Diğer")))
    
    iller = df_excel['STANDART_IL'].unique()
    
    for il in iller:
        df_il = df_excel[df_excel['STANDART_IL'] == il]
        # İlgili ildeki aktif ve onaylı uzmanları bul
        uzmanlar = pd.read_sql_query(f"SELECT email FROM kullanicilar WHERE sorumlu_il = '{il}' AND onay_durumu = 1", conn)
        
        if not uzmanlar.empty:
            html_tablo = df_il.to_html(index=False, border=1)
            icerik = f"""
            <html><body>
                <h3>Sayın Uzman,</h3>
                <p>Sorumlu olduğunuz <b>{il}</b> bölgesi için sisteme yeni numune kayıtları eklenmiştir.</p>
                <br>{html_tablo}<br>
                <p>Lütfen sisteme giriş yaparak şasi atamalarını gerçekleştiriniz.</p>
            </body></html>
            """
            for m in uzmanlar['email']:
                if m and "@" in m:
                    mail_gonder(m, f"TSE Yeni Numune Bildirimi - {il}", icerik)
    conn.close()

# --- 1. VERİTABANI VE DİĞER FONKSİYONLAR (DEĞİŞMEDİ) ---
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

# --- GİRİŞ VE ANA AKIŞ ---
if 'giris_yapildi' not in st.session_state:
    st.session_state.update({'giris_yapildi': False, 'kullanici_adi': "", 'rol': "", 'sorumlu_il': ""})

if not st.session_state.giris_yapildi:
    # Giriş ekranı (Kayıt olunca mail_at fonksiyonunu tetikler)
    c1, c2, c3 = st.columns([1, 2, 1])
    with c2:
        st.title("🇹🇷 TSE PORTAL")
        tg, tk = st.tabs(["🔐 Giriş", "📝 Kayıt"])
        with tk:
            with st.form("reg"):
                yk, ys, ye, yil = st.text_input("Kullanıcı"), st.text_input("Şifre"), st.text_input("E-Posta"), st.selectbox("İl", ["Ankara", "İstanbul", "Kocaeli", "İzmir", "Bursa"])
                if st.form_submit_button("Kayıt Ol"):
                    conn = sqlite3.connect('tse_v4.db')
                    try:
                        conn.execute("INSERT INTO kullanicilar (kullanici_adi, sifre, rol, email, sorumlu_il, onay_durumu) VALUES (?, ?, 'kullanici', ?, ?, 0)", (yk, ys, ye, yil))
                        conn.commit()
                        # ADMİNE ANLIK MAİL
                        mail_gonder(ADMIN_MAIL, "YENİ ÜYE TALEBİ", f"Yeni kullanıcı: {yk}<br>İl: {yil}<br>Email: {ye}")
                        st.success("Talebiniz iletildi, mail gönderildi.")
                    except: st.error("Hata!")
                    finally: conn.close()
    st.stop()

# --- ANA DASHBOARD ---
# (Önceki sürümlerdeki Tablo ve Numune Kayıt bölümleri aynen korunur...)

# VERİ GİRİŞİ SEKİMESİ (Excel Yükleme ve Mail Tetikleme)
# tabs[2] Veri Girişi bölümünde:
# if st.button("Excel Yükle"):
#    ... veritabanı yazma ...
#    threading.Thread(target=excel_yukle_ve_mail_at, args=(df_yuklenen,)).start()
