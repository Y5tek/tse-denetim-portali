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
# Streamlit Secrets üzerinden bilgiler çekiliyor (Güvenlik için koda şifre yazılmaz)
try:
    GONDERICI_MAIL = st.secrets["GONDERICI_MAIL"]
    GONDERICI_SIFRE = st.secrets["GONDERICI_SIFRE"].replace(" ", "")
    ADMIN_MAIL = st.secrets["ADMIN_MAIL"]
except Exception:
    st.error("Kritik Hata: Streamlit Secrets ayarları eksik!")
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
    if not df.empty:
        df['secim_tarihi_dt'] = pd.to_datetime(df['secim_tarihi'], errors='coerce')
        bugun = pd.to_datetime(datetime.now().strftime("%Y-%m-%d"))
        # [cite_start]Geçen Gün hesaplaması (Hataları önlemek için metin formatında) [cite: 1, 2]
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
    st.session_state.update({'giris_yapildi': False, 'kullanici_adi': "", 'rol': "", 'sorumlu_il': "", 'excel_yetkisi': 0})

def durum_guncelle_by_id(kayit_id, sasi_no, yeni_durum, notlar, starih="MEVCUT", talep_et_silme=False, silme_nedeni=""):
    conn = sqlite3.connect('tse_v4.db'); g_ani = datetime.now().strftime("%Y-%m-%d %H:%M:%S"); sil_v = 1 if talep_et_silme else 0
    if starih == "MEVCUT": conn.cursor().execute('UPDATE denetimler SET sasi_no=?, durum=?, notlar=?, guncelleme_tarihi=?, silme_talebi=?, silme_nedeni=? WHERE id=?', (sasi_no, yeni_durum, notlar, g_ani, sil_v, silme_nedeni, kayit_id))
    else: conn.cursor().execute('UPDATE denetimler SET sasi_no=?, durum=?, secim_tarihi=?, notlar=?, guncelleme_tarihi=?, silme_talebi=?, silme_nedeni=? WHERE id=?', (sasi_no, yeni_durum, starih, notlar, g_ani, sil_v, silme_nedeni, kayit_id))
    conn.commit(); conn.close()
    if talep_et_silme: threading.Thread(target=admin_bildirim_mail_at, args=("⚠️ YENİ SİLME TALEBİ", f"{sasi_no} için silme talebi var.")).start()

# --- 4. GİRİŞ EKRANI ---
if not st.session_state.giris_yapildi:
    _, c2, _ = st.columns([1, 2, 1])
    with c2:
        st.markdown("<h1 style='text-align: center; color: #E03131;'>🇹🇷 TSE DENETİM PORTALI</h1>", unsafe_allow_html=True)
        tg, tk = st.tabs(["🔐 Giriş Yap", "📝 Kayıt Ol"])
        with tg:
            with st.form("login_form"):
                ka, si = st.text_input("Kullanıcı Adı"), st.text_input("Şifre", type="password")
                if st.form_submit_button("Giriş Yap", use_container_width=True):
                    conn = sqlite3.connect('tse_v4.db'); u = conn.cursor().execute("SELECT rol, sorumlu_il, onay_durumu, excel_yukleme_yetkisi FROM kullanicilar WHERE kullanici_adi=? AND sifre=?", (ka, si)).fetchone(); conn.close()
                    if u:
                        if u[2]==0: st.warning("Oturum onayı bekleniyor.")
                        else: st.session_state.update({'giris_yapildi':True, 'kullanici_adi':ka, 'rol':u[0], 'sorumlu_il':u[1], 'excel_yetkisi':u[3]}); st.rerun()
                    else: st.error("❌ Hatalı bilgiler.")
        with tk:
            with st.form("register_form"):
                yk, ys, ye, yil = st.text_input("Kullanıcı Adı"), st.text_input("Şifre"), st.text_input("E-Posta"), st.selectbox("İl", ["Ankara", "İstanbul", "İzmir", "Bursa", "Kocaeli", "Diğer"])
                if st.form_submit_button("Kayıt Talebi Gönder"):
                    try:
                        conn = sqlite3.connect('tse_v4.db'); conn.cursor().execute("INSERT INTO kullanicilar (kullanici_adi, sifre, rol, email, sorumlu_il, onay_durumu, excel_yukleme_yetkisi) VALUES (?, ?, 'kullanici', ?, ?, 0, 0)", (yk, ys, ye, yil)); conn.commit(); conn.close()
                        threading.Thread(target=admin_bildirim_mail_at, args=("📝 YENİ KAYIT", f"Yeni üye talebi: {yk}")).start()
                        st.success("Tebrikler! Talebiniz iletildi."); time.sleep(1); st.rerun()
                    except: st.error("Kullanıcı adı mevcut.")
    st.stop()

# --- 5. ANA EKRAN ---
b_onay, b_silme = durum_sayilarini_al()
df = verileri_getir()

with st.sidebar:
    st.markdown("<h2 style='color: #E03131;'>TSE PANEL</h2>", unsafe_allow_html=True)
    st.write(f"👤 **{st.session_state.kullanici_adi}**")
    if st.session_state.rol == "admin" and (b_onay + b_silme > 0):
        st.error(f"🚨 {b_onay + b_silme} Bekleyen İşlem!")
    if st.button("🚪 Oturumu Kapat", use_container_width=True):
        st.session_state.clear(); st.rerun()

admin_tab_label = f"👑 Yönetici Paneli ({b_onay + b_silme})" if (st.session_state.rol == "admin" and (b_onay + b_silme > 0)) else "👑 Yönetici Paneli"
main_tabs = st.tabs(["📊 Ana Tablo", "🛠️ Numune Kayıt Girişi", "📥 Veri Girişi"] + ([admin_tab_label] if st.session_state.rol == "admin" else []))

with main_tabs[0]:
    st.subheader("Sistem Kayıtları")
    istenen = ['sasi_no', 'durum', 'secim_tarihi', 'Geçen Gün', 'marka', 'arac_tipi', 'firma_adi', 'arac_kategori', 'birim', 'il']
    display_df = df[[c for c in istenen if c in df.columns] + [c for c in df.columns if c not in istenen and c not in ['secim_tarihi_dt', 'silme_talebi']]]
    st.dataframe(display_df.style.apply(satir_boya, axis=1), use_container_width=True, height=600)

with main_tabs[1]:
    st.subheader("İşlem Paneli")
    # [cite_start]Filtreleme: Admin her şeyi görür, uzman kendi ilini [cite: 1]
    i_df = df if st.session_state.rol == "admin" else df[df['il'] == st.session_state.sorumlu_il]
    
    c_left, c_right = st.columns(2)
    with c_left:
        st.markdown("#### 🆕 Şasi Atama")
        b_list = i_df[i_df['durum'] == 'Şasi Bekliyor']
        if not b_list.empty:
            sel = st.selectbox("Başvuru Seç:", options=(b_list['id'].astype(str) + " | " + b_list['basvuru_no']).tolist(), index=None)
            if sel:
                sid = int(sel.split(" |")[0]); vin = st.text_input("VIN (Şasi)")
                if st.button("Kaydet ve Teste Gönder"):
                    durum_guncelle_by_id(sid, vin, 'Teste Gönderildi', "", starih=datetime.now().strftime("%Y-%m-%d"))
                    st.rerun()
        else: st.info("Bekleyen kayıt bulunamadı.")

    with c_right:
        st.markdown("#### 🔍 Güncelleme")
        i_list = i_df[i_df['durum'] != 'Şasi Bekliyor']
        if not i_list.empty:
            srch = st.selectbox("Şasi/Firma Ara:", options=(i_list['id'].astype(str) + " | " + i_list['sasi_no']).tolist(), index=None)
            if srch:
                sid_num = int(srch.split(" |")[0])
                # [cite_start]IndexError kontrolü: Seçilen ID'nin listede olduğundan emin oluyoruz [cite: 1]
                match = i_list[i_list['id'].astype(str) == str(sid_num)]
                if not match.empty:
                    cur = match.iloc[0]
                    with st.form("upd_form"):
                        nd = st.selectbox("Yeni Durum", ["Teste Gönderildi", "Tamamlandı - Olumlu", "Tamamlandı - Olumsuz", "Reddedildi"])
                        sl = st.checkbox("Silme Talebi")
                        if st.form_submit_button("Güncelle"):
                            durum_guncelle_by_id(sid_num, cur['sasi_no'], nd, "", talep_et_silme=sl, silme_nedeni="Kullanıcı Talebi")
                            st.rerun()
        else: st.info("Güncellenebilecek şasili kayıt bulunamadı.")

with main_tabs[2]:
    st.subheader("📥 Veri Girişi")
    c_f, c_e = st.columns(2)
    with c_f:
        with st.form("m_f"):
            st.write("Elden Kayıt")
            bn, fa, ma, ti, sn = st.text_input("B.No"), st.text_input("Firma"), st.text_input("Marka"), st.text_input("Tip"), st.text_input("Şasi")
            if st.form_submit_button("Ekle"):
                conn = sqlite3.connect('tse_v4.db'); conn.cursor().execute("INSERT INTO denetimler (firma_adi, marka, arac_tipi, sasi_no, basvuru_no, durum, basvuru_tarihi, secim_tarihi, il) VALUES (?,?,?,?,?, 'Şasi Bekliyor', ?, ?, ?)", (fa, ma, ti, sn, bn, datetime.now().strftime("%Y-%m-%d"), datetime.now().strftime("%Y-%m-%d"), st.session_state.sorumlu_il)); conn.commit(); conn.close(); st.rerun()
    with c_e:
        up = st.file_uploader("Excel Yükle", type=['xlsx'])
        if up and st.button("Sisteme Aktar"):
            xl = pd.read_excel(up)
            conn = sqlite3.connect('tse_v4.db')
            for _, r in xl.iterrows():
                try:
                    conn.execute("INSERT INTO denetimler (basvuru_no, firma_adi, marka, arac_tipi, il, durum, basvuru_tarihi) VALUES (?,?,?,?,?,'Şasi Bekliyor',?)", (str(r.get('Başvuru No', '-')), str(r.get('Firma Adı', '-')), str(r.get('Marka', '-')), str(r.get('Araç Tipi', '-')), st.session_state.sorumlu_il, datetime.now().strftime("%Y-%m-%d")))
                except: continue
            conn.commit(); conn.close(); st.success("Aktarıldı."); st.rerun()

if st.session_state.rol == "admin":
    with main_tabs[3]:
        st.subheader("⚙️ Yönetici İşlemleri")
        co, cs = st.columns(2)
        with co:
            st.write(f"Üye Onayları ({b_onay})")
            conn = sqlite3.connect('tse_v4.db'); k_df = pd.read_sql_query("SELECT * FROM kullanicilar WHERE onay_durumu=0", conn); conn.close()
            for _, r in k_df.iterrows():
                if st.button(f"Onayla: {r['kullanici_adi']}", key=f"o_{r['id']}"):
                    c = sqlite3.connect('tse_v4.db'); c.execute("UPDATE kullanicilar SET onay_durumu=1 WHERE id=?", (r['id'],)); c.commit(); c.close(); st.rerun()
        with cs:
            st.write(f"Silme Talepleri ({b_silme})")
            for _, r in df[df['silme_talebi']==1].iterrows():
                if st.button(f"SİL: {r['sasi_no']}", key=f"s_{r['id']}"):
                    c = sqlite3.connect('tse_v4.db'); c.execute("DELETE FROM denetimler WHERE id=?", (r['id'],)); c.commit(); c.close(); st.rerun()
