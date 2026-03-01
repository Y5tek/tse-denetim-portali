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

# --- 1. AYARLAR VE GÜVENLİK ---
try:
    GONDERICI_MAIL = st.secrets["GONDERICI_MAIL"]
    GONDERICI_SIFRE = st.secrets["GONDERICI_SIFRE"].replace(" ", "")
    ADMIN_MAIL = st.secrets["ADMIN_MAIL"]
except Exception:
    st.error("Kritik Hata: Streamlit Secrets (Mail ayarları) bulunamadı!")
    st.stop()

SMTP_SUNUCU = "smtp.gmail.com"
SMTP_PORT = 465

# --- 2. VERİTABANI MOTORU ---
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
    conn.commit()
    conn.close()

veritabanini_hazirla()

# --- 3. YARDIMCI FONKSİYONLAR ---
def admin_bildirim_mail_at(konu, icerik):
    msg = MIMEMultipart()
    msg['From'], msg['To'], msg['Subject'] = GONDERICI_MAIL, ADMIN_MAIL, konu
    msg.attach(MIMEText(f"<html><body><h3>TSE Bildirim</h3><p>{icerik}</p></body></html>", 'html'))
    try:
        server = smtplib.SMTP_SSL(SMTP_SUNUCU, SMTP_PORT)
        server.login(GONDERICI_MAIL, GONDERICI_SIFRE)
        server.send_message(msg); server.quit()
    except: pass

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
    df['secim_tarihi_dt'] = pd.to_datetime(df['secim_tarihi'], errors='coerce')
    bugun = pd.to_datetime(datetime.now().strftime("%Y-%m-%d"))
    df['Geçen Gün'] = (bugun - df['secim_tarihi_dt']).dt.days.apply(lambda x: str(int(x)) if pd.notnull(x) else '-')
    df['secim_tarihi'] = df['secim_tarihi_dt'].dt.strftime('%Y-%m-%d').fillna('-')
    for c in df.columns: 
        if c not in ['Geçen Gün', 'secim_tarihi_dt']: df[c] = df[c].fillna('-')
    return df

def satir_boya(row): 
    if row['durum'] == 'Şasi Bekliyor': return ['background-color: rgba(255, 193, 7, 0.2)'] * len(row)
    elif row['durum'] == 'Tamamlandı - Olumlu': return ['background-color: rgba(40, 167, 69, 0.2)'] * len(row)
    elif row['durum'] == 'Tamamlandı - Olumsuz': return ['background-color: rgba(220, 53, 69, 0.2)'] * len(row)
    return [''] * len(row)

def durum_guncelle_by_id(kayit_id, sasi_no, yeni_durum, notlar, starih="MEVCUT", talep_et_silme=False, silme_nedeni=""):
    conn = sqlite3.connect('tse_v4.db')
    g_ani = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sil_v = 1 if talep_et_silme else 0
    if starih == "MEVCUT":
        conn.cursor().execute('UPDATE denetimler SET sasi_no=?, durum=?, notlar=?, guncelleme_tarihi=?, silme_talebi=?, silme_nedeni=? WHERE id=?', (sasi_no, yeni_durum, notlar, g_ani, sil_v, silme_nedeni, kayit_id))
    else:
        conn.cursor().execute('UPDATE denetimler SET sasi_no=?, durum=?, secim_tarihi=?, notlar=?, guncelleme_tarihi=?, silme_talebi=?, silme_nedeni=? WHERE id=?', (sasi_no, yeni_durum, starih, notlar, g_ani, sil_v, silme_nedeni, kayit_id))
    conn.commit(); conn.close()
    if talep_et_silme: threading.Thread(target=admin_bildirim_mail_at, args=("⚠️ YENİ SİLME TALEBİ", f"{sasi_no} için silme talebi var.")).start()

# --- 4. OTURUM VE GİRİŞ ---
if 'giris_yapildi' not in st.session_state:
    st.session_state.update({'giris_yapildi': False, 'kullanici_adi': "", 'rol': "", 'sorumlu_il': "", 'excel_yetkisi': 0})

if not st.session_state.giris_yapildi:
    c1, c2, c3 = st.columns([1, 2, 1])
    with c2:
        st.markdown("<h1 style='text-align: center; color: #E03131;'>🇹🇷 TSE DENETİM PORTALI</h1>", unsafe_allow_html=True)
        tg, tk = st.tabs(["🔐 Giriş Yap", "📝 Kayıt Ol"])
        with tg:
            with st.form("login_form"):
                ka, si = st.text_input("Kullanıcı Adı"), st.text_input("Şifre", type="password")
                if st.form_submit_button("Giriş Yap", use_container_width=True):
                    conn = sqlite3.connect('tse_v4.db')
                    u = conn.cursor().execute("SELECT rol, sorumlu_il, onay_durumu, excel_yukleme_yetkisi FROM kullanicilar WHERE kullanici_adi=? AND sifre=?", (ka, si)).fetchone()
                    conn.close()
                    if u:
                        if u[2]==0: st.warning("Oturum onayı bekleniyor.")
                        else: 
                            st.session_state.update({'giris_yapildi':True, 'kullanici_adi':ka, 'rol':u[0], 'sorumlu_il':u[1], 'excel_yetkisi':u[3]})
                            st.rerun()
                    else: st.error("❌ Hatalı bilgiler.")
        with tk:
            with st.form("register_form"):
                yk, ys, ye, yil = st.text_input("Kullanıcı Adı"), st.text_input("Şifre"), st.text_input("E-Posta"), st.selectbox("İl", ["Ankara", "İstanbul", "İzmir", "Bursa", "Kocaeli", "Diğer"])
                if st.form_submit_button("Kayıt Talebi Gönder"):
                    try:
                        conn = sqlite3.connect('tse_v4.db')
                        conn.cursor().execute("INSERT INTO kullanicilar (kullanici_adi, sifre, rol, email, sorumlu_il, onay_durumu, excel_yukleme_yetkisi) VALUES (?, ?, 'kullanici', ?, ?, 0, 0)", (yk, ys, ye, yil))
                        conn.commit(); conn.close()
                        threading.Thread(target=admin_bildirim_mail_at, args=("📝 YENİ KAYIT", f"Yeni üye talebi: {yk}")).start()
                        st.success("Talebiniz iletildi."); time.sleep(1); st.rerun()
                    except: st.error("Kullanıcı adı mevcut.")
    st.stop()

# --- 5. ANA PANEL ---
b_onay, b_silme = durum_sayilarini_al()
toplam_bekleyen = b_onay + b_silme
df = verileri_getir()

with st.sidebar:
    st.markdown("<h2 style='color: #E03131;'>TSE PANEL</h2>", unsafe_allow_html=True)
    st.write(f"👤 **{st.session_state.kullanici_adi}**")
    st.write(f"📍 **{st.session_state.sorumlu_il}**")
    if st.button("🚪 Oturumu Kapat", use_container_width=True):
        st.session_state.clear(); st.rerun()

main_tabs = ["📊 Ana Tablo", "🛠️ Numune Kayıt Girişi", "📥 Veri Girişi"]
if st.session_state.rol == "admin": main_tabs.append(f"👑 Yönetici ({toplam_bekleyen})")
tabs = st.tabs(main_tabs)

# TAB 0: ANA TABLO
with tabs[0]:
    st.subheader("Sistem Kayıtları")
    c_m1, c_m2, c_m3 = st.columns(3)
    c_m1.metric("Toplam", len(df))
    c_m2.metric("Teste Gönderildi", len(df[df['durum'] == 'Teste Gönderildi']))
    c_m3.metric("Olumlu", len(df[df['durum'] == 'Tamamlandı - Olumlu']))
    
    istenen = ['sasi_no', 'durum', 'secim_tarihi', 'Geçen Gün', 'marka', 'arac_tipi', 'firma_adi', 'il']
    display_df = df[[c for c in istenen if c in df.columns]]
    
    src = st.text_input("🔍 Hızlı Filtrele (Şasi, Marka, Firma vb.):")
    if src: display_df = display_df[display_df.apply(lambda r: src.lower() in r.astype(str).str.lower().values, axis=1)]
    
    st.dataframe(display_df.style.apply(satir_boya, axis=1), use_container_width=True, height=500)

# TAB 1: İŞLEM PANELİ
with tabs[1]:
    st.subheader("İşlem Paneli")
    i_df = df if st.session_state.rol == "admin" else df[(df['il'] == st.session_state.sorumlu_il)]
    
    c_left, c_right = st.columns(2)
    with c_left:
        st.markdown("#### 🆕 Şasi Atama")
        b_list = i_df[i_df['durum'] == 'Şasi Bekliyor']
        sel = st.selectbox("Bekleyen Başvuru Seç:", options=(b_list['id'].astype(str) + " | " + b_list['basvuru_no'] + " | " + b_list['firma_adi']).tolist(), index=None)
        if sel:
            sid = int(sel.split(" |")[0])
            vin = st.text_input("VIN (Şasi) Numarası Giriniz:")
            if st.button("Kaydet ve Teste Gönder"):
                durum_guncelle_by_id(sid, vin, 'Teste Gönderildi', "", starih=datetime.now().strftime("%Y-%m-%d"))
                st.success("İşlem Başarılı!"); time.sleep(1); st.rerun()

    with c_right:
        st.markdown("#### 🔍 Durum Güncelle")
        i_list = i_df[i_df['durum'] != 'Şasi Bekliyor']
        srch = st.selectbox("Güncellenecek Şasi/Firma:", options=(i_list['id'].astype(str) + " | " + i_list['sasi_no']).tolist(), index=None)
        if srch:
            sid = int(srch.split(" |")[0])
            nd = st.selectbox("Yeni Durum", ["Teste Gönderildi", "Tamamlandı - Olumlu", "Tamamlandı - Olumsuz", "Reddedildi"])
            if st.button("Güncelle"):
                durum_guncelle_by_id(sid, "MEVCUT", nd, "")
                st.rerun()

# TAB 2: VERİ GİRİŞİ (EXCEL VE MANUEL)
with tabs[2]:
    st.subheader("📥 Veri Girişi")
    c_form, c_excel = st.columns(2)
    
    with c_form:
        st.markdown("### 📝 Elden Tekli Kayıt")
        with st.form("manuel_form"):
            bn = st.text_input("Başvuru No")
            fa = st.text_input("Firma Adı")
            ma = st.text_input("Marka")
            ti = st.text_input("Araç Tipi")
            if st.form_submit_button("Sisteme Kaydet"):
                conn = sqlite3.connect('tse_v4.db')
                conn.cursor().execute("INSERT INTO denetimler (firma_adi, marka, arac_tipi, basvuru_no, durum, basvuru_tarihi, il, ekleyen_kullanici) VALUES (?,?,?,?,?,?,?,?)", 
                                     (fa, ma, ti, bn, 'Şasi Bekliyor', datetime.now().strftime("%Y-%m-%d"), st.session_state.sorumlu_il, st.session_state.kullanici_adi))
                conn.commit(); conn.close()
                st.success("Kayıt Eklendi!"); time.sleep(1); st.rerun()

    with c_excel:
        st.markdown("### 📥 Excel ile Toplu Yükleme")
        up = st.file_uploader("Dosya Seç (xlsx veya csv)", type=['xlsx', 'csv'])
        if up:
            try:
                df_up = pd.read_excel(up) if up.name.endswith('.xlsx') else pd.read_csv(up)
                df_up.columns = [str(c).strip().lower().replace(" ", "").replace("_", "") for c in df_up.columns]
                
                st.write("📌 Örnek Veri:", df_up.head(2))
                if st.button("Verileri Aktar"):
                    conn = sqlite3.connect('tse_v4.db')
                    bas, hat = 0, 0
                    for _, r in df_up.iterrows():
                        try:
                            # Akıllı sütun yakalama
                            def gv(tags):
                                for t in tags:
                                    if t in df_up.columns: return str(r[t])
                                return "-"
                            
                            conn.cursor().execute("INSERT INTO denetimler (basvuru_no, firma_adi, marka, arac_tipi, durum, basvuru_tarihi, il, ekleyen_kullanici) VALUES (?,?,?,?,?,?,?,?)",
                                (gv(['basvuruno', 'no', 'basvuru']), gv(['firmaadi', 'firma', 'unvan']), gv(['marka']), gv(['aractipi', 'tip', 'model']), 'Şasi Bekliyor', datetime.now().strftime("%Y-%m-%d"), st.session_state.sorumlu_il, st.session_state.kullanici_adi))
                            bas += 1
                        except: hat += 1
                    conn.commit(); conn.close()
                    st.success(f"Bitti: {bas} Başarılı, {hat} Hata"); time.sleep(2); st.rerun()
            except Exception as e: st.error(f"Hata: {e}")

# TAB 3: YÖNETİCİ PANELİ
if st.session_state.rol == "admin":
    with tabs[3]:
        st.subheader("👑 Yönetici Paneli")
        co, cs = st.columns(2)
        with co:
            st.markdown(f"**Onay Bekleyen Üyeler ({b_onay})**")
            conn = sqlite3.connect('tse_v4.db'); k_df = pd.read_sql_query("SELECT * FROM kullanicilar WHERE onay_durumu=0", conn); conn.close()
            for _, r in k_df.iterrows():
                if st.button(f"Onayla: {r['kullanici_adi']}", key=f"o_{r['id']}"):
                    c = sqlite3.connect('tse_v4.db'); c.execute("UPDATE kullanicilar SET onay_durumu=1 WHERE id=?", (r['id'],)); c.commit(); c.close(); st.rerun()
        with cs:
            st.markdown(f"**Silme Talepleri ({b_silme})**")
            for _, r in df[df['silme_talebi']==1].iterrows():
                if st.button(f"SİL: {r['sasi_no']}", key=f"sil_{r['id']}"):
                    c = sqlite3.connect('tse_v4.db'); c.execute("DELETE FROM denetimler WHERE id=?", (r['id'],)); c.commit(); c.close(); st.rerun()
