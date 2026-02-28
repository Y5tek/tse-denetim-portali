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
st.set_page_config(page_title="TSE Araç İthalat Denetim Portalı", layout="wide", page_icon="🚗")

# Secrets'tan mail bilgilerini çekiyoruz
try:
    GONDERICI_MAIL = st.secrets["GONDERICI_MAIL"]
    GONDERICI_SIFRE = st.secrets["GONDERICI_SIFRE"]
    ADMIN_MAIL = st.secrets["ADMIN_MAIL"]
except Exception:
    st.error("Kritik Hata: Streamlit Secrets (Mail ayarları) bulunamadı!")
    st.stop()

SMTP_SUNUCU = "smtp.gmail.com"
SMTP_PORT = 465 # SSL Portu (Cloud ortamı için en güvenlisi)

# --- 1. VERİTABANI VE YARDIMCI FONKSİYONLAR ---
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
def mail_gonder(alici, konu, icerik_html):
    msg = MIMEMultipart()
    msg['From'] = GONDERICI_MAIL
    msg['To'] = alici
    msg['Subject'] = konu
    msg.attach(MIMEText(icerik_html, 'html'))
    try:
        server = smtplib.SMTP_SSL(SMTP_SUNUCU, SMTP_PORT)
        server.login(GONDERICI_MAIL, GONDERICI_SIFRE)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print(f"MAİL HATASI: {str(e)}")
        return False

# --- 2. VERİ ÇEKME (LOGLARDAKİ PYARROW HATASI DÜZELTİLDİ) ---
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
    
    # Tarih ve Gün Hesaplama
    df['secim_tarihi_dt'] = pd.to_datetime(df['secim_tarihi'], errors='coerce')
    bugun = pd.to_datetime(datetime.now().strftime("%Y-%m-%d"))
    
    # Loglardaki Arrow hatasını engellemek için metne çeviriyoruz 
    df['Geçen Gün'] = (bugun - df['secim_tarihi_dt']).dt.days
    df['secim_tarihi'] = df['secim_tarihi_dt'].dt.strftime('%Y-%m-%d')
    
    # Tüm tabloyu string (metin) yaparak uyumsuzlukları bitiriyoruz [cite: 11]
    df_display = df.copy()
    for col in df_display.columns:
        df_display[col] = df_display[col].astype(str).replace('nan', '-').replace('None', '-')
    
    return df_display

def satir_boya(row): 
    if row['durum'] == 'Şasi Bekliyor': return ['background-color: rgba(255, 193, 7, 0.2)'] * len(row)
    elif row['durum'] == 'Tamamlandı - Olumlu': return ['background-color: rgba(40, 167, 69, 0.2)'] * len(row)
    elif row['durum'] == 'Tamamlandı - Olumsuz': return ['background-color: rgba(220, 53, 69, 0.2)'] * len(row)
    return [''] * len(row)

# --- 3. OTURUM YÖNETİMİ ---
if 'giris_yapildi' not in st.session_state:
    st.session_state.update({'giris_yapildi': False, 'kullanici_adi': "", 'rol': "", 'sorumlu_il': ""})

def durum_guncelle_by_id(kayit_id, sasi_no, yeni_durum, notlar, starih="MEVCUT", talep_et_silme=False, silme_nedeni=""):
    conn = sqlite3.connect('tse_v4.db'); g_ani = datetime.now().strftime("%Y-%m-%d %H:%M:%S"); sil_v = 1 if talep_et_silme else 0
    if starih == "MEVCUT": conn.cursor().execute('UPDATE denetimler SET sasi_no=?, durum=?, notlar=?, guncelleme_tarihi=?, silme_talebi=?, silme_nedeni=? WHERE id=?', (sasi_no, yeni_durum, notlar, g_ani, sil_v, silme_nedeni, kayit_id))
    else: conn.cursor().execute('UPDATE denetimler SET sasi_no=?, durum=?, secim_tarihi=?, notlar=?, guncelleme_tarihi=?, silme_talebi=?, silme_nedeni=? WHERE id=?', (sasi_no, yeni_durum, starih, notlar, g_ani, sil_v, silme_nedeni, kayit_id))
    conn.commit(); conn.close()
    if talep_et_silme:
        threading.Thread(target=mail_gonder, args=(ADMIN_MAIL, "⚠️ SİLME TALEBİ", f"{sasi_no} için silme talebi var.")).start()

# --- 4. GİRİŞ EKRANI ---
if not st.session_state.giris_yapildi:
    c1, c2, c3 = st.columns([1, 2, 1])
    with c2:
        st.markdown("<h1 style='text-align: center; color: #E03131;'>🇹🇷 TSE PORTAL</h1>", unsafe_allow_html=True)
        tg, tk = st.tabs(["🔐 Giriş", "📝 Kayıt"])
        with tg:
            with st.form("login_form"):
                ka, si = st.text_input("Kullanıcı"), st.text_input("Şifre", type="password")
                if st.form_submit_button("Giriş", width="stretch"):
                    conn = sqlite3.connect('tse_v4.db'); u = conn.cursor().execute("SELECT rol, sorumlu_il, onay_durumu FROM kullanicilar WHERE kullanici_adi=? AND sifre=?", (ka, si)).fetchone(); conn.close()
                    if u:
                        if u[2]==0: st.warning("Onay bekleniyor.")
                        else: st.session_state.update({'giris_yapildi':True, 'kullanici_adi':ka, 'rol':u[0], 'sorumlu_il':u[1]}); st.rerun()
                    else: st.error("Hatalı giriş!")
        with tk:
            with st.form("reg_form"):
                yk, ys, ye, yil = st.text_input("Kullanıcı Adı"), st.text_input("Şifre", type="password"), st.text_input("E-Posta"), st.selectbox("İl", ["Ankara", "İstanbul", "Kocaeli", "Diğer"])
                if st.form_submit_button("Kayıt Ol"):
                    conn = sqlite3.connect('tse_v4.db')
                    try:
                        conn.execute("INSERT INTO kullanicilar (kullanici_adi, sifre, rol, email, sorumlu_il, onay_durumu) VALUES (?, ?, 'kullanici', ?, ?, 0)", (yk, ys, ye, yil))
                        conn.commit()
                        threading.Thread(target=mail_gonder, args=(ADMIN_MAIL, "📝 YENİ KAYIT TALEBİ", f"Yeni üye: {yk}")).start()
                        st.success("Talebiniz iletildi.")
                    except: st.error("Hata!")
                    finally: conn.close()
    st.stop()

# --- 5. ANA DASHBOARD ---
b_onay, b_silme = durum_sayilarini_al()
toplam_bekleyen = b_onay + b_silme
df = verileri_getir()

with st.sidebar:
    st.markdown("<h3 style='color: #E03131;'>TSE PANEL</h3>", unsafe_allow_html=True)
    st.write(f"👤 **{st.session_state.kullanici_adi}**")
    if st.session_state.rol == "admin" and toplam_bekleyen > 0:
        st.error(f"🔔 Bekleyen: {toplam_bekleyen}")
    if st.button("🚪 Çıkış"): st.session_state.clear(); st.rerun()

admin_label = f"👑 Yönetici Paneli ({toplam_bekleyen})" if (st.session_state.rol == "admin" and toplam_bekleyen > 0) else "👑 Yönetici Paneli"
tabs = st.tabs(["📊 Ana Tablo", "🛠️ Numune Kayıt Girişi", "📥 Veri Girişi", admin_label]) if st.session_state.rol == "admin" else st.tabs(["📊 Ana Tablo", "🛠️ Numune Kayıt Girişi", "📥 Veri Girişi"])

with tabs[0]:
    istenen = ['sasi_no', 'durum', 'secim_tarihi', 'Geçen Gün', 'marka', 'arac_tipi', 'firma_adi', 'arac_kategori', 'il']
    final_df = df[[c for c in istenen if c in df.columns]]
    st.dataframe(final_df.style.apply(satir_boya, axis=1), width="stretch", height=500)

with tabs[1]:
    st.subheader("Şasi Atama ve Güncelleme")
    islenmis = df[df['durum'] != 'Şasi Bekliyor']
    search = st.selectbox("Kayıt Seç:", options=(islenmis['id'].astype(str) + " | " + islenmis['sasi_no']).tolist(), index=None)
    if search:
        sid = int(search.split(" |")[0]); cur = islenmis[islenmis['id'] == sid].iloc[0]
        with st.form("upd_form"):
            nd = st.selectbox("Durum", ["Teste Gönderildi", "Tamamlandı - Olumlu", "Tamamlandı - Olumsuz"])
            sl = st.checkbox("Silme Talebi")
            sn = st.text_input("Neden") if sl else ""
            if st.form_submit_button("Güncelle"):
                durum_guncelle_by_id(sid, cur['sasi_no'], nd, "", talep_et_silme=sl, silme_nedeni=sn)
                st.rerun()

if st.session_state.rol == "admin":
    with tabs[3]:
        st.subheader("Yönetim")
        c1, c2 = st.columns(2)
        with c1:
            st.write(f"Üye Onayları ({b_onay})")
            conn = sqlite3.connect('tse_v4.db'); k_df = pd.read_sql_query("SELECT * FROM kullanicilar WHERE onay_durumu=0", conn); conn.close()
            for _, r in k_df.iterrows():
                if st.button(f"Onayla: {r['kullanici_adi']}", key=f"o_{r['id']}"):
                    c = sqlite3.connect('tse_v4.db'); c.execute("UPDATE kullanicilar SET onay_durumu=1 WHERE id=?", (r['id'],)); c.commit(); c.close(); st.rerun()
        with c2:
            st.write(f"Silme Talepleri ({b_silme})")
            s_df = df[df['silme_talebi'] == "1"]
            for _, r in s_df.iterrows():
                if st.button(f"Kalıcı Sil: {r['sasi_no']}", key=f"s_{r['id']}"):
                    c = sqlite3.connect('tse_v4.db'); c.execute("DELETE FROM denetimler WHERE id=?", (r['id'],)); c.commit(); c.close(); st.rerun()
