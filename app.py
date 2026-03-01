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
from psycopg2 import IntegrityError
from sqlalchemy import create_engine
from contextlib import contextmanager

# --- KULLANIM KILAVUZU METNİ ---
KILAVUZ_METNI = """# 🇹🇷 TSE NUMUNE TAKİP PORTALI - KULLANIM KILAVUZU VE SİSTEM ÖZETİ

Bu proje, kurum içindeki başvuru, numune atama (şasi eşleştirme) ve denetim süreçlerini dijitalleştirmek, kullanıcıları illere göre yönetmek ve süreçleri otomatik e-posta bildirimleriyle hızlandırmak amacıyla geliştirilmiştir.

## 🛠 1. Teknik Altyapı ve Güvenlik
* Arayüz (UI): Kullanıcı dostu Streamlit altyapısı kullanılmıştır.
* Veritabanı: Bulut tabanlı PostgreSQL (Supabase) kullanılmıştır.
* Veri Güvenliği: Şifreler ve e-posta sunucu bilgileri güvenli "Secrets" kasasında saklanmaktadır. Parolalar SHA-256 ile şifrelenmektedir.

## 👥 2. Rol ve Oturum Yönetimi
Sistemde iki farklı kullanıcı rolü bulunmaktadır: Kullanıcı ve Admin (Yönetici).
* Yeni kayıt olan bir kullanıcı sisteme yöneticinin onayından sonra girebilir.
* Yöneticiler tüm illerin verilerini görebilirken, standart kullanıcılar sadece kendi sorumlu oldukları illerin verilerini yönetebilirler.

## 🖥 3. Sistem Sekmeleri ve Fonksiyonlar
### 📊 Sekme 1: Ana Tablo (Sistem Kayıtları)
Tüm verilerin izlendiği ana gösterge panelidir. Akıllı Arama ile tüm tabloda filtreleme yapılabilir ve veriler tek tıkla Excel (.xlsx) formatında bilgisayara indirilebilir.

### 🛠️ Sekme 2: İşlem Paneli (Numune Kayıt Girişi)
* Şasi Atama: "Şasi Bekliyor" durumundaki başvurulara VIN numarası girilerek "Teste Gönderildi" aşamasına geçirilir. Çift kayıt uyarısı ile koruma altındadır.
* Güncelleme & İlave: Araçların durumları güncellenir veya silme talebi oluşturulabilir.

### 📥 Sekme 3: Veri Girişi (Manuel & Excel)
* Elden Kayıt: Tekil kayıtlar form aracılığıyla eklenebilir.
* Excel ile Toplu Yükleme: Sütun eşleştirme, akıllı il tahmini ve mükerrer firma/marka/tip kontrolü yapılarak veriler güvenle sisteme aktarılır.

### 👑 Sekme 4: Yönetici Paneli (Sadece Adminler)
Onay bekleyen üyeler ve silme talepleri yönetilir. Kullanıcılara yetkiler atanabilir.
"""

# --- SAYFA YAPILANDIRMASI ---
st.set_page_config(page_title="TSE NUMUNE TAKİP PORTALI", layout="wide")

# --- TSE KURUMSAL VE MAİL AYARLARI ---
try:
    GONDERICI_MAIL = st.secrets["GONDERICI_MAIL"]
    GONDERICI_SIFRE = st.secrets["GONDERICI_SIFRE"].replace(" ", "") 
    ADMIN_MAIL = st.secrets["ADMIN_MAIL"]
    DB_URI = st.secrets["DB_URI"]
except Exception:
    st.error("Kritik Hata: Streamlit Secrets (Mail veya Veritabanı ayarları) bulunamadı!")
    st.stop()

SMTP_SUNUCU = "smtp.gmail.com"
SMTP_PORT = 465 

# --- ŞİFRE HASHLEME ---
def sifreyi_hashle(sifre_metni):
    return hashlib.sha256(sifre_metni.encode('utf-8')).hexdigest()

# --- 1. VERİTABANI MOTORU (POSTGRESQL BAĞLANTISI) ---
engine = create_engine(DB_URI) # Pandas işlemleri için

@contextmanager
def get_db():
    """Psycopg2 veritabanı bağlantısını güvenle yöneten yapı."""
    conn = psycopg2.connect(DB_URI)
    try:
        yield conn
    finally:
        conn.close()

def veritabanini_hazirla():
    with get_db() as conn:
        cursor = conn.cursor()
        # PostgreSQL'de AUTOINCREMENT yerine SERIAL kullanılır.
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

        # Sistemde hiç admin yoksa, varsayılan bir admin oluştur (İlk kurulum kolaylığı için)
        cursor.execute("SELECT COUNT(*) FROM kullanicilar WHERE rol = 'admin'")
        if cursor.fetchone()[0] == 0:
            default_admin_hash = sifreyi_hashle("admin123")
            cursor.execute("INSERT INTO kullanicilar (kullanici_adi, sifre, rol, email, sorumlu_il, onay_durumu, excel_yukleme_yetkisi) VALUES (%s, %s, 'admin', %s, 'Tümü', 1, 1)", ("admin", default_admin_hash, ADMIN_MAIL))
            conn.commit()

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

# --- YARDIMCI İŞLEMLER ---
def excel_kaydet_ve_mail_at(df_yeni, atlanan_sayi):
    mail_gidenler = []
    # Pandas PostgreSQL motorunu kullanır
    df_yeni.to_sql('denetimler', engine, if_exists='append', index=False)
    
    try:
        with get_db() as conn:
            il_ozeti = df_yeni['il'].value_counts().to_dict()
            cursor = conn.cursor()
            for il_adi, adet in il_ozeti.items():
                # PostgreSQL'de ? yerine %s kullanılır
                cursor.execute("SELECT email, kullanici_adi FROM kullanicilar WHERE sorumlu_il=%s AND onay_durumu=1", (il_adi,))
                ilgili_kullanicilar = cursor.fetchall()
                for k_mail, k_adi in ilgili_kullanicilar:
                    if k_mail and "@" in k_mail: 
                        m_konu = f"TSE Sistemi - {il_adi} İli İçin Yeni Veri Girişi"
                        m_icerik = f"Merhaba <b>{k_adi}</b>,<br><br>Sistemde sorumlu olduğunuz <b>{il_adi}</b> ili için sisteme <b>{adet} adet</b> yeni kayıt yüklenmiştir. Lütfen portal üzerinden numune/şasi atama işlemlerini tamamlayınız."
                        threading.Thread(target=kullanici_bildirim_mail_at, args=(k_mail, m_konu, m_icerik)).start()
                        mail_gidenler.append(f"{k_adi} ({il_adi})")
    except Exception as mail_hata:
        st.warning(f"Uyarı: Kayıtlar eklendi ancak mail gönderilirken bir hata oluştu: {mail_hata}")
    
    eklenen_sayi = len(df_yeni)
    mesaj = f"Tebrikler! {eklenen_sayi} adet YENİ kayıt başarıyla aktarıldı."
    if atlanan_sayi > 0:
        mesaj += f" ({atlanan_sayi} adet mevcut başvuru numarası mükerrer olduğu için atlandı.)"
    if len(mail_gidenler) > 0:
        mesaj += f" Bildirim iletilenler: {', '.join(mail_gidenler)}"
        
    st.success(mesaj)
    time.sleep(3)
    st.rerun()

# --- 2. DURUM SORGULARI ---
def durum_sayilarini_al():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM kullanicilar WHERE onay_durumu = 0")
        onay_sayisi = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM denetimler WHERE silme_talebi = 1")
        silme_sayisi = cursor.fetchone()[0]
    return onay_sayisi, silme_sayisi

def verileri_getir():
    df = pd.read_sql_query("SELECT * FROM denetimler ORDER BY id DESC", engine)
    
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
    st.session_state.update({
        'giris_yapildi': False, 'kullanici_adi': "", 'rol': "", 'sorumlu_il': "", 'excel_yetkisi': 0,
        'onay_bekleyen_excel_df': None, 'atlanan_kayit_sayisi': 0
    })

def durum_guncelle_by_id(kayit_id, sasi_no, yeni_durum, notlar, starih="MEVCUT", talep_et_silme=False, silme_nedeni=""):
    g_ani = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sil_v = 1 if talep_et_silme else 0
    with get_db() as conn:
        cursor = conn.cursor()
        if starih == "MEVCUT": 
            cursor.execute('UPDATE denetimler SET sasi_no=%s, durum=%s, notlar=%s, guncelleme_tarihi=%s, silme_talebi=%s, silme_nedeni=%s WHERE id=%s', (sasi_no, yeni_durum, notlar, g_ani, sil_v, silme_nedeni, int(kayit_id)))
        else: 
            cursor.execute('UPDATE denetimler SET sasi_no=%s, durum=%s, secim_tarihi=%s, notlar=%s, guncelleme_tarihi=%s, silme_talebi=%s, silme_nedeni=%s WHERE id=%s', (sasi_no, yeni_durum, starih, notlar, g_ani, sil_v, silme_nedeni, int(kayit_id)))
        conn.commit()
        
    if talep_et_silme: threading.Thread(target=admin_bildirim_mail_at, args=("⚠️ YENİ SİLME TALEBİ", f"{sasi_no} için silme talebi var.")).start()

# --- 4. GİRİŞ EKRANI ---
if not st.session_state.giris_yapildi:
    c1, c2, c3 = st.columns([1, 2, 1])
    with c2:
        if os.path.exists("tse_logo.png"):
            logo_c1, logo_c2, logo_c3 = st.columns([1, 2, 1])
            with logo_c2:
                st.image("tse_logo.png", use_container_width=True)
                
        st.markdown("<h1 style='text-align: center; color: #E03131;'> TSE NUMUNE TAKİP PORTALI</h1>", unsafe_allow_html=True)
        tg, tk = st.tabs(["🔐 Giriş Yap", "📝 Kayıt Ol"])
        
        with tg:
            with st.form("login_form"):
                ka, si = st.text_input("Kullanıcı Adı"), st.text_input("Şifre", type="password")
                if st.form_submit_button("Giriş Yap", use_container_width=True):
                    hashli_giris_sifresi = sifreyi_hashle(si) 
                    with get_db() as conn:
                        cursor = conn.cursor()
                        cursor.execute("SELECT rol, sorumlu_il, onay_durumu, excel_yukleme_yetkisi FROM kullanicilar WHERE kullanici_adi=%s AND sifre=%s", (ka, hashli_giris_sifresi))
                        u = cursor.fetchone()
                    
                    if u:
                        if u[2]==0: st.warning("Oturum onayı bekleniyor.")
                        else: st.session_state.update({'giris_yapildi':True, 'kullanici_adi':ka, 'rol':u[0], 'sorumlu_il':u[1], 'excel_yetkisi':u[3]}); st.rerun()
                    else: st.error("❌ Hatalı kullanıcı adı veya şifre.")
        
        with tk:
            with st.form("register_form"):
                yk, ys, ye, yil = st.text_input("Kullanıcı Adı"), st.text_input("Şifre", type="password"), st.text_input("E-Posta"), st.selectbox("İl", ["Ankara", "İstanbul", "İzmir", "Bursa", "Kocaeli", "Diğer"])
                if st.form_submit_button("Kayıt Talebi Gönder"):
                    try:
                        hashli_yeni_sifre = sifreyi_hashle(ys)
                        with get_db() as conn:
                            cursor = conn.cursor()
                            cursor.execute("INSERT INTO kullanicilar (kullanici_adi, sifre, rol, email, sorumlu_il, onay_durumu, excel_yukleme_yetkisi) VALUES (%s, %s, 'kullanici', %s, %s, 0, 0)", (yk, hashli_yeni_sifre, ye, yil))
                            conn.commit()
                        threading.Thread(target=admin_bildirim_mail_at, args=("📝 YENİ KAYIT", f"Yeni üye talebi: {yk}")).start()
                        st.success("Tebrikler! Talebiniz iletildi."); time.sleep(1); st.rerun()
                    except IntegrityError: 
                        st.error("Kullanıcı adı mevcut.")
    st.stop()

# --- 5. ANA EKRAN (GİRİŞ SONRASI) ---
b_onay, b_silme = durum_sayilarini_al()
toplam_bekleyen = b_onay + b_silme
df = verileri_getir()

with st.sidebar:
    if os.path.exists("tse_logo.png"):
        st.image("tse_logo.png", use_container_width=True)
        
    st.markdown("<h2 style='color: #E03131;'>TSE PANEL</h2>", unsafe_allow_html=True)
    st.write(f"👤 **{st.session_state.kullanici_adi}**")
    st.write(f"📍 **{st.session_state.sorumlu_il}**")
    if st.session_state.rol == "admin" and toplam_bekleyen > 0:
        st.error(f"🚨 {toplam_bekleyen} Bekleyen İşlem!")
    
    st.divider()
    
    st.download_button(
        label="📄 Kullanım Kılavuzunu İndir",
        data=KILAVUZ_METNI,
        file_name="TSE_Denetim_Portali_Kullanim_Kilavuzu.md",
        mime="text/markdown",
        use_container_width=True
    )
    
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
        st.warning("⚠️ DİKKAT: Bu Firma, Marka ve Ara
