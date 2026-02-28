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

# Streamlit Secrets'tan bilgileri çekiyoruz
try:
    GONDERICI_MAIL = st.secrets["GONDERICI_MAIL"]
    GONDERICI_SIFRE = st.secrets["GONDERICI_SIFRE"].replace(" ", "") # Boşlukları temizler
    ADMIN_MAIL = st.secrets["ADMIN_MAIL"]
except Exception:
    st.error("Kritik Hata: Streamlit Secrets (Mail ayarları) bulunamadı! Lütfen Cloud panelinden ayarları yapın.")
    st.stop()

SMTP_SUNUCU = "smtp.gmail.com"
SMTP_PORT = 465 # SSL Portu Cloud için en kararlısıdır

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
    onay_sayisi = conn.execute("SELECT COUNT(*) FROM kullanicilar WHERE onay_durumu = 0").fetchone()[0]
    silme_sayisi = conn.execute("SELECT COUNT(*) FROM denetimler WHERE silme_talebi = 1").fetchone()[0]
    conn.close()
    return onay_sayisi, silme_sayisi

def verileri_getir():
    conn = sqlite3.connect('tse_v4.db')
    df = pd.read_sql_query("SELECT * FROM denetimler ORDER BY id DESC", conn)
    conn.close()
    
    # Tarih İşlemleri
    df['secim_tarihi_dt'] = pd.to_datetime(df['secim_tarihi'], errors='coerce')
    bugun = pd.to_datetime(datetime.now().strftime("%Y-%m-%d"))
    
    # Geçen Gün Hesaplama
    df['Geçen Gün'] = (bugun - df['secim_tarihi_dt']).dt.days
    
    # KRİTİK: Loglardaki Arrow hatasını engellemek için tüm tabloyu metne çeviriyoruz
    df_display = df.copy()
    df_display['secim_tarihi'] = df_display['secim_tarihi_dt'].dt.strftime('%Y-%m-%d')
    for col in df_display.columns:
        df_display[col] = df_display[col].astype(str).replace(['nan', 'None', '<NA>'], '-')
    
    return df_display

def satir_boya(row): 
    if row['durum'] == 'Şasi Bekliyor': return ['background-color: rgba(255, 193, 7, 0.2)'] * len(row)
    elif row['durum'] == 'Tamamlandı - Olumlu': return ['background-color: rgba(40, 167, 69, 0.2)'] * len(row)
    elif row['durum'] == 'Tamamlandı - Olumsuz': return ['background-color: rgba(220, 53, 69, 0.2)'] * len(row)
    return [''] * len(row)

# --- 3. OTURUM YÖNETİMİ ---
if 'giris_yapildi' not in st.session_state:
    st.session_state.update({'giris_yapildi': False, 'kullanici_adi': "", 'rol': "", 'sorumlu_il': "", 'excel_yetkisi': 0})

def durum_guncelle_by_id(kayit_id, sasi_no, yeni_durum, notlar, starih="MEVCUT", talep_et_silme=False, silme_nedeni=""):
    conn = sqlite3.connect('tse_v4.db'); g_ani = datetime.now().strftime("%Y-%m-%d %H:%M:%S"); sil_v = 1 if talep_et_silme else 0
    if starih == "MEVCUT": conn.cursor().execute('UPDATE denetimler SET sasi_no=?, durum=?, notlar=?, guncelleme_tarihi=?, silme_talebi=?, silme_nedeni=? WHERE id=?', (sasi_no, yeni_durum, notlar, g_ani, sil_v, silme_nedeni, kayit_id))
    else: conn.cursor().execute('UPDATE denetimler SET sasi_no=?, durum=?, secim_tarihi=?, notlar=?, guncelleme_tarihi=?, silme_talebi=?, silme_nedeni=? WHERE id=?', (sasi_no, yeni_durum, starih, notlar, g_ani, sil_v, silme_nedeni, kayit_id))
    conn.commit(); conn.close()
    if talep_et_silme: 
        threading.Thread(target=mail_gonder, args=("⚠️ YENİ SİLME TALEBİ", f"Şasi: {sasi_no}\nNeden: {silme_nedeni}")).start()

# --- 4. GİRİŞ EKRANI ---
if not st.session_state.giris_yapildi:
    c1, c2, c3 = st.columns([1, 2, 1])
    with c2:
        st.markdown("<h1 style='text-align: center; color: #E03131;'>🇹🇷 TSE DENETİM PORTAL</h1>", unsafe_allow_html=True)
        tg, tk = st.tabs(["🔐 Giriş", "📝 Kayıt"])
        with tg:
            with st.form("l"):
                ka, si = st.text_input("Kullanıcı Adı"), st.text_input("Şifre", type="password")
                if st.form_submit_button("Giriş Yap", width="stretch"):
                    conn = sqlite3.connect('tse_v4.db'); u = conn.cursor().execute("SELECT rol, sorumlu_il, onay_durumu, excel_yukleme_yetkisi FROM kullanicilar WHERE kullanici_adi=? AND sifre=?", (ka, si)).fetchone(); conn.close()
                    if u:
                        if u[2]==0: st.warning("Oturum onayı bekleniyor.")
                        else: st.session_state.update({'giris_yapildi':True, 'kullanici_adi':ka, 'rol':u[0], 'sorumlu_il':u[1], 'excel_yetkisi':u[3]}); st.rerun()
                    else: st.error("❌ Hatalı bilgiler.")
        with tk:
            with st.form("r"):
                yk, ys, ye, yil = st.text_input("Kullanıcı Adı"), st.text_input("Şifre", type="password"), st.text_input("E-Posta"), st.selectbox("İl", ["Ankara", "İstanbul", "İzmir", "Bursa", "Kocaeli", "Diğer"])
                if st.form_submit_button("Kayıt Talebi Gönder"):
                    try:
                        conn = sqlite3.connect('tse_v4.db'); conn.cursor().execute("INSERT INTO kullanicilar (kullanici_adi, sifre, rol, email, sorumlu_il, onay_durumu, excel_yukleme_yetkisi) VALUES (?, ?, 'kullanici', ?, ?, 0, 0)", (yk, ys, ye, yil)); conn.commit(); conn.close()
                        threading.Thread(target=mail_gonder, args=("📝 YENİ KAYIT TALEBİ", f"Yeni üye talebi: {yk} ({yil})")).start()
                        st.success("Talebiniz iletildi."); time.sleep(1); st.rerun()
                    except: st.error("Kullanıcı adı mevcut.")
    st.stop()

# --- 5. ANA EKRAN ---
b_onay, b_silme = durum_sayilarini_al()
toplam_bekleyen = b_onay + b_silme
df = verileri_getir()

with st.sidebar:
    st.markdown("<h2 style='color: #E03131;'>TSE PANEL</h2>", unsafe_allow_html=True)
    st.write(f"👤 **{st.session_state.kullanici_adi}**")
    if st.session_state.rol == "admin" and toplam_bekleyen > 0:
        st.error(f"🚨 {toplam_bekleyen} Bekleyen İşlem!")
    st.divider()
    if st.button("🚪 Oturumu Kapat", use_container_width=True):
        st.session_state.clear(); st.rerun()

admin_tab_label = f"👑 Yönetici Paneli ({toplam_bekleyen})" if (st.session_state.rol == "admin" and toplam_bekleyen > 0) else "👑 Yönetici Paneli"
main_tabs_list = ["📊 Ana Tablo", "🛠️ Numune Kayıt Girişi", "📥 Veri Girişi"]
if st.session_state.rol == "admin": main_tabs_list.append(admin_tab_label)

tabs = st.tabs(main_tabs_list)

with tabs[0]:
    st.subheader("Sistem Kayıtları")
    c_m1, c_m2, c_m3 = st.columns(3)
    c_m1.metric("Toplam", len(df))
    c_m2.metric("Teste Gönderildi", len(df[df['durum'] == 'Teste Gönderildi']))
    c_m3.metric("Olumlu", len(df[df['durum'] == 'Tamamlandı - Olumlu']))
    
    istenen = ['sasi_no', 'durum', 'secim_tarihi', 'Geçen Gün', 'marka', 'arac_tipi', 'firma_adi', 'il']
    display_df = df[[c for c in istenen if c in df.columns]]
    
    src = st.text_input("🔍 Filtrele (Şasi, Marka, Firma vb.):")
    if src: display_df = display_df[display_df.apply(lambda r: src.lower() in r.astype(str).str.lower().values, axis=1)]
    
    st.dataframe(display_df.style.apply(satir_boya, axis=1), use_container_width=True, height=600)

with tabs[1]:
    st.subheader("İşlem Paneli")
    i_df = df if st.session_state.rol == "admin" else df[df['il'] == st.session_state.sorumlu_il]
    
    c_left, c_right = st.columns(2)
    with c_left:
        st.markdown("#### 🆕 Şasi Atama")
        b_list = i_df[i_df['durum'] == 'Şasi Bekliyor']
        sel = st.selectbox("Başvuru:", options=(b_list['id'].astype(str) + " | " + b_list['basvuru_no']).tolist(), index=None)
        if sel:
            sid = int(sel.split(" |")[0]); vin = st.text_input("VIN Numarası")
            if st.button("Kaydet ve Teste Gönder"):
                durum_guncelle_by_id(sid, vin, 'Teste Gönderildi', "", starih=datetime.now().strftime("%Y-%m-%d"))
                st.rerun()
    with c_right:
        st.markdown("#### 🔍 Güncelleme & İlave")
        i_list = i_df[i_df['durum'] != 'Şasi Bekliyor']
        srch = st.selectbox("Şasi/Firma Ara:", options=(i_list['id'].astype(str) + " | " + i_list['sasi_no']).tolist(), index=None)
        if srch:
            sid = int(srch.split(" |")[0]); cur = i_list[i_list['id'] == sid].iloc[0]
            with st.form("upd_form"):
                nd = st.selectbox("Yeni Durum", ["Teste Gönderildi", "Tamamlandı - Olumlu", "Tamamlandı - Olumsuz", "Reddedildi"])
                sl = st.checkbox("Silme Talebi")
                if st.form_submit_button("Güncelle"):
                    durum_guncelle_by_id(sid, cur['sasi_no'], nd, "", talep_et_silme=sl, silme_nedeni="Kullanıcı Talebi")
                    st.rerun()

with tabs[2]:
    st.subheader("📥 Veri Girişi")
    c_form, c_excel = st.columns(2)
    with c_form:
        with st.form("manuel_f"):
            st.write("Elden Kayıt Girişi")
            bn, fa, ma, ti, sn = st.text_input("B.No"), st.text_input("Firma"), st.text_input("Marka"), st.text_input("Tip"), st.text_input("Şasi")
            if st.form_submit_button("Ekle"):
                conn = sqlite3.connect('tse_v4.db'); conn.cursor().execute("INSERT INTO denetimler (firma_adi, marka, arac_tipi, sasi_no, basvuru_no, durum, basvuru_tarihi, secim_tarihi, il) VALUES (?,?,?,?,?, 'Teste Gönderildi', ?, ?, ?)", (fa, ma, ti, sn, bn, datetime.now().strftime("%Y-%m-%d"), datetime.now().strftime("%Y-%m-%d"), st.session_state.sorumlu_il)); conn.commit(); conn.close(); st.rerun()

if st.session_state.rol == "admin":
    with tabs[3]:
        st.subheader("👑 Yönetim Paneli")
        co, cs = st.columns(2)
        with co:
            st.markdown(f"**Onay Bekleyen Üyeler ({b_onay})**")
            conn = sqlite3.connect('tse_v4.db'); k_df = pd.read_sql_query("SELECT * FROM kullanicilar WHERE onay_durumu=0", conn); conn.close()
            for _, r in k_df.iterrows():
                st.write(f"👤 {r['kullanici_adi']} ({r['sorumlu_il']})")
                if st.button("Onayla", key=f"o_{r['id']}"):
                    c = sqlite3.connect('tse_v4.db'); c.execute("UPDATE kullanicilar SET onay_durumu=1 WHERE id=?", (r['id'],)); c.commit(); c.close(); st.rerun()
        with cs:
            st.markdown(f"**Silme Talepleri ({b_silme})**")
            for _, r in df[df['silme_talebi']=="1"].iterrows():
                st.write(f"🗑️ {r['sasi_no']}")
                if st.button("Kalıcı Sil", key=f"sil_{r['id']}"):
                    c = sqlite3.connect('tse_v4.db'); c.execute("DELETE FROM denetimler WHERE id=?", (r['id'],)); c.commit(); c.close(); st.rerun()
