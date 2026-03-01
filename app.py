import streamlit as st
import pandas as pd
from datetime import datetime
import io
import threading
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import time
import os
import hashlib
import psycopg2
from sqlalchemy import create_engine
from contextlib import contextmanager
import numpy as np
import plotly.express as px

# --- KULLANIM KILAVUZU METNİ ---
KILAVUZ_METNI = """# 🇹🇷 TSE NUMUNE TAKİP PORTALI - KULLANIM KILAVUZU

Bu portal, TSE numune takip süreçlerini dijitalleştirmek için tasarlanmıştır.

## 🖥 Sistem Sekmeleri
* **📊 Ana Tablo:** Gelişmiş filtreler ve interaktif grafiklerle verileri analiz edin.
* **🛠️ Numune Kayıt Girişi:** Bekleyen başvurulara şasi (VIN) atayın veya mevcut durumları güncelleyin.
* **📥 Veri Girişi:** Sisteme tekli form ile veya akıllı Excel eşleştirmesi ile toplu veri yükleyin.
* **👤 Profilim:** Hesap şifrenizi güvenli bir şekilde güncelleyin.
* **👑 Yönetici Paneli:** Kullanıcı yetkilerini ve silme taleplerini yönetin (Sadece Admin).
"""

st.set_page_config(page_title="TSE NUMUNE TAKİP PORTALI", layout="wide")

# --- AYARLAR VE GÜVENLİK ---
try:
    GONDERICI_MAIL = st.secrets["GONDERICI_MAIL"]
    GONDERICI_SIFRE = st.secrets["GONDERICI_SIFRE"].replace(" ", "") 
    ADMIN_MAIL = st.secrets["ADMIN_MAIL"]
    DB_URI = st.secrets["DB_URI"]
except Exception:
    st.error("Kritik Hata: Streamlit Secrets ayarları bulunamadı!")
    st.stop()

SMTP_SUNUCU, SMTP_PORT = "smtp.gmail.com", 465 

def sifreyi_hashle(sifre_metni):
    return hashlib.sha256(sifre_metni.encode('utf-8')).hexdigest()

# --- VERİTABANI MOTORU ---
engine = create_engine(DB_URI)

@contextmanager
def get_db():
    conn = psycopg2.connect(DB_URI)
    try: yield conn
    finally: conn.close()

def veritabanini_hazirla():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS denetimler (
            id SERIAL PRIMARY KEY, basvuru_no TEXT, firma_adi TEXT NOT NULL, marka TEXT,
            arac_kategori TEXT, arac_tipi TEXT NOT NULL, varyant TEXT, versiyon TEXT, ticari_ad TEXT,
            gtip_no TEXT, birim TEXT, uretim_ulkesi TEXT, arac_sayisi TEXT, sasi_no TEXT UNIQUE, 
            basvuru_tarihi DATE, secim_tarihi DATE, il TEXT, durum TEXT DEFAULT 'Şasi Bekliyor',
            notlar TEXT, guncelleme_tarihi TEXT, ekleyen_kullanici TEXT, silme_talebi INTEGER DEFAULT 0, silme_nedeni TEXT)''')
        
        cursor.execute('''CREATE TABLE IF NOT EXISTS kullanicilar (
            id SERIAL PRIMARY KEY, kullanici_adi TEXT UNIQUE NOT NULL, sifre TEXT NOT NULL,
            rol TEXT NOT NULL, email TEXT, sorumlu_il TEXT, onay_durumu INTEGER DEFAULT 1, excel_yukleme_yetkisi INTEGER DEFAULT 0)''')
        conn.commit()

        cursor.execute("SELECT COUNT(*) FROM kullanicilar WHERE rol = 'admin'")
        if cursor.fetchone()[0] == 0:
            cursor.execute("INSERT INTO kullanicilar (kullanici_adi, sifre, rol, email, sorumlu_il, onay_durumu, excel_yukleme_yetkisi) VALUES (%s, %s, 'admin', %s, 'Tümü', 1, 1)", ("admin", sifreyi_hashle("admin123"), ADMIN_MAIL))
            conn.commit()

veritabanini_hazirla()

# --- BİLDİRİM & EXCEL AKILLI YÜKLEME ---
def mail_gonder(kime, konu, icerik):
    msg = MIMEMultipart()
    msg['From'], msg['To'], msg['Subject'] = GONDERICI_MAIL, kime, konu
    msg.attach(MIMEText(f"<html><body><h3>TSE Bildirim</h3><p>{icerik}</p></body></html>", 'html'))
    try:
        server = smtplib.SMTP_SSL(SMTP_SUNUCU, SMTP_PORT)
        server.login(GONDERICI_MAIL, GONDERICI_SIFRE)
        server.send_message(msg); server.quit()
    except: pass

def excel_kaydet_ve_mail_at(df_yeni, atlanan_sayi):
    df_yeni = df_yeni.replace({np.nan: None})
    df_yeni.to_sql('denetimler', engine, if_exists='append', index=False)
    
    try:
        with get_db() as conn:
            il_ozeti = df_yeni['il'].value_counts().to_dict()
            cursor = conn.cursor()
            for il_adi, adet in il_ozeti.items():
                cursor.execute("SELECT email, kullanici_adi FROM kullanicilar WHERE sorumlu_il=%s AND onay_durumu=1", (il_adi,))
                for k_mail, k_adi in cursor.fetchall():
                    if k_mail and "@" in k_mail: 
                        m_icerik = f"Merhaba <b>{k_adi}</b>,<br>Sorumlu olduğunuz <b>{il_adi}</b> ili için sisteme <b>{adet} adet</b> yeni kayıt yüklenmiştir."
                        threading.Thread(target=mail_gonder, args=(k_mail, f"TSE Sistemi - {il_adi} İçin Yeni Veri", m_icerik)).start()
    except: pass
    st.success(f"Tebrikler! {len(df_yeni)} yeni kayıt başarıyla eklendi. ({atlanan_sayi} mükerrer atlandı.)")
    time.sleep(2); st.rerun()

def akilli_sutun_eslestir(df_columns):
    yeni = {}
    for col in df_columns:
        tc = str(col).lower().replace(" ", "").replace("_", "").replace(".", "").replace("ş", "s").replace("ı", "i").replace("ğ", "g").replace("ü", "u").replace("ç", "c").replace("ö", "o")
        if "basvuru" in tc: yeni[col] = "basvuru_no"
        elif "firma" in tc or "kurum" in tc: yeni[col] = "firma_adi"
        elif "marka" in tc: yeni[col] = "marka"
        elif "kategori" in tc: yeni[col] = "arac_kategori"
        elif "tip" in tc: yeni[col] = "arac_tipi"
        elif "varyant" in tc or "variant" in tc: yeni[col] = "varyant"
        elif "versiyon" in tc or "version" in tc: yeni[col] = "versiyon"
        elif "ticari" in tc: yeni[col] = "ticari_ad"
        elif "gtip" in tc: yeni[col] = "gtip_no"
        elif "birim" in tc or "sube" in tc or "hizmet" in tc: yeni[col] = "birim"
        elif "ulke" in tc: yeni[col] = "uretim_ulkesi"
        elif "sayi" in tc or "adet" in tc: yeni[col] = "arac_sayisi"
        else: yeni[col] = col 
    return yeni

# --- VERİ ÇEKME VE İŞLEME ---
def verileri_getir():
    try:
        df = pd.read_sql_query("SELECT * FROM denetimler ORDER BY id DESC", engine)
        if not df.empty:
            df['secim_tarihi_dt'] = pd.to_datetime(df['secim_tarihi'], errors='coerce')
            bugun = pd.to_datetime(datetime.now().strftime("%Y-%m-%d"))
            df['Geçen Gün'] = (bugun - df['secim_tarihi_dt']).dt.days.apply(lambda x: str(int(x)) if pd.notnull(x) else '-')
            df['secim_tarihi'] = df['secim_tarihi_dt'].dt.strftime('%Y-%m-%d').fillna('-')
            for c in df.columns: 
                if c not in ['Geçen Gün', 'secim_tarihi_dt']: df[c] = df[c].fillna('-')
        return df
    except: return pd.DataFrame()

# --- OTURUM YÖNETİMİ VE GİRİŞ ---
if 'giris_yapildi' not in st.session_state:
    st.session_state.update({'giris_yapildi': False, 'kullanici_adi': "", 'rol': "", 'sorumlu_il': "", 'excel_yetkisi': 0, 'ob_df': None, 'atlanmis': 0})

def durum_guncelle(kid, sasi, durum, notlar, starih="MEVCUT", silme=False, snedeni=""):
    g_ani = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sil_v = 1 if silme else 0
    with get_db() as conn:
        c = conn.cursor()
        if starih == "MEVCUT": c.execute('UPDATE denetimler SET sasi_no=%s, durum=%s, notlar=%s, guncelleme_tarihi=%s, silme_talebi=%s, silme_nedeni=%s WHERE id=%s', (sasi, durum, notlar, g_ani, sil_v, snedeni, int(kid)))
        else: c.execute('UPDATE denetimler SET sasi_no=%s, durum=%s, secim_tarihi=%s, notlar=%s, guncelleme_tarihi=%s, silme_talebi=%s, silme_nedeni=%s WHERE id=%s', (sasi, durum, starih, notlar, g_ani, sil_v, snedeni, int(kid)))
        conn.commit()
    if silme: threading.Thread(target=mail_gonder, args=(ADMIN_MAIL, "⚠️ YENİ SİLME TALEBİ", f"{sasi} için silme talebi var.")).start()

if not st.session_state.giris_yapildi:
    c1, c2, c3 = st.columns([1, 2, 1])
    with c2:
        if os.path.exists("tse_logo.png"): st.image("tse_logo.png", use_container_width=True)
        st.markdown("<h1 style='text-align: center; color: #E03131;'> TSE NUMUNE TAKİP</h1>", unsafe_allow_html=True)
        tg, tk = st.tabs(["🔐 Giriş Yap", "📝 Kayıt Ol"])
        with tg:
            with st.form("login"):
                ka, si = st.text_input("Kullanıcı Adı"), st.text_input("Şifre", type="password")
                if st.form_submit_button("Giriş Yap", use_container_width=True):
                    with get_db() as conn:
                        c = conn.cursor()
                        c.execute("SELECT rol, sorumlu_il, onay_durumu, excel_yukleme_yetkisi FROM kullanicilar WHERE kullanici_adi=%s AND sifre=%s", (ka, sifreyi_hashle(si)))
                        u = c.fetchone()
                    if u:
                        if u[2]==0: st.warning("Oturum onayı bekleniyor.")
                        else: st.session_state.update({'giris_yapildi':True, 'kullanici_adi':ka, 'rol':u[0], 'sorumlu_il':u[1], 'excel_yetkisi':u[3]}); st.rerun()
                    else: st.error("❌ Hatalı bilgi.")
        with tk:
            with st.form("reg"):
                yk, ys, ye, yil = st.text_input("Kullanıcı Adı"), st.text_input("Şifre", type="password"), st.text_input("E-Posta"), st.selectbox("İl", ["Ankara", "İstanbul", "İzmir", "Bursa", "Kocaeli", "Diğer"])
                if st.form_submit_button("Kayıt Ol"):
                    try:
                        with get_db() as conn:
                            conn.cursor().execute("INSERT INTO kullanicilar (kullanici_adi, sifre, rol, email, sorumlu_il, onay_durumu, excel_yukleme_yetkisi) VALUES (%s, %s, 'kullanici', %s, %s, 0, 0)", (yk, sifreyi_hashle(ys), ye, yil))
                            conn.commit()
                        st.success("Talebiniz iletildi."); time.sleep(1); st.rerun()
                    except: st.error("Kullanıcı adı mevcut.")
    st.stop()

# --- ANA EKRAN YÜKLENİYOR ---
df = verileri_getir()
with get_db() as conn:
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM kullanicilar WHERE onay_durumu = 0")
    b_onay = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM denetimler WHERE silme_talebi = 1")
    b_silme = c.fetchone()[0]

with st.sidebar:
    if os.path.exists("tse_logo.png"): st.image("tse_logo.png", use_container_width=True)
    st.markdown("<h2 style='color: #E03131;'>TSE PANEL</h2>", unsafe_allow_html=True)
    st.write(f"👤 **{st.session_state.kullanici_adi}** | 📍 **{st.session_state.sorumlu_il}**")
    st.divider()
    st.download_button("📄 Kılavuzu İndir", KILAVUZ_METNI, "Kilavuz.md", "text/markdown", use_container_width=True)
    if st.button("🚪 Çıkış", use_container_width=True): st.session_state.clear(); st.rerun()

mtabs = ["📊 Ana Tablo", "🛠️ İşlem Paneli", "📥 Veri Girişi", "👤 Profilim"]
if st.session_state.rol == "admin": mtabs.append(f"👑 Admin ({b_onay+b_silme})")
t = st.tabs(mtabs)

# --- SEKME 1: ANALİTİK DASHBOARD VE TABLO ---
with t[0]:
    if not df.empty:
        g_df = df if st.session_state.rol == "admin" else df[df['il'] == st.session_state.sorumlu_il]
        
        # Filtreleme Alanı
        with st.expander("🔎 Gelişmiş Filtreleme (Daralt)"):
            f1, f2, f3 = st.columns(3)
            sec_durum = f1.multiselect("Duruma Göre:", g_df['durum'].unique())
            sec_il = f2.multiselect("İle Göre:", g_df['il'].unique()) if st.session_state.rol == "admin" else [st.session_state.sorumlu_il]
            kelime = f3.text_input("Kelime Arama (Marka, Şasi vb.):")
            
            if sec_durum: g_df = g_df[g_df['durum'].isin(sec_durum)]
            if sec_il and st.session_state.rol == "admin": g_df = g_df[g_df['il'].isin(sec_il)]
            if kelime: g_df = g_df[g_df.apply(lambda r: kelime.lower() in r.astype(str).str.lower().values, axis=1)]

        # Özet Metrikler
        c_m1, c_m2, c_m3 = st.columns(3)
        c_m1.metric("Toplam Listelenen", len(g_df))
        c_m2.metric("Teste Gönderildi", len(g_df[g_df['durum'] == 'Teste Gönderildi']))
        c_m3.metric("Olumlu", len(g_df[g_df['durum'] == 'Tamamlandı - Olumlu']))

        # Grafikler
        if len(g_df) > 0:
            gc1, gc2 = st.columns(2)
            with gc1:
                fig1 = px.pie(g_df, names='durum', title='Durum Dağılımı', hole=0.3)
                st.plotly_chart(fig1, use_container_width=True)
            with gc2:
                if st.session_state.rol == "admin":
                    fig2 = px.bar(g_df['il'].value_counts().reset_index(), x='il', y='count', title='İllere Göre Dağılım', color='il')
                else:
                    fig2 = px.bar(g_df['marka'].value_counts().reset_index().head(10), x='marka', y='count', title='En Çok İşlem Yapılan Markalar', color='marka')
                st.plotly_chart(fig2, use_container_width=True)

        # Tablo
        istenen = ['sasi_no', 'durum', 'secim_tarihi', 'Geçen Gün', 'marka', 'arac_tipi', 'firma_adi', 'il']
        goster_df = g_df[[c for c in istenen if c in g_df.columns] + [c for c in g_df.columns if c not in istenen and c not in ['secim_tarihi_dt', 'silme_talebi']]]
        st.dataframe(goster_df, use_container_width=True, height=400)
        
        b = io.BytesIO(); goster_df.to_excel(b, index=False)
        st.download_button("📥 Tabloyu Excel Olarak İndir", b.getvalue(), "Rapor.xlsx")
    else: st.info("Sistemde kayıt yok.")

# --- SEKME 2: İŞLEM PANELİ ---
with t[1]:
    i_df = df if st.session_state.rol == "admin" else df[(df['il'] == st.session_state.sorumlu_il) | (df['ekleyen_kullanici'] == st.session_state.kullanici_adi)]
    p_id = st.session_state.get('o_id')
    
    if p_id:
        st.warning("⚠️ Çift Kayıt Riski! Yinede kaydetmek istiyor musunuz?")
        ce, ch = st.columns(2)
        if ce.button("✅ Devam"): 
            durum_guncelle(p_id, st.session_state.o_no, 'Teste Gönderildi', "", starih=datetime.now().strftime("%Y-%m-%d"))
            st.session_state.update({'o_id': None, 'o_no': None}); st.rerun()
        if ch.button("❌ İptal"): st.session_state.update({'o_id': None, 'o_no': None}); st.rerun()
    else:
        cl, cr = st.columns(2)
        with cl:
            st.markdown("#### 🆕 Şasi Atama")
            if not i_df.empty:
                b_list = i_df[i_df['durum'] == 'Şasi Bekliyor']
                sel = st.selectbox("Başvuru:", options=(b_list['id'].astype(str) + " | " + b_list['basvuru_no'].astype(str)).tolist(), index=None) if not b_list.empty else None
                if sel:
                    sid = int(sel.split(" |")[0]); rm = b_list[b_list['id'] == sid].iloc[0]
                    vin = st.text_input("VIN Numarası")
                    if st.button("Kaydet ve Gönder") and vin:
                        try:
                            with get_db() as conn:
                                cur = conn.cursor()
                                cur.execute('SELECT id FROM denetimler WHERE firma_adi=%s AND marka=%s AND arac_tipi=%s AND id != %s', (rm['firma_adi'], rm['marka'], rm['arac_tipi'], sid))
                                if cur.fetchone(): st.session_state.update({'o_id': sid, 'o_no': vin}); st.rerun()
                                else: durum_guncelle(sid, vin, 'Teste Gönderildi', "", starih=datetime.now().strftime("%Y-%m-%d")); st.rerun()
                        except: st.error("Şasi mevcut!")
        with cr:
            st.markdown("#### 🔍 Güncelleme & İlave")
            if not i_df.empty:
                ilist = i_df[i_df['durum'] != 'Şasi Bekliyor']
                sr = st.selectbox("Şasi/Firma:", options=(ilist['id'].astype(str) + " | " + ilist['sasi_no'].astype(str)).tolist(), index=None) if not ilist.empty else None
                if sr:
                    sid = int(sr.split(" |")[0]); cu = ilist[ilist['id'] == sid].iloc[0]
                    with st.form("upd"):
                        nd = st.selectbox("Yeni Durum", ["Teste Gönderildi", "Tamamlandı - Olumlu", "Tamamlandı - Olumsuz"])
                        sl = st.checkbox("Silme Talebi")
                        if st.form_submit_button("Güncelle"): durum_guncelle(sid, cu['sasi_no'], nd, "", silme=sl, snedeni="Talep"); st.rerun()

# --- SEKME 3: VERİ GİRİŞİ ---
with t[2]:
    if st.session_state.ob_df is not None:
        st.warning("⚠️ Mükerrer firma/marka çakışması! Yinede ekle?")
        c1, c2 = st.columns(2)
        if c1.button("✅ Ekle"): 
            excel_kaydet_ve_mail_at(st.session_state.ob_df, st.session_state.atlanmis)
            st.session_state.update({'ob_df': None, 'atlanmis': 0}); st.rerun()
        if c2.button("❌ İptal"): st.session_state.update({'ob_df': None, 'atlanmis': 0}); st.rerun()
    else:
        cf, ce = st.columns(2)
        with cf:
            with st.form("man"):
                bn, fa, ma, ti, sn = st.text_input("B.No"), st.text_input("Firma"), st.text_input("Marka"), st.text_input("Tip"), st.text_input("Şasi")
                if st.form_submit_button("Ekle"):
                    try:
                        with get_db() as c:
                            c.cursor().execute("INSERT INTO denetimler (firma_adi, marka, arac_tipi, sasi_no, basvuru_no, secim_tarihi, il) VALUES (%s,%s,%s,%s,%s,%s,%s)", (fa, ma, ti, sn, bn, datetime.now().strftime("%Y-%m-%d"), st.session_state.sorumlu_il))
                            c.commit()
                        st.success("Eklendi."); st.rerun()
                    except: st.error("Şasi mevcut!")
        with ce:
            up = st.file_uploader("Excel Yükle", type=['xlsx'])
            if up and st.button("Aktar"):
                df_ekle = pd.read_excel(up)
                df_ekle.rename(columns=akilli_sutun_eslestir(df_ekle.columns), inplace=True)
                df_ekle['ekleyen_kullanici'] = st.session_state.kullanici_adi
                if 'durum' not in df_ekle.columns: df_ekle['durum'] = 'Şasi Bekliyor'
                if 'il' not in df_ekle.columns: df_ekle['il'] = st.session_state.sorumlu_il
                
                df_ekle = df_ekle[[c for c in df_ekle.columns if c in ['basvuru_no', 'firma_adi', 'marka', 'arac_tipi', 'sasi_no', 'il', 'durum', 'ekleyen_kullanici']]]
                m_bas = pd.read_sql_query("SELECT basvuru_no FROM denetimler", engine)['basvuru_no'].astype(str).tolist()
                
                df_yeni = df_ekle[~df_ekle['basvuru_no'].astype(str).isin(m_bas)].copy()
                if len(df_yeni) > 0: st.session_state.update({'ob_df': df_yeni, 'atlanmis': len(df_ekle)-len(df_yeni)}); st.rerun()
                else: st.warning("Hepsi sistemde mevcut!")

# --- SEKME 4: PROFİLİM ---
with t[3]:
    st.subheader("Güvenlik Ayarları")
    with st.form("profil_form"):
        eski = st.text_input("Mevcut Şifreniz", type="password")
        yeni = st.text_input("Yeni Şifreniz", type="password")
        yeni_tekrar = st.text_input("Yeni Şifre (Tekrar)", type="password")
        if st.form_submit_button("Şifremi Güncelle"):
            if yeni != yeni_tekrar: st.error("Yeni şifreler eşleşmiyor!")
            else:
                with get_db() as conn:
                    c = conn.cursor()
                    c.execute("SELECT id FROM kullanicilar WHERE kullanici_adi=%s AND sifre=%s", (st.session_state.kullanici_adi, sifreyi_hashle(eski)))
                    if c.fetchone():
                        c.execute("UPDATE kullanicilar SET sifre=%s WHERE kullanici_adi=%s", (sifreyi_hashle(yeni), st.session_state.kullanici_adi))
                        conn.commit()
                        st.success("Şifreniz başarıyla güncellendi!"); time.sleep(1); st.session_state.clear(); st.rerun()
                    else: st.error("Mevcut şifreniz hatalı!")

# --- SEKME 5: ADMİN (Eğer yetkiliyse) ---
if st.session_state.rol == "admin":
    with t[4]:
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Onay Bekleyenler**")
            for _, r in pd.read_sql_query("SELECT * FROM kullanicilar WHERE onay_durumu=0", engine).iterrows():
                if st.button(f"Onayla: {r['kullanici_adi']}", key=f"o_{r['id']}"):
                    with get_db() as c: c.cursor().execute("UPDATE kullanicilar SET onay_durumu=1 WHERE id=%s", (r['id'],)); c.commit()
                    st.rerun()
        with c2:
            st.markdown("**Silme Talepleri**")
            for _, r in df[df['silme_talebi']==1].iterrows():
                if st.button(f"Kalıcı Sil: {r['sasi_no']}", key=f"s_{r['id']}"):
                    with get_db() as c: c.cursor().execute("DELETE FROM denetimler WHERE id=%s", (r['id'],)); c.commit()
                    st.rerun()
