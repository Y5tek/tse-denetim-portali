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
    st.error("Secrets ayarları eksik! Streamlit Cloud panelinden Secrets kısmını kontrol edin.")
    st.stop()

SMTP_SUNUCU = "smtp.gmail.com"
SMTP_PORT = 465 

# --- 1. VERİTABANI MOTORU VE OTOMATİK GÜNCELLEME ---
def veritabanini_hazirla():
    conn = sqlite3.connect('tse_v4.db')
    cursor = conn.cursor()
    # Tabloyu en geniş haliyle oluştur/güncelle
    cursor.execute('''CREATE TABLE IF NOT EXISTS denetimler (
        id INTEGER PRIMARY KEY AUTOINCREMENT, 
        basvuru_no TEXT, 
        firma_adi TEXT, 
        marka TEXT,
        arac_kategori TEXT, 
        arac_tipi TEXT, 
        varyant TEXT, 
        versiyon TEXT, 
        ticari_ad TEXT,
        gtip_no TEXT, 
        birim TEXT, 
        uretim_ulkesi TEXT, 
        arac_sayisi TEXT, 
        sasi_no TEXT UNIQUE, 
        basvuru_tarihi TEXT, 
        secim_tarihi TEXT, 
        il TEXT, 
        durum TEXT DEFAULT 'Şasi Bekliyor',
        notlar TEXT, 
        guncelleme_tarihi TEXT, 
        ekleyen_kullanici TEXT, 
        silme_talebi INTEGER DEFAULT 0, 
        silme_nedeni TEXT)''')
    
    # Eksik olabilecek sütunları kontrol et ve ekle (Hata almamak için)
    sutunlar = [row[1] for row in cursor.execute("PRAGMA table_info(denetimler)")]
    gerekli_sutunlar = ["basvuru_no", "basvuru_tarihi", "secim_tarihi", "il", "silme_talebi"]
    for s in gerekli_sutunlar:
        if s not in sutunlar:
            cursor.execute(f"ALTER TABLE denetimler ADD COLUMN {s} TEXT")
            
    cursor.execute('''CREATE TABLE IF NOT EXISTS kullanicilar (
        id INTEGER PRIMARY KEY AUTOINCREMENT, 
        kullanici_adi TEXT UNIQUE, 
        sifre TEXT,
        rol TEXT, 
        email TEXT, 
        sorumlu_il TEXT, 
        onay_durumu INTEGER DEFAULT 0, 
        excel_yukleme_yetkisi INTEGER DEFAULT 0)''')
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

# --- 2. VERİ ÇEKME FONKSİYONLARI ---
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
        df['secim_tarihi_dt'] = pd.to_datetime(df['secim_tarihi'], errors='coerce')
        bugun = pd.to_datetime(datetime.now().strftime("%Y-%m-%d"))
        # Loglarda hata veren PyArrow sorununu kökten çözmek için her şeyi metne çeviriyoruz
        df['Geçen Gün'] = (bugun - df['secim_tarihi_dt']).dt.days
        df_display = df.copy()
        df_display['secim_tarihi'] = df_display['secim_tarihi_dt'].dt.strftime('%Y-%m-%d')
        for col in df_display.columns:
            df_display[col] = df_display[col].astype(str).replace(['nan', 'None', '<NA>'], '-')
        return df_display
    return df

def satir_boya(row): 
    if row['durum'] == 'Şasi Bekliyor': return ['background-color: rgba(255, 193, 7, 0.2)'] * len(row)
    elif row['durum'] == 'Tamamlandı - Olumlu': return ['background-color: rgba(40, 167, 69, 0.2)'] * len(row)
    elif row['durum'] == 'Tamamlandı - Olumsuz': return ['background-color: rgba(220, 53, 69, 0.2)'] * len(row)
    return [''] * len(row)

# --- 3. OTURUM YÖNETİMİ ---
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
                if st.form_submit_button("Giriş Yap", use_container_width=True):
                    conn = sqlite3.connect('tse_v4.db'); u = conn.cursor().execute("SELECT rol, sorumlu_il, onay_durumu FROM kullanicilar WHERE kullanici_adi=? AND sifre=?", (ka, si)).fetchone(); conn.close()
                    if u and u[2]==1: st.session_state.update({'giris_yapildi':True, 'kullanici_adi':ka, 'rol':u[0], 'sorumlu_il':u[1]}); st.rerun()
                    else: st.error("Hesap onaylanmamış veya hatalı giriş.")
        with tk:
            with st.form("r"):
                yk, ys, ye, yil = st.text_input("Kullanıcı Adı"), st.text_input("Şifre", type="password"), st.text_input("E-Posta"), st.selectbox("İl", ["Ankara", "İstanbul", "Bursa", "Kocaeli"])
                if st.form_submit_button("Kayıt Ol"):
                    conn = sqlite3.connect('tse_v4.db')
                    conn.execute("INSERT INTO kullanicilar (kullanici_adi, sifre, rol, email, sorumlu_il, onay_durumu) VALUES (?, ?, 'uzman', ?, ?, 0)", (yk, ys, ye, yil))
                    conn.commit(); conn.close()
                    threading.Thread(target=mail_gonder, args=("📝 YENİ KAYIT TALEBİ", f"Üye: {yk}")).start()
                    st.success("Kayıt başarılı, mail gönderildi."); st.rerun()
    st.stop()

# --- 4. ANA DASHBOARD ---
b_onay, b_silme = durum_sayilarini_al()
df = verileri_getir()

with st.sidebar:
    st.header("TSE PANEL")
    st.write(f"👤 **{st.session_state.kullanici_adi}**")
    if st.session_state.rol == "admin" and (b_onay + b_silme) > 0:
        st.error(f"🚨 {b_onay + b_silme} Bekleyen İşlem!")
    if st.button("🚪 Çıkış"): st.session_state.clear(); st.rerun()

t_labels = ["📊 Ana Tablo", "🛠️ Numune İşlemleri", "📥 Veri Girişi"]
if st.session_state.rol == "admin": t_labels.append(f"👑 Yönetici ({b_onay+b_silme})")
tabs = st.tabs(t_labels)

# --- SEKME 1: ANA TABLO ---
with tabs[0]:
    sutun_sirasi = ['sasi_no', 'durum', 'secim_tarihi', 'Geçen Gün', 'marka', 'arac_tipi', 'firma_adi', 'arac_kategori', 'il']
    final_df = df[[c for c in sutun_sirasi if c in df.columns]]
    st.dataframe(final_df.style.apply(satir_boya, axis=1), use_container_width=True, height=600)

# --- SEKME 2: İŞLEMLER ---
with tabs[1]:
    st.subheader("🛠️ Şasi Atama ve Güncelleme")
    i_df = df if st.session_state.rol == "admin" else df[df['il'] == st.session_state.sorumlu_il]
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("#### 🆕 Şasi Atama")
        bekleyen = i_df[i_df['durum'] == 'Şasi Bekliyor']
        if not bekleyen.empty:
            sel = st.selectbox("Başvuru:", options=(bekleyen['id'].astype(str) + " | " + bekleyen['basvuru_no']).tolist(), index=None)
            if sel:
                sid = int(sel.split(" |")[0]); vin = st.text_input("VIN No")
                if st.button("Kaydet"):
                    conn = sqlite3.connect('tse_v4.db')
                    conn.execute('UPDATE denetimler SET sasi_no=?, durum="Teste Gönderildi", secim_tarihi=?, guncelleme_tarihi=? WHERE id=?', (vin, datetime.now().strftime("%Y-%m-%d"), datetime.now().strftime("%Y-%m-%d %H:%M:%S"), sid))
                    conn.commit(); conn.close(); st.rerun()
    with col2:
        st.markdown("#### 🔍 Güncelleme")
        islenmis = i_df[i_df['durum'] != 'Şasi Bekliyor']
        if not islenmis.empty:
            srch = st.selectbox("Şasi Seç:", options=(islenmis['id'].astype(str) + " | " + islenmis['sasi_no']).tolist(), index=None)
            if srch:
                sid_num = int(srch.split(" |")[0])
                match = islenmis[islenmis['id'].astype(str) == str(sid_num)]
                if not match.empty:
                    cur = match.iloc[0]
                    with st.form("up"):
                        nd = st.selectbox("Durum", ["Teste Gönderildi", "Tamamlandı - Olumlu", "Tamamlandı - Olumsuz"])
                        sl = st.checkbox("Silme Talebi")
                        if st.form_submit_button("Güncelle"):
                            conn = sqlite3.connect('tse_v4.db')
                            conn.execute('UPDATE denetimler SET durum=?, silme_talebi=?, guncelleme_tarihi=? WHERE id=?', (nd, 1 if sl else 0, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), sid_num))
                            conn.commit(); conn.close()
                            if sl: threading.Thread(target=mail_gonder, args=("⚠️ SİLME TALEBİ", f"Şasi: {cur['sasi_no']}")).start()
                            st.rerun()

# --- SEKME 3: VERİ GİRİŞİ (HATALARI GİDERİLDİ) ---
with tabs[2]:
    st.subheader("📥 Veri Girişi")
    with st.form("manuel_input"):
        c1, c2 = st.columns(2)
        with c1:
            f_bn = st.text_input("Başvuru No")
            f_fa = st.text_input("Firma Adı")
            f_ma = st.text_input("Marka")
        with c2:
            f_ti = st.text_input("Araç Tipi")
            f_ak = st.text_input("Araç Kategori")
            f_sn = st.text_input("Şasi No (Atanmışsa)")
        if st.form_submit_button("Sisteme Kaydet"):
            if f_fa and f_ti:
                conn = sqlite3.connect('tse_v4.db')
                starih = datetime.now().strftime("%Y-%m-%d")
                durum = "Teste Gönderildi" if f_sn else "Şasi Bekliyor"
                # Sütunları güvenli şekilde tek tek yazıyoruz
                conn.execute("""INSERT INTO denetimler 
                    (basvuru_no, firma_adi, marka, arac_tipi, arac_kategori, sasi_no, durum, basvuru_tarihi, secim_tarihi, il) 
                    VALUES (?,?,?,?,?,?,?,?,?,?)""", 
                    (f_bn, f_fa, f_ma, f_ti, f_ak, f_sn, durum, starih, starih, st.session_state.sorumlu_il))
                conn.commit(); conn.close(); st.success("Başarıyla eklendi."); st.rerun()
            else: st.warning("Firma Adı ve Araç Tipi boş bırakılamaz.")

# --- SEKME 4: YÖNETİCİ ---
if st.session_state.rol == "admin":
    with tabs[3]:
        st.subheader("👑 Yönetici İşlemleri")
        co, cs = st.columns(2)
        with co:
            st.write(f"Onay Bekleyen Üyeler ({b_onay})")
            conn = sqlite3.connect('tse_v4.db'); k_df = pd.read_sql_query("SELECT * FROM kullanicilar WHERE onay_durumu=0", conn); conn.close()
            for _, r in k_df.iterrows():
                if st.button(f"Onayla: {r['kullanici_adi']}", key=f"o_{r['id']}"):
                    c = sqlite3.connect('tse_v4.db'); c.execute("UPDATE kullanicilar SET onay_durumu=1 WHERE id=?", (r['id'],)); c.commit(); c.close(); st.rerun()
        with cs:
            st.write(f"Silme Talepleri ({b_silme})")
            for _, r in df[df['silme_talebi']=="1"].iterrows():
                if st.button(f"Kalıcı SİL: {r['sasi_no']}", key=f"s_{r['id']}"):
                    c = sqlite3.connect('tse_v4.db'); c.execute("DELETE FROM denetimler WHERE id=?", (r['id'],)); c.commit(); c.close(); st.rerun()
