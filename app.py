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
import os

# --- 1. MERKEZİ VE GÜVENLİ VERİTABANI MOTORU ---
DB_PATH = 'tse_v4.db'

def execute_query(query, params=(), fetch=False, is_pandas=False):
    """
    Veritabanı bağlantısını güvenli bir şekilde (with bloğu ile) yönetir.
    Hata anında veya işlem sonunda bağlantıyı otomatik kapatarak veri kaybını önler.
    """
    try:
        with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
            if is_pandas:
                return pd.read_sql_query(query, conn, params=params)
            
            cursor = conn.cursor()
            cursor.execute(query, params)
            if fetch:
                return cursor.fetchall()
            conn.commit()
            return True
    except sqlite3.Error as e:
        st.error(f"Veritabanı Hatası: {e}")
        return None

def veritabanini_hazirla():
    """Tablo yapılarını oluşturur."""
    execute_query('''CREATE TABLE IF NOT EXISTS denetimler (
        id INTEGER PRIMARY KEY AUTOINCREMENT, basvuru_no TEXT, firma_adi TEXT NOT NULL, marka TEXT,
        arac_kategori TEXT, arac_tipi TEXT NOT NULL, varyant TEXT, versiyon TEXT, ticari_ad TEXT,
        gtip_no TEXT, birim TEXT, uretim_ulkesi TEXT, arac_sayisi TEXT, sasi_no TEXT UNIQUE, 
        basvuru_tarihi DATE, secim_tarihi DATE, il TEXT, durum TEXT DEFAULT 'Şasi Bekliyor',
        notlar TEXT, guncelleme_tarihi TEXT, ekleyen_kullanici TEXT, silme_talebi INTEGER DEFAULT 0, silme_nedeni TEXT)''')
    
    execute_query('''CREATE TABLE IF NOT EXISTS kullanicilar (
        id INTEGER PRIMARY KEY AUTOINCREMENT, kullanici_adi TEXT UNIQUE NOT NULL, sifre TEXT NOT NULL,
        rol TEXT NOT NULL, email TEXT, sorumlu_il TEXT, onay_durumu INTEGER DEFAULT 1, excel_yukleme_yetkisi INTEGER DEFAULT 0)''')

# Uygulama başında veritabanını hazırla
veritabanini_hazirla()

# --- KULLANIM KILAVUZU METNİ ---
KILAVUZ_METNI = """# 🇹🇷 TSE NUMUNE TAKİP PORTALI - KULLANIM KILAVUZU
Kurum içi süreçlerin dijitalleştirilmesi ve otomatik bildirimler için tasarlanmıştır.
"""

# --- SAYFA YAPILANDIRMASI ---
st.set_page_config(page_title="TSE NUMUNE TAKİP PORTALI", layout="wide")

# --- MAİL AYARLARI ---
try:
    GONDERICI_MAIL = st.secrets["GONDERICI_MAIL"]
    GONDERICI_SIFRE = st.secrets["GONDERICI_SIFRE"].replace(" ", "") 
    ADMIN_MAIL = st.secrets["ADMIN_MAIL"]
except Exception:
    st.error("Kritik Hata: Streamlit Secrets bulunamadı!")
    st.stop()

SMTP_SUNUCU = "smtp.gmail.com"
SMTP_PORT = 465 

# --- BİLDİRİM MOTORU ---
def mail_at(kime, konu, icerik):
    msg = MIMEMultipart()
    msg['From'], msg['To'], msg['Subject'] = GONDERICI_MAIL, kime, konu
    msg.attach(MIMEText(f"<html><body><h3>TSE Bildirim</h3><p>{icerik}</p></body></html>", 'html'))
    try:
        server = smtplib.SMTP_SSL(SMTP_SUNUCU, SMTP_PORT)
        server.login(GONDERICI_MAIL, GONDERICI_SIFRE)
        server.send_message(msg); server.quit()
    except: pass

# --- VERİ FONKSİYONLARI ---
def verileri_getir():
    df = execute_query("SELECT * FROM denetimler ORDER BY id DESC", is_pandas=True)
    if df is not None and not df.empty:
        df['secim_tarihi_dt'] = pd.to_datetime(df['secim_tarihi'], errors='coerce')
        bugun = pd.to_datetime(datetime.now().strftime("%Y-%m-%d"))
        df['Geçen Gün'] = (bugun - df['secim_tarihi_dt']).dt.days.apply(lambda x: str(int(x)) if pd.notnull(x) else '-')
        df['secim_tarihi'] = df['secim_tarihi_dt'].dt.strftime('%Y-%m-%d').fillna('-')
        for c in df.columns: 
            if c not in ['Geçen Gün', 'secim_tarihi_dt']: df[c] = df[c].fillna('-')
    return df if df is not None else pd.DataFrame()

def durum_sayilarini_al():
    onay = execute_query("SELECT COUNT(*) FROM kullanicilar WHERE onay_durumu = 0", fetch=True)
    silme = execute_query("SELECT COUNT(*) FROM denetimler WHERE silme_talebi = 1", fetch=True)
    return (onay[0][0] if onay else 0), (silme[0][0] if silme else 0)

def satir_boya(row): 
    if row['durum'] == 'Şasi Bekliyor': return ['background-color: rgba(255, 193, 7, 0.2)'] * len(row)
    elif row['durum'] == 'Tamamlandı - Olumlu': return ['background-color: rgba(40, 167, 69, 0.2)'] * len(row)
    elif row['durum'] == 'Tamamlandı - Olumsuz': return ['background-color: rgba(220, 53, 69, 0.2)'] * len(row)
    return [''] * len(row)

# --- OTURUM YÖNETİMİ ---
if 'giris_yapildi' not in st.session_state:
    st.session_state.update({'giris_yapildi': False, 'kullanici_adi': "", 'rol': "", 'sorumlu_il': "", 'onay_bekleyen_excel_df': None})

# --- GİRİŞ EKRANI ---
if not st.session_state.giris_yapildi:
    c1, c2, c3 = st.columns([1, 2, 1])
    with c2:
        if os.path.exists("tse_logo.png"):
            st.image("tse_logo.png", width=200)
        st.markdown("<h1 style='text-align: center; color: #E03131;'>TSE NUMUNE TAKİP PORTALI</h1>", unsafe_allow_html=True)
        tg, tk = st.tabs(["🔐 Giriş Yap", "📝 Kayıt Ol"])
        with tg:
            with st.form("login_form"):
                ka, si = st.text_input("Kullanıcı Adı"), st.text_input("Şifre", type="password")
                if st.form_submit_button("Giriş Yap", use_container_width=True):
                    u = execute_query("SELECT rol, sorumlu_il, onay_durumu, excel_yukleme_yetkisi FROM kullanicilar WHERE kullanici_adi=? AND sifre=?", (ka, si), fetch=True)
                    if u:
                        if u[0][2] == 0: st.warning("Oturum onayı bekleniyor.")
                        else:
                            st.session_state.update({'giris_yapildi':True, 'kullanici_adi':ka, 'rol':u[0][0], 'sorumlu_il':u[0][1], 'excel_yetkisi':u[0][3]})
                            st.rerun()
                    else: st.error("❌ Hatalı bilgiler.")
        with tk:
            with st.form("register_form"):
                yk, ys, ye, yil = st.text_input("Kullanıcı Adı"), st.text_input("Şifre"), st.text_input("E-Posta"), st.selectbox("İl", ["Ankara", "İstanbul", "İzmir", "Bursa", "Kocaeli", "Diğer"])
                if st.form_submit_button("Kayıt Talebi Gönder"):
                    if execute_query("INSERT INTO kullanicilar (kullanici_adi, sifre, rol, email, sorumlu_il, onay_durumu, excel_yukleme_yetkisi) VALUES (?, ?, 'kullanici', ?, ?, 0, 0)", (yk, ys, ye, yil)):
                        threading.Thread(target=mail_at, args=(ADMIN_MAIL, "📝 YENİ KAYIT", f"Yeni üye: {yk}")).start()
                        st.success("Talebiniz iletildi."); time.sleep(1); st.rerun()
                    else: st.error("Kullanıcı adı mevcut veya hata oluştu.")
    st.stop()

# --- ANA PANEL ---
df = verileri_getir()
b_onay, b_silme = durum_sayilarini_al()

with st.sidebar:
    if os.path.exists("tse_logo.png"): st.image("tse_logo.png", use_container_width=True)
    st.write(f"👤 **{st.session_state.kullanici_adi}** | 📍 **{st.session_state.sorumlu_il}**")
    if st.button("🚪 Oturumu Kapat", use_container_width=True):
        st.session_state.clear(); st.rerun()

tabs = st.tabs(["📊 Ana Tablo", "🛠️ İşlem Paneli", "📥 Veri Girişi", "👑 Yönetici Paneli"] if st.session_state.rol == "admin" else ["📊 Ana Tablo", "🛠️ İşlem Paneli", "📥 Veri Girişi"])

with tabs[0]:
    st.subheader("Sistem Kayıtları")
    c_m1, c_m2, c_m3 = st.columns(3)
    c_m1.metric("Toplam Kayıt", len(df))
    c_m2.metric("Şasi Bekleyen", len(df[df['durum'] == 'Şasi Bekliyor']) if not df.empty else 0)
    
    src = st.text_input("🔍 Ara (Şasi, Marka, Firma...):")
    display_df = df.copy()
    if src:
        display_df = display_df[display_df.apply(lambda r: src.lower() in r.astype(str).str.lower().values, axis=1)]
    st.dataframe(display_df.style.apply(satir_boya, axis=1), use_container_width=True, height=600)

with tabs[2]:
    st.subheader("📥 Veri Girişi")
    c_form, c_excel = st.columns(2)
    with c_form:
        with st.form("manuel_form"):
            st.write("Tekil Kayıt Girişi")
            bn, fa, ma, ti, sn = st.text_input("Başvuru No"), st.text_input("Firma"), st.text_input("Marka"), st.text_input("Tip"), st.text_input("Şasi")
            if st.form_submit_button("Kaydet"):
                if execute_query("INSERT INTO denetimler (firma_adi, marka, arac_tipi, sasi_no, basvuru_no, durum, basvuru_tarihi, secim_tarihi, il) VALUES (?,?,?,?,?, 'Teste Gönderildi', ?, ?, ?)", 
                                 (fa, ma, ti, sn, bn, datetime.now().strftime("%Y-%m-%d"), datetime.now().strftime("%Y-%m-%d"), st.session_state.sorumlu_il)):
                    st.success("Kayıt eklendi."); st.rerun()
    
    with c_excel:
        up = st.file_uploader("Excel Yükle", type=['xlsx'])
        if up and st.button("Sisteme Aktar"):
            try:
                yüklenen_df = pd.read_excel(up)
                # Sütun eşleme ve temizlik işlemleri burada yapılır...
                with sqlite3.connect(DB_PATH) as conn:
                    yüklenen_df.to_sql('denetimler', conn, if_exists='append', index=False)
                st.success("Excel başarıyla aktarıldı."); time.sleep(1); st.rerun()
            except Exception as e:
                st.error(f"Hata: {e}")

if st.session_state.rol == "admin":
    with tabs[3]:
        st.subheader("👑 Yönetici Paneli")
        if st.button("Kullanıcı Onaylarını Görüntüle"):
            onay_df = execute_query("SELECT * FROM kullanicilar WHERE onay_durumu=0", fetch=True)
            st.write(onay_df)
