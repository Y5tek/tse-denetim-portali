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

# --- SAYFA YAPILANDIRMASI (MAKSİMUM GENİŞLİK İÇİN EKLENDİ) ---
st.set_page_config(page_title="TSE Denetim Portalı", layout="wide")

# --- TSE KURUMSAL VE MAİL AYARLARI ---
try:
    GONDERICI_MAIL = st.secrets["GONDERICI_MAIL"]
    GONDERICI_SIFRE = st.secrets["GONDERICI_SIFRE"].replace(" ", "") 
    ADMIN_MAIL = st.secrets["ADMIN_MAIL"]
except Exception:
    st.error("Kritik Hata: Streamlit Secrets (Mail ayarları) bulunamadı!")
    st.stop()

SMTP_SUNUCU = "smtp.gmail.com"
SMTP_PORT = 465 

# --- 1. VERİTABANI MOTORU ---
def veritabanini_hazirla():
    conn = sqlite3.connect('tse_v4.db', check_same_thread=False)
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

def kullanici_bildirim_mail_at(kime_mail, konu, icerik):
    msg = MIMEMultipart()
    msg['From'], msg['To'], msg['Subject'] = GONDERICI_MAIL, kime_mail, konu
    msg.attach(MIMEText(f"<html><body><h3>TSE Bildirim</h3><p>{icerik}</p></body></html>", 'html'))
    try:
        server = smtplib.SMTP_SSL(SMTP_SUNUCU, SMTP_PORT)
        server.login(GONDERICI_MAIL, GONDERICI_SIFRE)
        server.send_message(msg); server.quit()
    except: pass

# --- 2. DURUM SORGULARI ---
def durum_sayilarini_al():
    conn = sqlite3.connect('tse_v4.db', check_same_thread=False)
    onay_sayisi = conn.execute("SELECT COUNT(*) FROM kullanicilar WHERE onay_durumu = 0").fetchone()[0]
    silme_sayisi = conn.execute("SELECT COUNT(*) FROM denetimler WHERE silme_talebi = 1").fetchone()[0]
    conn.close()
    return onay_sayisi, silme_sayisi

def verileri_getir():
    conn = sqlite3.connect('tse_v4.db', check_same_thread=False)
    df = pd.read_sql_query("SELECT * FROM denetimler ORDER BY id DESC", conn)
    conn.close()
    df['secim_tarihi_dt'] = pd.to_datetime(df['secim_tarihi'])
    bugun = pd.to_datetime(datetime.now().strftime("%Y-%m-%d"))
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
    conn = sqlite3.connect('tse_v4.db', check_same_thread=False); g_ani = datetime.now().strftime("%Y-%m-%d %H:%M:%S"); sil_v = 1 if talep_et_silme else 0
    if starih == "MEVCUT": conn.cursor().execute('UPDATE denetimler SET sasi_no=?, durum=?, notlar=?, guncelleme_tarihi=?, silme_talebi=?, silme_nedeni=? WHERE id=?', (sasi_no, yeni_durum, notlar, g_ani, sil_v, silme_nedeni, kayit_id))
    else: conn.cursor().execute('UPDATE denetimler SET sasi_no=?, durum=?, secim_tarihi=?, notlar=?, guncelleme_tarihi=?, silme_talebi=?, silme_nedeni=? WHERE id=?', (sasi_no, yeni_durum, starih, notlar, g_ani, sil_v, silme_nedeni, kayit_id))
    conn.commit(); conn.close()
    if talep_et_silme: threading.Thread(target=admin_bildirim_mail_at, args=("⚠️ YENİ SİLME TALEBİ", f"{sasi_no} için silme talebi var.")).start()

# --- 4. GİRİŞ EKRANI ---
if not st.session_state.giris_yapildi:
    c1, c2, c3 = st.columns([1, 2, 1])
    with c2:
        st.markdown("<h1 style='text-align: center; color: #E03131;'>🇹🇷 TSE DENETİM PORTALI</h1>", unsafe_allow_html=True)
        tg, tk = st.tabs(["🔐 Giriş Yap", "📝 Kayıt Ol"])
        with tg:
            with st.form("login_form"):
                ka, si = st.text_input("Kullanıcı Adı"), st.text_input("Şifre", type="password")
                if st.form_submit_button("Giriş Yap", use_container_width=True):
                    conn = sqlite3.connect('tse_v4.db', check_same_thread=False); u = conn.cursor().execute("SELECT rol, sorumlu_il, onay_durumu, excel_yukleme_yetkisi FROM kullanicilar WHERE kullanici_adi=? AND sifre=?", (ka, si)).fetchone(); conn.close()
                    if u:
                        if u[2]==0: st.warning("Oturum onayı bekleniyor.")
                        else: st.session_state.update({'giris_yapildi':True, 'kullanici_adi':ka, 'rol':u[0], 'sorumlu_il':u[1], 'excel_yetkisi':u[3]}); st.rerun()
                    else: st.error("❌ Hatalı bilgiler.")
        with tk:
            with st.form("register_form"):
                yk, ys, ye, yil = st.text_input("Kullanıcı Adı"), st.text_input("Şifre"), st.text_input("E-Posta"), st.selectbox("İl", ["Ankara", "İstanbul", "İzmir", "Bursa", "Kocaeli", "Diğer"])
                if st.form_submit_button("Kayıt Talebi Gönder"):
                    try:
                        conn = sqlite3.connect('tse_v4.db', check_same_thread=False); conn.cursor().execute("INSERT INTO kullanicilar (kullanici_adi, sifre, rol, email, sorumlu_il, onay_durumu, excel_yukleme_yetkisi) VALUES (?, ?, 'kullanici', ?, ?, 0, 0)", (yk, ys, ye, yil)); conn.commit(); conn.close()
                        threading.Thread(target=admin_bildirim_mail_at, args=("📝 YENİ KAYIT", f"Yeni üye talebi: {yk}")).start()
                        st.success("Tebrikler! Talebiniz iletildi."); time.sleep(1); st.rerun()
                    except: st.error("Kullanıcı adı mevcut.")
    st.stop()

# --- 5. ANA EKRAN (GİRİŞ SONRASI) ---
b_onay, b_silme = durum_sayilarini_al()
toplam_bekleyen = b_onay + b_silme
df = verileri_getir()

with st.sidebar:
    st.markdown("<h2 style='color: #E03131;'>TSE PANEL</h2>", unsafe_allow_html=True)
    st.write(f"👤 **{st.session_state.kullanici_adi}**")
    st.write(f"📍 **{st.session_state.sorumlu_il}**")
    if st.session_state.rol == "admin" and toplam_bekleyen > 0:
        st.error(f"🚨 {toplam_bekleyen} Bekleyen İşlem!")
    st.divider()
    if st.button("🚪 Oturumu Kapat", use_container_width=True):
        st.session_state.clear(); st.rerun()

if st.session_state.rol == "admin" and toplam_bekleyen > 0:
    st.error(f"📢 **Yönetici Bildirimi:** Şu an onay bekleyen **{b_onay} üye** ve **{b_silme} silme talebi** var.")

admin_tab_label = f"👑 Yönetici Paneli ({toplam_bekleyen})" if (st.session_state.rol == "admin" and toplam_bekleyen > 0) else "👑 Yönetici Paneli"
main_tabs = ["📊 Ana Tablo", "🛠️ Numune Kayıt Girişi", "📥 Veri Girişi"]
if st.session_state.rol == "admin": main_tabs.append(admin_tab_label)

tabs = st.tabs(main_tabs)

with tabs[0]:
    st.subheader("Sistem Kayıtları")
    c_m1, c_m2, c_m3 = st.columns(3)
    c_m1.metric("Toplam", len(df))
    c_m2.metric("Teste Gönderildi", len(df[df['durum'] == 'Teste Gönderildi']))
    c_m3.metric("Olumlu", len(df[df['durum'] == 'Tamamlandı - Olumlu']))
    
    istenen = ['sasi_no', 'durum', 'secim_tarihi', 'Geçen Gün', 'marka', 'arac_tipi', 'firma_adi', 'arac_kategori', 'birim', 'il']
    display_df = df[[c for c in istenen if c in df.columns] + [c for c in df.columns if c not in istenen and c not in ['secim_tarihi_dt', 'silme_talebi']]]
    
    src = st.text_input("🔍 Filtrele (Şasi, Marka, Firma vb.):")
    if src: display_df = display_df[display_df.apply(lambda r: src.lower() in r.astype(str).str.lower().values, axis=1)]
    
    st.dataframe(display_df.style.apply(satir_boya, axis=1), use_container_width=True, height=800)
    
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as w: display_df.to_excel(w, index=False)
    st.download_button("📥 Excel İndir", buffer.getvalue(), f"TSE_Rapor_{datetime.now().strftime('%Y-%m-%d')}.xlsx")

with tabs[1]:
    st.subheader("İşlem Paneli")
    i_df = df if st.session_state.rol == "admin" else df[(df['il'] == st.session_state.sorumlu_il) | (df['ekleyen_kullanici'] == st.session_state.kullanici_adi)]
    
    p_id = st.session_state.get('onay_bekleyen_sasi_id')
    if p_id:
        st.warning("⚠️ Marka-Tip çakışması! Yine de şasiyi kaydetmek istiyor musunuz?")
        if st.button("✅ Evet, Kaydet"):
            durum_guncelle_by_id(p_id, st.session_state.onay_bekleyen_sasi_no, 'Teste Gönderildi', "", starih=datetime.now().strftime("%Y-%m-%d"))
            st.session_state.update({'onay_bekleyen_sasi_id': None, 'onay_bekleyen_sasi_no': None}); st.rerun()
    else:
        c_left, c_right = st.columns(2)
        with c_left:
            st.markdown("#### 🆕 Şasi Atama")
            b_list = i_df[i_df['durum'] == 'Şasi Bekliyor']
            sel = st.selectbox("Başvuru:", options=(b_list['id'].astype(str) + " | " + b_list['basvuru_no']).tolist(), index=None)
            if sel:
                sid = int(sel.split(" |")[0]); row_m = b_list[b_list['id'] == sid].iloc[0]
                vin = st.text_input("VIN Numarası")
                if st.button("Kaydet ve Teste Gönder"):
                    conn = sqlite3.connect('tse_v4.db', check_same_thread=False); once = conn.cursor().execute('SELECT id FROM denetimler WHERE firma_adi=? AND marka=? AND arac_tipi=? AND secim_tarihi IS NOT NULL AND id != ?', (row_m['firma_adi'], row_m['marka'], row_m['arac_tipi'], sid)).fetchone(); conn.close()
                    if once: st.session_state.update({'onay_bekleyen_sasi_id': sid, 'onay_bekleyen_sasi_no': vin}); st.rerun()
                    else: durum_guncelle_by_id(sid, vin, 'Teste Gönderildi', "", starih=datetime.now().strftime("%Y-%m-%d")); st.rerun()
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
                        durum_guncelle_by_id(sid, cur['sasi_no'], nd, "", talep_et_silme=sl, silme_nedeni="Talep Edildi")
                        st.rerun()

with tabs[2]:
    st.subheader("📥 Veri Girişi")
    c_form, c_excel = st.columns(2)
    with c_form:
        with st.form("manuel_form"):
            st.write("Elden Kayıt")
            bn, fa, ma, ti, sn = st.text_input("B.No"), st.text_input("Firma"), st.text_input("Marka"), st.text_input("Tip"), st.text_input("Şasi")
            if st.form_submit_button("Ekle"):
                conn = sqlite3.connect('tse_v4.db', check_same_thread=False); conn.cursor().execute("INSERT INTO denetimler (firma_adi, marka, arac_tipi, sasi_no, basvuru_no, durum, basvuru_tarihi, secim_tarihi, il) VALUES (?,?,?,?,?, 'Teste Gönderildi', ?, ?, ?)", (fa, ma, ti, sn, bn, datetime.now().strftime("%Y-%m-%d"), datetime.now().strftime("%Y-%m-%d"), st.session_state.sorumlu_il)); conn.commit(); conn.close(); st.rerun()
    
    with c_excel:
        up = st.file_uploader("Excel Yükle", type=['xlsx', 'csv'])
        if up and st.button("Sisteme Aktar"):
            try:
                if up.name.endswith('.csv'):
                    df_ekle = pd.read_csv(up)
                else:
                    df_ekle = pd.read_excel(up)
                
                sutun_haritasi = {
                    "BasvuruNo": "basvuru_no",
                    "Firma": "firma_adi",
                    "Marka": "marka",
                    "Araç Kategori": "arac_kategori",
                    "Tip": "arac_tipi",
                    "Varyant": "varyant",
                    "Versiyon": "versiyon",
                    "TicariAd": "ticari_ad",
                    "GtipNo": "gtip_no",
                    "Birim": "birim",
                    "Üretildiği Ülke": "uretim_ulkesi",
                    "Araç Sayısı": "arac_sayisi"
                }
                
                df_ekle.columns = df_ekle.columns.str.strip()
                df_ekle.rename(columns=sutun_haritasi, inplace=True)
                
                df_ekle['ekleyen_kullanici'] = st.session_state.kullanici_adi
                if 'il' not in df_ekle.columns:
                    df_ekle['il'] = st.session_state.sorumlu_il
                if 'durum' not in df_ekle.columns:
                    df_ekle['durum'] = 'Şasi Bekliyor'
                
                gecerli_sutunlar = ['basvuru_no', 'firma_adi', 'marka', 'arac_kategori', 'arac_tipi', 
                                    'varyant', 'versiyon', 'ticari_ad', 'gtip_no', 'birim', 'uretim_ulkesi', 
                                    'arac_sayisi', 'sasi_no', 'basvuru_tarihi', 'secim_tarihi', 'il', 'durum', 
                                    'notlar', 'guncelleme_tarihi', 'ekleyen_kullanici', 'silme_talebi', 'silme_nedeni']
                
                df_ekle = df_ekle[[col for col in df_ekle.columns if col in gecerli_sutunlar]]
                
                # --- ÇÖZÜM 1: MÜKERRER KAYIT (ÇİFT KAYIT) KONTROLÜ ---
                conn = sqlite3.connect('tse_v4.db', check_same_thread=False)
                
                # Veritabanındaki mevcut başvuru numaralarını çekiyoruz
                mevcut_kayitlar = pd.read_sql_query("SELECT basvuru_no FROM denetimler", conn)
                mevcut_basvuru_listesi = mevcut_kayitlar['basvuru_no'].astype(str).tolist()
                
                # Excel'deki verilerin başvuru numarasını string (metin) formata çevirip karşılaştırıyoruz
                df_ekle['basvuru_no_str'] = df_ekle['basvuru_no'].astype(str)
                # SADECE veritabanında olmayanları (yeni olanları) alıyoruz
                df_yeni = df_ekle[~df_ekle['basvuru_no_str'].isin(mevcut_basvuru_listesi)].copy()
                df_yeni.drop(columns=['basvuru_no_str'], inplace=True) # Karşılaştırma sütununu siliyoruz
                
                if len(df_yeni) == 0:
                    st.warning("⚠️ Yüklediğiniz dosyadaki tüm kayıtlar zaten sistemde mevcut! Mükerrer kayıt engellendi.")
                    conn.close()
                else:
                    # Sadece YENİ kayıtları veritabanına ekliyoruz
                    df_yeni.to_sql('denetimler', conn, if_exists='append', index=False)
                    
                    # --- ÇÖZÜM 2: GÜVENLİ KULLANICI BİLDİRİM MAİLİ ---
                    mail_gidenler = []
                    try:
                        unique_iller = df_yeni['il'].unique().tolist()
                        cursor = conn.cursor()
                        for il_adi in unique_iller:
                            ilgili_kullanicilar = cursor.execute("SELECT email, kullanici_adi FROM kullanicilar WHERE sorumlu_il=? AND onay_durumu=1", (il_adi,)).fetchall()
                            for k_mail, k_adi in ilgili_kullanicilar:
                                if k_mail and "@" in k_mail: # Geçerli bir e-posta mı diye basit bir kontrol
                                    m_konu = f"TSE Sistemi - {il_adi} İli İçin Yeni Veri Girişi"
                                    m_icerik = f"Merhaba <b>{k_adi}</b>,<br><br>Sistemde sorumlu olduğunuz <b>{il_adi}</b> ili için sisteme yeni veri yüklenmiştir. Lütfen portal üzerinden numune/şasi atama işlemlerini tamamlayınız."
                                    threading.Thread(target=kullanici_bildirim_mail_at, args=(k_mail, m_konu, m_icerik)).start()
                                    mail_gidenler.append(k_adi)
                    except Exception as mail_hata:
                        st.warning(f"Uyarı: Kayıtlar eklendi ancak mail gönderilirken bir hata oluştu: {mail_hata}")
                    
                    conn.close()
                    
                    # Kullanıcıya detaylı sonuç mesajı gösteriyoruz
                    eklenen_sayi = len(df_yeni)
                    atlanan_sayi = len(df_ekle) - eklenen_sayi
                    
                    mesaj = f"Tebrikler! {eklenen_sayi} adet YENİ kayıt başarıyla aktarıldı."
                    if atlanan_sayi > 0:
                        mesaj += f" ({atlanan_sayi} adet mevcut mükerrer kayıt atlandı.)"
                    if len(mail_gidenler) > 0:
                        mesaj += f" Bildirim iletilenler: {', '.join(mail_gidenler)}"
                        
                    st.success(mesaj)
                    time.sleep(3) # Kullanıcının mesajı okuyabilmesi için biraz süre tanıdık
                    st.rerun()
            except Exception as e:
                st.error(f"Aktarım sırasında kritik bir hata oluştu: {e}")

if st.session_state.rol == "admin":
    with tabs[3]:
        st.subheader("👑 Yönetici Paneli")
        co, cs = st.columns(2)
        with co:
            st.markdown(f"**Onay Bekleyen Üyeler ({b_onay})**")
            conn = sqlite3.connect('tse_v4.db', check_same_thread=False); k_df = pd.read_sql_query("SELECT * FROM kullanicilar WHERE onay_durumu=0", conn); conn.close()
            for _, r in k_df.iterrows():
                st.write(f"👤 {r['kullanici_adi']}")
                if st.button("Onayla", key=f"o_{r['id']}"):
                    c = sqlite3.connect('tse_v4.db', check_same_thread=False); c.execute("UPDATE kullanicilar SET onay_durumu=1 WHERE id=?", (r['id'],)); c.commit(); c.close(); st.rerun()
        with cs:
            st.markdown(f"**Silme Talepleri ({b_silme})**")
            for _, r in df[df['silme_talebi']==1].iterrows():
                st.write(f"🗑️ {r['sasi_no']}")
                if st.button("Kalıcı Sil", key=f"sil_{r['id']}"):
                    c = sqlite3.connect('tse_v4.db', check_same_thread=False); c.execute("DELETE FROM denetimler WHERE id=?", (r['id'],)); c.commit(); c.close(); st.rerun()
